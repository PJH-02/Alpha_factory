from __future__ import annotations

from dataclasses import replace
from decimal import Decimal

import pytest

from alpha_foundry.domain.errors import DomainError, ErrorCode, ErrorDetail
from alpha_foundry.domain.models import (
    Decision,
    Domain,
    GateComparison,
    GateResult,
    Metric,
    RankingDirection,
    RankingRule,
    ValidationGate,
    ValidationProfile,
)
from alpha_foundry.search.models import (
    CandidateTrial,
    CandidateTrialTerminal,
    Proposal,
    ProposalSource,
    SearchFailureReason,
    SearchRunResult,
    SearchStopReason,
    TrialEvaluation,
    TrialEvent,
    TrialEventKind,
)
from alpha_foundry.validation.pbo import (
    PboCertificateDecision,
    PboCompletenessCertificate,
    PboEligibilityPolicy,
    TrialFoldScore,
    build_trial_fold_matrix,
    calculate_pbo,
    certify_pbo_input,
    construct_cscv_partitions,
)

FOLD_IDS = ("fold-0", "fold-1", "fold-2", "fold-3")
TRIAL_IDS = ("alpha", "beta", "gamma")
LINEAGE_ID = "lineage-1"
VALIDATION_ID = "validation-1"


def _digest(number: int) -> str:
    return f"sha256:{number:064x}"


def _profile(
    *,
    minimum_eligible: int = 3,
    fold_ids: tuple[str, ...] = FOLD_IDS,
    ranking_direction: RankingDirection = RankingDirection.DESC,
) -> ValidationProfile:
    return ValidationProfile(
        validation_profile_id="validation-profile",
        domain=Domain.FACTOR,
        version="v1",
        profile_hash=_digest(1),
        hard_gates=(
            ValidationGate(
                gate_id="quality",
                metric_name="score",
                comparison=GateComparison.GTE,
                threshold=Decimal("0"),
            ),
        ),
        ranking=(
            RankingRule(
                metric_name="score",
                direction=ranking_direction,
                quantization=Decimal("0.01"),
            ),
        ),
        fold_ids=fold_ids,
        pbo_minimum_eligible=minimum_eligible,
        patience=2,
        holdout_policy_hash=_digest(2),
        disclosure_policy_hash=_digest(3),
        allowed_disclosure_fields=(),
    )


def _policy(
    profile: ValidationProfile, *, allowed_gates: tuple[str, ...] = ()
) -> PboEligibilityPolicy:
    return PboEligibilityPolicy(
        profile_hash=profile.profile_hash,
        eligible_hard_gate_ids=allowed_gates,
    )


def _gate_result(gate: ValidationGate, decision: Decision) -> GateResult:
    assert gate.comparison is GateComparison.GTE
    observed_value = gate.threshold + (Decimal("1") if decision is Decision.PASS else Decimal("-1"))
    return GateResult(
        gate_id=gate.gate_id,
        decision=decision,
        metric_name=gate.metric_name,
        threshold=gate.threshold,
        comparison=gate.comparison,
        observed_value=observed_value,
        reason=(
            None
            if decision is Decision.PASS
            else ErrorDetail.for_code(ErrorCode.VALIDATION, "hard gate failed")
        ),
    )


def _evaluation(profile: ValidationProfile, terminal: CandidateTrialTerminal) -> TrialEvaluation:
    if terminal is CandidateTrialTerminal.EVALUATED:
        gate_results = tuple(_gate_result(gate, Decision.PASS) for gate in profile.hard_gates)
        return TrialEvaluation(
            terminal=terminal,
            metrics=tuple(
                Metric(name=result.metric_name, value=result.observed_value)
                for result in gate_results
            ),
            gate_results=gate_results,
            complete=True,
            error_code=None,
        )
    if terminal is CandidateTrialTerminal.REJECTED_HARD_GATE:
        gate_results = tuple(
            _gate_result(
                gate,
                Decision.FAIL if index == len(profile.hard_gates) - 1 else Decision.PASS,
            )
            for index, gate in enumerate(profile.hard_gates)
        )
        return TrialEvaluation(
            terminal=terminal,
            gate_results=gate_results,
            complete=True,
            error_code="hard-gate-rejected",
        )
    return TrialEvaluation(
        terminal=terminal,
        complete=False,
        error_code="terminal-not-evaluable",
    )


def _search_run(
    profile: ValidationProfile,
    trial_terminals: tuple[tuple[str, CandidateTrialTerminal], ...] = tuple(
        (trial_id, CandidateTrialTerminal.EVALUATED) for trial_id in TRIAL_IDS
    ),
    *,
    profile_hash: str | None = None,
    failure_reason: SearchFailureReason | None = None,
) -> SearchRunResult:
    candidate_hashes = tuple(_digest(index + 100) for index in range(len(trial_terminals)))
    evaluations = tuple(_evaluation(profile, terminal) for _, terminal in trial_terminals)
    trials = tuple(
        CandidateTrial(
            trial_id=trial_id,
            ledger_position=index,
            candidate_hash=candidate_hashes[index],
            generation_index=0,
            slot_index=index,
            operator_id=None,
            parent_hashes=(),
            parameter_indices=(),
            terminal=terminal,
            evaluation=evaluations[index],
        )
        for index, (trial_id, terminal) in enumerate(trial_terminals)
    )
    return SearchRunResult(
        search_spec_hash=_digest(10),
        universe_hash=_digest(11),
        profile_hash=profile.profile_hash if profile_hash is None else profile_hash,
        stop_reason=SearchStopReason.UNIVERSE_EXHAUSTED if failure_reason is None else None,
        failure_reason=failure_reason,
        best_candidate_hash=candidate_hashes[0],
        plateau_counter=0,
        generation_count=1,
        parent_pools=(),
        proposals=tuple(
            Proposal(
                candidate_hash=candidate_hash,
                generation_index=0,
                slot_index=index,
                source=ProposalSource.INITIAL,
                operator_id=None,
                parent_hash=None,
                parameter_hash=None,
            )
            for index, candidate_hash in enumerate(candidate_hashes)
        ),
        proposed_candidate_hashes=candidate_hashes,
        visited_candidate_hashes=candidate_hashes,
        events=tuple(
            event
            for index, ((trial_id, terminal), evaluation) in enumerate(
                zip(trial_terminals, evaluations, strict=True)
            )
            for event in (
                TrialEvent(
                    sequence=2 * index,
                    trial_id=trial_id,
                    ledger_position=index,
                    kind=TrialEventKind.TRIAL_STARTED,
                    error_code=None,
                ),
                TrialEvent(
                    sequence=2 * index + 1,
                    trial_id=trial_id,
                    ledger_position=index,
                    kind=TrialEventKind(terminal.value),
                    error_code=evaluation.error_code,
                ),
            )
        ),
        trials=trials,
    )


def _scores(
    trial_ids: tuple[str, ...] = TRIAL_IDS,
    fold_ids: tuple[str, ...] = FOLD_IDS,
) -> tuple[TrialFoldScore, ...]:
    values = {
        "alpha": ("10", "10", "0", "0"),
        "beta": ("6", "6", "6", "6"),
        "gamma": ("5", "5", "5", "5"),
    }
    return tuple(
        TrialFoldScore(
            trial_id=trial_id,
            fold_id=fold_id,
            score=Decimal(
                values.get(
                    trial_id,
                    tuple(str(10 * trial_index + index) for index in range(len(fold_ids))),
                )[fold_index]
            ),
        )
        for trial_index, trial_id in enumerate(trial_ids, start=1)
        for fold_index, fold_id in enumerate(fold_ids)
    )


def _certificate(
    profile: ValidationProfile,
    search_run: SearchRunResult,
    scores: tuple[TrialFoldScore, ...],
    *,
    eligibility_policy: PboEligibilityPolicy | None = None,
    score_direction: RankingDirection | None = None,
) -> PboCompletenessCertificate:
    return certify_pbo_input(
        profile=profile,
        eligibility_policy=_policy(profile) if eligibility_policy is None else eligibility_policy,
        scores=scores,
        lineage_id=LINEAGE_ID,
        validation_id=VALIDATION_ID,
        search_run=search_run,
        score_direction=profile.ranking[0].direction
        if score_direction is None
        else score_direction,
    )


def _reasons(certificate: PboCompletenessCertificate) -> tuple[str, ...]:
    reason = certificate.reason
    assert reason is not None
    return tuple(detail.reason for detail in reason.details)


def _complete_matrix(
    profile: ValidationProfile,
    scores: tuple[TrialFoldScore, ...],
):
    certificate = _certificate(profile, _search_run(profile), scores)
    assert certificate.is_complete
    return certificate, build_trial_fold_matrix(
        certificate=certificate,
        scores=scores,
        score_direction=profile.ranking[0].direction,
    )


def test_calculate_pbo_matches_a_hand_computed_cscv_oracle_and_preserves_provenance() -> None:
    profile = _profile()
    scores = _scores()
    certificate, matrix = _complete_matrix(profile, scores)

    result = calculate_pbo(matrix, certificate=certificate, decimal_precision=28)

    assert tuple(
        (
            outcome.partition.partition_id,
            outcome.selected_trial_id,
            outcome.out_of_sample_rank,
            outcome.overfit,
        )
        for outcome in result.partitions
    ) == (
        ("cscv-0", "alpha", 1, True),
        ("cscv-1", "beta", 3, False),
        ("cscv-2", "beta", 3, False),
        ("cscv-3", "beta", 3, False),
        ("cscv-4", "beta", 3, False),
        ("cscv-5", "beta", 2, False),
    )
    assert (
        result.lineage_id,
        result.validation_id,
        result.profile_hash,
        result.search_run_hash,
        result.certificate_hash,
        result.matrix_digest,
    ) == (
        LINEAGE_ID,
        VALIDATION_ID,
        profile.profile_hash,
        certificate.search_run_hash,
        certificate.certificate_hash,
        matrix.matrix_digest,
    )
    assert result.overfit_count == 1
    assert result.partition_count == 6
    assert result.probability_of_backtest_overfitting == Decimal("0.1666666666666666666666666667")
    assert Decimal(0) <= result.probability_of_backtest_overfitting <= Decimal(1)
    with pytest.raises(ValueError, match="PBO must equal overfit_count / partition_count"):
        replace(result, probability_of_backtest_overfitting=Decimal("0"))
    with pytest.raises(ValueError, match="PBO must be a finite probability"):
        replace(result, probability_of_backtest_overfitting=Decimal("NaN"))


def test_pbo_certificate_and_result_are_deterministic_for_score_arrival_order() -> None:
    profile = _profile()
    search_run = _search_run(profile)
    scores = _scores()

    first_certificate = _certificate(profile, search_run, scores)
    second_certificate = _certificate(profile, search_run, tuple(reversed(scores)))
    first_matrix = build_trial_fold_matrix(
        certificate=first_certificate,
        scores=scores,
        score_direction=RankingDirection.DESC,
    )
    second_matrix = build_trial_fold_matrix(
        certificate=second_certificate,
        scores=tuple(reversed(scores)),
        score_direction=RankingDirection.DESC,
    )

    assert first_certificate == second_certificate
    assert first_matrix == second_matrix
    assert calculate_pbo(
        first_matrix, certificate=first_certificate, decimal_precision=28
    ) == calculate_pbo(second_matrix, certificate=second_certificate, decimal_precision=28)


def test_certificate_uses_only_complete_authoritative_terminal_evidence() -> None:
    profile = _profile(minimum_eligible=2)
    trial_terminals = (
        ("evaluated", CandidateTrialTerminal.EVALUATED),
        ("hard-gate", CandidateTrialTerminal.REJECTED_HARD_GATE),
        ("preflight", CandidateTrialTerminal.REJECTED_PREFLIGHT),
    )
    scores = _scores(("evaluated", "hard-gate"))
    certificate = _certificate(
        profile,
        _search_run(profile, trial_terminals),
        scores,
        eligibility_policy=_policy(profile, allowed_gates=("quality",)),
    )

    assert certificate.decision is PboCertificateDecision.PASS
    assert (
        certificate.started_trial_ids
        == certificate.terminal_trial_ids
        == (
            "evaluated",
            "hard-gate",
            "preflight",
        )
    )
    assert (
        certificate.eligible_trial_ids
        == certificate.matrix_trial_ids
        == (
            "evaluated",
            "hard-gate",
        )
    )
    assert tuple(item.observed_fold_ids for item in certificate.fold_coverage) == (
        FOLD_IDS,
        FOLD_IDS,
    )


def test_certificate_excludes_unpermitted_hard_gate_rejections() -> None:
    profile = _profile(minimum_eligible=2)
    trial_terminals = (
        ("evaluated", CandidateTrialTerminal.EVALUATED),
        ("hard-gate", CandidateTrialTerminal.REJECTED_HARD_GATE),
    )

    certificate = _certificate(
        profile,
        _search_run(profile, trial_terminals),
        _scores(("evaluated", "hard-gate")),
    )

    assert certificate.decision is PboCertificateDecision.FAIL
    assert "eligible and matrix trial IDs are not exactly equal" in _reasons(certificate)


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("metric_name", "other-score"),
        ("threshold", Decimal("-1")),
        ("comparison", GateComparison.GT),
    ),
)
def test_certificate_rejects_gate_evidence_that_differs_from_the_frozen_profile(
    field: str, value: object
) -> None:
    profile = _profile()
    search_run = _search_run(profile)
    evaluation = search_run.trials[0].evaluation
    assert evaluation is not None
    altered_evaluation = evaluation.model_copy(
        update={"gate_results": (evaluation.gate_results[0].model_copy(update={field: value}),)}
    )
    altered_trial = search_run.trials[0].model_copy(update={"evaluation": altered_evaluation})
    altered_run = search_run.model_copy(update={"trials": (altered_trial, *search_run.trials[1:])})

    certificate = _certificate(profile, altered_run, _scores())

    assert certificate.decision is PboCertificateDecision.FAIL
    assert any(
        "identity, metric, threshold, or comparison differs" in reason
        for reason in _reasons(certificate)
    )


def test_certificate_fails_when_authoritative_started_and_terminal_ledger_entries_differ() -> None:
    profile = _profile()
    search_run = _search_run(profile)
    dangling_trial = search_run.trials[-1].model_copy(update={"terminal": None, "evaluation": None})
    incomplete_run = search_run.model_copy(
        update={"trials": (*search_run.trials[:-1], dangling_trial)}
    )

    certificate = _certificate(profile, incomplete_run, _scores())

    assert certificate.decision is PboCertificateDecision.FAIL
    assert certificate.reason is not None
    assert certificate.reason.code is ErrorCode.PBO_INCOMPLETE
    assert "search run violates authoritative completed-ledger invariants" in _reasons(certificate)
    assert "started and terminal trial IDs are not exactly equal" in _reasons(certificate)
    with pytest.raises(DomainError) as error:
        build_trial_fold_matrix(
            certificate=certificate,
            scores=_scores(),
            score_direction=RankingDirection.DESC,
        )
    assert error.value.detail == certificate.reason


@pytest.mark.parametrize(
    ("case", "reason_fragment"),
    (
        ("search-profile", "search run profile hash does not match the validation profile"),
        ("policy-profile", "eligibility policy profile hash does not match the validation profile"),
        (
            "unknown-policy-gate",
            "eligibility policy names hard gates absent from the validation profile",
        ),
        ("failed-search", "PBO requires a normally stopped search run"),
    ),
)
def test_certificate_rejects_policy_profile_and_search_lifecycle_mismatches(
    case: str, reason_fragment: str
) -> None:
    profile = _profile()
    search_run = _search_run(profile)
    policy = _policy(profile)
    if case == "search-profile":
        search_run = _search_run(profile, profile_hash=_digest(99))
    elif case == "policy-profile":
        policy = PboEligibilityPolicy(profile_hash=_digest(99), eligible_hard_gate_ids=())
    elif case == "unknown-policy-gate":
        policy = _policy(profile, allowed_gates=("unknown-gate",))
    else:
        search_run = _search_run(profile, failure_reason=SearchFailureReason.OPERATOR_FAILED)

    certificate = _certificate(profile, search_run, _scores(), eligibility_policy=policy)

    assert certificate.decision is PboCertificateDecision.FAIL
    assert certificate.reason is not None
    assert certificate.reason.code is ErrorCode.PBO_INCOMPLETE
    assert any(reason_fragment in reason for reason in _reasons(certificate))


@pytest.mark.parametrize(
    ("scores", "reason_fragment"),
    (
        (_scores()[:-1], "exact profile fold set"),
        (_scores() + (_scores()[0],), "duplicate trial/fold score"),
        (
            (
                TrialFoldScore(trial_id="alpha", fold_id="fold-0", score=Decimal("NaN")),
                *_scores()[1:],
            ),
            "non-finite or non-Decimal score",
        ),
        (
            (
                *_scores(),
                TrialFoldScore(trial_id="unreported", fold_id="fold-0", score=Decimal("1")),
            ),
            "matrix contains a score for a non-eligible terminal",
        ),
    ),
)
def test_certificate_rejects_incomplete_or_non_authorized_matrix_scores(
    scores: tuple[TrialFoldScore, ...], reason_fragment: str
) -> None:
    profile = _profile()

    certificate = _certificate(profile, _search_run(profile), scores)

    assert certificate.decision is PboCertificateDecision.FAIL
    assert certificate.reason is not None
    assert certificate.reason.code is ErrorCode.PBO_INCOMPLETE
    assert any(reason_fragment in reason for reason in _reasons(certificate))


@pytest.mark.parametrize(
    ("scores", "reason_fragment"),
    (
        (_scores()[:-1], "scores do not exactly match the certified matrix"),
        (
            (
                TrialFoldScore(trial_id="alpha", fold_id="fold-0", score=Decimal("9")),
                *_scores()[1:],
            ),
            "scores do not match the certificate matrix digest",
        ),
        (
            (
                TrialFoldScore(trial_id="alpha", fold_id="fold-0", score=Decimal("NaN")),
                *_scores()[1:],
            ),
            "certified matrix contains a non-finite score",
        ),
    ),
)
def test_bound_matrix_rejects_missing_or_tampered_scores(
    scores: tuple[TrialFoldScore, ...], reason_fragment: str
) -> None:
    profile = _profile()
    certificate, _ = _complete_matrix(profile, _scores())

    with pytest.raises(DomainError) as error:
        build_trial_fold_matrix(
            certificate=certificate,
            scores=scores,
            score_direction=RankingDirection.DESC,
        )
    assert error.value.detail.code is ErrorCode.PBO_INCOMPLETE
    assert error.value.detail.message == reason_fragment


def test_calculate_pbo_uses_ascending_tie_breaks() -> None:
    profile = _profile(
        minimum_eligible=2,
        fold_ids=("fold-0", "fold-1"),
        ranking_direction=RankingDirection.ASC,
    )
    scores = (
        TrialFoldScore(trial_id="alpha", fold_id="fold-0", score=Decimal("1")),
        TrialFoldScore(trial_id="alpha", fold_id="fold-1", score=Decimal("4")),
        TrialFoldScore(trial_id="beta", fold_id="fold-0", score=Decimal("1")),
        TrialFoldScore(trial_id="beta", fold_id="fold-1", score=Decimal("2")),
    )
    search_run = _search_run(
        profile,
        (("alpha", CandidateTrialTerminal.EVALUATED), ("beta", CandidateTrialTerminal.EVALUATED)),
    )
    certificate = _certificate(profile, search_run, scores, score_direction=RankingDirection.ASC)
    matrix = build_trial_fold_matrix(
        certificate=certificate,
        scores=scores,
        score_direction=RankingDirection.ASC,
    )

    result = calculate_pbo(matrix, certificate=certificate, decimal_precision=28)

    assert tuple(
        (outcome.selected_trial_id, outcome.out_of_sample_rank, outcome.overfit)
        for outcome in result.partitions
    ) == (("alpha", 1, True), ("beta", 2, False))
    assert result.probability_of_backtest_overfitting == Decimal("0.5")
    with pytest.raises(ValueError, match="decimal_precision must be positive"):
        calculate_pbo(matrix, certificate=certificate, decimal_precision=0)


@pytest.mark.parametrize(
    ("field", "value", "message"),
    (
        ("normal_stop", False, "requires a normally stopped search"),
        ("eligible_trial_ids", ("alpha", "beta"), "requires eligible=matrix"),
        ("fold_coverage", (), "requires coverage for every matrix trial"),
        (
            "expected_fold_ids",
            ("fold-0", "fold-1"),
            "requires the exact fold set on every row",
        ),
        ("minimum_eligible", 4, "requires the minimum eligible count"),
    ),
)
def test_passing_certificate_refuses_structural_tampering(
    field: str, value: object, message: str
) -> None:
    profile = _profile()
    certificate = _certificate(profile, _search_run(profile), _scores())

    with pytest.raises(ValueError, match=message):
        replace(certificate, **{field: value})


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("lineage_id", "tampered-lineage"),
        ("validation_id", "tampered-validation"),
        ("search_run_hash", _digest(98)),
        ("matrix_digest", _digest(99)),
    ),
)
def test_calculate_pbo_rejects_tampered_certificate_or_matrix_identity(
    field: str, value: str
) -> None:
    profile = _profile()
    certificate, matrix = _complete_matrix(profile, _scores())
    tampered_certificate = replace(certificate, **{field: value})
    unbound_matrix = replace(matrix, certificate_hash=None, matrix_digest=None)

    for candidate_certificate, candidate_matrix in (
        (tampered_certificate, matrix),
        (certificate, unbound_matrix),
    ):
        with pytest.raises(DomainError) as error:
            calculate_pbo(
                candidate_matrix,
                certificate=candidate_certificate,
                decimal_precision=28,
            )
        assert error.value.detail.code is ErrorCode.PBO_INCOMPLETE
        assert error.value.detail.message == "matrix is not bound to the passing PBO certificate"


def test_bound_matrix_rejects_a_mismatched_score_direction() -> None:
    profile = _profile()
    certificate, _ = _complete_matrix(profile, _scores())

    with pytest.raises(DomainError) as error:
        build_trial_fold_matrix(
            certificate=certificate,
            scores=_scores(),
            score_direction=RankingDirection.ASC,
        )
    assert error.value.detail.code is ErrorCode.PBO_INCOMPLETE
    assert error.value.detail.message == "score direction does not match the PBO certificate"


@pytest.mark.parametrize(
    ("fold_ids", "reason_fragment"),
    (
        (("fold-0",), "even number of at least two folds"),
        (("fold-0", "fold-0"), "fold IDs must be unique"),
    ),
)
def test_construct_cscv_partitions_rejects_invalid_fold_definitions(
    fold_ids: tuple[str, ...], reason_fragment: str
) -> None:
    with pytest.raises(ValueError, match=reason_fragment):
        construct_cscv_partitions(fold_ids)
