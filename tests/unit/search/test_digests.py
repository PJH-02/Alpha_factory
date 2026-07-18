from __future__ import annotations

from decimal import Decimal
from typing import cast

import pytest
from hypothesis import given
from hypothesis import strategies as st

from alpha_foundry.domain.models import AstLiteral, Domain, FactorStrategyAst
from alpha_foundry.search.models import (
    Candidate,
    OperatorParameter,
    ParameterAlternative,
    ParentAlternative,
    SearchOperator,
    SearchSpec,
    Traversal,
    Universe,
    digest_bytes,
    operator_set_hash,
)


def _digest(number: int) -> str:
    return f"sha256:{number:064x}"


def _operator_set() -> dict[str, SearchOperator]:
    return {"op": SearchOperator(operator_id="op", arity=0, commutative=False, parameters=())}


def _operator_set_identity() -> str:
    return operator_set_hash(_operator_set())


def _candidate(value: int) -> Candidate:
    return Candidate(
        domain=Domain.FACTOR,
        operator_set_hash=_operator_set_identity(),
        strategy_ast=FactorStrategyAst(root=AstLiteral(kind="literal", value=value)),
    )


def _universe(*values: int, version: str = "v1") -> Universe:
    candidates = tuple(
        sorted(
            (_candidate(value) for value in values),
            key=lambda candidate: digest_bytes(candidate.candidate_hash),
        )
    )
    return Universe(
        domain=Domain.FACTOR,
        operator_set_hash=_operator_set_identity(),
        version=version,
        candidates=candidates,
    )


def _search_spec(universe: Universe) -> SearchSpec:
    return SearchSpec(
        domain=Domain.FACTOR,
        seed=7,
        initial_population_size=1,
        offspring_count=2,
        parent_pool_size=1,
        patience=2,
        operator_schedule=("op",),
        operator_set_hash=_operator_set_identity(),
        universe_hash=universe.universe_hash,
        profile_hash=_digest(20),
        dataset_hashes=(_digest(30),),
        policy_hashes=(_digest(40),),
        schema_hashes=(_digest(50),),
        code_hash=_digest(60),
    )


def test_candidate_and_universe_digest_change_for_semantic_content() -> None:
    first_candidate = _candidate(1)
    second_candidate = _candidate(2)
    first_universe = _universe(1, 2)
    version_changed = _universe(1, 2, version="v2")
    membership_changed = _universe(1, 3)

    assert first_candidate.candidate_hash != second_candidate.candidate_hash
    assert first_universe.universe_hash != version_changed.universe_hash
    assert first_universe.universe_hash != membership_changed.universe_hash


@pytest.mark.parametrize(
    ("field", "replacement"),
    [
        ("domain", Domain.STAT_ARB),
        ("seed", 8),
        ("initial_population_size", 2),
        ("offspring_count", 3),
        ("parent_pool_size", 2),
        ("patience", 3),
        ("operator_schedule", ("other",)),
        ("operator_set_hash", _digest(11)),
        ("universe_hash", _digest(12)),
        ("profile_hash", _digest(21)),
        ("dataset_hashes", (_digest(31),)),
        ("policy_hashes", (_digest(41),)),
        ("schema_hashes", (_digest(51),)),
        ("code_hash", _digest(61)),
    ],
)
def test_every_direct_search_spec_semantic_field_changes_digest(
    field: str, replacement: object
) -> None:
    search_spec = _search_spec(_universe(1, 2))
    fields = search_spec.model_dump(mode="python")
    fields[field] = replacement
    changed = SearchSpec(**fields)

    assert changed.search_spec_hash != search_spec.search_spec_hash


def test_parent_and_parameter_alternative_digests_include_slot_semantics() -> None:
    candidate = _candidate(1)
    parent = ParentAlternative(
        generation_index=1,
        operator_id="op",
        parent_hashes=(candidate.candidate_hash,),
        schedule_offset=0,
        seed=7,
        slot_index=0,
    )
    parameter = ParameterAlternative(
        generation_index=1,
        operator_id="op",
        parameter_indices=(0,),
        schedule_offset=0,
        seed=7,
        slot_index=0,
    )

    assert parent.parent_hash != parent.model_copy(update={"slot_index": 1}).parent_hash
    assert parameter.parameter_hash != parameter.model_copy(update={"slot_index": 1}).parameter_hash


@given(st.integers(min_value=-10_000, max_value=10_000).filter(lambda seed: seed != 7))
def test_traversal_digest_changes_when_seed_changes(seed: int) -> None:
    candidate_hash = _candidate(1).candidate_hash

    assert (
        Traversal(candidate_hash=candidate_hash, seed=7).traversal_hash
        != Traversal(candidate_hash=candidate_hash, seed=seed).traversal_hash
    )


@pytest.mark.parametrize(
    ("value", "message"),
    [
        ("sha512:" + ("0" * 64), "sha256"),
        ("sha256:" + ("0" * 63), "sha256"),
        ("sha256:" + ("g" * 64), "lowercase hex"),
    ],
)
def test_digest_bytes_rejects_malformed_public_digests(value: str, message: str) -> None:
    with pytest.raises(ValueError, match=message):
        digest_bytes(value)


@pytest.mark.parametrize(
    ("field", "values", "message"),
    [
        (
            "dataset_hashes",
            (_digest(2), _digest(1)),
            "dataset_hashes must be in raw digest-byte ascending order",
        ),
        (
            "policy_hashes",
            (_digest(1), _digest(1)),
            "policy_hashes must not contain duplicate digests",
        ),
        (
            "schema_hashes",
            (_digest(2), _digest(1)),
            "schema_hashes must be in raw digest-byte ascending order",
        ),
    ],
)
def test_search_spec_rejects_unsorted_and_duplicate_pinned_digests(
    field: str, values: tuple[str, ...], message: str
) -> None:
    fields = _search_spec(_universe(1, 2)).model_dump(mode="python")
    fields[field] = values

    with pytest.raises(ValueError, match=message):
        SearchSpec(**fields)


def test_universe_rejects_duplicate_candidate_hashes() -> None:
    candidate = _candidate(1)

    with pytest.raises(ValueError, match="universe candidate hashes must not contain duplicate"):
        Universe(
            domain=Domain.FACTOR,
            operator_set_hash=_operator_set_identity(),
            version="v1",
            candidates=(candidate, candidate),
        )


@pytest.mark.parametrize(
    ("values", "message"),
    [
        ((2, 1), "AF-CANON byte order"),
        ((1, 1), "deduplicated by AF-CANON bytes"),
        ((Decimal("NaN"),), "finite number"),
    ],
)
def test_operator_parameter_rejects_noncanonical_grids(
    values: tuple[str | int | Decimal | bool | None, ...], message: str
) -> None:
    with pytest.raises(ValueError, match=message):
        OperatorParameter(name="parameter", values=values)


@pytest.mark.parametrize("arity", [0, 1])
def test_commutative_operator_requires_multiple_parents(arity: int) -> None:
    with pytest.raises(ValueError, match="only multi-parent operators may be commutative"):
        SearchOperator(operator_id="op", arity=arity, commutative=True, parameters=())


@pytest.mark.parametrize(
    ("parameters", "message"),
    [
        (
            (
                OperatorParameter(name="beta", values=(1,)),
                OperatorParameter(name="alpha", values=(1,)),
            ),
            "ASCII byte order",
        ),
        (
            (
                OperatorParameter(name="alpha", values=(1,)),
                OperatorParameter(name="alpha", values=(2,)),
            ),
            "operator parameter names must not contain duplicates",
        ),
    ],
)
def test_search_operator_rejects_noncanonical_or_duplicate_parameter_names(
    parameters: tuple[OperatorParameter, ...], message: str
) -> None:
    with pytest.raises(ValueError, match=message):
        SearchOperator(operator_id="op", arity=0, commutative=False, parameters=parameters)


@pytest.mark.parametrize(
    ("domain", "operator_identity", "message"),
    (
        (
            Domain.STAT_ARB,
            _operator_set_identity(),
            "universe candidates must all use the universe domain",
        ),
        (
            Domain.FACTOR,
            _digest(99),
            "universe candidates must all use the universe operator_set_hash",
        ),
    ),
)
def test_universe_rejects_candidates_with_foreign_pinned_identity(
    domain: Domain, operator_identity: str, message: str
) -> None:
    with pytest.raises(ValueError, match=message):
        Universe(
            domain=domain,
            operator_set_hash=operator_identity,
            version="v1",
            candidates=(_candidate(1),),
        )


def test_universe_rejects_descending_candidate_digest_order() -> None:
    candidates = tuple(reversed(_universe(1, 2).candidates))

    with pytest.raises(ValueError, match="raw digest-byte ascending order"):
        Universe(
            domain=Domain.FACTOR,
            operator_set_hash=_operator_set_identity(),
            version="v1",
            candidates=candidates,
        )


@pytest.mark.parametrize(
    ("operators", "message"),
    (
        ({"op": object()}, "operator set entries must be SearchOperator instances"),
        (
            {
                "different": SearchOperator(
                    operator_id="op",
                    arity=0,
                    commutative=False,
                    parameters=(),
                )
            },
            "operator mapping keys must match declared operator IDs",
        ),
    ),
)
def test_operator_set_hash_rejects_invalid_registered_operator_identity(
    operators: dict[str, object], message: str
) -> None:
    with pytest.raises(ValueError, match=message):
        operator_set_hash(cast(dict[str, SearchOperator], operators))


def test_operator_set_hash_is_independent_of_mapping_insertion_order() -> None:
    first = SearchOperator(operator_id="first", arity=0, commutative=False, parameters=())
    second = SearchOperator(operator_id="second", arity=0, commutative=False, parameters=())

    assert operator_set_hash({"first": first, "second": second}) == operator_set_hash(
        {"second": second, "first": first}
    )


def test_candidate_requires_the_strategy_ast_primary_domain() -> None:
    with pytest.raises(ValueError, match="candidate domain must match its strategy AST domain"):
        Candidate(
            domain=Domain.STAT_ARB,
            operator_set_hash=_operator_set_identity(),
            strategy_ast=FactorStrategyAst(root=AstLiteral(kind="literal", value=1)),
        )


def test_parent_and_parameter_alternatives_reject_noncanonical_indices_and_parents() -> None:
    candidate_hash = _candidate(1).candidate_hash

    with pytest.raises(ValueError, match="parent_hashes must not contain duplicates"):
        ParentAlternative(
            generation_index=1,
            operator_id="op",
            parent_hashes=(candidate_hash, candidate_hash),
            schedule_offset=0,
            seed=7,
            slot_index=0,
        )
    with pytest.raises(ValueError, match="parameter indices must be non-negative"):
        ParameterAlternative(
            generation_index=1,
            operator_id="op",
            parameter_indices=(-1,),
            schedule_offset=0,
            seed=7,
            slot_index=0,
        )
