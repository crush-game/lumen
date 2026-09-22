from __future__ import annotations

from pathlib import Path

import pytest

from poirot.backend.lumen.domain.models import LumenRunStatus
from poirot.backend.lumen.infrastructure.sqlite_store import LumenStore, StateTransitionError


def test_store_persists_task_run_and_checkpoint_across_reopen(tmp_path: Path) -> None:
    database_path = tmp_path / "lumen.sqlite3"
    store = LumenStore(database_path)
    store.create_task(
        task_id="task-1",
        title="Fix validation",
        description="Implement the requested validation rule.",
        completion_contract={"commands": ["python -m pytest"]},
    )
    store.add_requirement(
        requirement_id="req-1",
        task_id="task-1",
        description="Reject empty input.",
        acceptance_criteria={"test": "test_rejects_empty_input"},
    )
    store.create_run(run_id="run-1", task_id="task-1", model_name="test-model")
    store.transition_run("run-1", LumenRunStatus.RUNNING)
    store.create_checkpoint(
        checkpoint_id="checkpoint-1",
        run_id="run-1",
        verified_requirement_ids=["req-1"],
        workspace_evidence={"test_exit_code": 0},
        artifact_ids=[],
        next_step="run the full test suite",
        recovery_context={"completed_steps": ["add validation"]},
    )
    store.close()

    reopened = LumenStore(database_path)
    assert reopened.get_task("task-1").completion_contract == {"commands": ["python -m pytest"]}
    assert reopened.get_run("run-1").status is LumenRunStatus.RUNNING
    assert reopened.get_checkpoint("checkpoint-1").verified_requirement_ids == ("req-1",)
    reopened.close()


def test_store_rejects_completed_without_verification_path(tmp_path: Path) -> None:
    store = _store_with_task(tmp_path)
    store.create_run(run_id="run-1", task_id="task-1")

    with pytest.raises(StateTransitionError, match="queued -> completed"):
        store.transition_run("run-1", LumenRunStatus.COMPLETED)

    store.transition_run("run-1", LumenRunStatus.RUNNING)
    store.transition_run("run-1", LumenRunStatus.CANDIDATE_DONE)
    store.transition_run("run-1", LumenRunStatus.VERIFYING)
    completed = store.transition_run("run-1", LumenRunStatus.COMPLETED)

    assert completed.status is LumenRunStatus.COMPLETED
    store.close()


def test_child_run_keeps_parent_interrupted_and_same_task(tmp_path: Path) -> None:
    store = _store_with_task(tmp_path)
    store.create_run(run_id="run-parent", task_id="task-1")
    store.transition_run("run-parent", LumenRunStatus.RUNNING)
    store.transition_run("run-parent", LumenRunStatus.INTERRUPTED, failure_code="tool_timeout")

    child = store.create_run(
        run_id="run-child",
        task_id="task-1",
        parent_run_id="run-parent",
        metadata={"resume_from": "checkpoint-1"},
    )

    assert child.parent_run_id == "run-parent"
    assert child.status is LumenRunStatus.QUEUED
    assert store.get_run("run-parent").status is LumenRunStatus.INTERRUPTED
    store.close()


def test_reconcile_marks_only_running_runs_as_interrupted(tmp_path: Path) -> None:
    store = _store_with_task(tmp_path)
    store.create_run(run_id="run-running", task_id="task-1")
    store.create_run(run_id="run-queued", task_id="task-1")
    store.transition_run("run-running", LumenRunStatus.RUNNING)

    reconciled = store.reconcile_running_runs()

    assert [run.run_id for run in reconciled] == ["run-running"]
    interrupted = store.get_run("run-running")
    assert interrupted.status is LumenRunStatus.INTERRUPTED
    assert interrupted.failure_code == "process_restarted"
    assert store.get_run("run-queued").status is LumenRunStatus.QUEUED
    store.close()


def test_checkpoint_rejects_requirement_from_other_task(tmp_path: Path) -> None:
    store = _store_with_task(tmp_path)
    store.create_task(
        task_id="task-2",
        title="Second task",
        description="Separate task.",
        completion_contract={},
    )
    store.add_requirement(
        requirement_id="req-2",
        task_id="task-2",
        description="Separate requirement.",
        acceptance_criteria={},
    )
    store.create_run(run_id="run-1", task_id="task-1")

    with pytest.raises(ValueError, match="different task"):
        store.create_checkpoint(
            checkpoint_id="checkpoint-1",
            run_id="run-1",
            verified_requirement_ids=["req-2"],
            workspace_evidence={},
            artifact_ids=[],
            next_step="none",
            recovery_context={},
        )
    store.close()


def _store_with_task(tmp_path: Path) -> LumenStore:
    store = LumenStore(tmp_path / "lumen.sqlite3")
    store.create_task(
        task_id="task-1",
        title="Task",
        description="Task description.",
        completion_contract={},
    )
    return store
