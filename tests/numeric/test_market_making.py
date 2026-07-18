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
    MarketMakingStrategyAst,
    StrategySpec,
)
from alpha_foundry.engines.market_making import MarketMakingEngine

_HASH = "sha256:" + "3" * 64


def _config() -> ExperimentConfig:
    strategy = StrategySpec(
        strategy_id="market-making-strategy",
        version="v1",
        domain=Domain.MARKET_MAKING,
        operator_set_version="v1",
        operator_set_hash=_HASH,
        schema_hash=_HASH,
        ast=MarketMakingStrategyAst(
            root=AstOperator(
                kind="operator",
                operator_id="symmetric_quote",
                parameters=(
                    AstParameter(name="quote_size", value=Decimal("1")),
                    AstParameter(name="inventory_skew", value=Decimal("0")),
                ),
            )
        ),
    )
    return ExperimentConfig(
        experiment_id="market-making-experiment",
        strategy=strategy,
        datasets=(
            DatasetRef(
                dataset_id="top-of-book-events",
                domain=Domain.MARKET_MAKING,
                version="v1",
                content_hash=_HASH,
            ),
        ),
        execution_policies=(
            ExecutionPolicy(
                policy_id="market-making-policy",
                domain=Domain.MARKET_MAKING,
                version="v1",
                content_hash=_HASH,
            ),
        ),
        schema_hashes=(_HASH,),
        code_hash=_HASH,
        seed=13,
    )


def _events() -> tuple[dict[str, object], ...]:
    return (
        {
            "timestamp": 0,
            "bid": Decimal("99"),
            "ask": Decimal("101"),
            "bid_size": Decimal("1"),
            "ask_size": Decimal("1"),
            "buy_volume": Decimal("0"),
            "sell_volume": Decimal("0"),
        },
        {
            "timestamp": 1,
            "bid": Decimal("99"),
            "ask": Decimal("121"),
            "bid_size": Decimal("1"),
            "ask_size": Decimal("1"),
            "buy_volume": Decimal("0"),
            "sell_volume": Decimal("1"),
        },
        {
            "timestamp": 2,
            "bid": Decimal("105"),
            "ask": Decimal("115"),
            "bid_size": Decimal("1"),
            "ask_size": Decimal("1"),
            "buy_volume": Decimal("1"),
            "sell_volume": Decimal("0"),
        },
    )


def _policy() -> dict[str, object]:
    return {
        "latency_events": 1,
        "fill_ratio": Decimal("1"),
        "quote_spread": Decimal("2"),
        "inventory_limit": Decimal("1"),
        "allow_negative_cash": False,
        "commission_rate": Decimal("0.01"),
        "slippage_rate": Decimal("0.02"),
        "impact_rate": Decimal("0.001"),
        "adverse_selection_rate": Decimal("0.005"),
    }


def _data(
    *,
    events: tuple[dict[str, object], ...] | None = None,
    policy: dict[str, object] | None = None,
) -> dict[str, object]:
    return {
        "events": events or _events(),
        "policy": policy or _policy(),
        "quote_size": Decimal("1"),
        "initial_cash": Decimal("1000"),
        "initial_inventory": Decimal("0"),
    }


def _metrics(result: ExperimentResult) -> dict[str, Decimal]:
    return {metric.name: metric.value for metric in result.metrics}


def _failed_path(result: ExperimentResult) -> str:
    assert result.status is ExperimentStatus.FAILED
    assert result.reason is not None
    return result.reason.details[0].path


def test_num_eng_003_market_making_delays_quotes_and_accounts_for_fills_costs_and_inventory() -> (
    None
):
    engine = MarketMakingEngine()
    costed = engine.run(_config(), _data())
    costless_policy = _policy()
    costless_policy.update(
        {
            "commission_rate": Decimal("0"),
            "slippage_rate": Decimal("0"),
            "impact_rate": Decimal("0"),
            "adverse_selection_rate": Decimal("0"),
        }
    )
    costless = engine.run(_config(), _data(policy=costless_policy))

    assert costed.status is ExperimentStatus.SUCCEEDED
    assert costed == engine.run(_config(), _data())
    assert costless.status is ExperimentStatus.SUCCEEDED

    metrics = _metrics(costed)
    assert metrics["bid_fill_000000"] == Decimal("0")
    assert metrics["bid_fill_000001"] == Decimal("1")
    assert metrics["ask_fill_000002"] == Decimal("1")
    assert metrics["inventory_000001"] == Decimal("1")
    assert metrics["inventory_000002"] == Decimal("0")
    assert metrics["cash_000001"] == Decimal("887.734")
    assert metrics["cash_000002"] == Decimal("982.528")
    assert metrics["total_turnover"] == Decimal("210")
    assert metrics["total_commission"] == Decimal("2.10")
    assert metrics["total_slippage"] == Decimal("4.20")
    assert metrics["total_impact"] == Decimal("22.122")
    assert metrics["total_adverse_selection_cost"] == Decimal("1.050")
    assert metrics["total_cost"] == Decimal("29.472")
    assert metrics["final_equity"] == Decimal("982.528")

    costless_metrics = _metrics(costless)
    assert costless_metrics["total_cost"] == Decimal("0")
    assert costless_metrics["final_equity"] == Decimal("1012")


def test_num_eng_003_market_making_does_not_fill_against_the_current_book_when_latency_is_one() -> (
    None
):
    events = (
        _events()[0],
        {
            "timestamp": 1,
            "bid": Decimal("105"),
            "ask": Decimal("115"),
            "bid_size": Decimal("1"),
            "ask_size": Decimal("1"),
            "buy_volume": Decimal("0"),
            "sell_volume": Decimal("1"),
        },
    )

    result = MarketMakingEngine().run(_config(), _data(events=events))

    assert result.status is ExperimentStatus.SUCCEEDED
    metrics = _metrics(result)
    assert metrics["bid_fill_000001"] == Decimal("0")
    assert metrics["total_turnover"] == Decimal("0")
    assert metrics["cash_000001"] == Decimal("1000")
    assert metrics["inventory_000001"] == Decimal("0")


def test_num_eng_003_market_making_rejects_missing_and_non_monotonic_events() -> None:
    missing_events = _data()
    del missing_events["events"]
    duplicate_timestamps = _data(
        events=(
            _events()[0],
            {**_events()[1], "timestamp": 0},
            _events()[2],
        )
    )

    missing_result = MarketMakingEngine().run(_config(), missing_events)
    unordered_result = MarketMakingEngine().run(_config(), duplicate_timestamps)

    assert _failed_path(missing_result) == "events"
    assert _failed_path(unordered_result) == "events.timestamp"
    assert unordered_result.reason is not None
    assert unordered_result.reason.code is ErrorCode.SCHEMA
