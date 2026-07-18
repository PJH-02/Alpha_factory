"""Contract tests for structured, redacted API observability events."""

from __future__ import annotations

import json
import logging
from collections.abc import Mapping
from pathlib import Path

from fastapi.testclient import TestClient

from alpha_foundry.bootstrap import BootstrapSettings, bootstrap
from alpha_foundry.interfaces.api import create_app

_REQUIRED_EVENT_FIELDS = frozenset(
    {
        "correlation_id",
        "job_id",
        "request_hash",
        "attempt_ordinal",
        "search_run_id",
        "generation_index",
        "slot_index",
        "candidate_trial_id",
        "publication_id",
        "stage",
        "duration_ms",
        "error_code",
    }
)


def _facade(tmp_path: Path):
    return bootstrap(
        BootstrapSettings(
            database_path=tmp_path / "metadata.sqlite",
            artifact_root=tmp_path / "artifacts",
            llm_provider="fake",
        )
    )


def _event_fields(record: logging.LogRecord) -> dict[str, object]:
    if isinstance(record.msg, Mapping):
        return dict(record.msg)
    if isinstance(record.msg, str):
        try:
            decoded = json.loads(record.getMessage())
        except json.JSONDecodeError:
            decoded = None
        if isinstance(decoded, Mapping):
            return dict(decoded)
    return {
        field_name: record.__dict__[field_name]
        for field_name in _REQUIRED_EVENT_FIELDS
        if field_name in record.__dict__
    }


def _record_text(record: logging.LogRecord) -> str:
    return f"{record.getMessage()}\n{record.__dict__!r}"


def test_api_observability_events_are_structured_correlated_and_redacted(
    caplog, tmp_path: Path
) -> None:
    correlation_id = "11111111-1111-1111-1111-111111111111"
    api_secret = "release-observability-api-secret"
    sealed_selector = "sealed://holdout/factor/row-17"
    facade = _facade(tmp_path)
    caplog.set_level(logging.INFO, logger="alpha_foundry")
    try:
        with TestClient(create_app(facade)) as client:
            caplog.clear()
            response = client.get(
                "/api/v1/health",
                headers={
                    "Authorization": f"Bearer {api_secret}",
                    "X-Correlation-ID": correlation_id,
                    "X-Sealed-Selector": sealed_selector,
                },
            )

        assert response.status_code == 200
        assert response.headers["X-Correlation-ID"] == correlation_id
        assert response.json()["meta"]["correlation_id"] == correlation_id

        records = [record for record in caplog.records if record.name.startswith("alpha_foundry")]
        assert records, "API requests must emit a first-party structured observability event"
        for record in records:
            fields = _event_fields(record)
            assert fields.keys() >= _REQUIRED_EVENT_FIELDS
            assert fields["correlation_id"] == correlation_id
            rendered = _record_text(record)
            assert api_secret not in rendered
            assert sealed_selector not in rendered
    finally:
        facade.close()
