"""Deterministic leakage-free rolling-momentum time-series engine."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
from decimal import Decimal, DecimalException
from typing import Any

from alpha_foundry.domain.errors import ErrorCode
from alpha_foundry.domain.models import Domain, ExperimentConfig, ExperimentResult

from .base import (
    BaseEngine,
    EngineInputError,
    Timestamp,
    array_items,
    cost_components,
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
_POLICY_FIELDS = frozenset(
    {
        "lookback_periods",
        "position_lag_periods",
        "rebalance_periods",
        "signal_threshold",
        "position_size",
        "maximum_gross_exposure",
        "allow_short",
        "commission_rate",
        "slippage_rate",
        "impact_rate",
    }
)


class TimeSeriesEngine(BaseEngine):
    """Trade lagged rolling momentum without reading any value after the decision time.

    At realized period ``t``, a position update can only use a rolling return ending at
    ``t - position_lag_periods``.  The selected target is then applied to the return
    between the prior and current price observations.  Price availability is validated
    before simulation, so unavailable observations cannot enter a rolling indicator.
    """

    domain = Domain.TIME_SERIES
    engine_id = "time-series-v1"

    def run(self, config: ExperimentConfig, data: Mapping[str, Any]) -> ExperimentResult:
        try:
            self.validate_config(config)
            with deterministic_decimal_context():
                input_data = self._validate_data(data)
                return self._simulate(config, input_data)
        except EngineInputError as error:
            return self.failed(config, error)
        except DecimalException:
            return self.failed(
                config,
                EngineInputError(
                    ErrorCode.VALIDATION,
                    "accounting.decimal128",
                    "Decimal128 arithmetic failed",
                ),
            )

    def _validate_data(self, data: Mapping[str, Any]) -> dict[str, object]:
        source = require_mapping(data, "data")
        allowed_fields = frozenset(
            {"domain", "timestamps", "prices", "price_available_at", "policy"}
        )
        if set(source) - allowed_fields:
            raise EngineInputError(ErrorCode.SCHEMA, "data", "contains unknown fields")
        if "domain" not in source or source["domain"] != self.domain:
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
        prices = tuple(
            self._bounded_decimal(price, f"prices[{index}]", ErrorCode.SCHEMA)
            for index, price in enumerate(
                decimal_vector(require_field(source, "prices"), "prices", len(timestamps))
            )
        )
        for index, price in enumerate(prices):
            if price <= _ZERO:
                raise EngineInputError(ErrorCode.SCHEMA, f"prices[{index}]", "must be positive")
        price_available_at = self._validate_availability(
            require_field(source, "price_available_at"),
            "price_available_at",
            timestamps,
        )
        policy = require_mapping(
            require_field(source, "policy", ErrorCode.POLICY), "policy", ErrorCode.POLICY
        )
        if set(policy) != _POLICY_FIELDS:
            raise EngineInputError(
                ErrorCode.POLICY,
                "policy",
                "must contain exactly the signal timing, exposure, and cost fields",
            )
        lookback_periods = self._policy_positive_int(policy, "lookback_periods")
        position_lag_periods = self._policy_positive_int(policy, "position_lag_periods")
        rebalance_periods = self._policy_positive_int(policy, "rebalance_periods")
        if len(timestamps) < lookback_periods + position_lag_periods + 1:
            raise EngineInputError(
                ErrorCode.SCHEMA,
                "timestamps",
                "must contain a realized period after the first lagged rolling indicator",
            )
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
        rates = {
            name: self._bounded_decimal(value, f"policy.{name}", ErrorCode.POLICY)
            for name, value in require_policy_rates(
                {"policy": policy}, ("commission_rate", "slippage_rate", "impact_rate")
            ).items()
        }
        return {
            "timestamps": timestamps,
            "prices": prices,
            "price_available_at": price_available_at,
            "lookback_periods": lookback_periods,
            "position_lag_periods": position_lag_periods,
            "rebalance_periods": rebalance_periods,
            "signal_threshold": signal_threshold,
            "position_size": position_size,
            "maximum_gross_exposure": maximum_gross_exposure,
            "allow_short": allow_short,
            "rates": rates,
        }

    @staticmethod
    def _validate_availability(
        value: object,
        path: str,
        timestamps: tuple[Timestamp, ...],
    ) -> tuple[Timestamp, ...]:
        values = array_items(value, path)
        if len(values) != len(timestamps):
            raise EngineInputError(
                ErrorCode.SCHEMA, path, "must have the same length as timestamps"
            )
        expected_type = type(timestamps[0])
        normalized: list[Timestamp] = []
        for index, raw_available_at in enumerate(values):
            item_path = f"{path}[{index}]"
            available_at = TimeSeriesEngine._timestamp_value(
                raw_available_at,
                item_path,
                expected_type,
            )
            timestamp = timestamps[index]
            if TimeSeriesEngine._timestamp_key(available_at) > TimeSeriesEngine._timestamp_key(
                timestamp
            ):
                raise EngineInputError(
                    ErrorCode.SCHEMA,
                    item_path,
                    "is not available at its observation timestamp",
                )
            normalized.append(available_at)
        return tuple(normalized)

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
        return TimeSeriesEngine._bounded_decimal(value, f"policy.{name}", ErrorCode.POLICY)

    @staticmethod
    def _bounded_decimal(value: Decimal, path: str, code: ErrorCode) -> Decimal:
        """Reject values that cannot be represented exactly in the fixed Decimal128 context."""
        try:
            bounded = +value
        except DecimalException as error:
            raise EngineInputError(
                code, path, "must fit the Decimal128 range without rounding"
            ) from error
        if bounded != value:
            raise EngineInputError(code, path, "must fit the Decimal128 range without rounding")
        return bounded

    def _simulate(
        self, config: ExperimentConfig, input_data: Mapping[str, object]
    ) -> ExperimentResult:
        timestamps = input_data["timestamps"]
        prices = input_data["prices"]
        price_available_at = input_data["price_available_at"]
        lookback_periods = input_data["lookback_periods"]
        position_lag_periods = input_data["position_lag_periods"]
        rebalance_periods = input_data["rebalance_periods"]
        signal_threshold = input_data["signal_threshold"]
        position_size = input_data["position_size"]
        maximum_gross_exposure = input_data["maximum_gross_exposure"]
        allow_short = input_data["allow_short"]
        rates = input_data["rates"]
        if not (
            isinstance(timestamps, tuple)
            and isinstance(prices, tuple)
            and isinstance(price_available_at, tuple)
            and isinstance(lookback_periods, int)
            and isinstance(position_lag_periods, int)
            and isinstance(rebalance_periods, int)
            and isinstance(signal_threshold, Decimal)
            and isinstance(position_size, Decimal)
            and isinstance(maximum_gross_exposure, Decimal)
            and isinstance(allow_short, bool)
            and isinstance(rates, dict)
        ):
            raise RuntimeError("validated time series input has an invalid internal shape")

        previous_position = _ZERO
        positions: list[Decimal] = []
        signal_source_indices: list[Decimal] = []
        indicators: list[Decimal | None] = []
        turnovers: list[Decimal] = []
        gross_returns: list[Decimal] = []
        commissions: list[Decimal] = []
        slippages: list[Decimal] = []
        impacts: list[Decimal] = []
        net_returns: list[Decimal] = []
        equity: list[Decimal] = []
        nav = _ONE
        for period_index in range(len(timestamps)):
            decision_index = period_index - position_lag_periods
            indicator: Decimal | None = None
            position = previous_position
            if decision_index >= lookback_periods and decision_index % rebalance_periods == 0:
                indicator = (
                    prices[decision_index] / prices[decision_index - lookback_periods] - _ONE
                )
                position = self._target_position(
                    indicator,
                    signal_threshold,
                    position_size,
                    allow_short,
                )
                signal_source_index = Decimal(decision_index)
            else:
                signal_source_index = Decimal("-1")
            if abs(position) > maximum_gross_exposure:
                raise EngineInputError(
                    ErrorCode.POLICY,
                    f"accounting.positions[{period_index}]",
                    "position exceeds policy.maximum_gross_exposure",
                )
            turnover = abs(position - previous_position)
            realized_return = (
                _ZERO
                if period_index == 0
                else prices[period_index] / prices[period_index - 1] - _ONE
            )
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
            positions.append(position)
            signal_source_indices.append(signal_source_index)
            indicators.append(indicator)
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
        rebalance_count = sum(1 for source_index in signal_source_indices if source_index >= _ZERO)
        metrics = (
            *metric_path("net_return", net_returns),
            *metric_path("equity", equity),
            ("period_count", Decimal(len(timestamps))),
            ("lookback_periods", Decimal(lookback_periods)),
            ("position_lag_periods", Decimal(position_lag_periods)),
            ("rebalance_periods", Decimal(rebalance_periods)),
            ("rebalance_count", Decimal(rebalance_count)),
            ("total_turnover", sum(turnovers, _ZERO)),
            ("total_commission", total_commission),
            ("total_slippage", total_slippage),
            ("total_impact", total_impact),
            ("total_cost", total_cost),
            ("cumulative_gross_return", sum(gross_returns, _ZERO)),
            ("cumulative_net_return", nav - _ONE),
            ("final_equity", nav),
            ("max_drawdown", max_drawdown(equity)),
        )
        artifact = {
            "commission": tuple(commissions),
            "equity": tuple(equity),
            "gross_returns": tuple(gross_returns),
            "indicators": tuple(indicators),
            "impact": tuple(impacts),
            "net_returns": tuple(net_returns),
            "positions": tuple(positions),
            "price_available_at": price_available_at,
            "prices": prices,
            "signal_source_indices": tuple(signal_source_indices),
            "slippage": tuple(slippages),
            "timestamps": timestamps,
            "turnover": tuple(turnovers),
        }
        return self.succeeded(config, metrics, artifact)

    @staticmethod
    def _target_position(
        indicator: Decimal,
        signal_threshold: Decimal,
        position_size: Decimal,
        allow_short: bool,
    ) -> Decimal:
        if indicator > signal_threshold:
            return position_size
        if indicator < -signal_threshold and allow_short:
            return -position_size
        return _ZERO
