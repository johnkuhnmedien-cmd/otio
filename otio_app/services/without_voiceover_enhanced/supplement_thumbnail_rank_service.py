"""Text-/Thumbnail-Ranking und Finalvergleich für den Enhanced Supplement-Funnel."""

from __future__ import annotations

import json
import logging
import re
import time
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FuturesTimeout
from dataclasses import dataclass, field
from typing import Any, Callable, Sequence
from urllib.parse import urlparse

from otio_app.defaults import resolve_funnel_gemini_model
from otio_app.services.api_keys import is_api_key_set
from otio_app.services.gemini_client import (
    _extract_json,
    _get_client,
)
from otio_app.services.plan_llm_client import (
    PlanLlmCancelledError,
    abort_registered_llm_http,
    cancellable_httpx_client,
    llm_cancel_requested,
)
from otio_app.services.without_voiceover_enhanced.models import (
    CoverageGap,
    FunnelCandidateRecord,
    FunnelTextScores,
    FunnelThumbnailScores,
    StockCandidate,
)
from otio_app.services.without_voiceover_enhanced.stock.safe_fetch import (
    SafeFetchError,
    fetch_preview_image_bytes,
)
from otio_app.services.without_voiceover_enhanced.supplement_funnel_status import (
    transition,
)

logger = logging.getLogger(__name__)

DEFAULT_FUNNEL_MODEL = "gemini-3.5-flash"
THUMBNAIL_BATCH_SIZE = 10
FINALISTS_PER_BATCH = 3
MAX_FINALISTS = 6
# Vision-Batches (bis 10 Bilder) dürfen die UI nicht endlos blockieren.
# google.genai HttpOptions.timeout beendet generate_content oft nicht wirklich
# (Retries auf 408/429/5xx). Der ThreadPool-Timeout gibt die Funnel-Schleife frei.
FUNNEL_GEMINI_TIMEOUT_MS = 120_000
FUNNEL_GEMINI_HARD_TIMEOUT_SEC = FUNNEL_GEMINI_TIMEOUT_MS / 1000.0

TextLlmCallable = Callable[[str], str]
VisionLlmCallable = Callable[[str, list[tuple[str, bytes]]], str]


class FunnelRankError(RuntimeError):
    pass


def compute_preliminary_score(
    *,
    text_relevance: int,
    semantic_fit: int,
    editorial_function_fit: int,
    style_fit: int,
    continuity_fit: int,
    composition_quality: int,
    visual_quality: int,
    misrepresentation_risk: int,
) -> float:
    """Deterministischer vorläufiger Score (0–100)."""
    score = (
        0.20 * text_relevance
        + 0.25 * semantic_fit
        + 0.20 * editorial_function_fit
        + 0.10 * style_fit
        + 0.05 * continuity_fit
        + 0.10 * composition_quality
        + 0.10 * visual_quality
        - 0.20 * misrepresentation_risk
    )
    return float(max(0.0, min(100.0, score)))


def _clamp_score(value: Any) -> int:
    try:
        number = int(round(float(value)))
    except (TypeError, ValueError):
        raise FunnelRankError(f"Score nicht ganzzahlig: {value!r}") from None
    if number < 0 or number > 100:
        raise FunnelRankError(f"Score außerhalb 0–100: {number}")
    return number


def _is_details_page_url(url: str) -> bool:
    text = (url or "").strip().lower()
    if not text:
        return False
    parsed = urlparse(text)
    path = parsed.path or ""
    if "archive.org" in (parsed.netloc or "") and "/details/" in path:
        return True
    if path.endswith((".html", ".htm", ".php")):
        return True
    return False


def _looks_like_direct_image_url(url: str) -> bool:
    path = (urlparse(url).path or "").lower()
    return path.endswith((".jpg", ".jpeg", ".png", ".webp", ".gif"))


def resolve_preview_url(candidate: StockCandidate) -> tuple[str | None, str]:
    """Liefert (preview_url|None, status_reason). Niemals source_page."""
    provider = (candidate.provider or "").strip().lower()
    preview = (candidate.preview_url or "").strip()
    download = (candidate.download_url or "").strip()
    source = (candidate.source_page or "").strip()

    if provider == "archive_org":
        return None, "preview_unavailable"

    if provider == "wikimedia":
        # Nur echte Thumb-URL (thumburl gesetzt vom Adapter); nie Volldatei.
        # Konvention: Adapter speichert Thumb in preview_url nur wenn thumburl
        # vorhanden und != download_url.
        if preview and preview != download and _looks_like_direct_image_url(preview):
            if "/thumb/" in preview or "thumb" in preview.lower():
                return preview, "ok"
            # Begrenzte Thumb-URL oft .../thumb/...
            return preview, "ok"
        return None, "preview_unavailable"

    if not preview:
        return None, "preview_unavailable"
    if source and preview == source:
        return None, "preview_unavailable"
    if _is_details_page_url(preview):
        return None, "preview_unavailable"
    if preview == download and (candidate.media_type or "").lower() == "video":
        # Vollvideo nicht als Preview.
        return None, "preview_unavailable"
    return preview, "ok"


def apply_hard_exclusions(
    candidates: Sequence[StockCandidate],
    *,
    enabled_providers: set[str],
    preferred_media_type: str,
) -> list[tuple[StockCandidate, str | None]]:
    """Deterministische Ausschlussregeln. Weak titles werden NICHT ausgeschlossen."""
    preferred = (preferred_media_type or "").strip().lower()
    seen_provider_ids: set[tuple[str, str]] = set()
    out: list[tuple[StockCandidate, str | None]] = []
    for candidate in candidates:
        cid = (candidate.candidate_id or "").strip()
        provider = (candidate.provider or "").strip().lower()
        asset_id = (candidate.provider_asset_id or "").strip()
        if not cid:
            out.append((candidate, "Keine stabile Kandidaten-ID."))
            continue
        if provider not in enabled_providers:
            out.append((candidate, f"Provider deaktiviert/unerlaubt: {provider}"))
            continue
        if not asset_id:
            out.append((candidate, "provider_asset_id fehlt."))
            continue
        key = (provider, asset_id)
        if key in seen_provider_ids:
            out.append((candidate, "Doppelte Provider-Asset-ID."))
            continue
        seen_provider_ids.add(key)
        media = (candidate.media_type or "").strip().lower()
        if media not in {"photo", "image", "video"}:
            out.append((candidate, f"Medientyp unbrauchbar: {media}"))
            continue
        # Keine endgültige Löschung bei Typ-Mismatch — nur leichte Markierung.
        preview = (candidate.preview_url or "").strip()
        download = (candidate.download_url or "").strip()
        source = (candidate.source_page or "").strip()
        if source and (preview == source or download == source):
            # Source Page als Media-URL — technisch unbrauchbar für Auto-Preview/Download.
            if provider == "archive_org":
                # Archive bleibt als manueller Fallback, nicht hart ausgeschlossen.
                out.append((candidate, None))
                continue
            if not download or download == source:
                out.append((candidate, "Nur Source-Page-URL, kein Medienlink."))
                continue
        _ = preferred  # bewusst nicht hart ausschließen
        out.append((candidate, None))
    return out


_BLOCKED_PROVIDER_STATUSES = frozenset({"disabled", "unavailable", "failed"})


@dataclass(frozen=True)
class BalancedCandidatePool:
    """Provider-balancierter Pool vor Text-/Thumbnail-Ranking."""

    candidates: list[StockCandidate]
    candidate_pool_limit: int = 20
    eligible_providers: list[str] = field(default_factory=list)
    provider_candidate_counts: dict[str, int] = field(default_factory=dict)


def media_type_fits_preferred(media_type: str, preferred_media_type: str) -> bool:
    """Ob ein Kandidat für den gewünschten Medientyp in den Pool darf.

    Soft-Policy: Bei preferred=video bleiben Foto/Image als Still-Fallback
    zulässig (Ranking bevorzugt weiterhin Video). Hart ausgeschlossen wird
    nur unbrauchbarer Medientyp.
    """
    preferred = (preferred_media_type or "").strip().lower()
    media = (media_type or "").strip().lower()
    if preferred in {"video"}:
        return media in {"video", "photo", "image"}
    if preferred in {"photo", "image"}:
        return media in {"photo", "image"}
    # either / leer / sonstige → photo/image/video zulässig
    return media in {"photo", "image", "video"}


def _provider_presort_key(
    candidate: StockCandidate, *, preferred_media_type: str
) -> tuple:
    """Deterministische Vorsortierung innerhalb eines Providers (höher = besser)."""
    preferred = (preferred_media_type or "").strip().lower()
    media = (candidate.media_type or "").strip().lower()
    media_match = 1 if media_type_fits_preferred(media, preferred) else 0
    if preferred and media == preferred:
        media_match = 2
    preview_url, _status = resolve_preview_url(candidate)
    has_preview = 1 if preview_url else 0
    has_resolution = 1 if int(candidate.width or 0) > 0 and int(candidate.height or 0) > 0 else 0
    has_duration = 1 if float(candidate.duration_seconds or 0.0) > 0 else 0
    has_title = 1 if (candidate.title or "").strip() else 0
    return (
        -media_match,
        -has_preview,
        -has_resolution,
        -has_duration,
        -has_title,
        (candidate.provider or "").strip().lower(),
        (candidate.candidate_id or "").strip(),
    )


def select_provider_balanced_candidates(
    candidates: Sequence[StockCandidate],
    *,
    enabled_providers: set[str],
    preferred_media_type: str,
    limit: int = 20,
    provider_status: dict[str, str] | None = None,
) -> BalancedCandidatePool:
    """Fairer 20er-Pool: Grundquote je geeignetem Provider, Rest umverteilen.

    Nach der Poolbildung bleibt das Ranking providerneutral (kein Bonus/Malus).
    """
    pool_limit = max(1, min(20, int(limit)))
    status_map = {
        str(k).strip().lower(): str(v or "").strip().lower()
        for k, v in (provider_status or {}).items()
    }
    enabled = {(p or "").strip().lower() for p in enabled_providers if p}

    # Stabile Eingangsreihenfolge — unabhängig von API-/Dict-Ordnung.
    ordered = sorted(
        candidates,
        key=lambda c: (
            (c.provider or "").lower(),
            (c.provider_asset_id or ""),
            (c.candidate_id or ""),
        ),
    )

    by_provider: dict[str, list[StockCandidate]] = {}
    seen_keys: set[tuple[str, str]] = set()
    for candidate, reason in apply_hard_exclusions(
        ordered,
        enabled_providers=enabled,
        preferred_media_type=preferred_media_type,
    ):
        if reason:
            continue
        provider = (candidate.provider or "").strip().lower()
        if not provider:
            continue
        if status_map.get(provider) in _BLOCKED_PROVIDER_STATUSES:
            continue
        if not media_type_fits_preferred(candidate.media_type, preferred_media_type):
            continue
        key = (provider, (candidate.provider_asset_id or "").strip())
        if key in seen_keys:
            continue
        seen_keys.add(key)
        by_provider.setdefault(provider, []).append(candidate)

    eligible_providers = sorted(
        name for name, items in by_provider.items() if items
    )
    if not eligible_providers:
        return BalancedCandidatePool(
            candidates=[],
            candidate_pool_limit=pool_limit,
            eligible_providers=[],
            provider_candidate_counts={},
        )

    for name in eligible_providers:
        by_provider[name] = sorted(
            by_provider[name],
            key=lambda c: _provider_presort_key(
                c, preferred_media_type=preferred_media_type
            ),
        )

    n_providers = len(eligible_providers)
    base_quota = pool_limit // n_providers
    remainder = pool_limit % n_providers
    quotas = {name: base_quota for name in eligible_providers}
    for name in eligible_providers[:remainder]:
        quotas[name] += 1

    selected: list[StockCandidate] = []
    selected_ids: set[str] = set()
    used_counts: dict[str, int] = {name: 0 for name in eligible_providers}
    cursors: dict[str, int] = {name: 0 for name in eligible_providers}
    unused_slots = 0

    for name in eligible_providers:
        available = by_provider[name]
        take = min(quotas[name], len(available))
        for idx in range(take):
            cand = available[idx]
            selected.append(cand)
            selected_ids.add(cand.candidate_id)
            used_counts[name] += 1
        cursors[name] = take
        unused_slots += quotas[name] - take

    if unused_slots > 0 and len(selected) < pool_limit:
        leftovers: list[tuple[tuple, StockCandidate]] = []
        for name in eligible_providers:
            available = by_provider[name]
            for idx in range(cursors[name], len(available)):
                cand = available[idx]
                if cand.candidate_id in selected_ids:
                    continue
                # Tie-Break: Provider-interner Rang, Providername, candidate_id
                leftovers.append(((idx, name, cand.candidate_id), cand))
        leftovers.sort(key=lambda item: item[0])
        for _key, cand in leftovers:
            if unused_slots <= 0 or len(selected) >= pool_limit:
                break
            selected.append(cand)
            selected_ids.add(cand.candidate_id)
            used_counts[(cand.provider or "").strip().lower()] += 1
            unused_slots -= 1

    selected = selected[:pool_limit]
    counts = {
        name: used_counts[name]
        for name in eligible_providers
        if used_counts[name] > 0
    }
    return BalancedCandidatePool(
        candidates=selected,
        candidate_pool_limit=pool_limit,
        eligible_providers=list(eligible_providers),
        provider_candidate_counts=counts,
    )


def format_provider_distribution(counts: dict[str, int]) -> str:
    """Kompakte Anzeige: ``Pexels 5 · Pixabay 5``."""
    labels = {
        "pexels": "Pexels",
        "pixabay": "Pixabay",
        "wikimedia": "Wikimedia",
        "openverse": "Openverse",
        "archive_org": "Archive.org",
    }
    parts: list[str] = []
    for name in sorted(counts):
        label = labels.get(name, name.replace("_", " ").title())
        parts.append(f"{label} {int(counts[name])}")
    return " · ".join(parts)


def select_funnel_candidates(
    candidates: Sequence[StockCandidate],
    *,
    enabled_providers: set[str],
    preferred_media_type: str,
    limit: int = 20,
    provider_status: dict[str, str] | None = None,
) -> list[StockCandidate]:
    """Provider-balancierter Pool (max. 20) für Text-/Thumbnail-Ranking."""
    pool = select_provider_balanced_candidates(
        candidates,
        enabled_providers=enabled_providers,
        preferred_media_type=preferred_media_type,
        limit=limit,
        provider_status=provider_status,
    )
    return list(pool.candidates)


def validate_text_reviews_payload(
    payload: Any,
    *,
    gap_id: str,
    expected_ids: Sequence[str],
) -> list[FunnelTextScores]:
    if not isinstance(payload, dict):
        raise FunnelRankError("Text-Review ist kein JSON-Objekt.")
    if str(payload.get("gap_id") or "") != gap_id:
        raise FunnelRankError("Text-Review gap_id stimmt nicht.")
    reviews = payload.get("candidate_reviews")
    if not isinstance(reviews, list):
        raise FunnelRankError("candidate_reviews fehlt.")
    expected = list(expected_ids)
    if len(reviews) != len(expected):
        raise FunnelRankError(
            f"Erwarte {len(expected)} Text-Reviews, erhalten {len(reviews)}."
        )
    by_id: dict[str, FunnelTextScores] = {}
    for item in reviews:
        if not isinstance(item, dict):
            raise FunnelRankError("Review-Eintrag ungültig.")
        cid = str(item.get("candidate_id") or "").strip()
        if cid not in expected:
            raise FunnelRankError(f"Unbekannte Kandidaten-ID: {cid}")
        if cid in by_id:
            raise FunnelRankError(f"Doppelte Kandidaten-ID: {cid}")
        by_id[cid] = FunnelTextScores(
            text_relevance=_clamp_score(item.get("text_relevance", 0)),
            metadata_quality=_clamp_score(item.get("metadata_quality", 0)),
            media_type_fit=_clamp_score(item.get("media_type_fit", 0)),
            license_metadata_quality=_clamp_score(
                item.get("license_metadata_quality", 0)
            ),
            misrepresentation_risk=_clamp_score(
                item.get("misrepresentation_risk", 0)
            ),
            reason=str(item.get("reason") or "").strip(),
        )
    missing = [cid for cid in expected if cid not in by_id]
    if missing:
        raise FunnelRankError(f"Fehlende Text-Reviews: {missing}")
    return [by_id[cid] for cid in expected]


def validate_thumbnail_batch_payload(
    payload: Any,
    *,
    expected_ids: Sequence[str],
) -> dict[str, FunnelThumbnailScores]:
    if not isinstance(payload, dict):
        raise FunnelRankError("Thumbnail-Review ist kein JSON-Objekt.")
    reviews = payload.get("candidate_reviews") or payload.get("reviews")
    if not isinstance(reviews, list):
        raise FunnelRankError("Thumbnail candidate_reviews fehlt.")
    expected = set(expected_ids)
    by_id: dict[str, FunnelThumbnailScores] = {}
    for item in reviews:
        if not isinstance(item, dict):
            raise FunnelRankError("Thumbnail-Eintrag ungültig.")
        cid = str(item.get("candidate_id") or "").strip()
        if cid not in expected:
            raise FunnelRankError(f"Unbekannte Thumbnail-ID: {cid}")
        if cid in by_id:
            raise FunnelRankError(f"Doppelte Thumbnail-ID: {cid}")
        by_id[cid] = FunnelThumbnailScores(
            semantic_fit=_clamp_score(item.get("semantic_fit", 0)),
            editorial_function_fit=_clamp_score(
                item.get("editorial_function_fit", 0)
            ),
            style_fit=_clamp_score(item.get("style_fit", 0)),
            continuity_fit=_clamp_score(item.get("continuity_fit", 0)),
            composition_quality=_clamp_score(item.get("composition_quality", 0)),
            visual_quality=_clamp_score(item.get("visual_quality", 0)),
            misrepresentation_risk=_clamp_score(
                item.get("misrepresentation_risk", 0)
            ),
            reason=str(item.get("reason") or "").strip(),
        )
    missing = [cid for cid in expected_ids if cid not in by_id]
    if missing:
        raise FunnelRankError(f"Fehlende Thumbnail-Reviews: {missing}")
    return by_id


def validate_finalists_payload(
    payload: Any,
    *,
    gap_id: str,
    expected_ids: Sequence[str],
) -> list[dict[str, Any]]:
    if not isinstance(payload, dict):
        raise FunnelRankError("Finalvergleich ist kein JSON-Objekt.")
    if str(payload.get("gap_id") or "") != gap_id:
        raise FunnelRankError("Finalvergleich gap_id stimmt nicht.")
    finalists = payload.get("finalists")
    if not isinstance(finalists, list) or not finalists:
        raise FunnelRankError("finalists fehlt.")
    expected = set(expected_ids)
    seen: set[str] = set()
    ranks: set[int] = set()
    winners = 0
    out: list[dict[str, Any]] = []
    for item in finalists:
        if not isinstance(item, dict):
            raise FunnelRankError("Finalist ungültig.")
        cid = str(item.get("candidate_id") or "").strip()
        if cid not in expected:
            raise FunnelRankError(f"Unbekannte Finalisten-ID: {cid}")
        if cid in seen:
            raise FunnelRankError(f"Doppelter Finalist: {cid}")
        seen.add(cid)
        score = _clamp_score(item.get("final_score", 0))
        rank = int(item.get("rank") or 0)
        if rank < 1 or rank in ranks:
            raise FunnelRankError(f"Ungültiger/doppelter Rang: {rank}")
        ranks.add(rank)
        decision = str(item.get("decision") or "").strip()
        if decision not in {"winner", "fallback", "manual_review"}:
            raise FunnelRankError(f"Ungültige decision: {decision}")
        if decision == "winner":
            winners += 1
        out.append(
            {
                "candidate_id": cid,
                "final_score": score,
                "rank": rank,
                "decision": decision,
                "reason": str(item.get("reason") or "").strip(),
            }
        )
    if winners > 1:
        raise FunnelRankError("Mehr als ein Gewinner.")
    return out


def split_thumbnail_batches(
    candidate_ids: Sequence[str],
    *,
    batch_size: int = THUMBNAIL_BATCH_SIZE,
) -> list[list[str]]:
    ids = list(candidate_ids)
    if not ids:
        return []
    size = max(1, min(THUMBNAIL_BATCH_SIZE, int(batch_size)))
    return [ids[i : i + size] for i in range(0, len(ids), size)]


def _finalist_sort_key(record: FunnelCandidateRecord) -> tuple:
    thumb_risk = (
        record.thumbnail_scores.misrepresentation_risk
        if record.thumbnail_scores is not None
        else 100
    )
    text = record.text_scores
    return (
        -float(record.preliminary_score or 0.0),
        int(thumb_risk),
        -int(text.license_metadata_quality if text is not None else 0),
        -int(text.metadata_quality if text is not None else 0),
        record.candidate_id,
    )


def pick_finalists_from_batches(
    records: Sequence[FunnelCandidateRecord],
    *,
    batch_ids: Sequence[Sequence[str]],
    per_batch: int = FINALISTS_PER_BATCH,
) -> list[str]:
    """Wählt Finalisten: zuerst mit Thumb-Score, dann Text-only-Backfill.

    Assets ohne Preview/Thumbnail fallen nicht mehr automatisch raus — sie
    füllen freie Finalisten-Plätze nach Preliminary-/Text-Score auf.
    """
    by_id = {r.candidate_id: r for r in records}
    finalists: list[str] = []

    for batch in batch_ids:
        scored = [
            by_id[cid]
            for cid in batch
            if cid in by_id and by_id[cid].preview_status == "scored"
        ]
        scored.sort(key=_finalist_sort_key)
        for record in scored[:per_batch]:
            if record.candidate_id not in finalists:
                finalists.append(record.candidate_id)
            if len(finalists) >= MAX_FINALISTS:
                return finalists

    if len(finalists) >= MAX_FINALISTS:
        return finalists

    # Backfill: preview unavailable / text-only (bereits vorläufig gescored).
    text_only = [
        record
        for record in records
        if record.candidate_id not in finalists
        and str(record.preview_status or "").strip().lower()
        in {"unavailable", "preview_unavailable", "thumbnail_pending", ""}
    ]
    text_only.sort(key=_finalist_sort_key)
    for record in text_only:
        finalists.append(record.candidate_id)
        if len(finalists) >= MAX_FINALISTS:
            break
    return finalists


def build_text_only_finalist_payload(
    records: Sequence[FunnelCandidateRecord],
) -> list[dict[str, Any]]:
    """Finalisten-Payload ohne Vision — Score aus preliminary/text."""
    ordered = sorted(records, key=_finalist_sort_key)
    payload: list[dict[str, Any]] = []
    for index, record in enumerate(ordered, start=1):
        score = int(round(float(record.preliminary_score or 0.0)))
        score = max(0, min(100, score))
        if score >= 60:
            decision = "winner" if index == 1 else "fallback"
        elif score >= 40:
            decision = "fallback"
        else:
            decision = "manual_review"
        payload.append(
            {
                "candidate_id": record.candidate_id,
                "final_score": score,
                "rank": index,
                "decision": decision,
                "reason": "Text-only Finalist (kein Preview/Thumbnail).",
            }
        )
    return payload


def deterministic_tiebreak_key(record: FunnelCandidateRecord) -> tuple:
    return (
        -(record.final_score or -1),
        record.thumbnail_scores.misrepresentation_risk
        if record.thumbnail_scores
        else 100,
        -(record.text_scores.license_metadata_quality),
        -(record.text_scores.metadata_quality),
        record.candidate_id,
    )


def order_by_final_scores(
    records: Sequence[FunnelCandidateRecord],
    finalist_payload: Sequence[dict[str, Any]],
) -> list[FunnelCandidateRecord]:
    from otio_app.services.without_voiceover_enhanced.fit_bridge import (
        fit_bucket_from_final_score,
    )

    by_id = {r.candidate_id: r for r in records}
    updated: list[FunnelCandidateRecord] = []
    for item in finalist_payload:
        record = by_id[item["candidate_id"]]
        record.final_score = int(item["final_score"])
        record.rank = int(item["rank"])
        record.decision = str(item["decision"])
        record.reason = str(item["reason"])
        record.fit_bucket = fit_bucket_from_final_score(record.final_score)
        # Reject <40: kein Download/export_ready (vor Winner-Logik markieren).
        if record.fit_bucket == "reject":
            record.excluded = True
            record.exclude_reason = (
                f"final_score {record.final_score} < 40 (reject)"
            )
            record.decision = "manual_review"
            suffix = "reject(<40)"
            record.reason = (
                f"{record.reason} · {suffix}" if record.reason else suffix
            )
        try:
            record.funnel_status = transition(record.funnel_status, "finalist")
        except Exception:  # noqa: BLE001 — Ranking-Helper darf Status setzen
            record.funnel_status = "finalist"
        updated.append(record)
    # Deterministische Neuordnung bei Bedarf (Tie-Break).
    updated.sort(key=deterministic_tiebreak_key)
    for index, record in enumerate(updated, start=1):
        record.rank = index
        if record.fit_bucket == "reject":
            record.decision = "manual_review"
            continue
        if index == 1 and record.decision != "manual_review":
            # Genau ein Gewinner wenn geeignet.
            if any(r.decision == "winner" for r in updated):
                record.decision = (
                    "winner" if record.decision == "winner" else record.decision
                )
            elif record.decision != "manual_review":
                record.decision = "winner"
        elif record.decision == "winner" and index != 1:
            record.decision = "fallback"
    # Wenn alle manual_review: keinen Winner erzwingen.
    if all(r.decision == "manual_review" for r in updated):
        for record in updated:
            record.decision = "manual_review"
    elif not any(r.decision == "winner" for r in updated):
        # Ersten nicht-reject als Winner setzen.
        for record in updated:
            if record.fit_bucket != "reject":
                record.decision = "winner"
                break
    # Rejects dürfen nie winner/fallback bleiben.
    for record in updated:
        if record.fit_bucket == "reject":
            record.decision = "manual_review"
    return updated


def _generate_funnel_content(*, client: Any, model: str, contents: Any) -> Any:
    """generate_content mit hartem Timeout — HttpOptions allein reicht nicht."""
    resolved = resolve_funnel_gemini_model(model)

    def _call() -> Any:
        return client.models.generate_content(model=resolved, contents=contents)

    timeout_sec = float(FUNNEL_GEMINI_HARD_TIMEOUT_SEC)
    deadline = time.monotonic() + timeout_sec
    executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="funnel-gemini")
    try:
        future = executor.submit(_call)
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                abort_registered_llm_http()
                raise FunnelRankError(
                    f"Funnel-LLM Timeout nach {int(timeout_sec)}s "
                    f"(Modell {resolved}). Gap wird übersprungen."
                )
            try:
                return future.result(timeout=min(0.25, remaining))
            except FuturesTimeout:
                if llm_cancel_requested():
                    abort_registered_llm_http()
                    raise PlanLlmCancelledError("LLM-Aufruf abgebrochen.")
    finally:
        # wait=False: sonst blockiert shutdown() weiter auf dem hängenden Call.
        executor.shutdown(wait=False, cancel_futures=True)


def default_funnel_text_llm(prompt: str, *, model: str = DEFAULT_FUNNEL_MODEL) -> str:
    if not is_api_key_set("GEMINI_API_KEY"):
        raise FunnelRankError("GEMINI_API_KEY fehlt.")
    from google.genai import types

    with cancellable_httpx_client() as http:
        extra = {}
        if http is not None:
            extra["http_client"] = http
        client = _get_client(timeout_ms=FUNNEL_GEMINI_TIMEOUT_MS, **extra)
        try:
            response = _generate_funnel_content(
                client=client,
                model=model,
                contents=[
                    types.Content(role="user", parts=[types.Part.from_text(text=prompt)])
                ],
            )
        except PlanLlmCancelledError:
            raise
        except FunnelRankError:
            raise
        except Exception as exc:  # noqa: BLE001 — Timeout/API in FunnelRankError
            if llm_cancel_requested():
                raise PlanLlmCancelledError("LLM-Aufruf abgebrochen.") from exc
            raise FunnelRankError(f"Funnel-Text-LLM: {exc}") from exc
        return (response.text or "").strip()


def default_funnel_vision_llm(
    prompt: str,
    images: list[tuple[str, bytes]],
    *,
    model: str = DEFAULT_FUNNEL_MODEL,
) -> str:
    if not is_api_key_set("GEMINI_API_KEY"):
        raise FunnelRankError("GEMINI_API_KEY fehlt.")
    from google.genai import types

    parts: list = [types.Part.from_text(text=prompt)]
    for _label, data in images:
        parts.append(types.Part.from_bytes(data=data, mime_type="image/jpeg"))
    with cancellable_httpx_client() as http:
        extra = {}
        if http is not None:
            extra["http_client"] = http
        client = _get_client(timeout_ms=FUNNEL_GEMINI_TIMEOUT_MS, **extra)
        try:
            response = _generate_funnel_content(
                client=client,
                model=model,
                contents=[types.Content(role="user", parts=parts)],
            )
        except PlanLlmCancelledError:
            raise
        except FunnelRankError:
            raise
        except Exception as exc:  # noqa: BLE001 — Timeout/API in FunnelRankError
            if llm_cancel_requested():
                raise PlanLlmCancelledError("LLM-Aufruf abgebrochen.") from exc
            raise FunnelRankError(f"Funnel-Vision-LLM: {exc}") from exc
        return (response.text or "").strip()


# Kompatibilitäts-Aliase (ältere Imports/Tests).
_default_text_llm = default_funnel_text_llm
_default_vision_llm = default_funnel_vision_llm


def _parse_json_with_repair(
    raw: str,
    *,
    repair_callable: Callable[[str], str] | None,
) -> Any:
    try:
        return _extract_json(raw)
    except (json.JSONDecodeError, ValueError):
        if repair_callable is None:
            raise FunnelRankError("LLM-JSON ungültig.") from None
        repaired = repair_callable(
            "Repariere die folgende Antwort zu gültigem JSON ohne Kommentar:\n" + raw
        )
        try:
            return _extract_json(repaired)
        except (json.JSONDecodeError, ValueError) as exc:
            raise FunnelRankError("LLM-JSON nach Repair ungültig.") from exc


def build_text_rank_prompt(
    *,
    gap: CoverageGap,
    candidates: Sequence[StockCandidate],
    context: dict[str, Any],
) -> str:
    payload = []
    for candidate in candidates:
        payload.append(
            {
                "candidate_id": candidate.candidate_id,
                "provider": candidate.provider,
                "title": candidate.title,
                "description": "",
                "tags": "",
                "media_type": candidate.media_type,
                "width": candidate.width,
                "height": candidate.height,
                "duration_seconds": candidate.duration_seconds,
                "license": candidate.license,
                "attribution": candidate.attribution,
                "creator": candidate.creator,
            }
        )
    return (
        "Bewerte Stock-Kandidaten NUR anhand von Metadaten (keine Medienbytes).\n"
        f"Coverage Gap: {gap.gap_id}\n"
        f"needed_visual: {gap.needed_visual}\n"
        f"editorial_purpose: {gap.editorial_purpose}\n"
        f"must_include: {gap.must_include}\n"
        f"must_avoid: {gap.must_avoid}\n"
        f"preferred_media_type: {gap.preferred_media_type}\n"
        f"Kontext: {json.dumps(context, ensure_ascii=False)}\n"
        f"Kandidaten: {json.dumps(payload, ensure_ascii=False)}\n"
        "Antworte NUR als JSON:\n"
        '{"gap_id":"'
        + gap.gap_id
        + '","candidate_reviews":[{"candidate_id":"...","text_relevance":0,'
        '"metadata_quality":0,"media_type_fit":0,"license_metadata_quality":0,'
        '"misrepresentation_risk":0,"reason":"..."}]}\n'
        "Scores sind ganze Zahlen 0–100. Genau ein Review pro übergebenem Kandidaten."
    )


def run_text_ranking(
    *,
    gap: CoverageGap,
    candidates: Sequence[StockCandidate],
    context: dict[str, Any] | None = None,
    text_llm: TextLlmCallable | None = None,
) -> dict[str, FunnelTextScores]:
    if not candidates:
        return {}
    llm = text_llm or (lambda prompt: _default_text_llm(prompt))
    prompt = build_text_rank_prompt(
        gap=gap, candidates=candidates, context=context or {}
    )
    raw = llm(prompt)
    try:
        payload = _parse_json_with_repair(raw, repair_callable=llm)
        scores_list = validate_text_reviews_payload(
            payload,
            gap_id=gap.gap_id,
            expected_ids=[c.candidate_id for c in candidates],
        )
    except FunnelRankError:
        # Ein gezielter Repair-Versuch ist in _parse_json_with_repair enthalten.
        raise
    return {
        candidate.candidate_id: score
        for candidate, score in zip(candidates, scores_list, strict=True)
    }


def fetch_preview_bytes_for_candidate(
    candidate: StockCandidate,
    *,
    fetch_callable: Callable[..., Any] | None = None,
) -> tuple[bytes | None, str]:
    url, status = resolve_preview_url(candidate)
    if not url:
        return None, status
    try:
        if fetch_callable is not None:
            result = fetch_callable(url, provider=candidate.provider)
            content = result.content if hasattr(result, "content") else result
        else:
            result = fetch_preview_image_bytes(url, provider=candidate.provider)
            content = result.content
        return content, "scored"
    except (SafeFetchError, FunnelRankError, OSError, ValueError) as exc:
        logger.info("Preview fehlgeschlagen für %s: %s", candidate.candidate_id, exc)
        return None, "preview_unavailable"


def run_thumbnail_batch(
    *,
    gap: CoverageGap,
    candidates: Sequence[StockCandidate],
    preview_bytes: dict[str, bytes],
    context: dict[str, Any] | None = None,
    vision_llm: VisionLlmCallable | None = None,
) -> dict[str, FunnelThumbnailScores]:
    if len(candidates) > THUMBNAIL_BATCH_SIZE:
        raise FunnelRankError(
            f"Thumbnail-Batch größer als {THUMBNAIL_BATCH_SIZE}."
        )
    if not candidates:
        return {}
    llm = vision_llm or (
        lambda prompt, images: _default_vision_llm(prompt, images)
    )
    images: list[tuple[str, bytes]] = []
    for candidate in candidates:
        data = preview_bytes.get(candidate.candidate_id)
        if not data:
            raise FunnelRankError(
                f"Previewbytes fehlen für {candidate.candidate_id}"
            )
        images.append((candidate.candidate_id, data))
    prompt = (
        "Bewerte Preview-Thumbnails als Vorauswahl für ein Coverage Gap.\n"
        f"gap_id={gap.gap_id}\n"
        f"needed_visual={gap.needed_visual}\n"
        f"editorial_purpose={gap.editorial_purpose}\n"
        f"must_include={gap.must_include}\n"
        f"must_avoid={gap.must_avoid}\n"
        f"Kontext={json.dumps(context or {}, ensure_ascii=False)}\n"
        "Kandidatenreihenfolge der Bilder: "
        + ", ".join(c.candidate_id for c in candidates)
        + "\n"
        "Nicht nur einzelne Wörter bebildern. Keine Bewegung aus Einzelbildern "
        "behaupten. Keine Orte/Personen erfinden. Thumbnail ist nur Vorauswahl.\n"
        "Antworte NUR JSON:\n"
        '{"candidate_reviews":[{"candidate_id":"...","semantic_fit":0,'
        '"editorial_function_fit":0,"style_fit":0,"continuity_fit":0,'
        '"composition_quality":0,"visual_quality":0,'
        '"misrepresentation_risk":0,"reason":"..."}]}'
    )
    raw = llm(prompt, images)
    payload = _parse_json_with_repair(
        raw,
        # Repair nur als Text — die Preview-Bilder nicht ein zweites Mal senden.
        repair_callable=lambda p: default_funnel_text_llm(p),
    )
    return validate_thumbnail_batch_payload(
        payload, expected_ids=[c.candidate_id for c in candidates]
    )


def run_final_comparison(
    *,
    gap: CoverageGap,
    candidates: Sequence[StockCandidate],
    preview_bytes: dict[str, bytes],
    context: dict[str, Any] | None = None,
    vision_llm: VisionLlmCallable | None = None,
) -> list[dict[str, Any]]:
    if len(candidates) > MAX_FINALISTS:
        raise FunnelRankError(f"Mehr als {MAX_FINALISTS} Finalisten.")
    if not candidates:
        return []
    llm = vision_llm or (
        lambda prompt, images: _default_vision_llm(prompt, images)
    )
    images = []
    for candidate in candidates:
        data = preview_bytes.get(candidate.candidate_id)
        if not data:
            raise FunnelRankError(f"Finalist ohne Preview: {candidate.candidate_id}")
        images.append((candidate.candidate_id, data))
    prompt = (
        "Vergleiche die Finalisten-Thumbnails und erstelle eine Rangliste.\n"
        f"gap_id={gap.gap_id}\n"
        f"needed_visual={gap.needed_visual}\n"
        f"Kontext={json.dumps(context or {}, ensure_ascii=False)}\n"
        "Kandidaten: " + ", ".join(c.candidate_id for c in candidates) + "\n"
        "Genau ein winner wenn mindestens ein Kandidat geeignet ist, "
        "sonst alle manual_review. Ränge eindeutig.\n"
        "Antworte NUR JSON:\n"
        '{"gap_id":"'
        + gap.gap_id
        + '","finalists":[{"candidate_id":"...","final_score":0,"rank":1,'
        '"decision":"winner|fallback|manual_review","reason":"..."}]}'
    )
    raw = llm(prompt, images)
    payload = _parse_json_with_repair(
        raw,
        repair_callable=lambda p: default_funnel_text_llm(p),
    )
    return validate_finalists_payload(
        payload,
        gap_id=gap.gap_id,
        expected_ids=[c.candidate_id for c in candidates],
    )


_HTML_SNIFF = re.compile(rb"<!doctype html|<html", re.I)
