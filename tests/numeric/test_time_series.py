from decimal import Decimal

from alpha_foundry.domain.errors import ErrorCode
from alpha_foundry.domain.models import (
    AstOperator,
    AstParameter,
    DatasetRef,
    Domain,
    ExecutionPolicy,
    ExperimentConfig,
    ExperimentResult,
    ExperimentStatus,
    StrategySpec,
    TimeSeriesStrategyAst,
)
from alpha_foundry.engines.time_series import TimeSeriesEngine
from alpha_foundry.labs.registry import DEFAULT_LAB_REGISTRY

_HASH = "sha256:" + "8" * 64


def _config() -> ExperimentConfig:
    lab = DEFAULT_LAB_REGISTRY.get(Domain.TIME_SERIES)

    strategy = StrategySpec(
        strategy_id="rolling-momentum-strategy",
        version="v1",
        domain=Domain.TIME_SERIES,
        operator_set_version=lab.operator_set_version,
        operator_set_hash=lab.operator_set_hash,
        schema_hash=lab.schema_hash,
        ast=TimeSeriesStrategyAst(
            root=AstOperator(
                kind="operator",
                operator_id="rolling_momentum",
                parameters=(
                    AstParameter(name="lookback_periods", value=2),
                    AstParameter(name="position_lag_periods", value=1),
                    AstParameter(name="rebalance_periods", value=2),
                    AstParameter(name="signal_threshold", value=Decimal("0.05")),
                    AstParameter(name="position_size", value=Decimal("0.5")),
                    AstParameter(name="maximum_gross_exposure", value=Decimal("1")),
                    AstParameter(name="allow_short", value=True),
                ),
            )
        ),
    )
    return ExperimentConfig(
        experiment_id="time-series-experiment",
        strategy=strategy,
        datasets=(
            DatasetRef(
                dataset_id="daily-prices",
                domain=Domain.TIME_SERIES,
                version="v1",
                content_hash=_HASH,
            ),
        ),
        execution_policies=(
            ExecutionPolicy(
                policy_id="time-series-policy",
                domain=Domain.TIME_SERIES,
                version="v1",
                content_hash=_HASH,
            ),
        ),
        schema_hashes=(lab.schema_hash,),
        code_hash=_HASH,
        seed=19,
    )


def _policy() -> dict[str, object]:
    return {
        "lookback_periods": 2,
        "position_lag_periods": 1,
        "rebalance_periods": 2,
        "signal_threshold": Decimal("0.05"),
        "position_size": Decimal("0.5"),
        "maximum_gross_exposure": Decimal("1"),
        "allow_short": True,
        "commission_rate": Decimal("0.01"),
        "slippage_rate": Decimal("0.005"),
        "impact_rate": Decimal("0.002"),
    }


def _data(*, policy: dict[str, object] | None = None) -> dict[str, object]:
    return {
        "domain": Domain.TIME_SERIES,
        "timestamps": (0, 1, 2, 3, 4, 5),
        "prices": (
            Decimal("100"),
            Decimal("110"),
            Decimal("121"),
            Decimal("121"),
            Decimal("108.9"),
            Decimal("108.9"),
        ),
        "price_available_at": (0, 1, 2, 3, 4, 5),
        "policy": policy or _policy(),
    }


def _metrics(result: ExperimentResult) -> dict[str, Decimal]:
    return {metric.name: metric.value for metric in result.metrics}


def test_num_eng_008_time_series_warms_up_lags_rebalances_and_accounts_for_costs() -> None:
    config = _config()
    engine = TimeSeriesEngine()

    costed = engine.run(config, _data())
    costless = engine.run(
        config,
        _data(
            policy={
                **_policy(),
                "commission_rate": Decimal("0"),
                "slippage_rate": Decimal("0"),
                "impact_rate": Decimal("0"),
            }
        ),
    )

    assert costed.status is ExperimentStatus.SUCCEEDED
    assert costless.status is ExperimentStatus.SUCCEEDED
    assert costed == engine.run(config, _data())

    costed_metrics = _metrics(costed)
    assert costed_metrics["net_return_000000"] == Decimal("0")
    assert costed_metrics["net_return_000001"] == Decimal("0")
    assert costed_metrics["net_return_000002"] == Decimal("0")
    assert costed_metrics["net_return_000003"] == Decimal("-0.008")
    assert costed_metrics["net_return_000004"] == Decimal("-0.050")
    assert costed_metrics["net_return_000005"] == Decimal("-0.017")
    assert costed_metrics["lookback_periods"] == Decimal("2")
    assert costed_metrics["position_lag_periods"] == Decimal("1")
    assert costed_metrics["rebalance_periods"] == Decimal("2")
    assert costed_metrics["rebalance_count"] == Decimal("2")
    assert costed_metrics["total_turnover"] == Decimal("1.5")
    assert costed_metrics["total_commission"] == Decimal("0.015")
    assert costed_metrics["total_slippage"] == Decimal("0.0075")
    assert costed_metrics["total_impact"] == Decimal("0.0025")
    assert costed_metrics["total_cost"] == Decimal("0.0250")
    assert costed_metrics["cumulative_gross_return"] == Decimal("-0.050")
    assert costed_metrics["final_equity"] == Decimal("0.9263792")

    costless_metrics = _metrics(costless)
    assert costless_metrics["net_return_000003"] == Decimal("0")
    assert costless_metrics["net_return_000004"] == Decimal("-0.050")
    assert costless_metrics["net_return_000005"] == Decimal("0")
    assert costless_metrics["total_cost"] == Decimal("0")
    assert costless_metrics["final_equity"] == Decimal("0.950")


def test_num_eng_008_time_series_rejects_price_available_after_observation() -> None:
    data = _data()
    data["price_available_at"] = (0, 1, 3, 3, 4, 5)

    result = TimeSeriesEngine().run(_config(), data)

    assert result.status is ExperimentStatus.FAILED
    assert result.reason is not None
    assert result.reason.code is ErrorCode.SCHEMA
    assert result.reason.details[0].path == "price_available_at[2]"


def test_num_eng_008_time_series_rejects_insufficient_history_for_lagged_indicator() -> None:
    data = _data()
    data["timestamps"] = (0, 1, 2)
    data["prices"] = (Decimal("100"), Decimal("110"), Decimal("121"))
    data["price_available_at"] = (0, 1, 2)

    result = TimeSeriesEngine().run(_config(), data)

    assert result.status is ExperimentStatus.FAILED
    assert result.reason is not None
    assert result.reason.code is ErrorCode.SCHEMA
    assert result.reason.details[0].path == "timestamps"
