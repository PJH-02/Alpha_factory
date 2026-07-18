"""LLM-assisted markdown ingestion, query, and idea pages for local research wiki flows."""

from __future__ import annotations

import re
from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from alpha_foundry.domain.canonical import digest
from alpha_foundry.domain.models import Domain, FrozenModel, Identifier, ProviderModel
from alpha_foundry.generation import (
    JsonValue,
    LlmProviderPort,
    ProviderFailure,
    ProviderRequest,
    response_schema_digest,
)
from alpha_foundry.knowledge import (
    Citation,
    Claim,
    ClaimContext,
    Counterevidence,
    FailureMemory,
    KnowledgePack,
    order_claim_context,
)

_DEFAULT_FAKE_MODEL = "wiki-fake"
_DEFAULT_NVIDIA_MODELS = ("deepseek-ai/deepseek-v4-pro", "z-ai/glm-5.2")
_DEFAULT_SAMPLING: dict[str, JsonValue] = {
    "chat_template_kwargs": {"thinking": False},
    "max_tokens": 4096,
    "temperature": 0,
    "top_p": 1,
}
_TOKEN_PATTERN = re.compile(r"\w+", re.UNICODE)
_SLUG_PATTERN = re.compile(r"[^a-z0-9]+")


class WikiImportError(RuntimeError):
    """Raised when markdown ingestion or idea generation cannot complete."""


class SourceMaterial(FrozenModel):
    relative_path: str = Field(min_length=1)
    markdown: str = Field(min_length=1)


class DraftCitation(FrozenModel):
    locator: str = Field(min_length=1)
    excerpt: str = Field(min_length=1)


class DraftCounterevidence(FrozenModel):
    statement: str = Field(min_length=1)
    citations: tuple[DraftCitation, ...] = ()


class DraftFailureMemory(FrozenModel):
    reason_code: Identifier
    summary: str = Field(min_length=1)


class DraftClaim(FrozenModel):
    statement: str = Field(min_length=1)
    citations: tuple[DraftCitation, ...] = Field(min_length=1)
    counterevidence: tuple[DraftCounterevidence, ...] = ()
    failure_memory: tuple[DraftFailureMemory, ...] = ()


class DraftPage(FrozenModel):
    title: str = Field(min_length=1)
    summary: str = Field(min_length=1)
    body_markdown: str = Field(min_length=1)
    key_people: tuple[str, ...] = ()
    claims: tuple[DraftClaim, ...] = Field(min_length=1)


class IdeaDraft(FrozenModel):
    title: str = Field(min_length=1)
    summary: str = Field(min_length=1)
    body_markdown: str = Field(min_length=1)
    supporting_claim_ids: tuple[Identifier, ...] = Field(min_length=1)


class IdeaBundle(FrozenModel):
    ideas: tuple[IdeaDraft, ...] = Field(min_length=1)


class WikiPage(FrozenModel):
    page_id: Identifier
    kind: Literal["SOURCE", "IDEA"]
    title: str = Field(min_length=1)
    summary: str = Field(min_length=1)
    body_markdown: str = Field(min_length=1)
    source_documents: tuple[str, ...] = ()
    related_pages: tuple[Identifier, ...] = ()
    claim_ids: tuple[Identifier, ...] = ()
    key_people: tuple[str, ...] = ()


class WikiIndex(FrozenModel):
    pack_id: Identifier
    pack_version: str = Field(min_length=1)
    pages: tuple[WikiPage, ...]


class WikiImportResult(FrozenModel):
    knowledge_pack: KnowledgePack
    pack_path: str = Field(min_length=1)
    index_path: str = Field(min_length=1)
    page_paths: tuple[str, ...]
    models_used: tuple[str, ...]


class WikiClaimMatch(FrozenModel):
    claim_id: Identifier
    page_id: Identifier
    page_path: str = Field(min_length=1)
    source: str = Field(min_length=1)
    statement: str = Field(min_length=1)
    version: str = Field(min_length=1)
    match_score: int = Field(ge=0)


class WikiPageMatch(FrozenModel):
    page_id: Identifier
    page_path: str = Field(min_length=1)
    title: str = Field(min_length=1)
    summary: str = Field(min_length=1)
    match_score: int = Field(ge=0)
    claim_ids: tuple[Identifier, ...]


class WikiQueryResult(FrozenModel):
    pack_id: Identifier
    pack_version: str = Field(min_length=1)
    query: str = Field(min_length=1)
    pages: tuple[WikiPageMatch, ...]
    claims: tuple[WikiClaimMatch, ...]


class WikiIdeaResult(FrozenModel):
    query: str = Field(min_length=1)
    idea_paths: tuple[str, ...]
    idea_page_ids: tuple[Identifier, ...]
    models_used: tuple[str, ...]


class _StructuredCompletionResult(BaseModel):
    model_config = ConfigDict(extra="forbid", arbitrary_types_allowed=True)

    content: BaseModel
    model_name: str


def default_wiki_root() -> Path:
    return Path("knowledge") / "wiki"


def default_model_candidates(provider_name: str) -> tuple[str, ...]:
    if provider_name == "fake":
        return (_DEFAULT_FAKE_MODEL,)
    if provider_name == "nvidia":
        return _DEFAULT_NVIDIA_MODELS
    raise ValueError("non-NVIDIA providers require an explicit model")


def next_pack_version(existing_versions: Iterable[str]) -> str:
    latest = (0, 0, -1)
    for version in existing_versions:
        pieces = version.split(".")
        if len(pieces) != 3 or any(not piece.isdigit() for piece in pieces):
            raise ValueError("existing knowledge pack versions must be numeric semantic versions")
        current = (int(pieces[0]), int(pieces[1]), int(pieces[2]))
        latest = max(latest, current)
    if latest == (0, 0, -1):
        return "1.0.0"
    return f"{latest[0]}.{latest[1]}.{latest[2] + 1}"


def discover_markdown_materials(source_root: Path) -> tuple[SourceMaterial, ...]:
    if not source_root.exists() or not source_root.is_dir():
        raise ValueError("source_root must be an existing directory")
    materials: list[SourceMaterial] = []
    for path in sorted(source_root.rglob("*.md")):
        relative_path = path.relative_to(source_root).as_posix()
        markdown = path.read_text(encoding="utf-8")
        if markdown.strip():
            materials.append(SourceMaterial(relative_path=relative_path, markdown=markdown))
    if not materials:
        raise ValueError("source_root must contain at least one non-empty markdown file")
    return tuple(materials)


def import_markdown_corpus(
    *,
    source_root: Path,
    wiki_root: Path,
    pack_id: str,
    pack_version: str,
    domain: Domain,
    providers: Mapping[str, LlmProviderPort],
    provider_name: str,
    model_names: Sequence[str],
) -> WikiImportResult:
    materials = discover_markdown_materials(source_root)
    drafts: list[DraftPage] = []
    models_used: list[str] = []
    for material in materials:
        completion = _structured_completion(
            providers=providers,
            provider_name=provider_name,
            model_names=model_names,
            operation="knowledge.import-markdown",
            payload={
                "domain": domain.value,
                "material": material.model_dump(mode="python"),
                "requirements": {
                    "body": "Write a concise wiki page grounded only in the markdown.",
                    "claims": "Return evidence-backed claims with precise locators and excerpts.",
                    "language": "Keep the source language where practical.",
                },
            },
            response_model=DraftPage,
        )
        drafts.append(DraftPage.model_validate(completion.content.model_dump(mode="python")))
        models_used.append(completion.model_name)
    knowledge_pack, pages = _assemble_pack(
        pack_id=pack_id,
        pack_version=pack_version,
        domain=domain,
        materials=materials,
        drafts=tuple(drafts),
    )
    index = WikiIndex(pack_id=pack_id, pack_version=pack_version, pages=pages)
    pack_path, index_path, page_paths = write_wiki_pack(wiki_root, knowledge_pack, index)
    return WikiImportResult(
        knowledge_pack=knowledge_pack,
        pack_path=pack_path,
        index_path=index_path,
        page_paths=page_paths,
        models_used=tuple(dict.fromkeys(models_used)),
    )


def query_pack(
    *,
    pack: KnowledgePack,
    wiki_root: Path,
    query: str,
    domain: Domain,
    limit: int,
) -> WikiQueryResult:
    if limit <= 0:
        raise ValueError("limit must be positive")
    index = read_wiki_index(wiki_root, pack.id, pack.version)
    page_by_path = {page_path(pack.id, pack.version, page.page_id): page for page in index.pages}
    contexts = order_claim_context(pack.claims, domain=domain, query=query, limit=limit)
    claims = tuple(
        WikiClaimMatch(
            claim_id=context.claim.id,
            page_id=_page_id_from_source(context.claim.source),
            page_path=context.claim.source,
            source=context.claim.source,
            statement=context.claim.statement,
            version=context.claim.version,
            match_score=context.match_score,
        )
        for context in contexts
    )
    page_scores: dict[str, int] = defaultdict(int)
    for claim in claims:
        page_scores[claim.page_path] = max(page_scores[claim.page_path], claim.match_score)
    pages = tuple(
        sorted(
            (
                WikiPageMatch(
                    page_id=page.page_id,
                    page_path=path,
                    title=page.title,
                    summary=page.summary,
                    match_score=score,
                    claim_ids=tuple(
                        claim.claim_id for claim in claims if claim.page_path == path
                    ),
                )
                for path, score in page_scores.items()
                if (page := page_by_path.get(path)) is not None
            ),
            key=lambda item: (-item.match_score, item.page_id),
        )
    )
    return WikiQueryResult(
        pack_id=pack.id,
        pack_version=pack.version,
        query=query,
        pages=pages,
        claims=claims,
    )


def generate_idea_pages(
    *,
    pack: KnowledgePack,
    wiki_root: Path,
    query: str,
    domain: Domain,
    limit: int,
    idea_count: int,
    providers: Mapping[str, LlmProviderPort],
    provider_name: str,
    model_names: Sequence[str],
) -> WikiIdeaResult:
    matches = order_claim_context(pack.claims, domain=domain, query=query, limit=limit)
    if not matches:
        raise ValueError("query did not match any wiki-backed claims")
    completion = _structured_completion(
        providers=providers,
        provider_name=provider_name,
        model_names=model_names,
        operation="knowledge.ideate",
        payload={
            "domain": domain.value,
            "query": query,
            "idea_count": idea_count,
            "claims": [
                {
                    "claim_id": match.claim.id,
                    "version": match.claim.version,
                    "source": match.claim.source,
                    "statement": match.claim.statement,
                    "full_text": match.full_text,
                    "match_score": match.match_score,
                }
                for match in matches
            ],
            "requirements": {
                "grounding": "Use only the supplied wiki-backed claims.",
                "output": "Return durable idea pages with concise, testable hypotheses.",
            },
        },
        response_model=IdeaBundle,
    )
    bundle = IdeaBundle.model_validate(completion.content.model_dump(mode="python"))
    index = read_wiki_index(wiki_root, pack.id, pack.version)
    ideas = _idea_pages(pack=pack, matches=matches, drafts=bundle.ideas, index=index)
    idea_paths = write_idea_pages(wiki_root, pack.id, pack.version, ideas)
    merged_index = WikiIndex(pack_id=index.pack_id, pack_version=index.pack_version, pages=index.pages + ideas)
    write_wiki_index(wiki_root, merged_index)
    return WikiIdeaResult(
        query=query,
        idea_paths=idea_paths,
        idea_page_ids=tuple(page.page_id for page in ideas),
        models_used=(completion.model_name,),
    )


def read_wiki_index(wiki_root: Path, pack_id: str, pack_version: str) -> WikiIndex:
    path = index_path(wiki_root, pack_id, pack_version)
    return WikiIndex.model_validate_json(path.read_text(encoding="utf-8"))


def write_wiki_pack(
    wiki_root: Path,
    knowledge_pack: KnowledgePack,
    index: WikiIndex,
) -> tuple[str, str, tuple[str, ...]]:
    pack_target = pack_path(wiki_root, knowledge_pack.id, knowledge_pack.version)
    pack_target.parent.mkdir(parents=True, exist_ok=True)
    pack_target.write_text(knowledge_pack.model_dump_json(indent=2), encoding="utf-8")
    page_target_dir = wiki_root / "pages" / knowledge_pack.id / knowledge_pack.version
    page_target_dir.mkdir(parents=True, exist_ok=True)
    page_paths: list[str] = []
    for page in index.pages:
        target = page_target_dir / f"{page.page_id}.md"
        target.write_text(_render_page(page, knowledge_pack.id, knowledge_pack.version), encoding="utf-8")
        page_paths.append(target.as_posix())
    index_target = write_wiki_index(wiki_root, index)
    return pack_target.as_posix(), index_target, tuple(page_paths)


def write_wiki_index(wiki_root: Path, index: WikiIndex) -> str:
    target = index_path(wiki_root, index.pack_id, index.pack_version)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(index.model_dump_json(indent=2), encoding="utf-8")
    latest = target.parent.parent / "latest.json"
    latest.write_text(index.model_dump_json(indent=2), encoding="utf-8")
    return target.as_posix()


def write_idea_pages(
    wiki_root: Path,
    pack_id: str,
    pack_version: str,
    pages: Sequence[WikiPage],
) -> tuple[str, ...]:
    target_dir = wiki_root / "ideas" / pack_id / pack_version
    target_dir.mkdir(parents=True, exist_ok=True)
    written: list[str] = []
    for page in pages:
        target = target_dir / f"{page.page_id}.md"
        target.write_text(_render_page(page, pack_id, pack_version), encoding="utf-8")
        written.append(target.as_posix())
    return tuple(written)


def page_path(pack_id: str, pack_version: str, page_id: str) -> str:
    return (Path("pages") / pack_id / pack_version / f"{page_id}.md").as_posix()


def index_path(wiki_root: Path, pack_id: str, pack_version: str) -> Path:
    return wiki_root / "indexes" / pack_id / pack_version / "index.json"


def pack_path(wiki_root: Path, pack_id: str, pack_version: str) -> Path:
    return wiki_root / "packs" / pack_id / pack_version / "knowledge-pack.json"


def _assemble_pack(
    *,
    pack_id: str,
    pack_version: str,
    domain: Domain,
    materials: Sequence[SourceMaterial],
    drafts: Sequence[DraftPage],
) -> tuple[KnowledgePack, tuple[WikiPage, ...]]:
    pages: list[WikiPage] = []
    claims: list[Claim] = []
    for material, draft in zip(materials, drafts, strict=True):
        page_id = _material_page_id(material.relative_path)
        page_claim_ids: list[str] = []
        source_path = page_path(pack_id, pack_version, page_id)
        for index, draft_claim in enumerate(draft.claims, start=1):
            claim_id = f"{page_id}.claim.{index}"
            page_claim_ids.append(claim_id)
            claims.append(
                Claim(
                    id=claim_id,
                    version=pack_version,
                    domain=domain,
                    statement=draft_claim.statement,
                    source=source_path,
                    citations=tuple(
                        Citation(
                            source_id=material.relative_path,
                            locator=citation.locator,
                            excerpt=citation.excerpt,
                        )
                        for citation in draft_claim.citations
                    ),
                    counterevidence=tuple(
                        Counterevidence(
                            statement=item.statement,
                            citations=tuple(
                                Citation(
                                    source_id=material.relative_path,
                                    locator=citation.locator,
                                    excerpt=citation.excerpt,
                                )
                                for citation in item.citations
                            ),
                        )
                        for item in draft_claim.counterevidence
                    ),
                    failure_memory=tuple(
                        FailureMemory(reason_code=item.reason_code, summary=item.summary)
                        for item in draft_claim.failure_memory
                    ),
                )
            )
        pages.append(
            WikiPage(
                page_id=page_id,
                kind="SOURCE",
                title=draft.title,
                summary=draft.summary,
                body_markdown=draft.body_markdown,
                source_documents=(material.relative_path,),
                claim_ids=tuple(page_claim_ids),
                key_people=tuple(person for person in draft.key_people if person.strip()),
            )
        )
    related_pages = _related_pages(pages)
    updated_pages = tuple(
        page.model_copy(update={"related_pages": related_pages[page.page_id]}) for page in pages
    )
    return KnowledgePack(id=pack_id, version=pack_version, claims=tuple(claims)), updated_pages


def _idea_pages(
    *,
    pack: KnowledgePack,
    matches: Sequence[ClaimContext],
    drafts: Sequence[IdeaDraft],
    index: WikiIndex,
) -> tuple[WikiPage, ...]:
    claims_by_id = {claim.id: claim for claim in pack.claims}
    known_page_ids = {page.page_id for page in index.pages}
    pages: list[WikiPage] = []
    for draft in drafts:
        try:
            supporting_claims = tuple(claims_by_id[claim_id] for claim_id in draft.supporting_claim_ids)
        except KeyError as error:
            raise ValueError("idea page referenced an unknown supporting claim") from error
        page_id = _idea_page_id(draft.title, draft.summary, draft.supporting_claim_ids)
        supporting_pages = tuple(sorted({_page_id_from_source(claim.source) for claim in supporting_claims}))
        related_pages = tuple(page_id for page_id in supporting_pages if page_id in known_page_ids)
        pages.append(
            WikiPage(
                page_id=page_id,
                kind="IDEA",
                title=draft.title,
                summary=draft.summary,
                body_markdown=draft.body_markdown,
                source_documents=tuple(sorted({claim.source for claim in supporting_claims})),
                related_pages=related_pages,
                claim_ids=draft.supporting_claim_ids,
            )
        )
    return tuple(pages)


def _related_pages(pages: Sequence[WikiPage]) -> dict[str, tuple[str, ...]]:
    tokens_by_page = {
        page.page_id: set(_tokens(" ".join((page.title, page.summary, " ".join(page.key_people)))))
        for page in pages
    }
    related: dict[str, tuple[str, ...]] = {}
    for page in pages:
        scored = []
        for candidate in pages:
            if candidate.page_id == page.page_id:
                continue
            overlap = len(tokens_by_page[page.page_id] & tokens_by_page[candidate.page_id])
            if overlap <= 0:
                continue
            scored.append((overlap, candidate.page_id))
        scored.sort(key=lambda item: (-item[0], item[1]))
        related[page.page_id] = tuple(page_id for _, page_id in scored[:3])
    return related


def _structured_completion(
    *,
    providers: Mapping[str, LlmProviderPort],
    provider_name: str,
    model_names: Sequence[str],
    operation: str,
    payload: Mapping[str, JsonValue],
    response_model: type[FrozenModel],
) -> _StructuredCompletionResult:
    provider = providers.get(provider_name)
    if provider is None:
        raise ValueError("configured provider is not available")
    schema = response_model.model_json_schema(mode="validation")
    schema_hash = response_schema_digest(schema)
    errors: list[str] = []
    for ordinal, model_name in enumerate(model_names):
        request_hash = digest(
            "AF:WIKI_REQUEST:1",
            {
                "model": model_name,
                "operation": operation,
                "payload": dict(payload),
                "provider": provider_name,
                "schema_hash": schema_hash,
            },
        )
        request = ProviderRequest(
            request_hash=request_hash,
            provider_model=ProviderModel(
                ordinal=ordinal,
                provider=provider_name,
                model=model_name,
                config_hash=digest(
                    "AF:WIKI_PROVIDER_CONFIG:1",
                    {"model": model_name, "operation": operation, "provider": provider_name},
                ),
            ),
            payload={
                **payload,
                "request_hash": request_hash,
                "request_schema_hash": schema_hash,
                "sampling": _DEFAULT_SAMPLING,
            },
            response_schema=schema,
        )
        try:
            response = provider.complete(request)
            content = response_model.model_validate_json(response.content)
            return _StructuredCompletionResult(content=content, model_name=model_name)
        except (ProviderFailure, ValidationError, ValueError) as error:
            errors.append(f"{model_name}: {error}")
    raise WikiImportError("no configured model completed the structured request: " + "; ".join(errors))


def _render_page(page: WikiPage, pack_id: str, pack_version: str) -> str:
    source_lines = "\n".join(f"- {source}" for source in page.source_documents) or "- none"
    related_lines = "\n".join(f"- [[{page_id}]]" for page_id in page.related_pages) or "- none"
    claim_lines = "\n".join(f"- {claim_id}" for claim_id in page.claim_ids) or "- none"
    people_lines = "\n".join(f"- {person}" for person in page.key_people) or "- none"
    return (
        f"---\n"
        f"page_id: {page.page_id}\n"
        f"pack_id: {pack_id}\n"
        f"pack_version: {pack_version}\n"
        f"kind: {page.kind}\n"
        f"---\n\n"
        f"# {page.title}\n\n"
        f"## Summary\n\n{page.summary}\n\n"
        f"## Source Documents\n\n{source_lines}\n\n"
        f"## Key People\n\n{people_lines}\n\n"
        f"## Wiki Body\n\n{page.body_markdown}\n\n"
        f"## Claim IDs\n\n{claim_lines}\n\n"
        f"## Related Pages\n\n{related_lines}\n"
    )


def _material_page_id(relative_path: str) -> str:
    return _slug(Path(relative_path).with_suffix("").as_posix())


def _idea_page_id(title: str, summary: str, claim_ids: Sequence[str]) -> str:
    suffix = digest(
        "AF:WIKI_IDEA:1",
        {"claim_ids": list(claim_ids), "summary": summary, "title": title},
    ).removeprefix("sha256:")[:12]
    return f"idea-{_slug(title)}-{suffix}"


def _page_id_from_source(source: str) -> str:
    return Path(source).stem


def _slug(value: str) -> str:
    lowered = value.casefold()
    normalized = _SLUG_PATTERN.sub("-", lowered).strip("-")
    return normalized or "page"


def _tokens(value: str) -> tuple[str, ...]:
    return tuple(_TOKEN_PATTERN.findall(value.casefold()))


__all__ = [
    "WikiClaimMatch",
    "WikiIdeaResult",
    "WikiImportError",
    "WikiImportResult",
    "WikiIndex",
    "WikiPage",
    "WikiPageMatch",
    "WikiQueryResult",
    "default_model_candidates",
    "default_wiki_root",
    "discover_markdown_materials",
    "generate_idea_pages",
    "import_markdown_corpus",
    "next_pack_version",
    "page_path",
    "query_pack",
    "read_wiki_index",
]
