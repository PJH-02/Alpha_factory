from decimal import Decimal

from hypothesis import given
from hypothesis import strategies as st

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
    FactorStrategyAst,
    StrategySpec,
)
from alpha_foundry.engines.factor import FactorEngine

_HASH = "sha256:" + "0" * 64


def _config() -> ExperimentConfig:
    strategy = StrategySpec(
        strategy_id="factor-strategy",
        version="v1",
        domain=Domain.FACTOR,
        operator_set_version="v1",
        operator_set_hash=_HASH,
        schema_hash=_HASH,
        ast=FactorStrategyAst(
            root=AstOperator(
                kind="operator",
                operator_id="ranked_long_short",
                parameters=(
                    AstParameter(name="long_count", value=1),
                    AstParameter(name="short_count", value=1),
                ),
            )
        ),
    )
    return ExperimentConfig(
        experiment_id="factor-experiment",
        strategy=strategy,
        datasets=(
            DatasetRef(
                dataset_id="factor-panel",
                domain=Domain.FACTOR,
                version="v1",
                content_hash=_HASH,
            ),
        ),
        execution_policies=(
            ExecutionPolicy(
                policy_id="factor-policy",
                domain=Domain.FACTOR,
                version="v1",
                content_hash=_HASH,
            ),
        ),
        schema_hashes=(_HASH,),
        code_hash=_HASH,
        seed=7,
    )


def _data(*, policy: dict[str, Decimal] | None = None) -> dict[str, object]:
    return {
        "timestamps": (0, 1, 2),
        "asset_ids": ("A", "B"),
        "signals": (
            (Decimal("10"), Decimal("0")),
            (Decimal("0"), Decimal("10")),
            (Decimal("0"), Decimal("0")),
        ),
        "returns": (
            (Decimal("0.90"), Decimal("-0.90")),
            (Decimal("0.10"), Decimal("-0.20")),
            (Decimal("0.20"), Decimal("0.30")),
        ),
        "signal_available_at": ((0, 0), (1, 1), (2, 2)),
        "long_count": 1,
        "short_count": 1,
        "policy": policy
        or {
            "commission_rate": Decimal("0.01"),
            "slippage_rate": Decimal("0.005"),
            "impact_rate": Decimal("0.002"),
        },
    }


def _metrics(result: ExperimentResult) -> dict[str, Decimal]:
    return {metric.name: metric.value for metric in result.metrics}


def test_num_eng_001_factor_lags_signals_and_applies_each_explicit_cost() -> None:
    config = _config()
    engine = FactorEngine()

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
    assert costed_metrics["net_return_000001"] == Decimal("0.262")
    assert costed_metrics["net_return_000002"] == Decimal("0.008")
    assert costed_metrics["total_turnover"] == Decimal("6")
    assert costed_metrics["total_commission"] == Decimal("0.06")
    assert costed_metrics["total_slippage"] == Decimal("0.03")
    assert costed_metrics["total_impact"] == Decimal("0.040")
    assert costed_metrics["total_cost"] == Decimal("0.130")
    assert costed_metrics["cumulative_gross_return"] == Decimal("0.43")
    assert costed_metrics["final_equity"] == Decimal("1.272096")

    costless_metrics = _metrics(costless)
    assert costless_metrics["net_return_000001"] == Decimal("0.30")
    assert costless_metrics["net_return_000002"] == Decimal("0.10")
    assert costless_metrics["total_cost"] == Decimal("0")
    assert costless_metrics["final_equity"] == Decimal("1.430")


@given(st.integers(min_value=1, max_value=10_000))
def test_num_eng_001_factor_rejects_non_monotonic_timestamps(first_timestamp: int) -> None:
    data = _data()
    data["timestamps"] = (first_timestamp, first_timestamp, first_timestamp + 1)

    result = FactorEngine().run(_config(), data)

    assert result.status is ExperimentStatus.FAILED
    assert result.reason is not None
    assert result.reason.code is ErrorCode.SCHEMA
    assert result.reason.details[0].path == "timestamps"


def test_num_eng_001_factor_rejects_missing_return_panel() -> None:
    data = _data()
    del data["returns"]

    result = FactorEngine().run(_config(), data)

    assert result.status is ExperimentStatus.FAILED
    assert result.reason is not None
    assert result.reason.code is ErrorCode.SCHEMA
    assert result.reason.details[0].path == "returns"
