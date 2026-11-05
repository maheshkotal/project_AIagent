"""Individual validation checks over a generated workspace. Each returns a CheckResult."""

from __future__ import annotations

import ast
import json
import sys
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path

import yaml

from agentforge.models import CheckResult, CheckStatus, TestFailure, TestRun
from agentforge.tools.openapi import contract_problems, operations
from agentforge.tools.runner import run_module
from agentforge.tools.sandbox import Sandbox

CONTRACT_PATH = "contract/openapi.yaml"
ALLOWED_THIRD_PARTY = frozenset({"fastapi", "starlette", "pydantic", "sqlalchemy", "httpx", "uvicorn", "pytest"})
# flake8's "serious" set: syntax errors, invalid comparisons/statements, undefined names.
SERIOUS_LINT_PREFIXES = ("E9", "F63", "F7", "F82")
MAX_DETAIL = 1500


def _python_files(ws: Sandbox) -> list[str]:
    return [p for p in ws.list_files() if p.endswith(".py")]


def check_syntax(ws: Sandbox) -> CheckResult:
    errors = []
    for path in _python_files(ws):
        try:
            ast.parse(ws.read_text(path), filename=path)
        except SyntaxError as exc:
            errors.append(f"{path}:{exc.lineno}: {exc.msg}")
    if not _python_files(ws):
        return CheckResult(name="syntax", status=CheckStatus.FAILED, summary="No Python files were generated")
    if errors:
        return CheckResult(name="syntax", status=CheckStatus.FAILED, summary=f"{len(errors)} file(s)", details=errors)
    return CheckResult(name="syntax", status=CheckStatus.PASSED, summary=f"{len(_python_files(ws))} files parse")


def check_imports(ws: Sandbox, allowed: frozenset[str] = ALLOWED_THIRD_PARTY) -> CheckResult:
    """Guardrail: generated code may only import the stdlib, local modules and allow-listed packages."""
    local = {Path(p).parts[0].removesuffix(".py") for p in ws.list_files()}
    violations = []
    for path in _python_files(ws):
        try:
            tree = ast.parse(ws.read_text(path))
        except SyntaxError:
            continue  # reported by check_syntax
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                names = [node.module]
            else:
                continue
            for name in names:
                top = name.split(".")[0]
                if top not in sys.stdlib_module_names and top not in local and top not in allowed:
                    violations.append(f"{path}:{node.lineno}: import of non-allow-listed package '{top}'")
    if violations:
        return CheckResult(
            name="dependencies", status=CheckStatus.FAILED, summary="Disallowed imports", details=violations
        )
    return CheckResult(name="dependencies", status=CheckStatus.PASSED, summary="Only allow-listed imports")


async def check_lint(ws: Sandbox) -> CheckResult:
    result = await run_module(
        "ruff",
        ["check", "--isolated", "--no-cache", "--select", "E9,F", "--output-format", "json", "."],
        cwd=ws.root,
        timeout_s=60,
    )
    try:
        findings = json.loads(result.stdout or "[]")
    except json.JSONDecodeError:
        return CheckResult(
            name="lint", status=CheckStatus.WARNING, summary="Could not run ruff", details=[result.stderr]
        )
    lines = [
        f"{f['filename'].removeprefix(str(ws.root) + '/')}:{f['location']['row']}: {f['code']} {f['message']}"
        for f in findings
    ]
    serious = [
        line for line, f in zip(lines, findings, strict=True) if (f["code"] or "").startswith(SERIOUS_LINT_PREFIXES)
    ]
    if serious:
        return CheckResult(
            name="lint", status=CheckStatus.FAILED, summary=f"{len(serious)} serious issue(s)", details=serious
        )
    if lines:
        return CheckResult(
            name="lint", status=CheckStatus.WARNING, summary=f"{len(lines)} minor issue(s)", details=lines
        )
    return CheckResult(name="lint", status=CheckStatus.PASSED, summary="No lint findings")


def check_contract_document(ws: Sandbox) -> CheckResult:
    if not ws.exists(CONTRACT_PATH):
        return CheckResult(name="contract", status=CheckStatus.FAILED, summary=f"{CONTRACT_PATH} is missing")
    problems = contract_problems(ws.read_text(CONTRACT_PATH))
    if problems:
        return CheckResult(name="contract", status=CheckStatus.FAILED, summary="Invalid OpenAPI", details=problems)
    return CheckResult(name="contract", status=CheckStatus.PASSED, summary="Valid OpenAPI document")


async def check_contract_conformance(ws: Sandbox) -> CheckResult:
    """Compare the operations the generated app actually exposes with the designed contract."""
    name = "contract_conformance"
    if not ws.exists(CONTRACT_PATH) or contract_problems(ws.read_text(CONTRACT_PATH)):
        return CheckResult(name=name, status=CheckStatus.SKIPPED, summary="No valid contract to compare against")
    result = await run_module("agentforge.tools.contract_probe", [], cwd=ws.root, timeout_s=60)
    if result.returncode != 0:
        return CheckResult(
            name=name,
            status=CheckStatus.FAILED,
            summary="Could not build the app via app.main.create_app()",
            details=[result.stderr[-MAX_DETAIL:]],
        )
    designed = operations(yaml.safe_load(ws.read_text(CONTRACT_PATH)))
    actual = operations(json.loads(result.stdout))
    missing, extra = sorted(designed - actual), sorted(actual - designed)
    details = [f"missing: {m} {p}" for m, p in missing] + [f"not in contract: {m} {p}" for m, p in extra]
    if missing:
        return CheckResult(
            name=name,
            status=CheckStatus.FAILED,
            summary=f"{len(missing)} operation(s) not implemented",
            details=details,
        )
    if extra:
        return CheckResult(
            name=name, status=CheckStatus.WARNING, summary="App exposes undocumented operations", details=details
        )
    return CheckResult(name=name, status=CheckStatus.PASSED, summary=f"All {len(designed)} operations implemented")


async def run_tests(ws: Sandbox, timeout_s: float = 180) -> tuple[CheckResult, TestRun | None]:
    name = "tests"
    if not any(p.startswith("tests/") for p in _python_files(ws)):
        return CheckResult(name=name, status=CheckStatus.FAILED, summary="No tests were generated"), None
    with tempfile.TemporaryDirectory() as tmp:
        junit = Path(tmp) / "junit.xml"
        result = await run_module(
            "pytest",
            # -c /dev/null + --rootdir: ignore any ini file outside the workspace.
            ["-q", "-p", "no:cacheprovider", "-c", "/dev/null", "--rootdir", ".", f"--junitxml={junit}", "tests"],
            cwd=ws.root,
            timeout_s=timeout_s,
        )
        tail = (result.stdout + result.stderr)[-3000:]
        if result.timed_out:
            return CheckResult(name=name, status=CheckStatus.FAILED, summary=result.stderr), TestRun(output_tail=tail)
        if not junit.exists():
            return CheckResult(name=name, status=CheckStatus.FAILED, summary="pytest crashed", details=[tail]), TestRun(
                output_tail=tail
            )
        run = _parse_junit(junit, tail)
    if result.returncode == 5 or run.total == 0:
        return CheckResult(name=name, status=CheckStatus.FAILED, summary="No tests collected", details=[tail]), run
    summary = f"{run.passed} passed, {run.failed} failed, {run.errors} errors, {run.skipped} skipped"
    if run.failed or run.errors or result.returncode != 0:
        details = [f"{f.test_id}: {f.message}" for f in run.failures] or [tail]
        return CheckResult(name=name, status=CheckStatus.FAILED, summary=summary, details=details), run
    return CheckResult(name=name, status=CheckStatus.PASSED, summary=summary), run


def _parse_junit(path: Path, tail: str) -> TestRun:
    run = TestRun(output_tail=tail)
    for case in ET.parse(path).getroot().iter("testcase"):
        test_id = f"{case.get('classname', '')}::{case.get('name', '')}"
        outcome = next((child for child in case if child.tag in ("failure", "error", "skipped")), None)
        if outcome is None:
            run.passed += 1
        elif outcome.tag == "skipped":
            run.skipped += 1
        else:
            if outcome.tag == "failure":
                run.failed += 1
            else:
                run.errors += 1
            message = (outcome.get("message") or "") + "\n" + (outcome.text or "")
            run.failures.append(TestFailure(test_id=test_id, message=message.strip()[-MAX_DETAIL:]))
    return run
