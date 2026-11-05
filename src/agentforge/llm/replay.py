"""Record/replay client: capture real LLM responses once, then replay them deterministically.

Used for CI, offline demos and reproducible example runs. Prompts must therefore be deterministic
(no timestamps, run IDs or absolute paths), otherwise the cache key changes between runs.
"""

from __future__ import annotations

import json
import re
from enum import StrEnum
from pathlib import Path

from agentforge.llm.base import LLMClient, LLMError, LLMRequest, LLMResponse


class ReplayMode(StrEnum):
    RECORD = "record"  # always call the inner client and (over)write the cassette
    REPLAY = "replay"  # never call the inner client; a missing cassette is an error
    AUTO = "auto"  # replay when a cassette exists, otherwise record


class ReplayMissError(LLMError):
    retryable = False


class ReplayClient(LLMClient):
    def __init__(self, cassette_dir: Path, mode: ReplayMode, inner: LLMClient | None = None) -> None:
        if mode is not ReplayMode.REPLAY and inner is None:
            raise ValueError(f"mode {mode.value!r} needs an inner client to record from")
        self._dir = cassette_dir
        self._mode = mode
        self._inner = inner

    def _path(self, request: LLMRequest) -> Path:
        agent = re.sub(r"[^a-zA-Z0-9_-]", "_", request.agent)
        return self._dir / f"{agent}-{request.cache_key()[:16]}.json"

    async def complete(self, request: LLMRequest) -> LLMResponse:
        path = self._path(request)
        if self._mode is not ReplayMode.RECORD and path.exists():
            return LLMResponse.model_validate(json.loads(path.read_text(encoding="utf-8"))["response"])
        if self._mode is ReplayMode.REPLAY or self._inner is None:
            raise ReplayMissError(f"No recorded response for agent {request.agent!r} ({path.name})")
        response = await self._inner.complete(request)
        self._dir.mkdir(parents=True, exist_ok=True)
        cassette = {"request": request.model_dump(mode="json"), "response": response.model_dump(mode="json")}
        path.write_text(json.dumps(cassette, indent=2), encoding="utf-8")
        return response
