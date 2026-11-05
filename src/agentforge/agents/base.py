"""Common machinery for LLM-backed agents."""

from __future__ import annotations

from functools import cache
from importlib import resources
from typing import ClassVar

from pydantic import BaseModel

from agentforge.config import Settings
from agentforge.llm import LLMClient, generate_structured


@cache
def load_prompt(name: str) -> str:
    return resources.files("agentforge.prompts").joinpath(f"{name}.md").read_text(encoding="utf-8")


class LLMAgent:
    name: ClassVar[str]
    # The system prompt is composed from these prompt files, in order.
    prompt_files: ClassVar[tuple[str, ...]]

    def __init__(self, llm: LLMClient, settings: Settings) -> None:
        self.llm = llm
        self.settings = settings
        self.system = "\n\n".join(load_prompt(p) for p in self.prompt_files)

    async def ask[T: BaseModel](self, prompt: str, schema: type[T], *, purpose: str = "") -> T:
        return await generate_structured(
            self.llm,
            agent=f"{self.name}.{purpose}" if purpose else self.name,
            system=self.system,
            prompt=prompt,
            schema=schema,
            repair_attempts=self.settings.schema_repair_attempts,
        )
