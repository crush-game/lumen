from __future__ import annotations

from pathlib import Path

import pytest

from poirot.backend.lumen.application.completion import CheckResult, VerificationEvidence
from poirot.backend.lumen.application.runtime import (
    CompletionContractError,
    LumenRuntimeAdapter,
    LumenTaskSpec,
    RequirementSpec,
)
from poirot.backend.lumen.domain.models import LumenRunStatus
from poirot.backend.lumen.infrastructure.sqlite_store import LumenStore


def test_adapter_gates_agent_return_with_completion_contract(tmp_path: Path) -> None:
    store = LumenStore(tmp_path / "lumen.sqlite3")
    adapter = LumenRuntimeAdapter(store)
    spec = _spec()

    result = adapter.execute(
        spec,
        run_id="run-1",
        execute_agent=lambda: "agent result",
        collect_evidence=lambda _: _passing_evidence(store, "run-1"),
        model_name="fake-model",
    )

    assert result == "agent result"
    assert store.get_run("run-1").status is LumenRunStatus.COMPLETED
    store.close()


def test_adapter_does_not_mark_failed_contract_as_completed(tmp_path: Path) -> None:
    store = LumenStore(tmp_path / "lumen.sqlite3")
    adapter = LumenRuntimeAdapter(store)

    with pytest.raises(CompletionContractError, match="completion contract failed"):
        adapter.execute(
            _spec(),
            run_id="run-1",
            execute_agent=lambda: "agent result",
            collect_evidence=lambda _: VerificationEvidence(
                check_results=(CheckResult("unit-tests", False, 1, "failed"),),
                covered_requirement_ids=(),
                modified_paths=(),
            ),
        )

    assert store.get_run("run-1").status is LumenRunStatus.CONTRACT_FAILED
    store.close()


def test_adapter_marks_agent_exception_as_failed(tmp_path: Path) -> None:
    store = LumenStore(tmp_path / "lumen.sqlite3")
    adapter = LumenRuntimeAdapter(store)

    with pytest.raises(RuntimeError, match="agent crashed"):
        adapter.execute(
            _spec(),
            run_id="run-1",
            execute_agent=lambda: (_ for _ in ()).throw(RuntimeError("agent crashed")),
            collect_evidence=lambda _: VerificationEvidence(),
        )

    assert store.get_run("run-1").status is LumenRunStatus.FAILED
    assert store.get_run("run-1").failure_code == "runtime_exception"
    store.close()


def _spec() -> LumenTaskSpec:
    return LumenTaskSpec(
        task_id="task-1",
        title="Fix validation",
        description="Implement the requested validation rule.",
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


def _passing_evidence(store: LumenStore, run_id: str) -> VerificationEvidence:
    store.create_artifact(
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
