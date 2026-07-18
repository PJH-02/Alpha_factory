"""Thin application composition root with explicit domain commands and results."""

from __future__ import annotations

from alpha_foundry.application.generation import GenerationCommand, GenerationService
from alpha_foundry.application.publication import (
    PublicationCommand,
    PublicationResult,
    PublicationService,
)
from alpha_foundry.generation import GenerationResult
from alpha_foundry.search.models import SearchRunResult
from alpha_foundry.search.runner import SearchRunner
from alpha_foundry.validation.holdout import HoldoutOutcome, HoldoutRequest, HoldoutService


class ApplicationOrchestrator:
    """Delegate to existing services without translating or weakening their contracts."""

    def __init__(
        self,
        *,
        generation_service: GenerationService,
        holdout_service: HoldoutService,
        publication_service: PublicationService,
    ) -> None:
        self._generation_service = generation_service
        self._holdout_service = holdout_service
        self._publication_service = publication_service

    def generate(self, command: GenerationCommand) -> GenerationResult:
        """Run the durable generation command unchanged."""
        return self._generation_service.generate(command)

    def run_search(self, runner: SearchRunner) -> SearchRunResult:
        """Run an already-pinned, single-domain search unchanged."""
        return runner.run()

    def run_holdout(self, request: HoldoutRequest) -> HoldoutOutcome:
        """Run the automatic one-shot holdout using its explicit request contract."""
        return self._holdout_service.run(request=request)

    def publish(self, command: PublicationCommand) -> PublicationResult:
        """Stage and atomically publish an explicit complete-pass command."""
        return self._publication_service.publish(command)


__all__ = ["ApplicationOrchestrator"]
