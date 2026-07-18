from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from pydantic import ValidationError

from alpha_foundry.domain.errors import DomainError, ErrorCode
from alpha_foundry.domain.models import (
    AstOperator,
    AstParameter,
    Domain,
    MarketMakingStrategyAst,
    StrategySpec,
    StructuralFlowStrategyAst,
)
from alpha_foundry.labs.structural_flow import (
    StructuralFlowEvent,
    StructuralFlowExecutionPolicy,
    StructuralFlowObservation,
    StructuralFlowQuestionPayload,
    StructuralFlowRunPayload,
    compile_structural_flow_strategy,
)

_HASH = "sha256:" + "6" * 64
_START = datetime(2024, 1, 1, tzinfo=UTC)


def _execution_policy() -> StructuralFlowExecutionPolicy:
    return StructuralFlowExecutionPolicy(
        execution_lag_periods=1,
        impact_multiplier=Decimal("1.5"),
        decay_rate=Decimal("0.2"),
        maximum_position=Decimal("3"),
        allow_negative_cash=False,
        commission_rate=Decimal("0.001"),
        slippage_rate=Decimal("0.002"),
        impact_rate=Decimal("0.003"),
    )


def _run_payload() -> dict[str, object]:
    return {
        "observations": (
            StructuralFlowObservation(timestamp=_START, price=Decimal("100")),
            StructuralFlowObservation(timestamp=_START + timedelta(days=1), price=Decimal("101")),
        ),
        "events": (
            StructuralFlowEvent(
                event_at=_START,
                available_at=_START + timedelta(hours=1),
                impact=Decimal("2"),
            ),
        ),
        "execution_policy": _execution_policy(),
        "position_scale": Decimal("1"),
        "initial_cash": Decimal("1000"),
        "initial_position": Decimal("0"),
    }


def _structural_flow_strategy(operator_id: str = "decayed_event_impact") -> StrategySpec:
    return StrategySpec(
        strategy_id="structural-flow-strategy",
        version="v1",
        domain=Domain.STRUCTURAL_FLOW,
        operator_set_version="v1",
        operator_set_hash=_HASH,
        schema_hash=_HASH,
        ast=StructuralFlowStrategyAst(
            root=AstOperator(
                kind="operator",
                operator_id=operator_id,
                parameters=(AstParameter(name="position_scale", value=Decimal("1")),),
            )
        ),
    )


def _market_making_strategy() -> StrategySpec:
    return StrategySpec(
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


def test_ct_dsl_001_structural_flow_payloads_are_strictly_typed_and_domain_bound() -> None:
    payload = StructuralFlowRunPayload(domain=Domain.STRUCTURAL_FLOW, **_run_payload())

    assert payload.domain is Domain.STRUCTURAL_FLOW
    assert payload.events[0].available_at == _START + timedelta(hours=1)
    assert payload.execution_policy.impact_multiplier == Decimal("1.5")

    with pytest.raises(ValidationError) as error:
        StructuralFlowRunPayload(domain=Domain.MARKET_MAKING, **_run_payload())
    assert error.value.errors()[0]["loc"] == ("domain",)
    assert error.value.errors()[0]["type"] == "literal_error"


@pytest.mark.parametrize(
    ("payload", "rejected_field"),
    (
        (
            {
                "domain": Domain.STRUCTURAL_FLOW,
                "research_id": "research-1",
                "instrument_id": "WTI",
                "event_source_id": "inventory-reports",
                "shell_command": "curl https://example.invalid",
            },
            "shell_command",
        ),
        (
            {
                "domain": Domain.STRUCTURAL_FLOW,
                "research_id": "research-1",
                "instrument_id": "WTI",
                "event_source_id": "inventory-reports",
                "quote_currency": "USD",
            },
            "quote_currency",
        ),
    ),
)
def test_ct_dsl_002_structural_flow_questions_reject_code_and_cross_domain_fields(
    payload: dict[str, object], rejected_field: str
) -> None:
    with pytest.raises(ValidationError) as error:
        StructuralFlowQuestionPayload(**payload)

    assert error.value.errors()[0]["loc"] == (rejected_field,)
    assert error.value.errors()[0]["type"] == "extra_forbidden"


def test_ct_dsl_003_structural_flow_compiler_allows_only_its_data_dsl() -> None:
    compiled = compile_structural_flow_strategy(_structural_flow_strategy())

    assert compiled.model_dump() == {"position_scale": Decimal("1")}

    with pytest.raises(DomainError) as code_operator:
        compile_structural_flow_strategy(_structural_flow_strategy(operator_id="shell_exec"))
    assert code_operator.value.detail.code is ErrorCode.SCHEMA
    assert code_operator.value.detail.details[0].path == "strategy.ast.root.operator_id"

    with pytest.raises(DomainError) as cross_domain:
        compile_structural_flow_strategy(_market_making_strategy())
    assert cross_domain.value.detail.code is ErrorCode.SCHEMA
    assert cross_domain.value.detail.details[0].path == "strategy"
