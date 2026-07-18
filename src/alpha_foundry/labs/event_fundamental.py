"""Typed Event Fundamental research inputs and a non-executable strategy compiler."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import Literal

from pydantic import Field, field_validator, model_validator

from alpha_foundry.domain.errors import DomainError, ErrorCode, ErrorDetail, ErrorField
from alpha_foundry.domain.models import (
    AstOperator,
    Domain,
    EventFundamentalStrategyAst,
    FrozenModel,
    StrategySpec,
)


class EventFundamentalExecutionPolicy(FrozenModel):
    """All event entry, holding, exposure, and transaction-cost semantics are explicit."""

    entry_lag_periods: int = Field(ge=1)
    holding_periods: int = Field(ge=1)
    signal_threshold: Decimal = Field(ge=Decimal("0"))
    position_size: Decimal = Field(gt=Decimal("0"))
    maximum_gross_exposure: Decimal = Field(gt=Decimal("0"))
    allow_short: bool
    commission_rate: Decimal = Field(ge=Decimal("0"))
    slippage_rate: Decimal = Field(ge=Decimal("0"))
    impact_rate: Decimal = Field(ge=Decimal("0"))

    @field_validator(
        "signal_threshold",
        "position_size",
        "maximum_gross_exposure",
        "commission_rate",
        "slippage_rate",
        "impact_rate",
    )
    @classmethod
    def _validate_finite_decimal(cls, value: Decimal) -> Decimal:
        if not value.is_finite():
            raise ValueError("execution policy Decimal values must be finite")
        return value

    @model_validator(mode="after")
    def _validate_exposure(self) -> EventFundamentalExecutionPolicy:
        if self.position_size > self.maximum_gross_exposure:
            raise ValueError("position_size must not exceed maximum_gross_exposure")
        return self


class FundamentalEvent(FrozenModel):
    """One immutable event observation with its first observable time."""

    event_id: str = Field(min_length=1)
    published_at: datetime
    available_at: datetime
    surprise: Decimal

    @field_validator("published_at", "available_at")
    @classmethod
    def _normalize_timestamp(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("event timestamps must be timezone-aware")
        return value.astimezone(UTC)

    @field_validator("surprise")
    @classmethod
    def _validate_finite_surprise(cls, value: Decimal) -> Decimal:
        if not value.is_finite():
            raise ValueError("event surprise must be finite")
        return value

    @model_validator(mode="after")
    def _validate_publication_order(self) -> FundamentalEvent:
        if self.available_at < self.published_at:
            raise ValueError("available_at must not precede published_at")
        return self


class EventFundamentalRunPayload(FrozenModel):
    """Validated immutable numerical input for :class:`EventFundamentalEngine`."""

    domain: Literal[Domain.EVENT_FUNDAMENTAL] = Domain.EVENT_FUNDAMENTAL
    timestamps: tuple[datetime, ...] = Field(min_length=2)
    returns: tuple[Decimal, ...] = Field(min_length=2)
    events: tuple[FundamentalEvent, ...] = Field(min_length=1)
    policy: EventFundamentalExecutionPolicy

    @field_validator("timestamps")
    @classmethod
    def _normalize_timestamps(cls, values: tuple[datetime, ...]) -> tuple[datetime, ...]:
        normalized: list[datetime] = []
        for value in values:
            if value.tzinfo is None or value.utcoffset() is None:
                raise ValueError("timestamps must be timezone-aware")
            normalized.append(value.astimezone(UTC))
        return tuple(normalized)

    @field_validator("returns")
    @classmethod
    def _validate_returns(cls, values: tuple[Decimal, ...]) -> tuple[Decimal, ...]:
        if any(not value.is_finite() for value in values):
            raise ValueError("returns must be finite")
        return values

    @model_validator(mode="after")
    def _validate_payload(self) -> EventFundamentalRunPayload:
        if len(self.returns) != len(self.timestamps):
            raise ValueError("returns must have the same length as timestamps")
        if any(
            current <= previous
            for previous, current in zip(self.timestamps[:-1], self.timestamps[1:], strict=True)
        ):
            raise ValueError("timestamps must be strictly increasing")
        event_ids = tuple(event.event_id for event in self.events)
        if len(event_ids) != len(set(event_ids)):
            raise ValueError("event IDs must be unique")
        return self


class EventFundamentalQuestionPayload(FrozenModel):
    """Strict domain request used to constrain Event Fundamental strategy generation."""

    domain: Literal[Domain.EVENT_FUNDAMENTAL] = Domain.EVENT_FUNDAMENTAL
    research_id: str = Field(min_length=1)
    instrument_id: str = Field(min_length=1)
    event_type: str = Field(min_length=1)


class CompiledEventFundamentalStrategy(FrozenModel):
    """Data-only event-entry plan derived from an allowlisted AST."""

    entry_lag_periods: int = Field(ge=1)
    holding_periods: int = Field(ge=1)
    signal_threshold: Decimal = Field(ge=Decimal("0"))
    position_size: Decimal = Field(gt=Decimal("0"))
    maximum_gross_exposure: Decimal = Field(gt=Decimal("0"))
    allow_short: bool

    @field_validator("signal_threshold", "position_size", "maximum_gross_exposure")
    @classmethod
    def _validate_finite_decimal(cls, value: Decimal) -> Decimal:
        if not value.is_finite():
            raise ValueError("compiled strategy Decimal values must be finite")
        return value

    @model_validator(mode="after")
    def _validate_exposure(self) -> CompiledEventFundamentalStrategy:
        if self.position_size > self.maximum_gross_exposure:
            raise ValueError("position_size must not exceed maximum_gross_exposure")
        return self


class _EventFundamentalStrategyCompiler:
    """Compile only declared event-surprise ASTs; arbitrary code is never accepted."""

    _ALLOWED_OPERATOR_IDS = frozenset({"event_surprise"})
    _REQUIRED_PARAMETERS = frozenset(
        {
            "entry_lag_periods",
            "holding_periods",
            "signal_threshold",
            "position_size",
            "maximum_gross_exposure",
            "allow_short",
        }
    )

    @classmethod
    def compile(cls, strategy: StrategySpec) -> CompiledEventFundamentalStrategy:

        if strategy.domain is not Domain.EVENT_FUNDAMENTAL or not isinstance(
            strategy.ast, EventFundamentalStrategyAst
        ):
            raise _schema_error(
                "strategy", "Event Fundamental compiler requires an EVENT_FUNDAMENTAL strategy"
            )
        root = strategy.ast.root
        if not isinstance(root, AstOperator):
            raise _schema_error("strategy.ast.root", "root must be an allowlisted operator")
        if root.operator_id not in cls._ALLOWED_OPERATOR_IDS:
            raise _schema_error("strategy.ast.root.operator_id", "operator is not allowlisted")
        if root.arguments:
            raise _schema_error(
                "strategy.ast.root.arguments", "event surprise operators do not accept arguments"
            )
        parameters = {parameter.name: parameter.value for parameter in root.parameters}
        if set(parameters) != cls._REQUIRED_PARAMETERS:
            raise _schema_error(
                "strategy.ast.root.parameters",
                "operator requires explicit lag, holding, signal, exposure, and short-sale parameters",
            )
        entry_lag_periods = parameters["entry_lag_periods"]
        holding_periods = parameters["holding_periods"]
        signal_threshold = parameters["signal_threshold"]
        position_size = parameters["position_size"]
        maximum_gross_exposure = parameters["maximum_gross_exposure"]
        allow_short = parameters["allow_short"]
        integer_parameters: dict[str, int] = {}
        for name, value in (
            ("entry_lag_periods", entry_lag_periods),
            ("holding_periods", holding_periods),
        ):
            if isinstance(value, bool) or not isinstance(value, int):
                raise _schema_error(
                    f"strategy.ast.root.parameters.{name}", f"{name} must be an integer"
                )
            integer_parameters[name] = value
        decimal_parameters: dict[str, Decimal] = {}
        for name, value in (
            ("signal_threshold", signal_threshold),
            ("position_size", position_size),
            ("maximum_gross_exposure", maximum_gross_exposure),
        ):
            if isinstance(value, bool) or not isinstance(value, Decimal):
                raise _schema_error(
                    f"strategy.ast.root.parameters.{name}", f"{name} must be a Decimal"
                )
            decimal_parameters[name] = value
        if not isinstance(allow_short, bool):
            raise _schema_error(
                "strategy.ast.root.parameters.allow_short", "allow_short must be a boolean"
            )
        try:
            return CompiledEventFundamentalStrategy(
                entry_lag_periods=integer_parameters["entry_lag_periods"],
                holding_periods=integer_parameters["holding_periods"],
                signal_threshold=decimal_parameters["signal_threshold"],
                position_size=decimal_parameters["position_size"],
                maximum_gross_exposure=decimal_parameters["maximum_gross_exposure"],
                allow_short=allow_short,
            )
        except ValueError as error:
            raise _schema_error("strategy.ast.root.parameters", str(error)) from error


def _schema_error(path: str, reason: str) -> DomainError:
    return DomainError(
        ErrorDetail.for_code(
            ErrorCode.SCHEMA,
            "Event Fundamental strategy compilation failed",
            (ErrorField(path=path, reason=reason),),
        )
    )
