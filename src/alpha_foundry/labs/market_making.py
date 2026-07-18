"""Strict Market Making payloads and a data-only strategy compiler."""

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
    MarketMakingStrategyAst,
    StrategySpec,
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
_TWO = Decimal("2")


class MarketMakingExecutionPolicy(FrozenModel):
    """Complete event-level execution and accounting policy for one simulation."""

    latency_events: int = Field(ge=0)
    fill_ratio: Decimal = Field(ge=_ZERO, le=_ONE)
    quote_spread: Decimal = Field(gt=_ZERO)
    inventory_limit: Decimal = Field(gt=_ZERO)
    allow_negative_cash: bool
    commission_rate: Decimal = Field(ge=_ZERO)
    slippage_rate: Decimal = Field(ge=_ZERO)
    impact_rate: Decimal = Field(ge=_ZERO)
    adverse_selection_rate: Decimal = Field(ge=_ZERO)

    @field_validator(
        "fill_ratio",
        "quote_spread",
        "inventory_limit",
        "commission_rate",
        "slippage_rate",
        "impact_rate",
        "adverse_selection_rate",
    )
    @classmethod
    def _finite_decimal(cls, value: Decimal) -> Decimal:
        if not value.is_finite():
            raise ValueError("market-making policy values must be finite")
        return value


class MarketMakingEvent(FrozenModel):
    """One top-of-book event and its observed aggressive buy/sell flow."""

    timestamp: datetime
    bid: Decimal = Field(gt=_ZERO)
    ask: Decimal = Field(gt=_ZERO)
    bid_size: Decimal = Field(ge=_ZERO)
    ask_size: Decimal = Field(ge=_ZERO)
    buy_volume: Decimal = Field(ge=_ZERO)
    sell_volume: Decimal = Field(ge=_ZERO)

    @field_validator("timestamp")
    @classmethod
    def _utc_timestamp(cls, value: datetime) -> datetime:
        return _normalize_timestamp(value)

    @field_validator("bid", "ask", "bid_size", "ask_size", "buy_volume", "sell_volume")
    @classmethod
    def _finite_decimal(cls, value: Decimal) -> Decimal:
        if not value.is_finite():
            raise ValueError("market-making event values must be finite")
        return value

    @model_validator(mode="after")
    def _valid_book(self) -> MarketMakingEvent:
        if self.bid >= self.ask:
            raise ValueError("bid must be strictly less than ask")
        return self


class MarketMakingRunPayload(FrozenModel):
    """Immutable, domain-owned input for event-driven market-making research."""

    domain: Literal[Domain.MARKET_MAKING] = Domain.MARKET_MAKING
    events: tuple[MarketMakingEvent, ...] = Field(min_length=1)
    execution_policy: MarketMakingExecutionPolicy
    quote_size: Decimal = Field(gt=_ZERO)
    initial_cash: Decimal = Field(ge=_ZERO)
    initial_inventory: Decimal

    @field_validator("quote_size", "initial_cash", "initial_inventory")
    @classmethod
    def _finite_decimal(cls, value: Decimal) -> Decimal:
        if not value.is_finite():
            raise ValueError("market-making account values must be finite")
        return value

    @model_validator(mode="after")
    def _ordered_events_and_account(self) -> MarketMakingRunPayload:
        _strictly_increasing(
            tuple(event.timestamp for event in self.events),
            "market-making event timestamps",
        )
        if abs(self.initial_inventory) > self.execution_policy.inventory_limit:
            raise ValueError("initial inventory exceeds the explicit inventory limit")
        try:
            with deterministic_decimal_context():
                initial_midpoint = (self.events[0].bid + self.events[0].ask) / _TWO
                initial_equity = self.initial_cash + self.initial_inventory * initial_midpoint
        except DecimalException as error:
            raise ValueError("initial marked equity arithmetic failed") from error
        if initial_equity <= _ZERO:
            raise ValueError("initial marked equity must be positive")
        return self


class MarketMakingQuestionPayload(FrozenModel):
    """Strict domain request used to constrain Market Making strategy generation."""

    domain: Literal[Domain.MARKET_MAKING] = Domain.MARKET_MAKING
    research_id: str = Field(min_length=1)
    instrument_id: str = Field(min_length=1)
    quote_currency: str = Field(min_length=1)


class CompiledMarketMakingStrategy(FrozenModel):
    """Data-only quote parameters derived from the Market Making AST allowlist."""

    quote_size: Decimal = Field(gt=_ZERO)
    inventory_skew: Decimal = Field(ge=_ZERO)

    @field_validator("quote_size", "inventory_skew")
    @classmethod
    def _finite_decimal(cls, value: Decimal) -> Decimal:
        if not value.is_finite():
            raise ValueError("compiled market-making values must be finite")
        return value


class MarketMakingStrategyCompiler:
    """Compile the small declared quote DSL without executing arbitrary code."""

    _ALLOWED_OPERATOR_IDS = frozenset({"inventory_limited_quote"})
    _REQUIRED_PARAMETERS = frozenset({"quote_size", "inventory_skew"})

    @classmethod
    def compile(cls, strategy: StrategySpec) -> CompiledMarketMakingStrategy:
        if strategy.domain is not Domain.MARKET_MAKING or not isinstance(
            strategy.ast, MarketMakingStrategyAst
        ):
            raise _schema_error(
                "strategy", "Market Making compiler requires a MARKET_MAKING strategy"
            )
        root = strategy.ast.root
        if not isinstance(root, AstOperator):
            raise _schema_error("strategy.ast.root", "root must be an allowlisted operator")
        if root.operator_id not in cls._ALLOWED_OPERATOR_IDS:
            raise _schema_error("strategy.ast.root.operator_id", "operator is not allowlisted")
        if root.arguments:
            raise _schema_error(
                "strategy.ast.root.arguments", "quote operators do not accept arguments"
            )
        parameters = {parameter.name: parameter.value for parameter in root.parameters}
        if set(parameters) != cls._REQUIRED_PARAMETERS:
            raise _schema_error(
                "strategy.ast.root.parameters",
                "operator requires exactly quote_size and inventory_skew",
            )
        quote_size = parameters["quote_size"]
        inventory_skew = parameters["inventory_skew"]
        if not isinstance(quote_size, Decimal):
            raise _schema_error(
                "strategy.ast.root.parameters.quote_size", "quote_size must be a Decimal"
            )
        if not isinstance(inventory_skew, Decimal):
            raise _schema_error(
                "strategy.ast.root.parameters.inventory_skew",
                "inventory_skew must be a Decimal",
            )
        try:
            return CompiledMarketMakingStrategy(
                quote_size=quote_size,
                inventory_skew=inventory_skew,
            )
        except ValueError as error:
            raise _schema_error("strategy.ast.root.parameters", str(error)) from error


def compile_market_making_strategy(strategy: StrategySpec) -> CompiledMarketMakingStrategy:
    """Compile a MARKET_MAKING StrategySpec into a non-executable quote plan."""

    return MarketMakingStrategyCompiler.compile(strategy)




def _schema_error(path: str, reason: str) -> DomainError:
    return DomainError(
        ErrorDetail.for_code(
            ErrorCode.SCHEMA,
            "Market Making strategy compilation failed",
            (ErrorField(path=path, reason=reason),),
        )
    )
