"""Runtime configuration, loaded from environment variables (and an optional `.env` file)."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

DEFAULT_MODEL = "gemini-3.8-flash"


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        return int(raw)
    except ValueError as exc:
        raise ValueError(f"Environment variable {name} must be an integer, got {raw!r}") from exc


@dataclass(frozen=True)
class Settings:
    """All tunables in one place. Defaults are sized for the Gemini free tier."""

    gemini_api_key: str | None = None
    model: str = DEFAULT_MODEL
    # Rate limiting: stay under the provider's requests-per-minute and cap in-flight calls.
    requests_per_minute: int = 10
    max_concurrency: int = 3
    # Transient-error retries per LLM call (429 / 5xx), with exponential backoff.
    llm_max_retries: int = 4
    llm_backoff_base_s: float = 2.0
    llm_backoff_max_s: float = 60.0
    # Times a model may self-correct after returning output that fails schema validation.
    schema_repair_attempts: int = 2
    # Hard budgets per run (guardrails against runaway loops / cost).
    max_llm_calls_per_run: int = 80
    max_tokens_per_run: int = 2_000_000
    # Orchestration limits.
    task_max_attempts: int = 2
    max_replans: int = 2
    max_repair_rounds: int = 3
    # Times a human (or auto mode) may re-run failed tasks after an execution round.
    max_execution_retries: int = 1
    runs_dir: Path = Path("runs")

    @classmethod
    def from_env(cls, env_file: Path | None = None) -> Settings:
        load_dotenv(env_file or Path(".env"), override=False)
        return cls(
            gemini_api_key=os.getenv("GEMINI_API_KEY") or None,
            model=os.getenv("AGENTFORGE_MODEL", DEFAULT_MODEL),
            requests_per_minute=_env_int("AGENTFORGE_RPM", cls.requests_per_minute),
            max_concurrency=_env_int("AGENTFORGE_MAX_CONCURRENCY", cls.max_concurrency),
            max_llm_calls_per_run=_env_int("AGENTFORGE_MAX_LLM_CALLS", cls.max_llm_calls_per_run),
            max_tokens_per_run=_env_int("AGENTFORGE_MAX_TOKENS", cls.max_tokens_per_run),
            runs_dir=Path(os.getenv("AGENTFORGE_RUNS_DIR", str(cls.runs_dir))),
        )
