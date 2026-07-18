from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from alpha_foundry.bootstrap import ApplicationFacade, BootstrapSettings, bootstrap
from alpha_foundry.interfaces.api import create_app


def _digest(number: int) -> str:
    return f"sha256:{number:064x}"


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


def test_e2e_rest_operator_search_remains_available_after_cli_simplification(
    facade: ApplicationFacade,
) -> None:
    with TestClient(create_app(facade)) as client:
        response = client.post(
            "/api/v1/search-runs",
            headers={"Idempotency-Key": "rest-search-key"},
            json={"search_spec": _search_spec_payload()},
        )

    assert response.status_code == 202
    assert response.json()["data"]["job"]["status"] == "QUEUED"
