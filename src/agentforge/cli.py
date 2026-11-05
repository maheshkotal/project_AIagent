"""Command-line interface: `agentforge run "<requirement>"` and `agentforge resume <run-id>`."""

from __future__ import annotations

import argparse
import asyncio
from dataclasses import replace
from pathlib import Path
from typing import Any

from rich.console import Console

from agentforge.audit import AuditLog
from agentforge.config import Settings
from agentforge.factory import LLMMode, build_pipeline
from agentforge.llm.base import LLMError
from agentforge.orchestrator import AutoApproveGate, CheckpointStore, CliGate, RunState, RunStatus


class ProgressPrinter:
    """Renders audit events as a live progress log."""

    def __init__(self, console: Console) -> None:
        self._console = console

    def __call__(self, event: dict[str, Any]) -> None:
        line = self._format(event)
        if line:
            self._console.print(line)

    def _format(self, e: dict[str, Any]) -> str | None:
        match e["event"]:
            case "phase_started":
                return f"\n[bold blue]== {e['phase']} ==[/bold blue]"
            case "phase_skipped":
                return f"[dim]   skipped {e['phase']}: {e['reason']}[/dim]"
            case "llm_call":
                return (
                    f"[dim]   llm {e['agent']}: {e['input_tokens']}+{e['output_tokens']} tokens, "
                    f"{e['latency_ms'] / 1000:.1f}s[/dim]"
                )
            case "llm_retry":
                return f"[yellow]   llm {e['agent']}: retry in {e['delay_s']}s ({e['error'][:80]})[/yellow]"
            case "plan_invalid":
                return "[yellow]   plan rejected by validation, replanning:[/yellow]\n" + "\n".join(
                    f"[yellow]     - {err}[/yellow]" for err in e["errors"]
                )
            case "task_started":
                return f"   [cyan]>[/cyan] {e['task_id']} ({e['agent']}), attempt {e['attempt']}"
            case "task_succeeded":
                return f"   [green]ok[/green] {e['task_id']}"
            case "task_retry":
                return f"   [yellow]retry[/yellow] {e['task_id']}: {e['error'][:120]}"
            case "task_failed":
                return f"   [red]FAILED[/red] {e['task_id']}: {e['error'][:160]}"
            case "task_skipped":
                return f"   [dim]skipped {e['task_id']} (blocked by {', '.join(e['blocked_by'])})[/dim]"
            case "validation":
                checks = ", ".join(f"{k}={v}" for k, v in e["checks"].items())
                color, verdict = ("green", "PASSED") if e["passed"] else ("red", "FAILED")
                return f"   [{color}]validation round {e['round']}: {verdict}[/{color}] ({checks})"
            case "review_verdict":
                return f"   [magenta]reviewer[/magenta]: fault in {e['fault']} - {e['rationale'][:160]}"
            case "repair_applied":
                return f"   [magenta]repair {e['round']}[/magenta]: updated {', '.join(e['files']) or 'no files'}"
            case "gate_decision":
                return f"   [bold]gate {e['gate']}[/bold]: {e['action']} ({e['decided_by']})"
            case _:
                return None


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="agentforge", description="Agentic software-engineering pipeline")
    sub = parser.add_subparsers(dest="command", required=True)
    run = sub.add_parser("run", help="Start a new run from a requirement")
    run.add_argument("requirement", help="The software requirement, in plain language")
    run.add_argument("--repo", type=Path, help="Existing codebase to change (brownfield mode)")
    resume = sub.add_parser("resume", help="Resume an interrupted or aborted run")
    resume.add_argument("run_id")
    for p in (run, resume):
        p.add_argument("--auto-approve", action="store_true", help="Approve every gate with its default action")
        p.add_argument(
            "--llm",
            choices=[m.value for m in LLMMode],
            default=LLMMode.LIVE.value,
            help="live: call Gemini; record/replay/auto: use LLM cassettes (default: live)",
        )
        p.add_argument("--cassettes", type=Path, default=Path("cassettes"), help="Cassette directory")
        p.add_argument("--runs-dir", type=Path, help="Where run state and workspaces are stored")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    settings = Settings.from_env()
    if args.runs_dir:
        settings = replace(settings, runs_dir=args.runs_dir)
    store = CheckpointStore(settings.runs_dir)
    console = Console()

    if args.command == "run":
        repo = str(args.repo.resolve()) if args.repo else None
        state = RunState(requirement=args.requirement, repo_path=repo)
    else:
        state = store.load(args.run_id)

    audit = AuditLog(store.run_dir(state.run_id) / "audit.jsonl")
    audit.subscribe(ProgressPrinter(console))
    gate = AutoApproveGate() if args.auto_approve else CliGate(console)
    try:
        pipeline = build_pipeline(
            state, settings=settings, gate=gate, mode=LLMMode(args.llm), cassettes=args.cassettes, audit=audit
        )
    except LLMError as exc:
        console.print(f"[red]{exc}[/red]")
        return 2

    console.print(f"[bold]Run {state.run_id}[/bold]  ({store.run_dir(state.run_id)})")
    final = asyncio.run(pipeline.run())

    color = {RunStatus.COMPLETED: "green", RunStatus.ABORTED: "yellow"}.get(final.status, "red")
    console.print(
        f"\n[bold {color}]Run {final.status.value}[/bold {color}]" + (f": {final.error}" if final.error else "")
    )
    console.print(f"Workspace: {pipeline.workspace.root}")
    if final.status is not RunStatus.COMPLETED:
        console.print(f"Resume with: agentforge resume {final.run_id}")
    return 0 if final.status is RunStatus.COMPLETED else 1


if __name__ == "__main__":
    raise SystemExit(main())
