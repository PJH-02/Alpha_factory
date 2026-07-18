"""Two-command CLI for article ingestion and wiki-grounded alpha generation."""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from dataclasses import replace
from pathlib import Path
from uuid import uuid4

from alpha_foundry.bootstrap import ApplicationFacade, BootstrapSettings, bootstrap
from alpha_foundry.domain.errors import DomainError, ErrorCode, ErrorDetail
from alpha_foundry.domain.models import Domain


def main(argv: Sequence[str] | None = None, *, facade: ApplicationFacade | None = None) -> int:
    """Run one of the two user-facing commands and return a documented exit code."""
    parser = _parser()
    raw_arguments = list(sys.argv[1:] if argv is None else argv)
    json_requested = "--json" in raw_arguments
    arguments = parser.parse_args([item for item in raw_arguments if item != "--json"])
    owns_facade = facade is None
    runtime = facade
    correlation_id = str(uuid4())
    serialized_data: str | None = None
    primary_error: Exception | None = None
    try:
        if runtime is None:
            runtime = bootstrap(_settings(arguments))
        serialized_data = _serialize_data(_dispatch(runtime, arguments), json_requested)
    except Exception as error:
        primary_error = error
    finally:
        if owns_facade and runtime is not None:
            try:
                runtime.close()
            except Exception as cleanup_error:
                if primary_error is None:
                    primary_error = cleanup_error
    if primary_error is not None:
        return _write_exception(primary_error, correlation_id, json_requested)
    if serialized_data is None:
        return _write_exception(
            RuntimeError("command completed without serialized output"),
            correlation_id,
            json_requested,
        )
    try:
        sys.stdout.write(serialized_data)
    except (OSError, UnicodeError, ValueError) as error:
        return _write_exception(error, correlation_id, json_requested)
    return 0


def _dispatch(runtime: ApplicationFacade, arguments: argparse.Namespace) -> dict[str, object]:
    models = tuple(arguments.model) if arguments.model else None
    if arguments.command == "article-add":
        return runtime.add_articles(
            arguments.article_root,
            wiki_root=arguments.wiki_root,
            provider_name=arguments.provider,
            models=models,
        )
    return runtime.generate_alpha(
        arguments.request,
        domain=Domain(arguments.domain),
        wiki_root=arguments.wiki_root,
        provider_name=arguments.provider,
        models=models,
        limit=arguments.limit,
        idea_count=arguments.idea_count,
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="alpha-foundry")
    parser.add_argument("--home", type=Path, help="local runtime directory")
    parser.add_argument("--database", type=Path, help="SQLite metadata path")
    parser.add_argument("--artifacts", type=Path, help="local CAS root")
    parser.add_argument("--json", action="store_true", help="emit compact JSON data")
    commands = parser.add_subparsers(dest="command", required=True)

    article = commands.add_parser(
        "article-add",
        help="process markdown under article/<DOMAIN>/ and import domain knowledge packs",
    )
    article.add_argument("article_root", type=Path, nargs="?", default=Path("article"))
    article.add_argument("--wiki-root", type=Path)
    article.add_argument("--provider")
    article.add_argument("--model", action="append")

    alpha = commands.add_parser(
        "alpha-generate",
        help="retrieve the latest domain wiki and save grounded alpha idea pages",
    )
    alpha.add_argument("request")
    alpha.add_argument("--domain", required=True, choices=tuple(domain.value for domain in Domain))
    alpha.add_argument("--wiki-root", type=Path)
    alpha.add_argument("--provider")
    alpha.add_argument("--model", action="append")
    alpha.add_argument("--limit", type=int, default=8)
    alpha.add_argument("--idea-count", type=int, default=3)
    return parser


def _settings(arguments: argparse.Namespace) -> BootstrapSettings:
    settings = BootstrapSettings.from_environment()
    if arguments.home is not None:
        settings = replace(
            settings,
            database_path=arguments.home / "alpha_foundry.sqlite",
            artifact_root=arguments.home / "artifacts",
        )
    if arguments.database is not None:
        settings = replace(settings, database_path=arguments.database)
    if arguments.artifacts is not None:
        settings = replace(settings, artifact_root=arguments.artifacts)
    return settings


def _serialize_data(data: dict[str, object], compact: bool) -> str:
    separators = (",", ":") if compact else None
    return (
        json.dumps(
            data,
            ensure_ascii=False,
            sort_keys=True,
            indent=None if compact else 2,
            separators=separators,
        )
        + "\n"
    )


def _write_exception(error: Exception, correlation_id: str, compact: bool) -> int:
    if isinstance(error, DomainError):
        _write_error(error.detail, correlation_id, compact)
        return _exit_code(error.detail.code)
    _write_error(
        ErrorDetail.for_code(ErrorCode.STORAGE, "unexpected command failure"),
        correlation_id,
        compact,
    )
    return 5


def _write_error(detail: ErrorDetail, correlation_id: str, compact: bool) -> None:
    payload = {
        "error": detail.model_dump(mode="json"),
        "meta": {"correlation_id": correlation_id, "api_version": "1.0"},
    }
    separators = (",", ":") if compact else None
    sys.stderr.write(
        json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            indent=None if compact else 2,
            separators=separators,
        )
        + "\n"
    )


def _exit_code(code: ErrorCode) -> int:
    if code is ErrorCode.SCHEMA:
        return 2
    if code in {ErrorCode.VALIDATION, ErrorCode.PBO_INCOMPLETE}:
        return 3
    if code in {ErrorCode.CAPABILITY, ErrorCode.LLM, ErrorCode.STORAGE, ErrorCode.JOB_INTERRUPTED}:
        return 4
    return 5


if __name__ == "__main__":
    raise SystemExit(main())
