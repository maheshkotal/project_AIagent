"""Append-only JSONL audit trail of LLM calls, phase transitions, task changes and human decisions."""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

Listener = Callable[[dict[str, Any]], None]


class AuditLog:
    def __init__(self, path: Path | None = None) -> None:
        self._path = path
        self._listeners: list[Listener] = []
        if path is not None:
            path.parent.mkdir(parents=True, exist_ok=True)

    def subscribe(self, listener: Listener) -> None:
        """Listeners (e.g. the CLI progress view) receive every event as it is recorded."""
        self._listeners.append(listener)

    def record(self, event: str, **data: Any) -> None:
        entry = {"ts": datetime.now(UTC).isoformat(), "event": event, **data}
        if self._path is not None:
            with self._path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(entry, default=str) + "\n")
        for listener in self._listeners:
            listener(entry)
