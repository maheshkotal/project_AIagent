"""Composition root: wires settings, LLM stack, agents, gates and storage into a runnable pipeline."""

from __future__ import annotations

from enum import StrEnum
from pathlib import Path

from agentforge.agents.impact import ImpactAnalystAgent
from agentforge.agents.planning import PlannerAgent, RequirementAnalystAgent, plan_rules
from agentforge.agents.review import RepairCoordinator, ReviewerAgent
from agentforge.agents.workers import ArchitectAgent, CoderAgent, DocsAgent, RiskAgent, TaskRouter, TesterAgent
from agentforge.audit import AuditLog
from agentforge.config import Settings
from agentforge.llm.base import LLMClient
from agentforge.llm.gemini import GeminiClient
from agentforge.llm.ratelimit import RateLimiter
from agentforge.llm.replay import ReplayClient, ReplayMode
from agentforge.llm.resilient import ResilientClient, RunBudget
from agentforge.orchestrator import AgentSuite, CheckpointStore, Gate, Pipeline, RunState
from agentforge.validation import WorkspaceValidator


class LLMMode(StrEnum):
    LIVE = "live"  # call Gemini
    RECORD = "record"  # call Gemini and save every response as a cassette
    REPLAY = "replay"  # offline: serve recorded cassettes only
    AUTO = "auto"  # replay when recorded, otherwise call Gemini and record


def build_llm(settings: Settings, mode: LLMMode, cassettes: Path, audit: AuditLog) -> LLMClient:
    live: LLMClient | None = None
    if mode is not LLMMode.REPLAY:
        gemini = GeminiClient(
            api_key=settings.gemini_api_key or "",
            model=settings.model,
            fallback_models=settings.fallback_models,
            timeout_s=settings.llm_timeout_s,
        )
        live = ResilientClient(
            gemini,
            limiter=RateLimiter(settings.requests_per_minute, settings.max_concurrency),
            budget=RunBudget(settings.max_llm_calls_per_run, settings.max_tokens_per_run),
            audit=audit,
            max_retries=settings.llm_max_retries,
            backoff_base_s=settings.llm_backoff_base_s,
            backoff_max_s=settings.llm_backoff_max_s,
        )
    if mode is LLMMode.LIVE:
        assert live is not None
        return live
    # Replay sits outside the resilience layer: cassette hits cost no quota, budget or rate limit.
    return ReplayClient(cassettes, ReplayMode(mode.value), live)


def build_agents(llm: LLMClient, settings: Settings, audit: AuditLog) -> AgentSuite:
    coder, tester = CoderAgent(llm, settings), TesterAgent(llm, settings)
    router = TaskRouter(
        [ArchitectAgent(llm, settings), coder, tester, DocsAgent(llm, settings), RiskAgent(llm, settings)]
    )
    return AgentSuite(
        analyst=RequirementAnalystAgent(llm, settings),
        planner=PlannerAgent(llm, settings),
        task_handler=router,
        worker_agents=router.agent_names,
        impact_analyst=ImpactAnalystAgent(llm, settings),
        plan_rules=plan_rules,
        validator=WorkspaceValidator(),
        repairer=RepairCoordinator(ReviewerAgent(llm, settings), coder, tester, audit),
    )


def build_pipeline(
    state: RunState,
    *,
    settings: Settings,
    gate: Gate,
    mode: LLMMode,
    cassettes: Path,
    audit: AuditLog | None = None,
) -> Pipeline:
    store = CheckpointStore(settings.runs_dir)
    audit = audit or AuditLog(store.run_dir(state.run_id) / "audit.jsonl")
    llm = build_llm(settings, mode, cassettes, audit)
    return Pipeline(
        state, agents=build_agents(llm, settings, audit), gate=gate, store=store, audit=audit, settings=settings
    )
