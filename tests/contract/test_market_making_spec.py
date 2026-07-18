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
from alpha_foundry.labs.market_making import (
    MarketMakingEvent,
    MarketMakingExecutionPolicy,
    MarketMakingQuestionPayload,
    MarketMakingRunPayload,
    compile_market_making_strategy,
)

_HASH = "sha256:" + "5" * 64
_START = datetime(2024, 1, 1, tzinfo=UTC)


def _execution_policy() -> MarketMakingExecutionPolicy:
    return MarketMakingExecutionPolicy(
        latency_events=1,
        fill_ratio=Decimal("0.5"),
        quote_spread=Decimal("0.02"),
        inventory_limit=Decimal("3"),
        allow_negative_cash=False,
        commission_rate=Decimal("0.001"),
        slippage_rate=Decimal("0.002"),
        impact_rate=Decimal("0.003"),
        adverse_selection_rate=Decimal("0.004"),
    )


def _run_payload() -> dict[str, object]:
    return {
        "events": (
            MarketMakingEvent(
                timestamp=_START,
                bid=Decimal("99"),
                ask=Decimal("101"),
                bid_size=Decimal("2"),
                ask_size=Decimal("3"),
                buy_volume=Decimal("1"),
                sell_volume=Decimal("1"),
            ),
            MarketMakingEvent(
                timestamp=_START + timedelta(seconds=1),
                bid=Decimal("100"),
                ask=Decimal("102"),
                bid_size=Decimal("2"),
                ask_size=Decimal("3"),
                buy_volume=Decimal("1"),
                sell_volume=Decimal("1"),
            ),
        ),
        "execution_policy": _execution_policy(),
        "quote_size": Decimal("1"),
        "initial_cash": Decimal("1000"),
        "initial_inventory": Decimal("0"),
    }


def _market_strategy(operator_id: str = "inventory_limited_quote") -> StrategySpec:
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
                operator_id=operator_id,
                parameters=(
                    AstParameter(name="quote_size", value=Decimal("1")),
                    AstParameter(name="inventory_skew", value=Decimal("0")),
                ),
            )
        ),
    )


def _structural_flow_strategy() -> StrategySpec:
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
                operator_id="decayed_event_impact",
                parameters=(AstParameter(name="position_scale", value=Decimal("1")),),
            )
        ),
    )


def test_ct_dsl_001_market_making_payloads_are_strictly_typed_and_domain_bound() -> None:
    payload = MarketMakingRunPayload(domain=Domain.MARKET_MAKING, **_run_payload())

    assert payload.domain is Domain.MARKET_MAKING
    assert payload.events[0].timestamp == _START
    assert payload.execution_policy.adverse_selection_rate == Decimal("0.004")

    with pytest.raises(ValidationError) as error:
        MarketMakingRunPayload(domain=Domain.STRUCTURAL_FLOW, **_run_payload())
    assert error.value.errors()[0]["loc"] == ("domain",)
    assert error.value.errors()[0]["type"] == "literal_error"


@pytest.mark.parametrize(
    ("payload", "rejected_field"),
    (
        (
            {
                "domain": Domain.MARKET_MAKING,
                "research_id": "research-1",
                "instrument_id": "BTC-USD",
                "quote_currency": "USD",
                "python_code": "__import__('os').system('whoami')",
            },
            "python_code",
        ),
        (
            {
                "domain": Domain.MARKET_MAKING,
                "research_id": "research-1",
                "instrument_id": "BTC-USD",
                "quote_currency": "USD",
                "event_source_id": "macro-events",
            },
            "event_source_id",
        ),
    ),
)
def test_ct_dsl_002_market_making_questions_reject_code_and_cross_domain_fields(
    payload: dict[str, object], rejected_field: str
) -> None:
    with pytest.raises(ValidationError) as error:
        MarketMakingQuestionPayload(**payload)

    assert error.value.errors()[0]["loc"] == (rejected_field,)
    assert error.value.errors()[0]["type"] == "extra_forbidden"


def test_ct_dsl_003_market_making_compiler_allows_only_its_data_dsl() -> None:
    compiled = compile_market_making_strategy(_market_strategy())

    assert compiled.model_dump() == {
        "quote_size": Decimal("1"),
        "inventory_skew": Decimal("0"),
    }

    with pytest.raises(DomainError) as code_operator:
        compile_market_making_strategy(_market_strategy(operator_id="python_exec"))
    assert code_operator.value.detail.code is ErrorCode.SCHEMA
    assert code_operator.value.detail.details[0].path == "strategy.ast.root.operator_id"

    with pytest.raises(DomainError) as cross_domain:
        compile_market_making_strategy(_structural_flow_strategy())
    assert cross_domain.value.detail.code is ErrorCode.SCHEMA
    assert cross_domain.value.detail.details[0].path == "strategy"
