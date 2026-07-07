"""Coverage-Bewertung: lokale Assets vs. Voice-over-Segmente."""

from __future__ import annotations

import re
import uuid
from datetime import datetime, timezone
from pathlib import Path

from otio_app.analysis_models import (
    AssetMediaAnalysis,
    SegmentCoverage,
    SupplementRequest,
    VoiceSegment,
)
from otio_app.defaults import DEFAULT_COVERAGE_THRESHOLD
from otio_app.models import Project
from otio_app.services.generic_outro_selector import asset_id_for_path
from otio_app.services.generic_outro_selector import section_id_for_folder
from otio_app.services.supplement_search import build_keyword_query


COVERAGE_LOCAL_GOOD = "LOCAL_GOOD"
COVERAGE_LOCAL_WEAK = "LOCAL_WEAK"
COVERAGE_LOCAL_MISSING = "LOCAL_MISSING"
COVERAGE_SUPPLEMENT_REQUIRED = "SUPPLEMENT_REQUIRED"
COVERAGE_SUPPLEMENT_AVAILABLE = "SUPPLEMENT_AVAILABLE"
COVERAGE_MANUAL_REVIEW = "MANUAL_REVIEW_REQUIRED"


def _tokens(text: str) -> set[str]:
    return set(re.findall(r"\w{3,}", text.lower()))


def score_asset_match(
    *,
    passage_text: str,
    visual_requirement: str,
    description: str,
    must_show: list[str] | None = None,
) -> float:
    query = f"{passage_text} {visual_requirement}".strip()
    req_tokens = _tokens(query)
    desc_tokens = _tokens(description)
    if not req_tokens:
        return 0.0
    overlap = len(req_tokens & desc_tokens)
    base = overlap / len(req_tokens)
    if must_show:
        must_tokens = {token.lower() for token in must_show if token}
        if must_tokens and not must_tokens & desc_tokens:
            return min(base, 0.35)
    return round(min(1.0, base), 4)


def derive_visual_requirement(passage_text: str) -> str:
    text = passage_text.strip()
    if not text:
        return ""
    return text


def derive_must_show_keywords(visual_requirement: str) -> list[str]:
    tokens = sorted(_tokens(visual_requirement), key=len, reverse=True)
    return [token for token in tokens if len(token) >= 5][:6]


def evaluate_segment_coverage(
    *,
    beat_id: str,
    segment: VoiceSegment,
    folder_name: str,
    voice_file: str,
    assets: list[AssetMediaAnalysis],
    threshold: float = DEFAULT_COVERAGE_THRESHOLD,
) -> SegmentCoverage:
    visual_requirement = derive_visual_requirement(segment.text)
    must_show = derive_must_show_keywords(visual_requirement)
    scored: list[tuple[float, AssetMediaAnalysis]] = []
    for asset in assets:
        if not asset.description and not asset.path:
            continue
        score = score_asset_match(
            passage_text=segment.text,
            visual_requirement=visual_requirement,
            description=asset.description or Path(asset.path).stem,
            must_show=must_show,
        )
        scored.append((score, asset))

    scored.sort(key=lambda entry: entry[0], reverse=True)
    best_score = scored[0][0] if scored else 0.0
    best_asset = scored[0][1] if scored else None
    best_asset_id = ""
    if best_asset is not None:
        best_asset_id = best_asset.asset_id or asset_id_for_path(best_asset.path)

    candidate_ids = [
        asset.asset_id or asset_id_for_path(asset.path)
        for score, asset in scored[:5]
        if score > 0.05
    ]

    duration_needed = max(0.1, segment.end_sec - segment.start_sec)
    if not assets:
        status = COVERAGE_LOCAL_MISSING
    elif best_score >= threshold:
        status = COVERAGE_LOCAL_GOOD
    elif best_score >= threshold * 0.65:
        status = COVERAGE_LOCAL_WEAK
    else:
        status = COVERAGE_SUPPLEMENT_REQUIRED

    if best_asset is not None and best_asset.asset_origin not in ("", "local_original"):
        if best_score >= threshold:
            status = COVERAGE_SUPPLEMENT_AVAILABLE

    return SegmentCoverage(
        beat_id=beat_id,
        passage_text=segment.text,
        visual_requirement=visual_requirement,
        must_show=must_show,
        local_candidate_asset_ids=candidate_ids,
        best_local_match_score=best_score,
        best_local_asset_id=best_asset_id,
        coverage_status=status,
        voice_file=voice_file,
        folder_name=folder_name,
        duration_needed_sec=round(duration_needed, 4),
    )


def coverage_to_supplement_request(
    coverage: SegmentCoverage,
    *,
    request_id: str | None = None,
) -> SupplementRequest | None:
    if coverage.coverage_status not in {
        COVERAGE_SUPPLEMENT_REQUIRED,
        COVERAGE_LOCAL_WEAK,
        COVERAGE_LOCAL_MISSING,
    }:
        return None
    now = datetime.now(timezone.utc)
    reason = (
        f"Lokale Assets unzureichend (Score {coverage.best_local_match_score:.2f}). "
        f"Benötigt: {coverage.visual_requirement[:120]}"
    )
    keyword_query = build_keyword_query(
        folder_name=coverage.folder_name,
        visual_requirement=coverage.visual_requirement,
        passage_text=coverage.passage_text,
    )
    return SupplementRequest(
        supplement_request_id=request_id or f"supp_req_{uuid.uuid4().hex[:8]}",
        section_id=section_id_for_folder(coverage.folder_name),
        folder_name=coverage.folder_name,
        location_name=coverage.folder_name,
        search_context=coverage.visual_requirement,
        beat_id=coverage.beat_id,
        passage_text=coverage.passage_text,
        visual_requirement=coverage.visual_requirement,
        duration_needed_sec=coverage.duration_needed_sec,
        reason=reason,
        local_best_asset_id=coverage.best_local_asset_id,
        local_best_match_score=coverage.best_local_match_score,
        search_queries={
            "de": [coverage.visual_requirement[:120]],
            "en": [keyword_query],
        },
        best_query=keyword_query,
        query_used=keyword_query,
        created_at=now,
        updated_at=now,
    )


def evaluate_folder_coverage(
    project: Project,
    *,
    folder_name: str,
    voice_file: str,
    segments: list[VoiceSegment],
    assets: list[AssetMediaAnalysis],
    threshold: float = DEFAULT_COVERAGE_THRESHOLD,
) -> tuple[list[SegmentCoverage], list[SupplementRequest]]:
    coverages: list[SegmentCoverage] = []
    requests: list[SupplementRequest] = []
    for index, segment in enumerate(segments, start=1):
        if not segment.text.strip():
            continue
        beat_id = f"beat_{index:03d}"
        coverage = evaluate_segment_coverage(
            beat_id=beat_id,
            segment=segment,
            folder_name=folder_name,
            voice_file=voice_file,
            assets=assets,
            threshold=threshold,
        )
        request = coverage_to_supplement_request(coverage)
        if request is not None:
            coverage = coverage.model_copy(
                update={"supplement_request_id": request.supplement_request_id}
            )
            requests.append(request)
        coverages.append(coverage)
    return coverages, requests
