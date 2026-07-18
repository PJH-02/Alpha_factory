from __future__ import annotations

from decimal import Decimal

from alpha_foundry.domain.models import (
    AstLiteral,
    Decision,
    Domain,
    FactorStrategyAst,
    GateComparison,
    GateResult,
    Metric,
    RankingDirection,
    RankingRule,
    ValidationGate,
    ValidationProfile,
)
from alpha_foundry.search.models import (
    Candidate,
    CandidateTrialTerminal,
    OperatorProposal,
    SearchOperator,
    SearchSpec,
    TrialEvaluation,
    Universe,
    digest_bytes,
    operator_set_hash,
)
from alpha_foundry.search.runner import run_search


def _digest(number: int) -> str:
    return f"sha256:{number:064x}"


def _operators() -> dict[str, SearchOperator]:
    return {"op": SearchOperator(operator_id="op", arity=1, commutative=False, parameters=())}


def _universe(*values: int, operators: dict[str, SearchOperator]) -> Universe:
    operator_identity = operator_set_hash(operators)
    candidates = tuple(
        sorted(
            (
                Candidate(
                    domain=Domain.FACTOR,
                    operator_set_hash=operator_identity,
                    strategy_ast=FactorStrategyAst(root=AstLiteral(kind="literal", value=value)),
                )
                for value in values
            ),
            key=lambda candidate: digest_bytes(candidate.candidate_hash),
        )
    )
    return Universe(
        domain=Domain.FACTOR,
        operator_set_hash=operator_identity,
        version="v1",
        candidates=candidates,
    )


def _profile(patience: int = 3) -> ValidationProfile:
    return ValidationProfile(
        validation_profile_id="profile",
        domain=Domain.FACTOR,
        version="v1",
        profile_hash=_digest(20),
        hard_gates=(
            ValidationGate(
                gate_id="gate",
                metric_name="score",
                comparison=GateComparison.GTE,
                threshold=Decimal("0"),
            ),
        ),
        ranking=(
            RankingRule(
                metric_name="score",
                direction=RankingDirection.DESC,
                quantization=Decimal("0.01"),
            ),
        ),
        fold_ids=("fold-0",),
        pbo_minimum_eligible=1,
        patience=patience,
        holdout_policy_hash=_digest(21),
        disclosure_policy_hash=_digest(22),
        allowed_disclosure_fields=(),
    )


def _search_spec(
    universe: Universe,
    profile: ValidationProfile,
    operators: dict[str, SearchOperator],
    *,
    initial_population_size: int,
    offspring_count: int,
    parent_pool_size: int,
) -> SearchSpec:
    return SearchSpec(
        domain=Domain.FACTOR,
        seed=17,
        initial_population_size=initial_population_size,
        offspring_count=offspring_count,
        parent_pool_size=parent_pool_size,
        patience=profile.patience,
        operator_schedule=("op",),
        operator_set_hash=operator_set_hash(operators),
        universe_hash=universe.universe_hash,
        profile_hash=profile.profile_hash,
        dataset_hashes=(_digest(30),),
        policy_hashes=(_digest(31),),
        schema_hashes=(_digest(32),),
        code_hash=_digest(33),
    )


def _passed(score: Decimal) -> TrialEvaluation:
    return TrialEvaluation(
        terminal=CandidateTrialTerminal.EVALUATED,
        metrics=(Metric(name="score", value=score),),
        gate_results=(
            GateResult(
                gate_id="gate",
                decision=Decision.PASS,
                metric_name="score",
                threshold=Decimal("0"),
                comparison=GateComparison.GTE,
                observed_value=score,
            ),
        ),
        complete=True,
        error_code=None,
    )


def _traversal(universe: Universe, seed: int) -> tuple[Candidate, ...]:
    from alpha_foundry.search.models import Traversal

    return tuple(
        sorted(
            universe.candidates,
            key=lambda candidate: (
                digest_bytes(
                    Traversal(candidate_hash=candidate.candidate_hash, seed=seed).traversal_hash
                ),
                digest_bytes(candidate.candidate_hash),
            ),
        )
    )


def test_parent_pool_retains_only_eligible_top_k_and_stays_frozen_for_all_slots() -> None:
    operators = _operators()
    universe = _universe(0, 1, 2, 3, operators=operators)
    profile = _profile()
    search_spec = _search_spec(
        universe,
        profile,
        operators,
        initial_population_size=2,
        offspring_count=2,
        parent_pool_size=1,
    )
    initial_first, initial_second, remaining_first, remaining_second = _traversal(
        universe, search_spec.seed
    )
    scores = {
        initial_first.candidate_hash: Decimal("10"),
        initial_second.candidate_hash: Decimal("5"),
        remaining_first.candidate_hash: Decimal("50"),
        remaining_second.candidate_hash: Decimal("60"),
    }
    observed_parent_hashes: list[tuple[str, ...]] = []

    def callback(invocation: object) -> OperatorProposal:
        parent_alternative = invocation.parent_alternative
        observed_parent_hashes.append(parent_alternative.parent_hashes)
        candidate = (remaining_first, remaining_second)[parent_alternative.slot_index]
        return OperatorProposal(candidate=candidate)

    result = run_search(
        universe=universe,
        search_spec=search_spec,
        validation_profile=profile,
        operators=operators,
        operator_callbacks={"op": callback},
        evaluate_candidate=lambda candidate: _passed(scores[candidate.candidate_hash]),
    )

    assert result.parent_pools[0].parent_candidate_hashes == (initial_first.candidate_hash,)
    assert observed_parent_hashes == [
        (initial_first.candidate_hash,),
        (initial_first.candidate_hash,),
    ]
    assert [(event.event_type, event.kind.value) for event in result.events] == [
        ("trial", "TRIAL_STARTED"),
        ("trial", "EVALUATED"),
        ("trial", "TRIAL_STARTED"),
        ("trial", "EVALUATED"),
        ("trial", "TRIAL_STARTED"),
        ("trial", "EVALUATED"),
        ("trial", "TRIAL_STARTED"),
        ("trial", "EVALUATED"),
    ]


def test_parent_pool_ties_break_by_raw_candidate_hash_order() -> None:
    operators = _operators()
    universe = _universe(0, 1, 2, operators=operators)
    profile = _profile()
    search_spec = _search_spec(
        universe,
        profile,
        operators,
        initial_population_size=2,
        offspring_count=1,
        parent_pool_size=2,
    )
    initial_first, initial_second, remaining = _traversal(universe, search_spec.seed)

    result = run_search(
        universe=universe,
        search_spec=search_spec,
        validation_profile=profile,
        operators=operators,
        operator_callbacks={"op": lambda _: OperatorProposal(candidate=remaining)},
        evaluate_candidate=lambda _: _passed(Decimal("1")),
    )

    assert result.parent_pools[0].parent_candidate_hashes == tuple(
        sorted(
            (initial_first.candidate_hash, initial_second.candidate_hash),
            key=digest_bytes,
        )
    )


def test_zero_sized_parent_pool_skips_parented_operator_and_uses_fallback() -> None:
    operators = _operators()
    universe = _universe(0, 1, operators=operators)
    profile = _profile()
    search_spec = _search_spec(
        universe,
        profile,
        operators,
        initial_population_size=1,
        offspring_count=1,
        parent_pool_size=0,
    )
    callback_calls = 0

    def callback(_: object) -> OperatorProposal:
        nonlocal callback_calls
        callback_calls += 1
        raise AssertionError("a parented operator cannot run from an empty parent pool")

    result = run_search(
        universe=universe,
        search_spec=search_spec,
        validation_profile=profile,
        operators=operators,
        operator_callbacks={"op": callback},
        evaluate_candidate=lambda _: _passed(Decimal("1")),
    )

    assert callback_calls == 0
    assert result.parent_pools[0].parent_candidate_hashes == ()
    assert [(event.event_type, event.kind.value) for event in result.events] == [
        ("trial", "TRIAL_STARTED"),
        ("trial", "EVALUATED"),
        ("proposal", "DIRECT_FALLBACK"),
        ("trial", "TRIAL_STARTED"),
        ("trial", "EVALUATED"),
    ]
