"""Scripted in-memory client for unit tests."""

from __future__ import annotations

import json
from collections import deque
from collections.abc import Callable, Iterable
from typing import Any

from pydantic import BaseModel

from agentforge.llm.base import LLMClient, LLMError, LLMRequest, LLMResponse

ScriptItem = str | BaseModel | dict[str, Any] | Exception
Responder = Callable[[LLMRequest], ScriptItem]


class FakeClient(LLMClient):
    """Returns scripted responses per agent, in order. Exceptions in the script are raised."""

    def __init__(
        self, script: dict[str, Iterable[ScriptItem]] | None = None, responder: Responder | None = None
    ) -> None:
        self._queues = {agent: deque(items) for agent, items in (script or {}).items()}
        self._responder = responder
        self.requests: list[LLMRequest] = []

    async def complete(self, request: LLMRequest) -> LLMResponse:
        self.requests.append(request)
        queue = self._queues.get(request.agent)
        if queue:
            item = queue.popleft()
        elif self._responder is not None:
            item = self._responder(request)
        else:
            raise LLMError(f"FakeClient has no scripted response for agent {request.agent!r}")
        if isinstance(item, Exception):
            raise item
        if isinstance(item, BaseModel):
            text = item.model_dump_json()
        elif isinstance(item, dict):
            text = json.dumps(item)
        else:
            text = item
        return LLMResponse(text=text, model="fake", input_tokens=len(request.prompt) // 4, output_tokens=len(text) // 4)
