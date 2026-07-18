"""Deterministic point-in-time factor panel portfolio engine."""

from __future__ import annotations

from collections.abc import Mapping
from decimal import Decimal
from typing import Any

from alpha_foundry.domain.errors import ErrorCode
from alpha_foundry.domain.models import Domain, ExperimentConfig, ExperimentResult

from .base import (
    BaseEngine,
    EngineInputError,
    array_items,
    availability_matrix,
    cost_components,
    decimal_matrix,
    deterministic_decimal_context,
    max_drawdown,
    metric_path,
    ordered_timestamps,
    require_field,
    require_mapping,
    require_policy_rates,
    require_positive_int,
)

_ZERO = Decimal("0")
_ONE = Decimal("1")


class FactorEngine(BaseEngine):
    """Rank a point-in-time signal panel into a one-period-lagged long-short book.

    ``data`` must provide ``timestamps``, ``asset_ids``, ``signals``, ``returns``,
    ``signal_available_at``, ``long_count``, ``short_count``, and a ``policy`` mapping
    with Decimal ``commission_rate``, ``slippage_rate``, and ``impact_rate`` values.
    Matrices are time-major and have shape ``len(timestamps)`` by ``len(asset_ids)``.
    """

    domain = Domain.FACTOR
    engine_id = "factor-panel-v1"

    def run(self, config: ExperimentConfig, data: Mapping[str, Any]) -> ExperimentResult:
        try:
            self.validate_config(config)
            with deterministic_decimal_context():
                input_data = self._validate_data(data)
                return self._simulate(config, input_data)
        except EngineInputError as error:
            return self.failed(config, error)

    def _validate_data(self, data: Mapping[str, Any]) -> dict[str, object]:
        data = require_mapping(data, "data")
        timestamps = ordered_timestamps(require_field(data, "timestamps"), "timestamps")
        if len(timestamps) < 2:
            raise EngineInputError(
                ErrorCode.SCHEMA, "timestamps", "must contain at least two periods"
            )

        asset_values = array_items(require_field(data, "asset_ids"), "asset_ids")
        if not asset_values:
            raise EngineInputError(ErrorCode.SCHEMA, "asset_ids", "must not be empty")
        if any(not isinstance(asset, str) or not asset for asset in asset_values):
            raise EngineInputError(ErrorCode.SCHEMA, "asset_ids", "must contain non-empty strings")
        asset_ids: tuple[str, ...] = tuple(
            asset for asset in asset_values if isinstance(asset, str)
        )
        if len(asset_ids) != len(set(asset_ids)):
            raise EngineInputError(ErrorCode.SCHEMA, "asset_ids", "must be unique")

        periods = len(timestamps)
        asset_count = len(asset_ids)
        signals = decimal_matrix(require_field(data, "signals"), "signals", periods, asset_count)
        returns = decimal_matrix(require_field(data, "returns"), "returns", periods, asset_count)
        for period_index, row in enumerate(returns):
            for asset_index, realized_return in enumerate(row):
                if realized_return < -_ONE:
                    raise EngineInputError(
                        ErrorCode.SCHEMA,
                        f"returns[{period_index}][{asset_index}]",
                        "must be greater than or equal to -1",
                    )
        availability_matrix(
            require_field(data, "signal_available_at"),
            "signal_available_at",
            timestamps,
            asset_count,
        )

        long_count = require_positive_int(require_field(data, "long_count"), "long_count")
        short_count = require_positive_int(require_field(data, "short_count"), "short_count")
        if long_count + short_count > asset_count:
            raise EngineInputError(
                ErrorCode.SCHEMA,
                "long_count",
                "long_count plus short_count must not exceed the asset count",
            )
        rates = require_policy_rates(
            data,
            ("commission_rate", "slippage_rate", "impact_rate"),
        )
        return {
            "timestamps": timestamps,
            "asset_ids": asset_ids,
            "signals": signals,
            "returns": returns,
            "long_count": long_count,
            "short_count": short_count,
            "rates": rates,
        }

    def _simulate(
        self, config: ExperimentConfig, input_data: Mapping[str, object]
    ) -> ExperimentResult:
        timestamps = input_data["timestamps"]
        asset_ids = input_data["asset_ids"]
        signals = input_data["signals"]
        returns = input_data["returns"]
        long_count = input_data["long_count"]
        short_count = input_data["short_count"]
        rates = input_data["rates"]
        if not (
            isinstance(timestamps, tuple)
            and isinstance(asset_ids, tuple)
            and isinstance(signals, tuple)
            and isinstance(returns, tuple)
            and isinstance(long_count, int)
            and isinstance(short_count, int)
            and isinstance(rates, dict)
        ):
            raise RuntimeError("validated factor input has an invalid internal shape")

        asset_count = len(asset_ids)
        previous_position = tuple(_ZERO for _ in range(asset_count))
        positions: list[tuple[Decimal, ...]] = []
        turnovers: list[Decimal] = []
        gross_returns: list[Decimal] = []
        commissions: list[Decimal] = []
        slippages: list[Decimal] = []
        impacts: list[Decimal] = []
        net_returns: list[Decimal] = []
        equity: list[Decimal] = []
        nav = _ONE
        gross_nav = _ONE

        for period_index in range(len(timestamps)):
            position = (
                tuple(_ZERO for _ in range(asset_count))
                if period_index == 0
                else self._ranked_position(
                    signals[period_index - 1],
                    asset_ids,
                    long_count,
                    short_count,
                )
            )
            turnover = sum(
                (
                    abs(current - previous)
                    for current, previous in zip(position, previous_position, strict=True)
                ),
                _ZERO,
            )
            gross_return = sum(
                (
                    weight * realized
                    for weight, realized in zip(position, returns[period_index], strict=True)
                ),
                _ZERO,
            )
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
            ("asset_count", Decimal(asset_count)),
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
            "asset_ids": asset_ids,
            "commission": tuple(commissions),
            "equity": tuple(equity),
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
    def _ranked_position(
        signal: tuple[Decimal, ...],
        asset_ids: tuple[str, ...],
        long_count: int,
        short_count: int,
    ) -> tuple[Decimal, ...]:
        """Create equal-weight legs with asset-id tie breaking and no lookahead."""

        ranking = sorted(
            range(len(asset_ids)),
            key=lambda index: (-signal[index], asset_ids[index]),
        )
        long_indices = ranking[:long_count]
        short_indices = ranking[len(ranking) - short_count :]
        weights = [_ZERO for _ in asset_ids]
        long_weight = _ONE / Decimal(long_count)
        short_weight = -_ONE / Decimal(short_count)
        for index in long_indices:
            weights[index] = long_weight
        for index in short_indices:
            weights[index] = short_weight
        return tuple(weights)
