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


def _candidate(value: int, *, operator_set_hash: str) -> Candidate:
    return Candidate(
        domain=Domain.FACTOR,
        operator_set_hash=operator_set_hash,
        strategy_ast=FactorStrategyAst(root=AstLiteral(kind="literal", value=value)),
    )


def _universe(*values: int, operators: dict[str, SearchOperator]) -> Universe:
    operator_identity = operator_set_hash(operators)
    candidates = tuple(
        sorted(
            (_candidate(value, operator_set_hash=operator_identity) for value in values),
            key=lambda candidate: digest_bytes(candidate.candidate_hash),
        )
    )
    return Universe(
        domain=Domain.FACTOR,
        operator_set_hash=operator_identity,
        version="v1",
        candidates=candidates,
    )


def _profile(patience: int = 1) -> ValidationProfile:
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
    operator_schedule: tuple[str, ...],
    offspring_count: int,
) -> SearchSpec:
    return SearchSpec(
        domain=Domain.FACTOR,
        seed=23,
        initial_population_size=1,
        offspring_count=offspring_count,
        parent_pool_size=1,
        patience=profile.patience,
        operator_schedule=operator_schedule,
        operator_set_hash=operator_set_hash(operators),
        universe_hash=universe.universe_hash,
        profile_hash=profile.profile_hash,
        dataset_hashes=(_digest(30),),
        policy_hashes=(_digest(31),),
        schema_hashes=(_digest(32),),
        code_hash=_digest(33),
    )


def _operators(operator_ids: tuple[str, ...]) -> dict[str, SearchOperator]:
    return {
        operator_id: SearchOperator(
            operator_id=operator_id,
            arity=1,
            commutative=False,
            parameters=(),
        )
        for operator_id in operator_ids
    }


def _passed() -> TrialEvaluation:
    score = Decimal("1")
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


def test_slot_consumes_invalid_outside_and_duplicate_before_first_admissible_candidate() -> None:
    operators = _operators(("valid", "invalid", "outside", "duplicate"))
    universe = _universe(0, 1, 2, operators=operators)
    profile = _profile()
    search_spec = _search_spec(
        universe,
        profile,
        operators,
        operator_schedule=("valid", "invalid", "outside", "duplicate"),
        offspring_count=1,
    )
    initial, valid, _ = _traversal(universe, search_spec.seed)
    outside = _candidate(99, operator_set_hash=universe.operator_set_hash)

    result = run_search(
        universe=universe,
        search_spec=search_spec,
        validation_profile=profile,
        operators=operators,
        operator_callbacks={
            "invalid": lambda _: OperatorProposal(invalid_reason="AF-INVALID"),
            "outside": lambda _: OperatorProposal(candidate=outside),
            "duplicate": lambda _: OperatorProposal(candidate=initial),
            "valid": lambda _: OperatorProposal(candidate=valid),
        },
        evaluate_candidate=lambda _: _passed(),
    )

    assert [(event.event_type, event.kind.value) for event in result.events] == [
        ("trial", "TRIAL_STARTED"),
        ("trial", "EVALUATED"),
        ("proposal", "INVALID"),
        ("proposal", "OUTSIDE_UNIVERSE"),
        ("proposal", "DUPLICATE"),
        ("trial", "TRIAL_STARTED"),
        ("trial", "EVALUATED"),
    ]
    proposal_events = [event for event in result.events if event.event_type == "proposal"]
    assert [
        (event.slot_index, event.candidate_hash, event.reason_code) for event in proposal_events
    ] == [
        (0, None, "AF-INVALID"),
        (0, outside.candidate_hash, None),
        (0, initial.candidate_hash, None),
    ]
    assert [(proposal.source.value, proposal.candidate_hash) for proposal in result.proposals] == [
        ("INITIAL", initial.candidate_hash),
        ("OPERATOR", valid.candidate_hash),
    ]
    assert [(trial.generation_index, trial.slot_index) for trial in result.trials] == [
        (0, 0),
        (1, 0),
    ]


def test_fallback_is_first_unseen_and_proposed_visited_remain_run_scoped() -> None:
    operators = _operators(("repeat",))
    universe = _universe(0, 1, 2, 3, operators=operators)
    profile = _profile()
    search_spec = _search_spec(
        universe,
        profile,
        operators,
        operator_schedule=("repeat",),
        offspring_count=2,
    )
    initial, first_fallback, second_fallback, _ = _traversal(universe, search_spec.seed)

    def callback(invocation: object) -> OperatorProposal:
        parent_alternative = invocation.parent_alternative
        if parent_alternative.slot_index == 0:
            return OperatorProposal(candidate=initial)
        return OperatorProposal(candidate=first_fallback)

    result = run_search(
        universe=universe,
        search_spec=search_spec,
        validation_profile=profile,
        operators=operators,
        operator_callbacks={"repeat": callback},
        evaluate_candidate=lambda _: _passed(),
    )

    assert [(event.event_type, event.kind.value) for event in result.events] == [
        ("trial", "TRIAL_STARTED"),
        ("trial", "EVALUATED"),
        ("proposal", "DUPLICATE"),
        ("proposal", "DIRECT_FALLBACK"),
        ("trial", "TRIAL_STARTED"),
        ("trial", "EVALUATED"),
        ("proposal", "DUPLICATE"),
        ("proposal", "DIRECT_FALLBACK"),
        ("trial", "TRIAL_STARTED"),
        ("trial", "EVALUATED"),
    ]
    assert [
        (event.slot_index, event.candidate_hash)
        for event in result.events
        if event.event_type == "proposal"
    ] == [
        (0, initial.candidate_hash),
        (0, first_fallback.candidate_hash),
        (1, first_fallback.candidate_hash),
        (1, second_fallback.candidate_hash),
    ]
    assert result.proposed_candidate_hashes == (
        initial.candidate_hash,
        first_fallback.candidate_hash,
        second_fallback.candidate_hash,
    )
    assert result.visited_candidate_hashes == result.proposed_candidate_hashes
    assert [(trial.generation_index, trial.slot_index) for trial in result.trials] == [
        (0, 0),
        (1, 0),
        (1, 1),
    ]
