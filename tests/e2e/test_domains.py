from __future__ import annotations

from collections.abc import Iterator
from decimal import Decimal
from pathlib import Path

import pytest

from alpha_foundry.bootstrap import ApplicationFacade, BootstrapSettings, bootstrap
from alpha_foundry.domain.models import (
    AstOperator,
    AstParameter,
    CrossVenueStrategyAst,
    DerivativesStrategyAst,
    Domain,
    EventFundamentalStrategyAst,
    FactorStrategyAst,
    MarketMakingStrategyAst,
    StatArbStrategyAst,
    StrategySpec,
    StructuralFlowStrategyAst,
    TimeSeriesStrategyAst,
)

_AST_TYPES = {
    Domain.FACTOR: FactorStrategyAst,
    Domain.STAT_ARB: StatArbStrategyAst,
    Domain.MARKET_MAKING: MarketMakingStrategyAst,
    Domain.STRUCTURAL_FLOW: StructuralFlowStrategyAst,
    Domain.CROSS_VENUE: CrossVenueStrategyAst,
    Domain.DERIVATIVES: DerivativesStrategyAst,
    Domain.EVENT_FUNDAMENTAL: EventFundamentalStrategyAst,
    Domain.TIME_SERIES: TimeSeriesStrategyAst,
}


_DOMAIN_PLANS = {
    Domain.FACTOR: (
        "ranked_long_short",
        (("long_count", 2), ("short_count", 1)),
        {"long_count": 2, "short_count": 1},
    ),
    Domain.STAT_ARB: (
        "lagged_zscore_spread",
        (("lookback", 4), ("entry_z", Decimal("2")), ("exit_z", Decimal("0.5"))),
        {"lookback": 4, "entry_z": "2", "exit_z": "0.5"},
    ),
    Domain.MARKET_MAKING: (
        "inventory_limited_quote",
        (("quote_size", Decimal("10")), ("inventory_skew", Decimal("0.25"))),
        {"quote_size": "10", "inventory_skew": "0.25"},
    ),
    Domain.STRUCTURAL_FLOW: (
        "decayed_event_impact",
        (("position_scale", Decimal("0.75")),),
        {"position_scale": "0.75"},
    ),
    Domain.CROSS_VENUE: (
        "cross_venue_spread",
        (
            ("route_id", "venue-a-to-b"),
            ("minimum_edge_rate", Decimal("0.002")),
            ("maximum_quantity", Decimal("3")),
        ),
        {
            "route_id": "venue-a-to-b",
            "minimum_edge_rate": "0.002",
            "maximum_quantity": "3",
        },
    ),
    Domain.DERIVATIVES: (
        "lagged_hedge",
        (("signal_scale", Decimal("1.5")), ("underlying_hedge_ratio", Decimal("0.75"))),
        {"signal_scale": "1.5", "underlying_hedge_ratio": "0.75"},
    ),
    Domain.EVENT_FUNDAMENTAL: (
        "event_surprise",
        (
            ("entry_lag_periods", 1),
            ("holding_periods", 2),
            ("signal_threshold", Decimal("0.1")),
            ("position_size", Decimal("0.5")),
            ("maximum_gross_exposure", Decimal("1")),
            ("allow_short", True),
        ),
        {
            "entry_lag_periods": 1,
            "holding_periods": 2,
            "signal_threshold": "0.1",
            "position_size": "0.5",
            "maximum_gross_exposure": "1",
            "allow_short": True,
        },
    ),
    Domain.TIME_SERIES: (
        "rolling_momentum",
        (
            ("lookback_periods", 3),
            ("position_lag_periods", 1),
            ("rebalance_periods", 1),
            ("signal_threshold", Decimal("0.1")),
            ("position_size", Decimal("0.5")),
            ("maximum_gross_exposure", Decimal("1")),
            ("allow_short", True),
        ),
        {
            "lookback_periods": 3,
            "position_lag_periods": 1,
            "rebalance_periods": 1,
            "signal_threshold": "0.1",
            "position_size": "0.5",
            "maximum_gross_exposure": "1",
            "allow_short": True,
        },
    ),
}


@pytest.fixture
def facade(tmp_path: Path) -> Iterator[ApplicationFacade]:
    runtime = bootstrap(
        BootstrapSettings(
            database_path=tmp_path / "metadata.sqlite",
            artifact_root=tmp_path / "artifacts",
            llm_provider="fake",
        )
    )
    try:
        yield runtime
    finally:
        runtime.close()


def _strategy(facade: ApplicationFacade, domain: Domain) -> tuple[StrategySpec, dict[str, object]]:
    operator_id, parameters, expected_plan = _DOMAIN_PLANS[domain]
    lab = facade.lab_registry.get(domain)
    ast_type = _AST_TYPES[domain]
    strategy = StrategySpec(
        strategy_id=f"{domain.value.lower()}-strategy",
        version="1.0.0",
        domain=domain,
        operator_set_version=lab.operator_set_version,
        operator_set_hash=lab.operator_set_hash,
        schema_hash=lab.schema_hash,
        ast=ast_type(
            root=AstOperator(
                kind="operator",
                operator_id=operator_id,
                parameters=tuple(
                    AstParameter(name=name, value=value) for name, value in parameters
                ),
            )
        ),
    )
    return strategy, expected_plan


@pytest.mark.parametrize("domain", tuple(Domain), ids=lambda domain: domain.value.lower())
def test_e2e_domain_facade_compiles_each_minimal_execution_plan_deterministically(
    facade: ApplicationFacade,
    domain: Domain,
) -> None:
    strategy, expected_plan = _strategy(facade, domain)

    first_plan = facade.lab_registry.compile(strategy)
    repeated_plan = facade.lab_registry.compile(strategy)

    assert first_plan.model_dump(mode="json") == expected_plan
    assert repeated_plan == first_plan
