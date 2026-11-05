"""Human approval gates (controlled autonomy). Agents run freely between gates; humans decide at gates."""

from __future__ import annotations

import asyncio
from abc import ABC, abstractmethod
from collections import deque
from collections.abc import Iterable

from pydantic import BaseModel, Field
from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel
from rich.prompt import Prompt

from agentforge.models import Ambiguity
from agentforge.orchestrator.state import GateAction, GateDecision


class GateRequest(BaseModel):
    gate: str
    title: str
    summary: str = Field(description="Markdown shown to the reviewer.")
    questions: list[Ambiguity] = Field(default_factory=list)
    # Allowed actions; the first one is the default (and what auto-approve picks).
    actions: list[GateAction] = Field(default_factory=lambda: ["approve", "reject", "abort"])


class Gate(ABC):
    @abstractmethod
    async def review(self, request: GateRequest) -> GateDecision: ...


class AutoApproveGate(Gate):
    """Non-interactive mode for CI and replays: picks the default action and default assumptions."""

    async def review(self, request: GateRequest) -> GateDecision:
        return GateDecision(gate=request.gate, action=request.actions[0], decided_by="auto")


class ScriptedGate(Gate):
    """Test double: returns pre-scripted decisions in order, then falls back to auto-approve."""

    def __init__(self, decisions: Iterable[GateDecision] = ()) -> None:
        self._decisions = deque(decisions)
        self.requests: list[GateRequest] = []

    async def review(self, request: GateRequest) -> GateDecision:
        self.requests.append(request)
        if self._decisions and self._decisions[0].gate == request.gate:
            return self._decisions.popleft()
        return await AutoApproveGate().review(request)


class CliGate(Gate):
    def __init__(self, console: Console | None = None) -> None:
        self._console = console or Console()

    async def review(self, request: GateRequest) -> GateDecision:
        # Prompts block on stdin; run them off the event loop.
        return await asyncio.to_thread(self._review_blocking, request)

    def _review_blocking(self, request: GateRequest) -> GateDecision:
        c = self._console
        c.print()
        c.print(Panel(Markdown(request.summary), title=f"[bold]GATE: {request.title}[/bold]", border_style="yellow"))
        answers: dict[str, str] = {}
        for q in request.questions:
            answers_for_q = self._ask_question(q)
            if answers_for_q:
                answers[q.id] = answers_for_q
        action = Prompt.ask("Decision", choices=list(request.actions), default=request.actions[0], console=c)
        feedback = ""
        if action in ("reject", "retry"):
            feedback = Prompt.ask("Feedback for the agents (optional)", default="", console=c)
        return GateDecision(gate=request.gate, action=action, feedback=feedback, answers=answers)

    def _ask_question(self, q: Ambiguity) -> str:
        c = self._console
        c.print(f"\n[bold cyan]{q.id}[/bold cyan] ({q.impact.value} impact) {q.question}")
        for i, option in enumerate(q.options, start=1):
            c.print(f"   {i}. {option}")
        c.print(f"   [dim]Enter = default: {q.default_assumption}[/dim]")
        raw = Prompt.ask("Answer (number or free text)", default="", show_default=False, console=c).strip()
        if raw.isdigit() and 1 <= int(raw) <= len(q.options):
            return q.options[int(raw) - 1]
        return raw
