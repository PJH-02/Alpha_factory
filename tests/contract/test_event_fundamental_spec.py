from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from pydantic import ValidationError

from alpha_foundry.domain.errors import DomainError, ErrorCode
from alpha_foundry.domain.models import (
    AstOperator,
    AstParameter,
    Domain,
    EventFundamentalStrategyAst,
    StrategySpec,
)
from alpha_foundry.labs.event_fundamental import (
    EventFundamentalExecutionPolicy,
    EventFundamentalQuestionPayload,
    EventFundamentalRunPayload,
    FundamentalEvent,
)
from alpha_foundry.labs.registry import DEFAULT_LAB_REGISTRY


def compile_event_fundamental_strategy(strategy: StrategySpec) -> object:
    return DEFAULT_LAB_REGISTRY.get(Domain.EVENT_FUNDAMENTAL).compile(strategy)


_START = datetime(2024, 1, 1, tzinfo=UTC)
_TIMESTAMPS = tuple(_START + timedelta(days=index) for index in range(4))


def _policy() -> EventFundamentalExecutionPolicy:
    return EventFundamentalExecutionPolicy(
        entry_lag_periods=1,
        holding_periods=2,
        signal_threshold=Decimal("0.5"),
        position_size=Decimal("0.5"),
        maximum_gross_exposure=Decimal("1"),
        allow_short=False,
        commission_rate=Decimal("0.001"),
        slippage_rate=Decimal("0.002"),
        impact_rate=Decimal("0.003"),
    )


def _payload() -> dict[str, object]:
    return {
        "timestamps": _TIMESTAMPS,
        "returns": (Decimal("0"), Decimal("0.01"), Decimal("-0.02"), Decimal("0")),
        "events": (
            FundamentalEvent(
                event_id="earnings-1",
                published_at=_TIMESTAMPS[0],
                available_at=_TIMESTAMPS[1],
                surprise=Decimal("0.75"),
            ),
        ),
        "policy": _policy(),
    }


def _strategy(operator_id: str = "event_surprise") -> StrategySpec:
    lab = DEFAULT_LAB_REGISTRY.get(Domain.EVENT_FUNDAMENTAL)

    return StrategySpec(
        strategy_id="event-fundamental-strategy",
        version="v1",
        domain=Domain.EVENT_FUNDAMENTAL,
        operator_set_version=lab.operator_set_version,
        operator_set_hash=lab.operator_set_hash,
        schema_hash=lab.schema_hash,
        ast=EventFundamentalStrategyAst(
            root=AstOperator(
                kind="operator",
                operator_id=operator_id,
                parameters=(
                    AstParameter(name="entry_lag_periods", value=1),
                    AstParameter(name="holding_periods", value=2),
                    AstParameter(name="signal_threshold", value=Decimal("0.5")),
                    AstParameter(name="position_size", value=Decimal("0.5")),
                    AstParameter(name="maximum_gross_exposure", value=Decimal("1")),
                    AstParameter(name="allow_short", value=False),
                ),
            )
        ),
    )


def test_ct_event_001_payloads_are_strict_and_preserve_event_availability() -> None:
    payload = EventFundamentalRunPayload(domain=Domain.EVENT_FUNDAMENTAL, **_payload())

    assert payload.domain is Domain.EVENT_FUNDAMENTAL
    assert payload.events[0].published_at == _TIMESTAMPS[0]
    assert payload.events[0].available_at == _TIMESTAMPS[1]
    assert payload.policy.holding_periods == 2

    with pytest.raises(ValidationError) as generated_code:
        EventFundamentalRunPayload(
            domain=Domain.EVENT_FUNDAMENTAL,
            **_payload(),
            python_code="raise RuntimeError('must not run')",
        )
    assert generated_code.value.errors()[0]["loc"] == ("python_code",)
    assert generated_code.value.errors()[0]["type"] == "extra_forbidden"

    with pytest.raises(ValidationError) as wrong_domain:
        EventFundamentalQuestionPayload(
            domain=Domain.TIME_SERIES,
            research_id="research-1",
            instrument_id="instrument-1",
            event_type="earnings",
        )
    assert wrong_domain.value.errors()[0]["loc"] == ("domain",)
    assert wrong_domain.value.errors()[0]["type"] == "literal_error"


def test_ct_event_002_compiler_accepts_only_explicit_allowlisted_data_plan() -> None:
    plan = compile_event_fundamental_strategy(_strategy())

    assert plan.model_dump() == {
        "entry_lag_periods": 1,
        "holding_periods": 2,
        "signal_threshold": Decimal("0.5"),
        "position_size": Decimal("0.5"),
        "maximum_gross_exposure": Decimal("1"),
        "allow_short": False,
    }

    with pytest.raises(DomainError) as generated_operator:
        compile_event_fundamental_strategy(_strategy(operator_id="python_exec"))
    assert generated_operator.value.detail.code is ErrorCode.SCHEMA
    assert generated_operator.value.detail.details[0].path == "strategy.ast.root.operator_id"


@pytest.mark.parametrize(
    ("field", "path"),
    (
        ("operator_set_version", "strategy.operator_set_version"),
        ("operator_set_hash", "strategy.operator_set_hash"),
        ("schema_hash", "strategy.schema_hash"),
    ),
)
def test_ct_event_003_compiler_rejects_mismatched_lab_identity(field: str, path: str) -> None:
    strategy = _strategy()
    current_value = getattr(strategy, field)
    replacement = (
        f"{current_value}-unreviewed"
        if field == "operator_set_version"
        else current_value[:-1] + ("0" if current_value[-1] != "0" else "1")
    )

    with pytest.raises(DomainError) as mismatched_identity:
        compile_event_fundamental_strategy(strategy.model_copy(update={field: replacement}))

    assert mismatched_identity.value.detail.code is ErrorCode.SCHEMA
    assert mismatched_identity.value.detail.details[0].path == path


def test_ct_event_004_compiler_rejects_duplicate_parameter_names() -> None:
    strategy = _strategy()
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
        compile_event_fundamental_strategy(duplicate_strategy)

    assert duplicate_parameter.value.detail.code is ErrorCode.SCHEMA
    assert (
        duplicate_parameter.value.detail.details[0].path == "strategy.ast.root.parameters[6].name"
    )
