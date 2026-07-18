"""OpenAI-compatible adapter and deterministic provider fake.

Credentials remain inside this adapter.  It returns only plain JSON text or a typed,
non-secret failure to the application boundary.
"""

from __future__ import annotations

import json
import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field

import httpx

from ..generation import (
    JsonValue,
    LlmProviderPort,
    ProviderFailure,
    ProviderFailureCode,
    ProviderRequest,
    ProviderResponse,
)

type FakeOutcome = ProviderResponse | ProviderFailure | str | Mapping[str, JsonValue]


@dataclass(slots=True)
class OpenAICompatibleProvider(LlmProviderPort):
    """A provider registered under one snapshot provider name.

    The adapter makes no environment-derived model, URL, or provider fallback.  Its
    caller selects a model exclusively from the pinned ``ProviderRequest``.
    """

    provider: str
    base_url: str
    api_key: str = field(repr=False)
    timeout_seconds: float = 30.0
    client: httpx.Client | None = field(default=None, repr=False)
    _owns_client: bool = field(init=False, repr=False)

    def __post_init__(self) -> None:
        if not self.provider.strip():
            raise ValueError("provider must not be blank")
        if not self.base_url.strip():
            raise ValueError("base_url must not be blank")
        if not self.api_key:
            raise ValueError("api_key must not be blank")
        if (
            isinstance(self.timeout_seconds, bool)
            or not isinstance(self.timeout_seconds, (int, float))
            or not math.isfinite(self.timeout_seconds)
            or self.timeout_seconds <= 0
        ):
            raise ValueError("timeout_seconds must be finite and positive")
        self.base_url = self.base_url.rstrip("/")
        self._owns_client = self.client is None
        if self.client is None:
            self.client = httpx.Client(timeout=self.timeout_seconds)

    def close(self) -> None:
        """Close only an HTTP client created by this adapter."""

        if self._owns_client and self.client is not None:
            self.client.close()

    def complete(self, request: ProviderRequest) -> ProviderResponse:
        """Submit an OpenAI chat-completions JSON request without exposing secrets."""

        selection = request.provider_model
        if selection.provider != self.provider:
            raise ProviderFailure(
                ProviderFailureCode.UNREGISTERED,
                "provider is not registered for this snapshotted chain entry",
                retryable=False,
            )

        body = _openai_request_body(request)
        try:
            assert self.client is not None
            response = self.client.post(
                f"{self.base_url}/chat/completions",
                headers={
                    "Accept": "application/json",
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                },
                json=body,
                timeout=self.timeout_seconds,
            )
        except httpx.TimeoutException as error:
            raise ProviderFailure(
                ProviderFailureCode.TIMEOUT,
                "provider request timed out",
                retryable=True,
            ) from error
        except httpx.HTTPError as error:
            raise ProviderFailure(
                ProviderFailureCode.TRANSPORT,
                "provider transport failed",
                retryable=True,
            ) from error

        if response.status_code == 429:
            raise ProviderFailure(
                ProviderFailureCode.QUOTA,
                "provider quota rejected the request",
                retryable=True,
            )
        if response.status_code in {408, 504}:
            raise ProviderFailure(
                ProviderFailureCode.TIMEOUT,
                "provider request timed out",
                retryable=True,
            )
        if response.status_code >= 500:
            raise ProviderFailure(
                ProviderFailureCode.TRANSPORT,
                "provider service failed",
                retryable=True,
            )
        if response.status_code >= 400:
            raise ProviderFailure(
                ProviderFailureCode.SCHEMA,
                "provider rejected the structured request",
                retryable=False,
            )

        try:
            response_body = response.json()
            content = response_body["choices"][0]["message"]["content"]
        except (IndexError, KeyError, TypeError, ValueError) as error:
            raise ProviderFailure(
                ProviderFailureCode.SCHEMA,
                "provider response did not contain an OpenAI-compatible JSON choice",
                retryable=False,
            ) from error
        if not isinstance(content, str) or not content.strip():
            raise ProviderFailure(
                ProviderFailureCode.SCHEMA,
                "provider response content must be a non-empty JSON string",
                retryable=False,
            )
        return ProviderResponse(content=content, raw_bytes=content.encode("utf-8"))


@dataclass(slots=True)
class FakeProvider(LlmProviderPort):
    """A deterministic scripted provider for focused application tests."""

    outcomes: Sequence[FakeOutcome]
    calls: list[ProviderRequest] = field(default_factory=list, init=False)
    _index: int = field(default=0, init=False, repr=False)

    def __post_init__(self) -> None:
        self.outcomes = tuple(self.outcomes)

    @property
    def call_count(self) -> int:
        return len(self.calls)

    def complete(self, request: ProviderRequest) -> ProviderResponse:
        self.calls.append(request)
        if self._index >= len(self.outcomes):
            raise ProviderFailure(
                ProviderFailureCode.RESPONSE,
                "fake provider has no scripted response for this call",
                retryable=False,
            )
        outcome = self.outcomes[self._index]
        self._index += 1
        if isinstance(outcome, ProviderFailure):
            raise outcome
        if isinstance(outcome, ProviderResponse):
            return outcome
        if isinstance(outcome, str):
            return ProviderResponse(content=outcome, raw_bytes=outcome.encode("utf-8"))
        content = json.dumps(
            _as_json(outcome),
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        return ProviderResponse(content=content, raw_bytes=content.encode("utf-8"))


def _openai_request_body(request: ProviderRequest) -> dict[str, object]:
    """Build a fully explicit OpenAI-compatible request with no hidden defaults."""

    payload_json = json.dumps(
        _as_json(request.payload),
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    body: dict[str, object] = {
        "messages": [
            {
                "content": (
                    "Return only JSON that validates against the supplied schema. "
                    "Never execute code or modify authority state."
                ),
                "role": "system",
            },
            {"content": payload_json, "role": "user"},
        ],
        "model": request.provider_model.model,
        "response_format": {
            "json_schema": {
                "name": "generation_result",
                "schema": _as_json(request.response_schema),
                "strict": True,
            },
            "type": "json_schema",
        },
    }
    sampling = request.payload.get("sampling")
    if isinstance(sampling, Mapping):
        for name in (
            "temperature",
            "top_p",
            "max_tokens",
            "seed",
            "chat_template_kwargs",
            "presence_penalty",
            "frequency_penalty",
        ):
            if name in sampling:
                body[name] = _as_json(sampling[name])
    return body


def _as_json(value: object) -> object:
    if isinstance(value, Mapping):
        return {key: _as_json(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_as_json(item) for item in value]
    return value


__all__ = ["FakeOutcome", "FakeProvider", "OpenAICompatibleProvider"]
