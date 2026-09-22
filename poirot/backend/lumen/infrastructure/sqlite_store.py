from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from poirot.backend.lumen.domain.models import (
    Artifact,
    Checkpoint,
    LumenRun,
    LumenRunStatus,
    Requirement,
    Task,
    TraceEvent,
)


class StateTransitionError(ValueError):
    """Raised when a run attempts a transition outside the Lumen state machine."""


_ALLOWED_TRANSITIONS: dict[LumenRunStatus, frozenset[LumenRunStatus]] = {
    LumenRunStatus.QUEUED: frozenset({
        LumenRunStatus.RUNNING,
        LumenRunStatus.FAILED,
        LumenRunStatus.CANCELLED,
    }),
    LumenRunStatus.RUNNING: frozenset({
        LumenRunStatus.CANDIDATE_DONE,
        LumenRunStatus.CHECKPOINTED,
        LumenRunStatus.INTERRUPTED,
        LumenRunStatus.FAILED,
        LumenRunStatus.WAITING_USER,
        LumenRunStatus.CANCELLED,
    }),
    LumenRunStatus.CANDIDATE_DONE: frozenset({
        LumenRunStatus.VERIFYING,
        LumenRunStatus.CHECKPOINTED,
        LumenRunStatus.FAILED,
    }),
    LumenRunStatus.VERIFYING: frozenset({
        LumenRunStatus.COMPLETED,
        LumenRunStatus.CONTRACT_FAILED,
        LumenRunStatus.FAILED,
    }),
    LumenRunStatus.CONTRACT_FAILED: frozenset({
        LumenRunStatus.RECOVERING,
        LumenRunStatus.FAILED,
        LumenRunStatus.WAITING_USER,
    }),
    LumenRunStatus.CHECKPOINTED: frozenset({
        LumenRunStatus.RECOVERING,
        LumenRunStatus.FAILED,
        LumenRunStatus.WAITING_USER,
    }),
    LumenRunStatus.RECOVERING: frozenset({
        LumenRunStatus.FAILED,
        LumenRunStatus.WAITING_USER,
    }),
    LumenRunStatus.COMPLETED: frozenset(),
    LumenRunStatus.FAILED: frozenset(),
    LumenRunStatus.WAITING_USER: frozenset(),
    LumenRunStatus.CANCELLED: frozenset(),
    LumenRunStatus.INTERRUPTED: frozenset({LumenRunStatus.RECOVERING}),
}


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def _from_json(value: str) -> Any:
    return json.loads(value)


class LumenStore:
    """SQLite persistence boundary for Lumen domain records.

    Agent and UI code may ask the store to transition a run, but only this
    class enforces the state machine and writes durable records.
    """

    def __init__(self, database_path: Path) -> None:
        self.database_path = database_path
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self._connection = sqlite3.connect(self.database_path)
        self._connection.row_factory = sqlite3.Row
        self._connection.execute("PRAGMA foreign_keys = ON")
        self._initialize()

    def close(self) -> None:
        self._connection.close()

    def create_task(
        self,
        *,
        task_id: str,
        title: str,
        description: str,
        completion_contract: dict[str, Any],
    ) -> Task:
        now = _utc_now()
        self._connection.execute(
            """
            INSERT INTO lumen_tasks(task_id, title, description, completion_contract, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (task_id, title, description, _json(completion_contract), now, now),
        )
        self._connection.commit()
        return self.get_task(task_id)

    def get_task(self, task_id: str) -> Task:
        row = self._require_row("SELECT * FROM lumen_tasks WHERE task_id = ?", (task_id,), "task")
        return _task_from_row(row)

    def add_requirement(
        self,
        *,
        requirement_id: str,
        task_id: str,
        description: str,
        acceptance_criteria: dict[str, Any],
        status: str = "pending",
    ) -> Requirement:
        now = _utc_now()
        self._connection.execute(
            """
            INSERT INTO lumen_requirements(
                requirement_id, task_id, description, acceptance_criteria, status, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (requirement_id, task_id, description, _json(acceptance_criteria), status, now, now),
        )
        self._connection.commit()
        row = self._require_row(
            "SELECT * FROM lumen_requirements WHERE requirement_id = ?", (requirement_id,), "requirement"
        )
        return _requirement_from_row(row)

    def create_run(
        self,
        *,
        run_id: str,
        task_id: str,
        model_name: str | None = None,
        parent_run_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> LumenRun:
        self.get_task(task_id)
        if parent_run_id is not None:
            parent = self.get_run(parent_run_id)
            if parent.task_id != task_id:
                raise ValueError("child run must belong to the same task as its parent")
        now = _utc_now()
        self._connection.execute(
            """
            INSERT INTO lumen_runs(
                run_id, task_id, status, model_name, parent_run_id, failure_code, stop_reason,
                metadata, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, NULL, NULL, ?, ?, ?)
            """,
            (
                run_id,
                task_id,
                LumenRunStatus.QUEUED.value,
                model_name,
                parent_run_id,
                _json(metadata or {}),
                now,
                now,
            ),
        )
        self._connection.commit()
        return self.get_run(run_id)

    def get_run(self, run_id: str) -> LumenRun:
        row = self._require_row("SELECT * FROM lumen_runs WHERE run_id = ?", (run_id,), "run")
        return _run_from_row(row)

    def transition_run(
        self,
        run_id: str,
        target_status: LumenRunStatus,
        *,
        failure_code: str | None = None,
        stop_reason: str | None = None,
    ) -> LumenRun:
        current = self.get_run(run_id)
        if target_status not in _ALLOWED_TRANSITIONS[current.status]:
            raise StateTransitionError(
                f"invalid run transition: {current.status.value} -> {target_status.value}"
            )
        self._connection.execute(
            """
            UPDATE lumen_runs
            SET status = ?, failure_code = ?, stop_reason = ?, updated_at = ?
            WHERE run_id = ?
            """,
            (target_status.value, failure_code, stop_reason, _utc_now(), run_id),
        )
        self._connection.commit()
        return self.get_run(run_id)

    def reconcile_running_runs(self) -> list[LumenRun]:
        rows = self._connection.execute(
            "SELECT run_id FROM lumen_runs WHERE status = ?", (LumenRunStatus.RUNNING.value,)
        ).fetchall()
        reconciled = [
            self.transition_run(
                row["run_id"],
                LumenRunStatus.INTERRUPTED,
                failure_code="process_restarted",
                stop_reason="runtime process was not running during store reconciliation",
            )
            for row in rows
        ]
        return reconciled

    def create_checkpoint(
        self,
        *,
        checkpoint_id: str,
        run_id: str,
        verified_requirement_ids: Iterable[str],
        workspace_evidence: dict[str, Any],
        artifact_ids: Iterable[str],
        next_step: str,
        recovery_context: dict[str, Any],
    ) -> Checkpoint:
        self.get_run(run_id)
        requirement_ids = tuple(verified_requirement_ids)
        for requirement_id in requirement_ids:
            row = self._require_row(
                "SELECT task_id FROM lumen_requirements WHERE requirement_id = ?",
                (requirement_id,),
                "requirement",
            )
            if row["task_id"] != self.get_run(run_id).task_id:
                raise ValueError("checkpoint requirement belongs to a different task")
        now = _utc_now()
        self._connection.execute(
            """
            INSERT INTO lumen_checkpoints(
                checkpoint_id, run_id, verified_requirement_ids, workspace_evidence,
                artifact_ids, next_step, recovery_context, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                checkpoint_id,
                run_id,
                _json(requirement_ids),
                _json(workspace_evidence),
                _json(tuple(artifact_ids)),
                next_step,
                _json(recovery_context),
                now,
            ),
        )
        self._connection.commit()
        return self.get_checkpoint(checkpoint_id)

    def get_checkpoint(self, checkpoint_id: str) -> Checkpoint:
        row = self._require_row(
            "SELECT * FROM lumen_checkpoints WHERE checkpoint_id = ?", (checkpoint_id,), "checkpoint"
        )
        return _checkpoint_from_row(row)

    def create_artifact(
        self,
        *,
        artifact_id: str,
        task_id: str,
        run_id: str,
        kind: str,
        reference: str,
        summary: str,
        validation: dict[str, Any],
    ) -> Artifact:
        run = self.get_run(run_id)
        if run.task_id != task_id:
            raise ValueError("artifact task must match its run task")
        self._connection.execute(
            """
            INSERT INTO lumen_artifacts(
                artifact_id, task_id, run_id, kind, reference, summary, validation, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (artifact_id, task_id, run_id, kind, reference, summary, _json(validation), _utc_now()),
        )
        self._connection.commit()
        row = self._require_row(
            "SELECT * FROM lumen_artifacts WHERE artifact_id = ?", (artifact_id,), "artifact"
        )
        return _artifact_from_row(row)

    def append_trace_event(
        self,
        *,
        event_id: str,
        run_id: str,
        event_type: str,
        payload: dict[str, Any],
    ) -> TraceEvent:
        self.get_run(run_id)
        now = _utc_now()
        self._connection.execute(
            """
            INSERT INTO lumen_trace_events(event_id, run_id, event_type, payload, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (event_id, run_id, event_type, _json(payload), now),
        )
        self._connection.commit()
        return TraceEvent(event_id=event_id, run_id=run_id, event_type=event_type, payload=payload, created_at=now)

    def _initialize(self) -> None:
        self._connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS lumen_tasks (
                task_id TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                description TEXT NOT NULL,
                completion_contract TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS lumen_requirements (
                requirement_id TEXT PRIMARY KEY,
                task_id TEXT NOT NULL REFERENCES lumen_tasks(task_id),
                description TEXT NOT NULL,
                acceptance_criteria TEXT NOT NULL,
                status TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS lumen_runs (
                run_id TEXT PRIMARY KEY,
                task_id TEXT NOT NULL REFERENCES lumen_tasks(task_id),
                status TEXT NOT NULL,
                model_name TEXT,
                parent_run_id TEXT REFERENCES lumen_runs(run_id),
                failure_code TEXT,
                stop_reason TEXT,
                metadata TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS lumen_checkpoints (
                checkpoint_id TEXT PRIMARY KEY,
                run_id TEXT NOT NULL REFERENCES lumen_runs(run_id),
                verified_requirement_ids TEXT NOT NULL,
                workspace_evidence TEXT NOT NULL,
                artifact_ids TEXT NOT NULL,
                next_step TEXT NOT NULL,
                recovery_context TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS lumen_artifacts (
                artifact_id TEXT PRIMARY KEY,
                task_id TEXT NOT NULL REFERENCES lumen_tasks(task_id),
                run_id TEXT NOT NULL REFERENCES lumen_runs(run_id),
                kind TEXT NOT NULL,
                reference TEXT NOT NULL,
                summary TEXT NOT NULL,
                validation TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS lumen_trace_events (
                event_id TEXT PRIMARY KEY,
                run_id TEXT NOT NULL REFERENCES lumen_runs(run_id),
                event_type TEXT NOT NULL,
                payload TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            """
        )
        self._connection.commit()

    def _require_row(
        self,
        query: str,
        parameters: tuple[Any, ...],
        entity_name: str,
    ) -> sqlite3.Row:
        row = self._connection.execute(query, parameters).fetchone()
        if row is None:
            raise KeyError(f"unknown {entity_name}")
        return row


def _task_from_row(row: sqlite3.Row) -> Task:
    return Task(
        task_id=row["task_id"],
        title=row["title"],
        description=row["description"],
        completion_contract=_from_json(row["completion_contract"]),
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def _requirement_from_row(row: sqlite3.Row) -> Requirement:
    return Requirement(
        requirement_id=row["requirement_id"],
        task_id=row["task_id"],
        description=row["description"],
        acceptance_criteria=_from_json(row["acceptance_criteria"]),
        status=row["status"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def _run_from_row(row: sqlite3.Row) -> LumenRun:
    return LumenRun(
        run_id=row["run_id"],
        task_id=row["task_id"],
        status=LumenRunStatus(row["status"]),
        model_name=row["model_name"],
        parent_run_id=row["parent_run_id"],
        failure_code=row["failure_code"],
        stop_reason=row["stop_reason"],
        metadata=_from_json(row["metadata"]),
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def _checkpoint_from_row(row: sqlite3.Row) -> Checkpoint:
    return Checkpoint(
        checkpoint_id=row["checkpoint_id"],
        run_id=row["run_id"],
        verified_requirement_ids=tuple(_from_json(row["verified_requirement_ids"])),
        workspace_evidence=_from_json(row["workspace_evidence"]),
        artifact_ids=tuple(_from_json(row["artifact_ids"])),
        next_step=row["next_step"],
        recovery_context=_from_json(row["recovery_context"]),
        created_at=row["created_at"],
    )


def _artifact_from_row(row: sqlite3.Row) -> Artifact:
    return Artifact(
        artifact_id=row["artifact_id"],
        task_id=row["task_id"],
        run_id=row["run_id"],
        kind=row["kind"],
        reference=row["reference"],
        summary=row["summary"],
        validation=_from_json(row["validation"]),
        created_at=row["created_at"],
    )
