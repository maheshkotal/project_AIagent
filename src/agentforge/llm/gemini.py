"""Google Gemini adapter (official `google-genai` SDK)."""

from __future__ import annotations

import time

import httpx
from google import genai
from google.genai import errors, types

from agentforge.llm.base import LLMClient, LLMError, LLMRequest, LLMResponse, TransientLLMError

RETRYABLE_STATUS = frozenset({408, 429, 500, 502, 503, 504})


class GeminiClient(LLMClient):
    def __init__(self, *, api_key: str, model: str, client: genai.Client | None = None) -> None:
        if not api_key and client is None:
            raise LLMError("GEMINI_API_KEY is not set. Add it to .env, or use `--llm replay` with recorded cassettes.")
        self._model = model
        self._client = client or genai.Client(api_key=api_key)

    async def complete(self, request: LLMRequest) -> LLMResponse:
        config = types.GenerateContentConfig(
            system_instruction=request.system,
            temperature=request.temperature,
            response_mime_type="application/json" if request.json_schema else None,
            response_json_schema=request.json_schema,
        )
        started = time.perf_counter()
        try:
            response = await self._client.aio.models.generate_content(
                model=self._model, contents=request.prompt, config=config
            )
        except errors.APIError as exc:
            message = f"Gemini API error {exc.code}: {exc.message}"
            if exc.code in RETRYABLE_STATUS:
                raise TransientLLMError(message) from exc
            raise LLMError(message) from exc
        except httpx.TransportError as exc:
            raise TransientLLMError(f"Gemini transport error: {exc!r}") from exc
        latency_ms = (time.perf_counter() - started) * 1000

        text = response.text
        if not text:
            reason = response.candidates[0].finish_reason if response.candidates else "no candidates"
            raise TransientLLMError(f"Gemini returned an empty response (finish_reason={reason})")
        usage = response.usage_metadata
        return LLMResponse(
            text=text,
            model=self._model,
            input_tokens=(usage.prompt_token_count or 0) if usage else 0,
            output_tokens=((usage.candidates_token_count or 0) + (usage.thoughts_token_count or 0)) if usage else 0,
            latency_ms=latency_ms,
        )
