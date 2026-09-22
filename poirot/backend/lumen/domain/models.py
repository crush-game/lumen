from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class LumenRunStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    CANDIDATE_DONE = "candidate_done"
    VERIFYING = "verifying"
    CONTRACT_FAILED = "contract_failed"
    CHECKPOINTED = "checkpointed"
    RECOVERING = "recovering"
    COMPLETED = "completed"
    FAILED = "failed"
    WAITING_USER = "waiting_user"
    CANCELLED = "cancelled"
    INTERRUPTED = "interrupted"


TERMINAL_RUN_STATUSES = frozenset({
    LumenRunStatus.COMPLETED,
    LumenRunStatus.FAILED,
    LumenRunStatus.WAITING_USER,
    LumenRunStatus.CANCELLED,
    LumenRunStatus.INTERRUPTED,
})


@dataclass(frozen=True)
class Task:
    task_id: str
    title: str
    description: str
    completion_contract: dict[str, Any]
    created_at: str
    updated_at: str


@dataclass(frozen=True)
class Requirement:
    requirement_id: str
    task_id: str
    description: str
    acceptance_criteria: dict[str, Any]
    status: str
    created_at: str
    updated_at: str


@dataclass(frozen=True)
class LumenRun:
    run_id: str
    task_id: str
    status: LumenRunStatus
    model_name: str | None
    parent_run_id: str | None
    failure_code: str | None
    stop_reason: str | None
    metadata: dict[str, Any]
    created_at: str
    updated_at: str


@dataclass(frozen=True)
class Checkpoint:
    checkpoint_id: str
    run_id: str
    verified_requirement_ids: tuple[str, ...]
    workspace_evidence: dict[str, Any]
    artifact_ids: tuple[str, ...]
    next_step: str
    recovery_context: dict[str, Any]
    created_at: str


@dataclass(frozen=True)
class Artifact:
    artifact_id: str
    task_id: str
    run_id: str
    kind: str
    reference: str
    summary: str
    validation: dict[str, Any]
    created_at: str


@dataclass(frozen=True)
class TraceEvent:
    event_id: str
    run_id: str
    event_type: str
    payload: dict[str, Any] = field(default_factory=dict)
    created_at: str = ""

