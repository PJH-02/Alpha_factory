from decimal import Decimal

import pytest

from alpha_foundry.domain.errors import DomainError, ErrorCode
from alpha_foundry.domain.models import (
    AstOperator,
    AstParameter,
    CapabilitySnapshot,
    CrossVenueStrategyAst,
    DatasetRef,
    DerivativesStrategyAst,
    Domain,
    EventFundamentalStrategyAst,
    ExecutionPolicy,
    FactorStrategyAst,
    MarketMakingStrategyAst,
    ProviderModel,
    StatArbStrategyAst,
    StrategySpec,
    StructuralFlowStrategyAst,
    TimeSeriesStrategyAst,
)
from alpha_foundry.labs.registry import (
    DEFAULT_LAB_REGISTRY,
    LabRegistry,
    validate_compilation_capability,
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

_STRATEGY_ROOTS = {
    Domain.FACTOR: (
        "ranked_long_short",
        (("long_count", 2), ("short_count", 1)),
    ),
    Domain.STAT_ARB: (
        "lagged_zscore_spread",
        (("lookback", 4), ("entry_z", Decimal("2")), ("exit_z", Decimal("0.5"))),
    ),
    Domain.MARKET_MAKING: (
        "inventory_limited_quote",
        (("quote_size", Decimal("10")), ("inventory_skew", Decimal("0.25"))),
    ),
    Domain.STRUCTURAL_FLOW: (
        "decayed_event_impact",
        (("position_scale", Decimal("0.75")),),
    ),
    Domain.CROSS_VENUE: (
        "cross_venue_spread",
        (
            ("route_id", "venue-a-to-b"),
            ("minimum_edge_rate", Decimal("0.002")),
            ("maximum_quantity", Decimal("3")),
        ),
    ),
    Domain.DERIVATIVES: (
        "lagged_hedge",
        (("signal_scale", Decimal("1.5")), ("underlying_hedge_ratio", Decimal("0.75"))),
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
    ),
}

_EXPECTED_PLANS = {
    Domain.FACTOR: {"long_count": 2, "short_count": 1},
    Domain.STAT_ARB: {
        "lookback": 4,
        "entry_z": Decimal("2"),
        "exit_z": Decimal("0.5"),
    },
    Domain.MARKET_MAKING: {
        "quote_size": Decimal("10"),
        "inventory_skew": Decimal("0.25"),
    },
    Domain.STRUCTURAL_FLOW: {"position_scale": Decimal("0.75")},
    Domain.CROSS_VENUE: {
        "route_id": "venue-a-to-b",
        "minimum_edge_rate": Decimal("0.002"),
        "maximum_quantity": Decimal("3"),
    },
    Domain.DERIVATIVES: {
        "signal_scale": Decimal("1.5"),
        "underlying_hedge_ratio": Decimal("0.75"),
    },
    Domain.EVENT_FUNDAMENTAL: {
        "entry_lag_periods": 1,
        "holding_periods": 2,
        "signal_threshold": Decimal("0.1"),
        "position_size": Decimal("0.5"),
        "maximum_gross_exposure": Decimal("1"),
        "allow_short": True,
    },
    Domain.TIME_SERIES: {
        "lookback_periods": 3,
        "position_lag_periods": 1,
        "rebalance_periods": 1,
        "signal_threshold": Decimal("0.1"),
        "position_size": Decimal("0.5"),
        "maximum_gross_exposure": Decimal("1"),
        "allow_short": True,
    },
}
_CAPABILITY_HASH = "sha256:" + ("f" * 64)


def _strategy(domain: Domain) -> StrategySpec:
    lab = DEFAULT_LAB_REGISTRY.get(domain)
    operator_id, parameters = _STRATEGY_ROOTS[domain]
    ast_type = _AST_TYPES[domain]

    return StrategySpec(
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


def _capability(domain: Domain) -> CapabilitySnapshot:
    lab = DEFAULT_LAB_REGISTRY.get(domain)

    return CapabilitySnapshot(
        capability_snapshot_id=f"{domain.value.lower()}-capability",
        content_hash=_CAPABILITY_HASH,
        datasets=(
            DatasetRef(
                dataset_id=f"{domain.value.lower()}-dataset",
                domain=domain,
                version="1.0.0",
                content_hash=_CAPABILITY_HASH,
            ),
        ),
        execution_policies=(
            ExecutionPolicy(
                policy_id=f"{domain.value.lower()}-policy",
                domain=domain,
                version="1.0.0",
                content_hash=_CAPABILITY_HASH,
            ),
        ),
        schema_hashes=(lab.schema_hash,),
        code_version="1.0.0",
        provider_chain=(
            ProviderModel(
                ordinal=0,
                provider="deterministic-provider",
                model="deterministic-model",
                config_hash=_CAPABILITY_HASH,
            ),
        ),
    )


def test_ct_registry_001_registry_has_each_approved_primary_domain_once() -> None:
    registry_domains = DEFAULT_LAB_REGISTRY.domains

    assert registry_domains == tuple(Domain)
    assert tuple(DEFAULT_LAB_REGISTRY.get(domain).domain for domain in registry_domains) == tuple(
        Domain
    )
    assert len({id(DEFAULT_LAB_REGISTRY.get(domain)) for domain in registry_domains}) == len(Domain)


@pytest.mark.parametrize("domain", tuple(Domain), ids=lambda domain: domain.value.lower())
def test_ct_registry_002_each_registered_lab_compiles_its_typed_strategy(domain: Domain) -> None:
    compiled_plan = DEFAULT_LAB_REGISTRY.compile(_strategy(domain))

    assert compiled_plan.model_dump() == _EXPECTED_PLANS[domain]


@pytest.mark.parametrize(
    ("field", "replacement", "path"),
    (
        ("operator_set_version", "unreviewed-version", "strategy.operator_set_version"),
        ("operator_set_hash", _CAPABILITY_HASH, "strategy.operator_set_hash"),
        ("schema_hash", _CAPABILITY_HASH, "strategy.schema_hash"),
    ),
)
def test_ct_registry_003_rejects_unpinned_compilation_identities(
    field: str, replacement: str, path: str
) -> None:
    lab = DEFAULT_LAB_REGISTRY.get(Domain.FACTOR)
    strategy = _strategy(Domain.FACTOR).model_copy(update={field: replacement})

    with pytest.raises(DomainError) as rejected:
        validate_compilation_capability(strategy, _capability(Domain.FACTOR), lab)

    assert rejected.value.detail.code is ErrorCode.SCHEMA
    assert rejected.value.detail.details[0].path == path


def test_ct_registry_004_rejects_duplicate_and_missing_domain_registration() -> None:
    factor_lab = DEFAULT_LAB_REGISTRY.get(Domain.FACTOR)

    with pytest.raises(ValueError, match="duplicate lab registration for FACTOR"):
        LabRegistry((factor_lab, factor_lab))

    with pytest.raises(ValueError, match="missing: FACTOR"):
        LabRegistry(
            DEFAULT_LAB_REGISTRY.get(domain) for domain in Domain if domain is not Domain.FACTOR
        )


def test_ct_registry_005_rejects_code_like_operator_before_compilation() -> None:
    strategy = _strategy(Domain.FACTOR).model_copy(
        update={
            "ast": FactorStrategyAst(
                root=AstOperator(kind="operator", operator_id="eval", parameters=())
            )
        }
    )

    with pytest.raises(DomainError) as rejected:
        DEFAULT_LAB_REGISTRY.compile(strategy)

    assert rejected.value.detail.code is ErrorCode.SCHEMA
    assert rejected.value.detail.details[0].path == "strategy.ast.root.operator_id"


def test_ct_registry_006_rejects_an_unapproved_strategy_ast_subclass() -> None:
    class UnapprovedFactorStrategyAst(FactorStrategyAst):
        pass

    strategy = _strategy(Domain.FACTOR)
    strategy = strategy.model_copy(
        update={"ast": UnapprovedFactorStrategyAst(root=strategy.ast.root)}
    )

    with pytest.raises(DomainError) as rejected:
        DEFAULT_LAB_REGISTRY.compile(strategy)

    assert rejected.value.detail.code is ErrorCode.DOMAIN
    assert rejected.value.detail.details[0].path == "strategy.ast"
