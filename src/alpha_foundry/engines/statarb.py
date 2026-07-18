"""Deterministic multi-leg sequential statistical-arbitrage engine."""

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
    decimal_value,
    decimal_vector,
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


class StatArbEngine(BaseEngine):
    """Trade a static multi-leg spread from a lagged, sequential rolling z-score.

    ``data`` must provide ``timestamps``, ``leg_ids``, ``prices``,
    ``price_available_at``, ``hedge_weights``, ``lookback``, ``entry_z``, ``exit_z``,
    and a ``policy`` mapping with Decimal ``commission_rate``, ``slippage_rate``, and
    ``impact_rate`` values. Hedge ratios are converted once to gross-one
    initial-notional weights; price-relative signals, PnL, and costs use those same
    weights. A z-score observed at time ``t`` is converted into a position only at
    time ``t + 1``.
    """

    domain = Domain.STAT_ARB
    engine_id = "statarb-sequential-v1"

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
        leg_values = array_items(require_field(data, "leg_ids"), "leg_ids")
        if len(leg_values) < 2:
            raise EngineInputError(ErrorCode.SCHEMA, "leg_ids", "must contain at least two legs")
        if any(not isinstance(leg, str) or not leg for leg in leg_values):
            raise EngineInputError(ErrorCode.SCHEMA, "leg_ids", "must contain non-empty strings")
        leg_ids: tuple[str, ...] = tuple(leg for leg in leg_values if isinstance(leg, str))
        if len(leg_ids) != len(set(leg_ids)):
            raise EngineInputError(ErrorCode.SCHEMA, "leg_ids", "must be unique")

        periods = len(timestamps)
        leg_count = len(leg_ids)
        prices = decimal_matrix(require_field(data, "prices"), "prices", periods, leg_count)
        for row_index, row in enumerate(prices):
            for leg_index, price in enumerate(row):
                if price <= _ZERO:
                    raise EngineInputError(
                        ErrorCode.SCHEMA,
                        f"prices[{row_index}][{leg_index}]",
                        "must be strictly positive",
                    )
        availability_matrix(
            require_field(data, "price_available_at"),
            "price_available_at",
            timestamps,
            leg_count,
        )
        hedge_weights = decimal_vector(
            require_field(data, "hedge_weights"),
            "hedge_weights",
            leg_count,
        )
        if not any(weight > _ZERO for weight in hedge_weights) or not any(
            weight < _ZERO for weight in hedge_weights
        ):
            raise EngineInputError(
                ErrorCode.SCHEMA,
                "hedge_weights",
                "must contain at least one positive and one negative hedge weight",
            )

        lookback = require_positive_int(require_field(data, "lookback"), "lookback")
        if lookback < 2:
            raise EngineInputError(ErrorCode.SCHEMA, "lookback", "must be at least two")
        if periods < lookback + 2:
            raise EngineInputError(
                ErrorCode.SCHEMA,
                "timestamps",
                "must provide a complete lookback and at least one lagged execution period",
            )
        entry_z = decimal_value(require_field(data, "entry_z"), "entry_z")
        exit_z = decimal_value(require_field(data, "exit_z"), "exit_z")
        if entry_z <= _ZERO:
            raise EngineInputError(ErrorCode.SCHEMA, "entry_z", "must be greater than zero")
        if exit_z < _ZERO or exit_z >= entry_z:
            raise EngineInputError(
                ErrorCode.SCHEMA,
                "exit_z",
                "must be non-negative and strictly below entry_z",
            )
        rates = require_policy_rates(
            data,
            ("commission_rate", "slippage_rate", "impact_rate"),
        )
        return {
            "timestamps": timestamps,
            "leg_ids": leg_ids,
            "prices": prices,
            "hedge_weights": hedge_weights,
            "lookback": lookback,
            "entry_z": entry_z,
            "exit_z": exit_z,
            "rates": rates,
        }

    def _simulate(
        self, config: ExperimentConfig, input_data: Mapping[str, object]
    ) -> ExperimentResult:
        timestamps = input_data["timestamps"]
        leg_ids = input_data["leg_ids"]
        prices = input_data["prices"]
        hedge_weights = input_data["hedge_weights"]
        lookback = input_data["lookback"]
        entry_z = input_data["entry_z"]
        exit_z = input_data["exit_z"]
        rates = input_data["rates"]
        if not (
            isinstance(timestamps, tuple)
            and isinstance(leg_ids, tuple)
            and isinstance(prices, tuple)
            and isinstance(hedge_weights, tuple)
            and isinstance(lookback, int)
            and isinstance(entry_z, Decimal)
            and isinstance(exit_z, Decimal)
            and isinstance(rates, dict)
        ):
            raise RuntimeError("validated statarb input has an invalid internal shape")

        economic_weights = self._economic_weights(hedge_weights, prices[0])
        price_relatives = tuple(
            tuple(
                price / initial_price for price, initial_price in zip(row, prices[0], strict=True)
            )
            for row in prices
        )
        spreads = tuple(
            sum(
                (
                    weight * relative_price
                    for weight, relative_price in zip(economic_weights, row, strict=True)
                ),
                _ZERO,
            )
            for row in price_relatives
        )
        previous_position = tuple(_ZERO for _ in economic_weights)
        desired_state = 0
        desired_states: list[int] = []
        z_scores: list[Decimal | None] = []
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
            z_score = self._z_score(spreads, period_index, lookback)
            if z_score is not None:
                desired_state = self._next_state(desired_state, z_score, entry_z, exit_z)
            desired_states.append(desired_state)
            executed_state = desired_states[period_index - 1] if period_index else 0
            position = tuple(Decimal(executed_state) * weight for weight in economic_weights)

            turnover = sum(
                (
                    abs(current - previous)
                    for current, previous in zip(position, previous_position, strict=True)
                ),
                _ZERO,
            )
            gross_return = (
                _ZERO
                if period_index == 0
                else sum(
                    (
                        weight * (current / prior - _ONE)
                        for weight, current, prior in zip(
                            position,
                            prices[period_index],
                            prices[period_index - 1],
                            strict=True,
                        )
                    ),
                    _ZERO,
                )
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

            z_scores.append(z_score)
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
        trade_count = sum(1 for turnover in turnovers if turnover > _ZERO)
        entry_count = sum(
            1
            for index, state in enumerate(desired_states)
            if state and (index == 0 or desired_states[index - 1] == 0)
        )
        metrics = (
            *metric_path("net_return", net_returns),
            *metric_path("equity", equity),
            ("period_count", Decimal(len(timestamps))),
            ("leg_count", Decimal(len(leg_ids))),
            ("z_observation_count", Decimal(sum(score is not None for score in z_scores))),
            ("entry_count", Decimal(entry_count)),
            ("trade_count", Decimal(trade_count)),
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
            "commission": tuple(commissions),
            "desired_state": tuple(desired_states),
            "equity": tuple(equity),
            "gross_returns": tuple(gross_returns),
            "hedge_weights": economic_weights,
            "input_hedge_weights": hedge_weights,
            "impact": tuple(impacts),
            "leg_ids": leg_ids,
            "net_returns": tuple(net_returns),
            "positions": tuple(positions),
            "slippage": tuple(slippages),
            "spreads": spreads,
            "timestamps": timestamps,
            "turnover": tuple(turnovers),
            "z_scores": tuple(z_scores),
        }
        return self.succeeded(config, metrics, artifact)

    @staticmethod
    def _economic_weights(
        hedge_weights: tuple[Decimal, ...],
        initial_prices: tuple[Decimal, ...],
    ) -> tuple[Decimal, ...]:
        """Normalize declared hedge ratios into gross-one initial-notional weights."""

        gross_initial_notional = sum(
            (
                abs(weight) * price
                for weight, price in zip(hedge_weights, initial_prices, strict=True)
            ),
            _ZERO,
        )
        if gross_initial_notional <= _ZERO:
            raise EngineInputError(
                ErrorCode.SCHEMA,
                "hedge_weights",
                "must produce positive initial gross notional",
            )
        return tuple(
            weight * price / gross_initial_notional
            for weight, price in zip(hedge_weights, initial_prices, strict=True)
        )

    @staticmethod
    def _z_score(
        spreads: tuple[Decimal, ...],
        index: int,
        lookback: int,
    ) -> Decimal | None:
        """Use only strictly prior spreads for the rolling location and scale."""

        if index < lookback:
            return None
        history = spreads[index - lookback : index]
        mean = sum(history, _ZERO) / Decimal(lookback)
        variance = sum(((value - mean) * (value - mean) for value in history), _ZERO) / Decimal(
            lookback
        )
        if variance == _ZERO:
            return None
        return (spreads[index] - mean) / variance.sqrt()

    @staticmethod
    def _next_state(current: int, z_score: Decimal, entry_z: Decimal, exit_z: Decimal) -> int:
        """Apply entry, exit, and reversal rules to the current sequential state."""

        if abs(z_score) <= exit_z:
            return 0
        if z_score >= entry_z:
            return -1
        if z_score <= -entry_z:
            return 1
        return current
