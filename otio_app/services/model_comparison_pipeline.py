"""Reduzierte Preview-Pipeline und Delta-Tracking für Modellvergleich."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any

from otio_app.analysis_models import EditPlanSettings, EditPlanShot
from otio_app.defaults import (
    DEFAULT_AUDIO_OFFSET_SEC,
    DEFAULT_SHOT_MAX_SEC,
    DEFAULT_SHOT_MIN_SEC,
    FALLBACK_SOURCE_LOCAL,
    FALLBACK_SOURCE_MISSING,
)
from otio_app.services.edit_plan_rules import EditPlanRulesDocument
from otio_app.services.edit_plan_validator import validate_final_edit_plan
from otio_app.services.generic_outro_selector import asset_id_for_path
from otio_app.services.gemini_client import _extract_json, normalize_match_quality
from otio_app.services.model_comparison_models import (
    DeltaChangeEntry,
    LlmVsPythonDeltaDocument,
    ModelComparisonEffectiveRules,
    ModelComparisonPipelineFlags,
    ParsedLlmBeat,
    ParsedLlmCandidate,
    ParsedLlmPart,
    PLANNING_MODE_MODEL_COMPARISON_RAW,
    PREVIEW_STATUS_INVALID,
    PREVIEW_STATUS_OK,
    PREVIEW_STATUS_PARSE_FAILED,
)
from otio_app.services.shot_timing import TimedPart, allocate_time_by_text
from otio_app.services.timeline_plan_builder import build_timeline_items_for_folder


def content_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def build_comparison_effective_rules(
    *,
    shot_min_sec: float,
    shot_max_sec: float,
    max_asset_usage: int | None,
    min_asset_reuse_distance_shots: int,
) -> ModelComparisonEffectiveRules:
    return ModelComparisonEffectiveRules(
        planning_mode=PLANNING_MODE_MODEL_COMPARISON_RAW,
        shot_rules_enabled=False,
        shot_min_sec=shot_min_sec,
        shot_max_sec=shot_max_sec,
        max_asset_usage_enabled=False,
        max_asset_usage=max_asset_usage,
        asset_reuse_rules_enabled=False,
        min_asset_reuse_distance_shots=min_asset_reuse_distance_shots,
        pipeline=ModelComparisonPipelineFlags(),
    )


@dataclass
class DeltaRecorder:
    changes: list[DeltaChangeEntry] = field(default_factory=list)

    def record(
        self,
        *,
        beat_id: str,
        part_index: int,
        field_name: str,
        before: Any,
        after: Any,
        reason: str,
        function_name: str,
    ) -> None:
        if before == after:
            return
        self.changes.append(
            DeltaChangeEntry(
                beat_id=beat_id,
                part_index=part_index,
                field=field_name,
                before=before,
                after=after,
                reason=reason,
                function_name=function_name,
            )
        )


def parse_llm_candidate_from_text(
    raw_text: str,
    *,
    allowed_paths: set[str],
) -> ParsedLlmCandidate:
    try:
        payload = _extract_json(raw_text)
    except (json.JSONDecodeError, TypeError, ValueError) as exc:
        return ParsedLlmCandidate(parse_error=str(exc))

    beats_raw = payload.get("beats", []) if isinstance(payload, dict) else []
    if not isinstance(beats_raw, list):
        return ParsedLlmCandidate(parse_error="JSON beats field is not a list")

    parsed_beats: list[ParsedLlmBeat] = []
    for beat in beats_raw:
        if not isinstance(beat, dict):
            continue
        beat_id = str(beat.get("beat_id", "")).strip()
        if not beat_id:
            continue
        parts_raw = beat.get("parts", [])
        if not isinstance(parts_raw, list):
            continue
        parts: list[ParsedLlmPart] = []
        for part in parts_raw:
            if not isinstance(part, dict):
                continue
            asset_path = part.get("asset_path")
            if asset_path is not None:
                asset_path = str(asset_path).strip() or None
                if asset_path and asset_path not in allowed_paths:
                    asset_path = None
            desired = part.get("desired_duration_sec")
            desired_duration: float | None = None
            if desired is not None:
                try:
                    desired_duration = float(desired)
                except (TypeError, ValueError):
                    desired_duration = None
            proposed_asset_id = asset_id_for_path(asset_path) if asset_path else None
            parts.append(
                ParsedLlmPart(
                    text=str(part.get("text", "")).strip(),
                    motif=str(part.get("motif", "")).strip(),
                    asset_path=asset_path,
                    proposed_asset_id=proposed_asset_id,
                    match_quality=normalize_match_quality(str(part.get("match_quality", ""))),
                    visual_intent=str(part.get("visual_intent", "")).strip(),
                    reason=str(part.get("reason", "")).strip(),
                    confidence=(
                        str(part.get("confidence")).strip()
                        if part.get("confidence") is not None
                        else None
                    ),
                    desired_duration_sec=desired_duration,
                )
            )
        if parts:
            parsed_beats.append(ParsedLlmBeat(beat_id=beat_id, parts=parts))

    proposed_part_count = sum(len(beat.parts) for beat in parsed_beats)
    return ParsedLlmCandidate(
        beats=parsed_beats,
        proposed_part_count=proposed_part_count,
        raw_beats=[beat for beat in beats_raw if isinstance(beat, dict)],
    )


@dataclass
class ComparisonPreviewResult:
    preview_status: str
    shots: list[EditPlanShot]
    timeline_items: list
    voiceover: Any
    preview_errors: list[str]
    applied_rules: list[str]
    validation_status: str
    validation_errors: list[dict[str, Any]]


def build_technical_preview(
    *,
    parsed: ParsedLlmCandidate,
    segments_with_beats: list[tuple[str, Any]],
    folder_name: str,
    voice_path: str,
    asset_payload: list[dict[str, str]],
    allowed_paths: set[str],
    recorder: DeltaRecorder,
    rules_doc: EditPlanRulesDocument | None = None,
) -> ComparisonPreviewResult:
    """Baut eine technische Preview ohne harte Conforming-Schritte."""
    segment_by_beat = {beat_id: segment for beat_id, segment in segments_with_beats}
    shots: list[EditPlanShot] = []
    preview_errors: list[str] = []
    applied_rules: list[str] = ["allocate_time_by_text", "build_timeline_items_for_folder"]

    for beat in parsed.beats:
        segment = segment_by_beat.get(beat.beat_id)
        if segment is None:
            preview_errors.append(f"Unknown beat_id from LLM: {beat.beat_id}")
            continue
        texts = [part.text for part in beat.parts]
        time_ranges = allocate_time_by_text(segment.start_sec, segment.end_sec, texts)
        for part_index, (part, (start_sec, end_sec)) in enumerate(zip(beat.parts, time_ranges)):
            llm_duration = part.desired_duration_sec
            final_duration = max(0.0, end_sec - start_sec)
            recorder.record(
                beat_id=beat.beat_id,
                part_index=part_index,
                field_name="voice_start_sec",
                before=None,
                after=round(start_sec, 4),
                reason="PYTHON_TIMING_ALLOCATION",
                function_name="allocate_time_by_text",
            )
            recorder.record(
                beat_id=beat.beat_id,
                part_index=part_index,
                field_name="voice_end_sec",
                before=None,
                after=round(end_sec, 4),
                reason="PYTHON_TIMING_ALLOCATION",
                function_name="allocate_time_by_text",
            )
            if llm_duration is not None and abs(llm_duration - final_duration) > 0.05:
                recorder.record(
                    beat_id=beat.beat_id,
                    part_index=part_index,
                    field_name="duration_sec",
                    before=llm_duration,
                    after=round(final_duration, 4),
                    reason="PYTHON_TIMING_ALLOCATION",
                    function_name="allocate_time_by_text",
                )
            asset_path = part.asset_path if part.asset_path in allowed_paths else None
            if part.asset_path and asset_path is None:
                recorder.record(
                    beat_id=beat.beat_id,
                    part_index=part_index,
                    field_name="asset_path",
                    before=part.asset_path,
                    after=None,
                    reason="MISSING_ASSET",
                    function_name="build_technical_preview",
                )
            meta = next((asset for asset in asset_payload if asset.get("path") == asset_path), None)
            shots.append(
                EditPlanShot(
                    voice_file=voice_path,
                    folder=folder_name,
                    voice_start_sec=start_sec,
                    voice_end_sec=end_sec,
                    duration_sec=final_duration,
                    asset_path=asset_path,
                    asset_source=FALLBACK_SOURCE_LOCAL if asset_path else FALLBACK_SOURCE_MISSING,
                    asset_id=(
                        str(meta.get("asset_id", ""))
                        if meta
                        else (asset_id_for_path(asset_path) if asset_path else "")
                    ),
                    motif=part.motif or part.text[:80],
                    passage_text=part.text,
                    confidence=part.confidence,
                    match_quality=part.match_quality or None,
                    beat_id=beat.beat_id,
                )
            )

    preview_settings = EditPlanSettings(
        shot_min_sec=DEFAULT_SHOT_MIN_SEC,
        shot_max_sec=DEFAULT_SHOT_MAX_SEC,
        audio_offset_sec=DEFAULT_AUDIO_OFFSET_SEC,
        section_outro_sec=0.0,
    )
    timeline_items, voiceover, build_errors = build_timeline_items_for_folder(
        shots,
        folder_name=folder_name,
        voice_file=voice_path,
        settings=preview_settings,
        folder_assets=asset_payload,
        trim_leading_sec=0.0,
        opening_title_enabled=False,
        usage_by_asset_id={},
        max_asset_usage=None,
        outro_parts=None,
    )
    preview_errors.extend(build_errors)

    for item in timeline_items:
        if item.type not in {"video_shot", "image_shot", "generic_narration_visual"}:
            continue
        recorder.record(
            beat_id=item.beat_id or "",
            part_index=-1,
            field_name="timeline_in_sec",
            before=None,
            after=item.timeline_in_sec,
            reason="TIMELINE_PACKING",
            function_name="build_timeline_items_for_folder",
        )
        recorder.record(
            beat_id=item.beat_id or "",
            part_index=-1,
            field_name="timeline_out_sec",
            before=None,
            after=item.timeline_out_sec,
            reason="TIMELINE_PACKING",
            function_name="build_timeline_items_for_folder",
        )
        recorder.record(
            beat_id=item.beat_id or "",
            part_index=-1,
            field_name="source_in_sec",
            before=None,
            after=item.source_in_sec,
            reason="TIMELINE_PACKING",
            function_name="build_timeline_items_for_folder",
        )
        recorder.record(
            beat_id=item.beat_id or "",
            part_index=-1,
            field_name="source_out_sec",
            before=None,
            after=item.source_out_sec,
            reason="TIMELINE_PACKING",
            function_name="build_timeline_items_for_folder",
        )

    validation = validate_final_edit_plan(
        timeline_items,
        settings=preview_settings,
        voiceover=voiceover,
        rules_doc=rules_doc,
    )
    validation_status = "PASS" if validation.ok else "FAIL"
    validation_errors = [error.to_dict() for error in validation.errors]

    preview_status = PREVIEW_STATUS_OK if not preview_errors else PREVIEW_STATUS_INVALID
    if build_errors:
        preview_status = PREVIEW_STATUS_INVALID

    return ComparisonPreviewResult(
        preview_status=preview_status,
        shots=shots,
        timeline_items=timeline_items,
        voiceover=voiceover,
        preview_errors=preview_errors,
        applied_rules=applied_rules,
        validation_status=validation_status,
        validation_errors=validation_errors,
    )


def build_delta_document(
    *,
    parsed: ParsedLlmCandidate,
    preview: ComparisonPreviewResult | None,
    recorder: DeltaRecorder,
) -> LlmVsPythonDeltaDocument:
    final_part_count = len(preview.shots) if preview is not None else 0
    beat_summaries: list[dict[str, Any]] = []
    for beat in parsed.beats:
        llm_assets = [part.proposed_asset_id or part.asset_path for part in beat.parts]
        final_assets: list[str | None] = []
        final_durations: list[float] = []
        if preview is not None:
            beat_shots = [shot for shot in preview.shots if shot.beat_id == beat.beat_id]
            final_assets = [shot.asset_id or shot.asset_path for shot in beat_shots]
            final_durations = [round(shot.duration_sec, 4) for shot in beat_shots]
        beat_changes = [change for change in recorder.changes if change.beat_id == beat.beat_id]
        asset_changed = llm_assets != final_assets
        llm_durations = [
            part.desired_duration_sec
            for part in beat.parts
            if part.desired_duration_sec is not None
        ]
        duration_changed = bool(llm_durations) and any(
            change.field == "duration_sec" for change in beat_changes
        )
        beat_summaries.append(
            {
                "beat_id": beat.beat_id,
                "llm_part_count": len(beat.parts),
                "final_part_count": len(final_assets),
                "part_count_changed": len(beat.parts) != len(final_assets),
                "llm_asset_ids": llm_assets,
                "final_asset_ids": final_assets,
                "asset_changed": asset_changed,
                "llm_desired_durations_sec": llm_durations,
                "final_durations_sec": final_durations,
                "duration_changed": duration_changed,
                "change_reasons": sorted({change.reason for change in beat_changes}),
            }
        )

    changes = recorder.changes
    if not changes:
        return LlmVsPythonDeltaDocument(
            changes_count=0,
            note="No Python changes detected",
            beat_summaries=beat_summaries,
            changes=[],
        )
    asset_changes_count = sum(1 for change in changes if change.field in {"asset_path", "asset_id"})
    duration_changes_count = sum(
        1 for change in changes if change.field in {"duration_sec", "voice_start_sec", "voice_end_sec"}
    )
    _ = asset_changes_count, duration_changes_count
    return LlmVsPythonDeltaDocument(
        changes_count=len(changes),
        note="",
        beat_summaries=beat_summaries,
        changes=changes,
    )
