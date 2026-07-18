"""Deterministic generation identities, provenance values, and provider ports."""

from __future__ import annotations

import json
from collections.abc import Mapping
from enum import StrEnum
from hashlib import sha256
from types import MappingProxyType
from typing import ClassVar, Final, Protocol
from unicodedata import normalize

from pydantic import Field, computed_field, field_validator, model_validator

from .domain import CapabilitySnapshot, Domain, FrozenModel, ProviderModel, digest
from .domain.models import Identifier
from .knowledge import ClaimPin

type JsonValue = (
    None
    | bool
    | int
    | str
    | list["JsonValue"]
    | tuple["JsonValue", ...]
    | dict[str, "JsonValue"]
    | Mapping[str, "JsonValue"]
)


class GenerationModel(FrozenModel):
    """Strict, frozen model base for deterministic generation contracts."""


class GenerationState(StrEnum):
    AVAILABLE = "AVAILABLE"
    RUNNING = "RUNNING"
    ACCEPTED = "ACCEPTED"
    FAILED = "FAILED"


class AttemptState(StrEnum):
    STARTED = "STARTED"
    SUCCEEDED_VALID = "SUCCEEDED_VALID"
    FAILED = "FAILED"
    INVALID = "INVALID"
    INTERRUPTED = "INTERRUPTED"


class ProviderFailureCode(StrEnum):
    """Reason codes retained on attempts beneath the public AF-LLM-001 boundary."""

    TIMEOUT = "LLM_TIMEOUT"
    QUOTA = "LLM_QUOTA"
    SCHEMA = "LLM_SCHEMA"
    TRANSPORT = "LLM_TRANSPORT"
    RESPONSE = "LLM_RESPONSE"
    UNREGISTERED = "LLM_UNREGISTERED"


_SAFE_PROVIDER_FAILURE_TEXT: Final[Mapping[ProviderFailureCode, str]] = MappingProxyType(
    {
        ProviderFailureCode.TIMEOUT: "provider request timed out",
        ProviderFailureCode.QUOTA: "provider quota rejected the request",
        ProviderFailureCode.SCHEMA: "provider request or response violated the schema",
        ProviderFailureCode.TRANSPORT: "provider transport failed",
        ProviderFailureCode.RESPONSE: "provider returned an invalid response",
        ProviderFailureCode.UNREGISTERED: "provider is not registered for this request",
    }
)


class ProviderFailure(Exception):
    """A safe provider failure; it deliberately never contains credential or prompt text."""

    def __init__(
        self, code: ProviderFailureCode, message: str | None = None, *, retryable: bool
    ) -> None:
        if not isinstance(code, ProviderFailureCode):
            raise TypeError("provider failures require a registered reason code")
        if not isinstance(retryable, bool):
            raise TypeError("provider failure retryable must be a boolean")
        del message
        self.code = code
        self.retryable = retryable
        super().__init__(f"{code.value}: {_SAFE_PROVIDER_FAILURE_TEXT[code]}")


class GenerationRequest(GenerationModel):
    """All exhaustive semantic inputs to an ordered, replayable LLM request."""

    capability_snapshot_hash: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    knowledge_pack_hash: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    claim_pins: tuple[ClaimPin, ...] = Field(min_length=1)
    domain: Domain
    provider_chain: tuple[ProviderModel, ...] = Field(min_length=1)
    request_schema_hash: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    sampling: Mapping[str, JsonValue]
    constraints: Mapping[str, JsonValue]
    user_request: Mapping[str, JsonValue]

    @field_validator(
        "capability_snapshot_hash",
        "knowledge_pack_hash",
        "request_schema_hash",
    )
    @classmethod
    def _lowercase_hash(cls, value: str) -> str:
        if value != value.lower():
            raise ValueError("hashes must use lowercase hexadecimal")
        return value

    @model_validator(mode="after")
    def _validate_identity_inputs(self) -> GenerationRequest:
        expected_ordinals = tuple(range(len(self.provider_chain)))
        actual_ordinals = tuple(entry.ordinal for entry in self.provider_chain)
        if actual_ordinals != expected_ordinals:
            raise ValueError("provider_chain ordinals must be contiguous and ordered from zero")

        pin_keys = tuple((pin.id, pin.version, pin.hash) for pin in self.claim_pins)
        if len(pin_keys) != len(set(pin_keys)):
            raise ValueError("claim_pins must not contain duplicate immutable references")
        if pin_keys != tuple(sorted(pin_keys)):
            raise ValueError("claim_pins must be ordered by ID, version, and hash")

        object.__setattr__(self, "sampling", _freeze_object(self.sampling))
        object.__setattr__(self, "constraints", _freeze_object(self.constraints))
        object.__setattr__(self, "user_request", _freeze_object(self.user_request))
        return self

    @classmethod
    def from_capability_snapshot(
        cls,
        *,
        capability_snapshot: CapabilitySnapshot,
        knowledge_pack_hash: str,
        claim_pins: tuple[ClaimPin, ...],
        domain: Domain,
        request_schema_hash: str,
        sampling: Mapping[str, JsonValue],
        constraints: Mapping[str, JsonValue],
        user_request: Mapping[str, JsonValue],
    ) -> GenerationRequest:
        """Build a request using only the snapshot's pinned provider/model chain."""
        if request_schema_hash not in capability_snapshot.schema_hashes:
            raise ValueError("request_schema_hash must belong to the capability snapshot")

        return cls(
            capability_snapshot_hash=capability_snapshot.content_hash,
            knowledge_pack_hash=knowledge_pack_hash,
            claim_pins=claim_pins,
            domain=domain,
            provider_chain=capability_snapshot.provider_chain,
            request_schema_hash=request_schema_hash,
            sampling=sampling,
            constraints=constraints,
            user_request=user_request,
        )

    @computed_field(return_type=str)  # type: ignore[prop-decorator]
    @property
    def request_hash(self) -> str:
        """The AF-CANON identity that makes duplicate/replay behavior deterministic."""

        return digest("AF:GENERATION_REQUEST:1", self.canonical_fields())

    def canonical_fields(self) -> dict[str, object]:
        return {
            "capability_snapshot_hash": _hash_bytes(self.capability_snapshot_hash),
            "claim_pins": [pin.semantic_fields() for pin in self.claim_pins],
            "constraints": _canonical_json(self.constraints),
            "domain": self.domain.value,
            "knowledge_pack_hash": _hash_bytes(self.knowledge_pack_hash),
            "provider_chain": [
                {
                    "config_hash": _hash_bytes(entry.config_hash),
                    "model": entry.model,
                    "ordinal": entry.ordinal,
                    "provider": entry.provider,
                }
                for entry in self.provider_chain
            ],
            "request_schema_hash": _hash_bytes(self.request_schema_hash),
            "sampling": _canonical_json(self.sampling),
            "user_request": _canonical_json(self.user_request),
        }

    def prompt_input(
        self, claim_context: tuple[Mapping[str, JsonValue], ...]
    ) -> Mapping[str, JsonValue]:
        """Return the complete untrusted provider input without any authority secret."""

        frozen_context = _validate_claim_context(self.claim_pins, claim_context)
        return MappingProxyType(
            {
                "claim_context": frozen_context,
                "constraints": self.constraints,
                "domain": self.domain.value,
                "request_schema_hash": self.request_schema_hash,
                "request_hash": self.request_hash,
                "sampling": self.sampling,
                "user_request": self.user_request,
            }
        )

    def provider_request(
        self,
        provider_model: ProviderModel,
        claim_context: tuple[Mapping[str, JsonValue], ...],
        response_schema: Mapping[str, JsonValue],
    ) -> ProviderRequest:
        """Build one request bound to this request's exact chain, context, and schema."""

        if (
            provider_model.ordinal >= len(self.provider_chain)
            or self.provider_chain[provider_model.ordinal] != provider_model
        ):
            raise ValueError("provider_model must exactly match the request provider chain")
        return ProviderRequest(
            request_hash=self.request_hash,
            provider_model=provider_model,
            payload=self.prompt_input(claim_context),
            response_schema=response_schema,
        )


class ProviderRequest(GenerationModel):
    """One registered provider call, carrying no credentials or mutable authority."""

    request_hash: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    provider_model: ProviderModel
    payload: Mapping[str, JsonValue]
    response_schema: Mapping[str, JsonValue]

    @model_validator(mode="after")
    def _freeze_payload(self) -> ProviderRequest:
        frozen_payload = _freeze_object(self.payload)
        frozen_schema = _freeze_object(self.response_schema)
        if not isinstance(frozen_payload, Mapping):
            raise ValueError("provider payload must be a JSON object")
        request_schema_hash = frozen_payload.get("request_schema_hash")
        if frozen_payload.get("request_hash") != self.request_hash:
            raise ValueError("provider payload must carry the exact generation request hash")
        if not _is_digest(request_schema_hash):
            raise ValueError("provider payload must carry a request schema hash")
        if not isinstance(frozen_schema, Mapping) or not frozen_schema:
            raise ValueError("provider response schema must be a non-empty JSON object")
        if request_schema_hash != response_schema_digest(frozen_schema):
            raise ValueError("provider response schema must match the request schema hash")
        object.__setattr__(self, "payload", frozen_payload)
        object.__setattr__(self, "response_schema", frozen_schema)
        return self


class ProviderResponse(GenerationModel):
    """Raw provider response text and bytes before trusted schema validation."""

    __raw_text_fields__: ClassVar[frozenset[str]] = frozenset({"content"})

    content: str = Field(min_length=1)
    raw_bytes: bytes

    @field_validator("content")
    @classmethod
    def _validate_content(cls, value: str) -> str:
        try:
            value.encode("utf-8")
        except UnicodeEncodeError as error:
            raise ValueError("provider response text must be valid UTF-8") from error
        return value

    @model_validator(mode="after")
    def _bind_raw_response(self) -> ProviderResponse:
        try:
            decoded = self.raw_bytes.decode("utf-8")
        except UnicodeDecodeError as error:
            raise ValueError("provider response bytes must be valid UTF-8") from error
        if decoded != self.content:
            raise ValueError("provider response bytes must exactly match provider response text")
        return self

    @computed_field(return_type=str)  # type: ignore[prop-decorator]
    @property
    def response_hash(self) -> str:
        return f"sha256:{sha256(self.raw_bytes).hexdigest()}"


class LlmProviderPort(Protocol):
    """Port implemented only by explicitly registered snapshot providers."""

    def complete(self, request: ProviderRequest) -> ProviderResponse:
        """Return untrusted plain JSON or raise a reason-coded provider failure."""


class ArtifactRef(GenerationModel):
    """Content-addressed accepted JSON artifact metadata."""

    cas_uri: str = Field(min_length=1)
    content_hash: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    byte_size: int = Field(ge=0)
    media_type: str = Field(min_length=1)


class GenerationRecord(GenerationModel):
    """Repository read model for a single immutable request identity."""

    generation_request_id: Identifier
    request: GenerationRequest
    state: GenerationState
    next_ordinal: int = Field(ge=0)
    owner_job_id: Identifier | None = None
    accepted_artifact: ArtifactRef | None = None

    @model_validator(mode="after")
    def _validate_state(self) -> GenerationRecord:
        provider_count = len(self.request.provider_chain)
        if self.next_ordinal > provider_count:
            raise ValueError("generation record next_ordinal exceeds the provider chain")
        if self.state is GenerationState.AVAILABLE:
            if self.next_ordinal >= provider_count:
                raise ValueError(
                    "available generation records require an available provider ordinal"
                )
            if self.owner_job_id is not None or self.accepted_artifact is not None:
                raise ValueError("available generation records must be ownerless and artifactless")
        elif self.state is GenerationState.RUNNING:
            if self.owner_job_id is None:
                raise ValueError("running generation records require an owner job")
            if self.accepted_artifact is not None:
                raise ValueError("running generation records cannot reference an accepted artifact")
            if self.next_ordinal >= provider_count:
                raise ValueError("running generation records require an available provider ordinal")
        elif self.state is GenerationState.ACCEPTED:
            if self.owner_job_id is not None:
                raise ValueError("accepted generation records must be ownerless")
            if self.accepted_artifact is None:
                raise ValueError("accepted generation records require an accepted artifact")
        else:
            if self.owner_job_id is not None:
                raise ValueError("failed generation records must be ownerless")
            if self.accepted_artifact is not None:
                raise ValueError("failed generation records cannot reference an accepted artifact")
        return self


class GenerationLease(GenerationModel):
    """The exact CAS ownership values required for an attempt transition."""

    generation_request_id: Identifier
    owner_token: Identifier
    owner_epoch: int = Field(ge=1)
    row_version: int = Field(ge=1)
    next_ordinal: int = Field(ge=0)


class GenerationAttempt(GenerationModel):
    """Durable provenance for one ordinal in the snapshotted fallback chain."""

    attempt_id: Identifier
    generation_request_id: Identifier
    ordinal: int = Field(ge=0)
    provider_model: ProviderModel
    request_hash: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    state: AttemptState
    response_hash: str | None = Field(default=None, pattern=r"^sha256:[0-9a-f]{64}$")
    error_code: Identifier | None = None
    accepted_artifact: ArtifactRef | None = None

    @model_validator(mode="after")
    def _validate_state(self) -> GenerationAttempt:
        if self.provider_model.ordinal != self.ordinal:
            raise ValueError("generation attempt ordinal must match the provider model ordinal")
        if self.state is AttemptState.STARTED:
            if (
                self.response_hash is not None
                or self.error_code is not None
                or self.accepted_artifact is not None
            ):
                raise ValueError("started generation attempts cannot carry terminal evidence")
        elif self.state is AttemptState.SUCCEEDED_VALID:
            if self.response_hash is None or self.accepted_artifact is None:
                raise ValueError(
                    "valid generation attempts require a response hash and accepted artifact"
                )
            if self.error_code is not None:
                raise ValueError("valid generation attempts cannot carry an error code")
        elif self.state is AttemptState.FAILED:
            if self.error_code is None:
                raise ValueError("failed generation attempts require a reason code")
            if self.accepted_artifact is not None:
                raise ValueError("failed generation attempts cannot reference an accepted artifact")
        elif self.state is AttemptState.INVALID:
            if self.response_hash is None or self.error_code is None:
                raise ValueError(
                    "invalid generation attempts require a response hash and reason code"
                )
            if self.accepted_artifact is not None:
                raise ValueError(
                    "invalid generation attempts cannot reference an accepted artifact"
                )
        else:
            if self.error_code is None:
                raise ValueError("interrupted generation attempts require a reason code")
            if self.accepted_artifact is not None:
                raise ValueError(
                    "interrupted generation attempts cannot reference an accepted artifact"
                )
        return self


class GenerationResult(GenerationModel):
    """Service result that makes duplicate/replay provider-call behavior observable."""

    record: GenerationRecord
    artifact_json: Mapping[str, JsonValue] | None = None
    provider_calls: int = Field(ge=0)

    @model_validator(mode="after")
    def _validate_result(self) -> GenerationResult:
        if self.artifact_json is not None:
            object.__setattr__(self, "artifact_json", _freeze_object(self.artifact_json))
        if self.record.state is not GenerationState.ACCEPTED and self.artifact_json is not None:
            raise ValueError("non-accepted generation results cannot carry artifact JSON")
        return self


def _hash_bytes(value: str) -> bytes:
    return bytes.fromhex(value.removeprefix("sha256:"))


def _is_digest(value: object) -> bool:
    if not isinstance(value, str) or len(value) != 71 or not value.startswith("sha256:"):
        return False
    return all(character in "0123456789abcdef" for character in value.removeprefix("sha256:"))


def _validate_claim_context(
    claim_pins: tuple[ClaimPin, ...], claim_context: object
) -> tuple[Mapping[str, JsonValue], ...]:
    if not isinstance(claim_context, (list, tuple)):
        raise ValueError("claim_context must be a list or tuple")
    contexts: list[Mapping[str, JsonValue]] = []
    context_pins: list[ClaimPin] = []
    required_keys = {"content_hash", "id", "text", "version"}
    for item in claim_context:
        if not isinstance(item, Mapping) or set(item) != required_keys:
            raise ValueError("claim_context entries must contain exactly the pinned claim fields")
        text = item["text"]
        if not isinstance(text, str) or not text.strip():
            raise ValueError("claim_context entries require non-blank text")
        context_pins.append(
            ClaimPin.model_validate(
                {
                    "hash": item["content_hash"],
                    "id": item["id"],
                    "version": item["version"],
                }
            )
        )
        frozen = _freeze_object(item)
        if not isinstance(frozen, Mapping):
            raise ValueError("claim_context entries must be JSON objects")
        contexts.append(frozen)
    if tuple(context_pins) != claim_pins:
        raise ValueError(
            "claim_context must exactly match the request's pinned claim revisions in canonical order"
        )
    return tuple(contexts)


def _freeze_object(value: object) -> JsonValue:
    if value is None or isinstance(value, (bool, int)):
        return value
    if isinstance(value, str):
        return normalize("NFC", value)
    if isinstance(value, float):
        raise ValueError("JSON identity values cannot use floats")
    if isinstance(value, Mapping):
        frozen: dict[str, JsonValue] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise ValueError("JSON object keys must be strings")
            normalized_key = normalize("NFC", key)
            if not normalized_key or not normalized_key.isascii():
                raise ValueError("JSON object keys must be non-empty ASCII strings")
            if normalized_key in frozen:
                raise ValueError("JSON object keys must be unique after normalization")
            frozen[normalized_key] = _freeze_object(item)
        return MappingProxyType(frozen)
    if isinstance(value, (list, tuple)):
        return tuple(_freeze_object(item) for item in value)
    raise ValueError(f"unsupported JSON identity value type: {type(value).__name__}")


def response_schema_digest(schema: Mapping[str, JsonValue]) -> str:
    """Return the canonical digest of a trusted provider response JSON schema."""
    frozen_schema = _freeze_object(schema)
    if not isinstance(frozen_schema, Mapping) or not frozen_schema:
        raise ValueError("response schema must be a non-empty JSON object")
    encoded = json.dumps(
        _canonical_json(frozen_schema),
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return f"sha256:{sha256(encoded).hexdigest()}"


def _canonical_json(value: JsonValue) -> object:
    if isinstance(value, Mapping):
        return {key: _canonical_json(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_canonical_json(item) for item in value]
    return value


__all__ = [
    "ArtifactRef",
    "AttemptState",
    "GenerationAttempt",
    "GenerationLease",
    "GenerationRecord",
    "GenerationRequest",
    "GenerationResult",
    "GenerationState",
    "JsonValue",
    "LlmProviderPort",
    "ProviderFailure",
    "ProviderFailureCode",
    "ProviderRequest",
    "ProviderResponse",
    "response_schema_digest",
]
