"""Strict Structural Flow payloads and a data-only strategy compiler."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal, DecimalException
from typing import Literal

from pydantic import Field, field_validator, model_validator

from alpha_foundry.domain.errors import DomainError, ErrorCode, ErrorDetail, ErrorField
from alpha_foundry.domain.models import (
    AstOperator,
    Domain,
    FrozenModel,
    StrategySpec,
    StructuralFlowStrategyAst,
)
from alpha_foundry.domain.validators import (
    require_strictly_increasing as _strictly_increasing,
)
from alpha_foundry.domain.validators import (
    require_utc_timestamp as _normalize_timestamp,
)
from alpha_foundry.engines.base import deterministic_decimal_context

_ZERO = Decimal("0")
_ONE = Decimal("1")


class StructuralFlowExecutionPolicy(FrozenModel):
    """Explicit impact, decay, execution, cost, and cash-accounting semantics."""

    execution_lag_periods: int = Field(ge=1)
    impact_multiplier: Decimal
    decay_rate: Decimal = Field(ge=_ZERO, le=_ONE)
    maximum_position: Decimal = Field(gt=_ZERO)
    allow_negative_cash: bool
    commission_rate: Decimal = Field(ge=_ZERO)
    slippage_rate: Decimal = Field(ge=_ZERO)
    impact_rate: Decimal = Field(ge=_ZERO)

    @field_validator(
        "impact_multiplier",
        "decay_rate",
        "maximum_position",
        "commission_rate",
        "slippage_rate",
        "impact_rate",
    )
    @classmethod
    def _finite_decimal(cls, value: Decimal) -> Decimal:
        if not value.is_finite():
            raise ValueError("structural-flow policy values must be finite")
        return value


class StructuralFlowEvent(FrozenModel):
    """A signed structural event, unavailable until its declared PIT timestamp."""

    event_at: datetime
    available_at: datetime
    impact: Decimal

    @field_validator("event_at", "available_at")
    @classmethod
    def _utc_timestamp(cls, value: datetime) -> datetime:
        return _normalize_timestamp(value)

    @field_validator("impact")
    @classmethod
    def _finite_impact(cls, value: Decimal) -> Decimal:
        if not value.is_finite():
            raise ValueError("event impact must be finite")
        return value

    @model_validator(mode="after")
    def _point_in_time_order(self) -> StructuralFlowEvent:
        if self.available_at < self.event_at:
            raise ValueError("available_at must not precede event_at")
        return self


class StructuralFlowObservation(FrozenModel):
    """One timestamped execution price in the structural-flow market clock."""

    timestamp: datetime
    price: Decimal = Field(gt=_ZERO)

    @field_validator("timestamp")
    @classmethod
    def _utc_timestamp(cls, value: datetime) -> datetime:
        return _normalize_timestamp(value)

    @field_validator("price")
    @classmethod
    def _finite_price(cls, value: Decimal) -> Decimal:
        if not value.is_finite():
            raise ValueError("observation prices must be finite")
        return value


class StructuralFlowRunPayload(FrozenModel):
    """Immutable input for point-in-time, lagged structural-flow accounting."""

    domain: Literal[Domain.STRUCTURAL_FLOW] = Domain.STRUCTURAL_FLOW
    observations: tuple[StructuralFlowObservation, ...] = Field(min_length=1)
    events: tuple[StructuralFlowEvent, ...] = Field(min_length=1)
    execution_policy: StructuralFlowExecutionPolicy
    position_scale: Decimal = Field(gt=_ZERO)
    initial_cash: Decimal = Field(ge=_ZERO)
    initial_position: Decimal

    @field_validator("position_scale", "initial_cash", "initial_position")
    @classmethod
    def _finite_decimal(cls, value: Decimal) -> Decimal:
        if not value.is_finite():
            raise ValueError("structural-flow account values must be finite")
        return value

    @model_validator(mode="after")
    def _ordered_inputs_and_account(self) -> StructuralFlowRunPayload:
        _strictly_increasing(
            tuple(observation.timestamp for observation in self.observations),
            "observation timestamps",
        )
        _strictly_increasing(tuple(event.event_at for event in self.events), "event timestamps")
        _strictly_increasing(
            tuple(event.available_at for event in self.events),
            "event availability timestamps",
        )
        if abs(self.initial_position) > self.execution_policy.maximum_position:
            raise ValueError("initial position exceeds the explicit maximum position")
        try:
            with deterministic_decimal_context():
                initial_equity = (
                    self.initial_cash + self.initial_position * self.observations[0].price
                )
        except DecimalException as error:
            raise ValueError("initial marked equity arithmetic failed") from error
        if initial_equity <= _ZERO:
            raise ValueError("initial marked equity must be positive")
        return self


class StructuralFlowQuestionPayload(FrozenModel):
    """Strict domain request used to constrain Structural Flow strategy generation."""

    domain: Literal[Domain.STRUCTURAL_FLOW] = Domain.STRUCTURAL_FLOW
    research_id: str = Field(min_length=1)
    instrument_id: str = Field(min_length=1)
    event_source_id: str = Field(min_length=1)


class CompiledStructuralFlowStrategy(FrozenModel):
    """Data-only position scaling derived from an allowlisted structural-flow AST."""

    position_scale: Decimal = Field(gt=_ZERO)

    @field_validator("position_scale")
    @classmethod
    def _finite_scale(cls, value: Decimal) -> Decimal:
        if not value.is_finite():
            raise ValueError("compiled position_scale must be finite")
        return value


class StructuralFlowStrategyCompiler:
    """Compile only declared decayed-impact operators; never execute candidate code."""

    _ALLOWED_OPERATOR_IDS = frozenset({"decayed_event_impact"})
    _REQUIRED_PARAMETERS = frozenset({"position_scale"})

    @classmethod
    def compile(cls, strategy: StrategySpec) -> CompiledStructuralFlowStrategy:
        if strategy.domain is not Domain.STRUCTURAL_FLOW or not isinstance(
            strategy.ast, StructuralFlowStrategyAst
        ):
            raise _schema_error(
                "strategy", "Structural Flow compiler requires a STRUCTURAL_FLOW strategy"
            )
        root = strategy.ast.root
        if not isinstance(root, AstOperator):
            raise _schema_error("strategy.ast.root", "root must be an allowlisted operator")
        if root.operator_id not in cls._ALLOWED_OPERATOR_IDS:
            raise _schema_error("strategy.ast.root.operator_id", "operator is not allowlisted")
        if root.arguments:
            raise _schema_error(
                "strategy.ast.root.arguments", "impact operators do not accept arguments"
            )
        parameters = {parameter.name: parameter.value for parameter in root.parameters}
        if set(parameters) != cls._REQUIRED_PARAMETERS:
            raise _schema_error(
                "strategy.ast.root.parameters",
                "operator requires exactly position_scale",
            )
        position_scale = parameters["position_scale"]
        if not isinstance(position_scale, Decimal):
            raise _schema_error(
                "strategy.ast.root.parameters.position_scale",
                "position_scale must be a Decimal",
            )
        try:
            return CompiledStructuralFlowStrategy(position_scale=position_scale)
        except ValueError as error:
            raise _schema_error("strategy.ast.root.parameters", str(error)) from error


def compile_structural_flow_strategy(strategy: StrategySpec) -> CompiledStructuralFlowStrategy:
    """Compile a STRUCTURAL_FLOW StrategySpec into a non-executable position plan."""

    return StructuralFlowStrategyCompiler.compile(strategy)




def _schema_error(path: str, reason: str) -> DomainError:
    return DomainError(
        ErrorDetail.for_code(
            ErrorCode.SCHEMA,
            "Structural Flow strategy compilation failed",
            (ErrorField(path=path, reason=reason),),
        )
    )
