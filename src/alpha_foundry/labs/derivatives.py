"""Typed Derivatives research inputs and the non-executable strategy compiler."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Literal

from pydantic import Field, field_validator, model_validator

from alpha_foundry.domain.errors import DomainError, ErrorCode, ErrorDetail, ErrorField
from alpha_foundry.domain.models import (
    AstOperator,
    DerivativesStrategyAst,
    Digest,
    Domain,
    FrozenModel,
    Identifier,
    StrategySpec,
    Version,
)
from alpha_foundry.domain.validators import (
    require_unique as _require_unique,
)
from alpha_foundry.domain.validators import (
    require_utc_timestamp as _utc_timestamp,
)


class CashShortfallAction(StrEnum):
    """The only permitted response when an intended rebalance lacks free cash."""

    REJECT_ORDER = "REJECT_ORDER"
    LIQUIDATE = "LIQUIDATE"


class MarginCallAction(StrEnum):
    """The explicitly pinned response when collateral falls below maintenance margin."""

    LIQUIDATE = "LIQUIDATE"


class DerivativesExecutionPolicy(FrozenModel):
    """An identified policy with full futures-and-hedge accounting semantics."""

    policy_id: Identifier
    version: Version
    content_hash: Digest
    contract_multiplier: Decimal = Field(gt=Decimal("0"))
    funding_rate_per_period: Decimal
    borrow_rate_per_period: Decimal = Field(ge=Decimal("0"))
    initial_margin_rate: Decimal = Field(gt=Decimal("0"), le=Decimal("1"))
    maintenance_margin_rate: Decimal = Field(gt=Decimal("0"), le=Decimal("1"))
    derivative_transaction_cost_rate: Decimal = Field(ge=Decimal("0"))
    underlying_transaction_cost_rate: Decimal = Field(ge=Decimal("0"))
    execution_lag_periods: int = Field(ge=1)
    cash_shortfall_action: CashShortfallAction
    margin_call_action: MarginCallAction
    short_underlying_allowed: bool

    @field_validator(
        "contract_multiplier",
        "funding_rate_per_period",
        "borrow_rate_per_period",
        "initial_margin_rate",
        "maintenance_margin_rate",
        "derivative_transaction_cost_rate",
        "underlying_transaction_cost_rate",
    )
    @classmethod
    def _validate_finite_decimal(cls, value: Decimal) -> Decimal:
        if not value.is_finite():
            raise ValueError("derivatives policy values must be finite")
        return value

    @model_validator(mode="after")
    def _validate_margin_rates(self) -> DerivativesExecutionPolicy:
        if self.maintenance_margin_rate > self.initial_margin_rate:
            raise ValueError("maintenance margin rate must not exceed initial margin rate")
        return self


class DerivativeObservation(FrozenModel):
    """Synchronized underlying and derivative prices with their PIT availability time."""

    observed_at: datetime
    available_at: datetime
    underlying_price: Decimal = Field(gt=Decimal("0"))
    derivative_price: Decimal = Field(gt=Decimal("0"))

    @field_validator("observed_at", "available_at")
    @classmethod
    def _normalize_timestamp(cls, value: datetime) -> datetime:
        return _utc_timestamp(value)

    @field_validator("underlying_price", "derivative_price")
    @classmethod
    def _validate_finite_decimal(cls, value: Decimal) -> Decimal:
        if not value.is_finite():
            raise ValueError("prices must be finite")
        return value

    @model_validator(mode="after")
    def _validate_point_in_time_order(self) -> DerivativeObservation:
        if self.available_at < self.observed_at:
            raise ValueError("price availability must not precede its observation")
        return self


class DerivativeSignal(FrozenModel):
    """One scalar signal observable at ``available_at`` and executed only after policy lag."""

    available_at: datetime
    value: Decimal

    @field_validator("available_at")
    @classmethod
    def _normalize_timestamp(cls, value: datetime) -> datetime:
        return _utc_timestamp(value)

    @field_validator("value")
    @classmethod
    def _validate_finite_decimal(cls, value: Decimal) -> Decimal:
        if not value.is_finite():
            raise ValueError("signals must be finite")
        return value


class DerivativesRunPayload(FrozenModel):
    """Immutable numerical input for deterministic lagged derivatives accounting."""

    domain: Literal[Domain.DERIVATIVES] = Domain.DERIVATIVES
    observations: tuple[DerivativeObservation, ...] = Field(min_length=1)
    signals: tuple[DerivativeSignal, ...] = Field(min_length=1)
    execution_policy: DerivativesExecutionPolicy
    initial_cash: Decimal = Field(gt=Decimal("0"))
    initial_underlying_units: Decimal
    initial_derivative_contracts: Decimal

    @field_validator("initial_cash", "initial_underlying_units", "initial_derivative_contracts")
    @classmethod
    def _validate_finite_decimal(cls, value: Decimal) -> Decimal:
        if not value.is_finite():
            raise ValueError("initial account values must be finite")
        return value

    @model_validator(mode="after")
    def _validate_ordering_and_shorting(self) -> DerivativesRunPayload:
        observation_times = tuple(observation.available_at for observation in self.observations)
        signal_times = tuple(signal.available_at for signal in self.signals)
        _require_unique(observation_times, "observation availability timestamps")
        _require_unique(signal_times, "signal availability timestamps")
        if self.initial_underlying_units < 0 and not self.execution_policy.short_underlying_allowed:
            raise ValueError(
                "initial short underlying position requires an explicit shorting policy"
            )
        return self


class DerivativesQuestionPayload(FrozenModel):
    """Strict domain request used to constrain Derivatives strategy generation."""

    domain: Literal[Domain.DERIVATIVES] = Domain.DERIVATIVES
    research_id: str = Field(min_length=1)
    underlying_instrument_id: str = Field(min_length=1)
    derivative_instrument_id: str = Field(min_length=1)
    contract_currency: str = Field(min_length=1)


class CompiledDerivativesStrategy(FrozenModel):
    """Data-only lagged hedge plan derived from the Derivatives allowlist."""

    signal_scale: Decimal
    underlying_hedge_ratio: Decimal

    @field_validator("signal_scale", "underlying_hedge_ratio")
    @classmethod
    def _validate_finite_decimal(cls, value: Decimal) -> Decimal:
        if not value.is_finite():
            raise ValueError("compiled strategy numeric values must be finite")
        return value


class _DerivativesStrategyCompiler:
    """Compile a small declared DSL and reject arbitrary operators or executable fields."""

    _ALLOWED_OPERATOR_IDS = frozenset({"lagged_hedge"})
    _REQUIRED_PARAMETERS = frozenset({"signal_scale", "underlying_hedge_ratio"})

    @classmethod
    def compile(cls, strategy: StrategySpec) -> CompiledDerivativesStrategy:

        if strategy.domain is not Domain.DERIVATIVES or not isinstance(
            strategy.ast, DerivativesStrategyAst
        ):
            raise _schema_error("strategy", "Derivatives compiler requires a DERIVATIVES strategy")
        root = strategy.ast.root
        if not isinstance(root, AstOperator):
            raise _schema_error("strategy.ast.root", "root must be an allowlisted operator")
        if root.operator_id not in cls._ALLOWED_OPERATOR_IDS:
            raise _schema_error("strategy.ast.root.operator_id", "operator is not allowlisted")
        if root.arguments:
            raise _schema_error(
                "strategy.ast.root.arguments", "lagged hedge operators do not accept arguments"
            )
        parameters = {parameter.name: parameter.value for parameter in root.parameters}
        if set(parameters) != cls._REQUIRED_PARAMETERS:
            raise _schema_error(
                "strategy.ast.root.parameters",
                "operator requires exactly signal_scale and underlying_hedge_ratio",
            )
        signal_scale = parameters["signal_scale"]
        hedge_ratio = parameters["underlying_hedge_ratio"]
        if isinstance(signal_scale, bool) or not isinstance(signal_scale, Decimal):
            raise _schema_error(
                "strategy.ast.root.parameters.signal_scale", "signal_scale must be a Decimal"
            )
        if isinstance(hedge_ratio, bool) or not isinstance(hedge_ratio, Decimal):
            raise _schema_error(
                "strategy.ast.root.parameters.underlying_hedge_ratio",
                "underlying_hedge_ratio must be a Decimal",
            )
        try:
            return CompiledDerivativesStrategy(
                signal_scale=signal_scale,
                underlying_hedge_ratio=hedge_ratio,
            )
        except ValueError as error:
            raise _schema_error("strategy.ast.root.parameters", str(error)) from error




def _schema_error(path: str, reason: str) -> DomainError:
    return DomainError(
        ErrorDetail.for_code(
            ErrorCode.SCHEMA,
            "Derivatives strategy compilation failed",
            (ErrorField(path=path, reason=reason),),
        )
    )
