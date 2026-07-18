"""Strict StatArb lab payloads and allowlisted compilation."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Literal

from pydantic import Field, field_validator, model_validator

from alpha_foundry.domain.errors import DomainError, ErrorCode, ErrorDetail, ErrorField
from alpha_foundry.domain.models import (
    AstOperator,
    Domain,
    FrozenModel,
    StatArbStrategyAst,
    StrategySpec,
)
from alpha_foundry.domain.validators import (
    require_matrix_shape as _require_matrix_shape,
)
from alpha_foundry.domain.validators import (
    require_strictly_increasing as _require_strictly_increasing,
)
from alpha_foundry.domain.validators import (
    require_unique as _require_unique,
)
from alpha_foundry.domain.validators import (
    require_utc_timestamp as _utc_timestamp,
)


class StatArbExecutionPolicy(FrozenModel):
    """All trading-cost inputs required by the sequential StatArb engine."""

    commission_rate: Decimal = Field(ge=Decimal("0"))
    slippage_rate: Decimal = Field(ge=Decimal("0"))
    impact_rate: Decimal = Field(ge=Decimal("0"))

    @field_validator("commission_rate", "slippage_rate", "impact_rate")
    @classmethod
    def _validate_finite_decimal(cls, value: Decimal) -> Decimal:
        if not value.is_finite():
            raise ValueError("StatArb execution-policy rates must be finite")
        return value


class StatArbRunPayload(FrozenModel):
    """Immutable multi-leg, point-in-time price panel for one StatArb engine run."""

    domain: Literal[Domain.STAT_ARB] = Domain.STAT_ARB
    timestamps: tuple[datetime, ...] = Field(min_length=4)
    leg_ids: tuple[str, ...] = Field(min_length=2)
    prices: tuple[tuple[Decimal, ...], ...]
    price_available_at: tuple[tuple[datetime, ...], ...]
    hedge_weights: tuple[Decimal, ...]
    execution_policy: StatArbExecutionPolicy

    @field_validator("timestamps")
    @classmethod
    def _normalize_timestamps(cls, values: tuple[datetime, ...]) -> tuple[datetime, ...]:
        return tuple(_utc_timestamp(value) for value in values)

    @field_validator("price_available_at")
    @classmethod
    def _normalize_availability(
        cls, values: tuple[tuple[datetime, ...], ...]
    ) -> tuple[tuple[datetime, ...], ...]:
        return tuple(tuple(_utc_timestamp(value) for value in row) for row in values)

    @field_validator("prices")
    @classmethod
    def _validate_prices(
        cls, values: tuple[tuple[Decimal, ...], ...]
    ) -> tuple[tuple[Decimal, ...], ...]:
        for row in values:
            for value in row:
                if not value.is_finite() or value <= Decimal("0"):
                    raise ValueError("StatArb prices must be finite and strictly positive")
        return values

    @field_validator("hedge_weights")
    @classmethod
    def _validate_hedge_weights(cls, values: tuple[Decimal, ...]) -> tuple[Decimal, ...]:
        if any(not value.is_finite() for value in values):
            raise ValueError("StatArb hedge weights must be finite")
        return values

    @model_validator(mode="after")
    def _validate_panel_shape_and_availability(self) -> StatArbRunPayload:
        _require_strictly_increasing(self.timestamps, "timestamps")
        _require_unique(self.leg_ids, "leg IDs")
        if any(not leg_id for leg_id in self.leg_ids):
            raise ValueError("leg IDs must be non-empty")

        period_count = len(self.timestamps)
        leg_count = len(self.leg_ids)
        _require_matrix_shape(self.prices, period_count, leg_count, "prices")
        _require_matrix_shape(
            self.price_available_at,
            period_count,
            leg_count,
            "price_available_at",
        )
        if len(self.hedge_weights) != leg_count:
            raise ValueError("hedge_weights must contain one value for every leg")
        if not any(weight > Decimal("0") for weight in self.hedge_weights) or not any(
            weight < Decimal("0") for weight in self.hedge_weights
        ):
            raise ValueError(
                "hedge_weights must contain at least one positive and one negative value"
            )
        for period_index, availability_row in enumerate(self.price_available_at):
            for leg_index, available_at in enumerate(availability_row):
                if available_at > self.timestamps[period_index]:
                    raise ValueError(
                        "price availability must not follow its panel observation "
                        f"at price_available_at[{period_index}][{leg_index}]"
                    )
        return self


class StatArbQuestionPayload(FrozenModel):
    """Strict StatArb-only generation request surface."""

    domain: Literal[Domain.STAT_ARB] = Domain.STAT_ARB
    research_id: str = Field(min_length=1)
    leg_universe_id: str = Field(min_length=1)
    spread_definition_id: str = Field(min_length=1)


class CompiledStatArbStrategy(FrozenModel):
    """Data-only rolling z-score spread plan with no executable content."""

    lookback: int = Field(ge=2)
    entry_z: Decimal = Field(gt=Decimal("0"))
    exit_z: Decimal = Field(ge=Decimal("0"))

    @field_validator("entry_z", "exit_z")
    @classmethod
    def _validate_finite_decimal(cls, value: Decimal) -> Decimal:
        if not value.is_finite():
            raise ValueError("compiled StatArb thresholds must be finite")
        return value

    @model_validator(mode="after")
    def _validate_thresholds(self) -> CompiledStatArbStrategy:
        if self.exit_z >= self.entry_z:
            raise ValueError("exit_z must be strictly below entry_z")
        return self


class StatArbStrategyCompiler:
    """Compile only the lagged rolling-z-score AST into an immutable data plan."""

    _ALLOWED_OPERATOR_IDS = frozenset({"lagged_zscore_spread"})
    _REQUIRED_PARAMETERS = frozenset({"lookback", "entry_z", "exit_z"})

    @classmethod
    def compile(cls, strategy: StrategySpec) -> CompiledStatArbStrategy:
        if strategy.domain is not Domain.STAT_ARB or not isinstance(
            strategy.ast, StatArbStrategyAst
        ):
            raise _schema_error("strategy", "StatArb compiler requires a STAT_ARB strategy")
        root = strategy.ast.root
        if not isinstance(root, AstOperator):
            raise _schema_error("strategy.ast.root", "root must be an allowlisted operator")
        if root.operator_id not in cls._ALLOWED_OPERATOR_IDS:
            raise _schema_error("strategy.ast.root.operator_id", "operator is not allowlisted")
        if root.arguments:
            raise _schema_error(
                "strategy.ast.root.arguments", "lagged z-score operators do not accept arguments"
            )

        parameters = {parameter.name: parameter.value for parameter in root.parameters}
        if set(parameters) != cls._REQUIRED_PARAMETERS:
            raise _schema_error(
                "strategy.ast.root.parameters",
                "operator requires exactly lookback, entry_z, and exit_z",
            )
        lookback = parameters["lookback"]
        entry_z = parameters["entry_z"]
        exit_z = parameters["exit_z"]
        if isinstance(lookback, bool) or not isinstance(lookback, int):
            raise _schema_error(
                "strategy.ast.root.parameters.lookback", "lookback must be an integer"
            )
        if isinstance(entry_z, bool) or not isinstance(entry_z, Decimal):
            raise _schema_error("strategy.ast.root.parameters.entry_z", "entry_z must be a Decimal")
        if isinstance(exit_z, bool) or not isinstance(exit_z, Decimal):
            raise _schema_error("strategy.ast.root.parameters.exit_z", "exit_z must be a Decimal")
        try:
            return CompiledStatArbStrategy(lookback=lookback, entry_z=entry_z, exit_z=exit_z)
        except ValueError as error:
            raise _schema_error("strategy.ast.root.parameters", str(error)) from error


def compile_statarb_strategy(strategy: StrategySpec) -> CompiledStatArbStrategy:
    """Compile a STAT_ARB StrategySpec into a data-only execution plan."""

    return StatArbStrategyCompiler.compile(strategy)




def _schema_error(path: str, reason: str) -> DomainError:
    return DomainError(
        ErrorDetail.for_code(
            ErrorCode.SCHEMA,
            "StatArb strategy compilation failed",
            (ErrorField(path=path, reason=reason),),
        )
    )
