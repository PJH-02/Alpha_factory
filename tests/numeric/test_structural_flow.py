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
    StructuralFlowStrategyAst,
)
from alpha_foundry.engines.structural_flow import StructuralFlowEngine

_HASH = "sha256:" + "4" * 64


def _config() -> ExperimentConfig:
    strategy = StrategySpec(
        strategy_id="structural-flow-strategy",
        version="v1",
        domain=Domain.STRUCTURAL_FLOW,
        operator_set_version="v1",
        operator_set_hash=_HASH,
        schema_hash=_HASH,
        ast=StructuralFlowStrategyAst(
            root=AstOperator(
                kind="operator",
                operator_id="decayed_event_impact",
                parameters=(AstParameter(name="position_scale", value=Decimal("1")),),
            )
        ),
    )
    return ExperimentConfig(
        experiment_id="structural-flow-experiment",
        strategy=strategy,
        datasets=(
            DatasetRef(
                dataset_id="structural-events",
                domain=Domain.STRUCTURAL_FLOW,
                version="v1",
                content_hash=_HASH,
            ),
        ),
        execution_policies=(
            ExecutionPolicy(
                policy_id="structural-flow-policy",
                domain=Domain.STRUCTURAL_FLOW,
                version="v1",
                content_hash=_HASH,
            ),
        ),
        schema_hashes=(_HASH,),
        code_hash=_HASH,
        seed=17,
    )


def _policy() -> dict[str, object]:
    return {
        "execution_lag_periods": 1,
        "impact_multiplier": Decimal("1"),
        "decay_rate": Decimal("0.5"),
        "maximum_position": Decimal("5"),
        "allow_negative_cash": False,
        "commission_rate": Decimal("0.01"),
        "slippage_rate": Decimal("0.02"),
        "impact_rate": Decimal("0.001"),
    }


def _data(
    *,
    timestamps: tuple[int, ...] = (0, 1, 2, 3),
    prices: tuple[Decimal, ...] = (
        Decimal("100"),
        Decimal("110"),
        Decimal("120"),
        Decimal("130"),
    ),
    events: tuple[dict[str, object], ...] | None = None,
    policy: dict[str, object] | None = None,
) -> dict[str, object]:
    return {
        "timestamps": timestamps,
        "prices": prices,
        "events": events
        or (
            {
                "event_at": 0,
                "available_at": 2,
                "impact": Decimal("2"),
            },
        ),
        "policy": policy or _policy(),
        "position_scale": Decimal("1"),
        "initial_cash": Decimal("1000"),
        "initial_position": Decimal("0"),
    }


def _metrics(result: ExperimentResult) -> dict[str, Decimal]:
    return {metric.name: metric.value for metric in result.metrics}


def _failed_path(result: ExperimentResult) -> str:
    assert result.status is ExperimentStatus.FAILED
    assert result.reason is not None
    return result.reason.details[0].path


def test_num_eng_004_structural_flow_respects_event_availability_lag_decay_and_accounting() -> None:
    engine = StructuralFlowEngine()
    result = engine.run(_config(), _data())
    zero_cost_policy = _policy()
    zero_cost_policy.update(
        {
            "commission_rate": Decimal("0"),
            "slippage_rate": Decimal("0"),
            "impact_rate": Decimal("0"),
        }
    )
    costless = engine.run(_config(), _data(policy=zero_cost_policy))
    no_decay_policy = _policy()
    no_decay_policy["decay_rate"] = Decimal("0")
    no_decay = engine.run(_config(), _data(policy=no_decay_policy))

    assert result.status is ExperimentStatus.SUCCEEDED
    assert result == engine.run(_config(), _data())
    assert costless.status is ExperimentStatus.SUCCEEDED
    assert no_decay.status is ExperimentStatus.SUCCEEDED

    metrics = _metrics(result)
    assert metrics["event_signal_000000"] == Decimal("0")
    assert metrics["event_signal_000001"] == Decimal("0")
    assert metrics["event_signal_000002"] == Decimal("2")
    assert metrics["event_signal_000003"] == Decimal("1.0")
    assert metrics["desired_position_000002"] == Decimal("2")
    assert metrics["position_000002"] == Decimal("0")
    assert metrics["position_000003"] == Decimal("2")
    assert metrics["cash_000003"] == Decimal("664.600")
    assert metrics["total_turnover"] == Decimal("260")
    assert metrics["total_commission"] == Decimal("2.60")
    assert metrics["total_slippage"] == Decimal("5.20")
    assert metrics["total_impact"] == Decimal("67.600")
    assert metrics["total_cost"] == Decimal("75.400")
    assert metrics["final_equity"] == Decimal("924.600")

    assert _metrics(costless)["total_cost"] == Decimal("0")
    assert _metrics(costless)["final_equity"] == Decimal("1000")
    assert _metrics(no_decay)["event_signal_000003"] == Decimal("2")


def test_num_eng_004_structural_flow_rejects_missing_non_monotonic_and_pre_available_events() -> (
    None
):
    missing_prices = _data()
    del missing_prices["prices"]
    duplicate_timestamps = _data(timestamps=(0, 1, 1, 3))
    impossible_availability = _data(
        events=(
            {
                "event_at": 1,
                "available_at": 0,
                "impact": Decimal("2"),
            },
        )
    )

    missing_result = StructuralFlowEngine().run(_config(), missing_prices)
    unordered_result = StructuralFlowEngine().run(_config(), duplicate_timestamps)
    availability_result = StructuralFlowEngine().run(_config(), impossible_availability)

    assert _failed_path(missing_result) == "prices"
    assert _failed_path(unordered_result) == "timestamps"
    assert _failed_path(availability_result) == "events[0].available_at"
    assert availability_result.reason is not None
    assert availability_result.reason.code is ErrorCode.SCHEMA
