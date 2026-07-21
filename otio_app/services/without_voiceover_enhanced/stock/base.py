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
    # Archive.org u. a. liefern creator/title oft als Liste.
    if isinstance(value, (list, tuple)):
        parts = [str(part).strip() for part in value if part is not None and str(part).strip()]
        text = ", ".join(parts)
    else:
        text = str(value).strip()
    if not text or text.lower() in {"unknown", "n/a", "none"}:
        return None
    return text


def optional_text(value: object, default: str = "") -> str:
    """Wie unknown_or_null, aber immer str (für Pflichtfelder wie creator)."""
    return unknown_or_null(value) or default
