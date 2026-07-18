from datetime import UTC, datetime
from decimal import Decimal

import pytest
from pydantic import ValidationError

from alpha_foundry.domain.errors import DomainError, ErrorCode
from alpha_foundry.domain.models import (
    AstOperator,
    AstParameter,
    CrossVenueStrategyAst,
    DerivativesStrategyAst,
    Domain,
    StrategySpec,
)
from alpha_foundry.labs.cross_venue import (
    CrossVenueExecutionPolicy,
    CrossVenueQuestionPayload,
    CrossVenueRunPayload,
    SameTimestampPriority,
)
from alpha_foundry.labs.registry import DEFAULT_LAB_REGISTRY


def compile_cross_venue_strategy(strategy: StrategySpec) -> object:
    return DEFAULT_LAB_REGISTRY.get(Domain.CROSS_VENUE).compile(strategy)


_HASH = "sha256:" + "6" * 64
_START = datetime(2024, 1, 1, tzinfo=UTC)


def _execution_policy() -> CrossVenueExecutionPolicy:
    return CrossVenueExecutionPolicy(
        policy_id="cross-venue-policy",
        version="v1",
        content_hash=_HASH,
        venue_clocks=(
            {
                "venue_id": "alpha",
                "utc_offset_ms": 0,
                "market_data_latency_ms": 0,
                "maximum_quote_age_ms": 100,
            },
            {
                "venue_id": "beta",
                "utc_offset_ms": 0,
                "market_data_latency_ms": 0,
                "maximum_quote_age_ms": 100,
            },
        ),
        venue_costs=(
            {"venue_id": "alpha", "taker_fee_rate": Decimal("0"), "fixed_fee": Decimal("0")},
            {"venue_id": "beta", "taker_fee_rate": Decimal("0"), "fixed_fee": Decimal("0")},
        ),
        venue_fills=(
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
        routes=(
            {
                "route_id": "alpha-to-beta",
                "buy_venue_id": "alpha",
                "sell_venue_id": "beta",
                "buy_order_latency_ms": 0,
                "sell_order_latency_ms": 0,
                "maximum_open_orders": 1,
            },
        ),
        same_timestamp_priority=SameTimestampPriority.QUOTE_BEFORE_ORDER,
        short_sales_allowed=False,
    )


def _payload() -> dict[str, object]:
    return {
        "quotes": (
            {
                "venue_id": "alpha",
                "observed_at": _START,
                "bid": Decimal("99"),
                "ask": Decimal("100"),
                "bid_size": Decimal("1"),
                "ask_size": Decimal("1"),
            },
            {
                "venue_id": "beta",
                "observed_at": _START,
                "bid": Decimal("101"),
                "ask": Decimal("102"),
                "bid_size": Decimal("1"),
                "ask_size": Decimal("1"),
            },
        ),
        "execution_policy": _execution_policy(),
        "initial_cash": (
            {"venue_id": "alpha", "cash": Decimal("100")},
            {"venue_id": "beta", "cash": Decimal("100")},
        ),
        "initial_positions": (
            {"venue_id": "alpha", "quantity": Decimal("0")},
            {"venue_id": "beta", "quantity": Decimal("1")},
        ),
    }


def _cross_venue_strategy(operator_id: str = "cross_venue_spread") -> StrategySpec:
    lab = DEFAULT_LAB_REGISTRY.get(Domain.CROSS_VENUE)

    return StrategySpec(
        strategy_id="cross-venue-strategy",
        version="v1",
        domain=Domain.CROSS_VENUE,
        operator_set_version=lab.operator_set_version,
        operator_set_hash=lab.operator_set_hash,
        schema_hash=lab.schema_hash,
        ast=CrossVenueStrategyAst(
            root=AstOperator(
                kind="operator",
                operator_id=operator_id,
                parameters=(
                    AstParameter(name="route_id", value="alpha-to-beta"),
                    AstParameter(name="minimum_edge_rate", value=Decimal("0.01")),
                    AstParameter(name="maximum_quantity", value=Decimal("1")),
                ),
            )
        ),
    )


def _derivatives_strategy() -> StrategySpec:
    lab = DEFAULT_LAB_REGISTRY.get(Domain.DERIVATIVES)

    return StrategySpec(
        strategy_id="derivatives-strategy",
        version="v1",
        domain=Domain.DERIVATIVES,
        operator_set_version=lab.operator_set_version,
        operator_set_hash=lab.operator_set_hash,
        schema_hash=lab.schema_hash,
        ast=DerivativesStrategyAst(
            root=AstOperator(
                kind="operator",
                operator_id="lagged_hedge",
                parameters=(
                    AstParameter(name="signal_scale", value=Decimal("1")),
                    AstParameter(name="underlying_hedge_ratio", value=Decimal("1")),
                ),
            )
        ),
    )


def test_ct_cross_venue_001_payload_is_domain_typed_and_rejects_decimal_coercion() -> None:
    payload = CrossVenueRunPayload(domain=Domain.CROSS_VENUE, **_payload())

    assert payload.domain is Domain.CROSS_VENUE
    assert payload.execution_policy.routes[0].route_id == "alpha-to-beta"

    with pytest.raises(ValidationError) as wrong_domain:
        CrossVenueRunPayload(domain=Domain.DERIVATIVES, **_payload())
    assert wrong_domain.value.errors()[0]["loc"] == ("domain",)
    assert wrong_domain.value.errors()[0]["type"] == "literal_error"

    invalid = _payload()
    invalid["initial_cash"] = (
        {"venue_id": "alpha", "cash": "100"},
        {"venue_id": "beta", "cash": Decimal("100")},
    )
    with pytest.raises(ValidationError) as decimal_coercion:
        CrossVenueRunPayload(domain=Domain.CROSS_VENUE, **invalid)
    assert decimal_coercion.value.errors()[0]["loc"] == ("initial_cash", 0, "cash")
    assert decimal_coercion.value.errors()[0]["type"] == "is_instance_of"


@pytest.mark.parametrize(
    ("payload", "rejected_field"),
    (
        (
            {
                "domain": Domain.CROSS_VENUE,
                "research_id": "research-1",
                "instrument_id": "asset-1",
                "venue_ids": ("alpha", "beta"),
                "python_code": "__import__('os').system('whoami')",
            },
            "python_code",
        ),
        (
            {
                "domain": Domain.CROSS_VENUE,
                "research_id": "research-1",
                "instrument_id": "asset-1",
                "venue_ids": ("alpha", "beta"),
                "derivative_instrument_id": "future-1",
            },
            "derivative_instrument_id",
        ),
    ),
)
def test_ct_cross_venue_002_question_payload_rejects_code_and_cross_domain_fields(
    payload: dict[str, object], rejected_field: str
) -> None:
    with pytest.raises(ValidationError) as error:
        CrossVenueQuestionPayload(**payload)

    assert error.value.errors()[0]["loc"] == (rejected_field,)
    assert error.value.errors()[0]["type"] == "extra_forbidden"


def test_ct_cross_venue_003_compiler_allows_only_declared_cross_venue_data_operators() -> None:
    plan = compile_cross_venue_strategy(_cross_venue_strategy())

    assert plan.model_dump() == {
        "route_id": "alpha-to-beta",
        "minimum_edge_rate": Decimal("0.01"),
        "maximum_quantity": Decimal("1"),
    }

    with pytest.raises(DomainError) as arbitrary_operator:
        compile_cross_venue_strategy(_cross_venue_strategy(operator_id="python_exec"))
    assert arbitrary_operator.value.detail.code is ErrorCode.SCHEMA
    assert arbitrary_operator.value.detail.details[0].path == "strategy.ast.root.operator_id"

    with pytest.raises(DomainError) as cross_domain:
        compile_cross_venue_strategy(_derivatives_strategy())
    assert cross_domain.value.detail.code is ErrorCode.DOMAIN
    assert cross_domain.value.detail.details[0].path == "strategy.domain"


@pytest.mark.parametrize(
    ("field", "path"),
    (
        ("operator_set_version", "strategy.operator_set_version"),
        ("operator_set_hash", "strategy.operator_set_hash"),
        ("schema_hash", "strategy.schema_hash"),
    ),
)
def test_ct_cross_venue_004_compiler_rejects_mismatched_lab_identity(field: str, path: str) -> None:
    strategy = _cross_venue_strategy()
    current_value = getattr(strategy, field)
    replacement = (
        f"{current_value}-unreviewed"
        if field == "operator_set_version"
        else current_value[:-1] + ("0" if current_value[-1] != "0" else "1")
    )

    with pytest.raises(DomainError) as mismatched_identity:
        compile_cross_venue_strategy(strategy.model_copy(update={field: replacement}))

    assert mismatched_identity.value.detail.code is ErrorCode.SCHEMA
    assert mismatched_identity.value.detail.details[0].path == path


def test_ct_cross_venue_005_compiler_rejects_duplicate_parameter_names() -> None:
    strategy = _cross_venue_strategy()
    root = strategy.ast.root
    assert isinstance(root, AstOperator)
    duplicate_strategy = strategy.model_copy(
        update={
            "ast": strategy.ast.model_copy(
                update={
                    "root": root.model_copy(
                        update={"parameters": (*root.parameters, root.parameters[0])}
                    )
                }
            )
        }
    )

    with pytest.raises(DomainError) as duplicate_parameter:
        compile_cross_venue_strategy(duplicate_strategy)

    assert duplicate_parameter.value.detail.code is ErrorCode.SCHEMA
    assert (
        duplicate_parameter.value.detail.details[0].path == "strategy.ast.root.parameters[3].name"
    )
