from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from pydantic import BaseModel, ConfigDict

from alpha_foundry.application.generation import GenerationCommand, GenerationService
from alpha_foundry.domain import Domain, ProviderModel
from alpha_foundry.generation import (
    ArtifactRef,
    AttemptState,
    GenerationAttempt,
    GenerationLease,
    GenerationRecord,
    GenerationRequest,
    GenerationState,
    ProviderFailure,
    ProviderFailureCode,
    response_schema_digest,
)
from alpha_foundry.infrastructure.artifacts import LocalArtifactStore
from alpha_foundry.infrastructure.llm import FakeProvider
from alpha_foundry.knowledge import (
    Citation,
    Claim,
    ClaimContext,
    KnowledgePack,
    order_claim_context,
)


class GeneratedAnswer(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    answer: str


@dataclass
class InMemoryGenerationRepository:
    records: dict[str, GenerationRecord] = field(default_factory=dict)
    attempts: list[GenerationAttempt] = field(default_factory=list)
    validated_requests: list[GenerationRequest] = field(default_factory=list)

    def validate_request_pins(self, request: GenerationRequest) -> None:
        self.validated_requests.append(request)

    def get_or_create(self, request: GenerationRequest) -> GenerationRecord:
        existing = self.records.get(request.request_hash)
        if existing is not None:
            return existing
        record = GenerationRecord(
            generation_request_id=f"request-{len(self.records) + 1}",
            request=request,
            state=GenerationState.AVAILABLE,
            next_ordinal=0,
        )
        self.records[request.request_hash] = record
        return record

    def read(self, request_hash: str) -> GenerationRecord:
        return self.records[request_hash]

    def acquire(self, record: GenerationRecord, owner_job_id: str) -> GenerationLease | None:
        current = self.read(record.request.request_hash)
        if current.state is not GenerationState.AVAILABLE:
            return None
        lease = GenerationLease(
            generation_request_id=current.generation_request_id,
            owner_token=f"owner-{current.generation_request_id}-1",
            owner_epoch=1,
            row_version=2,
            next_ordinal=current.next_ordinal,
        )
        self.records[current.request.request_hash] = current.model_copy(
            update={"state": GenerationState.RUNNING, "owner_job_id": owner_job_id}
        )
        return lease

    def start_attempt(
        self,
        lease: GenerationLease,
        provider_model: ProviderModel,
        request_hash: str,
    ) -> GenerationAttempt:
        assert lease.next_ordinal == provider_model.ordinal
        assert not any(
            existing.generation_request_id == lease.generation_request_id
            and existing.ordinal == lease.next_ordinal
            for existing in self.attempts
        )
        attempt = GenerationAttempt(
            attempt_id=f"attempt-{len(self.attempts) + 1}",
            generation_request_id=lease.generation_request_id,
            ordinal=lease.next_ordinal,
            provider_model=provider_model,
            request_hash=request_hash,
            state=AttemptState.STARTED,
        )
        self.attempts.append(attempt)
        return attempt

    def terminalize_attempt(
        self,
        lease: GenerationLease,
        attempt: GenerationAttempt,
        *,
        state: AttemptState,
        error_code: str | None,
        response_hash: str | None,
        advance_chain: bool,
    ) -> GenerationLease | GenerationRecord:
        assert attempt.state is AttemptState.STARTED
        assert state in {AttemptState.FAILED, AttemptState.INVALID}
        self._replace_attempt(
            attempt.model_copy(
                update={
                    "state": state,
                    "error_code": error_code,
                    "response_hash": response_hash,
                }
            )
        )
        record = self._record_for_lease(lease)
        next_ordinal = lease.next_ordinal + 1
        if not advance_chain or next_ordinal >= len(record.request.provider_chain):
            failed = record.model_copy(
                update={
                    "state": GenerationState.FAILED,
                    "owner_job_id": None,
                    "next_ordinal": next_ordinal if advance_chain else lease.next_ordinal,
                }
            )
            self.records[record.request.request_hash] = failed
            return failed
        self.records[record.request.request_hash] = record.model_copy(
            update={"next_ordinal": next_ordinal}
        )
        return lease.model_copy(
            update={"next_ordinal": next_ordinal, "row_version": lease.row_version + 1}
        )

    def accept_attempt(
        self,
        lease: GenerationLease,
        attempt: GenerationAttempt,
        *,
        response_hash: str,
        artifact: ArtifactRef,
    ) -> GenerationRecord:
        assert attempt.state is AttemptState.STARTED
        self._replace_attempt(
            attempt.model_copy(
                update={
                    "state": AttemptState.SUCCEEDED_VALID,
                    "response_hash": response_hash,
                    "accepted_artifact": artifact,
                }
            )
        )
        record = self._record_for_lease(lease)
        accepted = record.model_copy(
            update={
                "state": GenerationState.ACCEPTED,
                "owner_job_id": None,
                "accepted_artifact": artifact,
            }
        )
        self.records[record.request.request_hash] = accepted
        return accepted

    def _record_for_lease(self, lease: GenerationLease) -> GenerationRecord:
        return next(
            record
            for record in self.records.values()
            if record.generation_request_id == lease.generation_request_id
        )

    def _replace_attempt(self, replacement: GenerationAttempt) -> None:
        self.attempts = [
            replacement if attempt.attempt_id == replacement.attempt_id else attempt
            for attempt in self.attempts
        ]


def _command(owner_job_id: str = "job-1") -> GenerationCommand:
    claim = Claim(
        id="factor-claim",
        version="1.0.0",
        domain=Domain.FACTOR,
        statement="Use only cited factor evidence.",
        source="research-note",
        citations=(Citation(source_id="source", locator="section", excerpt="factor evidence"),),
    )
    pack = KnowledgePack(id="factor-pack", version="1.0.0", claims=(claim,))
    context: tuple[ClaimContext, ...] = order_claim_context(pack.claims, domain=Domain.FACTOR)
    request = GenerationRequest(
        capability_snapshot_hash=f"sha256:{'a' * 64}",
        knowledge_pack_hash=pack.content_hash,
        claim_pins=(claim.pin(),),
        domain=Domain.FACTOR,
        provider_chain=(
            ProviderModel(
                ordinal=0,
                provider="primary",
                model="primary-model",
                config_hash=f"sha256:{'b' * 64}",
            ),
            ProviderModel(
                ordinal=1,
                provider="fallback",
                model="fallback-model",
                config_hash=f"sha256:{'c' * 64}",
            ),
        ),
        request_schema_hash=response_schema_digest(
            GeneratedAnswer.model_json_schema(mode="validation")
        ),
        sampling={"seed": 7},
        constraints={"format": "json"},
        user_request={"task": "propose"},
    )
    return GenerationCommand(
        request=request,
        owner_job_id=owner_job_id,
        response_model=GeneratedAnswer,
        claim_context=context,
    )


def test_generation_persists_ordered_fallback_attempts_and_replays_without_provider_calls(
    tmp_path: Path,
) -> None:
    repository = InMemoryGenerationRepository()
    artifact_store = LocalArtifactStore(tmp_path / "artifacts")
    primary = FakeProvider(
        [ProviderFailure(ProviderFailureCode.TIMEOUT, "timed out", retryable=True)]
    )
    fallback = FakeProvider([{"answer": "accepted"}])
    service = GenerationService(
        repository,
        artifact_store,
        {"primary": primary, "fallback": fallback},
    )
    command = _command()

    generated = service.generate(command)

    assert generated.record.state is GenerationState.ACCEPTED
    assert generated.artifact_json == {"answer": "accepted"}
    assert generated.provider_calls == 2
    assert [
        (call.provider_model.provider, call.provider_model.model) for call in primary.calls
    ] == [("primary", "primary-model")]
    assert [
        (call.provider_model.provider, call.provider_model.model) for call in fallback.calls
    ] == [("fallback", "fallback-model")]
    assert [
        (attempt.ordinal, attempt.state, attempt.error_code) for attempt in repository.attempts
    ] == [
        (0, AttemptState.FAILED, ProviderFailureCode.TIMEOUT.value),
        (1, AttemptState.SUCCEEDED_VALID, None),
    ]
    accepted_artifact = generated.record.accepted_artifact
    assert accepted_artifact is not None
    assert artifact_store.read_bytes(accepted_artifact.cas_uri) == b'{"answer":"accepted"}'

    duplicate = service.generate(_command(owner_job_id="job-duplicate"))
    replayed = service.replay(command.request.request_hash, GeneratedAnswer)

    assert duplicate.provider_calls == replayed.provider_calls == 0
    assert duplicate.artifact_json == replayed.artifact_json == {"answer": "accepted"}
    assert primary.call_count == fallback.call_count == 1
    assert len(repository.attempts) == 2
