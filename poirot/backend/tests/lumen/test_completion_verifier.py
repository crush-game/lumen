from __future__ import annotations

from pathlib import Path

import pytest

from poirot.backend.lumen.application.completion import (
    CheckResult,
    CompletionVerifier,
    VerificationEvidence,
)
from poirot.backend.lumen.domain.models import LumenRunStatus
from poirot.backend.lumen.infrastructure.sqlite_store import LumenStore


def test_complete_contract_is_required_before_completed(tmp_path: Path) -> None:
    store = _store_with_contract(tmp_path)
    _prepare_candidate_run(store)
    store.create_artifact(
        artifact_id="artifact-1",
        task_id="task-1",
        run_id="run-1",
        kind="source",
        reference="src/validator.py",
        summary="Validation implementation",
        validation={"passed": True},
    )

    result = CompletionVerifier(store).verify_run(
        "run-1",
        VerificationEvidence(
            check_results=(CheckResult("unit-tests", True, 0, "3 passed"),),
            covered_requirement_ids=("req-1",),
            modified_paths=("src/validator.py",),
            budget_usage={"tool_calls": 2},
        ),
    )

    assert result.passed is True
    assert result.failures == ()
    assert store.get_run("run-1").status is LumenRunStatus.COMPLETED
    assert store.list_trace_events("run-1")[-1].event_type == "contract_checked"


def test_incomplete_contract_never_becomes_completed(tmp_path: Path) -> None:
    store = _store_with_contract(tmp_path)
    _prepare_candidate_run(store)

    result = CompletionVerifier(store).verify_run(
        "run-1",
        VerificationEvidence(
            check_results=(CheckResult("unit-tests", False, 1, "one failed"),),
            covered_requirement_ids=(),
            modified_paths=("src/validator.py",),
            budget_usage={"tool_calls": 2},
        ),
    )

    failure_codes = {failure.code for failure in result.failures}
    assert result.passed is False
    assert {"check_failed", "requirement_uncovered", "artifact_missing"} <= failure_codes
    assert store.get_run("run-1").status is LumenRunStatus.CONTRACT_FAILED
    assert store.get_run("run-1").failure_code == "contract_failed"


def test_forbidden_file_change_is_rejected_even_when_tests_pass(tmp_path: Path) -> None:
    store = _store_with_contract(tmp_path)
    _prepare_candidate_run(store)
    store.create_artifact(
        artifact_id="artifact-1",
        task_id="task-1",
        run_id="run-1",
        kind="source",
        reference="src/validator.py",
        summary="Validation implementation",
        validation={"passed": True},
    )

    result = CompletionVerifier(store).verify_run(
        "run-1",
        VerificationEvidence(
            check_results=(CheckResult("unit-tests", True, 0, "3 passed"),),
            covered_requirement_ids=("req-1",),
            modified_paths=("tests/test_contract.py",),
            budget_usage={"tool_calls": 2},
        ),
    )

    assert result.passed is False
    assert "forbidden_path_modified" in {failure.code for failure in result.failures}
    assert store.get_run("run-1").status is LumenRunStatus.CONTRACT_FAILED


def test_inconsistent_success_evidence_is_rejected(tmp_path: Path) -> None:
    store = _store_with_contract(tmp_path)
    _prepare_candidate_run(store)
    store.create_artifact(
        artifact_id="artifact-1",
        task_id="task-1",
        run_id="run-1",
        kind="source",
        reference="src/validator.py",
        summary="Validation implementation",
        validation={"passed": True},
    )

    result = CompletionVerifier(store).verify_run(
        "run-1",
        VerificationEvidence(
            # A passing flag with a non-zero exit code is not trusted as success.
            check_results=(CheckResult("unit-tests", True, 1, "forged success"),),
            covered_requirement_ids=("req-1",),
            modified_paths=("src/validator.py",),
            budget_usage={"tool_calls": 2},
        ),
    )

    assert result.passed is False
    assert "forged_check_result" in {failure.code for failure in result.failures}
    assert store.get_run("run-1").status is LumenRunStatus.CONTRACT_FAILED


def test_workspace_escape_is_rejected(tmp_path: Path) -> None:
    store = _store_with_contract(tmp_path)
    _prepare_candidate_run(store)
    store.create_artifact(
        artifact_id="artifact-1",
        task_id="task-1",
        run_id="run-1",
        kind="source",
        reference="src/validator.py",
        summary="Validation implementation",
        validation={"passed": True},
    )

    result = CompletionVerifier(store).verify_run(
        "run-1",
        VerificationEvidence(
            check_results=(CheckResult("unit-tests", True, 0, "3 passed"),),
            covered_requirement_ids=("req-1",),
            modified_paths=("../secrets.txt",),
            budget_usage={"tool_calls": 2},
        ),
    )

    assert result.passed is False
    assert "workspace_path_escape" in {failure.code for failure in result.failures}


def test_tool_call_budget_is_part_of_completion_contract(tmp_path: Path) -> None:
    store = _store_with_contract(tmp_path)
    _prepare_candidate_run(store)
    store.create_artifact(
        artifact_id="artifact-1",
        task_id="task-1",
        run_id="run-1",
        kind="source",
        reference="src/validator.py",
        summary="Validation implementation",
        validation={"passed": True},
    )

    result = CompletionVerifier(store).verify_run(
        "run-1",
        VerificationEvidence(
            check_results=(CheckResult("unit-tests", True, 0, "3 passed"),),
            covered_requirement_ids=("req-1",),
            modified_paths=("src/validator.py",),
            budget_usage={"tool_calls": 4},
        ),
    )

    assert result.passed is False
    assert "budget_exceeded" in {failure.code for failure in result.failures}


def _store_with_contract(tmp_path: Path) -> LumenStore:
    store = LumenStore(tmp_path / "lumen.sqlite3")
    store.create_task(
        task_id="task-1",
        title="Fix validation",
        description="Implement the requested validation rule.",
        completion_contract={
            "required_checks": [
                {"check_id": "unit-tests", "command": "python -m pytest"},
            ],
            "required_requirement_ids": ["req-1"],
            "required_artifact_ids": ["artifact-1"],
            "forbidden_paths": ["tests/"],
            "max_tool_calls": 3,
        },
    )
    store.add_requirement(
        requirement_id="req-1",
        task_id="task-1",
        description="Reject empty input.",
        acceptance_criteria={"test": "test_rejects_empty_input"},
    )
    return store


def _prepare_candidate_run(store: LumenStore) -> None:
    store.create_run(run_id="run-1", task_id="task-1")
    store.transition_run("run-1", LumenRunStatus.RUNNING)
    store.transition_run("run-1", LumenRunStatus.CANDIDATE_DONE)
