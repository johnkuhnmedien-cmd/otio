"""Basis-Schnittstelle für Supplement-Quellen."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path

from otio_app.analysis_models import SupplementAssetSidecar, SupplementCandidate, SupplementRequest
from otio_app.defaults import PROVIDER_STATUS_MOCK


@dataclass(frozen=True)
class SupplementAsset:
    local_path: Path
    sidecar: SupplementAssetSidecar


@dataclass(frozen=True)
class ProviderReadiness:
    provider: str
    status: str
    message: str = ""
    search_enabled: bool = True
    acquire_enabled: bool = False
    generate_enabled: bool = False
    is_mock: bool = False


class SupplementSourceAdapter(ABC):
    provider: str

    def readiness(self) -> ProviderReadiness:
        return ProviderReadiness(
            provider=self.provider,
            status=PROVIDER_STATUS_MOCK,
            message="Provider ist noch nicht produktiv angebunden.",
            search_enabled=True,
            is_mock=True,
        )

    @abstractmethod
    def search(self, request: SupplementRequest) -> list[SupplementCandidate]:
        raise NotImplementedError

    def acquire(
        self,
        candidate: SupplementCandidate,
        destination_folder: Path,
    ) -> SupplementAsset:
        raise NotImplementedError(
            f"{self.provider} unterstützt acquire() nicht — bitte generate() nutzen."
        )

    def generate(
        self,
        request: SupplementRequest,
        destination_folder: Path,
    ) -> SupplementAsset:
        raise NotImplementedError(
            f"{self.provider} unterstützt generate() nicht."
        )
