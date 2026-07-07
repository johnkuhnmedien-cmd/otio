"""Orchestrierung für LLM-Modellvergleich (Diagnose only — kein Produktionsplan)."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from otio_app.analysis_models import EditPlanSettings, VoiceSegment
from otio_app.defaults import DEFAULT_AUDIO_OFFSET_SEC, DEFAULT_SHOT_MAX_SEC, DEFAULT_SHOT_MIN_SEC
from otio_app.models import Project
from otio_app.services.asset_usage import get_asset_usage_rules, max_asset_usage_limit
from otio_app.services.edit_plan_builder import load_voice_analysis
from otio_app.services.edit_plan_rules import gemini_prompt_text, load_edit_plan_rules
from otio_app.services.generic_outro_selector import asset_id_for_path
from otio_app.services.gemini_client import (
    _format_asset_lines_holistic_v1,
    _format_segment_lines_holistic_v1,
    build_plan_folder_model_comparison_prompt,
)
from otio_app.services.inventory_hash import compute_folder_inventory_hash
from otio_app.services.inventory_loader import load_folder_inventory
from otio_app.services.media_utils import probe_duration_seconds
from otio_app.services.model_comparison_models import (
    LlmVsPythonDeltaDocument,
    ModelComparisonRunManifest,
    ModelComparisonSummary,
    ModelComparisonSummaryRunEntry,
    PLANNING_MODE_MODEL_COMPARISON_RAW,
    PREVIEW_STATUS_INVALID,
    PREVIEW_STATUS_PARSE_FAILED,
    PREVIEW_STATUS_SKIPPED,
    RawLlmResponseDocument,
)
from otio_app.services.model_comparison_pipeline import (
    DeltaRecorder,
    build_comparison_effective_rules,
    build_delta_document,
    build_technical_preview,
    content_hash,
    parse_llm_candidate_from_text,
)
from otio_app.services.model_comparison_storage import (
    ensure_run_dir,
    write_comparison_summary,
    write_conformed_preview_candidate,
    write_effective_rules,
    write_llm_vs_python_delta,
    write_parsed_llm_candidate,
    write_raw_llm_response,
    write_run_manifest,
    write_validation_report,
)
from otio_app.services.plan_llm_client import (
    PlanLlmNotConfiguredError,
    PlanLlmResponse,
    generate_plan_text_with_metadata,
    plan_model_provider,
)
from otio_app.services.voice_folder_matcher import load_voice_folder_mapping


ProgressCallback = Callable[[str, int, int], None]


@dataclass
class ModelComparisonSpec:
    model_id: str
    provider: str = ""


def _normalize_comparison_model_id(spec: ModelComparisonSpec) -> str:
    model = spec.model_id.strip()
    if model.startswith("openai:") or model.startswith("anthropic:"):
        return model
    if spec.provider == "openai":
        return f"openai:{model}"
    if spec.provider == "anthropic":
        return f"anthropic:{model}"
    return model


@dataclass
class ModelComparisonBatchResult:
    comparison_id: str
    folder_name: str
    runs: list[ModelComparisonSummaryRunEntry] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


def _clamp_voice_segment(segment: VoiceSegment, *, max_end_sec: float) -> VoiceSegment:
    start = max(0.0, segment.start_sec)
    end = min(segment.end_sec, max_end_sec)
    if end <= start:
        end = start
    return segment.model_copy(update={"start_sec": start, "end_sec": end})


def _build_folder_context(project: Project, folder_name: str) -> dict[str, Any]:
    mapping = load_voice_folder_mapping(project.voice_folder_mapping_path)
    if mapping is None or not mapping.confirmed:
        raise ValueError("Voice-over-Zuordnung fehlt oder ist nicht bestätigt.")

    voice_doc = load_voice_analysis(project)
    voice_files = {entry.path: entry for entry in voice_doc.files}
    voice_path = next(
        (
            entry.voice_file
            for entry in mapping.entries
            if entry.folder == folder_name and entry.confirmed and entry.voice_file
        ),
        None,
    )
    if not voice_path:
        raise ValueError(f"Kein bestätigtes Voice-over für Ordner {folder_name}.")

    voice_entry = voice_files.get(voice_path)
    if voice_entry is None:
        raise ValueError(f"Voice-over-Analyse fehlt für {voice_path}.")

    folder_inventory = load_folder_inventory(project, folder_name)
    asset_payload = [
        {
            "path": asset.path,
            "description": asset.description,
            "asset_id": asset.asset_id or asset_id_for_path(asset.path),
        }
        for asset in folder_inventory.assets
        if asset.description or asset.path
    ]
    allowed_paths = {asset["path"] for asset in asset_payload}

    file_duration = probe_duration_seconds(Path(voice_path))
    if file_duration is None or file_duration <= 0:
        file_duration = voice_entry.duration_sec

    segments_with_beats: list[tuple[str, VoiceSegment]] = []
    beat_index = 0
    for segment in voice_entry.segments:
        if not segment.text.strip():
            continue
        clamped = _clamp_voice_segment(segment, max_end_sec=file_duration)
        if clamped.end_sec - clamped.start_sec <= 0.05:
            continue
        beat_index += 1
        segments_with_beats.append((f"beat_{beat_index:03d}", clamped))

    segment_payload = [
        {
            "beat_id": beat_id,
            "text": segment.text,
            "start_sec": segment.start_sec,
            "end_sec": segment.end_sec,
        }
        for beat_id, segment in segments_with_beats
    ]

    return {
        "voice_path": voice_path,
        "voice_doc": voice_doc,
        "segments_with_beats": segments_with_beats,
        "segment_payload": segment_payload,
        "asset_payload": asset_payload,
        "allowed_paths": allowed_paths,
        "inventory_hash": compute_folder_inventory_hash(folder_inventory),
    }


def run_single_model_comparison(
    project: Project,
    *,
    folder_name: str,
    comparison_id: str,
    run_id: str,
    model_id: str,
    editor_hint: str,
    shot_min_sec: float,
    shot_max_sec: float,
    rules_doc,
) -> ModelComparisonSummaryRunEntry:
    """Führt einen einzelnen Modellrun aus und schreibt Artefakte."""
    context = _build_folder_context(project, folder_name)
    run_dir = ensure_run_dir(project, comparison_id, run_id)

    asset_usage = get_asset_usage_rules(rules_doc)
    effective_rules = build_comparison_effective_rules(
        shot_min_sec=shot_min_sec,
        shot_max_sec=shot_max_sec,
        max_asset_usage=max_asset_usage_limit(rules_doc),
        min_asset_reuse_distance_shots=asset_usage.min_asset_reuse_distance_shots,
    )
    write_effective_rules(run_dir, effective_rules)

    prompt = build_plan_folder_model_comparison_prompt(
        folder_name=folder_name,
        segment_lines=_format_segment_lines_holistic_v1(context["segment_payload"]),
        asset_lines=_format_asset_lines_holistic_v1(context["asset_payload"]),
        language=context["voice_doc"].language,
        editor_hint=editor_hint,
    )
    prompt_hash = content_hash(prompt)
    effective_rules_hash = content_hash(effective_rules.model_dump_json())

    llm_response: PlanLlmResponse | None = None
    raw_document = RawLlmResponseDocument(
        provider=plan_model_provider(model_id),
        model=model_id,
        raw_text="",
    )
    preview_status = PREVIEW_STATUS_SKIPPED
    validation_status = "SKIPPED"
    parse_error: str | None = None
    raw_part_count = 0
    final_part_count = 0
    asset_changes_count = 0
    duration_changes_count = 0
    recorder = DeltaRecorder()

    try:
        llm_response = generate_plan_text_with_metadata(prompt=prompt, model=model_id)
        raw_document = RawLlmResponseDocument(
            provider=llm_response.provider,
            model=llm_response.resolved_model_id or model_id,
            raw_text=llm_response.raw_text,
            latency_ms=llm_response.latency_ms,
            token_usage=llm_response.token_usage,
        )
    except PlanLlmNotConfiguredError as exc:
        raw_document.raw_text = f"ERROR: {exc}"
        parse_error = str(exc)
        preview_status = PREVIEW_STATUS_PARSE_FAILED
        write_raw_llm_response(run_dir, raw_document)
        parsed = parse_llm_candidate_from_text("", allowed_paths=context["allowed_paths"])
        parsed.parse_error = parse_error
        write_parsed_llm_candidate(run_dir, parsed)
        delta = LlmVsPythonDeltaDocument(changes_count=0, note="LLM call failed")
        write_llm_vs_python_delta(run_dir, delta)
        write_conformed_preview_candidate(
            run_dir,
            {"preview_status": preview_status, "error": parse_error, "timeline_items": []},
        )
        write_validation_report(run_dir, {"validation_status": validation_status, "errors": []})
        manifest = ModelComparisonRunManifest(
            run_id=run_id,
            comparison_id=comparison_id,
            provider=raw_document.provider,
            model=raw_document.model,
            folder_name=folder_name,
            voiceover_path=context["voice_path"],
            inventory_hash=context["inventory_hash"],
            prompt_hash=prompt_hash,
            effective_rules_hash=effective_rules_hash,
            preview_status=preview_status,
            validation_status=validation_status,
        )
        write_run_manifest(run_dir, manifest)
        return ModelComparisonSummaryRunEntry(
            run_id=run_id,
            provider=raw_document.provider,
            model=raw_document.model,
            preview_status=preview_status,
            validation_status=validation_status,
            parse_error=parse_error,
            latency_ms=raw_document.latency_ms,
            token_usage=raw_document.token_usage,
        )

    write_raw_llm_response(run_dir, raw_document)

    parsed = parse_llm_candidate_from_text(
        raw_document.raw_text,
        allowed_paths=context["allowed_paths"],
    )
    write_parsed_llm_candidate(run_dir, parsed)
    raw_part_count = parsed.proposed_part_count

    if parsed.parse_error:
        parse_error = parsed.parse_error
        preview_status = PREVIEW_STATUS_PARSE_FAILED
        delta = build_delta_document(parsed=parsed, preview=None, recorder=recorder)
        write_llm_vs_python_delta(run_dir, delta)
        write_conformed_preview_candidate(
            run_dir,
            {
                "preview_status": preview_status,
                "parse_error": parse_error,
                "timeline_items": [],
                "applied_rules": [],
            },
        )
        write_validation_report(run_dir, {"validation_status": "FAIL", "errors": [{"type": "PARSE_ERROR", "message": parse_error}]})
    else:
        preview = build_technical_preview(
            parsed=parsed,
            segments_with_beats=context["segments_with_beats"],
            folder_name=folder_name,
            voice_path=context["voice_path"],
            asset_payload=context["asset_payload"],
            allowed_paths=context["allowed_paths"],
            recorder=recorder,
            rules_doc=rules_doc,
        )
        preview_status = preview.preview_status
        validation_status = preview.validation_status
        final_part_count = len(preview.shots)
        delta = build_delta_document(parsed=parsed, preview=preview, recorder=recorder)
        asset_changes_count = sum(
            1 for change in delta.changes if change.field in {"asset_path", "asset_id"}
        )
        duration_changes_count = sum(
            1
            for change in delta.changes
            if change.field in {"duration_sec", "voice_start_sec", "voice_end_sec"}
        )
        write_llm_vs_python_delta(run_dir, delta)
        write_conformed_preview_candidate(
            run_dir,
            {
                "preview_status": preview.preview_status,
                "applied_rules": preview.applied_rules,
                "preview_errors": preview.preview_errors,
                "timeline_items": [item.model_dump(mode="json") for item in preview.timeline_items],
                "shots": [shot.model_dump(mode="json") for shot in preview.shots],
                "validation_status": preview.validation_status,
            },
        )
        write_validation_report(
            run_dir,
            {
                "validation_status": preview.validation_status,
                "errors": preview.validation_errors,
                "preview_errors": preview.preview_errors,
            },
        )

    manifest = ModelComparisonRunManifest(
        run_id=run_id,
        comparison_id=comparison_id,
        provider=raw_document.provider,
        model=raw_document.model,
        folder_name=folder_name,
        voiceover_path=context["voice_path"],
        inventory_hash=context["inventory_hash"],
        prompt_hash=prompt_hash,
        effective_rules_hash=effective_rules_hash,
        preview_status=preview_status,
        validation_status=validation_status,
    )
    write_run_manifest(run_dir, manifest)

    return ModelComparisonSummaryRunEntry(
        run_id=run_id,
        provider=raw_document.provider,
        model=raw_document.model,
        raw_part_count=raw_part_count,
        final_part_count=final_part_count,
        asset_changes_count=asset_changes_count,
        duration_changes_count=duration_changes_count,
        preview_status=preview_status,
        validation_status=validation_status,
        latency_ms=raw_document.latency_ms,
        token_usage=raw_document.token_usage,
        parse_error=parse_error,
    )


def run_model_comparison_batch(
    project: Project,
    *,
    folder_name: str,
    model_specs: list[ModelComparisonSpec],
    shot_min_sec: float = DEFAULT_SHOT_MIN_SEC,
    shot_max_sec: float = DEFAULT_SHOT_MAX_SEC,
    progress_callback: ProgressCallback | None = None,
) -> ModelComparisonBatchResult:
    """Führt mehrere Modellruns sequenziell aus (Diagnose — kein EditPlan)."""
    if not model_specs:
        raise ValueError("Mindestens ein Modell für den Vergleich auswählen.")

    comparison_id = str(uuid.uuid4())
    rules_doc = load_edit_plan_rules(project)
    editor_hint = gemini_prompt_text(rules_doc)
    runs: list[ModelComparisonSummaryRunEntry] = []
    errors: list[str] = []
    total = len(model_specs)

    for index, spec in enumerate(model_specs, start=1):
        if progress_callback is not None:
            progress_callback(spec.model_id, index, total)
        run_id = str(uuid.uuid4())
        normalized_model = _normalize_comparison_model_id(spec)
        try:
            entry = run_single_model_comparison(
                project,
                folder_name=folder_name,
                comparison_id=comparison_id,
                run_id=run_id,
                model_id=normalized_model,
                editor_hint=editor_hint,
                shot_min_sec=shot_min_sec,
                shot_max_sec=shot_max_sec,
                rules_doc=rules_doc,
            )
            runs.append(entry)
        except Exception as exc:
            errors.append(f"{spec.model_id}: {exc}")
            runs.append(
                ModelComparisonSummaryRunEntry(
                    run_id=run_id,
                    provider=plan_model_provider(spec.model_id),
                    model=spec.model_id,
                    preview_status=PREVIEW_STATUS_INVALID,
                    validation_status="FAIL",
                    parse_error=str(exc),
                )
            )

    summary = ModelComparisonSummary(
        comparison_id=comparison_id,
        folder_name=folder_name,
        runs=runs,
    )
    write_comparison_summary(project, comparison_id, summary)
    return ModelComparisonBatchResult(
        comparison_id=comparison_id,
        folder_name=folder_name,
        runs=runs,
        errors=errors,
    )
