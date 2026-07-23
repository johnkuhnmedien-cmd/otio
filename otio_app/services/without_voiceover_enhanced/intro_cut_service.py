"""Intro-only Cut: gebündeltes Inventar, strong-only, Opening/Closing, separater OTIO."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from otio_app.defaults import AUDIO_SCOPE_INTRO
from otio_app.models import Project
from otio_app.services.media_utils import probe_duration_seconds
from otio_app.services.plan_llm_client import generate_plan_text_with_metadata
from otio_app.services.gemini_client import _extract_json
from otio_app.services.voiceover_generation.dramaturgy_service import (
    load_confirmed_dramaturgy,
)
from otio_app.services.voiceover_generation.intro_hook_service import (
    load_confirmed_intro_hook,
)
from otio_app.services.voiceover_generation.style_reference_service import (
    style_context_text_for_prompts,
)
from otio_app.services.voiceover_generation.tts_orchestration_service import (
    load_audio_manifest,
)
from otio_app.services.without_voiceover_enhanced.cut_plan_service import (
    CutPlanError,
    _local_assets_payload,
    parse_rough_cut_response,
)
from otio_app.services.without_voiceover_enhanced.io_utils import load_model, write_json
from otio_app.services.without_voiceover_enhanced.models import (
    CoverageGap,
    CoverageGapsDocument,
    NarrationTimelineDocument,
    NarrationTimelineEntry,
    ResolvedAudioSegment,
    ResolvedShot,
    ResolvedTimelineDocument,
    RoughCutPlanDocument,
    RoughShot,
)
from otio_app.services.without_voiceover_enhanced.otio_export_service import (
    EnhancedOtioExportError,
)
from otio_app.services.without_voiceover_enhanced.paths import (
    assert_enhanced_work_root,
    coverage_gaps_path,
    intro_bundled_inventory_path,
    intro_coverage_gaps_path,
    intro_cut_meta_path,
    intro_cut_plan_path,
    intro_narration_timeline_path,
    intro_repair_log_path,
    intro_resolved_timeline_path,
)
from otio_app.services.without_voiceover_enhanced.script_prompts import (
    build_intro_cut_prompt,
)
from otio_app.services.without_voiceover_enhanced.timeline_resolver import (
    TimelineResolveError,
    _asset_catalog,
    _seconds_to_frame,
)

INTRO_SEGMENT_ID = "intro_segment_001"
INTRO_ID_PREFIX = "intro_"
INTRO_OPENING_HOLD_SEC = 4.0
INTRO_CLOSING_HOLD_DEFAULT_SEC = 6.5
INTRO_CLOSING_HOLD_MIN_SEC = 5.0
INTRO_CLOSING_HOLD_MAX_SEC = 8.0
INTRO_MIN_SHOT_SEC = 0.8
INTRO_MAX_SHOT_SEC = 120.0

_POSITION_RATIOS = {
    "start": 0.0,
    "early": 0.2,
    "middle": 0.5,
    "late": 0.8,
    "end": 1.0,
}


class IntroCutError(RuntimeError):
    pass


@dataclass
class IntroCutGenerateResult:
    cut: RoughCutPlanDocument
    coverage: CoverageGapsDocument
    bundled_inventory: dict[str, Any]
    shot_count: int
    gap_count: int


def build_bundled_inventory_for_intro(
    project: Project,
    *,
    include_middle_frames: bool = False,
) -> dict[str, Any]:
    """Alle Kapitel-Inventare als eine gebündelte JSON-Struktur."""
    assert_enhanced_work_root(project)
    chapters: dict[str, list[dict[str, Any]]] = {}
    all_assets: list[dict[str, Any]] = []
    folders = list(project.selected_asset_subdirs or [])
    for folder in folders:
        if not folder:
            continue
        assets = _local_assets_payload(
            project,
            folder_name=folder,
            include_middle_frames=include_middle_frames,
        )
        chapters[folder] = assets
        all_assets.extend(assets)
    return {
        "schema_version": "enhanced-intro-bundled-inventory-v1",
        "chapter_count": len(chapters),
        "asset_count": len(all_assets),
        "chapters": chapters,
        "all_assets": all_assets,
    }


def _require_intro_audio(project: Project) -> tuple[str, float]:
    manifest = load_audio_manifest(project)
    item = next(
        (entry for entry in manifest.items if entry.scope == AUDIO_SCOPE_INTRO),
        None,
    )
    if item is None:
        raise IntroCutError(
            "Intro-Audio fehlt — zuerst Intro bei ElevenLabs vertonen."
        )
    audio_path = str(item.audio_path or "").strip()
    if not audio_path or not Path(audio_path).expanduser().is_file():
        raise IntroCutError(
            "Intro-Audiodatei fehlt oder ist ungültig — Intro erneut vertonen."
        )
    duration = float(item.audio_duration_sec or 0.0)
    if duration <= 0:
        try:
            duration = float(probe_duration_seconds(Path(audio_path)))
        except Exception as exc:  # noqa: BLE001
            raise IntroCutError(
                f"Intro-Audiodauer konnte nicht gelesen werden: {exc}"
            ) from exc
    if duration <= 0:
        raise IntroCutError("Intro-Audiodauer ist 0 — Intro erneut vertonen.")
    return audio_path, duration


def _style_text(project: Project) -> str:
    return style_context_text_for_prompts(project, detailed=True)


def _dramaturgy_text(project: Project) -> str:
    plan = load_confirmed_dramaturgy(project)
    return plan.model_dump_json(indent=2) if plan else "(keine Dramaturgie)"


def _known_asset_ids(bundled: dict[str, Any]) -> set[str]:
    ids: set[str] = set()
    for item in bundled.get("all_assets") or []:
        if not isinstance(item, dict):
            continue
        asset_id = item.get("local_asset_id") or item.get("asset_id")
        if asset_id:
            ids.add(str(asset_id))
    return ids


def _ensure_intro_prefix(value: str, *, kind: str) -> str:
    text = str(value or "").strip()
    if not text:
        return f"{INTRO_ID_PREFIX}{kind}_001"
    if text.startswith(INTRO_ID_PREFIX):
        return text
    return f"{INTRO_ID_PREFIX}{text}"


def _position_ratio(position: str) -> float:
    return _POSITION_RATIOS.get(str(position or "start").strip().lower(), 0.0)


def enforce_intro_strong_only(
    cut: RoughCutPlanDocument,
    coverage: CoverageGapsDocument,
) -> tuple[RoughCutPlanDocument, CoverageGapsDocument]:
    """Nur asset_fit=strong behalten; sonst Asset entfernen und Coverage-Gap erzeugen."""
    gaps_by_id = {gap.gap_id: gap for gap in coverage.gaps}
    gaps = list(coverage.gaps)
    updated_shots: list[RoughShot] = []

    for shot in cut.shots:
        fit = str(shot.asset_fit or "").strip().lower()
        asset_id = shot.local_asset_id or shot.asset_id
        if asset_id and fit == "strong":
            updated = shot.model_copy(
                update={
                    "local_asset_id": asset_id,
                    "asset_id": asset_id,
                    "asset_fit": "strong",
                    "coverage_gap_id": None,
                }
            )
            updated_shots.append(updated)
            continue

        # Kein strong → Gap (auch wenn LLM „acceptable“ geliefert hat).
        gap_id = _ensure_intro_prefix(
            shot.coverage_gap_id or f"gap_auto_{shot.shot_id}",
            kind="gap",
        )
        reason = (
            shot.asset_fit_reason
            or f"Intro verlangt asset_fit=strong; erhalten: {fit or 'none'}."
        )
        if gap_id not in gaps_by_id:
            gap = CoverageGap(
                gap_id=gap_id,
                related_shot_ids=[shot.shot_id],
                needed_visual=shot.visual_intent or shot.narrative_function,
                editorial_purpose=reason,
                preferred_media_type="video",
                search_concepts=[
                    shot.visual_intent or shot.narrative_function or shot.shot_id
                ],
                subject=shot.visual_intent or shot.narrative_function,
                reason=reason,
                search_queries=[
                    shot.visual_intent or shot.narrative_function or shot.shot_id
                ],
                priority="high",
            )
            gaps.append(gap)
            gaps_by_id[gap_id] = gap
        else:
            gap = gaps_by_id[gap_id]
            if shot.shot_id not in gap.related_shot_ids:
                gap.related_shot_ids.append(shot.shot_id)

        updated_shots.append(
            shot.model_copy(
                update={
                    "local_asset_id": None,
                    "asset_id": None,
                    "asset_fit": "none",
                    "asset_fit_reason": reason,
                    "coverage_gap_id": gap_id,
                }
            )
        )

    # Gap-IDs auf intro_ normalisieren.
    normalized_gaps: list[CoverageGap] = []
    for gap in gaps:
        gid = _ensure_intro_prefix(gap.gap_id, kind="gap")
        normalized_gaps.append(gap.model_copy(update={"gap_id": gid}))

    normalized_shots: list[RoughShot] = []
    for shot in updated_shots:
        sid = _ensure_intro_prefix(shot.shot_id, kind="shot")
        gap_ref = (
            _ensure_intro_prefix(shot.coverage_gap_id, kind="gap")
            if shot.coverage_gap_id
            else None
        )
        normalized_shots.append(
            shot.model_copy(update={"shot_id": sid, "coverage_gap_id": gap_ref})
        )

    return (
        cut.model_copy(update={"shots": normalized_shots}),
        coverage.model_copy(update={"gaps": normalized_gaps}),
    )


def _merge_intro_gaps_into_main(
    project: Project,
    intro_coverage: CoverageGapsDocument,
) -> None:
    """Intro-Gaps in die Funnel-Coverage mergen (intro_* ersetzen, Rest behalten)."""
    existing = load_model(coverage_gaps_path(project), CoverageGapsDocument)
    kept: list[CoverageGap] = []
    if existing is not None:
        kept = [
            gap
            for gap in existing.gaps
            if not str(gap.gap_id).startswith(INTRO_ID_PREFIX)
        ]
    script_version = intro_coverage.script_version or (
        existing.script_version if existing is not None else "intro"
    )
    merged = CoverageGapsDocument(
        script_version=script_version,
        gaps=[*kept, *intro_coverage.gaps],
    )
    write_json(coverage_gaps_path(project), merged)


def preserve_intro_gaps_in_coverage(
    project: Project,
    chapter_coverage: CoverageGapsDocument,
) -> CoverageGapsDocument:
    """Beim Kapitel-Merge intro_*-Gaps nicht überschreiben."""
    intro_doc = load_model(intro_coverage_gaps_path(project), CoverageGapsDocument)
    if intro_doc is None or not intro_doc.gaps:
        return chapter_coverage
    chapter_only = [
        gap
        for gap in chapter_coverage.gaps
        if not str(gap.gap_id).startswith(INTRO_ID_PREFIX)
    ]
    return chapter_coverage.model_copy(
        update={"gaps": [*chapter_only, *intro_doc.gaps]}
    )


def generate_intro_cut(
    project: Project,
    *,
    provider: str = "openai",
    model: str = "gpt-5.6-terra",
    llm_callable: Callable[..., Any] | None = None,
) -> IntroCutGenerateResult:
    """Ein LLM-Call nur für das Intro; persistiert Intro-Artefakte + Funnel-Gaps."""
    from otio_app.services.voiceover_generation.model_settings_service import (
        resolve_llm_model_id,
    )

    assert_enhanced_work_root(project)
    hook = load_confirmed_intro_hook(project)
    if hook is None:
        raise IntroCutError("Kein bestätigter Intro-Hook vorhanden.")
    _, audio_duration = _require_intro_audio(project)

    bundled = build_bundled_inventory_for_intro(project)
    if not bundled.get("all_assets"):
        raise IntroCutError(
            "Gebündeltes Inventar ist leer — zuerst Inventare der Kapitel aufbauen."
        )
    write_json(intro_bundled_inventory_path(project), bundled)

    prompt = build_intro_cut_prompt(
        intro_hook_json=hook.model_dump_json(indent=2),
        bundled_inventory_json=json.dumps(bundled, ensure_ascii=False, indent=2),
        intro_audio_duration_seconds=audio_duration,
        style_profile_text=_style_text(project),
        dramaturgy_text=_dramaturgy_text(project),
    )
    model_id = resolve_llm_model_id(provider, model)
    if llm_callable is not None:
        raw = llm_callable(prompt=prompt, model=model_id)
        raw_text = raw if isinstance(raw, str) else getattr(raw, "raw_text", str(raw))
    else:
        raw_text = generate_plan_text_with_metadata(
            prompt=prompt,
            model=model_id,
        ).raw_text

    script_version = f"intro:{hook.hook_id}"
    try:
        raw_payload = _extract_json(raw_text) if isinstance(raw_text, str) else raw_text
    except Exception:  # noqa: BLE001
        raw_payload = None
    if not isinstance(raw_payload, dict):
        raw_payload = None
    closing_hold = _extract_closing_hold_seconds(raw_payload)
    write_json(
        intro_cut_meta_path(project),
        {
            "schema_version": "enhanced-intro-cut-meta-v1",
            "hook_id": hook.hook_id,
            "opening_hold_seconds": INTRO_OPENING_HOLD_SEC,
            "closing_hold_seconds": closing_hold,
            "intro_audio_duration_seconds": audio_duration,
        },
    )
    try:
        cut, coverage = parse_rough_cut_response(raw_text, script_version)
    except CutPlanError as exc:
        raise IntroCutError(str(exc)) from exc

    if not cut.shots:
        raise IntroCutError("Intro-LLM-Antwort enthielt keine Shots.")

    known = _known_asset_ids(bundled)
    validated_shots: list[RoughShot] = []
    for shot in cut.shots:
        asset_id = shot.local_asset_id or shot.asset_id
        if asset_id and str(asset_id) not in known:
            validated_shots.append(
                shot.model_copy(
                    update={
                        "local_asset_id": None,
                        "asset_id": None,
                        "asset_fit": "none",
                        "asset_fit_reason": (
                            f"Unbekannte Asset-ID '{asset_id}' — nicht im "
                            "gebündelten Inventar."
                        ),
                        "coverage_gap_id": shot.coverage_gap_id
                        or f"gap_unknown_{shot.shot_id}",
                    }
                )
            )
        else:
            validated_shots.append(shot)
    cut = cut.model_copy(update={"shots": validated_shots})
    cut, coverage = enforce_intro_strong_only(cut, coverage)

    write_json(intro_cut_plan_path(project), cut)
    write_json(intro_coverage_gaps_path(project), coverage)
    _merge_intro_gaps_into_main(project, coverage)
    return IntroCutGenerateResult(
        cut=cut,
        coverage=coverage,
        bundled_inventory=bundled,
        shot_count=len(cut.shots),
        gap_count=len(coverage.gaps),
    )


def _extract_closing_hold_seconds(raw_payload: dict[str, Any] | None) -> float:
    hold = INTRO_CLOSING_HOLD_DEFAULT_SEC
    if raw_payload:
        for item in raw_payload.get("shots") or []:
            if not isinstance(item, dict):
                continue
            if str(item.get("shot_role") or "").lower() != "closing":
                continue
            value = item.get("closing_hold_seconds")
            if value is None:
                continue
            try:
                hold = float(value)
            except (TypeError, ValueError):
                continue
    return max(INTRO_CLOSING_HOLD_MIN_SEC, min(INTRO_CLOSING_HOLD_MAX_SEC, hold))


def _load_closing_hold_seconds(project: Project) -> float:
    path = intro_cut_meta_path(project)
    if not path.is_file():
        return INTRO_CLOSING_HOLD_DEFAULT_SEC
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        hold = float(payload.get("closing_hold_seconds") or INTRO_CLOSING_HOLD_DEFAULT_SEC)
    except (OSError, UnicodeError, json.JSONDecodeError, TypeError, ValueError):
        return INTRO_CLOSING_HOLD_DEFAULT_SEC
    return max(INTRO_CLOSING_HOLD_MIN_SEC, min(INTRO_CLOSING_HOLD_MAX_SEC, hold))


def _vo_seconds_from_anchor(position: str, vo_duration: float) -> float:
    return round(_position_ratio(position) * max(0.0, vo_duration), 6)


def resolve_intro_timeline(project: Project) -> ResolvedTimelineDocument:
    """Python-Timing nur für Intro: Opening 4s, Closing 5–8s, short shots OK."""
    assert_enhanced_work_root(project)
    cut = load_model(intro_cut_plan_path(project), RoughCutPlanDocument)
    if cut is None or not cut.shots:
        raise TimelineResolveError(
            "Intro-Cut-Plan fehlt — zuerst „Intro: LLM Schnitt“ ausführen."
        )
    audio_path, vo_duration = _require_intro_audio(project)
    fps = float(project.fps)
    opening = INTRO_OPENING_HOLD_SEC
    closing = _load_closing_hold_seconds(project)

    audio_start = _seconds_to_frame(opening, fps)
    audio_end = _seconds_to_frame(opening + vo_duration, fps)
    total = _seconds_to_frame(audio_end + closing, fps)

    narration = NarrationTimelineDocument(
        schema_version="enhanced-intro-narration-timeline-v1",
        script_version=cut.script_version,
        total_duration_seconds=total,
        entries=[
            NarrationTimelineEntry(
                segment_id=INTRO_SEGMENT_ID,
                start_seconds=audio_start,
                end_seconds=audio_end,
                pause_after_seconds=closing,
                next_segment_start_seconds=None,
            )
        ],
    )
    write_json(intro_narration_timeline_path(project), narration)

    catalog = _asset_catalog(project)
    errors: list[str] = []
    repairs: list[str] = []
    resolved_shots: list[ResolvedShot] = []

    # Shots ohne strong Asset überspringen (Gaps → Funnel), nicht fail-closed.
    assignable = [
        shot
        for shot in cut.shots
        if (shot.local_asset_id or shot.asset_id)
        and str(shot.asset_fit).lower() == "strong"
    ]
    if not assignable:
        errors.append(
            "Intro-Cut hat keine strong Assets — Supplement-Funnel nutzen, "
            "dann Intro-LLM erneut oder Timing nach Accept."
        )

    n = len(assignable)
    for index, shot in enumerate(assignable):
        asset_id = str(shot.local_asset_id or shot.asset_id)
        if asset_id not in catalog:
            errors.append(f"Unbekannte Asset-ID im Intro: {asset_id}")
            continue

        role = "body"
        if index == 0:
            role = "opening"
        elif index == n - 1:
            role = "closing"

        start_pos = shot.start_anchor.position if shot.start_anchor else "start"
        end_pos = shot.end_anchor.position if shot.end_anchor else "end"
        vo_start = _vo_seconds_from_anchor(start_pos, vo_duration)
        vo_end = _vo_seconds_from_anchor(end_pos, vo_duration)
        if vo_end <= vo_start:
            vo_end = min(vo_duration, vo_start + INTRO_MIN_SHOT_SEC)
            repairs.append(
                f"{shot.shot_id}: Ankerende ≤ Start — auf Mindestlänge im VO gesetzt."
            )

        if role == "opening":
            start = 0.0
            end = _seconds_to_frame(opening + vo_end, fps)
            # Opening muss mindestens die 4s Hold abdecken.
            if end < opening:
                end = audio_start
                repairs.append(
                    f"{shot.shot_id}: Opening auf {opening:.1f}s Hold verlängert."
                )
        elif role == "closing":
            start = _seconds_to_frame(opening + vo_start, fps)
            end = total
            if start >= audio_end:
                start = _seconds_to_frame(max(0.0, audio_end - INTRO_MIN_SHOT_SEC), fps)
                repairs.append(
                    f"{shot.shot_id}: Closing-Start in den VO-Schluss gezogen."
                )
        else:
            start = _seconds_to_frame(opening + vo_start, fps)
            end = _seconds_to_frame(opening + vo_end, fps)

        duration = end - start
        if duration < INTRO_MIN_SHOT_SEC:
            end = _seconds_to_frame(start + INTRO_MIN_SHOT_SEC, fps)
            duration = end - start
            repairs.append(
                f"{shot.shot_id}: unter Intro-Mindestlänge — auf "
                f"{INTRO_MIN_SHOT_SEC}s verlängert."
            )
        if duration > INTRO_MAX_SHOT_SEC:
            end = _seconds_to_frame(start + INTRO_MAX_SHOT_SEC, fps)
            duration = end - start
            repairs.append(f"{shot.shot_id}: über Maximallänge — gekürzt.")

        media_duration = catalog[asset_id].get("duration_seconds")
        # Stills / kaputte Probe-Werte (z. B. JPEG ≈ 0s) wie unbekannte Dauer behandeln.
        if media_duration is not None and float(media_duration) < INTRO_MIN_SHOT_SEC:
            media_duration = None
        if media_duration is not None and media_duration > 0 and media_duration < duration:
            # Intro: Source auf Mediendauer, Timeline behalten (Hold in Resolve).
            repairs.append(
                f"{shot.shot_id}: Asset kürzer als Shot "
                f"({media_duration:.2f}s < {duration:.2f}s) — "
                "Source auf Mediendauer, Timeline behalten (Hold)."
            )
            source_start = 0.0
            source_end = float(media_duration)
        elif media_duration is None or media_duration <= 0:
            source_start = 0.0
            source_end = duration
        else:
            usable = float(media_duration)
            if duration > usable + 1e-6:
                source_start = 0.0
                source_end = usable
                repairs.append(
                    f"{shot.shot_id}: Timeline länger als Media — "
                    "Source 0…Ende, restlicher Hold in Resolve ok."
                )
            else:
                source_start = max(0.0, (usable - duration) / 2.0)
                source_end = source_start + duration

        resolved_shots.append(
            ResolvedShot(
                shot_id=shot.shot_id,
                asset_id=asset_id,
                timeline_start_seconds=start,
                timeline_end_seconds=end,
                source_start_seconds=round(source_start, 6),
                source_end_seconds=round(source_end, 6),
                editorial_function=shot.narrative_function or role,
                may_overlap_pause=False,
            )
        )

    # Überlappungen entschärfen (Intro-Schnitt soll dicht, aber nicht overlapping).
    ordered = sorted(resolved_shots, key=lambda s: (s.timeline_start_seconds, s.shot_id))
    cleaned: list[ResolvedShot] = []
    for shot in ordered:
        if cleaned and shot.timeline_start_seconds < cleaned[-1].timeline_end_seconds - 1e-6:
            prev = cleaned[-1]
            # Schnittpunkt: vorheriges Ende auf neuen Start ziehen wenn möglich.
            if shot.timeline_start_seconds - prev.timeline_start_seconds >= INTRO_MIN_SHOT_SEC:
                cleaned[-1] = prev.model_copy(
                    update={"timeline_end_seconds": shot.timeline_start_seconds}
                )
                repairs.append(
                    f"Überlappung {prev.shot_id}/{shot.shot_id}: "
                    f"{prev.shot_id} gekürzt."
                )
            else:
                shot = shot.model_copy(
                    update={"timeline_start_seconds": prev.timeline_end_seconds}
                )
                if shot.timeline_end_seconds - shot.timeline_start_seconds < INTRO_MIN_SHOT_SEC:
                    shot = shot.model_copy(
                        update={
                            "timeline_end_seconds": _seconds_to_frame(
                                shot.timeline_start_seconds + INTRO_MIN_SHOT_SEC,
                                fps,
                            )
                        }
                    )
                repairs.append(
                    f"Überlappung {prev.shot_id}/{shot.shot_id}: "
                    f"{shot.shot_id} nach hinten geschoben."
                )
        cleaned.append(shot)

    # Closing muss bis total reichen.
    if cleaned:
        last = cleaned[-1]
        if last.timeline_end_seconds < total - 1e-6:
            cleaned[-1] = last.model_copy(update={"timeline_end_seconds": total})
            repairs.append(
                f"{last.shot_id}: Closing bis {total:.2f}s "
                f"(+{closing:.1f}s nach VO) verlängert."
            )
        # Opening muss bei 0 starten.
        first = cleaned[0]
        if first.timeline_start_seconds > 1e-6:
            cleaned[0] = first.model_copy(update={"timeline_start_seconds": 0.0})
            repairs.append(f"{first.shot_id}: Opening auf Timeline-Start 0 gesetzt.")

    audio_segments = [
        ResolvedAudioSegment(
            segment_id=INTRO_SEGMENT_ID,
            audio_path=audio_path,
            timeline_start_seconds=audio_start,
            timeline_end_seconds=audio_end,
            pause_after_seconds=closing,
        )
    ]
    if cleaned:
        total = max(total, cleaned[-1].timeline_end_seconds)

    document = ResolvedTimelineDocument(
        schema_version="enhanced-intro-resolved-timeline-v1",
        script_version=cut.script_version,
        fps=fps,
        total_duration_seconds=round(total, 6),
        audio_segments=audio_segments,
        shots=cleaned,
        repairs=repairs,
        errors=errors,
    )
    write_json(intro_resolved_timeline_path(project), document)
    write_json(
        intro_repair_log_path(project),
        {"repairs": repairs, "errors": errors},
    )
    if errors:
        raise TimelineResolveError("; ".join(errors))
    return document


def export_intro_otio(
    project: Project,
    *,
    basename: str = "enhanced_intro",
) -> Path:
    """Separater OTIO-Export nur aus der Intro-resolved Timeline."""
    assert_enhanced_work_root(project)
    resolved = load_model(
        intro_resolved_timeline_path(project), ResolvedTimelineDocument
    )
    if resolved is None:
        raise EnhancedOtioExportError(
            "Intro-Timeline fehlt — zuerst „Intro: Python Timing“ ausführen."
        )
    if resolved.errors:
        raise EnhancedOtioExportError(
            "Intro-Timeline enthält Fehler: " + "; ".join(resolved.errors)
        )

    # Temporär: Export-Helfer erwartet resolved_timeline_path — wir schreiben
    # kurz die Intro-Timeline dorthin nicht um; eigene Export-Logik via
    # gemeinsamer Builder. Hier: Pfad-Swap durch Schreiben einer Kopie ist
    # riskant. Stattdessen rufen wir die bestehende Funktion mit Monkeypatch
    # der Path-Resolution… Sauberer: inline reuse durch Parameter.
    return _export_otio_document(project, resolved, basename=basename)


def _export_otio_document(
    project: Project,
    resolved: ResolvedTimelineDocument,
    *,
    basename: str,
) -> Path:
    """OTIO aus einem ResolvedTimelineDocument (Intro oder Body)."""
    from otio_app.services.without_voiceover_enhanced import otio_export_service as oes

    # Nutzt die gleiche Fail-Closed-Logik wie der Body-Export, indem wir
    # vorübergehend die Body-Resolved-Datei nicht anfassen und die Clip-
    # Konstruktion hier spiegeln — Import der privaten Helfer.
    import opentimelineio as otio

    from otio_app.services.without_voiceover_enhanced.paths import exports_dir

    fps = resolved.fps or float(project.fps)
    timeline = otio.schema.Timeline(name=f"{project.name} enhanced intro")
    video_track = otio.schema.Track(name="Video", kind=otio.schema.TrackKind.Video)
    audio_track = otio.schema.Track(name="Narration", kind=otio.schema.TrackKind.Audio)

    cursor = 0.0
    for shot in sorted(resolved.shots, key=lambda s: s.timeline_start_seconds):
        if shot.timeline_start_seconds > cursor + 1e-6:
            gap = shot.timeline_start_seconds - cursor
            video_track.append(
                otio.schema.Gap(
                    source_range=otio.opentime.TimeRange(
                        start_time=otio.opentime.RationalTime(0, fps),
                        duration=otio.opentime.RationalTime(gap * fps, fps),
                    )
                )
            )
        media_path = oes._media_path_for_asset(project, shot.asset_id)
        duration = shot.timeline_end_seconds - shot.timeline_start_seconds
        clip = otio.schema.Clip(
            name=shot.shot_id,
            media_reference=otio.schema.ExternalReference(target_url=media_path),
            source_range=otio.opentime.TimeRange(
                start_time=otio.opentime.RationalTime(
                    shot.source_start_seconds * fps, fps
                ),
                duration=otio.opentime.RationalTime(duration * fps, fps),
            ),
        )
        video_track.append(clip)
        cursor = shot.timeline_end_seconds

    audio_cursor = 0.0
    for segment in resolved.audio_segments:
        if segment.timeline_start_seconds > audio_cursor + 1e-6:
            gap = segment.timeline_start_seconds - audio_cursor
            audio_track.append(
                otio.schema.Gap(
                    source_range=otio.opentime.TimeRange(
                        start_time=otio.opentime.RationalTime(0, fps),
                        duration=otio.opentime.RationalTime(gap * fps, fps),
                    )
                )
            )
        audio_path = oes._assert_local_media_reference(
            segment.audio_path, label=f"Audio {segment.segment_id}"
        )
        duration = segment.timeline_end_seconds - segment.timeline_start_seconds
        clip = otio.schema.Clip(
            name=segment.segment_id,
            media_reference=otio.schema.ExternalReference(target_url=audio_path),
            source_range=otio.opentime.TimeRange(
                start_time=otio.opentime.RationalTime(0, fps),
                duration=otio.opentime.RationalTime(duration * fps, fps),
            ),
        )
        audio_track.append(clip)
        audio_cursor = segment.timeline_end_seconds
        if segment.pause_after_seconds > 0:
            audio_track.append(
                otio.schema.Gap(
                    source_range=otio.opentime.TimeRange(
                        start_time=otio.opentime.RationalTime(0, fps),
                        duration=otio.opentime.RationalTime(
                            segment.pause_after_seconds * fps, fps
                        ),
                    )
                )
            )
            audio_cursor += segment.pause_after_seconds

    timeline.tracks.append(video_track)
    timeline.tracks.append(audio_track)

    for url in oes._collect_target_urls(timeline):
        if oes.is_http_url(url):
            raise EnhancedOtioExportError(
                f"OTIO enthält verbotene Web-URL als Medienreferenz: {url}"
            )

    out_dir = exports_dir(project)
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{basename}.otio"
    otio.adapters.write_to_file(timeline, str(out_path))
    return out_path
