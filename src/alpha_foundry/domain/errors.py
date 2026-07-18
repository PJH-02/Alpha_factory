"""Reason-coded domain errors shared across deterministic services."""

from __future__ import annotations

from enum import StrEnum
from typing import Final

from pydantic import BaseModel, ConfigDict, Field


class ErrorCode(StrEnum):
    """Stable machine-readable failure codes defined by the public contract."""

    SCHEMA = "AF-SCHEMA-001"
    DOMAIN = "AF-DOMAIN-001"
    POLICY = "AF-POLICY-001"
    RESOURCE = "AF-RESOURCE-001"
    STATE = "AF-STATE-001"
    IDEMPOTENCY = "AF-IDEMPOTENCY-001"
    HOLDOUT = "AF-HOLDOUT-001"
    CAPABILITY = "AF-CAPABILITY-001"
    PBO_INCOMPLETE = "AF-PBO-INCOMPLETE"
    VALIDATION = "AF-VALIDATION-001"
    JOB_INTERRUPTED = "AF-JOB-INTERRUPTED"
    LLM = "AF-LLM-001"
    STORAGE = "AF-STORAGE-001"


_REASON_RETRYABLE: Final[frozenset[ErrorCode]] = frozenset(
    {
        ErrorCode.CAPABILITY,
        ErrorCode.LLM,
        ErrorCode.STORAGE,
    }
)


class ErrorField(BaseModel):
    """A field-level explanation that is safe to return across boundaries."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    path: str = Field(min_length=1)
    reason: str = Field(min_length=1)


class ErrorDetail(BaseModel):
    """Structured failure information; callers branch only on :attr:`code`."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    code: ErrorCode
    message: str = Field(min_length=1)
    retryable: bool
    details: tuple[ErrorField, ...] = ()

    @classmethod
    def for_code(
        cls,
        code: ErrorCode,
        message: str,
        details: tuple[ErrorField, ...] = (),
    ) -> ErrorDetail:
        """Create a contract-consistent error without caller-selected retryability."""

        return cls(
            code=code,
            message=message,
            retryable=code in _REASON_RETRYABLE,
            details=details,
        )


ReasonCode = ErrorCode


class DomainError(Exception):
    """Exception wrapper for a typed domain failure."""

    def __init__(self, detail: ErrorDetail) -> None:
        self.detail = detail
        super().__init__(f"{detail.code.value}: {detail.message}")
