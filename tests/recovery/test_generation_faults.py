from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import pytest
from pydantic import BaseModel, ConfigDict

from alpha_foundry.application.generation import (
    GenerationCommand,
    GenerationReplayError,
    GenerationService,
)
from alpha_foundry.domain import Domain, ProviderModel
from alpha_foundry.generation import (
    ArtifactRef,
    AttemptState,
    GenerationAttempt,
    GenerationLease,
    GenerationRecord,
    GenerationRequest,
    GenerationState,
    ProviderRequest,
    ProviderResponse,
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
class RecordingGenerationRepository:
    records: dict[str, GenerationRecord] = field(default_factory=dict)
    attempts: list[GenerationAttempt] = field(default_factory=list)

    def validate_request_pins(self, request: GenerationRequest) -> None:
        assert request.claim_pins

    def get_or_create(self, request: GenerationRequest) -> GenerationRecord:
        existing = self.records.get(request.request_hash)
        if existing is not None:
            return existing
        created = GenerationRecord(
            generation_request_id="generation-1",
            request=request,
            state=GenerationState.AVAILABLE,
            next_ordinal=0,
        )
        self.records[request.request_hash] = created
        return created

    def read(self, request_hash: str) -> GenerationRecord:
        return self.records[request_hash]

    def acquire(self, record: GenerationRecord, owner_job_id: str) -> GenerationLease | None:
        current = self.read(record.request.request_hash)
        if current.state is not GenerationState.AVAILABLE:
            return None
        self.records[current.request.request_hash] = current.model_copy(
            update={"state": GenerationState.RUNNING, "owner_job_id": owner_job_id}
        )
        return GenerationLease(
            generation_request_id=current.generation_request_id,
            owner_token="owner-token",
            owner_epoch=1,
            row_version=2,
            next_ordinal=0,
        )

    def start_attempt(
        self,
        lease: GenerationLease,
        provider_model: ProviderModel,
        request_hash: str,
    ) -> GenerationAttempt:
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
        terminal = attempt.model_copy(
            update={"state": state, "error_code": error_code, "response_hash": response_hash}
        )
        self.attempts = [
            terminal if saved.attempt_id == terminal.attempt_id else saved
            for saved in self.attempts
        ]
        record = self._record_for(lease)
        next_ordinal = lease.next_ordinal + (1 if advance_chain else 0)
        failed = record.model_copy(
            update={
                "state": GenerationState.FAILED,
                "owner_job_id": None,
                "next_ordinal": next_ordinal,
            }
        )
        self.records[record.request.request_hash] = failed
        return failed

    def accept_attempt(
        self,
        lease: GenerationLease,
        attempt: GenerationAttempt,
        *,
        response_hash: str,
        artifact: ArtifactRef,
    ) -> GenerationRecord:
        raise AssertionError("invalid JSON must not be accepted")

    def _record_for(self, lease: GenerationLease) -> GenerationRecord:
        return next(
            record
            for record in self.records.values()
            if record.generation_request_id == lease.generation_request_id
        )


@dataclass
class CrashAfterStartProvider:
    repository: RecordingGenerationRepository
    calls: list[ProviderRequest] = field(default_factory=list)

    def complete(self, request: ProviderRequest) -> ProviderResponse:
        self.calls.append(request)
        assert self.repository.attempts[-1].state is AttemptState.STARTED
        raise RuntimeError("simulated process death after provider dispatch")


def _command(owner_job_id: str = "job-1") -> GenerationCommand:
    claim = Claim(
        id="factor-claim",
        version="1.0.0",
        domain=Domain.FACTOR,
        statement="Evidence is pinned before generation.",
        source="research-note",
        citations=(Citation(source_id="source", locator="section", excerpt="evidence"),),
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
                provider="provider",
                model="model",
                config_hash=f"sha256:{'b' * 64}",
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


def test_schema_failure_is_persisted_as_invalid_and_never_accepts_an_artifact(
    tmp_path: Path,
) -> None:
    repository = RecordingGenerationRepository()
    provider = FakeProvider(['{"answer":7}'])
    service = GenerationService(
        repository,
        LocalArtifactStore(tmp_path / "artifacts"),
        {"provider": provider},
    )
    command = _command()

    result = service.generate(command)

    assert result.record.state is GenerationState.FAILED
    assert result.record.accepted_artifact is None
    assert result.artifact_json is None
    assert result.provider_calls == provider.call_count == 1
    assert len(repository.attempts) == 1
    attempt = repository.attempts[0]
    assert attempt.state is AttemptState.INVALID
    assert attempt.error_code == "LLM_SCHEMA"
    expected_response = ProviderResponse(
        content='{"answer":7}',
        raw_bytes=b'{"answer":7}',
    )
    assert attempt.response_hash == expected_response.response_hash


def test_replay_rejects_unaccepted_request_without_invoking_a_provider(
    tmp_path: Path,
) -> None:
    repository = RecordingGenerationRepository()
    provider = FakeProvider([])
    service = GenerationService(
        repository,
        LocalArtifactStore(tmp_path / "artifacts"),
        {"provider": provider},
    )
    command = _command()
    repository.get_or_create(command.request)

    with pytest.raises(GenerationReplayError, match="only ACCEPTED"):
        service.replay(command.request.request_hash, GeneratedAnswer)

    assert provider.call_count == 0


def test_crash_after_write_ahead_attempt_leaves_running_duplicate_without_a_second_provider_call(
    tmp_path: Path,
) -> None:
    repository = RecordingGenerationRepository()
    provider = CrashAfterStartProvider(repository)
    service = GenerationService(
        repository,
        LocalArtifactStore(tmp_path / "artifacts"),
        {"provider": provider},
    )
    command = _command()

    with pytest.raises(RuntimeError, match="simulated process death"):
        service.generate(command)

    persisted = repository.read(command.request.request_hash)
    assert persisted.state is GenerationState.RUNNING
    assert persisted.owner_job_id == "job-1"
    assert [(attempt.ordinal, attempt.state) for attempt in repository.attempts] == [
        (0, AttemptState.STARTED)
    ]

    duplicate = service.generate(_command(owner_job_id="job-2"))

    assert duplicate.record.state is GenerationState.RUNNING
    assert duplicate.provider_calls == 0
    assert len(provider.calls) == 1
    assert len(repository.attempts) == 1
