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
from alpha_foundry.labs.derivatives import (
    CashShortfallAction,
    DerivativesExecutionPolicy,
    DerivativesQuestionPayload,
    DerivativesRunPayload,
    MarginCallAction,
)
from alpha_foundry.labs.registry import DEFAULT_LAB_REGISTRY


def compile_derivatives_strategy(strategy: StrategySpec) -> object:
    return DEFAULT_LAB_REGISTRY.get(Domain.DERIVATIVES).compile(strategy)


_HASH = "sha256:" + "7" * 64
_START = datetime(2024, 1, 1, tzinfo=UTC)


def _execution_policy() -> DerivativesExecutionPolicy:
    return DerivativesExecutionPolicy(
        policy_id="derivatives-policy",
        version="v1",
        content_hash=_HASH,
        contract_multiplier=Decimal("10"),
        funding_rate_per_period=Decimal("0.01"),
        borrow_rate_per_period=Decimal("0.02"),
        initial_margin_rate=Decimal("0.1"),
        maintenance_margin_rate=Decimal("0.05"),
        derivative_transaction_cost_rate=Decimal("0.001"),
        underlying_transaction_cost_rate=Decimal("0.002"),
        execution_lag_periods=1,
        cash_shortfall_action=CashShortfallAction.REJECT_ORDER,
        margin_call_action=MarginCallAction.LIQUIDATE,
        short_underlying_allowed=True,
    )


def _payload() -> dict[str, object]:
    return {
        "observations": (
            {
                "observed_at": _START,
                "available_at": _START,
                "underlying_price": Decimal("100"),
                "derivative_price": Decimal("101"),
            },
        ),
        "signals": ({"available_at": _START, "value": Decimal("1")},),
        "execution_policy": _execution_policy(),
        "initial_cash": Decimal("1000"),
        "initial_underlying_units": Decimal("0"),
        "initial_derivative_contracts": Decimal("0"),
    }


def _derivatives_strategy(operator_id: str = "lagged_hedge") -> StrategySpec:
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
                operator_id=operator_id,
                parameters=(
                    AstParameter(name="signal_scale", value=Decimal("1")),
                    AstParameter(name="underlying_hedge_ratio", value=Decimal("1")),
                ),
            )
        ),
    )


def _cross_venue_strategy() -> StrategySpec:
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
                operator_id="cross_venue_spread",
                parameters=(
                    AstParameter(name="route_id", value="alpha-to-beta"),
                    AstParameter(name="minimum_edge_rate", value=Decimal("0.01")),
                    AstParameter(name="maximum_quantity", value=Decimal("1")),
                ),
            )
        ),
    )


def test_ct_derivatives_001_payload_is_domain_typed_and_rejects_decimal_coercion() -> None:
    payload = DerivativesRunPayload(domain=Domain.DERIVATIVES, **_payload())

    assert payload.domain is Domain.DERIVATIVES
    assert payload.execution_policy.contract_multiplier == Decimal("10")

    with pytest.raises(ValidationError) as wrong_domain:
        DerivativesRunPayload(domain=Domain.CROSS_VENUE, **_payload())
    assert wrong_domain.value.errors()[0]["loc"] == ("domain",)
    assert wrong_domain.value.errors()[0]["type"] == "literal_error"

    invalid = _payload()
    invalid["initial_cash"] = "1000"
    with pytest.raises(ValidationError) as decimal_coercion:
        DerivativesRunPayload(domain=Domain.DERIVATIVES, **invalid)
    assert decimal_coercion.value.errors()[0]["loc"] == ("initial_cash",)
    assert decimal_coercion.value.errors()[0]["type"] == "is_instance_of"


def test_ct_derivatives_002_policy_rejects_an_inverted_margin_requirement() -> None:
    with pytest.raises(ValidationError) as error:
        DerivativesExecutionPolicy(
            policy_id="derivatives-policy",
            version="v1",
            content_hash=_HASH,
            contract_multiplier=Decimal("10"),
            funding_rate_per_period=Decimal("0"),
            borrow_rate_per_period=Decimal("0"),
            initial_margin_rate=Decimal("0.05"),
            maintenance_margin_rate=Decimal("0.10"),
            derivative_transaction_cost_rate=Decimal("0"),
            underlying_transaction_cost_rate=Decimal("0"),
            execution_lag_periods=1,
            cash_shortfall_action=CashShortfallAction.REJECT_ORDER,
            margin_call_action=MarginCallAction.LIQUIDATE,
            short_underlying_allowed=False,
        )

    assert error.value.errors()[0]["loc"] == ()
    assert "maintenance margin rate" in error.value.errors()[0]["msg"]


@pytest.mark.parametrize(
    ("payload", "rejected_field"),
    (
        (
            {
                "domain": Domain.DERIVATIVES,
                "research_id": "research-1",
                "underlying_instrument_id": "spot-1",
                "derivative_instrument_id": "future-1",
                "contract_currency": "USD",
                "shell_command": "curl https://example.invalid",
            },
            "shell_command",
        ),
        (
            {
                "domain": Domain.DERIVATIVES,
                "research_id": "research-1",
                "underlying_instrument_id": "spot-1",
                "derivative_instrument_id": "future-1",
                "contract_currency": "USD",
                "venue_ids": ("alpha", "beta"),
            },
            "venue_ids",
        ),
    ),
)
def test_ct_derivatives_003_question_payload_rejects_code_and_cross_domain_fields(
    payload: dict[str, object], rejected_field: str
) -> None:
    with pytest.raises(ValidationError) as error:
        DerivativesQuestionPayload(**payload)

    assert error.value.errors()[0]["loc"] == (rejected_field,)
    assert error.value.errors()[0]["type"] == "extra_forbidden"


def test_ct_derivatives_004_compiler_allows_only_declared_derivatives_data_operators() -> None:
    plan = compile_derivatives_strategy(_derivatives_strategy())

    assert plan.model_dump() == {
        "signal_scale": Decimal("1"),
        "underlying_hedge_ratio": Decimal("1"),
    }

    with pytest.raises(DomainError) as arbitrary_operator:
        compile_derivatives_strategy(_derivatives_strategy(operator_id="python_exec"))
    assert arbitrary_operator.value.detail.code is ErrorCode.SCHEMA
    assert arbitrary_operator.value.detail.details[0].path == "strategy.ast.root.operator_id"

    with pytest.raises(DomainError) as cross_domain:
        compile_derivatives_strategy(_cross_venue_strategy())
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
def test_ct_derivatives_005_compiler_rejects_mismatched_lab_identity(field: str, path: str) -> None:
    strategy = _derivatives_strategy()
    current_value = getattr(strategy, field)
    replacement = (
        f"{current_value}-unreviewed"
        if field == "operator_set_version"
        else current_value[:-1] + ("0" if current_value[-1] != "0" else "1")
    )

    with pytest.raises(DomainError) as mismatched_identity:
        compile_derivatives_strategy(strategy.model_copy(update={field: replacement}))

    assert mismatched_identity.value.detail.code is ErrorCode.SCHEMA
    assert mismatched_identity.value.detail.details[0].path == path


def test_ct_derivatives_006_compiler_rejects_duplicate_parameter_names() -> None:
    strategy = _derivatives_strategy()
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
        compile_derivatives_strategy(duplicate_strategy)

    assert duplicate_parameter.value.detail.code is ErrorCode.SCHEMA
    assert (
        duplicate_parameter.value.detail.details[0].path == "strategy.ast.root.parameters[2].name"
    )
