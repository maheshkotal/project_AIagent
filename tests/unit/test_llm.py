from __future__ import annotations

import json
from types import SimpleNamespace

import httpx
import pytest
from google.genai import errors
from pydantic import BaseModel

from agentforge.audit import AuditLog
from agentforge.llm.base import (
    BudgetExceededError,
    LLMError,
    LLMRequest,
    LLMResponse,
    SchemaValidationError,
    TransientLLMError,
)
from agentforge.llm.fake import FakeClient
from agentforge.llm.gemini import GeminiClient
from agentforge.llm.ratelimit import RateLimiter
from agentforge.llm.replay import ReplayClient, ReplayMissError, ReplayMode
from agentforge.llm.resilient import ResilientClient, RunBudget
from agentforge.llm.structured import generate_structured, strip_code_fences


class FakeClock:
    def __init__(self) -> None:
        self.now = 0.0
        self.sleeps: list[float] = []

    def __call__(self) -> float:
        return self.now

    async def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


class Point(BaseModel):
    x: int
    y: int


def request(agent: str = "analyst", prompt: str = "p") -> LLMRequest:
    return LLMRequest(agent=agent, system="s", prompt=prompt)


# ---- rate limiter ---------------------------------------------------------------------------


async def test_rate_limiter_waits_when_window_is_full() -> None:
    clock = FakeClock()
    limiter = RateLimiter(2, 5, clock=clock, sleep=clock.sleep)
    for _ in range(3):
        async with limiter.slot():
            pass
    assert clock.sleeps == [60.0]


async def test_rate_limiter_frees_capacity_after_window() -> None:
    clock = FakeClock()
    limiter = RateLimiter(1, 1, clock=clock, sleep=clock.sleep)
    async with limiter.slot():
        pass
    clock.now = 61.0
    async with limiter.slot():
        pass
    assert clock.sleeps == []


def test_rate_limiter_rejects_invalid_limits() -> None:
    with pytest.raises(ValueError):
        RateLimiter(0, 1)


# ---- resilient client -----------------------------------------------------------------------


def resilient(inner: FakeClient, *, max_calls: int = 10, max_retries: int = 2) -> tuple[ResilientClient, list, list]:
    events: list[dict] = []
    audit = AuditLog()
    audit.subscribe(events.append)
    sleeps: list[float] = []

    async def sleep(seconds: float) -> None:
        sleeps.append(seconds)

    client = ResilientClient(
        inner,
        limiter=RateLimiter(100, 10),
        budget=RunBudget(max_calls=max_calls, max_tokens=10_000),
        audit=audit,
        max_retries=max_retries,
        backoff_base_s=1.0,
        backoff_max_s=3.0,
        sleep=sleep,
        jitter=lambda: 1.0,
    )
    return client, events, sleeps


async def test_resilient_retries_transient_errors_with_capped_backoff() -> None:
    inner = FakeClient({"analyst": [TransientLLMError("429"), TransientLLMError("503"), "ok"]})
    client, events, sleeps = resilient(inner, max_retries=3)
    response = await client.complete(request())
    assert response.text == "ok"
    assert sleeps == [1.0, 2.0]
    assert [e["event"] for e in events] == ["llm_retry", "llm_retry", "llm_call"]


async def test_resilient_gives_up_after_max_retries() -> None:
    inner = FakeClient({"analyst": [TransientLLMError("429")] * 5})
    client, events, _ = resilient(inner, max_retries=2)
    with pytest.raises(TransientLLMError):
        await client.complete(request())
    assert len(inner.requests) == 3
    assert events[-1]["event"] == "llm_failed"


async def test_resilient_does_not_retry_permanent_errors() -> None:
    inner = FakeClient({"analyst": [LLMError("400 bad request")]})
    client, _, _ = resilient(inner)
    with pytest.raises(LLMError):
        await client.complete(request())
    assert len(inner.requests) == 1


async def test_budget_counts_retries_and_blocks_further_calls() -> None:
    inner = FakeClient({"analyst": [TransientLLMError("429"), "ok"]})
    client, _, _ = resilient(inner, max_calls=2)
    await client.complete(request())
    with pytest.raises(BudgetExceededError):
        await client.complete(request())


def test_budget_errors_are_not_retryable() -> None:
    assert BudgetExceededError.retryable is False
    assert TransientLLMError.retryable is True


# ---- structured output ----------------------------------------------------------------------


async def test_generate_structured_parses_fenced_json() -> None:
    client = FakeClient({"analyst": ['```json\n{"x": 1, "y": 2}\n```']})
    point = await generate_structured(client, agent="analyst", system="s", prompt="p", schema=Point)
    assert point == Point(x=1, y=2)
    assert client.requests[0].json_schema == Point.model_json_schema()


async def test_generate_structured_feeds_validation_errors_back() -> None:
    client = FakeClient({"analyst": ['{"x": "not a number"}', '{"x": 1, "y": 2}']})
    point = await generate_structured(client, agent="analyst", system="s", prompt="original", schema=Point)
    assert point.x == 1
    repair_prompt = client.requests[1].prompt
    assert repair_prompt.startswith("original")
    assert "Validation errors" in repair_prompt and "not a number" in repair_prompt


async def test_generate_structured_gives_up_after_repair_attempts() -> None:
    client = FakeClient({"analyst": ["nope"] * 3})
    with pytest.raises(SchemaValidationError):
        await generate_structured(client, agent="analyst", system="s", prompt="p", schema=Point, repair_attempts=2)
    assert len(client.requests) == 3


def test_strip_code_fences_leaves_plain_text() -> None:
    assert strip_code_fences('  {"a": 1} ') == '{"a": 1}'


# ---- record / replay ------------------------------------------------------------------------


async def test_replay_records_then_replays_without_inner(tmp_path) -> None:
    recorder = ReplayClient(tmp_path, ReplayMode.RECORD, FakeClient({"analyst": ["recorded"]}))
    await recorder.complete(request())
    replayer = ReplayClient(tmp_path, ReplayMode.REPLAY)
    assert (await replayer.complete(request())).text == "recorded"
    cassette = json.loads(next(tmp_path.glob("analyst-*.json")).read_text())
    assert cassette["request"]["prompt"] == "p"


async def test_replay_miss_is_a_non_retryable_error(tmp_path) -> None:
    replayer = ReplayClient(tmp_path, ReplayMode.REPLAY)
    with pytest.raises(ReplayMissError) as info:
        await replayer.complete(request(prompt="never recorded"))
    assert info.value.retryable is False


async def test_replay_auto_mode_only_calls_inner_on_miss(tmp_path) -> None:
    inner = FakeClient({"analyst": ["first", "second"]})
    client = ReplayClient(tmp_path, ReplayMode.AUTO, inner)
    assert (await client.complete(request())).text == "first"
    assert (await client.complete(request())).text == "first"
    assert len(inner.requests) == 1


def test_replay_record_mode_requires_inner(tmp_path) -> None:
    with pytest.raises(ValueError):
        ReplayClient(tmp_path, ReplayMode.RECORD)


# ---- Gemini adapter (SDK stubbed) -----------------------------------------------------------


def gemini_with(behaviour) -> tuple[GeminiClient, list[dict]]:
    calls: list[dict] = []

    async def generate_content(**kwargs):
        calls.append(kwargs)
        if isinstance(behaviour, Exception):
            raise behaviour
        return behaviour

    sdk = SimpleNamespace(aio=SimpleNamespace(models=SimpleNamespace(generate_content=generate_content)))
    return GeminiClient(api_key="", model="gemini-test", client=sdk), calls


def sdk_response(text: str | None) -> SimpleNamespace:
    usage = SimpleNamespace(prompt_token_count=10, candidates_token_count=5, thoughts_token_count=3)
    return SimpleNamespace(text=text, usage_metadata=usage, candidates=[SimpleNamespace(finish_reason="SAFETY")])


async def test_gemini_maps_request_and_usage() -> None:
    client, calls = gemini_with(sdk_response('{"x": 1, "y": 2}'))
    req = LLMRequest(agent="a", system="sys", prompt="hi", json_schema=Point.model_json_schema())
    response = await client.complete(req)
    assert response == LLMResponse(
        text='{"x": 1, "y": 2}', model="gemini-test", input_tokens=10, output_tokens=8, latency_ms=response.latency_ms
    )
    config = calls[0]["config"]
    assert calls[0]["model"] == "gemini-test" and calls[0]["contents"] == "hi"
    assert config.system_instruction == "sys"
    assert config.response_mime_type == "application/json"
    assert config.response_json_schema == Point.model_json_schema()


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (errors.APIError(429, {"error": {"message": "quota", "status": "RESOURCE_EXHAUSTED"}}), TransientLLMError),
        (errors.APIError(503, {"error": {"message": "unavailable"}}), TransientLLMError),
        (httpx.ConnectError("boom"), TransientLLMError),
    ],
)
async def test_gemini_maps_transient_errors(error, expected) -> None:
    client, _ = gemini_with(error)
    with pytest.raises(expected):
        await client.complete(request())


async def test_gemini_maps_client_errors_as_permanent() -> None:
    client, _ = gemini_with(errors.APIError(400, {"error": {"message": "bad schema"}}))
    with pytest.raises(LLMError) as info:
        await client.complete(request())
    assert not isinstance(info.value, TransientLLMError)


async def test_gemini_empty_response_is_transient() -> None:
    client, _ = gemini_with(sdk_response(None))
    with pytest.raises(TransientLLMError, match="SAFETY"):
        await client.complete(request())


def test_gemini_requires_api_key() -> None:
    with pytest.raises(LLMError, match="GEMINI_API_KEY"):
        GeminiClient(api_key="", model="m")
