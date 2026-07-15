from __future__ import annotations

from enum import StrEnum

from app.exceptions import ValidationError


class OperationStatus(StrEnum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    FAILED = "FAILED"
    RETRY_AUTHORIZED = "RETRY_AUTHORIZED"
    NEEDS_RECONCILIATION = "NEEDS_RECONCILIATION"
    VERIFIED = "VERIFIED"
    NOT_APPLIED = "NOT_APPLIED"
    MANUAL_REVIEW = "MANUAL_REVIEW"
    MANUAL_HANDLED = "MANUAL_HANDLED"


class AttemptStatus(StrEnum):
    STARTING = "STARTING"
    RUNNING = "RUNNING"
    START_FAILED = "START_FAILED"
    START_UNKNOWN = "START_UNKNOWN"
    FAILED = "FAILED"
    SIDE_EFFECT_UNKNOWN = "SIDE_EFFECT_UNKNOWN"
    VERIFIED = "VERIFIED"
    NOT_APPLIED = "NOT_APPLIED"
    READ_COMPLETED = "READ_COMPLETED"
    PREVIEW_COMPLETED = "PREVIEW_COMPLETED"


class ResultStatus(StrEnum):
    VERIFIED = "VERIFIED"
    NOT_APPLIED = "NOT_APPLIED"
    FAILED = "FAILED"
    START_FAILED = "START_FAILED"
    START_UNKNOWN = "START_UNKNOWN"
    SIDE_EFFECT_UNKNOWN = "SIDE_EFFECT_UNKNOWN"
    READ_COMPLETED = "READ_COMPLETED"
    PREVIEW_COMPLETED = "PREVIEW_COMPLETED"


class SideEffectState(StrEnum):
    NOT_STARTED = "NOT_STARTED"
    SUBMIT_INTENT_RECORDED = "SUBMIT_INTENT_RECORDED"
    SUBMIT_CLICKED = "SUBMIT_CLICKED"
    UNKNOWN = "UNKNOWN"
    VERIFIED = "VERIFIED"
    NOT_APPLIED = "NOT_APPLIED"


class QueueArtifactState(StrEnum):
    TEMP = "TEMP"
    READY = "READY"
    WORKING = "WORKING"
    RESULT = "RESULT"
    ARCHIVE = "ARCHIVE"
    QUARANTINE = "QUARANTINE"


LEGACY_RESULT_SUCCESS = "SUCCESS"
LEGACY_RESULT_ALREADY_APPLIED = "ALREADY_APPLIED"
LEGACY_NEEDS_RECONCILIATION = "NEEDS_RECONCILIATION"
LEGACY_NOT_APPLIED_ALIASES = {"NONE_VERIFIED", "NO_SIDE_EFFECT_VERIFIED"}


TERMINAL_ATTEMPT_STATUSES = {
    AttemptStatus.START_FAILED.value,
    AttemptStatus.START_UNKNOWN.value,
    AttemptStatus.FAILED.value,
    AttemptStatus.SIDE_EFFECT_UNKNOWN.value,
    AttemptStatus.VERIFIED.value,
    AttemptStatus.NOT_APPLIED.value,
    AttemptStatus.READ_COMPLETED.value,
    AttemptStatus.PREVIEW_COMPLETED.value,
}

ACTIVE_ATTEMPT_STATUSES = {AttemptStatus.STARTING.value, AttemptStatus.RUNNING.value}

UNKNOWN_ATTEMPT_STATUSES = {
    AttemptStatus.START_UNKNOWN.value,
    AttemptStatus.SIDE_EFFECT_UNKNOWN.value,
}


def normalize_side_effect_state(value: str) -> tuple[str, str | None]:
    normalized = str(value or "").strip().upper()
    if normalized in LEGACY_NOT_APPLIED_ALIASES:
        return SideEffectState.NOT_APPLIED.value, normalized
    try:
        return SideEffectState(normalized).value, None
    except ValueError as exc:
        raise ValidationError(f"unsupported ShadowBot side_effect_state: {value}") from exc


def normalize_result_status(status: str, side_effect_state: str) -> tuple[str, str | None]:
    normalized = str(status or "").strip().upper()
    if normalized in {LEGACY_RESULT_SUCCESS, LEGACY_RESULT_ALREADY_APPLIED}:
        return ResultStatus.VERIFIED.value, normalized
    if normalized == LEGACY_NEEDS_RECONCILIATION:
        replacement = (
            ResultStatus.SIDE_EFFECT_UNKNOWN.value
            if side_effect_state
            in {
                SideEffectState.SUBMIT_INTENT_RECORDED.value,
                SideEffectState.SUBMIT_CLICKED.value,
                SideEffectState.UNKNOWN.value,
            }
            else ResultStatus.START_UNKNOWN.value
        )
        return replacement, normalized
    try:
        return ResultStatus(normalized).value, None
    except ValueError as exc:
        raise ValidationError(f"unsupported ShadowBot result status: {status}") from exc


def attempt_status_from_result(result_status: str) -> str:
    try:
        return AttemptStatus(result_status).value
    except ValueError as exc:
        raise ValidationError(f"result status cannot be stored on an attempt: {result_status}") from exc


def operation_status_from_result(result_status: str, side_effect_state: str) -> str:
    mapping = {
        ResultStatus.VERIFIED.value: OperationStatus.VERIFIED.value,
        ResultStatus.NOT_APPLIED.value: OperationStatus.NOT_APPLIED.value,
        ResultStatus.FAILED.value: OperationStatus.FAILED.value,
        ResultStatus.START_FAILED.value: OperationStatus.FAILED.value,
        ResultStatus.START_UNKNOWN.value: OperationStatus.NEEDS_RECONCILIATION.value,
        ResultStatus.SIDE_EFFECT_UNKNOWN.value: OperationStatus.NEEDS_RECONCILIATION.value,
    }
    if result_status in {ResultStatus.READ_COMPLETED.value, ResultStatus.PREVIEW_COMPLETED.value}:
        return OperationStatus.RUNNING.value
    if result_status == ResultStatus.FAILED.value and side_effect_state == SideEffectState.UNKNOWN.value:
        return OperationStatus.NEEDS_RECONCILIATION.value
    try:
        return mapping[result_status]
    except KeyError as exc:
        raise ValidationError(
            f"undefined ShadowBot result mapping: status={result_status}, side_effect_state={side_effect_state}"
        ) from exc


def validate_result_state(
    *,
    status: str,
    side_effect_state: str,
    run_success_flag: bool | None,
    business_operation_completed: bool | None,
    retryable: bool,
    error_code: str,
) -> None:
    try:
        ResultStatus(status)
        SideEffectState(side_effect_state)
    except ValueError as exc:
        raise ValidationError("ShadowBot result contains an unsupported state.") from exc

    expected_flags = {
        ResultStatus.VERIFIED.value: (True, True),
        ResultStatus.NOT_APPLIED.value: (True, False),
        ResultStatus.FAILED.value: (False, False),
        ResultStatus.START_FAILED.value: (False, False),
        ResultStatus.START_UNKNOWN.value: (None, None),
        ResultStatus.SIDE_EFFECT_UNKNOWN.value: (None, None),
        ResultStatus.READ_COMPLETED.value: (True, False),
        ResultStatus.PREVIEW_COMPLETED.value: (True, False),
    }
    if (run_success_flag, business_operation_completed) != expected_flags[status]:
        raise ValidationError("ShadowBot result flags do not match status contract.")
    if status in {ResultStatus.FAILED.value, ResultStatus.START_FAILED.value} and not error_code:
        raise ValidationError(f"{status} ShadowBot result requires error_code.")
    if status == ResultStatus.FAILED.value and side_effect_state not in {
        SideEffectState.NOT_STARTED.value,
        SideEffectState.NOT_APPLIED.value,
    }:
        raise ValidationError("FAILED requires NOT_STARTED or NOT_APPLIED side-effect state.")
    if status in {ResultStatus.START_UNKNOWN.value, ResultStatus.SIDE_EFFECT_UNKNOWN.value}:
        if retryable:
            raise ValidationError(f"{status} must not be retryable.")
    if status == ResultStatus.START_UNKNOWN.value and side_effect_state not in {
        SideEffectState.NOT_STARTED.value,
        SideEffectState.UNKNOWN.value,
    }:
        raise ValidationError("START_UNKNOWN requires NOT_STARTED or UNKNOWN side-effect state.")
    if status == ResultStatus.SIDE_EFFECT_UNKNOWN.value and side_effect_state != SideEffectState.UNKNOWN.value:
        raise ValidationError("SIDE_EFFECT_UNKNOWN requires side_effect_state=UNKNOWN.")
    if status == ResultStatus.VERIFIED.value and side_effect_state != SideEffectState.VERIFIED.value:
        raise ValidationError("VERIFIED requires side_effect_state=VERIFIED.")
    if status == ResultStatus.NOT_APPLIED.value and side_effect_state != SideEffectState.NOT_APPLIED.value:
        raise ValidationError("NOT_APPLIED requires side_effect_state=NOT_APPLIED.")
