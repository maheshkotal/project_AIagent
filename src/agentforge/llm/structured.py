"""Typed LLM calls: request JSON matching a Pydantic model and let the model self-correct on bad output."""

from __future__ import annotations

import re

from pydantic import BaseModel, ValidationError

from agentforge.llm.base import LLMClient, LLMRequest, SchemaValidationError

_FENCE = re.compile(r"^\s*```(?:json)?\s*\n(.*?)\n\s*```\s*$", re.DOTALL)
_MAX_ECHOED_OUTPUT = 4000


def strip_code_fences(text: str) -> str:
    match = _FENCE.match(text)
    return match.group(1) if match else text.strip()


async def generate_structured[T: BaseModel](
    client: LLMClient,
    *,
    agent: str,
    system: str,
    prompt: str,
    schema: type[T],
    repair_attempts: int = 2,
    temperature: float = 0.2,
) -> T:
    """Call the model and parse its output into `schema`.

    On a validation failure, the validation errors and the offending output are sent back to the
    model so it can correct itself, up to `repair_attempts` times.
    """
    request = LLMRequest(
        agent=agent, system=system, prompt=prompt, temperature=temperature, json_schema=schema.model_json_schema()
    )
    last_error: ValidationError | None = None
    for _ in range(repair_attempts + 1):
        response = await client.complete(request)
        try:
            return schema.model_validate_json(strip_code_fences(response.text))
        except ValidationError as exc:
            last_error = exc
            request = request.model_copy(update={"prompt": _repair_prompt(prompt, response.text, exc)})
    raise SchemaValidationError(
        f"{agent}: output did not match {schema.__name__} after {repair_attempts} repair attempts: {last_error}"
    )


def _repair_prompt(original_prompt: str, bad_output: str, error: ValidationError) -> str:
    return (
        f"{original_prompt}\n\n"
        "---\n"
        "Your previous response was rejected because it does not match the required JSON schema.\n"
        f"Validation errors:\n{error}\n\n"
        f"Previous response (truncated):\n{bad_output[:_MAX_ECHOED_OUTPUT]}\n\n"
        "Return a corrected response as JSON only, matching the schema exactly."
    )
