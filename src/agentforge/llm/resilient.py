"""Decorator client that adds guardrails to any LLMClient: budget, rate limiting, retries, auditing."""

from __future__ import annotations

import asyncio
import random
from collections.abc import Awaitable, Callable

from agentforge.audit import AuditLog
from agentforge.llm.base import (
    BudgetExceededError,
    LLMClient,
    LLMRequest,
    LLMResponse,
    TransientLLMError,
)
from agentforge.llm.ratelimit import RateLimiter


class RunBudget:
    """Hard per-run caps on LLM calls and tokens, shared by every agent in a run."""

    def __init__(self, max_calls: int, max_tokens: int) -> None:
        self.max_calls = max_calls
        self.max_tokens = max_tokens
        self.calls = 0
        self.tokens = 0

    def reserve_call(self) -> None:
        if self.calls >= self.max_calls:
            raise BudgetExceededError(f"LLM call budget exhausted ({self.max_calls} calls)")
        if self.tokens >= self.max_tokens:
            raise BudgetExceededError(f"LLM token budget exhausted ({self.max_tokens} tokens)")
        self.calls += 1

    def record_tokens(self, tokens: int) -> None:
        self.tokens += tokens


class ResilientClient(LLMClient):
    def __init__(
        self,
        inner: LLMClient,
        *,
        limiter: RateLimiter,
        budget: RunBudget,
        audit: AuditLog,
        max_retries: int = 4,
        backoff_base_s: float = 2.0,
        backoff_max_s: float = 60.0,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        jitter: Callable[[], float] = random.random,
    ) -> None:
        self._inner = inner
        self._limiter = limiter
        self._budget = budget
        self._audit = audit
        self._max_retries = max_retries
        self._backoff_base_s = backoff_base_s
        self._backoff_max_s = backoff_max_s
        self._sleep = sleep
        self._jitter = jitter

    async def complete(self, request: LLMRequest) -> LLMResponse:
        prompt_hash = request.cache_key()[:12]
        attempt = 0
        while True:
            attempt += 1
            # Every attempt (including retries) consumes provider quota, so every attempt is budgeted.
            self._budget.reserve_call()
            try:
                async with self._limiter.slot():
                    response = await self._inner.complete(request)
            except TransientLLMError as exc:
                if attempt > self._max_retries:
                    self._audit.record(
                        "llm_failed", agent=request.agent, prompt_hash=prompt_hash, attempts=attempt, error=str(exc)
                    )
                    raise
                delay = self._backoff_delay(attempt)
                self._audit.record(
                    "llm_retry",
                    agent=request.agent,
                    prompt_hash=prompt_hash,
                    attempt=attempt,
                    delay_s=round(delay, 2),
                    error=str(exc),
                )
                await self._sleep(delay)
                continue
            self._budget.record_tokens(response.input_tokens + response.output_tokens)
            self._audit.record(
                "llm_call",
                agent=request.agent,
                model=response.model,
                prompt_hash=prompt_hash,
                attempt=attempt,
                input_tokens=response.input_tokens,
                output_tokens=response.output_tokens,
                latency_ms=round(response.latency_ms, 1),
            )
            return response

    def _backoff_delay(self, attempt: int) -> float:
        """Exponential backoff with jitter in [50%, 100%] of the capped delay."""
        capped = min(self._backoff_max_s, self._backoff_base_s * 2 ** (attempt - 1))
        return capped * (0.5 + self._jitter() / 2)
