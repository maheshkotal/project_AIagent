"""Brownfield impact analysis: static repository map + LLM reasoning over the existing code."""

from __future__ import annotations

from agentforge.agents.base import LLMAgent
from agentforge.agents.context import render_files, render_spec, section
from agentforge.models import ImpactReport, NormalizedSpec
from agentforge.tools.code_index import repo_map
from agentforge.tools.sandbox import Sandbox

MAX_SOURCE_CHARS = 200_000
SOURCE_SUFFIXES = (".py", ".yaml", ".yml", ".toml", ".md", ".cfg", ".ini")


class ImpactAnalystAgent(LLMAgent):
    name = "impact"
    prompt_files = ("impact",)

    async def analyze(self, spec: NormalizedSpec, workspace: Sandbox) -> ImpactReport:
        included, omitted, budget = [], [], MAX_SOURCE_CHARS
        for path in workspace.list_files():
            if not path.endswith(SOURCE_SUFFIXES):
                continue
            size = len(workspace.read_text(path))
            if size <= budget:
                included.append(path)
                budget -= size
            else:
                omitted.append(path)
        parts = [
            section("Requested change", render_spec(spec)),
            section("Repository map (static analysis)", repo_map(workspace)),
            section("Source files", render_files(workspace, included)),
        ]
        if omitted:
            parts.append(section("Files omitted for size (see the map)", "\n".join(f"- `{p}`" for p in omitted)))
        return await self.ask("\n".join(parts), ImpactReport)
