"""AF-CANON release vectors for public candidate, search, and generation identities."""

from __future__ import annotations

import json
from collections.abc import Mapping
from copy import deepcopy
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from alpha_foundry.domain import Domain, ExperimentResult, ExperimentStatus, canonical_bytes, digest
from alpha_foundry.generation import GenerationRequest
from alpha_foundry.search.models import Candidate, SearchSpec

_FIXTURE_DIRECTORY = Path(__file__).parents[1] / "fixtures" / "canonical"


def _fixture(name: str) -> dict[str, Any]:
    return json.loads(
        (_FIXTURE_DIRECTORY / name).read_text(encoding="utf-8"),
        parse_float=Decimal,
    )


def _digest(byte_value: int) -> str:
    return "sha256:" + f"{byte_value:02x}" * 32


def _digest_bytes(value: str) -> bytes:
    return bytes.fromhex(value.removeprefix("sha256:"))


def _candidate(payload: Mapping[str, Any]) -> Candidate:
    return Candidate.model_validate(payload)


def _search_spec(payload: Mapping[str, Any]) -> SearchSpec:
    return SearchSpec.model_validate(payload)


def _generation_request(payload: Mapping[str, Any]) -> GenerationRequest:
    return GenerationRequest.model_validate(payload)


def _candidate_fields(candidate: Candidate) -> dict[str, object]:
    return {
        "domain": candidate.domain.value,
        "operator_set_hash": _digest_bytes(candidate.operator_set_hash),
        "strategy_ast": candidate.strategy_ast.model_dump(mode="python"),
    }


def _search_spec_fields(search_spec: SearchSpec) -> dict[str, object]:
    return {
        "code_hash": _digest_bytes(search_spec.code_hash),
        "dataset_hashes": [_digest_bytes(value) for value in search_spec.dataset_hashes],
        "domain": search_spec.domain.value,
        "initial_population_size": search_spec.initial_population_size,
        "offspring_count": search_spec.offspring_count,
        "operator_schedule": list(search_spec.operator_schedule),
        "operator_set_hash": _digest_bytes(search_spec.operator_set_hash),
        "parent_pool_size": search_spec.parent_pool_size,
        "patience": search_spec.patience,
        "policy_hashes": [_digest_bytes(value) for value in search_spec.policy_hashes],
        "profile_hash": _digest_bytes(search_spec.profile_hash),
        "schema_hashes": [_digest_bytes(value) for value in search_spec.schema_hashes],
        "seed": search_spec.seed,
        "universe_hash": _digest_bytes(search_spec.universe_hash),
    }


def _reverse_object_order(value: object) -> object:
    if isinstance(value, Mapping):
        return {key: _reverse_object_order(item) for key, item in reversed(tuple(value.items()))}
    if isinstance(value, tuple):
        return tuple(_reverse_object_order(item) for item in value)
    if isinstance(value, list):
        return [_reverse_object_order(item) for item in value]
    return value


def _set_path(
    payload: dict[str, Any],
    path: tuple[str | int, ...],
    replacement: object,
) -> None:
    target: Any = payload
    for part in path[:-1]:
        target = target[part]
    target[path[-1]] = replacement


def _mutated_hash(
    fixture_name: str,
    factory: Any,
    hash_name: str,
    updates: tuple[tuple[tuple[str | int, ...], object], ...],
) -> str:
    payload = deepcopy(_fixture(fixture_name)["input"])
    for path, replacement in updates:
        _set_path(payload, path, replacement)
    return getattr(factory(payload), hash_name)


def test_candidate_vector_fixture_matches_exact_bytes_and_digest() -> None:
    fixture = _fixture("candidate.json")
    candidate = _candidate(fixture["input"])

    assert canonical_bytes(_candidate_fields(candidate)).hex() == fixture["canonical_hex"]
    assert candidate.candidate_hash == fixture["digest"]


def test_search_spec_vector_fixture_matches_exact_bytes_and_digest() -> None:
    fixture = _fixture("search_spec.json")
    search_spec = _search_spec(fixture["input"])

    assert canonical_bytes(_search_spec_fields(search_spec)).hex() == fixture["canonical_hex"]
    assert search_spec.search_spec_hash == fixture["digest"]


def test_generation_request_vector_fixture_matches_exact_bytes_and_digest() -> None:
    fixture = _fixture("generation_request.json")
    request = _generation_request(fixture["input"])

    assert isinstance(request.claim_pins, tuple)
    assert isinstance(request.provider_chain, tuple)
    assert request.domain.value == "FACTOR"
    assert request.sampling["modes"] == ("brief", "exact")
    assert canonical_bytes(request.canonical_fields()).hex() == fixture["canonical_hex"]
    assert request.request_hash == fixture["digest"]


def test_generation_request_rejects_extra_fields_and_binary_floats() -> None:
    fixture = _fixture("generation_request.json")

    extra_field = deepcopy(fixture["input"])
    extra_field["unexpected"] = "field"
    with pytest.raises(ValidationError, match="extra_forbidden"):
        _generation_request(extra_field)

    binary_float = deepcopy(fixture["input"])
    binary_float["sampling"]["temperature"] = 0.1
    with pytest.raises(ValidationError, match="binary floating point"):
        _generation_request(binary_float)


def test_domain_models_normalize_json_enums_lists_and_datetimes() -> None:
    result = ExperimentResult.model_validate(
        {
            "experiment_id": "experiment-1",
            "strategy_id": "strategy-1",
            "domain": "FACTOR",
            "config_hash": _digest(1),
            "status": "SUCCEEDED",
            "metrics": [],
            "artifact_hashes": [],
            "started_at": "2026-07-12T09:30:00Z",
            "finished_at": "2026-07-12T09:31:00Z",
        }
    )

    assert result.domain is Domain.FACTOR
    assert result.status is ExperimentStatus.SUCCEEDED
    assert result.metrics == ()
    assert result.artifact_hashes == ()
    assert result.started_at.isoformat() == "2026-07-12T09:30:00+00:00"


@pytest.mark.parametrize(
    ("fixture_name", "factory", "field_builder", "hash_name", "domain"),
    [
        (
            "candidate.json",
            _candidate,
            _candidate_fields,
            "candidate_hash",
            "AF:CANDIDATE:1",
        ),
        (
            "search_spec.json",
            _search_spec,
            _search_spec_fields,
            "search_spec_hash",
            "AF:SEARCH_SPEC:1",
        ),
        (
            "generation_request.json",
            _generation_request,
            lambda request: request.canonical_fields(),
            "request_hash",
            "AF:GENERATION_REQUEST:1",
        ),
    ],
)
def test_canonical_vectors_ignore_named_field_insertion_order(
    fixture_name: str,
    factory: Any,
    field_builder: Any,
    hash_name: str,
    domain: str,
) -> None:
    fixture = _fixture(fixture_name)
    instance = factory(_reverse_object_order(deepcopy(fixture["input"])))
    reordered_fields = _reverse_object_order(field_builder(instance))

    assert canonical_bytes(reordered_fields) == bytes.fromhex(fixture["canonical_hex"])
    assert digest(domain, reordered_fields) == fixture["digest"]
    assert getattr(instance, hash_name) == fixture["digest"]


@pytest.mark.parametrize(
    ("fixture_name", "factory", "field_builder", "hash_name"),
    [
        ("candidate.json", _candidate, _candidate_fields, "candidate_hash"),
        ("search_spec.json", _search_spec, _search_spec_fields, "search_spec_hash"),
        (
            "generation_request.json",
            _generation_request,
            lambda request: request.canonical_fields(),
            "request_hash",
        ),
    ],
)
def test_canonical_vectors_are_stable_across_repeated_construction(
    fixture_name: str,
    factory: Any,
    field_builder: Any,
    hash_name: str,
) -> None:
    fixture = _fixture(fixture_name)
    outputs = tuple(
        (
            canonical_bytes(field_builder(factory(deepcopy(fixture["input"])))),
            getattr(factory(deepcopy(fixture["input"])), hash_name),
        )
        for _ in range(3)
    )

    assert outputs == ((bytes.fromhex(fixture["canonical_hex"]), fixture["digest"]),) * 3


@pytest.mark.parametrize(
    ("case", "updates"),
    [
        (
            "domain",
            (
                (("domain",), "STAT_ARB"),
                (("strategy_ast", "domain"), "STAT_ARB"),
            ),
        ),
        ("operator_set_hash", ((("operator_set_hash",), _digest(11)),)),
        (
            "strategy_ast_operator_id",
            ((("strategy_ast", "root", "operator_id"), "weighted_sum_v2"),),
        ),
        (
            "strategy_ast_unicode_literal",
            ((("strategy_ast", "root", "arguments", 0, "value"), "naïve"),),
        ),
        (
            "strategy_ast_argument_order",
            (
                (
                    ("strategy_ast", "root", "arguments"),
                    [
                        {"kind": "literal", "value": Decimal("1.2300")},
                        {"kind": "literal", "value": "café"},
                    ],
                ),
            ),
        ),
        (
            "strategy_ast_decimal_literal",
            ((("strategy_ast", "root", "arguments", 1, "value"), Decimal("1.24")),),
        ),
        (
            "strategy_ast_parameter_name",
            ((("strategy_ast", "root", "parameters", 0, "name"), "limit"),),
        ),
        (
            "strategy_ast_decimal_parameter",
            ((("strategy_ast", "root", "parameters", 0, "value"), Decimal("0.06")),),
        ),
    ],
)
def test_every_candidate_semantic_field_mutation_changes_digest(
    case: str,
    updates: tuple[tuple[tuple[str | int, ...], object], ...],
) -> None:
    fixture = _fixture("candidate.json")

    assert (
        _mutated_hash("candidate.json", _candidate, "candidate_hash", updates) != fixture["digest"]
    ), case


@pytest.mark.parametrize(
    ("case", "updates"),
    [
        ("domain", ((("domain",), "STAT_ARB"),)),
        ("seed", ((("seed",), -18),)),
        ("initial_population_size", ((("initial_population_size",), 3),)),
        ("offspring_count", ((("offspring_count",), 4),)),
        ("parent_pool_size", ((("parent_pool_size",), 2),)),
        ("patience", ((("patience",), 5),)),
        (
            "operator_schedule",
            ((("operator_schedule",), ["crossover", "mutate"]),),
        ),
        ("operator_set_hash", ((("operator_set_hash",), _digest(17)),)),
        ("universe_hash", ((("universe_hash",), _digest(33)),)),
        ("profile_hash", ((("profile_hash",), _digest(49)),)),
        ("dataset_hashes_first", ((("dataset_hashes", 0), _digest(0)),)),
        ("dataset_hashes_second", ((("dataset_hashes", 1), _digest(3)),)),
        ("policy_hashes_first", ((("policy_hashes", 0), _digest(2)),)),
        ("policy_hashes_second", ((("policy_hashes", 1), _digest(5)),)),
        ("schema_hashes_first", ((("schema_hashes", 0), _digest(4)),)),
        ("schema_hashes_second", ((("schema_hashes", 1), _digest(7)),)),
        ("code_hash", ((("code_hash",), _digest(65)),)),
    ],
)
def test_every_search_spec_semantic_field_mutation_changes_digest(
    case: str,
    updates: tuple[tuple[tuple[str | int, ...], object], ...],
) -> None:
    fixture = _fixture("search_spec.json")

    assert (
        _mutated_hash("search_spec.json", _search_spec, "search_spec_hash", updates)
        != fixture["digest"]
    ), case


@pytest.mark.parametrize(
    ("case", "updates"),
    [
        ("capability_snapshot_hash", ((("capability_snapshot_hash",), _digest(81)),)),
        ("knowledge_pack_hash", ((("knowledge_pack_hash",), _digest(97)),)),
        ("claim_pin_id", ((("claim_pins", 0, "id"), "claim-002"),)),
        ("claim_pin_version", ((("claim_pins", 0, "version"), "1.2.4"),)),
        ("claim_pin_hash", ((("claim_pins", 0, "hash"), _digest(113)),)),
        ("domain", ((("domain",), "STAT_ARB"),)),
        (
            "provider_chain_provider",
            ((("provider_chain", 0, "provider"), "alpha-v2"),),
        ),
        ("provider_chain_model", ((("provider_chain", 0, "model"), "model-a-v2"),)),
        (
            "provider_chain_config_hash",
            ((("provider_chain", 0, "config_hash"), _digest(129)),),
        ),
        (
            "provider_chain_second_config_hash",
            ((("provider_chain", 1, "config_hash"), _digest(145)),),
        ),
        ("request_schema_hash", ((("request_schema_hash",), _digest(161)),)),
        ("sampling_integer", ((("sampling", "max_tokens"), 513),)),
        (
            "sampling_unicode_string",
            ((("sampling", "temperature_label"), "naïve"),),
        ),
        (
            "sampling_list_order",
            ((("sampling", "modes"), ["exact", "brief"]),),
        ),
        (
            "constraints_unicode_string",
            ((("constraints", "language"), "ko-영어"),),
        ),
        ("constraints_nested_integer", ((("constraints", "limits", "attempts"), 3),)),
        ("constraints_nested_boolean", ((("constraints", "limits", "strict"), False),)),
        ("user_request_unicode_string", ((("user_request", "objective"), "café"),)),
        (
            "user_request_list_order",
            ((("user_request", "assets"), ["ETH", "BTC"]),),
        ),
        ("user_request_null", ((("user_request", "include"), False),)),
    ],
)
def test_every_generation_request_semantic_field_mutation_changes_digest(
    case: str,
    updates: tuple[tuple[tuple[str | int, ...], object], ...],
) -> None:
    fixture = _fixture("generation_request.json")

    assert (
        _mutated_hash(
            "generation_request.json",
            _generation_request,
            "request_hash",
            updates,
        )
        != fixture["digest"]
    ), case
