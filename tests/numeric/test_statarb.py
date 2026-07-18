from decimal import Decimal

from alpha_foundry.domain.models import (
    AstOperator,
    AstParameter,
    DatasetRef,
    Domain,
    ExecutionPolicy,
    ExperimentConfig,
    ExperimentResult,
    ExperimentStatus,
    StatArbStrategyAst,
    StrategySpec,
)
from alpha_foundry.engines.statarb import StatArbEngine

_HASH = "sha256:" + "1" * 64


def _config() -> ExperimentConfig:
    strategy = StrategySpec(
        strategy_id="statarb-strategy",
        version="v1",
        domain=Domain.STAT_ARB,
        operator_set_version="v1",
        operator_set_hash=_HASH,
        schema_hash=_HASH,
        ast=StatArbStrategyAst(
            root=AstOperator(
                kind="operator",
                operator_id="lagged_zscore_spread",
                parameters=(
                    AstParameter(name="lookback", value=2),
                    AstParameter(name="entry_z", value=Decimal("2")),
                    AstParameter(name="exit_z", value=Decimal("0.5")),
                ),
            )
        ),
    )
    return ExperimentConfig(
        experiment_id="statarb-experiment",
        strategy=strategy,
        datasets=(
            DatasetRef(
                dataset_id="pair-panel",
                domain=Domain.STAT_ARB,
                version="v1",
                content_hash=_HASH,
            ),
        ),
        execution_policies=(
            ExecutionPolicy(
                policy_id="statarb-policy",
                domain=Domain.STAT_ARB,
                version="v1",
                content_hash=_HASH,
            ),
        ),
        schema_hashes=(_HASH,),
        code_hash=_HASH,
        seed=11,
    )


def _data(*, policy: dict[str, Decimal] | None = None) -> dict[str, object]:
    return {
        "timestamps": (0, 1, 2, 3),
        "leg_ids": ("first-leg", "second-leg"),
        "prices": (
            (Decimal("100"), Decimal("100")),
            (Decimal("102"), Decimal("100")),
            (Decimal("110"), Decimal("100")),
            (Decimal("121"), Decimal("90")),
        ),
        "price_available_at": ((0, 0), (1, 1), (2, 2), (3, 3)),
        "hedge_weights": (Decimal("1"), Decimal("-1")),
        "lookback": 2,
        "entry_z": Decimal("2"),
        "exit_z": Decimal("0.5"),
        "policy": policy
        or {
            "commission_rate": Decimal("0.01"),
            "slippage_rate": Decimal("0.005"),
            "impact_rate": Decimal("0.002"),
        },
    }


def _metrics(result: ExperimentResult) -> dict[str, Decimal]:
    return {metric.name: metric.value for metric in result.metrics}


def test_num_eng_002_statarb_lags_zscore_and_accounts_for_both_legs() -> None:
    config = _config()
    engine = StatArbEngine()

    costed = engine.run(config, _data())
    costless = engine.run(
        config,
        _data(
            policy={
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
    assert costed_metrics["net_return_000003"] == Decimal("-0.117")
    assert costed_metrics["z_observation_count"] == Decimal("2")
    assert costed_metrics["entry_count"] == Decimal("1")
    assert costed_metrics["trade_count"] == Decimal("1")
    assert costed_metrics["total_turnover"] == Decimal("1")
    assert costed_metrics["total_commission"] == Decimal("0.01")
    assert costed_metrics["total_slippage"] == Decimal("0.005")
    assert costed_metrics["total_impact"] == Decimal("0.002")
    assert costed_metrics["total_cost"] == Decimal("0.017")
    assert costed_metrics["cumulative_gross_return"] == Decimal("-0.10")
    assert costed_metrics["final_equity"] == Decimal("0.883")

    costless_metrics = _metrics(costless)
    assert costless_metrics["net_return_000003"] == Decimal("-0.10")
    assert costless_metrics["total_cost"] == Decimal("0")
    assert costless_metrics["final_equity"] == Decimal("0.90")
