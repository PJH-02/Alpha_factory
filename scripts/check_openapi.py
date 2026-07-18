"""Validate the live OpenAPI surface without constructing writable runtime resources."""

from __future__ import annotations

import importlib
import re
import sys
from collections.abc import Iterator, Mapping
from pathlib import Path
from typing import Any, cast

ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = ROOT / "src"

# These routes are the shared REST command/query boundary.  Keeping the method with each
# path catches an accidental change from a safe query to a state-changing command.
REQUIRED_OPERATIONS = (
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
)
_COMPOSITION_TOKEN = re.compile(r"(?:composition|meta[-_ ]?portfolio)", re.IGNORECASE)
_MANUAL_HOLDOUT_TOKEN = re.compile(
    r"(?:sealed[-_]?evaluation|sealed[-_]?holdout|run[-_]?sealed[-_]?holdout|"
    r"manual[-_]?holdout|holdout[-_/]?(?:approval|approve|consume)|"
    r"(?:approval|approve|consume)[-_]?holdout)",
    re.IGNORECASE,
)


def _load_openapi() -> Mapping[str, Any]:
    if not SOURCE_ROOT.is_dir():
        raise RuntimeError(f"missing source root: {SOURCE_ROOT.relative_to(ROOT).as_posix()}")

    sys.dont_write_bytecode = True
    source_root = str(SOURCE_ROOT)
    if source_root not in sys.path:
        sys.path.insert(0, source_root)

    try:
        module = importlib.import_module("alpha_foundry.interfaces.api")
        create_app = module.create_app
        app = create_app(cast("Any", object()))
        schema = app.openapi()
    except Exception as error:  # Import and schema construction are both contract checks.
        raise RuntimeError(f"could not import and build OpenAPI schema: {error}") from error

    if not isinstance(schema, Mapping):
        raise RuntimeError("FastAPI did not produce an OpenAPI object")
    return schema


def _operation_paths(schema: Mapping[str, Any]) -> Mapping[str, Any]:
    paths = schema.get("paths")
    if not isinstance(paths, Mapping):
        raise RuntimeError("OpenAPI schema has no paths object")
    return paths


def _schema_nodes(value: object, location: str = "components") -> Iterator[tuple[str, str]]:
    if isinstance(value, Mapping):
        for key, nested in value.items():
            if isinstance(key, str):
                yield location, key
                yield from _schema_nodes(nested, f"{location}.{key}")
    elif isinstance(value, list):
        for index, nested in enumerate(value):
            yield from _schema_nodes(nested, f"{location}[{index}]")


def _check_required_operations(paths: Mapping[str, Any], errors: list[str]) -> None:
    for method, path in REQUIRED_OPERATIONS:
        item = paths.get(path)
        if not isinstance(item, Mapping) or method.casefold() not in item:
            errors.append(f"missing required OpenAPI operation: {method} {path}")


def _check_forbidden_routes(paths: Mapping[str, Any], errors: list[str]) -> None:
    for path, item in paths.items():
        if not isinstance(path, str) or not isinstance(item, Mapping):
            errors.append("OpenAPI contains a non-string path or malformed path item")
            continue
        lowered_path = path.casefold()
        methods = tuple(
            method.upper()
            for method in item
            if method.casefold() in {"get", "post", "put", "patch", "delete", "options", "head"}
        )
        if _COMPOSITION_TOKEN.search(path):
            errors.append(f"forbidden composition route: {', '.join(methods) or 'route'} {path}")
        if _MANUAL_HOLDOUT_TOKEN.search(path):
            errors.append(f"forbidden manual-holdout route: {', '.join(methods) or 'route'} {path}")
        if "holdout" in lowered_path and any(method != "GET" for method in methods):
            errors.append(
                f"holdout route must be read-only status only: {', '.join(methods)} {path}"
            )


def _check_forbidden_schema_surface(schema: Mapping[str, Any], errors: list[str]) -> None:
    components = schema.get("components")
    if components is None:
        return
    for location, key in _schema_nodes(components):
        if _COMPOSITION_TOKEN.search(key):
            errors.append(f"forbidden composition schema surface at {location}: {key}")
        if _MANUAL_HOLDOUT_TOKEN.search(key):
            errors.append(f"forbidden manual-holdout schema surface at {location}: {key}")


def main() -> int:
    try:
        schema = _load_openapi()
        paths = _operation_paths(schema)
    except RuntimeError as error:
        print(f"OpenAPI checker could not run: {error}", file=sys.stderr)
        return 2

    errors: list[str] = []
    _check_required_operations(paths, errors)
    _check_forbidden_routes(paths, errors)
    _check_forbidden_schema_surface(schema, errors)

    if errors:
        print("OpenAPI contract check failed:", file=sys.stderr)
        for error in errors:
            print(f"- {error}", file=sys.stderr)
        return 1

    print("OpenAPI routes satisfy the public-boundary contract.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
