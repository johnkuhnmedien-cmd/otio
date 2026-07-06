"""Basis-Schnittstelle für Supplement-Quellen."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path

from otio_app.analysis_models import SupplementAssetSidecar, SupplementCandidate, SupplementRequest


@dataclass(frozen=True)
class SupplementAsset:
    local_path: Path
    sidecar: SupplementAssetSidecar


class SupplementSourceAdapter(ABC):
    provider: str

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
