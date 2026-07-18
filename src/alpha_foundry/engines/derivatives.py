"""Deterministic lagged-signal derivatives engine with explicit margin cash accounting."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from decimal import Decimal, DecimalException
from typing import Any, Final

from pydantic import ValidationError

from alpha_foundry.domain.errors import DomainError, ErrorCode
from alpha_foundry.domain.models import Domain, ExperimentConfig, ExperimentResult
from alpha_foundry.labs.derivatives import (
    CashShortfallAction,
    CompiledDerivativesStrategy,
    DerivativeObservation,
    DerivativeSignal,
    DerivativesRunPayload,
    MarginCallAction,
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


class DerivativesEngine(BaseEngine):
    """Settle futures variation, funding, borrowing, margin, and lagged target signals."""

    domain = Domain.DERIVATIVES
    engine_id = "derivatives-margin-v1"

    def run(self, config: ExperimentConfig, data: Mapping[str, Any]) -> ExperimentResult:
        """Run futures-plus-underlying cash accounting without executing an unlagged signal."""

        try:
            self.validate_config(config)
            with deterministic_decimal_context():
                compiled = DEFAULT_LAB_REGISTRY.compile(config.strategy)
                if not isinstance(compiled, CompiledDerivativesStrategy):
                    raise EngineInputError(
                        ErrorCode.SCHEMA,
                        "strategy",
                        "Derivatives registry returned an invalid compiled plan",
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
                state = _DerivativesState(self, payload, strategy)
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


class _DerivativesState:
    def __init__(
        self,
        engine: DerivativesEngine,
        payload: DerivativesRunPayload,
        strategy: CompiledDerivativesStrategy,
    ) -> None:
        self._engine = engine
        self._payload = payload
        self._policy = payload.execution_policy
        self._strategy = strategy
        self._free_cash = payload.initial_cash
        self._margin_cash = _ZERO
        self._underlying_units = payload.initial_underlying_units
        self._derivative_contracts = payload.initial_derivative_contracts
        self._prior_derivative_price: Decimal | None = None
        self._scheduled_signals: dict[int, list[DerivativeSignal]] = {}
        self._signal_cursor = 0
        self._variation_pnl = _ZERO
        self._funding_cashflow = _ZERO
        self._borrow_cost = _ZERO
        self._transaction_fees = _ZERO
        self._gross_trade_notional = _ZERO
        self._order_count = 0
        self._rejected_order_count = 0
        self._liquidation_count = 0
        self._unexecuted_signal_count = 0
        self._equity_path: list[Decimal] = []
        self._return_path: list[Decimal] = []
        self._initial_equity: Decimal | None = None

    def execute(self, config: ExperimentConfig) -> ExperimentResult:
        observations = tuple(
            sorted(
                self._payload.observations,
                key=lambda observation: (observation.available_at, observation.observed_at),
            )
        )
        signals = tuple(sorted(self._payload.signals, key=lambda signal: signal.available_at))
        for index, observation in enumerate(observations):
            self._schedule_visible_signals(
                index, observation.available_at, signals, len(observations)
            )
            initial_recorded = self._record_initial_equity(observation)
            self._settle_prior_exposure(observation)
            liquidated = False
            if not self._synchronize_margin(observation):
                self._handle_margin_call(observation)
                liquidated = True
            scheduled_signals = self._scheduled_signals.pop(index, [])
            for signal in scheduled_signals:
                if liquidated:
                    self._rejected_order_count += 1
                    continue
                liquidated = self._rebalance(signal, observation)
            self._prior_derivative_price = observation.derivative_price
            if index > 0 or not initial_recorded or liquidated or scheduled_signals:
                self._record_equity(observation)

        self._unexecuted_signal_count += len(signals) - self._signal_cursor
        if self._initial_equity is None or not self._equity_path:
            raise _resource_error("data.observations", "no marked equity was produced")
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
                    "started_at": observations[0].available_at,
                    "finished_at": observations[-1].available_at,
                },
            },
            input_snapshot=self._payload.model_dump(mode="json"),
            policy_identity={
                "policy_id": self._policy.policy_id,
                "version": self._policy.version,
                "content_hash": self._policy.content_hash,
            },
            started_at=observations[0].available_at,
            finished_at=observations[-1].available_at,
        )

    def _schedule_visible_signals(
        self,
        observation_index: int,
        available_at: datetime,
        signals: tuple[DerivativeSignal, ...],
        observation_count: int,
    ) -> None:
        while self._signal_cursor < len(signals):
            signal = signals[self._signal_cursor]
            if signal.available_at > available_at:
                break
            execution_index = observation_index + self._policy.execution_lag_periods
            if execution_index < observation_count:
                self._scheduled_signals.setdefault(execution_index, []).append(signal)
            else:
                self._unexecuted_signal_count += 1
            self._signal_cursor += 1

    def _settle_prior_exposure(self, observation: DerivativeObservation) -> None:
        if self._prior_derivative_price is None:
            return
        multiplier = self._policy.contract_multiplier
        variation = (
            self._derivative_contracts
            * multiplier
            * (observation.derivative_price - self._prior_derivative_price)
        )
        funding = (
            self._derivative_contracts
            * multiplier
            * observation.derivative_price
            * self._policy.funding_rate_per_period
        )
        borrow = (
            max(-self._underlying_units, _ZERO)
            * observation.underlying_price
            * self._policy.borrow_rate_per_period
        )
        self._apply_settlement(variation - funding - borrow)
        self._variation_pnl += variation
        self._funding_cashflow += funding
        self._borrow_cost += borrow

    def _apply_settlement(self, cashflow: Decimal) -> None:
        """Apply settlement to free cash and posted margin before recording a shortfall."""
        if cashflow >= _ZERO:
            self._free_cash += cashflow
            return
        debit = -cashflow
        free_cash_debit = min(max(self._free_cash, _ZERO), debit)
        self._free_cash -= free_cash_debit
        debit -= free_cash_debit
        margin_debit = min(self._margin_cash, debit)
        self._margin_cash -= margin_debit
        debit -= margin_debit
        if debit > _ZERO:
            self._free_cash -= debit

    def _synchronize_margin(self, observation: DerivativeObservation) -> bool:
        required_margin = self._required_margin(observation.derivative_price)
        if self._margin_cash > required_margin:
            self._free_cash += self._margin_cash - required_margin
            self._margin_cash = required_margin
        elif self._margin_cash < required_margin:
            top_up = min(
                max(self._free_cash, _ZERO),
                required_margin - self._margin_cash,
            )
            self._free_cash -= top_up
            self._margin_cash += top_up
        if self._margin_cash < self._maintenance_margin(observation.derivative_price):
            return False
        self._assert_margin_invariant(observation.derivative_price)
        return True

    def _rebalance(self, signal: DerivativeSignal, observation: DerivativeObservation) -> bool:
        target_contracts = signal.value * self._strategy.signal_scale
        target_underlying_units = -target_contracts * self._strategy.underlying_hedge_ratio
        if target_underlying_units < _ZERO and not self._policy.short_underlying_allowed:
            self._rejected_order_count += 1
            return False
        derivative_delta = target_contracts - self._derivative_contracts
        underlying_delta = target_underlying_units - self._underlying_units
        derivative_notional = (
            abs(derivative_delta) * self._policy.contract_multiplier * observation.derivative_price
        )
        underlying_notional = abs(underlying_delta) * observation.underlying_price
        derivative_fee = derivative_notional * self._policy.derivative_transaction_cost_rate
        underlying_fee = underlying_notional * self._policy.underlying_transaction_cost_rate
        prospective_free_cash = (
            self._free_cash
            - underlying_delta * observation.underlying_price
            - derivative_fee
            - underlying_fee
        )
        prospective_margin = (
            abs(target_contracts)
            * self._policy.contract_multiplier
            * observation.derivative_price
            * self._policy.initial_margin_rate
        )
        if prospective_margin >= self._margin_cash:
            prospective_free_cash -= prospective_margin - self._margin_cash
        else:
            prospective_free_cash += self._margin_cash - prospective_margin
        if prospective_free_cash < _ZERO:
            return self._handle_order_shortfall(observation)

        self._free_cash = prospective_free_cash
        self._margin_cash = prospective_margin
        self._derivative_contracts = target_contracts
        self._underlying_units = target_underlying_units
        self._gross_trade_notional += derivative_notional + underlying_notional
        self._transaction_fees += derivative_fee + underlying_fee
        self._order_count += 1
        self._assert_margin_invariant(observation.derivative_price)
        return False

    def _handle_order_shortfall(self, observation: DerivativeObservation) -> bool:
        match self._policy.cash_shortfall_action:
            case CashShortfallAction.REJECT_ORDER:
                self._rejected_order_count += 1
                return False
            case CashShortfallAction.LIQUIDATE:
                self._liquidate(observation)
                return True
            case _:
                raise _policy_error("execution_policy.cash_shortfall_action", "is unsupported")

    def _handle_margin_call(self, observation: DerivativeObservation) -> None:
        match self._policy.margin_call_action:
            case MarginCallAction.LIQUIDATE:
                self._liquidate(observation)
            case _:
                raise _policy_error("execution_policy.margin_call_action", "is unsupported")

    def _liquidate(self, observation: DerivativeObservation) -> None:
        derivative_notional = (
            abs(self._derivative_contracts)
            * self._policy.contract_multiplier
            * observation.derivative_price
        )
        underlying_notional = abs(self._underlying_units) * observation.underlying_price
        derivative_fee = derivative_notional * self._policy.derivative_transaction_cost_rate
        underlying_fee = underlying_notional * self._policy.underlying_transaction_cost_rate
        self._free_cash += self._underlying_units * observation.underlying_price
        self._free_cash -= derivative_fee + underlying_fee
        self._free_cash += self._margin_cash
        self._gross_trade_notional += derivative_notional + underlying_notional
        self._transaction_fees += derivative_fee + underlying_fee
        self._derivative_contracts = _ZERO
        self._underlying_units = _ZERO
        self._margin_cash = _ZERO
        self._liquidation_count += 1

    def _required_margin(self, derivative_price: Decimal) -> Decimal:
        return (
            abs(self._derivative_contracts)
            * self._policy.contract_multiplier
            * derivative_price
            * self._policy.initial_margin_rate
        )

    def _maintenance_margin(self, derivative_price: Decimal) -> Decimal:
        return (
            abs(self._derivative_contracts)
            * self._policy.contract_multiplier
            * derivative_price
            * self._policy.maintenance_margin_rate
        )

    def _assert_margin_invariant(self, derivative_price: Decimal) -> None:
        required_margin = self._required_margin(derivative_price)
        if self._margin_cash < _ZERO or self._margin_cash > required_margin:
            raise _policy_error(
                "engine.margin",
                "margin cash must be non-negative and no greater than required initial margin",
            )

    def _record_initial_equity(self, observation: DerivativeObservation) -> bool:
        if self._initial_equity is not None:
            return False
        self._record_equity(observation)
        return True

    def _record_equity(self, observation: DerivativeObservation) -> None:
        equity = (
            self._free_cash
            + self._margin_cash
            + self._underlying_units * observation.underlying_price
        )
        if equity <= _ZERO:
            raise EngineInputError(
                ErrorCode.VALIDATION,
                "accounting.equity",
                "must remain positive after every period",
            )
        if self._initial_equity is None:
            period_return = _ZERO
            self._initial_equity = equity
        else:
            prior_equity = self._equity_path[-1]
            period_return = equity / prior_equity - _ONE
        self._equity_path.append(equity)
        self._return_path.append(period_return)

    def _result_metrics(self, final_equity: Decimal) -> tuple[tuple[str, Decimal], ...]:
        if self._initial_equity is None:
            raise _resource_error("engine.equity", "initial equity was not recorded")
        metrics: list[tuple[str, Decimal]] = [
            ("initial_equity", self._initial_equity),
            ("final_equity", final_equity),
            ("net_pnl", final_equity - self._initial_equity),
            ("net_return", final_equity / self._initial_equity - _ONE),
            ("max_drawdown", max_drawdown(self._equity_path)),
            ("variation_pnl", self._variation_pnl),
            ("funding_cashflow", self._funding_cashflow),
            ("borrow_cost", self._borrow_cost),
            ("transaction_fees", self._transaction_fees),
            ("gross_trade_notional", self._gross_trade_notional),
            ("final_margin_cash", self._margin_cash),
            ("final_underlying_units", self._underlying_units),
            ("final_derivative_contracts", self._derivative_contracts),
            ("order_count", Decimal(self._order_count)),
            ("rejected_order_count", Decimal(self._rejected_order_count)),
            ("liquidation_count", Decimal(self._liquidation_count)),
            ("unexecuted_signal_count", Decimal(self._unexecuted_signal_count)),
        ]
        metrics.extend(
            (f"net_return_{index:06d}", value) for index, value in enumerate(self._return_path)
        )
        metrics.extend(
            (f"equity_{index:06d}", value) for index, value in enumerate(self._equity_path)
        )
        return tuple(metrics)


def _parse_payload(data: Mapping[str, Any]) -> DerivativesRunPayload:
    try:
        return DerivativesRunPayload.model_validate(data)
    except ValidationError as error:
        raise payload_validation_error(error) from error


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
