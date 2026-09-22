from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping


@dataclass(frozen=True)
class ContractCheck:
    """A named check which must succeed before a run can complete."""

    check_id: str
    command: str | None = None


@dataclass(frozen=True)
class CompletionContract:
    """Programmatic completion requirements for one development task.

    Commands are descriptive data here. The verifier only accepts results from
    a check runner; it never executes arbitrary command text itself.
    """

    required_checks: tuple[ContractCheck, ...] = ()
    required_requirement_ids: tuple[str, ...] = ()
    required_artifact_ids: tuple[str, ...] = ()
    forbidden_paths: tuple[str, ...] = ()
    max_tool_calls: int | None = None
    max_retries: int | None = None

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any] | None) -> "CompletionContract":
        raw = value or {}
        checks = raw.get("required_checks", raw.get("checks"))
        if checks is None and "commands" in raw:
            commands = raw["commands"]
            if isinstance(commands, (list, tuple)):
                checks = [
                    {"check_id": f"command-{index}", "command": command}
                    for index, command in enumerate(commands)
                ]
            else:
                raise ValueError("commands list must contain strings")
        if checks is None:
            checks = ()
        parsed_checks: list[ContractCheck] = []
        for index, item in enumerate(checks):
            if isinstance(item, str):
                parsed_checks.append(ContractCheck(check_id=item))
                continue
            if not isinstance(item, Mapping):
                raise ValueError(f"required check at index {index} must be a string or mapping")
            check_id = item.get("check_id", item.get("id"))
            if not isinstance(check_id, str) or not check_id.strip():
                raise ValueError(f"required check at index {index} has no check_id")
            command = item.get("command")
            if command is not None and not isinstance(command, str):
                raise ValueError(f"command for check {check_id!r} must be a string")
            parsed_checks.append(ContractCheck(check_id=check_id, command=command))

        return cls(
            required_checks=tuple(parsed_checks),
            required_requirement_ids=_string_tuple(raw.get("required_requirement_ids", ()), "requirement"),
            required_artifact_ids=_string_tuple(raw.get("required_artifact_ids", ()), "artifact"),
            forbidden_paths=_string_tuple(raw.get("forbidden_paths", ()), "forbidden path"),
            max_tool_calls=_optional_non_negative_int(raw.get("max_tool_calls"), "max_tool_calls"),
            max_retries=_optional_non_negative_int(raw.get("max_retries"), "max_retries"),
        )


def _string_tuple(value: Any, field_name: str) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str) or not isinstance(value, (list, tuple, set, frozenset)):
        raise ValueError(f"{field_name} list must contain strings")
    values = tuple(value)
    if not all(isinstance(item, str) and item.strip() for item in values):
        raise ValueError(f"{field_name} list must contain non-empty strings")
    return values


def _optional_non_negative_int(value: Any, field_name: str) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{field_name} must be a non-negative integer")
    return value
