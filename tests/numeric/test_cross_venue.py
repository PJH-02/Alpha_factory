from datetime import UTC, datetime, timedelta
from decimal import Decimal

from alpha_foundry.domain.errors import ErrorCode
from alpha_foundry.domain.models import (
    AstOperator,
    AstParameter,
    CrossVenueStrategyAst,
    DatasetRef,
    Domain,
    ExecutionPolicy,
    ExperimentConfig,
    ExperimentResult,
    ExperimentStatus,
    StrategySpec,
)
from alpha_foundry.engines.cross_venue import CrossVenueEngine
from alpha_foundry.labs.cross_venue import SameTimestampPriority
from alpha_foundry.labs.registry import DEFAULT_LAB_REGISTRY

_HASH = "sha256:" + "4" * 64
_START = datetime(2024, 1, 1, tzinfo=UTC)


def _config(route_id: str = "alpha-to-beta") -> ExperimentConfig:
    lab = DEFAULT_LAB_REGISTRY.get(Domain.CROSS_VENUE)

    return ExperimentConfig(
        experiment_id="cross-venue-experiment",
        strategy=StrategySpec(
            strategy_id="cross-venue-strategy",
            version="v1",
            domain=Domain.CROSS_VENUE,
            operator_set_version=lab.operator_set_version,
            operator_set_hash=lab.operator_set_hash,
            schema_hash=lab.schema_hash,
            ast=CrossVenueStrategyAst(
                root=AstOperator(
                    kind="operator",
                    operator_id="cross_venue_spread",
                    parameters=(
                        AstParameter(name="route_id", value=route_id),
                        AstParameter(name="minimum_edge_rate", value=Decimal("0.05")),
                        AstParameter(name="maximum_quantity", value=Decimal("4")),
                    ),
                )
            ),
        ),
        datasets=(
            DatasetRef(
                dataset_id="cross-venue-quotes",
                domain=Domain.CROSS_VENUE,
                version="v1",
                content_hash=_HASH,
            ),
        ),
        execution_policies=(
            ExecutionPolicy(
                policy_id="cross-venue-policy",
                domain=Domain.CROSS_VENUE,
                version="v1",
                content_hash=_HASH,
            ),
        ),
        schema_hashes=(lab.schema_hash,),
        code_hash=_HASH,
        seed=13,
    )


def _data(*, costed: bool = True) -> dict[str, object]:
    alpha_fee_rate = Decimal("0.01") if costed else Decimal("0")
    beta_fee_rate = Decimal("0.02") if costed else Decimal("0")
    alpha_fixed_fee = Decimal("1") if costed else Decimal("0")
    beta_fixed_fee = Decimal("2") if costed else Decimal("0")
    return {
        "domain": Domain.CROSS_VENUE,
        "quotes": (
            {
                "venue_id": "alpha",
                "observed_at": _START,
                "bid": Decimal("99"),
                "ask": Decimal("100"),
                "bid_size": Decimal("8"),
                "ask_size": Decimal("8"),
            },
            {
                "venue_id": "beta",
                "observed_at": _START,
                "bid": Decimal("110"),
                "ask": Decimal("111"),
                "bid_size": Decimal("8"),
                "ask_size": Decimal("8"),
            },
        ),
        "execution_policy": {
            "policy_id": "cross-venue-policy",
            "version": "v1",
            "content_hash": _HASH,
            "venue_clocks": (
                {
                    "venue_id": "alpha",
                    "utc_offset_ms": 100,
                    "market_data_latency_ms": 100,
                    "maximum_quote_age_ms": 100,
                },
                {
                    "venue_id": "beta",
                    "utc_offset_ms": 0,
                    "market_data_latency_ms": 200,
                    "maximum_quote_age_ms": 100,
                },
            ),
            "venue_costs": (
                {
                    "venue_id": "alpha",
                    "taker_fee_rate": alpha_fee_rate,
                    "fixed_fee": alpha_fixed_fee,
                },
                {
                    "venue_id": "beta",
                    "taker_fee_rate": beta_fee_rate,
                    "fixed_fee": beta_fixed_fee,
                },
            ),
            "venue_fills": (
                {
                    "venue_id": "alpha",
                    "participation_rate": Decimal("0.5"),
                    "fill_ratio": Decimal("0.5"),
                },
                {
                    "venue_id": "beta",
                    "participation_rate": Decimal("0.5"),
                    "fill_ratio": Decimal("1"),
                },
            ),
            "routes": (
                {
                    "route_id": "alpha-to-beta",
                    "buy_venue_id": "alpha",
                    "sell_venue_id": "beta",
                    "buy_order_latency_ms": 0,
                    "sell_order_latency_ms": 25,
                    "maximum_open_orders": 1,
                },
            ),
            "same_timestamp_priority": SameTimestampPriority.QUOTE_BEFORE_ORDER,
            "short_sales_allowed": False,
        },
        "initial_cash": (
            {"venue_id": "alpha", "cash": Decimal("1000")},
            {"venue_id": "beta", "cash": Decimal("1000")},
        ),
        "initial_positions": (
            {"venue_id": "alpha", "quantity": Decimal("0")},
            {"venue_id": "beta", "quantity": Decimal("4")},
        ),
    }


def _metrics(result: ExperimentResult) -> dict[str, Decimal]:
    return {metric.name: metric.value for metric in result.metrics}


def test_num_eng_005_cross_venue_synchronizes_clocks_and_accounts_for_latency_fills_and_costs() -> (
    None
):
    engine = CrossVenueEngine()
    costed = engine.run(_config(), _data())
    costless = engine.run(_config(), _data(costed=False))

    assert costed.status is ExperimentStatus.SUCCEEDED
    assert costed == engine.run(_config(), _data())
    assert costed.started_at == _START + timedelta(milliseconds=200)
    assert costed.finished_at == _START + timedelta(milliseconds=225)

    metrics = _metrics(costed)
    assert metrics["initial_equity"] == Decimal("2440")
    assert metrics["final_equity"] == Decimal("2428.6")
    assert metrics["net_pnl"] == Decimal("-11.4")
    assert metrics["gross_notional"] == Decimal("420")
    assert metrics["fees"] == Decimal("9.4")
    assert metrics["filled_buy_quantity"] == Decimal("2")
    assert metrics["filled_sell_quantity"] == Decimal("2")
    assert metrics["decision_count"] == Decimal("1")
    assert metrics["order_count"] == Decimal("2")

    costless_metrics = _metrics(costless)
    assert costless.status is ExperimentStatus.SUCCEEDED
    assert costless_metrics["fees"] == Decimal("0")
    assert costless_metrics["final_equity"] == Decimal("2438")
    assert costless_metrics["final_equity"] - metrics["final_equity"] == Decimal("9.4")


def test_num_eng_005_cross_venue_does_not_use_a_future_quote_before_it_is_available() -> None:
    data = _data(costed=False)
    data["quotes"] = (
        {
            "venue_id": "alpha",
            "observed_at": _START,
            "bid": Decimal("99"),
            "ask": Decimal("100"),
            "bid_size": Decimal("4"),
            "ask_size": Decimal("4"),
        },
        {
            "venue_id": "beta",
            "observed_at": _START + timedelta(milliseconds=20),
            "bid": Decimal("110"),
            "ask": Decimal("111"),
            "bid_size": Decimal("4"),
            "ask_size": Decimal("4"),
        },
    )
    data["execution_policy"] = {
        "policy_id": "cross-venue-policy",
        "version": "v1",
        "content_hash": _HASH,
        "venue_clocks": (
            {
                "venue_id": "alpha",
                "utc_offset_ms": 0,
                "market_data_latency_ms": 0,
                "maximum_quote_age_ms": 10,
            },
            {
                "venue_id": "beta",
                "utc_offset_ms": 0,
                "market_data_latency_ms": 0,
                "maximum_quote_age_ms": 100,
            },
        ),
        "venue_costs": (
            {"venue_id": "alpha", "taker_fee_rate": Decimal("0"), "fixed_fee": Decimal("0")},
            {"venue_id": "beta", "taker_fee_rate": Decimal("0"), "fixed_fee": Decimal("0")},
        ),
        "venue_fills": (
            {
                "venue_id": "alpha",
                "participation_rate": Decimal("1"),
                "fill_ratio": Decimal("1"),
            },
            {
                "venue_id": "beta",
                "participation_rate": Decimal("1"),
                "fill_ratio": Decimal("1"),
            },
        ),
        "routes": (
            {
                "route_id": "alpha-to-beta",
                "buy_venue_id": "alpha",
                "sell_venue_id": "beta",
                "buy_order_latency_ms": 0,
                "sell_order_latency_ms": 0,
                "maximum_open_orders": 1,
            },
        ),
        "same_timestamp_priority": SameTimestampPriority.QUOTE_BEFORE_ORDER,
        "short_sales_allowed": False,
    }
    data["initial_positions"] = (
        {"venue_id": "alpha", "quantity": Decimal("0")},
        {"venue_id": "beta", "quantity": Decimal("0")},
    )

    result = CrossVenueEngine().run(_config(), data)

    assert result.status is ExperimentStatus.SUCCEEDED
    assert result.finished_at == _START + timedelta(milliseconds=20)
    metrics = _metrics(result)
    assert metrics["final_equity"] == Decimal("2000")
    assert metrics["decision_count"] == Decimal("0")
    assert metrics["order_count"] == Decimal("0")
    assert metrics["filled_buy_quantity"] == Decimal("0")
    assert metrics["filled_sell_quantity"] == Decimal("0")


def test_num_eng_005_cross_venue_rejects_a_strategy_route_absent_from_its_policy() -> None:
    result = CrossVenueEngine().run(_config(route_id="unknown-route"), _data())

    assert result.status is ExperimentStatus.FAILED
    assert result.reason is not None
    assert result.reason.code is ErrorCode.POLICY
    assert result.reason.details[0].path == "strategy.ast.root.parameters.route_id"
