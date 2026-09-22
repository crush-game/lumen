from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import PurePosixPath
from typing import Iterable, Mapping
from uuid import uuid4

from poirot.backend.lumen.domain.completion import CompletionContract
from poirot.backend.lumen.domain.models import LumenRunStatus
from poirot.backend.lumen.infrastructure.sqlite_store import LumenStore


@dataclass(frozen=True)
class CheckResult:
    check_id: str
    passed: bool
    exit_code: int | None
    summary: str = ""


@dataclass(frozen=True)
class VerificationEvidence:
    check_results: tuple[CheckResult, ...] = ()
    covered_requirement_ids: tuple[str, ...] = ()
    modified_paths: tuple[str, ...] = ()
    budget_usage: Mapping[str, int] = field(default_factory=dict)


@dataclass(frozen=True)
class ContractFailure:
    code: str
    message: str
    details: Mapping[str, object]


@dataclass(frozen=True)
class VerificationResult:
    passed: bool
    failures: tuple[ContractFailure, ...]
    checked_at: str


class CompletionVerifier:
    """Turn a candidate run into completed only after independent checks pass."""

    def __init__(self, store: LumenStore) -> None:
        self.store = store

    def verify_run(self, run_id: str, evidence: VerificationEvidence) -> VerificationResult:
        run = self.store.get_run(run_id)
        if run.status is LumenRunStatus.CANDIDATE_DONE:
            self.store.transition_run(run_id, LumenRunStatus.VERIFYING)
        elif run.status is not LumenRunStatus.VERIFYING:
            raise ValueError(f"completion verification requires candidate_done or verifying, got {run.status.value}")

        task = self.store.get_task(run.task_id)
        contract = CompletionContract.from_mapping(task.completion_contract)
        failures = self._check_contract(run_id, task.task_id, contract, evidence)
        result = VerificationResult(
            passed=not failures,
            failures=tuple(failures),
            checked_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        )
        self.store.append_trace_event(
            event_id=f"contract-check-{uuid4().hex}",
            run_id=run_id,
            event_type="contract_checked",
            payload={
                "passed": result.passed,
                "failure_codes": [failure.code for failure in result.failures],
                "checked_at": result.checked_at,
            },
        )
        if result.passed:
            self.store.transition_run(
                run_id,
                LumenRunStatus.COMPLETED,
                stop_reason="completion contract passed",
            )
        else:
            self.store.transition_run(
                run_id,
                LumenRunStatus.CONTRACT_FAILED,
                failure_code="contract_failed",
                stop_reason="completion contract did not pass",
            )
        return result

    def _check_contract(
        self,
        run_id: str,
        task_id: str,
        contract: CompletionContract,
        evidence: VerificationEvidence,
    ) -> list[ContractFailure]:
        failures: list[ContractFailure] = []
        check_results = _unique_checks(evidence.check_results, failures)

        for required_check in contract.required_checks:
            result = check_results.get(required_check.check_id)
            if result is None:
                failures.append(
                    ContractFailure(
                        "check_missing",
                        f"required check {required_check.check_id!r} has no result",
                        {"check_id": required_check.check_id},
                    )
                )
            elif result.passed and result.exit_code != 0:
                failures.append(
                    ContractFailure(
                        "forged_check_result",
                        f"check {required_check.check_id!r} claims success with non-zero exit code",
                        {"check_id": required_check.check_id, "exit_code": result.exit_code},
                    )
                )
            elif not result.passed or result.exit_code != 0:
                failures.append(
                    ContractFailure(
                        "check_failed",
                        f"required check {required_check.check_id!r} did not pass",
                        {"check_id": required_check.check_id, "exit_code": result.exit_code},
                    )
                )

        covered = set(evidence.covered_requirement_ids)
        for requirement_id in contract.required_requirement_ids:
            try:
                requirement = self.store.get_requirement(requirement_id)
            except KeyError:
                failures.append(
                    ContractFailure(
                        "requirement_missing",
                        f"required requirement {requirement_id!r} does not exist",
                        {"requirement_id": requirement_id},
                    )
                )
                continue
            if requirement.task_id != task_id:
                failures.append(
                    ContractFailure(
                        "requirement_wrong_task",
                        f"requirement {requirement_id!r} belongs to another task",
                        {"requirement_id": requirement_id},
                    )
                )
            elif requirement_id not in covered:
                failures.append(
                    ContractFailure(
                        "requirement_uncovered",
                        f"required requirement {requirement_id!r} has no verification evidence",
                        {"requirement_id": requirement_id},
                    )
                )

        for artifact_id in contract.required_artifact_ids:
            try:
                artifact = self.store.get_artifact(artifact_id)
            except KeyError:
                failures.append(
                    ContractFailure(
                        "artifact_missing",
                        f"required artifact {artifact_id!r} does not exist",
                        {"artifact_id": artifact_id},
                    )
                )
                continue
            if artifact.task_id != task_id or artifact.run_id != run_id:
                failures.append(
                    ContractFailure(
                        "artifact_wrong_scope",
                        f"artifact {artifact_id!r} does not belong to the candidate run",
                        {"artifact_id": artifact_id},
                    )
                )
            elif artifact.validation.get("passed") is not True:
                failures.append(
                    ContractFailure(
                        "artifact_invalid",
                        f"artifact {artifact_id!r} has not passed validation",
                        {"artifact_id": artifact_id},
                    )
                )

        failures.extend(_path_failures(contract.forbidden_paths, evidence.modified_paths))
        failures.extend(_budget_failures(contract, evidence.budget_usage))
        return failures


def _unique_checks(
    results: Iterable[CheckResult], failures: list[ContractFailure]
) -> dict[str, CheckResult]:
    indexed: dict[str, CheckResult] = {}
    for result in results:
        if result.check_id in indexed:
            failures.append(
                ContractFailure(
                    "duplicate_check_result",
                    f"check {result.check_id!r} has multiple results",
                    {"check_id": result.check_id},
                )
            )
        else:
            indexed[result.check_id] = result
    return indexed


def _path_failures(forbidden_paths: Iterable[str], modified_paths: Iterable[str]) -> list[ContractFailure]:
    normalized_forbidden = tuple(_normalize_path(path) for path in forbidden_paths)
    failures: list[ContractFailure] = []
    for raw_path in modified_paths:
        path = _normalize_path(raw_path)
        posix_path = PurePosixPath(path)
        if posix_path.is_absolute() or ".." in posix_path.parts:
            failures.append(
                ContractFailure(
                    "workspace_path_escape",
                    f"modified path {raw_path!r} escapes the workspace",
                    {"path": raw_path},
                )
            )
            continue
        if any(path == forbidden or path.startswith(f"{forbidden}/") for forbidden in normalized_forbidden):
            failures.append(
                ContractFailure(
                    "forbidden_path_modified",
                    f"forbidden path {raw_path!r} was modified",
                    {"path": raw_path},
                )
            )
    return failures


def _budget_failures(contract: CompletionContract, usage: Mapping[str, int]) -> list[ContractFailure]:
    failures: list[ContractFailure] = []
    limits = {
        "tool_calls": contract.max_tool_calls,
        "retries": contract.max_retries,
    }
    for key, maximum in limits.items():
        if maximum is None:
            continue
        actual = usage.get(key, 0)
        if not isinstance(actual, int) or actual < 0 or actual > maximum:
            failures.append(
                ContractFailure(
                    "budget_exceeded",
                    f"{key} usage exceeds the contract budget",
                    {"budget": key, "actual": actual, "maximum": maximum},
                )
            )
    return failures


def _normalize_path(path: str) -> str:
    return path.replace("\\", "/").removeprefix("./").rstrip("/").casefold()
