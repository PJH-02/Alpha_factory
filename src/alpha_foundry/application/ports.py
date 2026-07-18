"""Application-facing boundaries for time, external work, artifacts, and jobs."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from alpha_foundry.domain.errors import ErrorDetail
    from alpha_foundry.domain.models import (
        Domain,
        ExperimentConfig,
        ExperimentResult,
        Job,
        JobKind,
        JobProgress,
        JobStatus,
        StrategySpec,
    )

type JsonScalar = None | bool | int | str
type JsonValue = JsonScalar | list["JsonValue"] | dict[str, "JsonValue"]


class Clock(Protocol):
    """Returns the current UTC timestamp from an injectable source."""

    def now(self) -> datetime:
        """Return a timezone-aware UTC timestamp."""


class LlmProvider(Protocol):
    """Calls one already-pinned provider/model and returns plain JSON only."""

    def generate(
        self,
        *,
        provider: str,
        model: str,
        request: Mapping[str, JsonValue],
    ) -> Mapping[str, JsonValue]:
        """Return an untrusted JSON response for deterministic validation."""


class DomainEngine(Protocol):
    """Runs a typed strategy against explicitly pinned inputs and policies."""

    def run(
        self,
        *,
        domain: Domain,
        strategy: StrategySpec,
        config: ExperimentConfig,
    ) -> ExperimentResult:
        """Return a deterministic, typed result without mutating any input."""


class ArtifactMetadata(Protocol):
    """Verified metadata for one content-addressed payload."""

    @property
    def cas_uri(self) -> str:
        """Return the stable content-addressed URI."""

    @property
    def content_hash(self) -> str:
        """Return the SHA-256 display digest."""

    @property
    def byte_size(self) -> int:
        """Return the exact payload size."""

    @property
    def media_type(self) -> str:
        """Return the payload media type."""


class ArtifactStore(Protocol):
    """Stores verified bytes before a caller records matching DB metadata."""

    def put_bytes(self, payload: bytes, media_type: str) -> ArtifactMetadata:
        """Atomically write a content-addressed payload and return its metadata."""

    def read_bytes(self, reference: str) -> bytes:
        """Read and verify one content-addressed payload."""


class JobRepository(Protocol):
    """Persistence boundary used by the persistent single-worker service."""

    def create_job(
        self,
        *,
        job_id: str,
        kind: JobKind,
        resource_id: str,
        command_fingerprint: str,
        stage: str,
        progress: JobProgress | None = None,
    ) -> Job:
        """Durably enqueue a job or return the matching existing job."""

    def get_job(self, job_id: str) -> Job | None:
        """Return the persisted job, if it exists."""

    def claim_next_queued_job(self) -> Job | None:
        """Atomically transition the oldest queued job to running."""

    def transition_job(
        self,
        *,
        job_id: str,
        expected_status: JobStatus,
        target_status: JobStatus,
        stage: str | None = None,
        progress: JobProgress | None = None,
        result_ref: str | None = None,
        error: ErrorDetail | None = None,
    ) -> Job:
        """Apply one explicit compare-and-swap lifecycle transition."""

    def recover_interrupted_jobs(self) -> tuple[Job, ...]:
        """Fail every persisted running job during process startup."""


class JobExecutor(Protocol):
    """Executes work outside the job repository transaction."""

    def execute(self, job: Job) -> str:
        """Return an immutable result reference after external work completes."""
