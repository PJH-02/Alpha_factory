"""Immutable knowledge contracts and deterministic claim-context selection."""

from __future__ import annotations

import re
from collections.abc import Iterable
from typing import Self
from unicodedata import normalize

from pydantic import Field, computed_field, field_validator, model_validator

from .domain import Domain, FrozenModel, digest
from .domain.models import Identifier

_SEMVER_PATTERN = re.compile(
    r"^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)"
    r"(?:-[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?"
    r"(?:\+[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?$"
)


class KnowledgeModel(FrozenModel):
    """Strict, frozen base model for immutable knowledge resources."""


class Citation(KnowledgeModel):
    """A precise, reviewable location in a source supporting or disputing a claim."""

    source_id: Identifier
    locator: str = Field(min_length=1)
    excerpt: str = Field(min_length=1)

    @field_validator("source_id", "locator", "excerpt")
    @classmethod
    def _normalize_text(cls, value: str) -> str:
        return _required_text(value)

    def semantic_fields(self) -> dict[str, object]:
        return {
            "excerpt": self.excerpt,
            "locator": self.locator,
            "source_id": self.source_id,
        }


class Counterevidence(KnowledgeModel):
    """Evidence that constrains the scope or validity of a claim."""

    statement: str = Field(min_length=1)
    citations: tuple[Citation, ...] = Field(min_length=1)

    @field_validator("statement")
    @classmethod
    def _normalize_statement(cls, value: str) -> str:
        return _required_text(value)

    def semantic_fields(self) -> dict[str, object]:
        return {
            "citations": [citation.semantic_fields() for citation in self.citations],
            "statement": self.statement,
        }


class FailureMemory(KnowledgeModel):
    """A prior failure that must remain visible when a claim is used as context."""

    reason_code: Identifier
    summary: str = Field(min_length=1)

    @field_validator("reason_code", "summary")
    @classmethod
    def _normalize_text(cls, value: str) -> str:
        return _required_text(value)

    def semantic_fields(self) -> dict[str, object]:
        return {"reason_code": self.reason_code, "summary": self.summary}


class Claim(KnowledgeModel):
    """An immutable, versioned, evidence-backed statement."""

    id: Identifier
    version: str = Field(min_length=1)
    domain: Domain
    statement: str = Field(min_length=1)
    source: str = Field(min_length=1)
    citations: tuple[Citation, ...] = Field(min_length=1)
    counterevidence: tuple[Counterevidence, ...] = ()
    failure_memory: tuple[FailureMemory, ...] = ()

    @field_validator("id", "statement", "source")
    @classmethod
    def _normalize_text(cls, value: str) -> str:
        return _required_text(value)

    @field_validator("version")
    @classmethod
    def _validate_version(cls, value: str) -> str:
        return _semantic_version(value)

    @computed_field(return_type=str)  # type: ignore[prop-decorator]
    @property
    def content_hash(self) -> str:
        """The immutable semantic identity of this exact claim revision."""

        return digest("AF:CLAIM:1", self.semantic_fields())

    @property
    def claim_id(self) -> str:
        """Explicit name for code that distinguishes IDs from model attributes."""

        return self.id

    def semantic_fields(self) -> dict[str, object]:
        return {
            "citations": [citation.semantic_fields() for citation in self.citations],
            "counterevidence": [item.semantic_fields() for item in self.counterevidence],
            "domain": self.domain.value,
            "failure_memory": [item.semantic_fields() for item in self.failure_memory],
            "id": self.id,
            "source": self.source,
            "statement": self.statement,
            "version": self.version,
        }

    def pin(self) -> ClaimPin:
        return ClaimPin(id=self.id, version=self.version, hash=self.content_hash)


class ClaimPin(KnowledgeModel):
    """An exact immutable claim reference; it never means 'latest' revision."""

    id: Identifier
    version: str = Field(min_length=1)
    hash: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")

    @field_validator("id")
    @classmethod
    def _normalize_text(cls, value: str) -> str:
        return _required_text(value)

    @field_validator("version")
    @classmethod
    def _validate_version(cls, value: str) -> str:
        return _semantic_version(value)

    @property
    def content_hash(self) -> str:
        """Compatibility name for code handling generic immutable resources."""

        return self.hash

    @classmethod
    def from_claim(cls, claim: Claim) -> Self:
        return cls(id=claim.id, version=claim.version, hash=claim.content_hash)

    def semantic_fields(self) -> dict[str, object]:
        return {
            "hash": _hash_bytes(self.hash),
            "id": self.id,
            "version": self.version,
        }


class KnowledgePack(KnowledgeModel):
    """A versioned immutable collection of exact claim revisions."""

    id: Identifier
    version: str = Field(min_length=1)
    claims: tuple[Claim, ...] = Field(min_length=1)

    @field_validator("id")
    @classmethod
    def _normalize_text(cls, value: str) -> str:
        return _required_text(value)

    @field_validator("version")
    @classmethod
    def _validate_version(cls, value: str) -> str:
        return _semantic_version(value)

    @model_validator(mode="after")
    def _validate_claim_revisions(self) -> KnowledgePack:
        revisions = tuple((claim.id, claim.version) for claim in self.claims)
        if len(revisions) != len(set(revisions)):
            raise ValueError("knowledge pack cannot contain duplicate claim ID/version revisions")
        return self

    @computed_field(return_type=str)  # type: ignore[prop-decorator]
    @property
    def content_hash(self) -> str:
        """The immutable semantic identity of this pack and its pinned claims."""

        return digest("AF:KNOWLEDGE_PACK:1", self.semantic_fields())

    @property
    def pack_id(self) -> str:
        """Explicit name for route and repository code."""

        return self.id

    def semantic_fields(self) -> dict[str, object]:
        return {
            "claim_pins": [
                claim.pin().semantic_fields()
                for claim in sorted(
                    self.claims,
                    key=lambda claim: (claim.id, claim.version, claim.content_hash),
                )
            ],
            "id": self.id,
            "version": self.version,
        }

    def resolve_pins(self, pins: Iterable[ClaimPin]) -> tuple[Claim, ...]:
        """Return exact pinned revisions or reject missing/stale claim references."""

        by_revision = {(claim.id, claim.version): claim for claim in self.claims}
        resolved: list[Claim] = []
        seen: set[tuple[str, str, str]] = set()
        for pin in pins:
            key = (pin.id, pin.version, pin.hash)
            if key in seen:
                raise ValueError("claim pins must not contain duplicates")
            seen.add(key)
            claim = by_revision.get((pin.id, pin.version))
            if claim is None or claim.content_hash != pin.hash:
                raise ValueError("claim pin does not match an immutable pack claim revision")
            resolved.append(claim)
        return tuple(resolved)


class ClaimContext(KnowledgeModel):
    """A deterministically ranked, full-text-ready claim supplied to generation."""

    claim: Claim
    full_text: str = Field(min_length=1)
    match_score: int = Field(ge=0)

    @model_validator(mode="after")
    def _validate_full_text(self) -> ClaimContext:
        if self.full_text != _claim_full_text(self.claim):
            raise ValueError("claim context full_text must match the immutable claim revision")
        return self


_TOKEN_PATTERN = re.compile(r"\w+", re.UNICODE)


def order_claim_context(
    claims: Iterable[Claim],
    *,
    domain: Domain | None = None,
    query: str | None = None,
    limit: int | None = None,
) -> tuple[ClaimContext, ...]:
    """Filter claims by domain and return a deterministic full-text context order.

    The function is deliberately storage-agnostic: repositories can map the same
    normalized query to an FTS index, while this ordering remains the deterministic
    tie-breaker and works without an index.
    """

    if limit is not None and limit < 0:
        raise ValueError("limit must be non-negative")
    normalized_query = _required_text(query) if query is not None else None
    terms = tuple(sorted(set(_tokens(normalized_query)))) if normalized_query else ()
    if normalized_query is not None and not terms:
        raise ValueError("query must contain at least one searchable token")

    unique_claims: dict[str, Claim] = {}
    for claim in claims:
        if domain is not None and claim.domain != domain:
            continue
        unique_claims[claim.content_hash] = claim

    ranked: list[ClaimContext] = []
    for claim in unique_claims.values():
        full_text = _claim_full_text(claim)
        document_tokens = _tokens(full_text)
        score = sum(document_tokens.count(term) for term in terms)
        if terms and score == 0:
            continue
        ranked.append(ClaimContext(claim=claim, full_text=full_text, match_score=score))

    ranked.sort(
        key=lambda item: (
            -item.match_score,
            item.claim.domain.value,
            item.claim.id,
            item.claim.version,
            item.claim.content_hash,
        )
    )
    if limit is not None:
        ranked = ranked[:limit]
    return tuple(ranked)


def _claim_full_text(claim: Claim) -> str:
    """Create the deterministic, normalized document passed to an FTS implementation."""

    sections = [claim.statement, claim.source]
    for citation in claim.citations:
        sections.extend((citation.source_id, citation.locator, citation.excerpt))
    for counterevidence in claim.counterevidence:
        sections.append(counterevidence.statement)
        for citation in counterevidence.citations:
            sections.extend((citation.source_id, citation.locator, citation.excerpt))
    for failure in claim.failure_memory:
        sections.extend((failure.reason_code, failure.summary))
    return "\n".join(_required_text(section) for section in sections)


def _tokens(value: str) -> list[str]:
    return _TOKEN_PATTERN.findall(normalize("NFC", value).casefold())


def _required_text(value: str) -> str:
    if not isinstance(value, str):
        raise ValueError("text must be a string")
    normalized = normalize("NFC", value).strip()
    if not normalized:
        raise ValueError("text must not be blank")
    return normalized


def _semantic_version(value: str) -> str:
    normalized = _required_text(value)
    if _SEMVER_PATTERN.fullmatch(normalized) is None:
        raise ValueError("version must be a semantic version")
    prerelease = normalized.partition("+")[0].partition("-")[2]
    if any(
        identifier.isascii()
        and identifier.isdigit()
        and len(identifier) > 1
        and identifier.startswith("0")
        for identifier in prerelease.split(".")
        if identifier
    ):
        raise ValueError(
            "numeric semantic-version prerelease identifiers must not have leading zeroes"
        )
    return normalized


def _hash_bytes(value: str) -> bytes:
    return bytes.fromhex(value.removeprefix("sha256:"))


__all__ = [
    "Citation",
    "Claim",
    "ClaimContext",
    "ClaimPin",
    "Counterevidence",
    "FailureMemory",
    "KnowledgePack",
    "order_claim_context",
]
