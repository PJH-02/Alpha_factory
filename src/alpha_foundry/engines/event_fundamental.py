"""Deterministic event-time fundamental strategy engine with point-in-time guards."""

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
    decimal_vector,
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
_EVENT_FIELDS = frozenset({"event_id", "published_at", "available_at", "surprise"})
_POLICY_FIELDS = frozenset(
    {
        "entry_lag_periods",
        "holding_periods",
        "signal_threshold",
        "position_size",
        "maximum_gross_exposure",
        "allow_short",
        "commission_rate",
        "slippage_rate",
        "impact_rate",
    }
)


@dataclass(frozen=True)
class _Event:
    event_id: str
    published_at: Timestamp
    available_at: Timestamp
    surprise: Decimal


class EventFundamentalEngine(BaseEngine):
    """Trade a single return stream from event surprises only after their availability.

    The event is first observable at ``available_at``.  A signal can only enter after
    ``entry_lag_periods`` full timeline steps and remains active for exactly
    ``holding_periods`` realized returns.  Overlapping event positions are permitted
    only up to the explicitly supplied gross-exposure cap.
    """

    domain = Domain.EVENT_FUNDAMENTAL
    engine_id = "event-fundamental-v1"

    def run(self, config: ExperimentConfig, data: Mapping[str, Any]) -> ExperimentResult:
        try:
            self.validate_config(config)
            with deterministic_decimal_context():
                input_data = self._validate_data(data)
                return self._simulate(config, input_data)
        except EngineInputError as error:
            return self.failed(config, error)

    def _validate_data(self, data: Mapping[str, Any]) -> dict[str, object]:
        source = require_mapping(data, "data")
        allowed_fields = frozenset({"domain", "timestamps", "returns", "events", "policy"})
        if set(source) - allowed_fields:
            raise EngineInputError(ErrorCode.SCHEMA, "data", "contains unknown fields")
        if "domain" in source and source["domain"] != self.domain:
            raise EngineInputError(
                ErrorCode.DOMAIN,
                "data.domain",
                f"expected {self.domain.value}",
            )
        timestamps = ordered_timestamps(require_field(source, "timestamps"), "timestamps")
        if len(timestamps) < 2:
            raise EngineInputError(
                ErrorCode.SCHEMA, "timestamps", "must contain at least two periods"
            )
        returns = decimal_vector(require_field(source, "returns"), "returns", len(timestamps))
        for period_index, realized_return in enumerate(returns):
            if realized_return < -_ONE:
                raise EngineInputError(
                    ErrorCode.SCHEMA,
                    f"returns[{period_index}]",
                    "must be greater than or equal to -1",
                )
        events = self._validate_events(
            require_field(source, "events"),
            "events",
            type(timestamps[0]),
        )
        policy = require_mapping(
            require_field(source, "policy", ErrorCode.POLICY), "policy", ErrorCode.POLICY
        )
        if set(policy) != _POLICY_FIELDS:
            raise EngineInputError(
                ErrorCode.POLICY,
                "policy",
                "must contain exactly the event timing, exposure, and cost fields",
            )
        entry_lag_periods = self._policy_positive_int(policy, "entry_lag_periods")
        holding_periods = self._policy_positive_int(policy, "holding_periods")
        signal_threshold = self._policy_decimal(policy, "signal_threshold", allow_zero=True)
        position_size = self._policy_decimal(policy, "position_size", allow_zero=False)
        maximum_gross_exposure = self._policy_decimal(
            policy, "maximum_gross_exposure", allow_zero=False
        )
        if position_size > maximum_gross_exposure:
            raise EngineInputError(
                ErrorCode.POLICY,
                "policy.position_size",
                "must not exceed policy.maximum_gross_exposure",
            )
        allow_short = require_field(policy, "allow_short", ErrorCode.POLICY)
        if not isinstance(allow_short, bool):
            raise EngineInputError(ErrorCode.POLICY, "policy.allow_short", "must be a boolean")
        rates = require_policy_rates(
            {"policy": policy}, ("commission_rate", "slippage_rate", "impact_rate")
        )
        return {
            "timestamps": timestamps,
            "returns": returns,
            "events": events,
            "entry_lag_periods": entry_lag_periods,
            "holding_periods": holding_periods,
            "signal_threshold": signal_threshold,
            "position_size": position_size,
            "maximum_gross_exposure": maximum_gross_exposure,
            "allow_short": allow_short,
            "rates": rates,
        }

    @staticmethod
    def _validate_events(
        value: object,
        path: str,
        timestamp_type: type[object],
    ) -> tuple[_Event, ...]:
        items = array_items(value, path)
        if not items:
            raise EngineInputError(ErrorCode.SCHEMA, path, "must not be empty")
        events: list[_Event] = []
        seen_ids: set[str] = set()
        for index, item in enumerate(items):
            event_path = f"{path}[{index}]"
            event = require_mapping(item, event_path)
            if set(event) != _EVENT_FIELDS:
                raise EngineInputError(
                    ErrorCode.SCHEMA,
                    event_path,
                    "must contain exactly event_id, published_at, available_at, and surprise",
                )
            event_id = event["event_id"]
            if not isinstance(event_id, str) or not event_id:
                raise EngineInputError(
                    ErrorCode.SCHEMA, f"{event_path}.event_id", "must be a non-empty string"
                )
            if event_id in seen_ids:
                raise EngineInputError(ErrorCode.SCHEMA, f"{event_path}.event_id", "must be unique")
            seen_ids.add(event_id)
            published_at = EventFundamentalEngine._timestamp_value(
                event["published_at"], f"{event_path}.published_at", timestamp_type
            )
            available_at = EventFundamentalEngine._timestamp_value(
                event["available_at"], f"{event_path}.available_at", timestamp_type
            )
            if EventFundamentalEngine._timestamp_key(
                available_at
            ) < EventFundamentalEngine._timestamp_key(published_at):
                raise EngineInputError(
                    ErrorCode.SCHEMA,
                    f"{event_path}.available_at",
                    "must not precede the event publication time",
                )
            events.append(
                _Event(
                    event_id=event_id,
                    published_at=published_at,
                    available_at=available_at,
                    surprise=decimal_value(event["surprise"], f"{event_path}.surprise"),
                )
            )
        return tuple(events)

    @staticmethod
    def _timestamp_value(value: object, path: str, expected_type: type[object]) -> Timestamp:
        normalized = normalize_timestamp(value, path)
        if type(normalized) is not expected_type:
            raise EngineInputError(ErrorCode.SCHEMA, path, "must use the timestamps array type")
        return normalized

    @staticmethod
    def _timestamp_key(value: Timestamp) -> tuple[int, int, str]:
        if isinstance(value, datetime):
            return (0, 0, value.astimezone(UTC).isoformat())
        return (2, value, "")

    @staticmethod
    def _policy_positive_int(policy: Mapping[str, Any], name: str) -> int:
        value = require_field(policy, name, ErrorCode.POLICY)
        if isinstance(value, bool) or not isinstance(value, int) or value < 1:
            raise EngineInputError(ErrorCode.POLICY, f"policy.{name}", "must be a positive integer")
        return value

    @staticmethod
    def _policy_decimal(policy: Mapping[str, Any], name: str, *, allow_zero: bool) -> Decimal:
        value = require_field(policy, name, ErrorCode.POLICY)
        if (
            not isinstance(value, Decimal)
            or not value.is_finite()
            or (not allow_zero and value <= _ZERO)
        ):
            comparison = "non-negative" if allow_zero else "positive"
            raise EngineInputError(
                ErrorCode.POLICY,
                f"policy.{name}",
                f"must be a finite {comparison} Decimal",
            )
        if allow_zero and value < _ZERO:
            raise EngineInputError(
                ErrorCode.POLICY,
                f"policy.{name}",
                "must be a finite non-negative Decimal",
            )
        return value

    def _simulate(
        self, config: ExperimentConfig, input_data: Mapping[str, object]
    ) -> ExperimentResult:
        timestamps = input_data["timestamps"]
        returns = input_data["returns"]
        events = input_data["events"]
        entry_lag_periods = input_data["entry_lag_periods"]
        holding_periods = input_data["holding_periods"]
        signal_threshold = input_data["signal_threshold"]
        position_size = input_data["position_size"]
        maximum_gross_exposure = input_data["maximum_gross_exposure"]
        allow_short = input_data["allow_short"]
        rates = input_data["rates"]
        if not (
            isinstance(timestamps, tuple)
            and isinstance(returns, tuple)
            and isinstance(events, tuple)
            and isinstance(entry_lag_periods, int)
            and isinstance(holding_periods, int)
            and isinstance(signal_threshold, Decimal)
            and isinstance(position_size, Decimal)
            and isinstance(maximum_gross_exposure, Decimal)
            and isinstance(allow_short, bool)
            and isinstance(rates, dict)
        ):
            raise RuntimeError("validated event fundamental input has an invalid internal shape")
        if not all(isinstance(event, _Event) for event in events):
            raise RuntimeError("validated event fundamental events have an invalid internal shape")

        active_events: list[list[Decimal]] = [[] for _ in timestamps]
        scheduled_event_count = 0
        unavailable_event_count = 0
        for event in sorted(
            events,
            key=lambda item: (
                EventFundamentalEngine._timestamp_key(item.available_at),
                item.event_id,
            ),
        ):
            weight = self._event_weight(
                event.surprise,
                signal_threshold,
                position_size,
                allow_short,
            )
            if weight == _ZERO:
                continue
            event_available_key = EventFundamentalEngine._timestamp_key(event.available_at)
            first_available_index = next(
                (
                    index
                    for index, timestamp in enumerate(timestamps)
                    if EventFundamentalEngine._timestamp_key(timestamp) >= event_available_key
                ),
                None,
            )
            if first_available_index is None:
                unavailable_event_count += 1
                continue
            entry_index = first_available_index + entry_lag_periods
            if entry_index >= len(timestamps):
                continue
            scheduled_event_count += 1
            for period_index in range(
                entry_index, min(entry_index + holding_periods, len(timestamps))
            ):
                active_events[period_index].append(weight)

        previous_position = _ZERO
        positions: list[Decimal] = []
        gross_exposures: list[Decimal] = []
        active_event_counts: list[Decimal] = []
        turnovers: list[Decimal] = []
        gross_returns: list[Decimal] = []
        commissions: list[Decimal] = []
        slippages: list[Decimal] = []
        impacts: list[Decimal] = []
        net_returns: list[Decimal] = []
        equity: list[Decimal] = []
        nav = _ONE
        gross_nav = _ONE
        for period_index, realized_return in enumerate(returns):
            gross_exposure = sum((abs(weight) for weight in active_events[period_index]), _ZERO)
            if gross_exposure > maximum_gross_exposure:
                raise EngineInputError(
                    ErrorCode.POLICY,
                    f"accounting.gross_exposure[{period_index}]",
                    "overlapping event positions exceed policy.maximum_gross_exposure",
                )
            position = sum(active_events[period_index], _ZERO)
            turnover = abs(position - previous_position)
            gross_return = position * realized_return
            commission, slippage, impact = cost_components(turnover, rates)
            net_return = gross_return - commission - slippage - impact
            if net_return <= -_ONE:
                raise EngineInputError(
                    ErrorCode.VALIDATION,
                    f"accounting.net_returns[{period_index}]",
                    "must remain greater than -1 to preserve positive equity",
                )
            nav *= _ONE + net_return
            gross_nav *= _ONE + gross_return
            positions.append(position)
            gross_exposures.append(gross_exposure)
            active_event_counts.append(Decimal(len(active_events[period_index])))
            turnovers.append(turnover)
            gross_returns.append(gross_return)
            commissions.append(commission)
            slippages.append(slippage)
            impacts.append(impact)
            net_returns.append(net_return)
            equity.append(nav)
            previous_position = position

        total_commission = sum(commissions, _ZERO)
        total_slippage = sum(slippages, _ZERO)
        total_impact = sum(impacts, _ZERO)
        total_cost = total_commission + total_slippage + total_impact
        metrics = (
            *metric_path("net_return", net_returns),
            *metric_path("equity", equity),
            ("period_count", Decimal(len(timestamps))),
            ("event_count", Decimal(len(events))),
            ("scheduled_event_count", Decimal(scheduled_event_count)),
            ("unavailable_event_count", Decimal(unavailable_event_count)),
            ("entry_lag_periods", Decimal(entry_lag_periods)),
            ("holding_periods", Decimal(holding_periods)),
            ("total_turnover", sum(turnovers, _ZERO)),
            ("total_commission", total_commission),
            ("total_slippage", total_slippage),
            ("total_impact", total_impact),
            ("total_cost", total_cost),
            ("cumulative_gross_return", gross_nav - _ONE),
            ("cumulative_net_return", nav - _ONE),
            ("final_equity", nav),
            ("max_drawdown", max_drawdown(equity)),
        )
        artifact = {
            "active_event_counts": tuple(active_event_counts),
            "commission": tuple(commissions),
            "equity": tuple(equity),
            "events": tuple(
                {
                    "available_at": event.available_at,
                    "event_id": event.event_id,
                    "published_at": event.published_at,
                    "surprise": event.surprise,
                }
                for event in events
            ),
            "gross_exposure": tuple(gross_exposures),
            "gross_returns": tuple(gross_returns),
            "impact": tuple(impacts),
            "net_returns": tuple(net_returns),
            "positions": tuple(positions),
            "slippage": tuple(slippages),
            "timestamps": timestamps,
            "turnover": tuple(turnovers),
        }
        return self.succeeded(config, metrics, artifact)

    @staticmethod
    def _event_weight(
        surprise: Decimal,
        signal_threshold: Decimal,
        position_size: Decimal,
        allow_short: bool,
    ) -> Decimal:
        if surprise > _ZERO and surprise >= signal_threshold:
            return position_size
        if surprise < _ZERO and surprise <= -signal_threshold and allow_short:
            return -position_size
        return _ZERO
