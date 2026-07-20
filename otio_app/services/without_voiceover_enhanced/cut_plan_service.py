"""LLM-Lauf 2/3 + Coverage/Stock-Orchestrierung für Enhanced Cut Plan MVP."""

from __future__ import annotations

import json
from typing import Any, Callable

from otio_app.models import Project
from otio_app.services.gemini_client import _extract_json
from otio_app.services.generic_outro_selector import asset_id_for_path
from otio_app.services.inventory_loader import load_folder_inventory
from otio_app.services.media_utils import probe_duration_seconds
from otio_app.services.plan_llm_client import generate_plan_text_with_metadata
from otio_app.services.voiceover_generation.dramaturgy_service import load_confirmed_dramaturgy
from otio_app.services.voiceover_generation.style_reference_service import (
    style_context_text_for_prompts,
)
from otio_app.services.without_voiceover_enhanced.audio_timing_service import (
    load_segment_timings,
    validate_timings_against_script,
)
from otio_app.services.without_voiceover_enhanced.io_utils import load_model, write_json
from otio_app.services.without_voiceover_enhanced.models import (
    AcceptedSupplementsDocument,
    CoverageGap,
    CoverageGapsDocument,
    EditorialAnchor,
    FinalCutPlanDocument,
    FinalShot,
    NarrationAnchor,
    PauseDirective,
    RoughCutPlanDocument,
    RoughShot,
    StockCandidate,
    StockSearchResultsDocument,
)
from otio_app.services.without_voiceover_enhanced.paths import (
    accepted_supplements_path,
    coverage_gaps_path,
    final_cut_plan_path,
    narration_timeline_path,
    pause_directives_path,
    rough_cut_plan_path,
    stock_search_results_path,
)
from otio_app.services.without_voiceover_enhanced.pause_resolver import (
    build_narration_timeline,
)
from otio_app.services.without_voiceover_enhanced.script_lock_service import (
    require_locked_script,
)
from otio_app.services.without_voiceover_enhanced.script_prompts import (
    build_final_cut_prompt,
    build_rough_cut_prompt,
)
from otio_app.services.without_voiceover_enhanced.local_media_service import (
    STATUS_LOCAL_MEDIA_MISSING,
    list_export_ready_supplements,
    refresh_supplement_validation,
)
from otio_app.services.without_voiceover_enhanced.stock.registry import (
    search_configured_providers,
)
from otio_app.services.without_voiceover_enhanced.stock_provider_config import (
    enabled_provider_names,
)


class CutPlanError(RuntimeError):
    pass


def _local_assets_payload(project: Project) -> list[dict[str, Any]]:
    assets: list[dict[str, Any]] = []
    for folder in project.selected_asset_subdirs:
        inventory = load_folder_inventory(project, folder)
        if inventory is None:
            continue
        for asset in getattr(inventory, "assets", []) or []:
            path = getattr(asset, "path", None) or getattr(asset, "source_path", None)
            if path is None:
                continue
            asset_id = getattr(asset, "asset_id", None) or asset_id_for_path(str(path))
            duration = getattr(asset, "duration_sec", None)
            if duration is None:
                duration = getattr(asset, "duration_seconds", None)
            if duration is None:
                try:
                    duration = probe_duration_seconds(path)
                except Exception:  # noqa: BLE001
                    duration = None
            assets.append(
                {
                    "local_asset_id": asset_id,
                    "asset_id": asset_id,
                    "folder": folder,
                    "path": str(path),
                    "duration_seconds": duration,
                    "media_type": getattr(asset, "media_type", None),
                }
            )
    return assets


def _style_text(project: Project) -> str:
    return style_context_text_for_prompts(project, detailed=True)


def _dramaturgy_text(project: Project) -> str:
    plan = load_confirmed_dramaturgy(project)
    return plan.model_dump_json(indent=2) if plan else "(keine Dramaturgie)"


_POSITION_FRACTION = {
    "start": 0.0,
    "early": 0.25,
    "middle": 0.5,
    "late": 0.75,
    "end": 1.0,
}


def _nullish(value: Any) -> bool:
    return value in (None, "", "null")


def _parse_editorial_anchor(raw: Any) -> EditorialAnchor:
    if not isinstance(raw, dict):
        return EditorialAnchor()
    anchor_type = str(raw.get("type") or "segment").strip().lower() or "segment"
    position = str(raw.get("position") or "start").strip().lower() or "start"
    if position not in _POSITION_FRACTION:
        position = "start"
    after = raw.get("after_segment_id")
    segment_id = str(raw.get("segment_id") or "")
    if anchor_type == "pause":
        after_id = str(after or segment_id or "")
        return EditorialAnchor(
            type="pause",
            segment_id=segment_id or after_id,
            after_segment_id=after_id or None,
            position=position if position in {"start", "middle", "end"} else "start",
        )
    return EditorialAnchor(
        type="segment",
        segment_id=segment_id,
        after_segment_id=None if _nullish(after) else str(after),
        position=position,
    )


def _editorial_to_narration_anchor(anchor: EditorialAnchor) -> NarrationAnchor:
    """Bridge: editorial position → NarrationAnchor (fraction stored as offset).

    Final Cut / Resolver still use real seconds; LLM 2 must not emit seconds.
    Fractions (0–1) are a compact bridge for UI and later timing mapping.
    """
    if anchor.type == "pause":
        segment_id = str(anchor.after_segment_id or anchor.segment_id or "")
        # Pause start ≈ end of preceding segment; middle/end stay at end for now.
        fraction = 1.0 if anchor.position in {"start", "middle", "end"} else 1.0
        return NarrationAnchor(segment_id=segment_id, offset_seconds=fraction)
    fraction = _POSITION_FRACTION.get(anchor.position, 0.0)
    return NarrationAnchor(
        segment_id=anchor.segment_id,
        offset_seconds=float(fraction),
    )


def _legacy_anchor_to_editorial(raw: Any) -> EditorialAnchor:
    if not isinstance(raw, dict):
        return EditorialAnchor()
    segment_id = str(raw.get("segment_id") or "")
    offset = float(raw.get("offset_seconds") or 0.0)
    # Map rough offset buckets when legacy payloads still use seconds.
    if offset <= 0.05:
        position = "start"
    elif offset < 0.35:
        position = "early"
    elif offset < 0.65:
        position = "middle"
    elif offset < 0.9:
        position = "late"
    else:
        position = "end"
    return EditorialAnchor(type="segment", segment_id=segment_id, position=position)


def parse_rough_cut_response(raw: str | dict[str, Any], script_version: str) -> tuple[
    RoughCutPlanDocument, CoverageGapsDocument
]:
    payload = _extract_json(raw) if isinstance(raw, str) else raw
    if not isinstance(payload, dict):
        raise CutPlanError("Grober Cut Plan ist kein JSON-Objekt.")

    directives = [
        PauseDirective(
            after_segment_id=str(item.get("after_segment_id") or ""),
            pause_function=str(item.get("pause_function") or "breath"),
            duration_class=str(item.get("duration_class") or "medium"),
            visual_behavior=str(item.get("visual_behavior") or "editorial_choice"),
            editorial_reason=str(item.get("editorial_reason") or ""),
        )
        for item in payload.get("pause_directives") or []
        if isinstance(item, dict) and item.get("after_segment_id")
    ]

    for item in payload.get("shots") or []:
        if isinstance(item, dict) and (
            "start_frame" in item or "end_frame" in item or "timeline_start" in item
        ):
            raise CutPlanError("LLM-Ausgabe enthält finale Frames/Timelinezeiten.")

    shots: list[RoughShot] = []
    for index, item in enumerate(payload.get("shots") or [], start=1):
        if not isinstance(item, dict):
            continue
        uses_editorial = "start_anchor" in item or "end_anchor" in item
        if uses_editorial:
            if (
                "offset_seconds" in (item.get("start_anchor") or {})
                or "offset_seconds" in (item.get("end_anchor") or {})
            ):
                raise CutPlanError(
                    "LLM-Ausgabe enthält Sekunden in Editorial-Ankern — "
                    "nur position (start|early|middle|late|end) erlaubt."
                )
            start_anchor = _parse_editorial_anchor(item.get("start_anchor"))
            end_anchor = _parse_editorial_anchor(item.get("end_anchor"))
        else:
            start_anchor = _legacy_anchor_to_editorial(item.get("narration_start_anchor"))
            end_anchor = _legacy_anchor_to_editorial(item.get("narration_end_anchor"))

        local_asset = item.get("local_asset_id", item.get("asset_id"))
        local_asset_id = None if _nullish(local_asset) else str(local_asset)
        gap_ref = item.get("coverage_gap_id")
        coverage_gap_id = None if _nullish(gap_ref) else str(gap_ref)
        narrative_function = str(
            item.get("narrative_function")
            or item.get("editorial_function")
            or "orientation"
        )
        visual_intent = str(
            item.get("visual_intent") or item.get("visual_intent_id") or ""
        )
        asset_fit = str(item.get("asset_fit") or ("none" if local_asset_id is None else "acceptable"))
        asset_fit_reason = str(
            item.get("asset_fit_reason") or item.get("editorial_reason") or ""
        )
        narration_start = _editorial_to_narration_anchor(start_anchor)
        narration_end = _editorial_to_narration_anchor(end_anchor)
        if not uses_editorial:
            # Preserve legacy absolute offsets for older fixtures.
            legacy_start = item.get("narration_start_anchor") or {}
            legacy_end = item.get("narration_end_anchor") or {}
            if isinstance(legacy_start, dict):
                narration_start = NarrationAnchor(
                    segment_id=str(legacy_start.get("segment_id") or ""),
                    offset_seconds=float(legacy_start.get("offset_seconds") or 0.0),
                )
            if isinstance(legacy_end, dict):
                narration_end = NarrationAnchor(
                    segment_id=str(legacy_end.get("segment_id") or ""),
                    offset_seconds=float(legacy_end.get("offset_seconds") or 0.0),
                )

        shots.append(
            RoughShot(
                shot_id=str(item.get("shot_id") or f"shot_{index:03d}"),
                start_anchor=start_anchor,
                end_anchor=end_anchor,
                narrative_function=narrative_function,
                visual_intent=visual_intent,
                local_asset_id=local_asset_id,
                asset_fit=asset_fit,
                asset_fit_reason=asset_fit_reason,
                continuity_notes=str(item.get("continuity_notes") or ""),
                coverage_gap_id=coverage_gap_id,
                narration_start_anchor=narration_start,
                narration_end_anchor=narration_end,
                visual_intent_id=str(item.get("visual_intent_id") or visual_intent),
                asset_id=local_asset_id,
                candidate_asset_ids=[
                    str(x) for x in (item.get("candidate_asset_ids") or []) if x
                ],
                editorial_function=narrative_function,
                editorial_reason=asset_fit_reason,
                visual_behavior=str(item.get("visual_behavior") or "hold"),
                may_overlap_pause=bool(item.get("may_overlap_pause", False)),
            )
        )

    gaps: list[CoverageGap] = []
    for i, item in enumerate(payload.get("coverage_gaps") or [], start=1):
        if not isinstance(item, dict):
            continue
        gap_id = str(item.get("coverage_gap_id") or item.get("gap_id") or f"gap_{i:03d}")
        shot_id = item.get("shot_id")
        related = [str(x) for x in (item.get("related_shot_ids") or []) if x]
        if shot_id and str(shot_id) not in related:
            related = [str(shot_id), *related]
        needed = str(item.get("needed_visual") or item.get("subject") or "")
        purpose = str(item.get("editorial_purpose") or item.get("reason") or "")
        concepts = [str(x) for x in (item.get("search_concepts") or []) if x]
        queries = [str(x) for x in (item.get("search_queries") or []) if x]
        if not queries:
            queries = list(concepts)
        gaps.append(
            CoverageGap(
                gap_id=gap_id,
                related_shot_ids=related,
                needed_visual=needed,
                editorial_purpose=purpose,
                preferred_media_type=str(item.get("preferred_media_type") or "video"),
                search_concepts=concepts or list(queries),
                must_include=[str(x) for x in (item.get("must_include") or []) if x],
                must_avoid=[str(x) for x in (item.get("must_avoid") or []) if x],
                fact_check_required=bool(item.get("fact_check_required", False)),
                visual_intent_id=str(item.get("visual_intent_id") or ""),
                subject=needed or str(item.get("subject") or ""),
                location=str(item.get("location") or ""),
                action=str(item.get("action") or ""),
                editorial_function=str(item.get("editorial_function") or "orientation"),
                fallback_media_type=str(item.get("fallback_media_type") or "photo"),
                minimum_resolution=str(item.get("minimum_resolution") or "1920x1080"),
                priority=str(item.get("priority") or "high"),
                reason=purpose or str(item.get("reason") or ""),
                search_queries=queries,
            )
        )

    gaps_by_id = {gap.gap_id: gap for gap in gaps}
    covered_shots = {sid for gap in gaps for sid in gap.related_shot_ids}
    for shot in shots:
        if shot.local_asset_id is not None:
            continue
        if shot.coverage_gap_id and shot.coverage_gap_id in gaps_by_id:
            gap = gaps_by_id[shot.coverage_gap_id]
            if shot.shot_id not in gap.related_shot_ids:
                gap.related_shot_ids.append(shot.shot_id)
            continue
        if shot.shot_id in covered_shots:
            continue
        auto_id = shot.coverage_gap_id or f"gap_auto_{shot.shot_id}"
        gaps.append(
            CoverageGap(
                gap_id=auto_id,
                related_shot_ids=[shot.shot_id],
                needed_visual=shot.visual_intent or shot.narrative_function,
                editorial_purpose=shot.asset_fit_reason
                or "Kein lokales Asset für diesen Shot zugewiesen.",
                preferred_media_type="video",
                search_concepts=[
                    shot.visual_intent or shot.narrative_function or shot.shot_id
                ],
                subject=shot.visual_intent or shot.narrative_function,
                reason="Kein lokales Asset für diesen Shot zugewiesen.",
                search_queries=[
                    shot.visual_intent or shot.narrative_function or shot.shot_id
                ],
            )
        )
        shot.coverage_gap_id = auto_id

    rough = RoughCutPlanDocument(
        script_version=script_version,
        pause_directives=directives,
        shots=shots,
    )
    coverage = CoverageGapsDocument(script_version=script_version, gaps=gaps)
    return rough, coverage


def generate_rough_cut_and_pauses(
    project: Project,
    *,
    llm_callable: Callable[..., Any] | None = None,
) -> tuple[RoughCutPlanDocument, CoverageGapsDocument]:
    locked = require_locked_script(project)
    errors = validate_timings_against_script(project)
    if errors:
        raise CutPlanError("; ".join(errors))
    timings = load_segment_timings(project)
    assert timings is not None

    prompt = build_rough_cut_prompt(
        locked_script_json=locked.model_dump_json(indent=2),
        segment_timings_json=timings.model_dump_json(indent=2),
        local_assets_json=json.dumps(_local_assets_payload(project), ensure_ascii=False, indent=2),
        style_profile_text=_style_text(project),
        dramaturgy_text=_dramaturgy_text(project),
    )
    if llm_callable is not None:
        raw = llm_callable(prompt=prompt, model="openai:gpt-5.4-mini")
        raw_text = raw if isinstance(raw, str) else getattr(raw, "raw_text", str(raw))
    else:
        raw_text = generate_plan_text_with_metadata(
            prompt=prompt, model="openai:gpt-5.4-mini"
        ).raw_text
    rough, coverage = parse_rough_cut_response(raw_text, locked.script_version)

    timeline = build_narration_timeline(
        script_version=locked.script_version,
        segment_timings=timings.segments,
        pause_directives=rough.pause_directives,
    )
    write_json(pause_directives_path(project), {"directives": [d.model_dump(mode="json") for d in rough.pause_directives]})
    write_json(narration_timeline_path(project), timeline)
    write_json(rough_cut_plan_path(project), rough)
    write_json(coverage_gaps_path(project), coverage)
    return rough, coverage


def search_supplements_for_gaps(
    project: Project,
    *,
    providers=None,
) -> StockSearchResultsDocument:
    locked = require_locked_script(project)
    coverage = load_model(coverage_gaps_path(project), CoverageGapsDocument)
    if coverage is None or not coverage.gaps:
        raise CutPlanError("Keine Coverage Gaps vorhanden.")

    enabled = enabled_provider_names(project)
    if not enabled:
        # Preserve any previous search results; do not error.
        existing = load_model(stock_search_results_path(project), StockSearchResultsDocument)
        document = StockSearchResultsDocument(
            script_version=locked.script_version,
            provider_status={
                "pexels": "disabled",
                "pixabay": "disabled",
                "wikimedia": "disabled",
                "openverse": "disabled",
                "archive_org": "disabled",
            },
            candidates=list(existing.candidates) if existing is not None else [],
            message="Keine Stockanbieter aktiviert.",
        )
        write_json(stock_search_results_path(project), document)
        return document

    all_candidates: list[StockCandidate] = []
    provider_status: dict[str, str] = {}
    for gap in coverage.gaps:
        queries = (
            gap.search_concepts
            or gap.search_queries
            or [gap.needed_visual or gap.subject or gap.action or gap.gap_id]
        )
        for query in queries:
            if providers is not None:
                from otio_app.services.without_voiceover_enhanced.stock.registry import (
                    search_all_providers,
                )

                found, status = search_all_providers(
                    query,
                    media_type=gap.preferred_media_type,
                    providers=providers,
                    enabled_names=enabled,
                )
            else:
                found, status, _enabled = search_configured_providers(
                    project,
                    query,
                    media_type=gap.preferred_media_type,
                )
            for key, value in status.items():
                provider_status.setdefault(key, value)
            for candidate in found:
                candidate.gap_id = gap.gap_id
                all_candidates.append(candidate)

    document = StockSearchResultsDocument(
        script_version=locked.script_version,
        provider_status=provider_status,
        candidates=all_candidates,
        message="",
    )
    write_json(stock_search_results_path(project), document)
    return document


def accept_supplement_candidates(
    project: Project,
    candidate_ids: list[str],
) -> AcceptedSupplementsDocument:
    locked = require_locked_script(project)
    results = load_model(stock_search_results_path(project), StockSearchResultsDocument)
    if results is None:
        raise CutPlanError("Keine Stockergebnisse vorhanden.")
    selected: list[StockCandidate] = []
    for candidate in results.candidates:
        if candidate.candidate_id in candidate_ids:
            if candidate.license in (None, "", "unknown"):
                # Keep unknown license metadata as null/unknown — do not invent;
                # still allow manual accept but flag in attribution note.
                candidate.license = candidate.license or None
            candidate.selected = True
            if candidate.local_media_path:
                candidate = refresh_supplement_validation(candidate)
            else:
                candidate.media_validation_status = STATUS_LOCAL_MEDIA_MISSING
                candidate.media_validation_error = (
                    f"Supplement {candidate.candidate_id} besitzt keine validierte "
                    "lokale Mediendatei. Ordne zuerst eine lokale Originaldatei zu."
                )
            selected.append(candidate)
    document = AcceptedSupplementsDocument(
        script_version=locked.script_version,
        supplements=selected,
    )
    write_json(accepted_supplements_path(project), document)
    # Persist selection flags on search results too.
    for candidate in results.candidates:
        candidate.selected = candidate.candidate_id in candidate_ids
    write_json(stock_search_results_path(project), results)
    return document


def parse_final_cut_response(raw: str | dict[str, Any], script_version: str) -> FinalCutPlanDocument:
    payload = _extract_json(raw) if isinstance(raw, str) else raw
    if not isinstance(payload, dict):
        raise CutPlanError("Finaler Cut Plan ist kein JSON-Objekt.")
    shots: list[FinalShot] = []
    for index, item in enumerate(payload.get("shots") or [], start=1):
        if not isinstance(item, dict):
            continue
        start = item.get("narration_start_anchor") or {}
        end = item.get("narration_end_anchor") or {}
        asset_id = str(item.get("asset_id") or "").strip()
        if not asset_id:
            raise CutPlanError(f"Shot {item.get('shot_id')} ohne asset_id.")
        shots.append(
            FinalShot(
                shot_id=str(item.get("shot_id") or f"shot_{index:03d}"),
                narration_start_anchor=NarrationAnchor(
                    segment_id=str(start.get("segment_id") or ""),
                    offset_seconds=float(start.get("offset_seconds") or 0.0),
                ),
                narration_end_anchor=NarrationAnchor(
                    segment_id=str(end.get("segment_id") or ""),
                    offset_seconds=float(end.get("offset_seconds") or 0.0),
                ),
                asset_id=asset_id,
                editorial_function=str(item.get("editorial_function") or "narration_support"),
                editorial_reason=str(item.get("editorial_reason") or ""),
                transition_behavior=str(item.get("transition_behavior") or "straight_cut"),
                source_range_intent=str(
                    item.get("source_range_intent") or "representative_middle_section"
                ),
                may_overlap_pause=bool(item.get("may_overlap_pause", False)),
            )
        )
    if not shots:
        raise CutPlanError("Finaler Plan enthält keine Shots.")
    return FinalCutPlanDocument(script_version=script_version, shots=shots)


def generate_final_cut_plan(
    project: Project,
    *,
    llm_callable: Callable[..., Any] | None = None,
) -> FinalCutPlanDocument:
    from otio_app.services.without_voiceover_enhanced.models import NarrationTimelineDocument

    locked = require_locked_script(project)
    rough = load_model(rough_cut_plan_path(project), RoughCutPlanDocument)
    timeline = load_model(narration_timeline_path(project), NarrationTimelineDocument)
    if rough is None or timeline is None:
        raise CutPlanError("Grober Cut Plan / Narrationstimeline fehlt.")
    # Only accepted AND export_ready supplements may enter LLM run 3.
    export_ready = list_export_ready_supplements(project)
    accepted_json = json.dumps(
        {
            "schema_version": "enhanced-accepted-supplements-v1",
            "script_version": locked.script_version,
            "supplements": [s.model_dump(mode="json") for s in export_ready],
        },
        ensure_ascii=False,
        indent=2,
    )
    prompt = build_final_cut_prompt(
        locked_script_json=locked.model_dump_json(indent=2),
        narration_timeline_json=timeline.model_dump_json(indent=2),
        pause_directives_json=json.dumps(
            [d.model_dump(mode="json") for d in rough.pause_directives],
            ensure_ascii=False,
            indent=2,
        ),
        rough_cut_json=rough.model_dump_json(indent=2),
        local_assets_json=json.dumps(_local_assets_payload(project), ensure_ascii=False, indent=2),
        accepted_supplements_json=accepted_json,
        style_profile_text=_style_text(project),
    )
    if llm_callable is not None:
        raw = llm_callable(prompt=prompt, model="openai:gpt-5.4-mini")
        raw_text = raw if isinstance(raw, str) else getattr(raw, "raw_text", str(raw))
    else:
        raw_text = generate_plan_text_with_metadata(
            prompt=prompt, model="openai:gpt-5.4-mini"
        ).raw_text
    final = parse_final_cut_response(raw_text, locked.script_version)
    write_json(final_cut_plan_path(project), final)
    return final
