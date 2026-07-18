from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from pydantic import ValidationError

from alpha_foundry.domain.errors import DomainError, ErrorCode
from alpha_foundry.domain.models import (
    AstOperator,
    AstParameter,
    Domain,
    FactorStrategyAst,
    StatArbStrategyAst,
    StrategySpec,
)
from alpha_foundry.labs.factor import (
    FactorExecutionPolicy,
    FactorQuestionPayload,
    FactorRunPayload,
    compile_factor_strategy,
)
from alpha_foundry.labs.statarb import (
    StatArbExecutionPolicy,
    StatArbQuestionPayload,
    StatArbRunPayload,
    compile_statarb_strategy,
)

_HASH = "sha256:" + "3" * 64
_START = datetime(2024, 1, 1, tzinfo=UTC)
_TIMESTAMPS = tuple(_START + timedelta(days=index) for index in range(4))


def _factor_payload() -> dict[str, object]:
    return {
        "timestamps": _TIMESTAMPS[:2],
        "asset_ids": ("A", "B"),
        "signals": ((Decimal("2"), Decimal("1")), (Decimal("1"), Decimal("2"))),
        "returns": ((Decimal("0.01"), Decimal("-0.01")), (Decimal("0.02"), Decimal("-0.02"))),
        "signal_available_at": (
            (_TIMESTAMPS[0], _TIMESTAMPS[0]),
            (_TIMESTAMPS[1], _TIMESTAMPS[1]),
        ),
        "long_count": 1,
        "short_count": 1,
        "execution_policy": FactorExecutionPolicy(
            commission_rate=Decimal("0.001"),
            slippage_rate=Decimal("0.002"),
            impact_rate=Decimal("0.003"),
        ),
    }


def _statarb_payload() -> dict[str, object]:
    return {
        "timestamps": _TIMESTAMPS,
        "leg_ids": ("A", "B"),
        "prices": (
            (Decimal("100"), Decimal("100")),
            (Decimal("102"), Decimal("100")),
            (Decimal("110"), Decimal("100")),
            (Decimal("121"), Decimal("90")),
        ),
        "price_available_at": tuple((timestamp, timestamp) for timestamp in _TIMESTAMPS),
        "hedge_weights": (Decimal("1"), Decimal("-1")),
        "execution_policy": StatArbExecutionPolicy(
            commission_rate=Decimal("0.001"),
            slippage_rate=Decimal("0.002"),
            impact_rate=Decimal("0.003"),
        ),
    }


def _factor_strategy(operator_id: str = "ranked_long_short") -> StrategySpec:
    return StrategySpec(
        strategy_id="factor-strategy",
        version="v1",
        domain=Domain.FACTOR,
        operator_set_version="v1",
        operator_set_hash=_HASH,
        schema_hash=_HASH,
        ast=FactorStrategyAst(
            root=AstOperator(
                kind="operator",
                operator_id=operator_id,
                parameters=(
                    AstParameter(name="long_count", value=1),
                    AstParameter(name="short_count", value=1),
                ),
            )
        ),
    )


def _statarb_strategy(operator_id: str = "lagged_zscore_spread") -> StrategySpec:
    return StrategySpec(
        strategy_id="statarb-strategy",
        version="v1",
        domain=Domain.STAT_ARB,
        operator_set_version="v1",
        operator_set_hash=_HASH,
        schema_hash=_HASH,
        ast=StatArbStrategyAst(
            root=AstOperator(
                kind="operator",
                operator_id=operator_id,
                parameters=(
                    AstParameter(name="lookback", value=2),
                    AstParameter(name="entry_z", value=Decimal("2")),
                    AstParameter(name="exit_z", value=Decimal("0.5")),
                ),
            )
        ),
    )


def test_ct_dsl_001_factor_and_statarb_payloads_are_strictly_typed() -> None:
    factor = FactorRunPayload(domain=Domain.FACTOR, **_factor_payload())
    statarb = StatArbRunPayload(domain=Domain.STAT_ARB, **_statarb_payload())

    assert factor.domain is Domain.FACTOR
    assert factor.execution_policy.impact_rate == Decimal("0.003")
    assert statarb.domain is Domain.STAT_ARB
    assert statarb.hedge_weights == (Decimal("1"), Decimal("-1"))

    with pytest.raises(ValidationError) as factor_error:
        FactorRunPayload(domain=Domain.STAT_ARB, **_factor_payload())
    assert factor_error.value.errors()[0]["loc"] == ("domain",)
    assert factor_error.value.errors()[0]["type"] == "literal_error"

    with pytest.raises(ValidationError) as statarb_error:
        StatArbRunPayload(domain=Domain.FACTOR, **_statarb_payload())
    assert statarb_error.value.errors()[0]["loc"] == ("domain",)
    assert statarb_error.value.errors()[0]["type"] == "literal_error"


@pytest.mark.parametrize(
    ("payload_model", "payload", "rejected_field"),
    (
        (
            FactorQuestionPayload,
            {
                "domain": Domain.FACTOR,
                "research_id": "research-1",
                "asset_universe_id": "equities",
                "signal_definition_id": "value",
                "python_code": "__import__('os').system('whoami')",
            },
            "python_code",
        ),
        (
            FactorQuestionPayload,
            {
                "domain": Domain.FACTOR,
                "research_id": "research-1",
                "asset_universe_id": "equities",
                "signal_definition_id": "value",
                "leg_universe_id": "pairs",
            },
            "leg_universe_id",
        ),
        (
            StatArbQuestionPayload,
            {
                "domain": Domain.STAT_ARB,
                "research_id": "research-1",
                "leg_universe_id": "pairs",
                "spread_definition_id": "pair-spread",
                "shell_command": "curl https://example.invalid",
            },
            "shell_command",
        ),
        (
            StatArbQuestionPayload,
            {
                "domain": Domain.STAT_ARB,
                "research_id": "research-1",
                "leg_universe_id": "pairs",
                "spread_definition_id": "pair-spread",
                "asset_universe_id": "equities",
            },
            "asset_universe_id",
        ),
    ),
)
def test_ct_dsl_002_question_payloads_reject_code_and_cross_domain_fields(
    payload_model: type[FactorQuestionPayload] | type[StatArbQuestionPayload],
    payload: dict[str, object],
    rejected_field: str,
) -> None:
    with pytest.raises(ValidationError) as error:
        payload_model(**payload)

    assert error.value.errors()[0]["loc"] == (rejected_field,)
    assert error.value.errors()[0]["type"] == "extra_forbidden"


def test_ct_dsl_003_compilers_allow_only_own_data_operators() -> None:
    factor_plan = compile_factor_strategy(_factor_strategy())
    statarb_plan = compile_statarb_strategy(_statarb_strategy())

    assert factor_plan.model_dump() == {"long_count": 1, "short_count": 1}
    assert statarb_plan.model_dump() == {
        "lookback": 2,
        "entry_z": Decimal("2"),
        "exit_z": Decimal("0.5"),
    }

    with pytest.raises(DomainError) as arbitrary_operator:
        compile_factor_strategy(_factor_strategy(operator_id="python_exec"))
    assert arbitrary_operator.value.detail.code is ErrorCode.SCHEMA
    assert arbitrary_operator.value.detail.details[0].path == "strategy.ast.root.operator_id"

    with pytest.raises(DomainError) as cross_domain_factor:
        compile_factor_strategy(_statarb_strategy())
    assert cross_domain_factor.value.detail.code is ErrorCode.SCHEMA
    assert cross_domain_factor.value.detail.details[0].path == "strategy"

    with pytest.raises(DomainError) as cross_domain_statarb:
        compile_statarb_strategy(_factor_strategy())
    assert cross_domain_statarb.value.detail.code is ErrorCode.SCHEMA
    assert cross_domain_statarb.value.detail.details[0].path == "strategy"
