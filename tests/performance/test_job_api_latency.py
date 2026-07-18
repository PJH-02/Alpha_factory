"""Release latency evidence for REST job creation and retrieval without an engine worker."""

from __future__ import annotations

from pathlib import Path
from time import perf_counter

import pytest
from fastapi.testclient import TestClient

from alpha_foundry.bootstrap import BootstrapSettings, bootstrap
from alpha_foundry.interfaces.api import create_app

pytestmark = pytest.mark.release

_MAX_LOCAL_API_LATENCY_SECONDS = 1.0


def _facade(tmp_path: Path):
    return bootstrap(
        BootstrapSettings(
            database_path=tmp_path / "metadata.sqlite",
            artifact_root=tmp_path / "artifacts",
            llm_provider="fake",
        )
    )


def _mandate() -> dict[str, object]:
    return {
        "mandate_id": "factor-latency-mandate",
        "version": "1.0.0",
        "mode": "QUESTION",
        "domain": "FACTOR",
        "objective": "Measure local job API latency without executing an engine.",
        "data_requirements": ["synthetic-factor-panel"],
        "budget": {"max_candidates": 1},
    }


def test_release_job_create_and_show_stay_within_local_latency_budget(tmp_path: Path) -> None:
    facade = _facade(tmp_path)
    try:
        with TestClient(create_app(facade)) as client:
            created_mandate = client.post(
                "/api/v1/mandates",
                headers={"Idempotency-Key": "create-factor-latency-mandate"},
                json=_mandate(),
            )
            assert created_mandate.status_code == 201

            started = perf_counter()
            queued = client.post(
                "/api/v1/mandates/factor-latency-mandate/run",
                headers={"Idempotency-Key": "run-factor-latency-mandate"},
            )
            create_elapsed_seconds = perf_counter() - started

            assert queued.status_code == 202
            job = queued.json()["data"]["job"]
            assert job["status"] == "QUEUED"

            started = perf_counter()
            shown = client.get(f"/api/v1/jobs/{job['job_id']}")
            show_elapsed_seconds = perf_counter() - started

        assert shown.status_code == 200
        assert shown.json()["data"]["job"] == job
        assert create_elapsed_seconds <= _MAX_LOCAL_API_LATENCY_SECONDS
        assert show_elapsed_seconds <= _MAX_LOCAL_API_LATENCY_SECONDS
    finally:
        facade.close()
