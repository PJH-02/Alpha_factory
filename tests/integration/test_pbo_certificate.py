from __future__ import annotations

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
    certify_pbo_input,
)

FOLD_IDS = ("fold-0", "fold-1", "fold-2", "fold-3")
LINEAGE_ID = "integration-lineage"
VALIDATION_ID = "integration-validation"


def _digest(number: int) -> str:
    return f"sha256:{number:064x}"


def _profile() -> ValidationProfile:
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
                direction=RankingDirection.DESC,
                quantization=Decimal("0.01"),
            ),
        ),
        fold_ids=FOLD_IDS,
        pbo_minimum_eligible=2,
        patience=2,
        holdout_policy_hash=_digest(2),
        disclosure_policy_hash=_digest(3),
        allowed_disclosure_fields=(),
    )


def _gate_result(gate: ValidationGate, decision: Decision) -> GateResult:
    return GateResult(
        gate_id=gate.gate_id,
        decision=decision,
        metric_name=gate.metric_name,
        threshold=gate.threshold,
        comparison=gate.comparison,
        observed_value=gate.threshold
        + (Decimal("1") if decision is Decision.PASS else Decimal("-1")),
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
        gate_results = tuple(_gate_result(gate, Decision.FAIL) for gate in profile.hard_gates)
        return TrialEvaluation(
            terminal=terminal,
            gate_results=gate_results,
            complete=True,
            error_code="hard-gate-rejected",
        )
    return TrialEvaluation(
        terminal=terminal,
        complete=False,
        error_code="preflight-rejected",
    )


def _search_run(profile: ValidationProfile) -> SearchRunResult:
    terminals = (
        ("evaluated", CandidateTrialTerminal.EVALUATED),
        ("hard-gate", CandidateTrialTerminal.REJECTED_HARD_GATE),
        ("preflight", CandidateTrialTerminal.REJECTED_PREFLIGHT),
    )
    candidate_hashes = tuple(_digest(index + 100) for index in range(len(terminals)))
    evaluations = tuple(_evaluation(profile, terminal) for _, terminal in terminals)
    return SearchRunResult(
        search_spec_hash=_digest(10),
        universe_hash=_digest(11),
        profile_hash=profile.profile_hash,
        stop_reason=SearchStopReason.UNIVERSE_EXHAUSTED,
        failure_reason=None,
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
                zip(terminals, evaluations, strict=True)
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
        trials=tuple(
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
            for index, (trial_id, terminal) in enumerate(terminals)
        ),
    )


def _scores() -> tuple[TrialFoldScore, ...]:
    return tuple(
        TrialFoldScore(
            trial_id=trial_id,
            fold_id=fold_id,
            score=Decimal(trial_index * 10 + fold_index),
        )
        for trial_index, trial_id in enumerate(("evaluated", "hard-gate"), start=1)
        for fold_index, fold_id in enumerate(FOLD_IDS, start=1)
    )


def _certificate(
    profile: ValidationProfile,
    search_run: SearchRunResult,
    scores: tuple[TrialFoldScore, ...],
) -> PboCompletenessCertificate:
    return certify_pbo_input(
        profile=profile,
        eligibility_policy=PboEligibilityPolicy(
            profile_hash=profile.profile_hash,
            eligible_hard_gate_ids=("quality",),
        ),
        scores=scores,
        lineage_id=LINEAGE_ID,
        validation_id=VALIDATION_ID,
        search_run=search_run,
        score_direction=RankingDirection.DESC,
    )


def test_complete_authoritative_ledger_certifies_before_building_the_pbo_matrix() -> None:
    profile = _profile()
    scores = _scores()
    certificate = _certificate(profile, _search_run(profile), scores)

    matrix = build_trial_fold_matrix(
        certificate=certificate,
        scores=tuple(reversed(scores)),
        score_direction=RankingDirection.DESC,
    )

    assert certificate.decision is PboCertificateDecision.PASS
    assert (
        certificate.lineage_id,
        certificate.validation_id,
        certificate.profile_hash,
        certificate.score_direction,
    ) == (LINEAGE_ID, VALIDATION_ID, profile.profile_hash, RankingDirection.DESC)
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
    assert matrix.trial_ids == certificate.matrix_trial_ids
    assert matrix.fold_ids == FOLD_IDS
    assert matrix.scores == tuple(sorted(scores, key=lambda score: (score.trial_id, score.fold_id)))


def test_incomplete_authoritative_started_terminal_ledger_cannot_build_a_pbo_matrix() -> None:
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
    assert any(
        "started and terminal trial IDs are not exactly equal" in detail.reason
        for detail in certificate.reason.details
    )
    with pytest.raises(DomainError) as error:
        build_trial_fold_matrix(
            certificate=certificate,
            scores=_scores(),
            score_direction=RankingDirection.DESC,
        )
    assert error.value.detail == certificate.reason
