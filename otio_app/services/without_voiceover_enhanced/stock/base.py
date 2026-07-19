"""Gemeinsame Stock-Suchschnittstelle (MVP)."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Optional

from otio_app.services.without_voiceover_enhanced.models import StockCandidate


@dataclass
class ProviderStatus:
    provider_name: str
    status: str  # ready | unavailable | error
    message: str = ""


class StockProvider(ABC):
    provider_name: str

    @abstractmethod
    def readiness(self) -> ProviderStatus:
        raise NotImplementedError

    @abstractmethod
    def search(
        self,
        query: str,
        media_type: str | None = None,
    ) -> list[StockCandidate]:
        raise NotImplementedError


def unknown_or_null(value: object) -> Optional[str]:
    if value is None:
        return None
    text = str(value).strip()
    if not text or text.lower() in {"unknown", "n/a", "none"}:
        return None
    return text
