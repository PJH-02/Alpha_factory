"""Typed Time Series research inputs and a non-executable strategy compiler."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import Literal

from pydantic import Field, field_validator, model_validator

from alpha_foundry.domain.errors import DomainError, ErrorCode, ErrorDetail, ErrorField
from alpha_foundry.domain.models import (
    AstOperator,
    Domain,
    FrozenModel,
    StrategySpec,
    TimeSeriesStrategyAst,
)


class TimeSeriesExecutionPolicy(FrozenModel):
    """All signal timing, exposure, rebalancing, and transaction costs are explicit."""

    lookback_periods: int = Field(ge=1)
    position_lag_periods: int = Field(ge=1)
    rebalance_periods: int = Field(ge=1)
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
    def _validate_exposure(self) -> TimeSeriesExecutionPolicy:
        if self.position_size > self.maximum_gross_exposure:
            raise ValueError("position_size must not exceed maximum_gross_exposure")
        return self


class TimeSeriesRunPayload(FrozenModel):
    """Validated immutable numerical input for :class:`TimeSeriesEngine`."""

    domain: Literal[Domain.TIME_SERIES] = Domain.TIME_SERIES
    timestamps: tuple[datetime, ...] = Field(min_length=2)
    prices: tuple[Decimal, ...] = Field(min_length=2)
    price_available_at: tuple[datetime, ...] = Field(min_length=2)
    policy: TimeSeriesExecutionPolicy

    @field_validator("timestamps", "price_available_at")
    @classmethod
    def _normalize_timestamps(cls, values: tuple[datetime, ...]) -> tuple[datetime, ...]:
        normalized: list[datetime] = []
        for value in values:
            if value.tzinfo is None or value.utcoffset() is None:
                raise ValueError("timestamps must be timezone-aware")
            normalized.append(value.astimezone(UTC))
        return tuple(normalized)

    @field_validator("prices")
    @classmethod
    def _validate_prices(cls, values: tuple[Decimal, ...]) -> tuple[Decimal, ...]:
        if any(not value.is_finite() for value in values):
            raise ValueError("prices must be finite")
        return values

    @model_validator(mode="after")
    def _validate_payload(self) -> TimeSeriesRunPayload:
        if len(self.prices) != len(self.timestamps):
            raise ValueError("prices must have the same length as timestamps")
        if len(self.price_available_at) != len(self.timestamps):
            raise ValueError("price_available_at must have the same length as timestamps")
        if any(
            current <= previous
            for previous, current in zip(self.timestamps[:-1], self.timestamps[1:], strict=True)
        ):
            raise ValueError("timestamps must be strictly increasing")
        if any(
            available_at > timestamp
            for available_at, timestamp in zip(
                self.price_available_at, self.timestamps, strict=True
            )
        ):
            raise ValueError("price_available_at must not follow its observation timestamp")
        return self


class TimeSeriesQuestionPayload(FrozenModel):
    """Strict domain request used to constrain Time Series strategy generation."""

    domain: Literal[Domain.TIME_SERIES] = Domain.TIME_SERIES
    research_id: str = Field(min_length=1)
    instrument_id: str = Field(min_length=1)
    sampling_interval: str = Field(min_length=1)


class CompiledTimeSeriesStrategy(FrozenModel):
    """Data-only rolling-momentum plan derived from an allowlisted AST."""

    lookback_periods: int = Field(ge=1)
    position_lag_periods: int = Field(ge=1)
    rebalance_periods: int = Field(ge=1)
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
    def _validate_exposure(self) -> CompiledTimeSeriesStrategy:
        if self.position_size > self.maximum_gross_exposure:
            raise ValueError("position_size must not exceed maximum_gross_exposure")
        return self


class _TimeSeriesStrategyCompiler:
    """Compile only declared lagged rolling-momentum ASTs; never execute user code."""

    _ALLOWED_OPERATOR_IDS = frozenset({"rolling_momentum"})
    _REQUIRED_PARAMETERS = frozenset(
        {
            "lookback_periods",
            "position_lag_periods",
            "rebalance_periods",
            "signal_threshold",
            "position_size",
            "maximum_gross_exposure",
            "allow_short",
        }
    )

    @classmethod
    def compile(cls, strategy: StrategySpec) -> CompiledTimeSeriesStrategy:

        if strategy.domain is not Domain.TIME_SERIES or not isinstance(
            strategy.ast, TimeSeriesStrategyAst
        ):
            raise _schema_error("strategy", "Time Series compiler requires a TIME_SERIES strategy")
        root = strategy.ast.root
        if not isinstance(root, AstOperator):
            raise _schema_error("strategy.ast.root", "root must be an allowlisted operator")
        if root.operator_id not in cls._ALLOWED_OPERATOR_IDS:
            raise _schema_error("strategy.ast.root.operator_id", "operator is not allowlisted")
        if root.arguments:
            raise _schema_error(
                "strategy.ast.root.arguments", "rolling momentum operators do not accept arguments"
            )
        parameters = {parameter.name: parameter.value for parameter in root.parameters}
        if set(parameters) != cls._REQUIRED_PARAMETERS:
            raise _schema_error(
                "strategy.ast.root.parameters",
                "operator requires explicit lookback, lag, rebalancing, signal, exposure, and short-sale parameters",
            )
        lookback_periods = parameters["lookback_periods"]
        position_lag_periods = parameters["position_lag_periods"]
        rebalance_periods = parameters["rebalance_periods"]
        signal_threshold = parameters["signal_threshold"]
        position_size = parameters["position_size"]
        maximum_gross_exposure = parameters["maximum_gross_exposure"]
        allow_short = parameters["allow_short"]
        integer_parameters: dict[str, int] = {}
        for name, value in (
            ("lookback_periods", lookback_periods),
            ("position_lag_periods", position_lag_periods),
            ("rebalance_periods", rebalance_periods),
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
            return CompiledTimeSeriesStrategy(
                lookback_periods=integer_parameters["lookback_periods"],
                position_lag_periods=integer_parameters["position_lag_periods"],
                rebalance_periods=integer_parameters["rebalance_periods"],
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
            "Time Series strategy compilation failed",
            (ErrorField(path=path, reason=reason),),
        )
    )
