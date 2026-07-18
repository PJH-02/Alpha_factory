"""OpenAPI surface contract for the implemented REST adapter."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path

from alpha_foundry.bootstrap import BootstrapSettings, bootstrap
from alpha_foundry.interfaces.api import create_app

_REQUIRED_OPERATIONS = frozenset(
    {
        ("GET", "/api/v1/health"),
        ("POST", "/api/v1/knowledge/packs"),
        ("GET", "/api/v1/knowledge/packs/{pack_id}/versions/{version}"),
        ("POST", "/api/v1/mandates"),
        ("GET", "/api/v1/mandates/{mandate_id}"),
        ("POST", "/api/v1/mandates/{mandate_id}/run"),
        ("POST", "/api/v1/search-runs"),
        ("GET", "/api/v1/search-runs/{search_run_id}"),
        ("GET", "/api/v1/jobs/{job_id}"),
        ("POST", "/api/v1/jobs/{job_id}/cancel"),
        ("POST", "/api/v1/jobs/{job_id}/resubmit"),
        ("POST", "/api/v1/generation/requests/{generation_request_id}/replay"),
        ("GET", "/api/v1/registry/strategies"),
        ("GET", "/api/v1/registry/strategies/{strategy_id}"),
        ("GET", "/api/v1/reports/{publication_id}"),
        ("GET", "/api/v1/reports/{publication_id}/html"),
        ("GET", "/api/v1/internal/rejections"),
        ("GET", "/api/v1/internal/rejections/{rejection_id}"),
    }
)
_FORBIDDEN_PATHS = frozenset(
    {
        "/api/v1/compositions",
        "/api/v1/compositions/{composition_id}",
        "/api/v1/portfolios",
        "/api/v1/portfolios/{portfolio_id}",
        "/api/v1/publications",
        "/api/v1/experiments/{experiment_id}/sealed-evaluation",
        "/api/v1/holdout-slots/{lineage_id}/consume",
    }
)
_MUTATION_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})


def _facade(tmp_path: Path):
    return bootstrap(
        BootstrapSettings(
            database_path=tmp_path / "metadata.sqlite",
            artifact_root=tmp_path / "artifacts",
            llm_provider="fake",
        )
    )


def _operations(schema: Mapping[str, object]) -> set[tuple[str, str]]:
    paths = schema["paths"]
    assert isinstance(paths, Mapping)
    return {
        (method.upper(), path)
        for path, path_item in paths.items()
        if isinstance(path, str) and isinstance(path_item, Mapping)
        for method in path_item
        if method.upper() in {"GET", "POST", "PUT", "PATCH", "DELETE"}
    }


def _parameter_names(schema: Mapping[str, object], path: str, method: str) -> set[str]:
    paths = schema["paths"]
    assert isinstance(paths, Mapping)
    path_item = paths[path]
    assert isinstance(path_item, Mapping)
    operation = path_item[method.lower()]
    assert isinstance(operation, Mapping)
    parameters = operation.get("parameters", [])
    assert isinstance(parameters, list)
    return {
        parameter["name"]
        for parameter in parameters
        if isinstance(parameter, Mapping) and isinstance(parameter.get("name"), str)
    }


def test_openapi_exposes_only_the_implemented_public_and_internal_routes(tmp_path: Path) -> None:
    facade = _facade(tmp_path)
    try:
        schema = create_app(facade).openapi()
    finally:
        facade.close()

    assert _operations(schema) >= _REQUIRED_OPERATIONS
    paths = schema["paths"]
    assert isinstance(paths, Mapping)
    assert not _FORBIDDEN_PATHS & set(paths)

    for method, path in _operations(schema):
        normalized_path = path.lower()
        assert "composition" not in normalized_path
        assert "portfolio" not in normalized_path
        assert not (
            method in _MUTATION_METHODS
            and ("holdout" in normalized_path or "sealed" in normalized_path)
        )


def test_published_registry_route_has_no_caller_controlled_visibility_filter(
    tmp_path: Path,
) -> None:
    facade = _facade(tmp_path)
    try:
        schema = create_app(facade).openapi()
    finally:
        facade.close()

    parameter_names = _parameter_names(schema, "/api/v1/registry/strategies", "GET")

    assert {"state", "status", "publication_state"}.isdisjoint(parameter_names)
