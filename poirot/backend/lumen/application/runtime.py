from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, TypeVar

from poirot.backend.lumen.application.completion import (
    CompletionVerifier,
    VerificationEvidence,
    VerificationResult,
)
from poirot.backend.lumen.domain.models import LumenRun, LumenRunStatus
from poirot.backend.lumen.infrastructure.sqlite_store import LumenStore


@dataclass(frozen=True)
class RequirementSpec:
    requirement_id: str
    description: str
    acceptance_criteria: dict[str, object]


@dataclass(frozen=True)
class LumenTaskSpec:
    task_id: str
    title: str
    description: str
    completion_contract: dict[str, object]
    requirements: tuple[RequirementSpec, ...] = ()


class CompletionContractError(RuntimeError):
    """Raised when a development run returns but its contract is not met."""

    def __init__(self, result: VerificationResult) -> None:
        self.result = result
        codes = ", ".join(failure.code for failure in result.failures)
        super().__init__(f"completion contract failed: {codes}")


ResultT = TypeVar("ResultT")


class LumenRuntimeAdapter:
    """Bridge an external Agent run into Lumen's durable completion lifecycle.

    The adapter deliberately requires the caller to provide verification
    evidence. It does not treat an Agent's final text as proof of completion,
    and it does not execute arbitrary commands from a task contract.
    """

    def __init__(self, store: LumenStore) -> None:
        self.store = store
        self.verifier = CompletionVerifier(store)

    def start_run(
        self,
        spec: LumenTaskSpec,
        *,
        run_id: str,
        model_name: str | None = None,
        parent_run_id: str | None = None,
    ) -> LumenRun:
        self.store.create_task(
            task_id=spec.task_id,
            title=spec.title,
            description=spec.description,
            completion_contract=spec.completion_contract,
        )
        for requirement in spec.requirements:
            self.store.add_requirement(
                requirement_id=requirement.requirement_id,
                task_id=spec.task_id,
                description=requirement.description,
                acceptance_criteria=requirement.acceptance_criteria,
            )
        self.store.create_run(
            run_id=run_id,
            task_id=spec.task_id,
            model_name=model_name,
            parent_run_id=parent_run_id,
            metadata={"adapter": "lumen", "task_spec_id": spec.task_id},
        )
        return self.store.transition_run(run_id, LumenRunStatus.RUNNING)

    def mark_candidate_done(self, run_id: str) -> LumenRun:
        return self.store.transition_run(run_id, LumenRunStatus.CANDIDATE_DONE)

    def verify_candidate(self, run_id: str, evidence: VerificationEvidence) -> VerificationResult:
        return self.verifier.verify_run(run_id, evidence)

    def execute(
        self,
        spec: LumenTaskSpec,
        *,
        run_id: str,
        execute_agent: Callable[[], ResultT],
        collect_evidence: Callable[[ResultT], VerificationEvidence],
        model_name: str | None = None,
        parent_run_id: str | None = None,
    ) -> ResultT:
        """Run an Agent callback and gate its return through the contract."""

        self.start_run(
            spec,
            run_id=run_id,
            model_name=model_name,
            parent_run_id=parent_run_id,
        )
        try:
            result = execute_agent()
            self.mark_candidate_done(run_id)
            verification = self.verify_candidate(run_id, collect_evidence(result))
            if not verification.passed:
                raise CompletionContractError(verification)
            return result
        except Exception:
            current = self.store.get_run(run_id)
            if current.status in {
                LumenRunStatus.RUNNING,
                LumenRunStatus.CANDIDATE_DONE,
                LumenRunStatus.VERIFYING,
            }:
                self.store.transition_run(
                    run_id,
                    LumenRunStatus.FAILED,
                    failure_code="runtime_exception",
                    stop_reason="Agent callback raised before completion verification finished",
                )
            raise

    def close(self) -> None:
        self.store.close()
