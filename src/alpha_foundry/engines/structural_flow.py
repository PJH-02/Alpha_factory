"""Deterministic point-in-time structural-flow event-impact simulation."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
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


def _timestamp_value(value: object, path: str, expected_type: type[object]) -> Timestamp:
    normalized = normalize_timestamp(value, path)
    if type(normalized) is not expected_type:
        raise EngineInputError(ErrorCode.SCHEMA, path, "must use the timestamps array type")
    return normalized


def _timestamp_key(value: Timestamp) -> tuple[int, int, str]:
    if isinstance(value, datetime):
        return (0, 0, value.astimezone(UTC).isoformat())
    return (2, value, "")


@dataclass(frozen=True)
class _FlowEvent:
    event_at: Timestamp
    available_at: Timestamp
    impact: Decimal


@dataclass(frozen=True)
class _StructuralFlowInput:
    timestamps: tuple[Timestamp, ...]
    prices: tuple[Decimal, ...]
    events: tuple[_FlowEvent, ...]
    execution_lag_periods: int
    impact_multiplier: Decimal
    decay_rate: Decimal
    maximum_position: Decimal
    allow_negative_cash: bool
    position_scale: Decimal
    initial_cash: Decimal
    initial_position: Decimal
    rates: Mapping[str, Decimal]


class StructuralFlowEngine(BaseEngine):
    """Trade decayed event impacts only after their declared availability and lag.

    ``data`` requires ordered ``timestamps`` and matching positive ``prices``.  Every
    event in ``events`` provides ``event_at``, ``available_at``, and a signed
    ``impact``.  Event availability must be ordered, use the market timestamp type,
    and cannot precede its event time.  The explicit policy mapping supplies
    ``execution_lag_periods``, ``impact_multiplier``, ``decay_rate``,
    ``maximum_position``, ``allow_negative_cash``, and all cost rates.  The input
    also supplies ``position_scale``, ``initial_cash``, and ``initial_position``.
    """

    domain = Domain.STRUCTURAL_FLOW
    engine_id = "structural-flow-event-impact-v1"

    def run(self, config: ExperimentConfig, data: Mapping[str, Any]) -> ExperimentResult:
        try:
            self.validate_config(config)
            with deterministic_decimal_context():
                return self._simulate(config, self._validate_data(data))
        except EngineInputError as error:
            return self.failed(config, error)

    def _validate_data(self, data: Mapping[str, Any]) -> _StructuralFlowInput:
        input_data = require_mapping(data, "data")
        timestamps = ordered_timestamps(require_field(input_data, "timestamps"), "timestamps")
        raw_prices = array_items(require_field(input_data, "prices"), "prices")
        if len(raw_prices) != len(timestamps):
            raise EngineInputError(ErrorCode.SCHEMA, "prices", "must match timestamps length")
        prices = tuple(
            decimal_value(price, f"prices[{index}]") for index, price in enumerate(raw_prices)
        )
        if any(price <= _ZERO for price in prices):
            raise EngineInputError(ErrorCode.SCHEMA, "prices", "must contain positive prices")

        timestamp_type = type(timestamps[0])
        raw_events = array_items(require_field(input_data, "events"), "events")
        if not raw_events:
            raise EngineInputError(ErrorCode.SCHEMA, "events", "must not be empty")
        events: list[_FlowEvent] = []
        for index, raw_event in enumerate(raw_events):
            event = require_mapping(raw_event, f"events[{index}]")
            impact = decimal_value(require_field(event, "impact"), f"events[{index}].impact")
            events.append(
                _FlowEvent(
                    event_at=_timestamp_value(
                        require_field(event, "event_at"),
                        f"events[{index}].event_at",
                        timestamp_type,
                    ),
                    available_at=_timestamp_value(
                        require_field(event, "available_at"),
                        f"events[{index}].available_at",
                        timestamp_type,
                    ),
                    impact=impact,
                )
            )
        event_times = ordered_timestamps(
            tuple(event.event_at for event in events), "events.event_at"
        )
        availability_times = ordered_timestamps(
            tuple(event.available_at for event in events), "events.available_at"
        )
        for index, (event_time, availability_time) in enumerate(
            zip(event_times, availability_times, strict=True)
        ):
            if (
                type(event_time) is not timestamp_type
                or type(availability_time) is not timestamp_type
            ):
                raise EngineInputError(
                    ErrorCode.SCHEMA,
                    f"events[{index}]",
                    "event and availability timestamps must use the market timestamp type",
                )
            if _timestamp_key(availability_time) < _timestamp_key(event_time):
                raise EngineInputError(
                    ErrorCode.SCHEMA,
                    f"events[{index}].available_at",
                    "must not precede event_at",
                )

        policy = require_mapping(
            require_field(input_data, "policy", ErrorCode.POLICY), "policy", ErrorCode.POLICY
        )
        execution_lag_periods = _positive_int(
            policy,
            "execution_lag_periods",
            "policy.execution_lag_periods",
        )
        impact_multiplier = _finite_policy_decimal(
            policy,
            "impact_multiplier",
            "policy.impact_multiplier",
        )
        decay_rate = _nonnegative_policy_decimal(policy, "decay_rate", "policy.decay_rate")
        if decay_rate > _ONE:
            raise EngineInputError(ErrorCode.POLICY, "policy.decay_rate", "must not exceed one")
        maximum_position = _nonnegative_policy_decimal(
            policy,
            "maximum_position",
            "policy.maximum_position",
        )
        if maximum_position <= _ZERO:
            raise EngineInputError(ErrorCode.POLICY, "policy.maximum_position", "must be positive")
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
        position_scale = decimal_value(
            require_field(input_data, "position_scale"), "position_scale"
        )
        if position_scale <= _ZERO:
            raise EngineInputError(ErrorCode.SCHEMA, "position_scale", "must be positive")
        initial_cash = decimal_value(require_field(input_data, "initial_cash"), "initial_cash")
        if initial_cash < _ZERO:
            raise EngineInputError(ErrorCode.SCHEMA, "initial_cash", "must be non-negative")
        initial_position = decimal_value(
            require_field(input_data, "initial_position"), "initial_position"
        )
        if abs(initial_position) > maximum_position:
            raise EngineInputError(
                ErrorCode.SCHEMA,
                "initial_position",
                "must not exceed the explicit maximum position",
            )
        if initial_cash + initial_position * prices[0] <= _ZERO:
            raise EngineInputError(
                ErrorCode.VALIDATION,
                "accounting.initial_equity",
                "must be positive",
            )
        return _StructuralFlowInput(
            timestamps=timestamps,
            prices=prices,
            events=tuple(events),
            execution_lag_periods=execution_lag_periods,
            impact_multiplier=impact_multiplier,
            decay_rate=decay_rate,
            maximum_position=maximum_position,
            allow_negative_cash=allow_negative_cash,
            position_scale=position_scale,
            initial_cash=initial_cash,
            initial_position=initial_position,
            rates=rates,
        )

    def _simulate(
        self, config: ExperimentConfig, input_data: _StructuralFlowInput
    ) -> ExperimentResult:
        activation_periods = tuple(
            _activation_period(event.available_at, input_data.timestamps)
            for event in input_data.events
        )
        raw_signals: list[Decimal] = []
        desired_positions: list[Decimal] = []
        executed_positions: list[Decimal] = []
        cash_path: list[Decimal] = []
        equity: list[Decimal] = []
        net_returns: list[Decimal] = []
        turnovers: list[Decimal] = []
        commissions: list[Decimal] = []
        slippages: list[Decimal] = []
        impacts: list[Decimal] = []

        cash = input_data.initial_cash
        position = input_data.initial_position
        previous_equity = cash + position * input_data.prices[0]
        decay_base = _ONE - input_data.decay_rate
        for period_index, price in enumerate(input_data.prices):
            signal = sum(
                (
                    event.impact
                    * input_data.impact_multiplier
                    * (decay_base ** (period_index - activation_period))
                    for event, activation_period in zip(
                        input_data.events, activation_periods, strict=True
                    )
                    if activation_period is not None and activation_period <= period_index
                ),
                _ZERO,
            )
            desired_position = _clamp(
                signal * input_data.position_scale,
                -input_data.maximum_position,
                input_data.maximum_position,
            )
            raw_signals.append(signal)
            desired_positions.append(desired_position)
            target_position = (
                position
                if period_index < input_data.execution_lag_periods
                else desired_positions[period_index - input_data.execution_lag_periods]
            )
            trade_quantity = target_position - position
            turnover = abs(trade_quantity) * price
            commission, slippage, impact = cost_components(turnover, input_data.rates)
            cash = cash - trade_quantity * price - commission - slippage - impact
            position = target_position
            if abs(position) > input_data.maximum_position:
                raise EngineInputError(
                    ErrorCode.VALIDATION,
                    f"accounting.position[{period_index}]",
                    "exceeds the explicit maximum position",
                )
            if not input_data.allow_negative_cash and cash < _ZERO:
                raise EngineInputError(
                    ErrorCode.VALIDATION,
                    f"accounting.cash[{period_index}]",
                    "became negative under the explicit cash policy",
                )
            current_equity = cash + position * price
            if current_equity <= _ZERO:
                raise EngineInputError(
                    ErrorCode.VALIDATION,
                    f"accounting.equity[{period_index}]",
                    "must remain positive",
                )

            executed_positions.append(position)
            cash_path.append(cash)
            equity.append(current_equity)
            net_returns.append(current_equity / previous_equity - _ONE)
            turnovers.append(turnover)
            commissions.append(commission)
            slippages.append(slippage)
            impacts.append(impact)
            previous_equity = current_equity

        total_commission = sum(commissions, _ZERO)
        total_slippage = sum(slippages, _ZERO)
        total_impact = sum(impacts, _ZERO)
        initial_equity = (
            input_data.initial_cash + input_data.initial_position * input_data.prices[0]
        )
        metrics = (
            *metric_path("net_return", net_returns),
            *metric_path("equity", equity),
            *metric_path("cash", cash_path),
            *metric_path("position", executed_positions),
            *metric_path("event_signal", raw_signals),
            *metric_path("desired_position", desired_positions),
            ("period_count", Decimal(len(input_data.timestamps))),
            ("event_count", Decimal(len(input_data.events))),
            ("total_turnover", sum(turnovers, _ZERO)),
            ("total_commission", total_commission),
            ("total_slippage", total_slippage),
            ("total_impact", total_impact),
            ("total_cost", total_commission + total_slippage + total_impact),
            ("final_equity", equity[-1]),
            ("cumulative_net_return", equity[-1] / initial_equity - _ONE),
            ("max_drawdown", max_drawdown(equity)),
        )
        artifact = {
            "cash": tuple(cash_path),
            "commission": tuple(commissions),
            "desired_position": tuple(desired_positions),
            "equity": tuple(equity),
            "event_signal": tuple(raw_signals),
            "impact": tuple(impacts),
            "net_return": tuple(net_returns),
            "position": tuple(executed_positions),
            "slippage": tuple(slippages),
            "timestamps": input_data.timestamps,
            "turnover": tuple(turnovers),
        }
        return self.succeeded(config, metrics, artifact)


def _activation_period(available_at: Timestamp, timestamps: tuple[Timestamp, ...]) -> int | None:
    for index, timestamp in enumerate(timestamps):
        if _timestamp_key(available_at) <= _timestamp_key(timestamp):
            return index
    return None


def _clamp(value: Decimal, lower: Decimal, upper: Decimal) -> Decimal:
    return min(max(value, lower), upper)


def _positive_int(data: Mapping[str, Any], name: str, path: str) -> int:
    value = require_field(data, name, ErrorCode.POLICY)
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise EngineInputError(ErrorCode.POLICY, path, "must be a positive integer")
    return value


def _finite_policy_decimal(data: Mapping[str, Any], name: str, path: str) -> Decimal:
    value = require_field(data, name, ErrorCode.POLICY)
    if not isinstance(value, Decimal) or not value.is_finite():
        raise EngineInputError(ErrorCode.POLICY, path, "must be a finite Decimal")
    return value


def _nonnegative_policy_decimal(data: Mapping[str, Any], name: str, path: str) -> Decimal:
    value = _finite_policy_decimal(data, name, path)
    if value < _ZERO:
        raise EngineInputError(ErrorCode.POLICY, path, "must be non-negative")
    return value
