"""Gemeinsame Fit-Skala lokal ↔ Funnel (final_score → Bucket).

Einzige Stelle für Schwellwerte (PLAN Entscheidung 10).
"""

from __future__ import annotations

from typing import Literal

from otio_app.services.without_voiceover_enhanced.models import StockCandidate

FitBucket = Literal["strong", "acceptable", "weak", "reject"]

FIT_SCORE_STRONG_MIN = 80
FIT_SCORE_ACCEPTABLE_MIN = 60
FIT_SCORE_WEAK_MIN = 40

_BUCKET_RANK: dict[str, int] = {
    "reject": 0,
    "none": 0,
    "weak": 1,
    "acceptable": 2,
    "strong": 3,
    "manual": 2,  # Manual-Assign gilt als acceptable
}


def fit_bucket_from_final_score(score: int | float | None) -> FitBucket:
    """0–100 final_score → strong|acceptable|weak|reject."""
    if score is None:
        return "reject"
    value = float(score)
    if value >= FIT_SCORE_STRONG_MIN:
        return "strong"
    if value >= FIT_SCORE_ACCEPTABLE_MIN:
        return "acceptable"
    if value >= FIT_SCORE_WEAK_MIN:
        return "weak"
    return "reject"


def bucket_rank(bucket: str) -> int:
    return int(_BUCKET_RANK.get(str(bucket or "").strip().lower(), 0))


def supplement_beats_local(*, supplement_bucket: str, local_fit: str) -> bool:
    """Strikt auf Bucket-Ebene: Supplement muss klar besser sein als lokal."""
    local = str(local_fit or "none").strip().lower()
    if local == "none":
        # none: jeder nicht-reject Bucket ist einsetzbar (>= weak).
        return bucket_rank(supplement_bucket) >= bucket_rank("weak")
    return bucket_rank(supplement_bucket) > bucket_rank(local)


def required_candidate_duration_seconds(
    target_duration: float | None,
    *,
    head_trim: float = 0.0,
    short_tolerance: float = 0.0,
) -> float | None:
    """Mindest-API-Dauer: Slot + Head-Trim + Toleranz (hartes K.O. vor Scoring)."""
    if target_duration is None:
        return None
    need = float(target_duration) + max(0.0, float(head_trim)) + max(
        0.0, float(short_tolerance)
    )
    if need <= 0:
        return None
    return need


def passes_duration_prefilter(
    candidate: StockCandidate,
    *,
    min_duration: float | None,
) -> tuple[bool, str]:
    """Dauer-Vorfilter. Stills immer OK; Video ohne Metadaten → fail."""
    if min_duration is None or float(min_duration) <= 0:
        return True, ""
    media = (candidate.media_type or "").strip().lower()
    if media in {"photo", "image"}:
        return True, ""
    if media != "video":
        return True, ""
    if candidate.duration_seconds is None:
        return (
            False,
            f"Videodauer unbekannt (braucht ≥ {float(min_duration):.2f}s).",
        )
    duration = float(candidate.duration_seconds)
    if duration + 1e-9 < float(min_duration):
        return (
            False,
            f"Videodauer {duration:.2f}s < nötig {float(min_duration):.2f}s "
            f"(Slot + Head-Trim + Toleranz).",
        )
    return True, ""


def filter_candidates_by_duration(
    candidates: list[StockCandidate],
    *,
    min_duration: float | None,
) -> tuple[list[StockCandidate], list[tuple[StockCandidate, str]]]:
    """Returns (kept, excluded_with_reason)."""
    kept: list[StockCandidate] = []
    excluded: list[tuple[StockCandidate, str]] = []
    for candidate in candidates:
        ok, reason = passes_duration_prefilter(candidate, min_duration=min_duration)
        if ok:
            kept.append(candidate)
        else:
            excluded.append((candidate, reason))
    return kept, excluded
