from decimal import Decimal

import pytest
from pydantic import ValidationError

from alpha_foundry.compiler import compile_strategy
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
from alpha_foundry.labs.registry import DEFAULT_LAB_REGISTRY

_HASH = "sha256:" + "f" * 64
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
    Domain.FACTOR: ("ranked_long_short", (("long_count", 2), ("short_count", 1))),
    Domain.STAT_ARB: (
        "lagged_zscore_spread",
        (("lookback", 4), ("entry_z", Decimal("2")), ("exit_z", Decimal("0.5"))),
    ),
    Domain.MARKET_MAKING: (
        "inventory_limited_quote",
        (("quote_size", Decimal("10")), ("inventory_skew", Decimal("0.25"))),
    ),
    Domain.STRUCTURAL_FLOW: ("decayed_event_impact", (("position_scale", Decimal("0.75")),)),
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


def _strategy(domain: Domain, *, operator_id: str | None = None) -> StrategySpec:
    lab = DEFAULT_LAB_REGISTRY.get(domain)
    approved_operator_id, parameters = _STRATEGY_ROOTS[domain]
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
                operator_id=operator_id or approved_operator_id,
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
        content_hash=_HASH,
        datasets=(
            DatasetRef(
                dataset_id=f"{domain.value.lower()}-dataset",
                domain=domain,
                version="1.0.0",
                content_hash=_HASH,
            ),
        ),
        execution_policies=(
            ExecutionPolicy(
                policy_id=f"{domain.value.lower()}-policy",
                domain=domain,
                version="1.0.0",
                content_hash=_HASH,
            ),
        ),
        schema_hashes=(lab.schema_hash,),
        code_version="1.0.0",
        provider_chain=(
            ProviderModel(
                ordinal=0,
                provider="deterministic-provider",
                model="deterministic-model",
                config_hash=_HASH,
            ),
        ),
    )


@pytest.mark.parametrize("domain", tuple(Domain), ids=lambda domain: domain.value.lower())
def test_ct_compiler_001_rejects_unknown_operators_for_every_domain(domain: Domain) -> None:
    with pytest.raises(DomainError) as rejected:
        compile_strategy(_strategy(domain, operator_id="unapproved_operator"), _capability(domain))

    assert rejected.value.detail.code is ErrorCode.SCHEMA
    assert rejected.value.detail.details[0].path == "strategy.ast.root.operator_id"


def test_ct_compiler_002_rejects_unknown_strategy_fields_at_the_typed_boundary() -> None:
    payload = _strategy(Domain.FACTOR).model_dump(mode="python")
    payload["unreviewed_field"] = "not part of a strategy schema"

    with pytest.raises(ValidationError) as rejected:
        StrategySpec.model_validate(payload)

    assert rejected.value.errors()[0]["loc"] == ("unreviewed_field",)
    assert rejected.value.errors()[0]["type"] == "extra_forbidden"


@pytest.mark.parametrize(
    ("field", "value", "path"),
    (
        ("operator_set_version", "999.0.0", "strategy.operator_set_version"),
        ("operator_set_hash", _HASH, "strategy.operator_set_hash"),
        ("schema_hash", _HASH, "strategy.schema_hash"),
    ),
)
def test_ct_compiler_003_rejects_unreviewed_operator_and_schema_versions(
    field: str, value: str, path: str
) -> None:
    strategy = _strategy(Domain.FACTOR).model_copy(update={field: value})

    with pytest.raises(DomainError) as rejected:
        compile_strategy(strategy, _capability(Domain.FACTOR))

    assert rejected.value.detail.code is ErrorCode.SCHEMA
    assert rejected.value.detail.details[0].path == path


@pytest.mark.parametrize(
    ("field", "path"),
    (
        ("datasets", "capability.datasets"),
        ("execution_policies", "capability.execution_policies"),
        ("schema_hashes", "capability.schema_hashes"),
    ),
)
def test_ct_compiler_004_rejects_each_required_capability_gap(field: str, path: str) -> None:
    capability = _capability(Domain.FACTOR).model_copy(update={field: ()})

    with pytest.raises(DomainError) as rejected:
        compile_strategy(_strategy(Domain.FACTOR), capability)

    assert rejected.value.detail.code is ErrorCode.CAPABILITY
    assert rejected.value.detail.details[0].path == path


def test_ct_compiler_005_strategy_requires_one_matching_primary_domain() -> None:
    factor_payload = _strategy(Domain.FACTOR).model_dump(mode="python")
    factor_payload["ast"] = _strategy(Domain.STAT_ARB).ast

    with pytest.raises(ValidationError) as mismatched_domain:
        StrategySpec.model_validate(factor_payload)

    assert any(
        "strategy domain must match its typed AST envelope" in error["msg"]
        for error in mismatched_domain.value.errors()
    )

    factor_payload = _strategy(Domain.FACTOR).model_dump(mode="python")
    factor_payload["primary_domains"] = (Domain.FACTOR, Domain.STAT_ARB)

    with pytest.raises(ValidationError) as multiple_domains:
        StrategySpec.model_validate(factor_payload)

    assert multiple_domains.value.errors()[0]["loc"] == ("primary_domains",)
    assert multiple_domains.value.errors()[0]["type"] == "extra_forbidden"
