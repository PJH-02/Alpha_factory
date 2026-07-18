"""Deterministic event-driven market-making research simulation.

This is a deliberately transparent fill approximation, not a production exchange or
queue-priority simulator.  Quotes are delayed by an explicit count of book events,
and fills are constrained by observed aggressive flow, the configured fill ratio,
and the inventory limit.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from alpha_foundry.domain.errors import ErrorCode
from alpha_foundry.domain.models import Domain, ExperimentConfig, ExperimentResult

from .base import (
    BaseEngine,
    EngineInputError,
    Timestamp,
    array_items,
    cost_components,
    decimal_value,
    deterministic_decimal_context,
    max_drawdown,
    metric_path,
    normalize_timestamp,
    ordered_timestamps,
    require_field,
    require_mapping,
    require_policy_rates,
)

_ZERO = Decimal("0")
_ONE = Decimal("1")
_TWO = Decimal("2")


def _timestamp_value(value: object, path: str) -> Timestamp:
    return normalize_timestamp(value, path)


@dataclass(frozen=True)
class _BookEvent:
    timestamp: Timestamp
    bid: Decimal
    ask: Decimal
    bid_size: Decimal
    ask_size: Decimal
    buy_volume: Decimal
    sell_volume: Decimal

    @property
    def midpoint(self) -> Decimal:
        return (self.bid + self.ask) / _TWO


@dataclass(frozen=True)
class _MarketMakingInput:
    events: tuple[_BookEvent, ...]
    latency_events: int
    fill_ratio: Decimal
    quote_spread: Decimal
    inventory_limit: Decimal
    allow_negative_cash: bool
    quote_size: Decimal
    initial_cash: Decimal
    initial_inventory: Decimal
    rates: Mapping[str, Decimal]
    adverse_selection_rate: Decimal


class MarketMakingEngine(BaseEngine):
    """Quote a delayed symmetric spread against timestamp-ordered top-of-book events.

    ``data`` requires an ``events`` array.  Each event has ``timestamp``, ``bid``,
    ``ask``, ``bid_size``, ``ask_size``, ``buy_volume``, and ``sell_volume`` fields.
    ``buy_volume`` is aggressive buy flow that may lift our ask; ``sell_volume`` is
    aggressive sell flow that may hit our bid.  The explicit ``policy`` mapping
    requires a positive ``latency_events`` observation-before-order delay,
    ``fill_ratio``, ``quote_spread``, ``inventory_limit``, ``allow_negative_cash``,
    four rate fields, and ``adverse_selection_rate``. Delayed quotes that cross the
    current book are cancelled. Account inputs are ``quote_size``, ``initial_cash``,
    and ``initial_inventory``.
    """

    domain = Domain.MARKET_MAKING
    engine_id = "market-making-event-v1"

    def run(self, config: ExperimentConfig, data: Mapping[str, Any]) -> ExperimentResult:
        try:
            self.validate_config(config)
            with deterministic_decimal_context():
                return self._simulate(config, self._validate_data(data))
        except EngineInputError as error:
            return self.failed(config, error)

    def _validate_data(self, data: Mapping[str, Any]) -> _MarketMakingInput:
        input_data = require_mapping(data, "data")
        raw_events = array_items(require_field(input_data, "events"), "events")
        if not raw_events:
            raise EngineInputError(ErrorCode.SCHEMA, "events", "must not be empty")

        events: list[_BookEvent] = []
        for index, raw_event in enumerate(raw_events):
            event = require_mapping(raw_event, f"events[{index}]")
            bid = decimal_value(require_field(event, "bid"), f"events[{index}].bid")
            ask = decimal_value(require_field(event, "ask"), f"events[{index}].ask")
            if bid <= _ZERO or ask <= _ZERO:
                raise EngineInputError(
                    ErrorCode.SCHEMA, f"events[{index}]", "prices must be positive"
                )
            if bid >= ask:
                raise EngineInputError(
                    ErrorCode.SCHEMA,
                    f"events[{index}]",
                    "bid must be strictly less than ask",
                )
            bid_size = _nonnegative_decimal(event, "bid_size", f"events[{index}].bid_size")
            ask_size = _nonnegative_decimal(event, "ask_size", f"events[{index}].ask_size")
            buy_volume = _nonnegative_decimal(event, "buy_volume", f"events[{index}].buy_volume")
            sell_volume = _nonnegative_decimal(event, "sell_volume", f"events[{index}].sell_volume")
            events.append(
                _BookEvent(
                    timestamp=_timestamp_value(
                        require_field(event, "timestamp"),
                        f"events[{index}].timestamp",
                    ),
                    bid=bid,
                    ask=ask,
                    bid_size=bid_size,
                    ask_size=ask_size,
                    buy_volume=buy_volume,
                    sell_volume=sell_volume,
                )
            )
        ordered_timestamps(tuple(event.timestamp for event in events), "events.timestamp")

        policy = require_mapping(
            require_field(input_data, "policy", ErrorCode.POLICY), "policy", ErrorCode.POLICY
        )
        latency_events = _positive_int(policy, "latency_events", "policy.latency_events")
        fill_ratio = _policy_decimal(policy, "fill_ratio", "policy.fill_ratio")
        if fill_ratio > _ONE:
            raise EngineInputError(ErrorCode.POLICY, "policy.fill_ratio", "must not exceed one")
        quote_spread = _policy_decimal(policy, "quote_spread", "policy.quote_spread")
        if quote_spread <= _ZERO:
            raise EngineInputError(ErrorCode.POLICY, "policy.quote_spread", "must be positive")
        inventory_limit = _policy_decimal(policy, "inventory_limit", "policy.inventory_limit")
        if inventory_limit <= _ZERO:
            raise EngineInputError(ErrorCode.POLICY, "policy.inventory_limit", "must be positive")
        allow_negative_cash = require_field(policy, "allow_negative_cash", ErrorCode.POLICY)
        if not isinstance(allow_negative_cash, bool):
            raise EngineInputError(
                ErrorCode.POLICY,
                "policy.allow_negative_cash",
                "must be a boolean",
            )
        rates = require_policy_rates(
            input_data,
            ("commission_rate", "slippage_rate", "impact_rate"),
        )
        adverse_selection_rate = _policy_decimal(
            policy,
            "adverse_selection_rate",
            "policy.adverse_selection_rate",
        )

        quote_size = decimal_value(require_field(input_data, "quote_size"), "quote_size")
        if quote_size <= _ZERO:
            raise EngineInputError(ErrorCode.SCHEMA, "quote_size", "must be positive")
        initial_cash = decimal_value(require_field(input_data, "initial_cash"), "initial_cash")
        if initial_cash < _ZERO:
            raise EngineInputError(ErrorCode.SCHEMA, "initial_cash", "must be non-negative")
        initial_inventory = decimal_value(
            require_field(input_data, "initial_inventory"), "initial_inventory"
        )
        if abs(initial_inventory) > inventory_limit:
            raise EngineInputError(
                ErrorCode.SCHEMA,
                "initial_inventory",
                "must not exceed the explicit inventory limit",
            )
        if initial_cash + initial_inventory * events[0].midpoint <= _ZERO:
            raise EngineInputError(
                ErrorCode.VALIDATION,
                "accounting.initial_equity",
                "must be positive",
            )
        return _MarketMakingInput(
            events=tuple(events),
            latency_events=latency_events,
            fill_ratio=fill_ratio,
            quote_spread=quote_spread,
            inventory_limit=inventory_limit,
            allow_negative_cash=allow_negative_cash,
            quote_size=quote_size,
            initial_cash=initial_cash,
            initial_inventory=initial_inventory,
            rates=rates,
            adverse_selection_rate=adverse_selection_rate,
        )

    def _simulate(
        self, config: ExperimentConfig, input_data: _MarketMakingInput
    ) -> ExperimentResult:
        cash = input_data.initial_cash
        inventory = input_data.initial_inventory
        previous_equity = cash + inventory * input_data.events[0].midpoint
        half_spread = input_data.quote_spread / _TWO

        net_returns: list[Decimal] = []
        equity: list[Decimal] = []
        cash_path: list[Decimal] = []
        inventory_path: list[Decimal] = []
        bid_quotes: list[Decimal] = []
        ask_quotes: list[Decimal] = []
        bid_fills: list[Decimal] = []
        ask_fills: list[Decimal] = []
        commissions: list[Decimal] = []
        slippages: list[Decimal] = []
        impacts: list[Decimal] = []
        adverse_costs: list[Decimal] = []
        turnovers: list[Decimal] = []

        for index, event in enumerate(input_data.events):
            source_index = index - input_data.latency_events
            bid_quote = _ZERO
            ask_quote = _ZERO
            bid_fill = _ZERO
            ask_fill = _ZERO
            if source_index >= 0:
                reference_midpoint = input_data.events[source_index].midpoint
                bid_quote = reference_midpoint - half_spread
                ask_quote = reference_midpoint + half_spread
                if bid_quote <= _ZERO:
                    raise EngineInputError(
                        ErrorCode.POLICY,
                        "policy.quote_spread",
                        "creates a non-positive bid quote for the supplied book events",
                    )
                if bid_quote >= event.ask:
                    bid_quote = _ZERO
                if ask_quote <= event.bid:
                    ask_quote = _ZERO
                if bid_quote > _ZERO:
                    bid_capacity = input_data.inventory_limit - inventory
                    bid_quantity = min(input_data.quote_size, max(_ZERO, bid_capacity))
                    if bid_quote >= event.bid:
                        bid_fill = min(
                            bid_quantity,
                            min(event.sell_volume, event.bid_size) * input_data.fill_ratio,
                        )
                if ask_quote > _ZERO:
                    ask_capacity = input_data.inventory_limit + inventory
                    ask_quantity = min(input_data.quote_size, max(_ZERO, ask_capacity))
                    if ask_quote <= event.ask:
                        ask_fill = min(
                            ask_quantity,
                            min(event.buy_volume, event.ask_size) * input_data.fill_ratio,
                        )
                if bid_fill > _ZERO and not input_data.allow_negative_cash:
                    bid_fill = min(
                        bid_fill,
                        _cash_constrained_bid_capacity(
                            cash,
                            bid_quote,
                            ask_fill * ask_quote,
                            input_data.rates,
                            input_data.adverse_selection_rate,
                        ),
                    )
            turnover = bid_fill * bid_quote + ask_fill * ask_quote
            commission, slippage, impact = cost_components(turnover, input_data.rates)
            adverse_cost = turnover * input_data.adverse_selection_rate
            total_cost = commission + slippage + impact + adverse_cost
            cash = cash - bid_fill * bid_quote + ask_fill * ask_quote - total_cost
            inventory = inventory + bid_fill - ask_fill
            if abs(inventory) > input_data.inventory_limit:
                raise EngineInputError(
                    ErrorCode.VALIDATION,
                    f"accounting.inventory[{index}]",
                    "exceeds the explicit inventory limit",
                )
            if not input_data.allow_negative_cash and cash < _ZERO:
                raise EngineInputError(
                    ErrorCode.VALIDATION,
                    f"accounting.cash[{index}]",
                    "became negative under the explicit cash policy",
                )
            current_equity = cash + inventory * event.midpoint
            if current_equity <= _ZERO:
                raise EngineInputError(
                    ErrorCode.VALIDATION,
                    f"accounting.equity[{index}]",
                    "must remain positive",
                )
            net_return = current_equity / previous_equity - _ONE

            net_returns.append(net_return)
            equity.append(current_equity)
            cash_path.append(cash)
            inventory_path.append(inventory)
            bid_quotes.append(bid_quote)
            ask_quotes.append(ask_quote)
            bid_fills.append(bid_fill)
            ask_fills.append(ask_fill)
            commissions.append(commission)
            slippages.append(slippage)
            impacts.append(impact)
            adverse_costs.append(adverse_cost)
            turnovers.append(turnover)
            previous_equity = current_equity

        total_commission = sum(commissions, _ZERO)
        total_slippage = sum(slippages, _ZERO)
        total_impact = sum(impacts, _ZERO)
        total_adverse_cost = sum(adverse_costs, _ZERO)
        total_cost = total_commission + total_slippage + total_impact + total_adverse_cost
        initial_equity = (
            input_data.initial_cash + input_data.initial_inventory * input_data.events[0].midpoint
        )
        metrics = (
            *metric_path("net_return", net_returns),
            *metric_path("equity", equity),
            *metric_path("cash", cash_path),
            *metric_path("inventory", inventory_path),
            *metric_path("bid_fill", bid_fills),
            *metric_path("ask_fill", ask_fills),
            ("event_count", Decimal(len(input_data.events))),
            ("total_turnover", sum(turnovers, _ZERO)),
            ("total_bid_fill", sum(bid_fills, _ZERO)),
            ("total_ask_fill", sum(ask_fills, _ZERO)),
            ("total_commission", total_commission),
            ("total_slippage", total_slippage),
            ("total_impact", total_impact),
            ("total_adverse_selection_cost", total_adverse_cost),
            ("total_cost", total_cost),
            ("final_equity", equity[-1]),
            ("cumulative_net_return", equity[-1] / initial_equity - _ONE),
            ("max_drawdown", max_drawdown((initial_equity, *equity))),
        )
        artifact = {
            "ask_fill": tuple(ask_fills),
            "ask_quote": tuple(ask_quotes),
            "bid_fill": tuple(bid_fills),
            "bid_quote": tuple(bid_quotes),
            "cash": tuple(cash_path),
            "commission": tuple(commissions),
            "equity": tuple(equity),
            "impact": tuple(impacts),
            "inventory": tuple(inventory_path),
            "net_return": tuple(net_returns),
            "slippage": tuple(slippages),
            "timestamps": tuple(event.timestamp for event in input_data.events),
            "turnover": tuple(turnovers),
            "adverse_selection_cost": tuple(adverse_costs),
        }
        return self.succeeded(config, metrics, artifact)


def _nonnegative_decimal(data: Mapping[str, Any], name: str, path: str) -> Decimal:
    value = decimal_value(require_field(data, name), path)
    if value < _ZERO:
        raise EngineInputError(ErrorCode.SCHEMA, path, "must be non-negative")
    return value


def _policy_decimal(data: Mapping[str, Any], name: str, path: str) -> Decimal:
    value = require_field(data, name, ErrorCode.POLICY)
    if not isinstance(value, Decimal) or not value.is_finite() or value < _ZERO:
        raise EngineInputError(
            ErrorCode.POLICY,
            path,
            "must be a finite, non-negative Decimal",
        )
    return value


def _positive_int(data: Mapping[str, Any], name: str, path: str) -> int:
    value = require_field(data, name, ErrorCode.POLICY)
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise EngineInputError(ErrorCode.POLICY, path, "must be a positive integer")
    return value


def _cash_constrained_bid_capacity(
    cash: Decimal,
    bid_quote: Decimal,
    ask_turnover: Decimal,
    rates: Mapping[str, Decimal],
    adverse_selection_rate: Decimal,
) -> Decimal:
    """Return bid quantity affordable after same-event ask proceeds and combined costs."""

    linear_multiplier = (
        _ONE + rates["commission_rate"] + rates["slippage_rate"] + adverse_selection_rate
    )
    impact_rate = rates["impact_rate"]
    budget = cash + ask_turnover
    if impact_rate == _ZERO:
        affordable_total_turnover = budget / linear_multiplier
    else:
        discriminant = linear_multiplier * linear_multiplier + Decimal("4") * impact_rate * budget
        affordable_total_turnover = (discriminant.sqrt() - linear_multiplier) / (_TWO * impact_rate)
        if (
            affordable_total_turnover * linear_multiplier
            + affordable_total_turnover * affordable_total_turnover * impact_rate
            > budget
        ):
            affordable_total_turnover = budget / (
                linear_multiplier + affordable_total_turnover * impact_rate
            )
    affordable_bid_turnover = max(_ZERO, affordable_total_turnover - ask_turnover)
    return affordable_bid_turnover / bid_quote
