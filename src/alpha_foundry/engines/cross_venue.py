"""Deterministic, clock-aware Cross Venue numerical execution engine."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal, DecimalException
from typing import Any, Final

from pydantic import ValidationError

from alpha_foundry.domain.errors import DomainError, ErrorCode
from alpha_foundry.domain.models import Domain, ExperimentConfig, ExperimentResult
from alpha_foundry.labs.cross_venue import (
    CompiledCrossVenueStrategy,
    CrossVenueExecutionPolicy,
    CrossVenueRunPayload,
    RoutePolicy,
    SameTimestampPriority,
    VenueQuote,
    quote_available_at,
)
from alpha_foundry.labs.registry import DEFAULT_LAB_REGISTRY

from .base import (
    BaseEngine,
    EngineInputError,
    deterministic_decimal_context,
    max_drawdown,
    payload_validation_error,
)

_ZERO: Final = Decimal("0")
_ONE: Final = Decimal("1")


@dataclass(frozen=True)
class _QuoteArrival:
    available_at: datetime
    venue_id: str
    quote: VenueQuote


@dataclass(frozen=True)
class _LegOrder:
    arrival_at: datetime
    sequence: int
    trade_id: int
    route_id: str
    side: str
    venue_id: str
    requested_quantity: Decimal
    reserved_cash: Decimal
    reserved_quantity: Decimal


class CrossVenueEngine(BaseEngine):
    """Execute a declared spread route without using quotes unavailable at each decision."""

    domain = Domain.CROSS_VENUE
    engine_id = "cross-venue-clock-v1"

    def run(self, config: ExperimentConfig, data: Mapping[str, Any]) -> ExperimentResult:
        """Run one immutable Cross Venue experiment and return deterministic Decimal metrics.

        Quote visibility is derived only from each venue's configured clock and market-data
        latency. Orders are scheduled after a decision and execute only against quotes
        observable at their independent leg-arrival timestamps.
        """

        try:
            self.validate_config(config)
            with deterministic_decimal_context():
                compiled = DEFAULT_LAB_REGISTRY.compile(config.strategy)
                if not isinstance(compiled, CompiledCrossVenueStrategy):
                    raise EngineInputError(
                        ErrorCode.SCHEMA,
                        "strategy",
                        "Cross Venue registry returned an invalid compiled plan",
                    )
                strategy = compiled
                payload = _parse_payload(data)
                policy = payload.execution_policy
                self.bind_runtime_policy(
                    config,
                    policy_id=policy.policy_id,
                    version=policy.version,
                    content_hash=policy.content_hash,
                )
                route = _route_for_id(policy, strategy.route_id)
                state = _CrossVenueState(self, payload, policy, strategy, route)
                return state.execute(config)
        except EngineInputError as error:
            return self.failed(config, error)
        except DomainError as error:
            return self.failed(config, _engine_input_error(error))
        except DecimalException:
            return self.failed(
                config,
                EngineInputError(
                    ErrorCode.VALIDATION,
                    "accounting.decimal128",
                    "Decimal128 arithmetic failed",
                ),
            )


class _CrossVenueState:
    def __init__(
        self,
        engine: CrossVenueEngine,
        payload: CrossVenueRunPayload,
        policy: CrossVenueExecutionPolicy,
        strategy: CompiledCrossVenueStrategy,
        route: RoutePolicy,
    ) -> None:
        self._engine = engine
        self._payload = payload
        self._policy = policy
        self._strategy = strategy
        self._route = route
        self._clocks = {clock.venue_id: clock for clock in policy.venue_clocks}
        self._costs = {cost.venue_id: cost for cost in policy.venue_costs}
        self._fills = {fill.venue_id: fill for fill in policy.venue_fills}
        self._cash = {balance.venue_id: balance.cash for balance in payload.initial_cash}
        self._positions = {
            position.venue_id: position.quantity for position in payload.initial_positions
        }
        self._reserved_cash = dict.fromkeys(self._cash, _ZERO)
        self._reserved_positions = dict.fromkeys(self._positions, _ZERO)
        self._latest_quotes: dict[str, _QuoteArrival] = {}
        self._pending_by_time: dict[datetime, list[_LegOrder]] = {}
        self._open_routes: dict[str, int] = {item.route_id: 0 for item in policy.routes}
        self._remaining_legs: dict[int, int] = {}
        self._filled_by_trade: dict[int, dict[str, Decimal]] = {}
        self._next_sequence = 0
        self._next_trade_id = 0
        self._decision_count = 0
        self._order_count = 0
        self._gross_notional = _ZERO
        self._fees = _ZERO
        self._filled_buy_quantity = _ZERO
        self._filled_sell_quantity = _ZERO
        self._equity_path: list[Decimal] = []
        self._return_path: list[Decimal] = []
        self._initial_equity: Decimal | None = None
        self._started_at: datetime | None = None
        self._finished_at: datetime | None = None
        self._last_equity_at: datetime | None = None

    def execute(self, config: ExperimentConfig) -> ExperimentResult:
        arrivals = self._quote_arrivals()
        arrivals_by_time: dict[datetime, list[_QuoteArrival]] = {}
        for arrival in arrivals:
            arrivals_by_time.setdefault(arrival.available_at, []).append(arrival)
        pending_times = set(arrivals_by_time)

        while pending_times:
            timestamp = min(pending_times)
            pending_times.remove(timestamp)
            self._advance_run_boundary(timestamp)
            quotes = sorted(
                arrivals_by_time.pop(timestamp, []),
                key=lambda item: (item.venue_id.encode("utf-8"), item.quote.observed_at),
            )
            orders = sorted(
                self._pending_by_time.pop(timestamp, []),
                key=lambda item: item.sequence,
            )
            match self._policy.same_timestamp_priority:
                case SameTimestampPriority.QUOTE_BEFORE_ORDER:
                    self._apply_quotes(quotes)
                    initial_recorded = self._record_initial_equity(timestamp)
                    self._execute_orders(orders)
                case SameTimestampPriority.ORDER_BEFORE_QUOTE:
                    self._execute_orders(orders)
                    self._apply_quotes(quotes)
                    initial_recorded = self._record_initial_equity(timestamp)
                case _:
                    raise _policy_error(
                        "execution_policy.same_timestamp_priority", "is unsupported"
                    )

            immediate_orders = self._submit_route_if_eligible(timestamp) if quotes else []
            if immediate_orders:
                self._execute_orders(immediate_orders)

            recorded = True
            if not initial_recorded or orders or immediate_orders:
                recorded = self._record_equity(timestamp)
            if (orders or immediate_orders) and not recorded:
                raise _resource_error(
                    "data.quotes",
                    "fresh complete valuation quotes are required after order execution",
                )
            pending_times.update(self._pending_by_time)

        if self._initial_equity is None or not self._equity_path:
            raise _resource_error("data.quotes", "quotes never produced a complete mark-to-market")
        if self._finished_at is None or self._started_at is None:
            raise _resource_error("data.quotes", "quote clock produced no engine timestamps")
        if self._last_equity_at != self._finished_at:
            raise _resource_error(
                "data.quotes", "final equity requires fresh complete valuation quotes"
            )
        final_equity = self._equity_path[-1]
        return self._engine.succeeded(
            config,
            self._result_metrics(final_equity),
            {
                "policy": {
                    "policy_id": self._policy.policy_id,
                    "version": self._policy.version,
                    "content_hash": self._policy.content_hash,
                },
                "run_boundaries": {
                    "started_at": self._started_at,
                    "finished_at": self._finished_at,
                },
            },
            input_snapshot=self._payload.model_dump(mode="json"),
            policy_identity={
                "policy_id": self._policy.policy_id,
                "version": self._policy.version,
                "content_hash": self._policy.content_hash,
            },
            started_at=self._started_at,
            finished_at=self._finished_at,
        )

    def _quote_arrivals(self) -> tuple[_QuoteArrival, ...]:
        arrivals = tuple(
            _QuoteArrival(
                available_at=quote_available_at(quote, self._clocks[quote.venue_id]),
                venue_id=quote.venue_id,
                quote=quote,
            )
            for quote in self._payload.quotes
        )
        if not arrivals:
            raise _resource_error("data.quotes", "at least one quote is required")
        return arrivals

    def _apply_quotes(self, quotes: list[_QuoteArrival]) -> None:
        for quote in quotes:
            self._latest_quotes[quote.venue_id] = quote

    def _execute_orders(self, orders: list[_LegOrder]) -> None:
        for order in orders:
            self._execute_order(order)

    def _execute_order(self, order: _LegOrder) -> None:
        if order.side == "BUY":
            self._reserved_cash[order.venue_id] -= order.reserved_cash
        else:
            self._reserved_positions[order.venue_id] -= order.reserved_quantity
        self._assert_reservations()

        quantity = _ZERO
        quote_arrival = self._latest_quotes.get(order.venue_id)
        if quote_arrival is not None and self._quote_is_fresh(order.arrival_at, quote_arrival):
            if order.side == "BUY":
                quantity = self._fill_buy(order, quote_arrival.quote)
            else:
                quantity = self._fill_sell(order, quote_arrival.quote)
        self._complete_leg(order, quantity)

    def _fill_buy(self, order: _LegOrder, quote: VenueQuote) -> Decimal:
        fill_policy = self._fills[order.venue_id]
        cost = self._costs[order.venue_id]
        displayed_quantity = quote.ask_size * fill_policy.participation_rate
        quantity = min(order.requested_quantity, displayed_quantity) * fill_policy.fill_ratio
        if quantity <= _ZERO:
            return _ZERO
        unit_cash = quote.ask * (_ONE + cost.taker_fee_rate)
        available_cash = self._cash[order.venue_id] - self._reserved_cash[order.venue_id]
        if available_cash <= cost.fixed_fee:
            return _ZERO
        quantity = min(quantity, (available_cash - cost.fixed_fee) / unit_cash)
        if quantity <= _ZERO:
            return _ZERO
        notional = quantity * quote.ask
        fee = notional * cost.taker_fee_rate + cost.fixed_fee
        cash_flow = notional + fee
        if cash_flow > available_cash:
            raise _policy_error(
                "data.execution_policy", "buy fill exceeded available reserved cash"
            )
        self._cash[order.venue_id] -= cash_flow
        self._positions[order.venue_id] += quantity
        self._gross_notional += notional
        self._fees += fee
        self._filled_buy_quantity += quantity
        self._assert_nonnegative_cash()
        return quantity

    def _fill_sell(self, order: _LegOrder, quote: VenueQuote) -> Decimal:
        fill_policy = self._fills[order.venue_id]
        cost = self._costs[order.venue_id]
        displayed_quantity = quote.bid_size * fill_policy.participation_rate
        quantity = min(order.requested_quantity, displayed_quantity) * fill_policy.fill_ratio
        if not self._policy.short_sales_allowed:
            available_position = (
                self._positions[order.venue_id] - self._reserved_positions[order.venue_id]
            )
            quantity = min(quantity, max(available_position, _ZERO))
        if quantity <= _ZERO:
            return _ZERO
        notional = quantity * quote.bid
        fee = notional * cost.taker_fee_rate + cost.fixed_fee
        if self._cash[order.venue_id] + notional - fee < _ZERO:
            return _ZERO
        self._cash[order.venue_id] += notional - fee
        self._positions[order.venue_id] -= quantity
        self._gross_notional += notional
        self._fees += fee
        self._filled_sell_quantity += quantity
        self._assert_nonnegative_cash()
        return quantity

    def _submit_route_if_eligible(self, timestamp: datetime) -> list[_LegOrder]:
        if self._initial_equity is None:
            return []
        if self._open_routes[self._route.route_id] >= self._route.maximum_open_orders:
            return []
        buy_arrival = self._latest_quotes.get(self._route.buy_venue_id)
        sell_arrival = self._latest_quotes.get(self._route.sell_venue_id)
        if buy_arrival is None or sell_arrival is None:
            return []
        if not self._quote_is_fresh(timestamp, buy_arrival) or not self._quote_is_fresh(
            timestamp, sell_arrival
        ):
            return []
        buy_quote = buy_arrival.quote
        sell_quote = sell_arrival.quote
        matched_quantity = self._matched_quantity(buy_quote, sell_quote)
        if matched_quantity <= _ZERO:
            return []
        buy_cost = self._costs[self._route.buy_venue_id]
        buy_fill = self._fills[self._route.buy_venue_id]
        sell_fill = self._fills[self._route.sell_venue_id]
        buy_available = (
            self._cash[self._route.buy_venue_id] - self._reserved_cash[self._route.buy_venue_id]
        )
        if buy_available <= buy_cost.fixed_fee:
            return []
        matched_quantity = min(
            matched_quantity,
            (buy_available - buy_cost.fixed_fee)
            / (buy_quote.ask * (_ONE + buy_cost.taker_fee_rate)),
        )
        if not self._policy.short_sales_allowed:
            sell_available = (
                self._positions[self._route.sell_venue_id]
                - self._reserved_positions[self._route.sell_venue_id]
            )
            matched_quantity = min(matched_quantity, max(sell_available, _ZERO))
        if matched_quantity <= _ZERO:
            return []
        if not self._edge_is_eligible(buy_quote, sell_quote, matched_quantity):
            return []
        reserved_cash = (
            matched_quantity * buy_quote.ask * (_ONE + buy_cost.taker_fee_rate) + buy_cost.fixed_fee
        )
        self._reserved_cash[self._route.buy_venue_id] += reserved_cash
        if not self._policy.short_sales_allowed:
            self._reserved_positions[self._route.sell_venue_id] += matched_quantity
        self._assert_reservations()

        trade_id = self._next_trade_id
        self._next_trade_id += 1
        self._open_routes[self._route.route_id] += 1
        self._remaining_legs[trade_id] = 2
        self._filled_by_trade[trade_id] = {}
        buy_order = self._new_order(
            timestamp,
            trade_id,
            "BUY",
            self._route.buy_venue_id,
            matched_quantity / buy_fill.fill_ratio,
            reserved_cash,
            _ZERO,
            self._route.buy_order_latency_ms,
        )
        sell_order = self._new_order(
            timestamp,
            trade_id,
            "SELL",
            self._route.sell_venue_id,
            matched_quantity / sell_fill.fill_ratio,
            _ZERO,
            matched_quantity if not self._policy.short_sales_allowed else _ZERO,
            self._route.sell_order_latency_ms,
        )
        self._decision_count += 1
        self._order_count += 2
        immediate: list[_LegOrder] = []
        for order in (buy_order, sell_order):
            if order.arrival_at == timestamp:
                immediate.append(order)
            else:
                self._pending_by_time.setdefault(order.arrival_at, []).append(order)
        return sorted(immediate, key=lambda item: item.sequence)

    def _new_order(
        self,
        timestamp: datetime,
        trade_id: int,
        side: str,
        venue_id: str,
        quantity: Decimal,
        reserved_cash: Decimal,
        reserved_quantity: Decimal,
        latency_ms: int,
    ) -> _LegOrder:
        sequence = self._next_sequence
        self._next_sequence += 1
        return _LegOrder(
            arrival_at=timestamp + _milliseconds(latency_ms),
            sequence=sequence,
            trade_id=trade_id,
            route_id=self._route.route_id,
            side=side,
            venue_id=venue_id,
            requested_quantity=quantity,
            reserved_cash=reserved_cash,
            reserved_quantity=reserved_quantity,
        )

    def _complete_leg(self, order: _LegOrder, filled_quantity: Decimal) -> None:
        fills = self._filled_by_trade.get(order.trade_id)
        if fills is None or order.side in fills:
            raise _policy_error("engine.orders", "trade fill accounting is inconsistent")
        fills[order.side] = filled_quantity
        remaining = self._remaining_legs[order.trade_id] - 1
        if remaining < 0:
            raise _policy_error("engine.orders", "trade leg completion underflow")
        if remaining == 0:
            buy_quantity = fills.get("BUY")
            sell_quantity = fills.get("SELL")
            if buy_quantity is None or sell_quantity is None or buy_quantity != sell_quantity:
                raise _policy_error(
                    "engine.residual_risk",
                    "cross-venue route legs must fill the same matched quantity",
                )
            del self._remaining_legs[order.trade_id]
            del self._filled_by_trade[order.trade_id]
            self._open_routes[order.route_id] -= 1
            if self._open_routes[order.route_id] < 0:
                raise _policy_error("engine.orders", "open route count underflow")
        else:
            self._remaining_legs[order.trade_id] = remaining

    def _edge_is_eligible(
        self,
        buy_quote: VenueQuote,
        sell_quote: VenueQuote,
        matched_quantity: Decimal,
    ) -> bool:
        if matched_quantity <= _ZERO:
            return False
        buy_cost = self._costs[self._route.buy_venue_id]
        sell_cost = self._costs[self._route.sell_venue_id]
        net_proceeds = (
            matched_quantity * sell_quote.bid * (_ONE - sell_cost.taker_fee_rate)
            - sell_cost.fixed_fee
        )
        net_cost = (
            matched_quantity * buy_quote.ask * (_ONE + buy_cost.taker_fee_rate) + buy_cost.fixed_fee
        )
        net_edge = (net_proceeds - net_cost) / (matched_quantity * buy_quote.ask)
        return net_edge >= self._strategy.minimum_edge_rate

    def _matched_quantity(self, buy_quote: VenueQuote, sell_quote: VenueQuote) -> Decimal:
        buy_fill = self._fills[self._route.buy_venue_id]
        sell_fill = self._fills[self._route.sell_venue_id]
        return min(
            self._strategy.maximum_quantity,
            buy_quote.ask_size * buy_fill.participation_rate * buy_fill.fill_ratio,
            sell_quote.bid_size * sell_fill.participation_rate * sell_fill.fill_ratio,
        )

    def _quote_is_fresh(self, timestamp: datetime, arrival: _QuoteArrival) -> bool:
        clock = self._clocks[arrival.venue_id]
        age = timestamp - arrival.available_at
        return age >= _milliseconds(0) and age <= _milliseconds(clock.maximum_quote_age_ms)

    def _advance_run_boundary(self, timestamp: datetime) -> None:
        if self._started_at is None:
            self._started_at = timestamp
        self._finished_at = timestamp

    def _record_initial_equity(self, timestamp: datetime) -> bool:
        if self._initial_equity is not None:
            return False
        return self._record_equity(timestamp)

    def _record_equity(self, timestamp: datetime) -> bool:
        equity = self._try_equity(timestamp)
        if equity is None:
            return False
        if equity <= _ZERO:
            raise EngineInputError(
                ErrorCode.VALIDATION,
                "accounting.equity",
                "must remain positive after every period",
            )
        if self._initial_equity is None:
            self._initial_equity = equity
            period_return = _ZERO
        else:
            prior_equity = self._equity_path[-1]
            period_return = equity / prior_equity - _ONE
        self._equity_path.append(equity)
        self._return_path.append(period_return)
        self._last_equity_at = timestamp
        self._assert_nonnegative_cash()
        return True

    def _try_equity(self, timestamp: datetime) -> Decimal | None:
        equity = sum(self._cash.values(), _ZERO)
        for venue_id, quantity in self._positions.items():
            if quantity == _ZERO:
                continue
            arrival = self._latest_quotes.get(venue_id)
            if arrival is None or not self._quote_is_fresh(timestamp, arrival):
                return None
            executable_price = arrival.quote.bid if quantity >= _ZERO else arrival.quote.ask
            equity += quantity * executable_price
        return equity

    def _assert_nonnegative_cash(self) -> None:
        if any(cash < _ZERO for cash in self._cash.values()):
            raise _policy_error("engine.cash", "cash balance became negative")

    def _assert_reservations(self) -> None:
        if any(value < _ZERO for value in self._reserved_cash.values()):
            raise _policy_error("engine.reserved_cash", "cash reservation became negative")
        if any(value < _ZERO for value in self._reserved_positions.values()):
            raise _policy_error("engine.reserved_positions", "position reservation became negative")

    def _result_metrics(self, final_equity: Decimal) -> tuple[tuple[str, Decimal], ...]:
        if self._initial_equity is None:
            raise _resource_error("engine.equity", "initial equity was not recorded")
        metrics: list[tuple[str, Decimal]] = [
            ("initial_equity", self._initial_equity),
            ("final_equity", final_equity),
            ("net_pnl", final_equity - self._initial_equity),
            ("net_return", final_equity / self._initial_equity - _ONE),
            ("max_drawdown", max_drawdown(self._equity_path)),
            ("gross_notional", self._gross_notional),
            ("fees", self._fees),
            ("filled_buy_quantity", self._filled_buy_quantity),
            ("filled_sell_quantity", self._filled_sell_quantity),
            ("decision_count", Decimal(self._decision_count)),
            ("order_count", Decimal(self._order_count)),
        ]
        metrics.extend(
            (f"net_return_{index:06d}", value) for index, value in enumerate(self._return_path)
        )
        metrics.extend(
            (f"equity_{index:06d}", value) for index, value in enumerate(self._equity_path)
        )
        return tuple(metrics)


def _parse_payload(data: Mapping[str, Any]) -> CrossVenueRunPayload:
    try:
        return CrossVenueRunPayload.model_validate(data)
    except ValidationError as error:
        raise payload_validation_error(error) from error


def _route_for_id(policy: CrossVenueExecutionPolicy, route_id: str) -> RoutePolicy:
    for route in policy.routes:
        if route.route_id == route_id:
            return route
    raise _policy_error(
        "strategy.ast.root.parameters.route_id", "strategy route is not in execution policy"
    )


def _milliseconds(value: int) -> timedelta:
    return timedelta(milliseconds=value)


def _policy_error(path: str, reason: str) -> EngineInputError:
    return EngineInputError(ErrorCode.POLICY, path, reason)


def _resource_error(path: str, reason: str) -> EngineInputError:
    return EngineInputError(ErrorCode.RESOURCE, path, reason)


def _engine_input_error(error: DomainError) -> EngineInputError:
    detail = error.detail
    field = detail.details[0] if detail.details else None
    return EngineInputError(
        detail.code,
        field.path if field is not None else "strategy",
        field.reason if field is not None else detail.message,
        detail.details,
    )
