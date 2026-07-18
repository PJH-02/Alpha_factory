"""Typed Cross Venue research inputs and the non-executable strategy compiler."""

from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal
from enum import StrEnum
from typing import Literal

from pydantic import Field, field_validator, model_validator

from alpha_foundry.domain.errors import DomainError, ErrorCode, ErrorDetail, ErrorField
from alpha_foundry.domain.models import (
    AstOperator,
    CrossVenueStrategyAst,
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


class SameTimestampPriority(StrEnum):
    """The explicitly pinned precedence for arrivals sharing one engine timestamp."""

    QUOTE_BEFORE_ORDER = "QUOTE_BEFORE_ORDER"
    ORDER_BEFORE_QUOTE = "ORDER_BEFORE_QUOTE"


class VenueClock(FrozenModel):
    """Clock conversion and market-data availability for one venue."""

    venue_id: str = Field(min_length=1)
    utc_offset_ms: int
    market_data_latency_ms: int = Field(ge=0)
    maximum_quote_age_ms: int = Field(ge=0)


class VenueCostPolicy(FrozenModel):
    """All-in taker costs charged by one venue in quote currency."""

    venue_id: str = Field(min_length=1)
    taker_fee_rate: Decimal = Field(ge=Decimal("0"))
    fixed_fee: Decimal = Field(ge=Decimal("0"))

    @field_validator("taker_fee_rate", "fixed_fee")
    @classmethod
    def _validate_finite_decimal(cls, value: Decimal) -> Decimal:
        if not value.is_finite():
            raise ValueError("venue costs must be finite")
        return value


class VenueFillPolicy(FrozenModel):
    """Deterministic displayed-liquidity fill rule for one venue."""

    venue_id: str = Field(min_length=1)
    participation_rate: Decimal = Field(gt=Decimal("0"), le=Decimal("1"))
    fill_ratio: Decimal = Field(ge=Decimal("0"), le=Decimal("1"))

    @field_validator("participation_rate", "fill_ratio")
    @classmethod
    def _validate_finite_decimal(cls, value: Decimal) -> Decimal:
        if not value.is_finite():
            raise ValueError("fill policy values must be finite")
        return value


class RoutePolicy(FrozenModel):
    """One executable buy/sell route with independently explicit leg latency."""

    route_id: str = Field(min_length=1)
    buy_venue_id: str = Field(min_length=1)
    sell_venue_id: str = Field(min_length=1)
    buy_order_latency_ms: int = Field(ge=0)
    sell_order_latency_ms: int = Field(ge=0)
    maximum_open_orders: int = Field(ge=1)

    @model_validator(mode="after")
    def _validate_distinct_venues(self) -> RoutePolicy:
        if self.buy_venue_id == self.sell_venue_id:
            raise ValueError("a cross-venue route must use distinct venues")
        return self


class CrossVenueExecutionPolicy(FrozenModel):
    """An identified immutable policy with complete Cross Venue execution semantics."""

    policy_id: Identifier
    version: Version
    content_hash: Digest
    venue_clocks: tuple[VenueClock, ...] = Field(min_length=2)
    venue_costs: tuple[VenueCostPolicy, ...] = Field(min_length=2)
    venue_fills: tuple[VenueFillPolicy, ...] = Field(min_length=2)
    routes: tuple[RoutePolicy, ...] = Field(min_length=1)
    same_timestamp_priority: SameTimestampPriority
    short_sales_allowed: bool

    @model_validator(mode="after")
    def _validate_complete_venue_policy(self) -> CrossVenueExecutionPolicy:
        clock_ids = tuple(clock.venue_id for clock in self.venue_clocks)
        cost_ids = tuple(cost.venue_id for cost in self.venue_costs)
        fill_ids = tuple(fill.venue_id for fill in self.venue_fills)
        route_ids = tuple(route.route_id for route in self.routes)
        _require_unique(clock_ids, "venue clock IDs")
        _require_unique(cost_ids, "venue cost IDs")
        _require_unique(fill_ids, "venue fill IDs")
        _require_unique(route_ids, "route IDs")
        clock_set = set(clock_ids)
        if set(cost_ids) != clock_set:
            raise ValueError("venue costs must cover exactly the configured venue clocks")
        if set(fill_ids) != clock_set:
            raise ValueError("venue fills must cover exactly the configured venue clocks")
        for route in self.routes:
            if route.buy_venue_id not in clock_set or route.sell_venue_id not in clock_set:
                raise ValueError("route venues must have clock, cost, and fill policies")
        return self


class VenueQuote(FrozenModel):
    """One venue-local top-of-book observation before the configured clock conversion."""

    venue_id: str = Field(min_length=1)
    observed_at: datetime
    bid: Decimal = Field(gt=Decimal("0"))
    ask: Decimal = Field(gt=Decimal("0"))
    bid_size: Decimal = Field(ge=Decimal("0"))
    ask_size: Decimal = Field(ge=Decimal("0"))

    @field_validator("observed_at")
    @classmethod
    def _normalize_observed_at(cls, value: datetime) -> datetime:
        return _utc_timestamp(value)

    @field_validator("bid", "ask", "bid_size", "ask_size")
    @classmethod
    def _validate_finite_decimal(cls, value: Decimal) -> Decimal:
        if not value.is_finite():
            raise ValueError("quotes must be finite")
        return value

    @model_validator(mode="after")
    def _validate_book(self) -> VenueQuote:
        if self.bid > self.ask:
            raise ValueError("quote bid must not exceed ask")
        return self


class VenueCashBalance(FrozenModel):
    venue_id: str = Field(min_length=1)
    cash: Decimal = Field(ge=Decimal("0"))

    @field_validator("cash")
    @classmethod
    def _validate_finite_decimal(cls, value: Decimal) -> Decimal:
        if not value.is_finite():
            raise ValueError("cash balances must be finite")
        return value


class VenuePosition(FrozenModel):
    venue_id: str = Field(min_length=1)
    quantity: Decimal

    @field_validator("quantity")
    @classmethod
    def _validate_finite_decimal(cls, value: Decimal) -> Decimal:
        if not value.is_finite():
            raise ValueError("positions must be finite")
        return value


class CrossVenueRunPayload(FrozenModel):
    """Validated immutable numerical input for :class:`CrossVenueEngine`."""

    domain: Literal[Domain.CROSS_VENUE] = Domain.CROSS_VENUE
    quotes: tuple[VenueQuote, ...] = Field(min_length=2)
    execution_policy: CrossVenueExecutionPolicy
    initial_cash: tuple[VenueCashBalance, ...] = Field(min_length=2)
    initial_positions: tuple[VenuePosition, ...] = Field(min_length=2)

    @model_validator(mode="after")
    def _validate_complete_payload(self) -> CrossVenueRunPayload:
        venue_ids = {clock.venue_id for clock in self.execution_policy.venue_clocks}
        cash_ids = tuple(balance.venue_id for balance in self.initial_cash)
        position_ids = tuple(position.venue_id for position in self.initial_positions)
        _require_unique(cash_ids, "initial cash venue IDs")
        _require_unique(position_ids, "initial position venue IDs")
        if set(cash_ids) != venue_ids:
            raise ValueError("initial cash must cover exactly the configured venues")
        if set(position_ids) != venue_ids:
            raise ValueError("initial positions must cover exactly the configured venues")
        quote_keys = tuple((quote.venue_id, quote.observed_at) for quote in self.quotes)
        _require_unique(quote_keys, "venue quote timestamps")
        if any(quote.venue_id not in venue_ids for quote in self.quotes):
            raise ValueError("quotes must reference configured venues")
        if {quote.venue_id for quote in self.quotes} != venue_ids:
            raise ValueError("quotes must include every configured venue")
        return self


class CrossVenueQuestionPayload(FrozenModel):
    """Strict domain request used to constrain Cross Venue strategy generation."""

    domain: Literal[Domain.CROSS_VENUE] = Domain.CROSS_VENUE
    research_id: str = Field(min_length=1)
    instrument_id: str = Field(min_length=1)
    venue_ids: tuple[str, ...] = Field(min_length=2)

    @model_validator(mode="after")
    def _validate_venues(self) -> CrossVenueQuestionPayload:
        _require_unique(self.venue_ids, "question venue IDs")
        return self


class CompiledCrossVenueStrategy(FrozenModel):
    """A data-only executable plan derived from an allowlisted AST."""

    route_id: str = Field(min_length=1)
    minimum_edge_rate: Decimal = Field(ge=Decimal("0"))
    maximum_quantity: Decimal = Field(gt=Decimal("0"))

    @field_validator("minimum_edge_rate", "maximum_quantity")
    @classmethod
    def _validate_finite_decimal(cls, value: Decimal) -> Decimal:
        if not value.is_finite():
            raise ValueError("compiled strategy numeric values must be finite")
        return value


class _CrossVenueStrategyCompiler:
    """Compiles only the small declared Cross Venue AST surface; it never executes code."""

    _ALLOWED_OPERATOR_IDS = frozenset({"cross_venue_spread"})
    _REQUIRED_PARAMETERS = frozenset({"route_id", "minimum_edge_rate", "maximum_quantity"})

    @classmethod
    def compile(cls, strategy: StrategySpec) -> CompiledCrossVenueStrategy:

        if strategy.domain is not Domain.CROSS_VENUE or not isinstance(
            strategy.ast, CrossVenueStrategyAst
        ):
            raise _schema_error("strategy", "Cross Venue compiler requires a CROSS_VENUE strategy")
        root = strategy.ast.root
        if not isinstance(root, AstOperator):
            raise _schema_error("strategy.ast.root", "root must be an allowlisted operator")
        if root.operator_id not in cls._ALLOWED_OPERATOR_IDS:
            raise _schema_error("strategy.ast.root.operator_id", "operator is not allowlisted")
        if root.arguments:
            raise _schema_error(
                "strategy.ast.root.arguments", "spread operators do not accept arguments"
            )
        parameters = {parameter.name: parameter.value for parameter in root.parameters}
        if set(parameters) != cls._REQUIRED_PARAMETERS:
            raise _schema_error(
                "strategy.ast.root.parameters",
                "operator requires exactly route_id, minimum_edge_rate, and maximum_quantity",
            )
        route_id = parameters["route_id"]
        minimum_edge_rate = parameters["minimum_edge_rate"]
        maximum_quantity = parameters["maximum_quantity"]
        if not isinstance(route_id, str) or not route_id:
            raise _schema_error(
                "strategy.ast.root.parameters.route_id", "route_id must be a non-empty string"
            )
        if isinstance(minimum_edge_rate, bool) or not isinstance(minimum_edge_rate, Decimal):
            raise _schema_error(
                "strategy.ast.root.parameters.minimum_edge_rate",
                "minimum_edge_rate must be a Decimal",
            )
        if isinstance(maximum_quantity, bool) or not isinstance(maximum_quantity, Decimal):
            raise _schema_error(
                "strategy.ast.root.parameters.maximum_quantity",
                "maximum_quantity must be a Decimal",
            )
        try:
            return CompiledCrossVenueStrategy(
                route_id=route_id,
                minimum_edge_rate=minimum_edge_rate,
                maximum_quantity=maximum_quantity,
            )
        except ValueError as error:
            raise _schema_error("strategy.ast.root.parameters", str(error)) from error




def _schema_error(path: str, reason: str) -> DomainError:
    return DomainError(
        ErrorDetail.for_code(
            ErrorCode.SCHEMA,
            "Cross Venue strategy compilation failed",
            (ErrorField(path=path, reason=reason),),
        )
    )


def quote_available_at(quote: VenueQuote, clock: VenueClock) -> datetime:
    """Return the first UTC instant at which a quote is observable to the engine."""

    return quote.observed_at + timedelta(
        milliseconds=clock.utc_offset_ms + clock.market_data_latency_ms
    )
