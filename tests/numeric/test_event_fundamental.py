from decimal import Decimal

from alpha_foundry.domain.errors import ErrorCode
from alpha_foundry.domain.models import (
    AstOperator,
    AstParameter,
    DatasetRef,
    Domain,
    EventFundamentalStrategyAst,
    ExecutionPolicy,
    ExperimentConfig,
    ExperimentResult,
    ExperimentStatus,
    StrategySpec,
)
from alpha_foundry.engines.event_fundamental import EventFundamentalEngine
from alpha_foundry.labs.registry import DEFAULT_LAB_REGISTRY

_HASH = "sha256:" + "7" * 64


def _config() -> ExperimentConfig:
    lab = DEFAULT_LAB_REGISTRY.get(Domain.EVENT_FUNDAMENTAL)

    strategy = StrategySpec(
        strategy_id="event-surprise-strategy",
        version="v1",
        domain=Domain.EVENT_FUNDAMENTAL,
        operator_set_version=lab.operator_set_version,
        operator_set_hash=lab.operator_set_hash,
        schema_hash=lab.schema_hash,
        ast=EventFundamentalStrategyAst(
            root=AstOperator(
                kind="operator",
                operator_id="event_surprise",
                parameters=(
                    AstParameter(name="entry_lag_periods", value=1),
                    AstParameter(name="holding_periods", value=2),
                    AstParameter(name="signal_threshold", value=Decimal("0.5")),
                    AstParameter(name="position_size", value=Decimal("0.5")),
                    AstParameter(name="maximum_gross_exposure", value=Decimal("1")),
                    AstParameter(name="allow_short", value=False),
                ),
            )
        ),
    )
    return ExperimentConfig(
        experiment_id="event-fundamental-experiment",
        strategy=strategy,
        datasets=(
            DatasetRef(
                dataset_id="event-feed",
                domain=Domain.EVENT_FUNDAMENTAL,
                version="v1",
                content_hash=_HASH,
            ),
        ),
        execution_policies=(
            ExecutionPolicy(
                policy_id="event-policy",
                domain=Domain.EVENT_FUNDAMENTAL,
                version="v1",
                content_hash=_HASH,
            ),
        ),
        schema_hashes=(lab.schema_hash,),
        code_hash=_HASH,
        seed=17,
    )


def _policy(*, maximum_gross_exposure: Decimal = Decimal("1")) -> dict[str, object]:
    return {
        "entry_lag_periods": 1,
        "holding_periods": 2,
        "signal_threshold": Decimal("0.5"),
        "position_size": Decimal("0.5"),
        "maximum_gross_exposure": maximum_gross_exposure,
        "allow_short": False,
        "commission_rate": Decimal("0.01"),
        "slippage_rate": Decimal("0.005"),
        "impact_rate": Decimal("0.002"),
    }


def _data(*, policy: dict[str, object] | None = None) -> dict[str, object]:
    return {
        "domain": Domain.EVENT_FUNDAMENTAL,
        "timestamps": (0, 1, 2, 3, 4, 5),
        "returns": (
            Decimal("0"),
            Decimal("0.90"),
            Decimal("0.80"),
            Decimal("0.10"),
            Decimal("-0.20"),
            Decimal("0"),
        ),
        "events": (
            {
                "event_id": "earnings-1",
                "published_at": 0,
                "available_at": 2,
                "surprise": Decimal("1"),
            },
        ),
        "policy": policy or _policy(),
    }


def _metrics(result: ExperimentResult) -> dict[str, Decimal]:
    return {metric.name: metric.value for metric in result.metrics}


def test_num_eng_007_event_waits_for_availability_lag_holding_and_costs() -> None:
    config = _config()
    engine = EventFundamentalEngine()

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
    assert costed_metrics["net_return_000003"] == Decimal("0.042")
    assert costed_metrics["net_return_000004"] == Decimal("-0.10")
    assert costed_metrics["net_return_000005"] == Decimal("-0.008")
    assert costed_metrics["scheduled_event_count"] == Decimal("1")
    assert costed_metrics["entry_lag_periods"] == Decimal("1")
    assert costed_metrics["holding_periods"] == Decimal("2")
    assert costed_metrics["total_turnover"] == Decimal("1.0")
    assert costed_metrics["total_commission"] == Decimal("0.010")
    assert costed_metrics["total_slippage"] == Decimal("0.0050")
    assert costed_metrics["total_impact"] == Decimal("0.0010")
    assert costed_metrics["total_cost"] == Decimal("0.0160")
    assert costed_metrics["cumulative_gross_return"] == Decimal("-0.055")
    assert costed_metrics["final_equity"] == Decimal("0.9302976")

    costless_metrics = _metrics(costless)
    assert costless_metrics["net_return_000003"] == Decimal("0.050")
    assert costless_metrics["net_return_000004"] == Decimal("-0.10")
    assert costless_metrics["net_return_000005"] == Decimal("0")
    assert costless_metrics["total_cost"] == Decimal("0")
    assert costless_metrics["final_equity"] == Decimal("0.9450")


def test_num_eng_007_event_rejects_availability_before_publication() -> None:
    data = _data()
    data["events"] = (
        {
            "event_id": "revised-before-publication",
            "published_at": 2,
            "available_at": 1,
            "surprise": Decimal("1"),
        },
    )

    result = EventFundamentalEngine().run(_config(), data)

    assert result.status is ExperimentStatus.FAILED
    assert result.reason is not None
    assert result.reason.code is ErrorCode.SCHEMA
    assert result.reason.details[0].path == "events[0].available_at"


def test_num_eng_007_event_rejects_overlapping_positions_above_gross_cap() -> None:
    data = _data(policy=_policy(maximum_gross_exposure=Decimal("0.75")))
    data["events"] = (
        {
            "event_id": "earnings-1",
            "published_at": 0,
            "available_at": 2,
            "surprise": Decimal("1"),
        },
        {
            "event_id": "earnings-2",
            "published_at": 1,
            "available_at": 2,
            "surprise": Decimal("1"),
        },
    )

    result = EventFundamentalEngine().run(_config(), data)

    assert result.status is ExperimentStatus.FAILED
    assert result.reason is not None
    assert result.reason.code is ErrorCode.POLICY
    assert result.reason.details[0].path == "accounting.gross_exposure[3]"
