from datetime import UTC, datetime, timedelta
from decimal import Decimal

from alpha_foundry.domain.errors import ErrorCode
from alpha_foundry.domain.models import (
    AstOperator,
    AstParameter,
    DatasetRef,
    DerivativesStrategyAst,
    Domain,
    ExecutionPolicy,
    ExperimentConfig,
    ExperimentResult,
    ExperimentStatus,
    StrategySpec,
)
from alpha_foundry.engines.derivatives import DerivativesEngine
from alpha_foundry.labs.derivatives import CashShortfallAction, MarginCallAction
from alpha_foundry.labs.registry import DEFAULT_LAB_REGISTRY

_HASH = "sha256:" + "5" * 64
_START = datetime(2024, 1, 1, tzinfo=UTC)


def _config() -> ExperimentConfig:
    lab = DEFAULT_LAB_REGISTRY.get(Domain.DERIVATIVES)

    return ExperimentConfig(
        experiment_id="derivatives-experiment",
        strategy=StrategySpec(
            strategy_id="derivatives-strategy",
            version="v1",
            domain=Domain.DERIVATIVES,
            operator_set_version=lab.operator_set_version,
            operator_set_hash=lab.operator_set_hash,
            schema_hash=lab.schema_hash,
            ast=DerivativesStrategyAst(
                root=AstOperator(
                    kind="operator",
                    operator_id="lagged_hedge",
                    parameters=(
                        AstParameter(name="signal_scale", value=Decimal("1")),
                        AstParameter(name="underlying_hedge_ratio", value=Decimal("1")),
                    ),
                )
            ),
        ),
        datasets=(
            DatasetRef(
                dataset_id="derivative-prices",
                domain=Domain.DERIVATIVES,
                version="v1",
                content_hash=_HASH,
            ),
        ),
        execution_policies=(
            ExecutionPolicy(
                policy_id="derivatives-policy",
                domain=Domain.DERIVATIVES,
                version="v1",
                content_hash=_HASH,
            ),
        ),
        schema_hashes=(lab.schema_hash,),
        code_hash=_HASH,
        seed=17,
    )


def _policy() -> dict[str, object]:
    return {
        "policy_id": "derivatives-policy",
        "version": "v1",
        "content_hash": _HASH,
        "contract_multiplier": Decimal("10"),
        "funding_rate_per_period": Decimal("0.01"),
        "borrow_rate_per_period": Decimal("0.02"),
        "initial_margin_rate": Decimal("0.1"),
        "maintenance_margin_rate": Decimal("0.05"),
        "derivative_transaction_cost_rate": Decimal("0.01"),
        "underlying_transaction_cost_rate": Decimal("0.005"),
        "execution_lag_periods": 1,
        "cash_shortfall_action": CashShortfallAction.REJECT_ORDER,
        "margin_call_action": MarginCallAction.LIQUIDATE,
        "short_underlying_allowed": True,
    }


def _data() -> dict[str, object]:
    return {
        "domain": Domain.DERIVATIVES,
        "observations": (
            {
                "observed_at": _START,
                "available_at": _START,
                "underlying_price": Decimal("100"),
                "derivative_price": Decimal("100"),
            },
            {
                "observed_at": _START + timedelta(minutes=1),
                "available_at": _START + timedelta(minutes=1),
                "underlying_price": Decimal("110"),
                "derivative_price": Decimal("120"),
            },
            {
                "observed_at": _START + timedelta(minutes=2),
                "available_at": _START + timedelta(minutes=2),
                "underlying_price": Decimal("105"),
                "derivative_price": Decimal("130"),
            },
        ),
        "signals": ({"available_at": _START, "value": Decimal("1")},),
        "execution_policy": _policy(),
        "initial_cash": Decimal("1000"),
        "initial_underlying_units": Decimal("0"),
        "initial_derivative_contracts": Decimal("0"),
    }


def _metrics(result: ExperimentResult) -> dict[str, Decimal]:
    return {metric.name: metric.value for metric in result.metrics}


def test_num_eng_006_derivatives_lags_signals_and_accounts_for_multiplier_funding_borrow_and_margin() -> (
    None
):
    engine = DerivativesEngine()
    result = engine.run(_config(), _data())

    assert result.status is ExperimentStatus.SUCCEEDED
    assert result == engine.run(_config(), _data())
    assert result.started_at == _START
    assert result.finished_at == _START + timedelta(minutes=2)

    metrics = _metrics(result)
    assert metrics["initial_equity"] == Decimal("1000")
    assert metrics["net_return_000000"] == Decimal("0")
    assert metrics["net_return_000001"] == Decimal("-0.01255")
    assert metrics["final_equity"] == Decimal("1077.35")
    assert metrics["net_pnl"] == Decimal("77.35")
    assert metrics["variation_pnl"] == Decimal("100")
    assert metrics["funding_cashflow"] == Decimal("13")
    assert metrics["borrow_cost"] == Decimal("2.10")
    assert metrics["transaction_fees"] == Decimal("12.55")
    assert metrics["gross_trade_notional"] == Decimal("1310")
    assert metrics["final_margin_cash"] == Decimal("130")
    assert metrics["final_underlying_units"] == Decimal("-1")
    assert metrics["final_derivative_contracts"] == Decimal("1")
    assert metrics["order_count"] == Decimal("1")
    assert metrics["rejected_order_count"] == Decimal("0")
    assert metrics["liquidation_count"] == Decimal("0")


def test_num_eng_006_derivatives_never_executes_a_signal_that_arrives_without_a_future_lagged_observation() -> (
    None
):
    data = _data()
    data["signals"] = ({"available_at": _START + timedelta(minutes=2), "value": Decimal("1")},)

    result = DerivativesEngine().run(_config(), data)

    assert result.status is ExperimentStatus.SUCCEEDED
    metrics = _metrics(result)
    assert metrics["final_equity"] == Decimal("1000")
    assert metrics["final_margin_cash"] == Decimal("0")
    assert metrics["final_underlying_units"] == Decimal("0")
    assert metrics["final_derivative_contracts"] == Decimal("0")
    assert metrics["order_count"] == Decimal("0")
    assert metrics["unexecuted_signal_count"] == Decimal("1")


def test_num_eng_006_derivatives_rejects_an_invalid_execution_policy() -> None:
    data = _data()
    data["execution_policy"] = {**_policy(), "execution_lag_periods": 0}

    result = DerivativesEngine().run(_config(), data)

    assert result.status is ExperimentStatus.FAILED
    assert result.reason is not None
    assert result.reason.code is ErrorCode.POLICY
    assert result.reason.details[0].path == "execution_policy.execution_lag_periods"
