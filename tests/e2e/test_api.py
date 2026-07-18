from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from alpha_foundry.bootstrap import ApplicationFacade, BootstrapSettings, bootstrap
from alpha_foundry.domain.models import Decision, DisclosedMetric, Domain, ResearchReport
from alpha_foundry.infrastructure.artifacts import LocalArtifactStore, StoredArtifact
from alpha_foundry.interfaces.api import create_app
from alpha_foundry.reporting import canonical_research_report_json, render_research_report_html

_TIMESTAMP = "2026-07-12T09:30:00.000000Z"
_NOW = datetime(2026, 7, 12, 9, 30, tzinfo=UTC)


def _digest(number: int) -> str:
    return f"sha256:{number:064x}"


def _knowledge_pack_payload() -> dict[str, object]:
    return {
        "id": "factor-pack",
        "version": "1.0.0",
        "claims": [
            {
                "id": "factor-claim",
                "version": "1.0.0",
                "domain": "FACTOR",
                "statement": "Rank the liquid universe using the reviewed factor signal.",
                "source": "local-fixture",
                "citations": [
                    {
                        "source_id": "fixture-note",
                        "locator": "section-1",
                        "excerpt": "The factor signal is available at the observation time.",
                    }
                ],
            }
        ],
    }


def _search_spec_payload() -> dict[str, object]:
    return {
        "domain": "FACTOR",
        "seed": 17,
        "initial_population_size": 2,
        "offspring_count": 2,
        "parent_pool_size": 1,
        "patience": 1,
        "operator_schedule": ["mutate"],
        "operator_set_hash": _digest(1),
        "universe_hash": _digest(2),
        "profile_hash": _digest(3),
        "dataset_hashes": [_digest(4)],
        "policy_hashes": [_digest(5)],
        "schema_hashes": [_digest(6)],
        "code_hash": _digest(7),
    }


@pytest.fixture
def facade(tmp_path: Path) -> Iterator[ApplicationFacade]:
    runtime = bootstrap(
        BootstrapSettings(
            database_path=tmp_path / "metadata.sqlite",
            artifact_root=tmp_path / "artifacts",
            llm_provider="fake",
        )
    )
    try:
        yield runtime
    finally:
        runtime.close()


@pytest.fixture
def client(facade: ApplicationFacade) -> Iterator[TestClient]:
    with TestClient(create_app(facade)) as test_client:
        yield test_client


def _insert_artifact(
    connection: object,
    *,
    artifact_id: str,
    artifact: StoredArtifact,
) -> None:
    connection.execute(
        """
        INSERT INTO artifacts (
            artifact_id, cas_uri, content_hash, byte_size, media_type, created_at, created_by,
            schema_version, code_version
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            artifact_id,
            artifact.cas_uri,
            artifact.content_hash,
            artifact.byte_size,
            artifact.media_type,
            _TIMESTAMP,
            "local:test",
            "1.0.0",
            "1.0.0",
        ),
    )


def _seed_public_records(facade: ApplicationFacade, tmp_path: Path) -> None:
    report = ResearchReport(
        publication_id="publication-published",
        strategy_id="published-strategy",
        domain=Domain.FACTOR,
        evidence_ids=("validation-published",),
        provider_attempt_ids=("attempt-1",),
        dataset_hashes=(_digest(20),),
        config_hash=_digest(21),
        code_hash=_digest(22),
        schema_hashes=(_digest(23),),
        lineage_id="lineage-published",
        validation_decision=Decision.PASS,
        disclosed_metrics=(
            DisclosedMetric(
                name="sharpe",
                value=Decimal("1.25"),
                threshold=Decimal("1"),
                decision=Decision.PASS,
            ),
        ),
        artifact_hashes=(_digest(24),),
        created_at=_NOW,
    )
    artifacts = LocalArtifactStore(tmp_path / "artifacts")
    report_json = artifacts.put_bytes(canonical_research_report_json(report), "application/json")
    report_html = artifacts.put_bytes(render_research_report_html(report), "text/html")
    replay_artifact = artifacts.put_bytes(b'{"answer":"replayed"}', "application/json")

    with facade._store.transaction(immediate=True) as connection:
        connection.executemany(
            """
            INSERT INTO lineages (lineage_id, status, closed_at, close_reason, root_refs_json, created_at)
            VALUES (?, 'CLOSED', ?, 'TEST_SEED', '{}', ?)
            """,
            (
                ("lineage-published", _TIMESTAMP, _TIMESTAMP),
                ("lineage-draft", _TIMESTAMP, _TIMESTAMP),
            ),
        )
        published_profile_hash = _digest(31)
        connection.execute(
            """
            INSERT INTO search_runs (
                search_run_id, lineage_id, search_spec_hash, universe_hash, profile_hash,
                state, stop_reason, best_candidate_hash, plateau_counter,
                created_at, updated_at, row_version
            ) VALUES (?, ?, ?, ?, ?, 'SUCCEEDED', 'PLATEAU', ?, 0, ?, ?, 1)
            """,
            (
                "search-run-published",
                "lineage-published",
                _digest(26),
                _digest(27),
                published_profile_hash,
                _digest(30),
                _TIMESTAMP,
                _TIMESTAMP,
            ),
        )
        connection.execute(
            """
            INSERT INTO pbo_certificates (
                pbo_certificate_id, search_run_id, profile_hash, started_trial_ids_json,
                terminal_trial_ids_json, eligible_trial_ids_json, matrix_trial_ids_json,
                expected_fold_ids_json, observed_fold_ids_json, started_count, terminal_count,
                eligible_count, matrix_count, artifact_id, decision, reason_code, created_at,
                created_by, schema_version, code_version
            ) VALUES (?, ?, ?, '["trial-1"]', '["trial-1"]', '["trial-1"]', '["trial-1"]',
                '["fold-1"]', '["fold-1"]', 1, 1, 1, 1, NULL, 'PASS', NULL, ?, ?, ?, ?)
            """,
            (
                "pbo-published",
                "search-run-published",
                published_profile_hash,
                _TIMESTAMP,
                "local:test",
                "1.0.0",
                "1.0.0",
            ),
        )
        connection.executemany(
            """
            INSERT INTO validations (
                validation_id, strategy_id, candidate_hash, lineage_id, profile_hash,
                pre_validation_decision, gate_summary_json, fold_summary_json, pbo_certificate_id,
                content_hash, created_at, created_by, schema_version, code_version
            ) VALUES (?, ?, ?, ?, ?, 'PASS', '{}', '{}', ?, ?, ?, ?, ?, ?)
            """,
            (
                (
                    "validation-published",
                    "published-strategy",
                    _digest(30),
                    "lineage-published",
                    published_profile_hash,
                    "pbo-published",
                    _digest(32),
                    _TIMESTAMP,
                    "local:test",
                    "1.0.0",
                    "1.0.0",
                ),
                (
                    "validation-draft",
                    "draft-strategy",
                    _digest(33),
                    "lineage-draft",
                    _digest(34),
                    None,
                    _digest(35),
                    _TIMESTAMP,
                    "local:test",
                    "1.0.0",
                    "1.0.0",
                ),
            ),
        )
        connection.execute(
            """
            INSERT INTO holdout_slots (
                lineage_id, profile_hash, state, consumed_at, consuming_validation_id,
                selector_hash, row_version
            ) VALUES (?, ?, 'CONSUMED', ?, ?, ?, 1)
            """,
            (
                "lineage-published",
                published_profile_hash,
                _TIMESTAMP,
                "validation-published",
                _digest(28),
            ),
        )
        connection.executemany(
            """
            INSERT INTO publications (
                publication_id, validation_id, strategy_id, candidate_hash, lineage_id, profile_hash,
                registry_entry_id, state, owner_token, owner_job_id, owner_epoch, owner_expires_at,
                attempt_count, prepared_orphan_hashes_json, failure_kind, created_at, updated_at,
                published_at, row_version
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, NULL, NULL, 0, NULL, 0, NULL, NULL, ?, ?, ?, 1)
            """,
            (
                (
                    "publication-published",
                    "validation-published",
                    "published-strategy",
                    _digest(30),
                    "lineage-published",
                    published_profile_hash,
                    "registry-published",
                    "AVAILABLE",
                    _TIMESTAMP,
                    _TIMESTAMP,
                    None,
                ),
                (
                    "publication-draft",
                    "validation-draft",
                    "draft-strategy",
                    _digest(33),
                    "lineage-draft",
                    _digest(34),
                    "registry-draft",
                    "AVAILABLE",
                    _TIMESTAMP,
                    _TIMESTAMP,
                    None,
                ),
            ),
        )
        connection.executemany(
            """
            INSERT INTO registry_entries (
                registry_entry_id, publication_id, validation_id, strategy_id, candidate_hash,
                lineage_id, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                (
                    "registry-published",
                    "publication-published",
                    "validation-published",
                    "published-strategy",
                    _digest(30),
                    "lineage-published",
                    _TIMESTAMP,
                ),
                (
                    "registry-draft",
                    "publication-draft",
                    "validation-draft",
                    "draft-strategy",
                    _digest(33),
                    "lineage-draft",
                    _TIMESTAMP,
                ),
            ),
        )
        _insert_artifact(connection, artifact_id="report-json", artifact=report_json)
        _insert_artifact(connection, artifact_id="report-html", artifact=report_html)
        _insert_artifact(connection, artifact_id="replay-artifact", artifact=replay_artifact)
        connection.executemany(
            """
            INSERT INTO artifact_links (artifact_link_id, artifact_id, owner_type, owner_id, role, created_at)
            VALUES (?, ?, 'PUBLICATION', 'publication-published', ?, ?)
            """,
            (
                ("report-json-link", "report-json", "RESEARCH_REPORT_JSON", _TIMESTAMP),
                ("report-html-link", "report-html", "RESEARCH_REPORT_HTML", _TIMESTAMP),
            ),
        )
        connection.execute(
            """
            UPDATE publications
            SET state = 'PUBLISHED', updated_at = ?, published_at = ?, row_version = row_version + 1
            WHERE publication_id = ? AND state = 'AVAILABLE'
            """,
            (_TIMESTAMP, _TIMESTAMP, "publication-published"),
        )
        connection.execute(
            """
            INSERT INTO generation_requests (
                generation_request_id, request_hash, capability_snapshot_hash, knowledge_pack_hash,
                claim_pins_json, provider_chain_json, request_json, state, owner_token, owner_job_id,
                lease_epoch, lease_expires_at, next_ordinal, accepted_artifact_id, created_at,
                updated_at, created_by, schema_version, code_version, row_version
            ) VALUES (?, ?, ?, ?, '{}', '{}', '{}', 'ACCEPTED', NULL, NULL, 0, NULL, 1, ?, ?, ?, ?, ?, ?, 1)
            """,
            (
                "generation-accepted",
                _digest(40),
                _digest(41),
                _digest(42),
                "replay-artifact",
                _TIMESTAMP,
                _TIMESTAMP,
                "local:test",
                "1.0.0",
                "1.0.0",
            ),
        )
        connection.execute(
            """
            INSERT INTO rejection_registry (
                rejection_id, lineage_id, candidate_hash, strategy_id, validation_id, terminal_stage,
                reason_code, redacted_summary, artifact_id, evidence_hash, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, NULL, ?, ?)
            """,
            (
                "rejection-internal",
                "lineage-draft",
                _digest(50),
                "rejected-strategy",
                "validation-draft",
                "VALIDATION",
                "VALIDATION_NOT_PASS",
                "Validation did not meet the public release gate.",
                _digest(51),
                _TIMESTAMP,
            ),
        )


def test_e2e_api_health_knowledge_search_and_job_surfaces(client: TestClient) -> None:
    correlation_id = "d2719b30-3980-4c68-95d0-2a4b65e2c5ad"

    health = client.get("/api/v1/health", headers={"X-Correlation-ID": correlation_id})

    assert health.status_code == 200
    assert health.headers["X-Correlation-ID"] == correlation_id
    assert health.json()["data"] == {"status": "ok", "storage": "ok"}

    created_pack = client.post(
        "/api/v1/knowledge/packs",
        headers={"Idempotency-Key": "knowledge-key"},
        json=_knowledge_pack_payload(),
    )

    assert created_pack.status_code == 201
    saved_pack = created_pack.json()["data"]["knowledge_pack"]
    shown_pack = client.get("/api/v1/knowledge/packs/factor-pack/versions/1.0.0")
    assert shown_pack.status_code == 200
    assert shown_pack.json()["data"]["knowledge_pack"] == saved_pack

    submitted = client.post(
        "/api/v1/search-runs",
        headers={"Idempotency-Key": "search-key"},
        json={"search_spec": _search_spec_payload()},
    )

    assert submitted.status_code == 202
    submission = submitted.json()["data"]
    assert submission["job"]["job_id"]
    assert submission["job"]["kind"] == "RUN_SEARCH"
    assert submission["job"]["status"] == "QUEUED"
    assert "command_fingerprint" not in submission
    assert "command_fingerprint" not in submission["job"]

    shown_job = client.get(f"/api/v1/jobs/{submission['job']['job_id']}")
    assert shown_job.status_code == 200
    assert shown_job.json()["data"]["job"] == submission["job"]

    cancelled = client.post(
        f"/api/v1/jobs/{submission['job']['job_id']}/cancel",
        headers={"Idempotency-Key": "cancel-key"},
    )
    assert cancelled.status_code == 202
    assert cancelled.json()["data"]["job"]["status"] == "CANCELLED"


def test_e2e_api_public_replay_report_and_internal_rejection_surfaces(
    client: TestClient,
    facade: ApplicationFacade,
    tmp_path: Path,
) -> None:
    _seed_public_records(facade, tmp_path)

    replay = client.post("/api/v1/generation/requests/generation-accepted/replay")
    assert replay.status_code == 200
    replay_data = replay.json()["data"]
    assert replay_data["artifact_hash"].startswith("sha256:")
    assert replay_data == {
        "generation_request_id": "generation-accepted",
        "request_hash": _digest(40),
        "state": "ACCEPTED",
        "artifact_hash": replay_data["artifact_hash"],
    }

    strategies = client.get("/api/v1/registry/strategies")
    assert strategies.status_code == 200
    assert [strategy["strategy_id"] for strategy in strategies.json()["data"]["strategies"]] == [
        "published-strategy"
    ]

    strategy = client.get("/api/v1/registry/strategies/published-strategy")
    report = client.get("/api/v1/reports/publication-published")
    html_report = client.get("/api/v1/reports/publication-published/html")
    assert strategy.status_code == report.status_code == html_report.status_code == 200
    assert strategy.json()["data"]["report"] == report.json()["data"]["report"]
    assert report.json()["data"]["report"]["publication_id"] == "publication-published"
    assert html_report.text.startswith("<!doctype html>\n")

    draft = client.get("/api/v1/registry/strategies/draft-strategy")
    assert draft.status_code == 404
    assert draft.json()["error"]["code"] == "AF-RESOURCE-001"

    rejections = client.get("/api/v1/internal/rejections", params={"reason": "VALIDATION_NOT_PASS"})
    assert rejections.status_code == 200
    assert rejections.json()["data"]["rejections"] == [
        {
            "rejection_id": "rejection-internal",
            "terminal_stage": "VALIDATION",
            "reason_code": "VALIDATION_NOT_PASS",
            "redacted_summary": "Validation did not meet the public release gate.",
            "created_at": _TIMESTAMP,
        }
    ]
    rejection = client.get("/api/v1/internal/rejections/rejection-internal")
    assert rejection.status_code == 200
    assert rejection.json()["data"]["rejection"] == rejections.json()["data"]["rejections"][0]


def test_e2e_api_has_no_composition_manual_holdout_or_public_rejection_routes(
    client: TestClient,
) -> None:
    paths = client.app.openapi()["paths"]

    assert not [path for path in paths if "composition" in path.casefold()]
    assert not [path for path in paths if "holdout" in path.casefold()]
    assert not [
        path for path in paths if "rejection" in path.casefold() and "/internal/" not in path
    ]

    for method, path in (
        ("post", "/api/v1/compositions"),
        ("post", "/api/v1/holdout-slots/lineage-1/evaluate"),
        ("get", "/api/v1/rejections/rejection-internal"),
    ):
        response = getattr(client, method)(path)
        assert response.status_code == 404
