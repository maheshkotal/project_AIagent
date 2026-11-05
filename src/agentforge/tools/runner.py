"""Allow-listed subprocess execution for validating generated code.

Generated code is untrusted: it runs only via allow-listed Python modules, inside the workspace,
with a scrubbed environment (no API keys) and a hard timeout. This is process-level isolation;
container isolation is the production-grade next step (see docs/TESTING.md).
"""

from __future__ import annotations

import asyncio
import os
import sys
from dataclasses import dataclass
from pathlib import Path

from agentforge.tools.sandbox import SandboxViolation

ALLOWED_MODULES = frozenset({"pytest", "ruff", "agentforge.tools.contract_probe"})


@dataclass(frozen=True)
class CommandResult:
    returncode: int
    stdout: str
    stderr: str
    timed_out: bool = False


def scrubbed_env(workspace: Path) -> dict[str, str]:
    return {
        "PATH": os.environ.get("PATH", ""),
        "HOME": str(workspace),
        "LANG": "C.UTF-8",
        "PYTHONPATH": str(workspace),
        "PYTHONDONTWRITEBYTECODE": "1",
    }


async def run_module(module: str, args: list[str], *, cwd: Path, timeout_s: float) -> CommandResult:
    if module not in ALLOWED_MODULES:
        raise SandboxViolation(f"Module {module!r} is not allow-listed for execution")
    proc = await asyncio.create_subprocess_exec(
        sys.executable,
        "-m",
        module,
        *args,
        cwd=cwd,
        env=scrubbed_env(cwd),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout_s)
    except TimeoutError:
        proc.kill()
        await proc.wait()
        return CommandResult(-9, "", f"Timed out after {timeout_s}s", timed_out=True)
    return CommandResult(proc.returncode or 0, stdout.decode(errors="replace"), stderr.decode(errors="replace"))
