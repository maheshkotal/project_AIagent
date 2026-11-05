"""Brownfield end-to-end: the real sample repo is imported, analysed, fixed and validated (scripted LLM)."""

from __future__ import annotations

from pathlib import Path

from agentforge.audit import AuditLog
from agentforge.config import Settings
from agentforge.factory import build_agents
from agentforge.llm.fake import FakeClient
from agentforge.models import (
    ChangeType,
    DesignDecision,
    DesignDoc,
    FileBundle,
    GeneratedFile,
    ImpactedItem,
    ImpactReport,
    ModuleSpec,
    NormalizedSpec,
    ScenarioType,
    Task,
    TaskGraph,
)
from agentforge.orchestrator import CheckpointStore, Pipeline, RunState, RunStatus, ScriptedGate

SAMPLE = Path(__file__).parents[2] / "examples" / "brownfield" / "sample_repo"


def sample(path: str) -> str:
    return (SAMPLE / path).read_text()


def patched(path: str, old: str, new: str) -> str:
    text = sample(path)
    assert old in text, f"{old!r} not in {path}"
    return text.replace(old, new)


FIXED_SERVICE = patched(
    "app/service.py",
    "        if custom_alias:\n            return self._repository.add(custom_alias, url)",
    "        if custom_alias:\n"
    "            if self._repository.code_exists(custom_alias):\n"
    "                raise AliasTakenError(custom_alias)\n"
    "            return self._repository.add(custom_alias, url)",
).replace(
    'class CodeGenerationError(RuntimeError):\n    """Could not find a free short code."""',
    'class CodeGenerationError(RuntimeError):\n    """Could not find a free short code."""\n\n\n'
    'class AliasTakenError(ValueError):\n    """The requested custom alias is already in use."""',
)
FIXED_API = patched(
    "app/api.py",
    "    link = service.shorten(str(body.url), body.custom_alias)",
    "    try:\n        link = service.shorten(str(body.url), body.custom_alias)\n"
    "    except AliasTakenError as exc:\n"
    '        raise HTTPException(status_code=409, detail="Custom alias already in use") from exc',
).replace("from app.service import ShortenerService", "from app.service import AliasTakenError, ShortenerService")
FIXED_CONTRACT = patched(
    "contract/openapi.yaml",
    "        '422':\n          description: Invalid URL or alias",
    "        '409':\n          description: Custom alias already in use\n"
    "        '422':\n          description: Invalid URL or alias",
)
REGRESSION_TEST = """\
def test_duplicate_custom_alias_returns_409(client):
    first = client.post("/api/v1/links", json={"url": "https://a.example", "custom_alias": "taken"})
    assert first.status_code == 201
    second = client.post("/api/v1/links", json={"url": "https://b.example", "custom_alias": "taken"})
    assert second.status_code == 409
"""


def scripted_llm() -> FakeClient:
    return FakeClient(
        {
            "analyst": [
                NormalizedSpec(
                    title="Fix duplicate custom alias",
                    scenario=ScenarioType.BROWNFIELD,
                    problem_statement="Duplicate custom aliases crash with 500; they must return 409.",
                    functional_requirements=["POST /api/v1/links with a taken alias returns 409"],
                    non_functional_requirements=[],
                )
            ],
            "impact": [
                ImpactReport(
                    architecture_summary="Layered FastAPI app: api -> service -> repository -> SQLAlchemy.",
                    impacted_modules=[
                        ImpactedItem(
                            path="app/service.py",
                            change_type=ChangeType.MODIFY,
                            reason="shorten() inserts aliases without an existence check",
                        ),
                        ImpactedItem(path="app/api.py", change_type=ChangeType.MODIFY, reason="map to 409"),
                    ],
                    impacted_apis=[
                        ImpactedItem(
                            path="POST /api/v1/links", change_type=ChangeType.MODIFY, reason="new 409 response"
                        )
                    ],
                )
            ],
            "planner": [
                TaskGraph(
                    rationale="Contract first, then fix and regression test in parallel.",
                    tasks=[
                        Task(id="design_fix", title="Design", agent="architect", description="Add 409 to contract"),
                        Task(
                            id="fix_alias",
                            title="Fix",
                            agent="coder",
                            description="Owns app/service.py, app/api.py",
                            depends_on=["design_fix"],
                        ),
                        Task(
                            id="regression_test",
                            title="Test",
                            agent="tester",
                            description="Owns tests/test_duplicate_alias.py",
                            depends_on=["design_fix"],
                        ),
                    ],
                )
            ],
            "architect": [
                DesignDoc(
                    overview="Reject taken aliases in the service layer; the API maps the error to 409.",
                    architecture_diagram="flowchart LR\n  api --> service --> repository",
                    modules=[
                        ModuleSpec(
                            path="app/service.py",
                            responsibility="Business logic",
                            public_interface=["class AliasTakenError(ValueError)"],
                        )
                    ],
                    data_model="(unchanged)",
                    openapi_yaml=FIXED_CONTRACT,
                    key_decisions=[DesignDecision(decision="Check before insert", rationale="Clear error path")],
                    scaling_strategy="Unchanged; the unique index still guards concurrent inserts.",
                )
            ],
            "coder": [
                FileBundle(
                    summary="Alias check",
                    files=[
                        GeneratedFile(path="app/service.py", content=FIXED_SERVICE),
                        GeneratedFile(path="app/api.py", content=FIXED_API),
                    ],
                )
            ],
            "tester": [
                FileBundle(
                    summary="Regression test",
                    files=[GeneratedFile(path="tests/test_duplicate_alias.py", content=REGRESSION_TEST)],
                )
            ],
        }
    )


async def test_brownfield_fix_is_validated_and_diffed_without_touching_the_repo(tmp_path) -> None:
    before = {p: p.read_text() for p in SAMPLE.rglob("*.py")}
    settings = Settings(runs_dir=tmp_path / "runs", max_repair_rounds=1)
    llm = scripted_llm()
    pipeline = Pipeline(
        RunState(requirement="Fix: duplicate custom alias returns 500", repo_path=str(SAMPLE)),
        agents=build_agents(llm, settings, AuditLog()),
        gate=ScriptedGate(),
        store=CheckpointStore(settings.runs_dir),
        audit=AuditLog(),
        settings=settings,
    )
    state = await pipeline.run()

    assert state.status is RunStatus.COMPLETED, state.error
    report = state.validations[-1]
    assert report.passed and len(state.validations) == 1
    assert report.tests.passed == 8  # 7 existing tests + the new regression test

    # The impact analyst got a static map of the real code, with routes and the import graph.
    impact_prompt = next(r.prompt for r in llm.requests if r.agent == "impact")
    assert "POST /api/v1/links -> create_link()" in impact_prompt
    assert "imported by: app.api" in impact_prompt
    # The coder saw the existing code it must modify.
    coder_prompt = next(r.prompt for r in llm.requests if r.agent == "coder")
    assert "def shorten(self, url: str, custom_alias: str | None = None) -> Link:" in coder_prompt

    run_dir = tmp_path / "runs" / state.run_id
    diff = (run_dir / "changes.diff").read_text()
    assert "+                raise AliasTakenError(custom_alias)" in diff
    assert "+++ b/tests/test_duplicate_alias.py" in diff
    assert "app/models.py" not in diff  # untouched files are not in the diff
    summary = (run_dir / "SUMMARY.md").read_text()
    assert "## 3. Impact analysis" in summary and "app/service.py` (+" in summary
    assert {p: p.read_text() for p in SAMPLE.rglob("*.py")} == before  # original repo untouched
