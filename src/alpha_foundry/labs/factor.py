"""Strict Factor lab payloads and allowlisted compilation."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Literal

from pydantic import Field, field_validator, model_validator

from alpha_foundry.domain.errors import DomainError, ErrorCode, ErrorDetail, ErrorField
from alpha_foundry.domain.models import (
    AstOperator,
    Domain,
    FactorStrategyAst,
    FrozenModel,
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


class FactorExecutionPolicy(FrozenModel):
    """All trading-cost inputs required by the Factor panel engine."""

    commission_rate: Decimal = Field(ge=Decimal("0"))
    slippage_rate: Decimal = Field(ge=Decimal("0"))
    impact_rate: Decimal = Field(ge=Decimal("0"))

    @field_validator("commission_rate", "slippage_rate", "impact_rate")
    @classmethod
    def _validate_finite_decimal(cls, value: Decimal) -> Decimal:
        if not value.is_finite():
            raise ValueError("factor execution-policy rates must be finite")
        return value


class FactorRunPayload(FrozenModel):
    """Immutable point-in-time panel data for one Factor engine run."""

    domain: Literal[Domain.FACTOR] = Domain.FACTOR
    timestamps: tuple[datetime, ...] = Field(min_length=2)
    asset_ids: tuple[str, ...] = Field(min_length=2)
    signals: tuple[tuple[Decimal, ...], ...]
    returns: tuple[tuple[Decimal, ...], ...]
    signal_available_at: tuple[tuple[datetime, ...], ...]
    long_count: int = Field(ge=1)
    short_count: int = Field(ge=1)
    execution_policy: FactorExecutionPolicy

    @field_validator("timestamps")
    @classmethod
    def _normalize_timestamps(cls, values: tuple[datetime, ...]) -> tuple[datetime, ...]:
        return tuple(_utc_timestamp(value) for value in values)

    @field_validator("signal_available_at")
    @classmethod
    def _normalize_availability(
        cls, values: tuple[tuple[datetime, ...], ...]
    ) -> tuple[tuple[datetime, ...], ...]:
        return tuple(tuple(_utc_timestamp(value) for value in row) for row in values)

    @field_validator("signals", "returns")
    @classmethod
    def _validate_finite_matrices(
        cls, values: tuple[tuple[Decimal, ...], ...]
    ) -> tuple[tuple[Decimal, ...], ...]:
        for row in values:
            for value in row:
                if not value.is_finite():
                    raise ValueError("factor panel values must be finite")
        return values

    @model_validator(mode="after")
    def _validate_panel_shape_and_availability(self) -> FactorRunPayload:
        _require_strictly_increasing(self.timestamps, "timestamps")
        _require_unique(self.asset_ids, "asset IDs")
        if any(not asset_id for asset_id in self.asset_ids):
            raise ValueError("asset IDs must be non-empty")

        period_count = len(self.timestamps)
        asset_count = len(self.asset_ids)
        _require_matrix_shape(self.signals, period_count, asset_count, "signals")
        _require_matrix_shape(self.returns, period_count, asset_count, "returns")
        _require_matrix_shape(
            self.signal_available_at,
            period_count,
            asset_count,
            "signal_available_at",
        )
        if self.long_count + self.short_count > asset_count:
            raise ValueError("long_count plus short_count must not exceed the asset count")
        for period_index, availability_row in enumerate(self.signal_available_at):
            for asset_index, available_at in enumerate(availability_row):
                if available_at > self.timestamps[period_index]:
                    raise ValueError(
                        "signal availability must not follow its panel observation "
                        f"at signal_available_at[{period_index}][{asset_index}]"
                    )
        return self


class FactorQuestionPayload(FrozenModel):
    """Strict Factor-only generation request surface."""

    domain: Literal[Domain.FACTOR] = Domain.FACTOR
    research_id: str = Field(min_length=1)
    asset_universe_id: str = Field(min_length=1)
    signal_definition_id: str = Field(min_length=1)


class CompiledFactorStrategy(FrozenModel):
    """Data-only equal-weight long-short ranking plan."""

    long_count: int = Field(ge=1)
    short_count: int = Field(ge=1)


class FactorStrategyCompiler:
    """Compile only the Factor ranking AST into an immutable data plan."""

    _ALLOWED_OPERATOR_IDS = frozenset({"ranked_long_short"})
    _REQUIRED_PARAMETERS = frozenset({"long_count", "short_count"})

    @classmethod
    def compile(cls, strategy: StrategySpec) -> CompiledFactorStrategy:
        if strategy.domain is not Domain.FACTOR or not isinstance(strategy.ast, FactorStrategyAst):
            raise _schema_error("strategy", "Factor compiler requires a FACTOR strategy")
        root = strategy.ast.root
        if not isinstance(root, AstOperator):
            raise _schema_error("strategy.ast.root", "root must be an allowlisted operator")
        if root.operator_id not in cls._ALLOWED_OPERATOR_IDS:
            raise _schema_error("strategy.ast.root.operator_id", "operator is not allowlisted")
        if root.arguments:
            raise _schema_error(
                "strategy.ast.root.arguments", "Factor rank operators do not accept arguments"
            )

        parameters = {parameter.name: parameter.value for parameter in root.parameters}
        if set(parameters) != cls._REQUIRED_PARAMETERS:
            raise _schema_error(
                "strategy.ast.root.parameters",
                "operator requires exactly long_count and short_count",
            )
        long_count = parameters["long_count"]
        short_count = parameters["short_count"]
        if isinstance(long_count, bool) or not isinstance(long_count, int):
            raise _schema_error(
                "strategy.ast.root.parameters.long_count", "long_count must be an integer"
            )
        if isinstance(short_count, bool) or not isinstance(short_count, int):
            raise _schema_error(
                "strategy.ast.root.parameters.short_count", "short_count must be an integer"
            )
        try:
            return CompiledFactorStrategy(long_count=long_count, short_count=short_count)
        except ValueError as error:
            raise _schema_error("strategy.ast.root.parameters", str(error)) from error


def compile_factor_strategy(strategy: StrategySpec) -> CompiledFactorStrategy:
    """Compile a FACTOR StrategySpec into a data-only execution plan."""

    return FactorStrategyCompiler.compile(strategy)




def _schema_error(path: str, reason: str) -> DomainError:
    return DomainError(
        ErrorDetail.for_code(
            ErrorCode.SCHEMA,
            "Factor strategy compilation failed",
            (ErrorField(path=path, reason=reason),),
        )
    )
