"""Explicit runtime assembly and the shared application facade.

This module deliberately performs no environment reads, storage writes, provider calls, or
logging at import time.  Entrypoints call :func:`bootstrap` once to assemble the local
runtime used by both the REST and CLI adapters.
"""

from __future__ import annotations

import json
import math
import os
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from types import MappingProxyType
from typing import Any, Literal, cast
from urllib.parse import urlsplit
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, ValidationError, field_validator

from alpha_foundry.application.jobs import (
    JobStateError,
    PersistentSingleWorkerJobService,
    SQLiteJobRepository,
    SystemClock,
)
from alpha_foundry.application.ports import Clock
from alpha_foundry.domain.canonical import canonical_bytes, digest
from alpha_foundry.domain.errors import DomainError, ErrorCode, ErrorDetail
from alpha_foundry.domain.models import Decision, Domain, Job, JobKind, JobStatus, ResearchReport
from alpha_foundry.generation import LlmProviderPort
from alpha_foundry.infrastructure.artifacts import LocalArtifactStore
from alpha_foundry.infrastructure.db import SQLiteStore
from alpha_foundry.infrastructure.llm import FakeProvider, OpenAICompatibleProvider
from alpha_foundry.knowledge import KnowledgePack
from alpha_foundry.reporting import parse_canonical_research_report, render_research_report_html
from alpha_foundry.search.models import SearchSpec
from alpha_foundry.wiki import (
    WikiClaimMatch,
    WikiImportError,
    WikiPageMatch,
    default_model_candidates,
    default_wiki_root,
    generate_idea_pages,
    import_markdown_corpus,
    next_pack_version,
    query_pack,
)

_SCHEMA_VERSION = "1.0.0"
_CREATED_BY = "local:user"
_DEFAULT_PROVIDER = "nvidia"
_DEFAULT_BASE_URL = "https://integrate.api.nvidia.com/v1"

JobHandler = Callable[[Job], str]
type GenerationJobHandler = Callable[[Job, Mapping[str, LlmProviderPort]], str]
type RegisteredJobHandler = JobHandler | GenerationJobHandler


class _PublicJobDto(BaseModel):
    """Allowlisted durable-job fields safe for local operator responses."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    job_id: str
    kind: JobKind
    status: JobStatus
    stage: str
    error_code: ErrorCode | None
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None


class _SearchRunDto(BaseModel):
    """Allowlisted finite-search state safe for local operator responses."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    search_run_id: str
    search_spec_hash: str
    universe_hash: str
    profile_hash: str
    state: str
    stop_reason: str | None
    proposed_count: int
    created_at: str
    updated_at: str


class _GenerationReplayDto(BaseModel):
    """Safe replay receipt that never reveals storage location or raw generated content."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    generation_request_id: str
    request_hash: str
    state: str
    artifact_hash: str


class _RejectionDto(BaseModel):
    """Allowlisted local-operator rejection summary without lineage or evidence internals."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    rejection_id: str
    terminal_stage: str
    reason_code: str
    redacted_summary: str
    created_at: str


class _KnowledgePackResponseDto(BaseModel):
    """Strict public projection for an immutable knowledge-pack read or replay."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    knowledge_pack: KnowledgePack

    @field_validator("knowledge_pack", mode="before")
    @classmethod
    def _verify_computed_hashes(cls, value: object) -> object:
        if isinstance(value, KnowledgePack) or not isinstance(value, Mapping):
            return value
        supplied = dict(value)
        supplied_pack_hash = supplied.pop("content_hash", None)
        raw_claims = supplied.get("claims")
        supplied_claim_hashes: list[object] = []
        if isinstance(raw_claims, (list, tuple)):
            claims: list[object] = []
            for raw_claim in raw_claims:
                if isinstance(raw_claim, Mapping):
                    claim = dict(raw_claim)
                    supplied_claim_hashes.append(claim.pop("content_hash", None))
                    claims.append(claim)
                else:
                    supplied_claim_hashes.append(None)
                    claims.append(raw_claim)
            supplied["claims"] = claims
        pack = KnowledgePack.model_validate(supplied)
        if supplied_pack_hash is not None and supplied_pack_hash != pack.content_hash:
            raise ValueError("knowledge pack content hash does not match its canonical content")
        if supplied_claim_hashes and any(
            supplied_hash is not None and supplied_hash != claim.content_hash
            for supplied_hash, claim in zip(supplied_claim_hashes, pack.claims, strict=True)
        ):
            raise ValueError("knowledge claim content hash does not match its canonical content")
        return pack



class _KnowledgeImportResponseDto(BaseModel):
    """Strict response for markdown-to-wiki ingestion and pack registration."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    knowledge_pack: KnowledgePack
    pack_path: str
    index_path: str
    page_paths: tuple[str, ...]
    models_used: tuple[str, ...]


class _KnowledgeQueryResponseDto(BaseModel):
    """Strict response for deterministic wiki-backed claim and page retrieval."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    pack_id: str
    pack_version: str
    query: str
    pages: tuple[WikiPageMatch, ...]
    claims: tuple[WikiClaimMatch, ...]


class _KnowledgeIdeaResponseDto(BaseModel):
    """Strict response for wiki-grounded idea-page generation."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    query: str
    idea_paths: tuple[str, ...]
    idea_page_ids: tuple[str, ...]
    models_used: tuple[str, ...]

class _ArticleDomainImportDto(BaseModel):
    """One domain-folder import performed by the article CLI."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    domain: Domain
    article_count: int
    knowledge_pack: KnowledgePack
    pack_path: str
    index_path: str
    page_paths: tuple[str, ...]
    models_used: tuple[str, ...]


class _ArticleAddResponseDto(BaseModel):
    """Strict response for scanning the domain-classified article tree."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    article_root: str
    wiki_root: str
    imports: tuple[_ArticleDomainImportDto, ...]


class _AlphaGenerateResponseDto(BaseModel):
    """Strict response for automatic wiki retrieval and alpha ideation."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    domain: Domain
    request: str
    pack_id: str
    pack_version: str
    idea_paths: tuple[str, ...]
    idea_page_ids: tuple[str, ...]
    models_used: tuple[str, ...]
class _MandateDto(BaseModel):
    """Stable public mandate projection with immutable resource metadata."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    mandate_id: str
    version: str
    mode: Literal["QUESTION", "DOMAIN"]
    domain: Domain
    objective: str
    data_requirements: list[str]
    budget: dict[str, object]
    forbidden_conditions: list[str]
    content_hash: str
    created_at: str
    created_by: str
    schema_version: str
    code_version: str


class _MandateCreateResponseDto(BaseModel):
    """Strict response for the immutable mandate creation command."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    mandate: _MandateDto


class _MandateReadResponseDto(BaseModel):
    """Strict response for a mandate read and its optional generation job."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    mandate: _MandateDto
    job: _PublicJobDto | None = None


class _JobResponseDto(BaseModel):
    """Strict response for a durable-job query or cancellation command."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    job: _PublicJobDto


class _JobCommandResponseDto(BaseModel):
    """Strict response for a command that creates one durable job."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    job_id: str
    job: _PublicJobDto


class _SearchResponseDto(BaseModel):
    """Strict response for persisted SearchRun command and query projections."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    search_run: _SearchRunDto
    job: _PublicJobDto | None


class _PublishedStrategyDto(BaseModel):
    """Strict PUBLISHED-only registry row projection."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    strategy_id: str
    publication_id: str
    validation_id: str
    candidate_hash: str
    lineage_id: str
    published_at: str


class _PublishedStrategyListResponseDto(BaseModel):
    """Strict public projection for the PUBLISHED strategy list."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    strategies: tuple[_PublishedStrategyDto, ...]


class _PublishedStrategyResponseDto(BaseModel):
    """Strict public projection for one PUBLISHED strategy and report."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    strategy: _PublishedStrategyDto
    report: ResearchReport


class _ReportResponseDto(BaseModel):
    """Strict public projection for a canonical PUBLISHED report."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    report: ResearchReport


class _RejectionListResponseDto(BaseModel):
    """Strict internal operator projection for redacted rejection rows."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    rejections: tuple[_RejectionDto, ...]


class _RejectionResponseDto(BaseModel):
    """Strict internal operator projection for one redacted rejection row."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    rejection: _RejectionDto


@dataclass(frozen=True, slots=True)
class BootstrapSettings:
    """Explicit local runtime configuration with no secret-bearing representation."""

    database_path: Path
    artifact_root: Path
    llm_provider: str | None = None
    llm_base_url: str | None = None
    llm_api_key: str | None = field(default=None, repr=False)
    llm_timeout_seconds: float = 30.0

    def __post_init__(self) -> None:
        if (
            isinstance(self.llm_timeout_seconds, bool)
            or not isinstance(self.llm_timeout_seconds, (int, float))
            or not math.isfinite(self.llm_timeout_seconds)
            or self.llm_timeout_seconds <= 0
        ):
            raise ValueError("llm_timeout_seconds must be finite and positive")

    @classmethod
    def from_environment(cls) -> BootstrapSettings:
        """Construct settings when an executable entrypoint explicitly requests them."""

        environment = _runtime_environment()
        home = Path(environment.get("ALPHA_FOUNDRY_HOME", ".alpha-foundry"))
        provider = environment.get("ALPHA_FOUNDRY_LLM_PROVIDER")
        api_key = environment.get("ALPHA_FOUNDRY_LLM_API_KEY")
        if api_key is None and provider in {None, _DEFAULT_PROVIDER}:
            api_key = environment.get("NVIDIA_API_KEY")
        if provider is None and api_key:
            provider = _DEFAULT_PROVIDER
        return cls(
            database_path=Path(environment.get("ALPHA_FOUNDRY_DB", home / "alpha_foundry.sqlite")),
            artifact_root=Path(environment.get("ALPHA_FOUNDRY_ARTIFACTS", home / "artifacts")),
            llm_provider=provider,
            llm_base_url=environment.get("ALPHA_FOUNDRY_LLM_BASE_URL"),
            llm_api_key=api_key,
            llm_timeout_seconds=float(environment.get("ALPHA_FOUNDRY_LLM_TIMEOUT_SECONDS", "30")),
        )


class ApplicationFacade:
    """The only command/query boundary shared by REST, CLI, and the local worker."""

    def __init__(
        self,
        *,
        store: SQLiteStore,
        artifacts: LocalArtifactStore,
        providers: Mapping[str, LlmProviderPort],
        clock: Clock,
        job_handlers: Mapping[JobKind, RegisteredJobHandler] | None = None,
        lab_registry: object | None = None,
        closers: tuple[Callable[[], None], ...] = (),
    ) -> None:
        self._store = store
        self._artifacts = artifacts
        self._provider_snapshot = MappingProxyType(dict(providers))
        self._clock = clock
        self._job_handlers = dict(job_handlers or {})
        self._lab_registry = lab_registry
        self._closers = closers
        self._jobs = SQLiteJobRepository(store, clock)
        self._job_service = PersistentSingleWorkerJobService(self._jobs)
        self._started = False
        self._closed = False

    @property
    def lab_registry(self) -> object:
        """Return the bootstrap-owned lazy registry of isolated domain labs."""

        self._require_open()
        if self._lab_registry is None:
            raise RuntimeError("lab registry was not assembled")
        return self._lab_registry

    def startup(self) -> tuple[dict[str, object], ...]:
        """Apply mandatory interrupted-job recovery exactly once while the runtime is open."""

        self._require_open()
        if self._started:
            return ()
        recovered = self._job_service.startup()
        self._started = True
        return tuple(self._job_data(job) for job in recovered)

    def close(self) -> None:
        """Close every owned resource once, even when an earlier closer fails."""

        if self._closed:
            return
        self._closed = True
        errors: list[Exception] = []
        for closer in reversed(self._closers):
            try:
                closer()
            except Exception as error:  # pragma: no cover - close must attempt every resource
                errors.append(error)
        try:
            self._store.close()
        except Exception as error:  # pragma: no cover - close must attempt every resource
            errors.append(error)
        if errors:
            raise ExceptionGroup("runtime cleanup failed", errors)

    def _require_open(self) -> None:
        if self._closed:
            raise self._failure(ErrorCode.STATE, "runtime has already been closed")

    def health(self) -> dict[str, object]:
        """Return a non-sensitive local health result after checking SQLite reachability."""

        self._require_open()
        try:
            row = self._store.fetch_one("SELECT 1 AS healthy")
        except Exception as error:
            raise self._failure(ErrorCode.STORAGE, "metadata storage is unavailable") from error
        if row is None or row["healthy"] != 1:
            raise self._failure(ErrorCode.STORAGE, "metadata storage health check failed")
        return {"status": "ok", "storage": "ok"}

    def add_knowledge_pack(
        self,
        pack: KnowledgePack,
        *,
        idempotency_key: str,
    ) -> dict[str, object]:
        """Persist an immutable knowledge pack and all exact claim revisions atomically."""
        self._require_open()

        request_hash = self._fingerprint("knowledge.pack.add", pack.model_dump(mode="python"))
        self._require_idempotency_key(idempotency_key)
        response = self._dto_data(
            _KnowledgePackResponseDto,
            {"knowledge_pack": pack},
        )
        encoded_response = self._json(response)
        now = self._timestamp()
        try:
            with self._store.transaction(immediate=True) as connection:
                previous = self._idempotent_response(
                    connection,
                    "knowledge.pack.add",
                    idempotency_key,
                    request_hash,
                )
                if previous is not None:
                    return self._dto_data(_KnowledgePackResponseDto, previous)

                existing = connection.execute(
                    """
                    SELECT content_hash FROM knowledge_packs
                    WHERE knowledge_pack_id = ? AND version = ?
                    """,
                    (pack.id, pack.version),
                ).fetchone()
                if existing is not None:
                    if existing["content_hash"] != pack.content_hash:
                        raise self._failure(
                            ErrorCode.STATE,
                            "knowledge pack revisions are immutable",
                        )
                else:
                    for claim in pack.claims:
                        claim_payload = self._json(claim.model_dump(mode="json"))
                        claim_row = connection.execute(
                            """
                            SELECT content_hash FROM knowledge_claims
                            WHERE claim_id = ? AND version = ?
                            """,
                            (claim.id, claim.version),
                        ).fetchone()
                        if claim_row is not None:
                            if claim_row["content_hash"] != claim.content_hash:
                                raise self._failure(
                                    ErrorCode.STATE,
                                    "knowledge claim revisions are immutable",
                                )
                        else:
                            connection.execute(
                                """
                                INSERT INTO knowledge_claims (
                                    claim_id, version, content_hash, claim_json, source_ref,
                                    created_at, created_by, schema_version, code_version
                                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                                """,
                                (
                                    claim.id,
                                    claim.version,
                                    claim.content_hash,
                                    claim_payload,
                                    claim.source,
                                    now,
                                    _CREATED_BY,
                                    _SCHEMA_VERSION,
                                    _SCHEMA_VERSION,
                                ),
                            )
                    connection.execute(
                        """
                        INSERT INTO knowledge_packs (
                            knowledge_pack_id, version, content_hash, pack_json,
                            created_at, created_by, schema_version, code_version
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            pack.id,
                            pack.version,
                            pack.content_hash,
                            self._json(pack.model_dump(mode="json")),
                            now,
                            _CREATED_BY,
                            _SCHEMA_VERSION,
                            _SCHEMA_VERSION,
                        ),
                    )
                    for ordinal, claim in enumerate(pack.claims):
                        connection.execute(
                            """
                            INSERT INTO knowledge_pack_claims (
                                knowledge_pack_id, knowledge_pack_version, claim_id, claim_version, ordinal
                            ) VALUES (?, ?, ?, ?, ?)
                            """,
                            (pack.id, pack.version, claim.id, claim.version, ordinal),
                        )
                self._store_idempotency(
                    connection,
                    "knowledge.pack.add",
                    idempotency_key,
                    request_hash,
                    encoded_response,
                    now,
                )
        except DomainError:
            raise
        except Exception as error:
            raise self._failure(ErrorCode.STORAGE, "could not store knowledge pack") from error
        return self._dto_data(_KnowledgePackResponseDto, response)

    def get_knowledge_pack(self, pack_id: str, version: str) -> dict[str, object]:
        """Return one exact immutable knowledge-pack revision."""

        self._require_open()
        row = self._store.fetch_one(
            """
            SELECT pack_json FROM knowledge_packs
            WHERE knowledge_pack_id = ? AND version = ?
            """,
            (pack_id, version),
        )
        if row is None:
            raise self._failure(ErrorCode.RESOURCE, "knowledge pack was not found")
        try:
            return self._dto_data(
                _KnowledgePackResponseDto,
                {"knowledge_pack": json.loads(row["pack_json"])},
            )
        except json.JSONDecodeError as error:
            raise self._failure(ErrorCode.STORAGE, "stored knowledge pack is invalid") from error


    def import_markdown_knowledge(
        self,
        source_root: Path,
        *,
        domain: Domain,
        pack_id: str | None,
        pack_version: str | None,
        wiki_root: Path | None,
        provider_name: str | None,
        models: Sequence[str] | None,
        idempotency_key: str,
    ) -> dict[str, object]:
        """Summarize markdown sources into wiki pages, write artifacts, and import a pack."""
        self._require_open()
        self._require_idempotency_key(idempotency_key)
        normalized_pack_id = (pack_id or source_root.name).strip()
        if not normalized_pack_id:
            raise self._failure(ErrorCode.SCHEMA, "knowledge pack ID must not be blank")
        normalized_version = (
            pack_version.strip()
            if isinstance(pack_version, str) and pack_version.strip()
            else next_pack_version(self._knowledge_pack_versions(normalized_pack_id))
        )
        resolved_provider, resolved_models = self._interactive_provider_selection(provider_name, models)
        target_root = wiki_root if wiki_root is not None else default_wiki_root()
        try:
            result = import_markdown_corpus(
                source_root=source_root,
                wiki_root=target_root,
                pack_id=normalized_pack_id,
                pack_version=normalized_version,
                domain=domain,
                providers=self._provider_snapshot,
                provider_name=resolved_provider,
                model_names=resolved_models,
            )
        except WikiImportError as error:
            raise self._failure(ErrorCode.LLM, str(error)) from error
        except ValueError as error:
            raise self._failure(ErrorCode.SCHEMA, str(error)) from error
        except OSError as error:
            raise self._failure(ErrorCode.STORAGE, "could not write wiki import artifacts") from error
        self.add_knowledge_pack(result.knowledge_pack, idempotency_key=idempotency_key)
        return self._dto_data(
            _KnowledgeImportResponseDto,
            {
                "knowledge_pack": result.knowledge_pack,
                "pack_path": result.pack_path,
                "index_path": result.index_path,
                "page_paths": result.page_paths,
                "models_used": result.models_used,
            },
        )

    def query_knowledge_pack(
        self,
        pack_id: str,
        version: str,
        query: str,
        *,
        domain: Domain,
        wiki_root: Path | None,
        limit: int,
    ) -> dict[str, object]:
        """Return the highest-ranked wiki pages and claims for one query."""
        self._require_open()
        try:
            result = query_pack(
                pack=self._knowledge_pack_required(pack_id, version),
                wiki_root=wiki_root if wiki_root is not None else default_wiki_root(),
                query=query,
                domain=domain,
                limit=limit,
            )
        except FileNotFoundError as error:
            raise self._failure(ErrorCode.RESOURCE, "wiki index was not found for this knowledge pack") from error
        except ValueError as error:
            raise self._failure(ErrorCode.SCHEMA, str(error)) from error
        return self._dto_data(_KnowledgeQueryResponseDto, result.model_dump(mode="python"))

    def ideate_knowledge_pack(
        self,
        pack_id: str,
        version: str,
        query: str,
        *,
        domain: Domain,
        wiki_root: Path | None,
        provider_name: str | None,
        models: Sequence[str] | None,
        limit: int,
        idea_count: int,
    ) -> dict[str, object]:
        """Generate wiki-stored idea pages from the most relevant imported claims."""
        self._require_open()
        resolved_provider, resolved_models = self._interactive_provider_selection(provider_name, models)
        target_root = wiki_root if wiki_root is not None else default_wiki_root()
        try:
            result = generate_idea_pages(
                pack=self._knowledge_pack_required(pack_id, version),
                wiki_root=target_root,
                query=query,
                domain=domain,
                limit=limit,
                idea_count=idea_count,
                providers=self._provider_snapshot,
                provider_name=resolved_provider,
                model_names=resolved_models,
            )
        except FileNotFoundError as error:
            raise self._failure(ErrorCode.RESOURCE, "wiki index was not found for this knowledge pack") from error
        except WikiImportError as error:
            raise self._failure(ErrorCode.LLM, str(error)) from error
        except ValueError as error:
            raise self._failure(ErrorCode.SCHEMA, str(error)) from error
        except OSError as error:
            raise self._failure(ErrorCode.STORAGE, "could not write wiki idea pages") from error
        return self._dto_data(_KnowledgeIdeaResponseDto, result.model_dump(mode="python"))

    def add_articles(
        self,
        article_root: Path,
        *,
        wiki_root: Path | None,
        provider_name: str | None,
        models: Sequence[str] | None,
    ) -> dict[str, object]:
        """Import markdown only from explicit article/<DOMAIN>/ directories."""
        self._require_open()
        if not article_root.exists() or not article_root.is_dir():
            raise self._failure(ErrorCode.SCHEMA, "article root must be an existing directory")
        if any(article_root.glob("*.md")):
            raise self._failure(
                ErrorCode.SCHEMA,
                "markdown files must be placed inside an article domain directory",
            )
        known_domains = {domain.value: domain for domain in Domain}
        unknown = tuple(
            child.name
            for child in article_root.iterdir()
            if child.is_dir()
            and child.name not in known_domains
            and any(child.rglob("*.md"))
        )
        if unknown:
            raise self._failure(
                ErrorCode.SCHEMA,
                "article directories with markdown must use a supported domain name",
            )
        target_root = wiki_root if wiki_root is not None else default_wiki_root()
        imported_domains: list[dict[str, object]] = []
        for domain in Domain:
            domain_root = article_root / domain.value
            article_paths = (
                tuple(sorted(domain_root.rglob("*.md")))
                if domain_root.exists() and domain_root.is_dir()
                else ()
            )
            if not article_paths:
                continue
            pack_id = self._domain_article_pack_id(domain)
            imported = self.import_markdown_knowledge(
                domain_root,
                domain=domain,
                pack_id=pack_id,
                pack_version=None,
                wiki_root=target_root,
                provider_name=provider_name,
                models=models,
                idempotency_key=str(uuid4()),
            )
            pack_data = imported["knowledge_pack"]
            if not isinstance(pack_data, Mapping):
                raise self._failure(ErrorCode.STORAGE, "article import returned an invalid pack")
            version = pack_data.get("version")
            if not isinstance(version, str):
                raise self._failure(ErrorCode.STORAGE, "article import returned an invalid pack")
            pack_path_value = imported["pack_path"]
            index_path_value = imported["index_path"]
            page_paths_value = imported["page_paths"]
            models_used_value = imported["models_used"]
            if (
                not isinstance(pack_path_value, str)
                or not isinstance(index_path_value, str)
                or not isinstance(page_paths_value, (list, tuple))
                or not all(isinstance(path, str) for path in page_paths_value)
                or not isinstance(models_used_value, (list, tuple))
                or not all(isinstance(model, str) for model in models_used_value)
            ):
                raise self._failure(ErrorCode.STORAGE, "article import returned invalid paths")
            page_paths = tuple(cast("Sequence[str]", page_paths_value))
            models_used = tuple(cast("Sequence[str]", models_used_value))
            imported_domains.append(
                {
                    "domain": domain,
                    "article_count": len(article_paths),
                    "knowledge_pack": self._knowledge_pack_required(pack_id, version),
                    "pack_path": pack_path_value,
                    "index_path": index_path_value,
                    "page_paths": page_paths,
                    "models_used": models_used,
                }
            )
        if not imported_domains:
            raise self._failure(
                ErrorCode.SCHEMA,
                "article root contains no markdown under supported domain directories",
            )
        return self._dto_data(
            _ArticleAddResponseDto,
            {
                "article_root": article_root.as_posix(),
                "wiki_root": target_root.as_posix(),
                "imports": tuple(imported_domains),
            },
        )

    def generate_alpha(
        self,
        request: str,
        *,
        domain: Domain,
        wiki_root: Path | None,
        provider_name: str | None,
        models: Sequence[str] | None,
        limit: int,
        idea_count: int,
    ) -> dict[str, object]:
        """Retrieve the latest domain wiki automatically and persist grounded alpha ideas."""
        self._require_open()
        normalized_request = request.strip()
        if not normalized_request:
            raise self._failure(ErrorCode.SCHEMA, "alpha request must not be blank")
        pack_id = self._domain_article_pack_id(domain)
        version = self._latest_knowledge_pack_version(pack_id)
        generated = self.ideate_knowledge_pack(
            pack_id,
            version,
            normalized_request,
            domain=domain,
            wiki_root=wiki_root,
            provider_name=provider_name,
            models=models,
            limit=limit,
            idea_count=idea_count,
        )
        return self._dto_data(
            _AlphaGenerateResponseDto,
            {
                "domain": domain,
                "request": normalized_request,
                "pack_id": pack_id,
                "pack_version": version,
                "idea_paths": generated["idea_paths"],
                "idea_page_ids": generated["idea_page_ids"],
                "models_used": generated["models_used"],
            },
        )
    def create_mandate(
        self,
        mandate: Mapping[str, object],
        *,
        idempotency_key: str,
    ) -> dict[str, object]:
        """Register an immutable single-domain research mandate revision."""
        self._require_open()

        caller_input = self._mandate_caller_input(mandate)
        try:
            request_hash = self._fingerprint("mandate.create", caller_input)
        except ValueError as error:
            raise self._failure(
                ErrorCode.SCHEMA, "mandate contains non-canonical values"
            ) from error
        self._require_idempotency_key(idempotency_key)
        try:
            with self._store.transaction(immediate=True) as connection:
                previous = self._idempotent_response(
                    connection,
                    "mandate.create",
                    idempotency_key,
                    request_hash,
                )
                if previous is not None:
                    return self._dto_data(_MandateCreateResponseDto, previous)
                normalized = self._normalize_mandate(caller_input)
                now = self._timestamp()
                mandate_hash = digest("AF:MANDATE:1", normalized)
                response = self._dto_data(
                    _MandateCreateResponseDto,
                    {
                        "mandate": {
                            **normalized,
                            "content_hash": mandate_hash,
                            "created_at": now,
                            "created_by": _CREATED_BY,
                            "schema_version": _SCHEMA_VERSION,
                            "code_version": _SCHEMA_VERSION,
                        }
                    },
                )
                existing = connection.execute(
                    """
                    SELECT content_hash FROM immutable_resources
                    WHERE resource_kind = 'MANDATE' AND resource_key = ? AND version = ?
                    """,
                    (normalized["mandate_id"], normalized["version"]),
                ).fetchone()
                if existing is not None:
                    if existing["content_hash"] != mandate_hash:
                        raise self._failure(ErrorCode.STATE, "mandate revisions are immutable")
                else:
                    connection.execute(
                        """
                        INSERT INTO immutable_resources (
                            resource_id, resource_kind, resource_key, version, semantic_json,
                            canonical_bytes_hash, content_hash, created_at, created_by,
                            schema_version, code_version
                        ) VALUES (?, 'MANDATE', ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            str(uuid4()),
                            normalized["mandate_id"],
                            normalized["version"],
                            self._json(normalized),
                            self._bytes_hash(normalized),
                            mandate_hash,
                            now,
                            _CREATED_BY,
                            _SCHEMA_VERSION,
                            _SCHEMA_VERSION,
                        ),
                    )
                self._store_idempotency(
                    connection,
                    "mandate.create",
                    idempotency_key,
                    request_hash,
                    self._json(response),
                    now,
                )
        except DomainError:
            raise
        except Exception as error:
            raise self._failure(ErrorCode.STORAGE, "could not store mandate") from error
        return self._dto_data(_MandateCreateResponseDto, response)

    def get_mandate(self, mandate_id: str) -> dict[str, object]:
        """Return the newest stored immutable revision and its most recent job link."""
        self._require_open()

        row = self._store.fetch_one(
            """
            SELECT semantic_json, content_hash, created_at, created_by, schema_version, code_version
            FROM immutable_resources
            WHERE resource_kind = 'MANDATE' AND resource_key = ?
            ORDER BY created_at DESC, version DESC
            LIMIT 1
            """,
            (mandate_id,),
        )
        if row is None:
            raise self._failure(ErrorCode.RESOURCE, "mandate was not found")
        try:
            mandate = json.loads(row["semantic_json"])
        except json.JSONDecodeError as error:
            raise self._failure(ErrorCode.STORAGE, "stored mandate is invalid") from error
        mandate["content_hash"] = row["content_hash"]
        mandate["created_at"] = row["created_at"]
        mandate["created_by"] = row["created_by"]
        mandate["schema_version"] = row["schema_version"]
        mandate["code_version"] = row["code_version"]
        latest_job = self._store.fetch_one(
            """
            SELECT job_id FROM jobs
            WHERE resource_id = ? AND kind = ?
            ORDER BY created_at DESC, job_id DESC
            LIMIT 1
            """,
            (mandate_id, JobKind.GENERATE.value),
        )
        response: dict[str, object] = {"mandate": mandate}
        if latest_job is not None:
            response["job"] = self._job_data_required(latest_job["job_id"])
        return self._dto_data(
            _MandateReadResponseDto,
            response,
            exclude_none=True,
        )

    def run_mandate(self, mandate_id: str, *, idempotency_key: str) -> dict[str, object]:
        """Queue the durable generation command for one immutable mandate."""

        self._require_open()
        self._require_idempotency_key(idempotency_key)
        mandate = self.get_mandate(mandate_id)["mandate"]
        assert isinstance(mandate, dict)
        command_fingerprint = self._fingerprint(
            "mandate.run",
            {"content_hash": mandate["content_hash"], "mandate_id": mandate_id},
        )
        return self._create_job_command(
            operation="mandate.run",
            idempotency_key=idempotency_key,
            request_hash=command_fingerprint,
            kind=JobKind.GENERATE,
            resource_id=mandate_id,
            stage="MANDATE",
            command_fingerprint=command_fingerprint,
        )

    def submit_search(self, search_spec: SearchSpec, *, idempotency_key: str) -> dict[str, object]:
        """Persist a finite search run before queuing its one durable execution job."""

        self._require_open()
        self._require_idempotency_key(idempotency_key)
        request_hash = self._fingerprint("search.run", search_spec.model_dump(mode="python"))
        now = self._timestamp()
        try:
            with self._store.transaction(immediate=True) as connection:
                previous = self._idempotent_response(
                    connection,
                    "search.run",
                    idempotency_key,
                    request_hash,
                )
                if previous is not None:
                    return self._dto_data(_SearchResponseDto, previous)

                existing = connection.execute(
                    """
                    SELECT search_run_id FROM search_runs
                    WHERE search_spec_hash = ?
                    ORDER BY created_at ASC, search_run_id ASC
                    LIMIT 1
                    """,
                    (search_spec.search_spec_hash,),
                ).fetchone()
                if existing is not None:
                    response = self._search_response(existing["search_run_id"])
                else:
                    lineage_id = str(uuid4())
                    search_run_id = str(uuid4())
                    connection.execute(
                        """
                        INSERT INTO lineages (lineage_id, status, closed_at, close_reason, root_refs_json, created_at)
                        VALUES (?, 'OPEN', NULL, NULL, ?, ?)
                        """,
                        (
                            lineage_id,
                            self._json({"search_spec_hash": search_spec.search_spec_hash}),
                            now,
                        ),
                    )
                    connection.execute(
                        """
                        INSERT INTO search_runs (
                            search_run_id, lineage_id, search_spec_hash, universe_hash, profile_hash, state,
                            stop_reason, best_candidate_hash, plateau_counter, created_at, updated_at, row_version
                        ) VALUES (?, ?, ?, ?, ?, 'QUEUED', NULL, NULL, 0, ?, ?, 1)
                        """,
                        (
                            search_run_id,
                            lineage_id,
                            search_spec.search_spec_hash,
                            search_spec.universe_hash,
                            search_spec.profile_hash,
                            now,
                            now,
                        ),
                    )
                    job = self._jobs.create_job(
                        job_id=str(uuid4()),
                        kind=JobKind.RUN_SEARCH,
                        resource_id=search_run_id,
                        command_fingerprint=request_hash,
                        stage="SEARCH",
                    )
                    response = {
                        "search_run": self._search_data(
                            {
                                "search_run_id": search_run_id,
                                "search_spec_hash": search_spec.search_spec_hash,
                                "universe_hash": search_spec.universe_hash,
                                "profile_hash": search_spec.profile_hash,
                                "state": "QUEUED",
                                "stop_reason": None,
                                "proposed_count": 0,
                                "created_at": now,
                                "updated_at": now,
                            }
                        ),
                        "job": self._job_data(job),
                    }
                response = self._dto_data(_SearchResponseDto, response)
                self._store_idempotency(
                    connection,
                    "search.run",
                    idempotency_key,
                    request_hash,
                    self._json(response),
                    now,
                )
        except DomainError:
            raise
        except Exception as error:
            raise self._failure(ErrorCode.STORAGE, "could not queue search run") from error
        return self._dto_data(_SearchResponseDto, response)

    def get_search_run(self, search_run_id: str) -> dict[str, object]:
        """Return public run state without exposing candidate trials or rejected outcomes."""

        self._require_open()
        return self._dto_data(_SearchResponseDto, self._search_response(search_run_id))

    def get_job(self, job_id: str) -> dict[str, object]:
        """Return one durable job lifecycle record."""

        self._require_open()
        return self._dto_data(_JobResponseDto, {"job": self._job_data_required(job_id)})

    def cancel_job(self, job_id: str, *, idempotency_key: str) -> dict[str, object]:
        """Request cancellation; a running job observes it only at a worker boundary."""

        self._require_open()
        self._require_idempotency_key(idempotency_key)
        request_hash = self._fingerprint("job.cancel", {"job_id": job_id})
        now = self._timestamp()
        try:
            with self._store.transaction(immediate=True) as connection:
                previous = self._idempotent_response(
                    connection,
                    "job.cancel",
                    idempotency_key,
                    request_hash,
                )
                if previous is not None:
                    return self._dto_data(_JobResponseDto, previous)
                job = self._jobs.get_job(job_id)
                if job is None:
                    raise self._failure(ErrorCode.RESOURCE, "job was not found")
                if job.status not in {JobStatus.QUEUED, JobStatus.RUNNING}:
                    raise self._failure(
                        ErrorCode.STATE, "only queued or running jobs can be cancelled"
                    )
                try:
                    cancelled = self._job_service.cancel(job_id)
                except JobStateError as error:
                    raise self._failure(
                        ErrorCode.STATE, "job cancellation lost lifecycle ownership"
                    ) from error
                response = self._dto_data(
                    _JobResponseDto,
                    {"job": self._job_data(cancelled)},
                )
                self._store_idempotency(
                    connection,
                    "job.cancel",
                    idempotency_key,
                    request_hash,
                    self._json(response),
                    now,
                    job_id=cancelled.job_id,
                )
        except DomainError:
            raise
        except Exception as error:
            raise self._failure(ErrorCode.STORAGE, "could not cancel job") from error
        return self._dto_data(_JobResponseDto, response)

    def resubmit_job(self, job_id: str, *, idempotency_key: str) -> dict[str, object]:
        """Queue a new command only for a cancelled or failed prior job."""

        self._require_open()
        self._require_idempotency_key(idempotency_key)
        source = self._jobs.get_job(job_id)
        if source is None:
            raise self._failure(ErrorCode.RESOURCE, "job was not found")
        if source.status not in {JobStatus.CANCELLED, JobStatus.FAILED}:
            raise self._failure(ErrorCode.STATE, "only cancelled or failed jobs can be resubmitted")
        if source.kind is JobKind.RUN_SEARCH:
            raise self._failure(
                ErrorCode.STATE,
                "failed searches require a new immutable search specification and lineage",
            )
        request_hash = self._fingerprint(
            "job.resubmit",
            {
                "kind": source.kind.value,
                "resource_id": source.resource_id,
                "source_job_id": source.job_id,
            },
        )
        return self._create_job_command(
            operation="job.resubmit",
            idempotency_key=idempotency_key,
            request_hash=request_hash,
            kind=source.kind,
            resource_id=source.resource_id,
            stage="RESUBMIT",
            command_fingerprint=request_hash,
        )

    def cancellation_requested(self, job_id: str) -> bool:
        """Let registered handlers check the durable cancellation boundary safely."""
        self._require_open()

        job = self._jobs.get_job(job_id)
        return job is not None and job.status is JobStatus.CANCELLED

    def execute_job(self, job: Job) -> str:
        """Dispatch a claimed job to a configured application handler, never to generated code."""

        self._require_open()
        handler = self._job_handlers.get(job.kind)
        if job.kind is JobKind.GENERATE and not self._provider_snapshot:
            raise self._failure(
                ErrorCode.CAPABILITY,
                "no LLM provider is configured for generation work",
            )
        if handler is None:
            raise self._failure(
                ErrorCode.CAPABILITY,
                f"no {job.kind.value} executor is configured for this runtime",
            )
        if job.kind is JobKind.GENERATE:
            return cast(GenerationJobHandler, handler)(job, self._provider_snapshot)
        return cast(JobHandler, handler)(job)

    def execute(self, job: Job) -> str:
        """Satisfy the worker executor port through the configured job registry."""

        return self.execute_job(job)

    def create_worker(self, *, poll_interval_seconds: float = 0.5) -> object:
        """Build the one cooperative local worker without importing it at module load."""
        self._require_open()

        from alpha_foundry.worker import PersistentWorker

        return PersistentWorker(
            self._jobs,
            self,
            poll_interval_seconds=poll_interval_seconds,
        )

    def replay_generation(self, generation_request_id: str) -> dict[str, object]:
        """Read an accepted generation artifact without acquiring a lease or calling a provider."""
        self._require_open()

        row = self._store.fetch_one(
            """
            SELECT requests.generation_request_id, requests.request_hash, requests.state,
                   artifacts.cas_uri, artifacts.content_hash
            FROM generation_requests AS requests
            LEFT JOIN artifacts ON artifacts.artifact_id = requests.accepted_artifact_id
            WHERE requests.generation_request_id = ?
            """,
            (generation_request_id,),
        )
        if row is None:
            raise self._failure(ErrorCode.RESOURCE, "generation request was not found")
        if row["state"] != "ACCEPTED":
            raise self._failure(
                ErrorCode.STATE, "only accepted generation requests can be replayed"
            )
        if not isinstance(row["cas_uri"], str) or not row["cas_uri"]:
            raise self._failure(ErrorCode.STORAGE, "accepted generation artifact is unavailable")
        try:
            artifact_json = json.loads(self._artifacts.read_bytes(row["cas_uri"]).decode("utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError) as error:
            raise self._failure(
                ErrorCode.STORAGE, "accepted generation artifact is invalid"
            ) from error
        if not isinstance(artifact_json, dict):
            raise self._failure(ErrorCode.STORAGE, "accepted generation artifact is invalid")
        return self._dto_data(
            _GenerationReplayDto,
            {
                "generation_request_id": row["generation_request_id"],
                "request_hash": row["request_hash"],
                "state": row["state"],
                "artifact_hash": row["content_hash"],
            },
        )

    def list_published_strategies(self) -> dict[str, object]:
        """List only the fixed PUBLISHED public strategy view."""
        self._require_open()

        rows = self._store.fetch_all(
            """
            SELECT strategy_id, publication_id, validation_id, candidate_hash, lineage_id, published_at
            FROM published_strategy_view
            ORDER BY published_at ASC, strategy_id ASC, publication_id ASC
            """
        )
        return self._dto_data(
            _PublishedStrategyListResponseDto,
            {"strategies": [dict(row) for row in rows]},
        )

    def get_published_strategy(self, strategy_id: str) -> dict[str, object]:
        """Return one published aggregate; unpublished and rejected IDs are not visible here."""
        self._require_open()

        row = self._store.fetch_one(
            """
            SELECT strategy_id, publication_id, validation_id, candidate_hash, lineage_id, published_at
            FROM published_strategy_view
            WHERE strategy_id = ?
            ORDER BY published_at DESC, publication_id DESC
            LIMIT 1
            """,
            (strategy_id,),
        )
        if row is None:
            raise self._failure(ErrorCode.RESOURCE, "published strategy was not found")
        return self._dto_data(
            _PublishedStrategyResponseDto,
            {
                "strategy": dict(row),
                "report": self._published_report(row["publication_id"]),
            },
        )

    def get_report(self, publication_id: str) -> dict[str, object]:
        """Return the stored canonical JSON report for a PUBLISHED publication only."""
        self._require_open()

        return self._dto_data(
            _ReportResponseDto,
            {"report": self._published_report(publication_id)},
        )

    def get_report_html(self, publication_id: str) -> str:
        """Return the stored deterministic HTML report for a PUBLISHED publication only."""
        self._require_open()

        report = self._published_report(publication_id)
        artifact = self._report_artifact(publication_id, "RESEARCH_REPORT_HTML")
        try:
            html = self._artifacts.read_bytes(artifact)
        except OSError as error:
            raise self._failure(
                ErrorCode.STORAGE, "published HTML report is unavailable"
            ) from error
        if html != render_research_report_html(report):
            raise self._failure(ErrorCode.STORAGE, "published HTML report is not canonical")
        try:
            return html.decode("utf-8")
        except UnicodeDecodeError as error:
            raise self._failure(ErrorCode.STORAGE, "published HTML report is invalid") from error

    def list_rejections(self, *, reason_code: str | None = None) -> dict[str, object]:
        """Expose redacted append-only rejection records on the internal operator surface."""
        self._require_open()

        if reason_code is None:
            rows = self._store.fetch_all(
                """
                SELECT rejection_id, terminal_stage, reason_code, redacted_summary, created_at
                FROM rejection_registry
                ORDER BY created_at ASC, rejection_id ASC
                """
            )
        else:
            rows = self._store.fetch_all(
                """
                SELECT rejection_id, terminal_stage, reason_code, redacted_summary, created_at
                FROM rejection_registry
                WHERE reason_code = ?
                ORDER BY created_at ASC, rejection_id ASC
                """,
                (reason_code,),
            )
        return self._dto_data(
            _RejectionListResponseDto,
            {
                "rejections": [
                    self._rejection_data(cast("Mapping[str, object]", row)) for row in rows
                ]
            },
        )

    def get_rejection(self, rejection_id: str) -> dict[str, object]:
        """Return one redacted internal rejection record without sealed selector detail."""
        self._require_open()

        row = self._store.fetch_one(
            """
            SELECT rejection_id, terminal_stage, reason_code, redacted_summary, created_at
            FROM rejection_registry
            WHERE rejection_id = ?
            """,
            (rejection_id,),
        )
        if row is None:
            raise self._failure(ErrorCode.RESOURCE, "rejection record was not found")
        return self._dto_data(
            _RejectionResponseDto,
            {"rejection": self._rejection_data(cast("Mapping[str, object]", row))},
        )

    def _create_job_command(
        self,
        *,
        operation: str,
        idempotency_key: str,
        request_hash: str,
        kind: JobKind,
        resource_id: str,
        stage: str,
        command_fingerprint: str,
    ) -> dict[str, object]:
        self._require_open()
        now = self._timestamp()
        try:
            with self._store.transaction(immediate=True) as connection:
                previous = self._idempotent_response(
                    connection,
                    operation,
                    idempotency_key,
                    request_hash,
                )
                if previous is not None:
                    return self._dto_data(_JobCommandResponseDto, previous)
                job = self._jobs.create_job(
                    job_id=str(uuid4()),
                    kind=kind,
                    resource_id=resource_id,
                    command_fingerprint=command_fingerprint,
                    stage=stage,
                )
                response = self._dto_data(
                    _JobCommandResponseDto,
                    {
                        "job_id": job.job_id,
                        "job": self._job_data(job),
                    },
                )
                self._store_idempotency(
                    connection,
                    operation,
                    idempotency_key,
                    request_hash,
                    self._json(response),
                    now,
                    job_id=job.job_id,
                )
        except DomainError:
            raise
        except Exception as error:
            raise self._failure(ErrorCode.STORAGE, "could not queue job") from error
        return self._dto_data(_JobCommandResponseDto, response)

    def _search_response(self, search_run_id: str) -> dict[str, object]:
        row = self._store.fetch_one(
            """
            SELECT search_run_id, search_spec_hash, universe_hash, profile_hash, state,
                   stop_reason, created_at, updated_at
            FROM search_runs WHERE search_run_id = ?
            """,
            (search_run_id,),
        )
        if row is None:
            raise self._failure(ErrorCode.RESOURCE, "search run was not found")
        proposal_count = self._store.fetch_one(
            "SELECT COUNT(*) AS count FROM search_run_proposals WHERE search_run_id = ?",
            (search_run_id,),
        )
        job_row = self._store.fetch_one(
            """
            SELECT job_id FROM jobs
            WHERE kind = ? AND resource_id = ?
            ORDER BY created_at DESC, job_id DESC
            LIMIT 1
            """,
            (JobKind.RUN_SEARCH.value, search_run_id),
        )
        return self._dto_data(
            _SearchResponseDto,
            {
                "search_run": self._search_data(
                    {
                        **dict(row),
                        "proposed_count": proposal_count["count"]
                        if proposal_count is not None
                        else 0,
                    }
                ),
                "job": self._job_data_required(job_row["job_id"]) if job_row is not None else None,
            },
        )

    def _published_report(self, publication_id: str) -> ResearchReport:
        artifact = self._report_artifact(publication_id, "RESEARCH_REPORT_JSON")
        try:
            report = parse_canonical_research_report(self._artifacts.read_bytes(artifact))
        except (OSError, ValueError) as error:
            raise self._failure(ErrorCode.STORAGE, "published JSON report is invalid") from error
        if (
            report.publication_id != publication_id
            or report.validation_decision is not Decision.PASS
        ):
            raise self._failure(
                ErrorCode.STORAGE, "published JSON report has inconsistent identity"
            )
        return report

    def _report_artifact(self, publication_id: str, role: str) -> str:
        row = self._store.fetch_one(
            """
            SELECT artifacts.cas_uri
            FROM publications
            JOIN artifact_links
                ON artifact_links.owner_type = 'PUBLICATION'
                AND artifact_links.owner_id = publications.publication_id
                AND artifact_links.role = ?
            JOIN artifacts ON artifacts.artifact_id = artifact_links.artifact_id
            WHERE publications.publication_id = ? AND publications.state = 'PUBLISHED'
            """,
            (role, publication_id),
        )
        if row is None:
            raise self._failure(ErrorCode.RESOURCE, "published report was not found")
        cas_uri = row["cas_uri"]
        if not isinstance(cas_uri, str) or not cas_uri:
            raise self._failure(ErrorCode.STORAGE, "published report artifact is invalid")
        return cas_uri


    def _knowledge_pack_required(self, pack_id: str, version: str) -> KnowledgePack:
        row = self._store.fetch_one(
            """
            SELECT pack_json FROM knowledge_packs
            WHERE knowledge_pack_id = ? AND version = ?
            """,
            (pack_id, version),
        )
        if row is None:
            raise self._failure(ErrorCode.RESOURCE, "knowledge pack was not found")
        try:
            payload = json.loads(row["pack_json"])
        except json.JSONDecodeError as error:
            raise self._failure(ErrorCode.STORAGE, "stored knowledge pack is invalid") from error
        try:
            return _KnowledgePackResponseDto.model_validate({"knowledge_pack": payload}).knowledge_pack
        except ValidationError as error:
            raise self._failure(ErrorCode.STORAGE, "stored knowledge pack is invalid") from error

    def _knowledge_pack_versions(self, pack_id: str) -> tuple[str, ...]:
        rows = self._store.fetch_all(
            "SELECT version FROM knowledge_packs WHERE knowledge_pack_id = ?",
            (pack_id,),
        )
        return tuple(str(row["version"]) for row in rows)

    @staticmethod
    def _domain_article_pack_id(domain: Domain) -> str:
        return f"{domain.value.casefold().replace('_', '-')}-articles"

    def _latest_knowledge_pack_version(self, pack_id: str) -> str:
        versions = self._knowledge_pack_versions(pack_id)
        if not versions:
            raise self._failure(
                ErrorCode.RESOURCE,
                "no article knowledge pack exists for the requested domain",
            )
        try:
            return max(
                versions,
                key=lambda version: tuple(int(piece) for piece in version.split(".")),
            )
        except ValueError as error:
            raise self._failure(
                ErrorCode.STORAGE,
                "stored article knowledge pack version is invalid",
            ) from error

    def _interactive_provider_selection(
        self, provider_name: str | None, models: Sequence[str] | None
    ) -> tuple[str, tuple[str, ...]]:
        if not self._provider_snapshot:
            raise self._failure(ErrorCode.CAPABILITY, "no LLM provider is configured")
        if provider_name is None:
            if len(self._provider_snapshot) == 1:
                resolved_provider = next(iter(self._provider_snapshot))
            elif _DEFAULT_PROVIDER in self._provider_snapshot:
                resolved_provider = _DEFAULT_PROVIDER
            else:
                raise self._failure(
                    ErrorCode.SCHEMA,
                    "multiple providers are configured; specify one explicitly",
                )
        elif not provider_name.strip():
            raise self._failure(ErrorCode.SCHEMA, "provider name must not be blank")
        else:
            resolved_provider = provider_name.strip()
        if resolved_provider not in self._provider_snapshot:
            raise self._failure(ErrorCode.CAPABILITY, "requested LLM provider is not configured")
        if models is None or not models:
            try:
                resolved_models = default_model_candidates(resolved_provider)
            except ValueError as error:
                raise self._failure(ErrorCode.SCHEMA, str(error)) from error
        else:
            normalized_models = tuple(model.strip() for model in models if model.strip())
            if not normalized_models:
                raise self._failure(ErrorCode.SCHEMA, "at least one model must be non-blank")
            resolved_models = normalized_models
        return resolved_provider, resolved_models
    def _job_data_required(self, job_id: str) -> dict[str, object]:
        job = self._jobs.get_job(job_id)
        if job is None:
            raise self._failure(ErrorCode.RESOURCE, "job was not found")
        return self._job_data(job)

    @staticmethod
    def _job_data(job: Job) -> dict[str, object]:
        return ApplicationFacade._dto_data(
            _PublicJobDto,
            {
                "job_id": job.job_id,
                "kind": job.kind,
                "status": job.status,
                "stage": job.stage,
                "error_code": job.error.code if job.error is not None else None,
                "created_at": job.created_at,
                "started_at": job.started_at,
                "finished_at": job.finished_at,
            },
        )

    @staticmethod
    def _search_data(values: Mapping[str, object]) -> dict[str, object]:
        return ApplicationFacade._dto_data(
            _SearchRunDto,
            {
                "search_run_id": values["search_run_id"],
                "search_spec_hash": values["search_spec_hash"],
                "universe_hash": values["universe_hash"],
                "profile_hash": values["profile_hash"],
                "state": values["state"],
                "stop_reason": values["stop_reason"],
                "proposed_count": values["proposed_count"],
                "created_at": values["created_at"],
                "updated_at": values["updated_at"],
            },
        )

    @staticmethod
    def _rejection_data(values: Mapping[str, object]) -> dict[str, object]:
        return ApplicationFacade._dto_data(
            _RejectionDto,
            {
                "rejection_id": values["rejection_id"],
                "terminal_stage": values["terminal_stage"],
                "reason_code": values["reason_code"],
                "redacted_summary": values["redacted_summary"],
                "created_at": values["created_at"],
            },
        )

    @staticmethod
    def _dto_data(
        model_type: type[BaseModel],
        values: Mapping[str, object],
        *,
        exclude_none: bool = False,
    ) -> dict[str, object]:
        try:
            model = model_type.model_validate(dict(values))
        except ValidationError:
            try:
                model = model_type.model_validate_json(
                    json.dumps(dict(values), ensure_ascii=False, separators=(",", ":"))
                )
            except (TypeError, ValueError, ValidationError) as error:
                raise ApplicationFacade._failure(
                    ErrorCode.STORAGE, "stored response data does not match its safe projection"
                ) from error
        serialized = model.model_dump(mode="json", exclude_none=exclude_none)
        try:
            json.dumps(serialized, ensure_ascii=False, allow_nan=False)
        except (TypeError, ValueError) as error:
            raise ApplicationFacade._failure(
                ErrorCode.STORAGE, "stored response data does not match its safe projection"
            ) from error
        return dict(serialized)

    @staticmethod
    def _failure(code: ErrorCode, message: str) -> DomainError:
        return DomainError(ErrorDetail.for_code(code, message))

    def _timestamp(self) -> str:
        self._require_open()
        value = self._clock.now()
        if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
            raise self._failure(
                ErrorCode.STATE,
                "configured clock must return a timezone-aware timestamp",
            )
        return value.astimezone(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")

    @staticmethod
    def _json(value: object) -> str:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))

    @staticmethod
    def _bytes_hash(value: object) -> str:
        return f"sha256:{sha256(canonical_bytes(value)).hexdigest()}"

    @staticmethod
    def _fingerprint(operation: str, payload: object) -> str:
        if not isinstance(payload, Mapping):
            raise ValueError("command payload must be a named-field object")
        return digest("AF:COMMAND:1", {"operation": operation, "payload": dict(payload)})

    @staticmethod
    def _require_idempotency_key(idempotency_key: str) -> None:
        if not idempotency_key.strip():
            raise ApplicationFacade._failure(ErrorCode.SCHEMA, "Idempotency-Key must not be blank")

    @staticmethod
    def _idempotent_response(
        connection: Any,
        operation: str,
        idempotency_key: str,
        request_hash: str,
    ) -> dict[str, object] | None:
        row = connection.execute(
            """
            SELECT request_hash, final_response_json FROM idempotency_keys
            WHERE operation = ? AND idempotency_key = ?
            """,
            (operation, idempotency_key),
        ).fetchone()
        if row is None:
            return None
        if row["request_hash"] != request_hash:
            raise ApplicationFacade._failure(
                ErrorCode.IDEMPOTENCY,
                "Idempotency-Key was already used with a different request",
            )
        if row["final_response_json"] is None:
            raise ApplicationFacade._failure(ErrorCode.STATE, "command is already in progress")
        try:
            response = json.loads(row["final_response_json"])
        except json.JSONDecodeError as error:
            raise ApplicationFacade._failure(
                ErrorCode.STORAGE, "stored idempotency response is invalid"
            ) from error
        if not isinstance(response, dict):
            raise ApplicationFacade._failure(
                ErrorCode.STORAGE, "stored idempotency response is invalid"
            )
        return response

    @staticmethod
    def _store_idempotency(
        connection: Any,
        operation: str,
        idempotency_key: str,
        request_hash: str,
        response_json: str,
        now: str,
        *,
        job_id: str | None = None,
    ) -> None:
        connection.execute(
            """
            INSERT INTO idempotency_keys (
                operation, idempotency_key, request_hash, job_id, final_response_json,
                created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (operation, idempotency_key, request_hash, job_id, response_json, now, now),
        )

    @staticmethod
    def _mandate_caller_input(mandate: Mapping[str, object]) -> dict[str, object]:
        try:
            caller_input = dict(mandate)
        except (TypeError, ValueError) as error:
            raise ApplicationFacade._failure(
                ErrorCode.SCHEMA, "mandate must be a named-field object"
            ) from error
        if not all(isinstance(field, str) for field in caller_input):
            raise ApplicationFacade._failure(
                ErrorCode.SCHEMA, "mandate field names must be strings"
            )
        return caller_input

    @staticmethod
    def _normalize_mandate(mandate: Mapping[str, object]) -> dict[str, object]:
        allowed = {
            "mandate_id",
            "version",
            "mode",
            "domain",
            "objective",
            "data_requirements",
            "budget",
            "forbidden_conditions",
        }
        unknown = set(mandate) - allowed
        if unknown:
            raise ApplicationFacade._failure(ErrorCode.SCHEMA, "mandate contains unknown fields")
        required = {"mode", "domain", "objective", "data_requirements", "budget"}
        missing = required - set(mandate)
        if missing:
            raise ApplicationFacade._failure(ErrorCode.SCHEMA, "mandate is missing required fields")
        mandate_id = mandate["mandate_id"] if "mandate_id" in mandate else str(uuid4())
        version = mandate.get("version", "1.0.0")
        mode = mandate["mode"]
        domain = mandate["domain"]
        objective = mandate["objective"]
        data_requirements = mandate["data_requirements"]
        budget = mandate["budget"]
        forbidden = mandate.get("forbidden_conditions", ())
        if (
            not isinstance(mandate_id, str)
            or not mandate_id.strip()
            or not isinstance(version, str)
            or not version.strip()
            or not isinstance(mode, str)
            or mode not in {"QUESTION", "DOMAIN"}
            or not isinstance(domain, str)
            or domain
            not in {
                "FACTOR",
                "STAT_ARB",
                "MARKET_MAKING",
                "STRUCTURAL_FLOW",
                "CROSS_VENUE",
                "DERIVATIVES",
                "EVENT_FUNDAMENTAL",
                "TIME_SERIES",
            }
            or not isinstance(objective, str)
            or not objective.strip()
            or not isinstance(data_requirements, (list, tuple))
            or not data_requirements
            or not all(isinstance(item, str) and item.strip() for item in data_requirements)
            or not isinstance(budget, Mapping)
            or not isinstance(forbidden, (list, tuple))
            or not all(isinstance(item, str) and item.strip() for item in forbidden)
        ):
            raise ApplicationFacade._failure(ErrorCode.SCHEMA, "mandate schema validation failed")
        normalized: dict[str, object] = {
            "mandate_id": mandate_id,
            "version": version,
            "mode": mode,
            "domain": domain,
            "objective": objective,
            "data_requirements": list(data_requirements),
            "budget": dict(budget),
            "forbidden_conditions": list(forbidden),
        }
        try:
            canonical_bytes(normalized)
        except ValueError as error:
            raise ApplicationFacade._failure(
                ErrorCode.SCHEMA, "mandate contains non-canonical values"
            ) from error
        return normalized


def bootstrap(
    settings: BootstrapSettings | None = None,
    *,
    clock: Clock | None = None,
    job_handlers: Mapping[JobKind, RegisteredJobHandler] | None = None,
) -> ApplicationFacade:
    """Assemble the local SQLite/CAS/provider runtime when explicitly invoked."""

    resolved = settings or BootstrapSettings.from_environment()
    runtime_clock = clock if clock is not None else SystemClock()
    store: SQLiteStore | None = None
    closers: tuple[Callable[[], None], ...] = ()
    try:
        store = SQLiteStore(resolved.database_path)
        artifacts = LocalArtifactStore(resolved.artifact_root)
        providers, closers = _assemble_providers(resolved)
        from alpha_foundry.labs import DEFAULT_LAB_REGISTRY

        return ApplicationFacade(
            store=store,
            artifacts=artifacts,
            providers=providers,
            clock=runtime_clock,
            job_handlers=job_handlers,
            lab_registry=DEFAULT_LAB_REGISTRY,
            closers=closers,
        )
    except Exception as error:
        cleanup_errors = _close_failed_bootstrap(closers, store)
        if cleanup_errors:
            raise ExceptionGroup(
                "bootstrap failed and cleanup also failed",
                [error, *cleanup_errors],
            ) from error
        raise


def _close_failed_bootstrap(
    closers: tuple[Callable[[], None], ...],
    store: SQLiteStore | None,
) -> tuple[Exception, ...]:
    errors: list[Exception] = []
    for closer in reversed(closers):
        try:
            closer()
        except Exception as error:
            errors.append(error)
    if store is not None:
        try:
            store.close()
        except Exception as error:
            errors.append(error)
    return tuple(errors)


def _assemble_providers(
    settings: BootstrapSettings,
) -> tuple[dict[str, LlmProviderPort], tuple[Callable[[], None], ...]]:
    configured_name = settings.llm_provider
    if configured_name is None:
        if settings.llm_api_key is not None or settings.llm_base_url is not None:
            raise ValueError("LLM credentials and URLs require an explicit provider")
        return {}, ()
    if not isinstance(configured_name, str) or not configured_name.strip():
        raise ValueError("configured LLM provider must not be blank")
    provider_name = configured_name.strip()
    if provider_name == "fake":
        if settings.llm_api_key is not None or settings.llm_base_url is not None:
            raise ValueError("fake LLM provider does not accept credentials or a base URL")
        return {"fake": FakeProvider(())}, ()
    api_key = settings.llm_api_key
    if not isinstance(api_key, str) or not api_key:
        raise ValueError("configured LLM provider requires an API key")
    configured_base_url = settings.llm_base_url
    if provider_name == _DEFAULT_PROVIDER:
        base_url = (
            configured_base_url
            if isinstance(configured_base_url, str) and configured_base_url.strip()
            else _DEFAULT_BASE_URL
        )
    else:
        if not isinstance(configured_base_url, str) or not configured_base_url.strip():
            raise ValueError("custom LLM providers require an explicit base URL")
        base_url = configured_base_url
    if not _is_http_url(base_url):
        raise ValueError("configured LLM base URL must be an absolute HTTP(S) URL")
    provider = OpenAICompatibleProvider(
        provider=provider_name,
        base_url=base_url,
        api_key=api_key,
        timeout_seconds=settings.llm_timeout_seconds,
    )
    return {provider_name: provider}, (provider.close,)


def _is_http_url(value: object) -> bool:
    if not isinstance(value, str):
        return False
    parsed = urlsplit(value)
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


def _runtime_environment() -> dict[str, str]:
    environment = dict(os.environ)
    for key, value in _dotenv_values(Path(".env")).items():
        environment.setdefault(key, value)
    return environment


def _dotenv_values(path: Path) -> dict[str, str]:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except FileNotFoundError:
        return {}
    except UnicodeError as error:
        raise ValueError(".env must be valid UTF-8 text") from error
    values: dict[str, str] = {}
    for raw_line in lines:
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].lstrip()
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        normalized_key = key.strip()
        if not normalized_key:
            continue
        values[normalized_key] = value.strip().strip('"').strip("'")
    return values


__all__ = ["ApplicationFacade", "BootstrapSettings", "JobHandler", "bootstrap"]
