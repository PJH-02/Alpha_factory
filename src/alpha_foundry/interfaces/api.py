"""FastAPI adapter over the shared Alpha Foundry application facade."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping
from contextlib import asynccontextmanager
from time import perf_counter
from typing import Annotated, Literal, TypedDict
from uuid import UUID, uuid4

from fastapi import FastAPI, Header, HTTPException, Query, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel, ConfigDict

from alpha_foundry.application.jobs import JobStateError
from alpha_foundry.bootstrap import ApplicationFacade, bootstrap
from alpha_foundry.domain.errors import DomainError, ErrorCode, ErrorDetail, ErrorField
from alpha_foundry.domain.models import Domain, FrozenModel
from alpha_foundry.knowledge import KnowledgePack
from alpha_foundry.search.models import SearchSpec

_API_VERSION = "1.0"
_LOGGER = logging.getLogger("alpha_foundry.api")

IdempotencyKey = Annotated[str, Header(alias="Idempotency-Key")]


class SearchRunSubmission(BaseModel):
    """Strict REST envelope for the canonical finite SearchSpec command."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    search_spec: SearchSpec


class MandateSubmission(FrozenModel):
    """Strict frozen transport contract for one local research mandate."""

    mandate_id: str | None = None
    version: str = "1.0.0"
    mode: Literal["QUESTION", "DOMAIN"]
    domain: Domain
    objective: str
    data_requirements: tuple[str, ...]
    budget: dict[str, object]
    forbidden_conditions: tuple[str, ...] = ()


class _ApiRequestLog(TypedDict):
    """Fixed allowlist for request observability values."""

    correlation_id: str
    job_id: None
    request_hash: None
    attempt_ordinal: None
    search_run_id: None
    generation_index: None
    slot_index: None
    candidate_trial_id: None
    publication_id: None
    stage: Literal["API_REQUEST"]
    duration_ms: int
    error_code: str | None
    status_code: int


def create_app(facade: ApplicationFacade | None = None) -> FastAPI:
    """Create an API application without constructing runtime resources at import time."""

    owns_facade = facade is None
    runtime = facade or bootstrap()

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        try:
            runtime.startup()
        except BaseException as error:
            if owns_facade:
                _close_after_lifecycle_failure(runtime, error)
            raise
        try:
            yield
        except BaseException as error:
            if owns_facade:
                _close_after_lifecycle_failure(runtime, error)
            raise
        else:
            if owns_facade:
                runtime.close()

    application = FastAPI(title="Alpha Foundry API", version=_API_VERSION, lifespan=lifespan)
    application.state.alpha_foundry = runtime
    application.state.alpha_foundry_owns_facade = owns_facade

    @application.middleware("http")
    async def correlation_id(
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        request.state.correlation_id = _correlation_id(request.headers.get("X-Correlation-ID"))
        started_at = perf_counter()
        response: Response | None = None
        try:
            response = await call_next(request)
            response.headers["X-Correlation-ID"] = request.state.correlation_id
            return response
        except Exception:
            request.state.observability_error_code = ErrorCode.STORAGE
            raise
        finally:
            _LOGGER.info(
                _request_event(
                    request,
                    duration_ms=int((perf_counter() - started_at) * 1_000),
                    status_code=response.status_code if response is not None else 500,
                )
            )

    @application.exception_handler(DomainError)
    async def domain_error(request: Request, error: DomainError) -> JSONResponse:
        return _error_response(request, error.detail)

    @application.exception_handler(JobStateError)
    async def job_state_error(request: Request, error: JobStateError) -> JSONResponse:
        detail = ErrorDetail.for_code(ErrorCode.STATE, "job lifecycle transition is not available")
        return _error_response(request, detail)

    @application.exception_handler(RequestValidationError)
    async def request_validation_error(
        request: Request,
        error: RequestValidationError,
    ) -> JSONResponse:
        detail = ErrorDetail.for_code(
            ErrorCode.SCHEMA,
            "request schema validation failed",
            tuple(
                ErrorField(path=_validation_path(item.get("loc", ())), reason=item["msg"])
                for item in error.errors()
            ),
        )
        return _error_response(request, detail)

    @application.exception_handler(HTTPException)
    async def http_error(request: Request, error: HTTPException) -> JSONResponse:
        return _error_response(request, _http_error_detail(error.status_code))

    @application.exception_handler(Exception)
    async def unexpected_error(request: Request, error: Exception) -> JSONResponse:
        detail = ErrorDetail.for_code(ErrorCode.STORAGE, "unexpected application failure")
        return _error_response(request, detail)

    @application.get("/api/v1/health")
    async def health(request: Request) -> dict[str, object]:
        return _envelope(request, runtime.health())

    @application.post("/api/v1/knowledge/packs", status_code=201)
    async def add_knowledge_pack(
        request: Request,
        pack: KnowledgePack,
        idempotency_key: IdempotencyKey,
    ) -> dict[str, object]:
        return _envelope(request, runtime.add_knowledge_pack(pack, idempotency_key=idempotency_key))

    @application.get("/api/v1/knowledge/packs/{pack_id}/versions/{version}")
    async def show_knowledge_pack(
        request: Request, pack_id: str, version: str
    ) -> dict[str, object]:
        return _envelope(request, runtime.get_knowledge_pack(pack_id, version))

    @application.post("/api/v1/mandates", status_code=201)
    async def create_mandate(
        request: Request,
        mandate: MandateSubmission,
        idempotency_key: IdempotencyKey,
    ) -> dict[str, object]:
        return _envelope(
            request,
            runtime.create_mandate(
                mandate.model_dump(mode="python", exclude_none=True),
                idempotency_key=idempotency_key,
            ),
        )

    @application.get("/api/v1/mandates/{mandate_id}")
    async def show_mandate(request: Request, mandate_id: str) -> dict[str, object]:
        return _envelope(request, runtime.get_mandate(mandate_id))

    @application.post("/api/v1/mandates/{mandate_id}/run", status_code=202)
    async def run_mandate(
        request: Request,
        mandate_id: str,
        idempotency_key: IdempotencyKey,
    ) -> dict[str, object]:
        return _envelope(request, runtime.run_mandate(mandate_id, idempotency_key=idempotency_key))

    @application.post("/api/v1/search-runs", status_code=202)
    async def submit_search(
        request: Request,
        submission: SearchRunSubmission,
        idempotency_key: IdempotencyKey,
    ) -> dict[str, object]:
        return _envelope(
            request,
            runtime.submit_search(submission.search_spec, idempotency_key=idempotency_key),
        )

    @application.get("/api/v1/search-runs/{search_run_id}")
    async def show_search(request: Request, search_run_id: str) -> dict[str, object]:
        return _envelope(request, runtime.get_search_run(search_run_id))

    @application.get("/api/v1/jobs/{job_id}")
    async def show_job(request: Request, job_id: str) -> dict[str, object]:
        return _envelope(request, runtime.get_job(job_id))

    @application.post("/api/v1/jobs/{job_id}/cancel", status_code=202)
    async def cancel_job(
        request: Request,
        job_id: str,
        idempotency_key: IdempotencyKey,
    ) -> dict[str, object]:
        return _envelope(request, runtime.cancel_job(job_id, idempotency_key=idempotency_key))

    @application.post("/api/v1/jobs/{job_id}/resubmit", status_code=202)
    async def resubmit_job(
        request: Request,
        job_id: str,
        idempotency_key: IdempotencyKey,
    ) -> dict[str, object]:
        return _envelope(request, runtime.resubmit_job(job_id, idempotency_key=idempotency_key))

    @application.post("/api/v1/generation/requests/{generation_request_id}/replay")
    async def replay_generation(request: Request, generation_request_id: str) -> dict[str, object]:
        return _envelope(request, runtime.replay_generation(generation_request_id))

    @application.get("/api/v1/registry/strategies")
    async def list_strategies(request: Request) -> dict[str, object]:
        return _envelope(request, runtime.list_published_strategies())

    @application.get("/api/v1/registry/strategies/{strategy_id}")
    async def show_strategy(request: Request, strategy_id: str) -> dict[str, object]:
        return _envelope(request, runtime.get_published_strategy(strategy_id))

    @application.get("/api/v1/reports/{publication_id}")
    async def show_report(request: Request, publication_id: str) -> dict[str, object]:
        return _envelope(request, runtime.get_report(publication_id))

    @application.get("/api/v1/reports/{publication_id}/html")
    async def show_html_report(request: Request, publication_id: str) -> HTMLResponse:
        return HTMLResponse(runtime.get_report_html(publication_id))

    @application.get("/api/v1/internal/rejections")
    async def list_rejections(
        request: Request,
        reason: Annotated[str | None, Query()] = None,
    ) -> dict[str, object]:
        return _envelope(request, runtime.list_rejections(reason_code=reason))

    @application.get("/api/v1/internal/rejections/{rejection_id}")
    async def show_rejection(request: Request, rejection_id: str) -> dict[str, object]:
        return _envelope(request, runtime.get_rejection(rejection_id))

    return application


def _close_after_lifecycle_failure(
    runtime: ApplicationFacade,
    primary_error: BaseException,
) -> None:
    try:
        runtime.close()
    except BaseException as cleanup_error:
        raise BaseExceptionGroup(
            "application lifecycle failed and runtime cleanup also failed",
            [primary_error, cleanup_error],
        ) from primary_error


def _envelope(request: Request, data: Mapping[str, object]) -> dict[str, object]:
    return {
        "data": dict(data),
        "meta": {"correlation_id": _request_correlation_id(request), "api_version": _API_VERSION},
    }


def _error_response(
    request: Request,
    detail: ErrorDetail,
) -> JSONResponse:
    request.state.observability_error_code = detail.code
    return JSONResponse(
        status_code=_status_code(detail.code),
        content={
            "error": detail.model_dump(mode="json"),
            "meta": {
                "correlation_id": _request_correlation_id(request),
                "api_version": _API_VERSION,
            },
        },
    )


def _status_code(code: ErrorCode) -> int:
    return {
        ErrorCode.SCHEMA: 400,
        ErrorCode.DOMAIN: 400,
        ErrorCode.POLICY: 400,
        ErrorCode.RESOURCE: 404,
        ErrorCode.STATE: 409,
        ErrorCode.IDEMPOTENCY: 409,
        ErrorCode.HOLDOUT: 409,
        ErrorCode.CAPABILITY: 422,
        ErrorCode.PBO_INCOMPLETE: 422,
        ErrorCode.VALIDATION: 422,
        ErrorCode.JOB_INTERRUPTED: 500,
        ErrorCode.LLM: 502,
        ErrorCode.STORAGE: 500,
    }[code]


def _http_error_detail(status_code: int) -> ErrorDetail:
    if status_code == 404:
        return ErrorDetail.for_code(ErrorCode.RESOURCE, "resource was not found")
    if 400 <= status_code < 500:
        return ErrorDetail.for_code(ErrorCode.SCHEMA, "request is not supported")
    return ErrorDetail.for_code(ErrorCode.STORAGE, "unexpected application failure")


def _validation_path(location: object) -> str:
    if isinstance(location, tuple):
        return ".".join(str(part) for part in location) or "body"
    return str(location) or "body"


def _request_event(
    request: Request,
    *,
    duration_ms: int,
    status_code: int,
) -> _ApiRequestLog:
    return {
        "correlation_id": _request_correlation_id(request),
        "job_id": None,
        "request_hash": None,
        "attempt_ordinal": None,
        "search_run_id": None,
        "generation_index": None,
        "slot_index": None,
        "candidate_trial_id": None,
        "publication_id": None,
        "stage": "API_REQUEST",
        "duration_ms": max(duration_ms, 0),
        "error_code": _request_error_code(request),
        "status_code": status_code,
    }


def _request_error_code(request: Request) -> str | None:
    code = getattr(request.state, "observability_error_code", None)
    return code.value if isinstance(code, ErrorCode) else None


def _correlation_id(candidate: str | None) -> str:
    if candidate is not None:
        try:
            return str(UUID(candidate))
        except ValueError:
            pass
    return str(uuid4())


def _request_correlation_id(request: Request) -> str:
    correlation_id = getattr(request.state, "correlation_id", None)
    return correlation_id if isinstance(correlation_id, str) else str(uuid4())


__all__ = ["create_app"]
