"""Persist run state after every step so an interrupted or paused run can be resumed."""

from __future__ import annotations

import os
from pathlib import Path

from agentforge.orchestrator.state import RunState


class CheckpointStore:
    def __init__(self, runs_dir: Path) -> None:
        self.runs_dir = runs_dir

    def run_dir(self, run_id: str) -> Path:
        return self.runs_dir / run_id

    def save(self, state: RunState) -> Path:
        path = self.run_dir(state.run_id) / "state.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(state.model_dump_json(indent=2), encoding="utf-8")
        os.replace(tmp, path)  # atomic: a crash never leaves a half-written checkpoint
        return path

    def load(self, run_id: str) -> RunState:
        path = self.run_dir(run_id) / "state.json"
        if not path.exists():
            raise FileNotFoundError(f"No checkpoint for run {run_id!r} at {path}")
        return RunState.model_validate_json(path.read_text(encoding="utf-8"))
