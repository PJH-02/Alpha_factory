"""Application orchestration for durable ordered generation and zero-call replay."""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from hashlib import sha256
from typing import Protocol
from unicodedata import normalize

from pydantic import BaseModel, ValidationError

from ..domain import ProviderModel
from ..generation import (
    ArtifactRef,
    AttemptState,
    GenerationAttempt,
    GenerationLease,
    GenerationRecord,
    GenerationRequest,
    GenerationResult,
    GenerationState,
    JsonValue,
    LlmProviderPort,
    ProviderFailure,
    ProviderFailureCode,
    ProviderRequest,
    ProviderResponse,
    response_schema_digest,
)
from ..knowledge import ClaimContext, ClaimPin
from .ports import ArtifactMetadata


class GenerationRepositoryPort(Protocol):
    """Narrow durable authority port; implementations own transactions and CAS checks."""

    def validate_request_pins(self, request: GenerationRequest) -> None:
        """Verify exact immutable capability, pack, claim, and schema pins."""

    def get_or_create(self, request: GenerationRequest) -> GenerationRecord:
        """Insert the unique AVAILABLE request or return its immutable existing row."""

    def read(self, request_hash: str) -> GenerationRecord:
        """Read a request row after a failed acquisition race."""

    def acquire(self, record: GenerationRecord, owner_job_id: str) -> GenerationLease | None:
        """CAS AVAILABLE to RUNNING and persist the owner event, or return no lease."""

    def start_attempt(
        self,
        lease: GenerationLease,
        provider_model: ProviderModel,
        request_hash: str,
    ) -> GenerationAttempt:
        """Durably insert the unique STARTED ordinal before a provider call."""

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
        """Atomically terminalize an attempt and either advance or fail the request."""

    def accept_attempt(
        self,
        lease: GenerationLease,
        attempt: GenerationAttempt,
        *,
        response_hash: str,
        artifact: ArtifactRef,
    ) -> GenerationRecord:
        """Atomically record valid success, accepted artifact, and request completion."""


class ArtifactStorePort(Protocol):
    """Content-addressed JSON artifact port used only after typed validation."""

    def put_bytes(self, data: bytes, media_type: str) -> ArtifactMetadata:
        """Store verified bytes or raise an ArtifactStoreFailure."""

    def read_bytes(self, cas_uri: str) -> bytes:
        """Read an accepted payload or raise an ArtifactStoreFailure."""


class ArtifactStoreFailureCode(StrEnum):
    """Safe reason codes for expected artifact-port failures."""

    IO = "ARTIFACT_IO"
    PROTOCOL = "ARTIFACT_PROTOCOL"


class ArtifactStoreFailure(OSError):
    """Expected artifact-port failure that can be terminalized without leaking details."""

    def __init__(self, code: ArtifactStoreFailureCode) -> None:
        if not isinstance(code, ArtifactStoreFailureCode):
            raise TypeError("artifact store failures require a registered reason code")
        self.code = code
        super().__init__(code.value)


class _ArtifactStoreProtocolError(ArtifactStoreFailure):
    """The artifact port returned bytes or metadata outside its declared contract."""

    def __init__(self) -> None:
        super().__init__(ArtifactStoreFailureCode.PROTOCOL)


@dataclass(frozen=True, slots=True)
class GenerationCommand:
    """Trusted application input; the response model is code-owned, never provider-owned."""

    request: GenerationRequest
    owner_job_id: str
    response_model: type[BaseModel]
    claim_context: tuple[ClaimContext, ...]

    def __post_init__(self) -> None:
        if not self.owner_job_id.strip():
            raise ValueError("owner_job_id must not be blank")
        valid_model = isinstance(self.response_model, type) and issubclass(
            self.response_model, BaseModel
        )
        if not valid_model:
            raise ValueError("response_model must be a trusted Pydantic model class")
        response_schema = _response_schema(self.response_model)
        expected_schema_hash = response_schema_digest(response_schema)
        if self.request.request_schema_hash != expected_schema_hash:
            raise ValueError("generation request schema is not bound to the response model")
        canonical_context = _canonicalize_context(self.request, self.claim_context)
        object.__setattr__(self, "claim_context", canonical_context)


class GenerationReplayError(ValueError):
    """Raised when replay is requested for a request without an accepted artifact."""

    error_code = "AF-STATE-001"


class GenerationStorageError(RuntimeError):
    """Raised only after a storage failure has been terminalized durably."""

    error_code = "AF-STORAGE-001"


class GenerationService:
    """Runs exactly the snapshotted provider chain and never executes generated code."""

    def __init__(
        self,
        repository: GenerationRepositoryPort,
        artifact_store: ArtifactStorePort,
        providers: Mapping[str, LlmProviderPort],
    ) -> None:
        self._repository = repository
        self._artifact_store = artifact_store
        self._providers = dict(providers)

    def generate(self, command: GenerationCommand) -> GenerationResult:
        """Acquire, write-ahead each attempt, validate JSON, and accept exactly once."""

        response_schema = _response_schema(command.response_model)
        if command.request.request_schema_hash != response_schema_digest(response_schema):
            raise ValueError("generation request schema is not bound to the response model")
        self._repository.validate_request_pins(command.request)
        record = self._repository.get_or_create(command.request)
        if record.state is GenerationState.ACCEPTED:
            return self._accepted_result(record, command.response_model)
        if record.state in {GenerationState.FAILED, GenerationState.RUNNING}:
            return GenerationResult(record=record, provider_calls=0)

        lease = self._repository.acquire(record, command.owner_job_id)
        if lease is None:
            refreshed = self._repository.read(command.request.request_hash)
            if refreshed.state is GenerationState.ACCEPTED:
                return self._accepted_result(refreshed, command.response_model)
            return GenerationResult(record=refreshed, provider_calls=0)

        provider_calls = 0
        payload = command.request.prompt_input(_context_payload(command.claim_context))
        while True:
            if lease.next_ordinal >= len(command.request.provider_chain):
                raise RuntimeError(
                    "running generation lease has an ordinal beyond its pinned chain"
                )
            provider_model = command.request.provider_chain[lease.next_ordinal]
            attempt = self._repository.start_attempt(
                lease,
                provider_model,
                command.request.request_hash,
            )
            provider = self._providers.get(provider_model.provider)
            if provider is None:
                terminal = self._repository.terminalize_attempt(
                    lease,
                    attempt,
                    state=AttemptState.FAILED,
                    error_code=ProviderFailureCode.UNREGISTERED.value,
                    response_hash=None,
                    advance_chain=False,
                )
                if isinstance(terminal, GenerationRecord):
                    return GenerationResult(record=terminal, provider_calls=provider_calls)
                lease = terminal
                continue

            try:
                response_value: object = provider.complete(
                    ProviderRequest(
                        request_hash=command.request.request_hash,
                        provider_model=provider_model,
                        payload=payload,
                        response_schema=response_schema,
                    )
                )
                provider_calls += 1
            except ProviderFailure as failure:
                provider_calls += 1
                terminal = self._repository.terminalize_attempt(
                    lease,
                    attempt,
                    state=AttemptState.FAILED,
                    error_code=failure.code.value,
                    response_hash=None,
                    advance_chain=failure.retryable,
                )
                if isinstance(terminal, GenerationRecord):
                    return GenerationResult(record=terminal, provider_calls=provider_calls)
                lease = terminal
                continue
            if not isinstance(response_value, ProviderResponse):
                terminal = self._repository.terminalize_attempt(
                    lease,
                    attempt,
                    state=AttemptState.FAILED,
                    error_code=ProviderFailureCode.RESPONSE.value,
                    response_hash=None,
                    advance_chain=False,
                )
                if isinstance(terminal, GenerationRecord):
                    return GenerationResult(record=terminal, provider_calls=provider_calls)
                lease = terminal
                continue
            response = response_value

            try:
                accepted_json = _validated_json(response.content, command.response_model)
            except (json.JSONDecodeError, ValidationError, ValueError):
                terminal = self._repository.terminalize_attempt(
                    lease,
                    attempt,
                    state=AttemptState.INVALID,
                    error_code=ProviderFailureCode.SCHEMA.value,
                    response_hash=response.response_hash,
                    advance_chain=False,
                )
                if isinstance(terminal, GenerationRecord):
                    return GenerationResult(record=terminal, provider_calls=provider_calls)
                lease = terminal
                continue

            artifact_bytes = _canonical_json_bytes(accepted_json)
            try:
                stored_artifact = self._artifact_store.put_bytes(artifact_bytes, "application/json")
                artifact = _accepted_artifact_ref(
                    stored_artifact,
                    artifact_bytes,
                    "application/json",
                )
            except ArtifactStoreFailure as error:
                return self._terminalize_artifact_failure(
                    lease,
                    attempt,
                    response.response_hash,
                    error.code,
                    error,
                    provider_calls,
                )
            except OSError as error:
                return self._terminalize_artifact_failure(
                    lease,
                    attempt,
                    response.response_hash,
                    ArtifactStoreFailureCode.IO,
                    error,
                    provider_calls,
                )
            except (AttributeError, TypeError, ValueError) as error:
                return self._terminalize_artifact_failure(
                    lease,
                    attempt,
                    response.response_hash,
                    ArtifactStoreFailureCode.PROTOCOL,
                    error,
                    provider_calls,
                )

            accepted = self._repository.accept_attempt(
                lease,
                attempt,
                response_hash=response.response_hash,
                artifact=artifact,
            )
            return GenerationResult(
                record=accepted,
                artifact_json=accepted_json,
                provider_calls=provider_calls,
            )

    def _terminalize_artifact_failure(
        self,
        lease: GenerationLease,
        attempt: GenerationAttempt,
        response_hash: str,
        error_code: ArtifactStoreFailureCode,
        error: Exception,
        provider_calls: int,
    ) -> GenerationResult:
        terminal = self._repository.terminalize_attempt(
            lease,
            attempt,
            state=AttemptState.FAILED,
            error_code=error_code.value,
            response_hash=response_hash,
            advance_chain=False,
        )
        if isinstance(terminal, GenerationRecord) and terminal.state is GenerationState.FAILED:
            return GenerationResult(record=terminal, provider_calls=provider_calls)
        raise GenerationStorageError(
            "artifact failure did not terminalize the generation request"
        ) from error

    def replay(self, request_hash: str, response_model: type[BaseModel]) -> GenerationResult:
        """Return an accepted artifact without acquiring a lease or invoking a provider."""

        record = self._repository.read(request_hash)
        if record.state is not GenerationState.ACCEPTED:
            raise GenerationReplayError("only ACCEPTED generation requests can be replayed")
        return self._accepted_result(record, response_model)

    def _accepted_result(
        self,
        record: GenerationRecord,
        response_model: type[BaseModel],
    ) -> GenerationResult:
        response_schema = _response_schema(response_model)
        if record.request.request_schema_hash != response_schema_digest(response_schema):
            raise GenerationReplayError(
                "accepted generation request schema does not match the replay response model"
            )
        artifact = record.accepted_artifact
        if artifact is None:
            raise GenerationReplayError("accepted generation request has no accepted artifact")
        try:
            payload = self._artifact_store.read_bytes(artifact.cas_uri)
            _verify_accepted_artifact(artifact, payload, "application/json")
            accepted_json = _validated_json(payload.decode("utf-8"), response_model)
        except (
            OSError,
            TypeError,
            UnicodeDecodeError,
            json.JSONDecodeError,
            ValidationError,
            ValueError,
        ) as error:
            raise GenerationStorageError(
                "accepted artifact no longer validates as typed JSON"
            ) from error
        return GenerationResult(record=record, artifact_json=accepted_json, provider_calls=0)


def _canonicalize_context(
    request: GenerationRequest, context: tuple[ClaimContext, ...]
) -> tuple[ClaimContext, ...]:
    if not isinstance(context, tuple):
        raise ValueError("generation context must be an immutable tuple")
    contexts_by_pin: dict[tuple[str, str, str], ClaimContext] = {}
    for item in context:
        if not isinstance(item, ClaimContext):
            raise ValueError("generation context entries must be typed ClaimContext values")
        pin = ClaimPin.from_claim(item.claim)
        pin_key = (pin.id, pin.version, pin.hash)
        if pin_key in contexts_by_pin:
            raise ValueError("generation context cannot contain duplicate pinned claim revisions")
        if item.claim.domain is not request.domain:
            raise ValueError(
                "generation context contains a claim outside the request primary domain"
            )
        contexts_by_pin[pin_key] = item

    expected_pins = tuple((pin.id, pin.version, pin.hash) for pin in request.claim_pins)
    if set(contexts_by_pin) != set(expected_pins):
        raise ValueError(
            "generation context must contain exactly the request's pinned claim revisions"
        )
    return tuple(contexts_by_pin[pin] for pin in expected_pins)


def _context_payload(context: tuple[ClaimContext, ...]) -> tuple[Mapping[str, JsonValue], ...]:
    return tuple(
        {
            "content_hash": item.claim.content_hash,
            "id": item.claim.id,
            "text": item.full_text,
            "version": item.claim.version,
        }
        for item in context
    )


def _response_schema(response_model: type[BaseModel]) -> Mapping[str, JsonValue]:
    if not isinstance(response_model, type) or not issubclass(response_model, BaseModel):
        raise ValueError("response_model must be a trusted Pydantic model class")
    schema = response_model.model_json_schema(mode="validation")
    return _json_mapping(schema)


def _validated_json(content: str, response_model: type[BaseModel]) -> Mapping[str, JsonValue]:
    decoded = json.loads(content, object_pairs_hook=_reject_duplicate_json_keys)
    if not isinstance(decoded, dict):
        raise ValueError("provider response must be a JSON object")
    model = response_model.model_validate(decoded, strict=True)
    return _json_mapping(model.model_dump(mode="json", by_alias=True, exclude_none=False))


def _reject_duplicate_json_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
    decoded: dict[str, object] = {}
    normalized_keys: set[str] = set()
    for key, value in pairs:
        normalized_key = normalize("NFC", key)
        if normalized_key in normalized_keys:
            raise ValueError("JSON objects must not contain duplicate normalized keys")
        normalized_keys.add(normalized_key)
        decoded[normalized_key] = value
    return decoded


def _json_mapping(value: object) -> Mapping[str, JsonValue]:
    encoded = json.dumps(
        value, ensure_ascii=False, allow_nan=False, separators=(",", ":"), sort_keys=True
    )
    decoded = json.loads(encoded)
    _reject_floats(decoded)
    if not isinstance(decoded, dict):
        raise ValueError("JSON value must be an object")
    return decoded


def _reject_floats(value: object) -> None:
    if isinstance(value, float):
        raise ValueError("generated JSON cannot contain floating-point values")
    if isinstance(value, Mapping):
        for item in value.values():
            _reject_floats(item)
    elif isinstance(value, list):
        for item in value:
            _reject_floats(item)


def _canonical_json_bytes(value: Mapping[str, JsonValue]) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def _accepted_artifact_ref(
    metadata: ArtifactMetadata,
    payload: bytes,
    media_type: str,
) -> ArtifactRef:
    content_hash = f"sha256:{sha256(payload).hexdigest()}"
    expected_uri = _cas_uri(content_hash)
    cas_uri = metadata.cas_uri
    stored_hash = metadata.content_hash
    byte_size = metadata.byte_size
    stored_media_type = metadata.media_type
    if (
        not isinstance(cas_uri, str)
        or not isinstance(stored_hash, str)
        or not isinstance(byte_size, int)
        or isinstance(byte_size, bool)
        or not isinstance(stored_media_type, str)
        or cas_uri != expected_uri
        or stored_hash != content_hash
        or byte_size != len(payload)
        or stored_media_type != media_type
    ):
        raise _ArtifactStoreProtocolError()
    return ArtifactRef(
        cas_uri=cas_uri,
        content_hash=stored_hash,
        byte_size=byte_size,
        media_type=stored_media_type,
    )


def _verify_accepted_artifact(
    artifact: ArtifactRef,
    payload: object,
    media_type: str,
) -> None:
    if (
        artifact.cas_uri != _cas_uri(artifact.content_hash)
        or artifact.media_type != media_type
        or not isinstance(payload, bytes)
        or artifact.byte_size != len(payload)
        or artifact.content_hash != f"sha256:{sha256(payload).hexdigest()}"
    ):
        raise _ArtifactStoreProtocolError()


def _cas_uri(content_hash: str) -> str:
    return f"cas://sha256/{content_hash.removeprefix('sha256:')}"


__all__ = [
    "ArtifactStoreFailure",
    "ArtifactStoreFailureCode",
    "ArtifactStorePort",
    "GenerationCommand",
    "GenerationReplayError",
    "GenerationRepositoryPort",
    "GenerationService",
    "GenerationStorageError",
]
