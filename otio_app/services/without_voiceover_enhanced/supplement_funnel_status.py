"""Zentrale Validierung der Funnel-Statusübergänge."""

from __future__ import annotations

from otio_app.services.without_voiceover_enhanced.models import FUNNEL_STATUSES

ALLOWED_TRANSITIONS: dict[str, frozenset[str]] = {
    "discovered": frozenset(
        {
            "text_ranked",
            "thumbnail_unavailable",
            "download_failed",
        }
    ),
    "text_ranked": frozenset(
        {
            "thumbnail_pending",
            "thumbnail_unavailable",
            "thumbnail_scored",
        }
    ),
    "thumbnail_pending": frozenset(
        {
            "thumbnail_scored",
            "thumbnail_unavailable",
        }
    ),
    "thumbnail_unavailable": frozenset(
        {
            "finalist",
            "download_pending",
            # Historisch lesbar:
            "manual_review_required",
        }
    ),
    "thumbnail_scored": frozenset(
        {
            "finalist",
            "download_pending",
            "manual_review_required",
        }
    ),
    "finalist": frozenset(
        {
            "download_pending",
            "manual_review_required",
        }
    ),
    "download_pending": frozenset(
        {
            "download_failed",
            "local_media_invalid",
            "technically_valid",
        }
    ),
    "download_failed": frozenset(set()),
    "local_media_invalid": frozenset(set()),
    "technically_valid": frozenset(
        {
            "selected",
            "license_metadata_incomplete",
            # Historisch (R1 Full-Review-Pfad):
            "full_review_rejected",
            "manual_review_required",
            "review_ready",
        }
    ),
    "license_metadata_incomplete": frozenset(set()),
    "full_review_rejected": frozenset(set()),
    "manual_review_required": frozenset(
        {
            "selected",
            "full_review_rejected",
            "review_ready",
        }
    ),
    "review_ready": frozenset(
        {
            "selected",
            "license_review_required",
            "full_review_rejected",
        }
    ),
    "selected": frozenset(
        {
            "license_review_required",
            "license_metadata_incomplete",
            "export_ready",
        }
    ),
    "license_review_required": frozenset(
        {
            "export_ready",
            "selected",
        }
    ),
    "export_ready": frozenset(set()),
}


class FunnelStatusError(ValueError):
    pass


def assert_known_status(status: str) -> str:
    value = (status or "").strip()
    if value not in FUNNEL_STATUSES:
        raise FunnelStatusError(f"Unbekannter Funnel-Status: {status}")
    return value


def can_transition(current: str, nxt: str) -> bool:
    cur = assert_known_status(current)
    nxt_s = assert_known_status(nxt)
    if cur == nxt_s:
        return True
    return nxt_s in ALLOWED_TRANSITIONS.get(cur, frozenset())


def transition(current: str, nxt: str) -> str:
    if not can_transition(current, nxt):
        raise FunnelStatusError(f"Ungültiger Statusübergang: {current} → {nxt}")
    return assert_known_status(nxt)
