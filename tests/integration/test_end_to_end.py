"""End-to-end: full pipeline with a scripted LLM, real file writes and real validation (pytest/ruff/contract).

The scripted coder ships a bug (resolve returns 200 JSON instead of a 307 redirect). The contract-first
tests catch it, the reviewer attributes the fault to the code, the coder repairs it, and the second
validation round passes.
"""

from __future__ import annotations

from agentforge.audit import AuditLog
from agentforge.config import Settings
from agentforge.factory import build_agents
from agentforge.llm.fake import FakeClient
from agentforge.models import (
    Ambiguity,
    CheckStatus,
    DesignDecision,
    DesignDoc,
    FileBundle,
    GeneratedFile,
    Impact,
    ModuleSpec,
    NormalizedSpec,
    ReviewVerdict,
    RiskItem,
    RiskReport,
    ScenarioType,
    Task,
    TaskGraph,
)
from agentforge.orchestrator import CheckpointStore, Pipeline, RunState, RunStatus, ScriptedGate

CONTRACT = """\
openapi: 3.1.0
info:
  title: Mini shortener
  version: 1.0.0
paths:
  /links:
    post:
      operationId: createLink
      requestBody:
        required: true
        content:
          application/json:
            schema:
              $ref: '#/components/schemas/LinkIn'
      responses:
        '201':
          description: Created
          content:
            application/json:
              schema:
                $ref: '#/components/schemas/LinkOut'
  /{code}:
    get:
      operationId: resolve
      parameters:
        - name: code
          in: path
          required: true
          schema:
            type: string
      responses:
        '307':
          description: Redirect to the original URL
        '404':
          description: Unknown code
components:
  schemas:
    LinkIn:
      type: object
      required: [url]
      properties:
        url:
          type: string
    LinkOut:
      type: object
      required: [code]
      properties:
        code:
          type: string
"""

CODES_PY = '''\
"""Short-code generation."""

ALPHABET = "0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ"


def encode(n: int) -> str:
    """Base62-encode a non-negative integer."""
    if n < 0:
        raise ValueError("n must be non-negative")
    if n == 0:
        return ALPHABET[0]
    digits = []
    while n:
        n, rem = divmod(n, 62)
        digits.append(ALPHABET[rem])
    return "".join(reversed(digits))
'''

MAIN_TEMPLATE = '''\
"""Application factory."""

from fastapi import FastAPI, HTTPException
from fastapi.responses import RedirectResponse
from pydantic import BaseModel

from app.codes import encode


class LinkIn(BaseModel):
    url: str


class LinkOut(BaseModel):
    code: str


def create_app(database_url: str | None = None) -> FastAPI:
    app = FastAPI(title="Mini shortener")
    links: dict[str, str] = {{}}

    @app.post("/links", status_code=201, response_model=LinkOut)
    def create_link(body: LinkIn) -> LinkOut:
        code = encode(len(links) + 1)
        links[code] = body.url
        return LinkOut(code=code)

    @app.get("/{{code}}")
    def resolve(code: str):
        if code not in links:
            raise HTTPException(status_code=404, detail="Unknown code")
        {resolve_body}

    return app
'''
BUGGY_MAIN = MAIN_TEMPLATE.format(resolve_body='return {"url": links[code]}')
FIXED_MAIN = MAIN_TEMPLATE.format(resolve_body="return RedirectResponse(links[code], status_code=307)")

CONFTEST = """\
import pytest
from fastapi.testclient import TestClient

from app.main import create_app


@pytest.fixture
def client(tmp_path):
    with TestClient(create_app(f"sqlite:///{tmp_path}/test.db")) as test_client:
        yield test_client
"""

UNIT_TESTS = """\
import pytest

from app.codes import encode


def test_encode_small_numbers():
    assert encode(0) == "0"
    assert encode(61) == "Z"
    assert encode(62) == "10"


def test_encode_rejects_negative():
    with pytest.raises(ValueError):
        encode(-1)
"""

API_TESTS = """\
def test_create_and_resolve_redirects(client):
    created = client.post("/links", json={"url": "https://example.com/a"})
    assert created.status_code == 201
    response = client.get(f"/{created.json()['code']}", follow_redirects=False)
    assert response.status_code == 307
    assert response.headers["location"] == "https://example.com/a"


def test_unknown_code_is_404(client):
    assert client.get("/nope", follow_redirects=False).status_code == 404
"""

SPEC = NormalizedSpec(
    title="Mini URL shortener",
    scenario=ScenarioType.GREENFIELD,
    problem_statement="Create short codes for URLs and redirect.",
    functional_requirements=["POST /links returns a code", "GET /{code} redirects with 307"],
    non_functional_requirements=["p99 redirect latency < 50 ms (proposed)"],
    ambiguities=[Ambiguity(id="A1", question="Analytics?", default_assumption="Out of scope", impact=Impact.LOW)],
)

PLAN = TaskGraph(
    rationale="Contract first; code and tests in parallel.",
    tasks=[
        Task(id="design", title="Design", agent="architect", description="Design the service"),
        Task(id="implement", title="Implement", agent="coder", description="Owns app/", depends_on=["design"]),
        Task(id="write_tests", title="Tests", agent="tester", description="Owns tests/", depends_on=["design"]),
        Task(id="write_docs", title="Docs", agent="docs", description="README", depends_on=["design"]),
        Task(id="assess_risk", title="Risks", agent="risk", description="Risks", depends_on=["design"]),
    ],
)

DESIGN = DesignDoc(
    overview="A single FastAPI service with an in-memory store.",
    architecture_diagram="flowchart LR\n  client --> api --> store",
    modules=[
        ModuleSpec(path="app/__init__.py", responsibility="Package marker", public_interface=[]),
        ModuleSpec(path="app/codes.py", responsibility="Short codes", public_interface=["def encode(n: int) -> str"]),
        ModuleSpec(
            path="app/main.py",
            responsibility="App factory",
            public_interface=["def create_app(database_url: str | None = None) -> FastAPI"],
        ),
    ],
    data_model="CREATE TABLE links (code TEXT PRIMARY KEY, url TEXT NOT NULL);",
    openapi_yaml=CONTRACT,
    key_decisions=[DesignDecision(decision="Base62 counter codes", rationale="Short and collision-free")],
    scaling_strategy="Move the store to Postgres and add a Redis read-through cache.",
)


def scripted_llm() -> FakeClient:
    return FakeClient(
        {
            "analyst": [SPEC],
            "planner": [PLAN],
            "architect": [DESIGN],
            "coder": [
                FileBundle(
                    summary="App",
                    files=[
                        GeneratedFile(path="app/__init__.py", content=""),
                        GeneratedFile(path="app/codes.py", content=CODES_PY),
                        GeneratedFile(path="app/main.py", content=BUGGY_MAIN),
                    ],
                )
            ],
            "tester": [
                FileBundle(
                    summary="Tests",
                    files=[
                        GeneratedFile(path="tests/conftest.py", content=CONFTEST),
                        GeneratedFile(path="tests/unit/test_codes.py", content=UNIT_TESTS),
                        GeneratedFile(path="tests/integration/test_api.py", content=API_TESTS),
                    ],
                )
            ],
            "docs": [FileBundle(summary="Docs", files=[GeneratedFile(path="README.md", content="# Mini shortener\n")])],
            "risk": [
                RiskReport(
                    risks=[
                        RiskItem(
                            risk="Open redirect abuse",
                            likelihood=Impact.MEDIUM,
                            impact=Impact.HIGH,
                            mitigation="URL allow/deny lists",
                        )
                    ],
                    tradeoffs=[],
                    failure_scenarios=[],
                    validation_strategy=["Contract tests"],
                    assumptions_and_limitations=["In-memory store"],
                )
            ],
            "reviewer": [
                ReviewVerdict(
                    fault="code",
                    rationale="resolve returns 200 JSON; the contract requires a 307 redirect",
                    code_fix_instructions="Return RedirectResponse(url, status_code=307) in app/main.py",
                )
            ],
            "coder.repair": [
                FileBundle(summary="Fix redirect", files=[GeneratedFile(path="app/main.py", content=FIXED_MAIN)])
            ],
        }
    )


async def test_full_pipeline_detects_bug_repairs_it_and_passes_validation(tmp_path) -> None:
    settings = Settings(runs_dir=tmp_path / "runs", max_repair_rounds=2)
    llm = scripted_llm()
    audit = AuditLog()
    pipeline = Pipeline(
        RunState(requirement="Build a mini URL shortener"),
        agents=build_agents(llm, settings, audit),
        gate=ScriptedGate(),
        store=CheckpointStore(settings.runs_dir),
        audit=audit,
        settings=settings,
    )
    state = await pipeline.run()

    assert state.status is RunStatus.COMPLETED, state.error
    first, second = state.validations
    statuses = {c.name: c.status for c in first.checks}
    assert statuses["tests"] is CheckStatus.FAILED
    assert statuses["contract_conformance"] is CheckStatus.PASSED
    assert first.tests.failed == 1 and first.tests.passed == 3
    assert "test_create_and_resolve_redirects" in first.tests.failures[0].test_id

    assert second.passed, second.checks
    assert second.tests.passed == 4

    assert state.repairs[0].verdict.fault == "code"
    assert state.repairs[0].files_changed == ["app/main.py"]
    assert state.file_owners["app/main.py"] == "repair-1"
    assert {"README.md", "docs/RISKS.md", "docs/architecture.md", "contract/openapi.yaml"} <= set(
        pipeline.workspace.list_files()
    )

    # Contract-first: the tester never saw the implementation.
    tester_prompt = next(r.prompt for r in llm.requests if r.agent == "tester")
    assert "links: dict[str, str]" not in tester_prompt
    assert "def encode(n: int) -> str" in tester_prompt
    # The repairing coder saw the failing test and the reviewer's instructions.
    repair_prompt = next(r.prompt for r in llm.requests if r.agent == "coder.repair")
    assert "test_create_and_resolve_redirects" in repair_prompt
    assert "RedirectResponse(url, status_code=307)" in repair_prompt


async def test_unrepaired_failure_fails_safe_at_output_gate(tmp_path) -> None:
    settings = Settings(runs_dir=tmp_path / "runs", max_repair_rounds=0)
    pipeline = Pipeline(
        RunState(requirement="Build a mini URL shortener"),
        agents=build_agents(scripted_llm(), settings, AuditLog()),
        gate=ScriptedGate(),
        store=CheckpointStore(settings.runs_dir),
        audit=AuditLog(),
        settings=settings,
    )
    state = await pipeline.run()
    assert state.status is RunStatus.ABORTED
    assert not state.validations[-1].passed
    assert state.decisions[-1].gate == "output" and state.decisions[-1].action == "abort"
