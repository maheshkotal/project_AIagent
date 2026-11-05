"""Provider-agnostic LLM interface. Agents depend only on these types, never on a vendor SDK."""

from __future__ import annotations

import hashlib
import json
from abc import ABC, abstractmethod
from typing import Any

from pydantic import BaseModel


class LLMRequest(BaseModel):
    agent: str
    system: str
    prompt: str
    temperature: float = 0.2
    # JSON schema the response must conform to; None means free-form text.
    json_schema: dict[str, Any] | None = None

    def cache_key(self) -> str:
        """Stable hash of everything that influences the response (used by record/replay)."""
        payload = json.dumps(self.model_dump(mode="json"), sort_keys=True)
        return hashlib.sha256(payload.encode()).hexdigest()


class LLMResponse(BaseModel):
    text: str
    model: str = ""
    input_tokens: int = 0
    output_tokens: int = 0
    latency_ms: float = 0.0


class LLMError(Exception):
    """Base class for LLM failures. `retryable` tells orchestration layers whether retrying can help."""

    retryable = True


class TransientLLMError(LLMError):
    """Rate limits, timeouts, 5xx: safe to retry with backoff."""


class BudgetExceededError(LLMError):
    """Per-run call or token budget exhausted. Retrying would only exceed it further."""

    retryable = False


class SchemaValidationError(LLMError):
    """The model kept returning output that does not match the requested schema."""


class LLMClient(ABC):
    @abstractmethod
    async def complete(self, request: LLMRequest) -> LLMResponse:
        """Return the model's response, raising LLMError (or a subclass) on failure."""
