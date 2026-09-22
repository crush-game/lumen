from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from poirot.backend.agents.leader.agent import AgentRunResult
from poirot.backend.app.bootstrap import AppRuntime
from poirot.backend.lumen.application.completion import CheckResult, VerificationEvidence
from poirot.backend.lumen.application.runtime import LumenTaskSpec, RequirementSpec


@dataclass
class _Context:
    run_id: str


class _RunManager:
    def __init__(self) -> None:
        self.states: list[str] = []

    def create_run(self, **_: object) -> _Context:
        return _Context("run-1")

    def mark_running(self, run_id: str) -> None:
        self.states.append(f"running:{run_id}")

    def mark_success(self, run_id: str) -> None:
        self.states.append(f"success:{run_id}")

    def mark_failed(self, run_id: str, error: str) -> None:
        self.states.append(f"failed:{run_id}:{error}")


class _LeaderAgent:
    def run(self, *, question: str, run_context: _Context) -> AgentRunResult:
        return AgentRunResult(
            run_id=run_context.run_id,
            thread_id="thread-1",
            final_report=f"completed: {question}",
            events_path="events.jsonl",
            artifact_path=None,
            state={},
        )


def test_app_runtime_development_entrypoint_requires_external_evidence(tmp_path: Path) -> None:
    run_manager = _RunManager()
    runtime = AppRuntime(
        config=object(),
        capability_registry=object(),
        run_manager=run_manager,  # type: ignore[arg-type]
        researcher_model_name="fake-model",
        thread_id="thread-1",
        thread_dir=tmp_path,
        thread_journal=object(),
        leader_agent=_LeaderAgent(),  # type: ignore[arg-type]
    )

    result = runtime.run_development_task(
        _spec(),
        lambda agent_result, store: _passing_evidence(agent_result.run_id, store),
    )

    assert result.final_report.startswith("completed:")
    assert run_manager.states == ["running:run-1", "success:run-1"]


def _spec() -> LumenTaskSpec:
    return LumenTaskSpec(
        task_id="task-1",
        title="Fix validation",
        description="Implement validation.",
        completion_contract={
            "required_checks": [{"check_id": "unit-tests", "command": "python -m pytest"}],
            "required_requirement_ids": ["req-1"],
            "required_artifact_ids": ["artifact-1"],
        },
        requirements=(
            RequirementSpec(
                requirement_id="req-1",
                description="Reject empty input.",
                acceptance_criteria={"test": "test_rejects_empty_input"},
            ),
        ),
    )


def _passing_evidence(run_id: str, store: object) -> VerificationEvidence:
    store.create_artifact(  # type: ignore[attr-defined]
        artifact_id="artifact-1",
        task_id="task-1",
        run_id=run_id,
        kind="source",
        reference="src/validator.py",
        summary="Validation implementation",
        validation={"passed": True},
    )
    return VerificationEvidence(
        check_results=(CheckResult("unit-tests", True, 0, "3 passed"),),
        covered_requirement_ids=("req-1",),
        modified_paths=("src/validator.py",),
    )
