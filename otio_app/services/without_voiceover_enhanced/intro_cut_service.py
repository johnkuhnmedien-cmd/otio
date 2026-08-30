"""Intro-only Unified Cut: gebündeltes Inventar, strong-only, separater OTIO."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from otio_app.models import Project
from otio_app.services.without_voiceover_enhanced.cut_plan_options import (
    DEFAULT_INTRO_VOICEOVER_POSTROLL_MAX_SEC,
    DEFAULT_INTRO_VOICEOVER_POSTROLL_MIN_SEC,
    DEFAULT_INTRO_VOICEOVER_POSTROLL_SEC,
    DEFAULT_INTRO_VOICEOVER_PREROLL_SEC,
    CutPlanOptions,
    intro_hold_timings,
    load_cut_plan_options,
)
from otio_app.services.without_voiceover_enhanced.cut_plan_service import (
    _local_assets_payload,
    generate_unified_cut_for_folder,
)
from otio_app.services.without_voiceover_enhanced.intro_script_bridge import (
    ENHANCED_INTRO_FOLDER_NAME,
    is_intro_folder_name,
)
from otio_app.services.without_voiceover_enhanced.io_utils import load_model, write_json
from otio_app.services.without_voiceover_enhanced.models import (
    CutSlot,
    PauseDirective,
    ResolvedAudioSegment,
    ResolvedChapterEnvelope,
    ResolvedShot,
    ResolvedTimelineDocument,
    UnifiedCutPlanDocument,
)
from otio_app.services.without_voiceover_enhanced.otio_export_service import (
    EnhancedOtioExportError,
    export_otio_from_resolved_timeline,
)
from otio_app.services.without_voiceover_enhanced.paths import (
    assert_enhanced_work_root,
    coverage_gaps_path,
    cut_dir,
    resolved_timeline_path,
    unified_cut_plan_path,
)
from otio_app.services.without_voiceover_enhanced.script_lock_service import (
    require_locked_script,
)

# Defaults = Cut-Plan-Settings (UI). Konstanten bleiben für Tests/Import-Kompatibilität.
INTRO_OPENING_HOLD_SEC = DEFAULT_INTRO_VOICEOVER_PREROLL_SEC
INTRO_CLOSING_HOLD_DEFAULT_SEC = DEFAULT_INTRO_VOICEOVER_POSTROLL_SEC
INTRO_CLOSING_HOLD_MIN_SEC = DEFAULT_INTRO_VOICEOVER_POSTROLL_MIN_SEC
INTRO_CLOSING_HOLD_MAX_SEC = DEFAULT_INTRO_VOICEOVER_POSTROLL_MAX_SEC

INTRO_BUNDLED_INVENTORY_FILENAME = "intro_bundled_inventory.json"
INTRO_RESOLVED_TIMELINE_FILENAME = "intro_resolved_timeline.json"
INTRO_CUT_PLAN_FILENAME = "intro_unified_cut_plan.json"


class IntroCutError(RuntimeError):
    pass


@dataclass
class IntroCutGenerateResult:
    plan: UnifiedCutPlanDocument
    slot_count: int
    gap_count: int
    bundled_inventory: dict[str, Any]


def intro_bundled_inventory_path(project: Project) -> Path:
    return cut_dir(project) / INTRO_BUNDLED_INVENTORY_FILENAME


def intro_resolved_timeline_path(project: Project) -> Path:
    return cut_dir(project) / INTRO_RESOLVED_TIMELINE_FILENAME


def intro_unified_cut_plan_path(project: Project) -> Path:
    return cut_dir(project) / INTRO_CUT_PLAN_FILENAME


def clamp_intro_closing_hold(
    value: float | None,
    *,
    default: float = INTRO_CLOSING_HOLD_DEFAULT_SEC,
    min_sec: float = INTRO_CLOSING_HOLD_MIN_SEC,
    max_sec: float = INTRO_CLOSING_HOLD_MAX_SEC,
    options: CutPlanOptions | None = None,
) -> float:
    if options is not None:
        _preroll, default, min_sec, max_sec = intro_hold_timings(options)
        del _preroll
    hold = default if value is None else float(value)
    lo = min(float(min_sec), float(max_sec))
    hi = max(float(min_sec), float(max_sec))
    return max(lo, min(hi, hold))


def build_bundled_inventory_for_intro(
    project: Project,
    *,
    include_middle_frames: bool = False,
) -> dict[str, Any]:
    """Alle Kapitel-Slim-Inventare als eine gebündelte JSON-Struktur."""
    assert_enhanced_work_root(project)
    chapters: dict[str, list[dict[str, Any]]] = {}
    all_assets: list[dict[str, Any]] = []
    for folder in list(project.selected_asset_subdirs or []):
        if not folder or is_intro_folder_name(folder):
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
        # Persistenz/Debug: flache Liste. Für LLM-Prompts bitte
        # format_bundled_inventory_for_prompt() nutzen (ohne Duplikat).
        "all_assets": all_assets,
    }


_INTRO_DESC_MAX_CHARS = 280
_INTRO_TAG_LIMIT = 3
_INTRO_COLOR_LIMIT = 2


def _intro_limited_strings(values: Any, *, limit: int) -> list[str]:
    if not isinstance(values, (list, tuple)):
        return []
    seen: set[str] = set()
    out: list[str] = []
    for raw in values:
        text = str(raw or "").strip()
        if not text:
            continue
        key = text.casefold()
        if key in seen:
            continue
        seen.add(key)
        out.append(text)
        if len(out) >= limit:
            break
    return out


def _slim_intro_asset_row(row: dict[str, Any]) -> dict[str, Any]:
    """Minimale Asset-Zeile für Intro-LLM (Kosten-/Disconnect-Schutz)."""
    asset_id = str(row.get("local_asset_id") or row.get("asset_id") or "").strip()
    out: dict[str, Any] = {
        "local_asset_id": asset_id,
        "folder": row.get("folder") or "",
        "file": row.get("file") or "",
        "media_type": row.get("media_type") or "",
    }
    if row.get("duration_seconds") is not None:
        out["duration_seconds"] = row.get("duration_seconds")
    desc = " ".join(str(row.get("description") or "").split())
    if len(desc) > _INTRO_DESC_MAX_CHARS:
        desc = desc[: _INTRO_DESC_MAX_CHARS - 3].rstrip() + "..."
    if desc:
        out["description"] = desc
    tags = _intro_limited_strings(row.get("tags"), limit=_INTRO_TAG_LIMIT)
    if tags:
        out["tags"] = tags
    for key in ("motion", "framing", "shot_scale", "people"):
        value = row.get(key)
        if value not in (None, "", [], {}):
            out[key] = value
    # Intro: nur kompakte Quality-/Look-Teilmenge (kein composition/clarity/…).
    quality_src = row.get("quality")
    if isinstance(quality_src, dict):
        quality: dict[str, Any] = {}
        for key in ("technical", "appeal", "hero", "defect"):
            value = quality_src.get(key)
            if value is None or value == "":
                continue
            try:
                quality[key] = int(value)
            except (TypeError, ValueError):
                continue
        if quality:
            out["quality"] = quality
    look_src = row.get("look")
    if isinstance(look_src, dict):
        look: dict[str, Any] = {}
        temperature = str(look_src.get("temperature") or "").strip()
        if temperature and temperature != "unknown":
            look["temperature"] = temperature
        colors = _intro_limited_strings(
            look_src.get("colors"), limit=_INTRO_COLOR_LIMIT
        )
        if colors:
            look["colors"] = colors
        if look:
            out["look"] = look
    return out


def format_bundled_inventory_for_prompt(bundled: dict[str, Any] | None) -> str:
    """Kompaktes Intro-Inventar für den LLM-Prompt.

    - nur ``chapters`` (kein ``all_assets``-Duplikat)
    - gekürzte Beschreibungen
    - ohne Pretty-Print

    Früherer Dump (chapters + all_assets, indent=2) konnte bei ~37 Kapiteln
    leicht in den mehrstelligen Dollar-Bereich pro fehlgeschlagenem Call laufen.
    """
    import json

    src = bundled or {}
    raw_chapters = src.get("chapters") or {}
    if not isinstance(raw_chapters, dict):
        raw_chapters = {}
    chapters: dict[str, list[dict[str, Any]]] = {}
    asset_count = 0
    for folder, rows in raw_chapters.items():
        if not isinstance(rows, list):
            continue
        slim_rows = [
            _slim_intro_asset_row(row)
            for row in rows
            if isinstance(row, dict) and (row.get("local_asset_id") or row.get("asset_id"))
        ]
        chapters[str(folder)] = slim_rows
        asset_count += len(slim_rows)
    payload = {
        "schema_version": src.get("schema_version")
        or "enhanced-intro-bundled-inventory-v1",
        "chapter_count": len(chapters),
        "asset_count": asset_count,
        "chapters": chapters,
    }
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def _is_intro_id(value: str, *, slug: str) -> bool:
    text = str(value or "")
    prefix = f"{slug}_"
    return text.startswith(prefix) or text.lower().startswith("intro_")


def _is_intro_pause(directive: PauseDirective, *, slug: str) -> bool:
    """Pause gehört zum Intro, wenn Segment- oder Sentence-ID Intro-Präfix hat."""
    segment = str(directive.after_segment_id or "")
    sentence = str(directive.after_sentence_id or "")
    return _is_intro_id(segment, slug=slug) or _is_intro_id(sentence, slug=slug)


def intro_envelope_asset_errors(plan: UnifiedCutPlanDocument) -> list[str]:
    """Fail-closed Checks für LLM-Opener/Closing-Hold (keine Content-Kopien)."""
    errors: list[str] = []
    opener = str(plan.intro_opener_asset_id or "").strip()
    closing = str(plan.intro_closing_asset_id or "").strip()
    fallback = str(plan.closing_fallback_asset_id or "").strip()
    slot_assets = {
        str(slot.local_asset_id or "").strip()
        for slot in plan.slots
        if str(slot.asset_fit or "").strip().lower() == "strong"
        and str(slot.local_asset_id or "").strip()
    }
    if not opener:
        errors.append(
            "Intro: intro_opener_asset_id fehlt — LLM muss den semantisch "
            "passendsten Opener wählen (nicht First-Slot-Kopie)."
        )
    if not closing:
        errors.append(
            "Intro: intro_closing_asset_id fehlt — LLM muss den semantisch "
            "passendsten Closing-Hold wählen (nicht Last-Slot-Kopie)."
        )
    if opener and closing and opener == closing:
        errors.append(
            "Intro: intro_opener_asset_id und intro_closing_asset_id müssen "
            f"verschieden sein (beide {opener!r})."
        )
    for label, asset_id in (
        ("intro_opener_asset_id", opener),
        ("intro_closing_asset_id", closing),
    ):
        if asset_id and asset_id in slot_assets:
            errors.append(
                f"Intro: {label}={asset_id!r} darf keinem VO-Slot "
                "(asset_fit=strong) entsprechen."
            )
        if asset_id and fallback and asset_id == fallback:
            errors.append(
                f"Intro: {label}={asset_id!r} darf nicht gleich "
                "closing_fallback_asset_id sein."
            )
    return errors


def enforce_intro_strong_only(
    plan: UnifiedCutPlanDocument,
    *,
    options: CutPlanOptions | None = None,
) -> UnifiedCutPlanDocument:
    """Nur strong behalten; acceptable/weak → none + Gap-Felder."""
    updated_slots: list[CutSlot] = []
    for index, slot in enumerate(plan.slots, start=1):
        fit = str(slot.asset_fit or "").strip().lower()
        asset_id = slot.local_asset_id
        if asset_id and fit == "strong":
            updated_slots.append(
                slot.model_copy(
                    update={
                        "asset_fit": "strong",
                        "coverage_gap_id": None,
                    }
                )
            )
            continue
        gap_id = slot.coverage_gap_id or f"Intro_gap_auto_{index:03d}"
        reason = (
            slot.asset_fit_reason
            or f"Intro verlangt asset_fit=strong; erhalten: {fit or 'none'}."
        )
        updated_slots.append(
            slot.model_copy(
                update={
                    "local_asset_id": None,
                    "asset_fit": "none",
                    "asset_fit_reason": reason,
                    "coverage_gap_id": gap_id,
                    "needed_visual": slot.needed_visual or slot.visual_intent or reason,
                    "search_concepts": list(slot.search_concepts)
                    or [
                        (slot.visual_intent or "intro visual").strip()[:40]
                        or "intro visual"
                    ],
                }
            )
        )
    preroll, _post_default, _lo, _hi = intro_hold_timings(options)
    postroll = clamp_intro_closing_hold(
        plan.voiceover_postroll_sec, options=options
    )
    opener = str(plan.intro_opener_asset_id or "").strip() or None
    closing = str(plan.intro_closing_asset_id or "").strip() or None
    return plan.model_copy(
        update={
            "slots": updated_slots,
            "voiceover_preroll_sec": preroll,
            "voiceover_postroll_sec": postroll,
            "intro_opener_asset_id": opener,
            "intro_closing_asset_id": closing,
        }
    )


def split_intro_from_unified(
    plan: UnifiedCutPlanDocument,
    *,
    intro_slug: str = "Intro",
) -> tuple[UnifiedCutPlanDocument | None, UnifiedCutPlanDocument | None]:
    """Teilt Unified-Plan in Intro-Fragment und Rest-Kapitel.

    Nach E2E-4-Merge fehlt die erste Body-Grenze (geteilt mit Intro-Ende) —
    sie wird aus ``start_sentence_id`` / letzter Intro-Grenze rekonstruiert.
    """
    from otio_app.services.without_voiceover_enhanced.models import CutBoundary

    intro_bounds = [
        b for b in plan.boundaries if _is_intro_id(b.cut_id, slug=intro_slug)
    ]
    intro_slots = [
        s for s in plan.slots if _is_intro_id(s.slot_id, slug=intro_slug)
    ]
    body_bounds = [
        b for b in plan.boundaries if not _is_intro_id(b.cut_id, slug=intro_slug)
    ]
    body_slots = [
        s for s in plan.slots if not _is_intro_id(s.slot_id, slug=intro_slug)
    ]
    intro_doc = None
    body_doc = None
    if intro_bounds and intro_slots and len(intro_bounds) == len(intro_slots) + 1:
        intro_doc = UnifiedCutPlanDocument(
            script_version=plan.script_version,
            pause_directives=[
                p
                for p in plan.pause_directives
                if _is_intro_pause(p, slug=intro_slug)
            ],
            boundaries=intro_bounds,
            slots=intro_slots,
            voiceover_preroll_sec=plan.voiceover_preroll_sec,
            voiceover_postroll_sec=plan.voiceover_postroll_sec,
            closing_fallback_asset_id=plan.closing_fallback_asset_id,
            intro_opener_asset_id=plan.intro_opener_asset_id,
            intro_closing_asset_id=plan.intro_closing_asset_id,
        )
    if body_slots:
        if len(body_bounds) != len(body_slots) + 1:
            # Geteilte Grenze nach Intro wiederherstellen.
            sentence = ""
            if body_slots[0].start_sentence_id:
                sentence = str(body_slots[0].start_sentence_id)
            elif intro_bounds:
                sentence = str(intro_bounds[-1].sentence_id or "")
            elif body_bounds:
                sentence = str(body_bounds[0].sentence_id or "")
            if sentence:
                shared = CutBoundary(
                    cut_id=f"{intro_slug}_shared_cut",
                    sentence_id=sentence,
                    position="start",
                    alignment=(
                        intro_bounds[-1].alignment
                        if intro_bounds
                        else "sentence_boundary"
                    ),
                    offset_seconds=(
                        intro_bounds[-1].offset_seconds if intro_bounds else None
                    ),
                )
                body_bounds = [shared, *body_bounds]
        if len(body_bounds) == len(body_slots) + 1:
            body_doc = UnifiedCutPlanDocument(
                script_version=plan.script_version,
                pause_directives=[
                    p
                    for p in plan.pause_directives
                    if not _is_intro_pause(p, slug=intro_slug)
                ],
                boundaries=body_bounds,
                slots=body_slots,
                voiceover_preroll_sec=plan.voiceover_preroll_sec,
                voiceover_postroll_sec=plan.voiceover_postroll_sec,
            )
    return intro_doc, body_doc


def merge_intro_and_body_plans(
    *,
    intro: UnifiedCutPlanDocument,
    body: UnifiedCutPlanDocument | None,
    script_version: str,
) -> UnifiedCutPlanDocument:
    """Intro zuerst, dann Kapitel (E2E-4: geteilte Grenze → start_sentence_id)."""
    if body is None or not body.boundaries or not body.slots:
        return intro.model_copy(update={"script_version": script_version})
    next_first = body.boundaries[0]
    next_slots = list(body.slots)
    first_slot = next_slots[0].model_copy(
        update={
            "start_sentence_id": str(next_first.sentence_id or "").strip() or None
        }
    )
    next_slots[0] = first_slot
    return UnifiedCutPlanDocument(
        script_version=script_version,
        pause_directives=[*intro.pause_directives, *body.pause_directives],
        boundaries=[*intro.boundaries, *body.boundaries[1:]],
        slots=[*intro.slots, *next_slots],
        voiceover_preroll_sec=intro.voiceover_preroll_sec,
        voiceover_postroll_sec=intro.voiceover_postroll_sec,
        closing_fallback_asset_id=intro.closing_fallback_asset_id,
        closing_fallback_by_chapter=dict(body.closing_fallback_by_chapter or {}),
        intro_opener_asset_id=intro.intro_opener_asset_id,
        intro_closing_asset_id=intro.intro_closing_asset_id,
    )


def invalidate_intro_resolved_timeline(project: Project) -> bool:
    """Löscht veraltetes Intro-Timing nach neuem LLM-Plan.

    Sonst exportiert „Intro: OTIO“ weiter die alte ``intro_resolved_timeline.json``
    (z. B. 10 Shots), obwohl der Plan schon 7 Slots hat.
    """
    path = intro_resolved_timeline_path(project)
    if not path.is_file():
        return False
    try:
        path.unlink()
    except OSError:
        return False
    return True


def intro_plan_matches_locked_script(
    project: Project,
    plan: UnifiedCutPlanDocument | None,
) -> bool:
    """False wenn Intro-Plan zu einer anderen Skriptversion gehört."""
    if plan is None or not plan.slots:
        return False
    from otio_app.services.without_voiceover_enhanced.chapter_cut_service import (
        _artifact_matches_locked_script_version,
    )

    return _artifact_matches_locked_script_version(
        project, getattr(plan, "script_version", None)
    )


def intro_resolved_matches_plan(
    plan: UnifiedCutPlanDocument | None,
    resolved: ResolvedTimelineDocument | None,
    *,
    project: Project | None = None,
) -> bool:
    """True wenn Resolved zur aktuellen Slot-Anzahl des Intro-Plans passt.

    Envelope-Shots außerhalb der Slotkette (Shortfall-Tails; Map-Opener
    falls jemals gesetzt) zählen nicht als zusätzliche Plan-Slots.
    Mit ``project`` zusätzlich Skriptversions-Check (nach Script-Regen).
    """
    if plan is None or resolved is None:
        return False
    if project is not None:
        from otio_app.services.without_voiceover_enhanced.chapter_cut_service import (
            _artifact_matches_locked_script_version,
        )

        if not _artifact_matches_locked_script_version(
            project, getattr(plan, "script_version", None)
        ):
            return False
        if not _artifact_matches_locked_script_version(
            project, getattr(resolved, "script_version", None)
        ):
            return False
    from otio_app.services.without_voiceover_enhanced.chapter_cut_service import (
        _is_non_plan_envelope_shot,
        production_blocking_placeholder_labels,
    )

    if production_blocking_placeholder_labels(resolved):
        return False
    parent_shots = [
        shot
        for shot in resolved.shots
        if not _is_non_plan_envelope_shot(shot)
    ]
    return len(parent_shots) == len(plan.slots)


def _reset_open_intro_gaps(project: Project) -> list[str]:
    """Offene Gaps des vorherigen Intro-Cuts räumen (wie bei Kapiteln)."""
    from otio_app.services.without_voiceover_enhanced.chapter_cut_service import (
        chapter_gap_ids,
    )
    from otio_app.services.without_voiceover_enhanced.gap_reset_service import (
        reset_open_coverage_gaps,
    )

    previous = load_model(intro_unified_cut_plan_path(project), UnifiedCutPlanDocument)
    gap_ids = chapter_gap_ids(previous)
    if not gap_ids:
        return []
    return reset_open_coverage_gaps(project, gap_ids=gap_ids).removed_gap_ids


def persist_intro_unified_plan(
    project: Project,
    intro_plan: UnifiedCutPlanDocument,
) -> UnifiedCutPlanDocument:
    """Schreibt Intro-Plan und merged ihn in den globalen Unified Plan."""
    from otio_app.services.without_voiceover_enhanced.gap_search_concepts import (
        enrich_coverage_search_concepts,
    )
    from otio_app.services.without_voiceover_enhanced.unified_cut_plan import (
        unified_to_rough,
    )
    from otio_app.services.without_voiceover_enhanced.paths import (
        pause_directives_path,
        rough_cut_plan_path,
    )

    locked = require_locked_script(project)
    options = load_cut_plan_options(project)
    intro_plan = enforce_intro_strong_only(intro_plan, options=options)
    intro_plan = intro_plan.model_copy(update={"pause_directives": []})
    _reset_open_intro_gaps(project)
    write_json(intro_unified_cut_plan_path(project), intro_plan)
    # Alter Resolved-Stand darf OTIO nicht mehr antreiben.
    invalidate_intro_resolved_timeline(project)

    existing = load_model(unified_cut_plan_path(project), UnifiedCutPlanDocument)
    body = None
    if existing is not None:
        _old_intro, body = split_intro_from_unified(existing)
    merged = merge_intro_and_body_plans(
        intro=intro_plan,
        body=body,
        script_version=locked.script_version,
    )
    merged = merged.model_copy(update={"pause_directives": []})
    rough, coverage = unified_to_rough(merged)
    coverage = enrich_coverage_search_concepts(project, coverage, plan=merged)
    from otio_app.services.without_voiceover_enhanced.gap_status_service import (
        carry_over_user_confirmed_weak,
    )
    from otio_app.services.without_voiceover_enhanced.models import (
        CoverageGapsDocument,
    )

    previous_coverage = load_model(coverage_gaps_path(project), CoverageGapsDocument)
    coverage = carry_over_user_confirmed_weak(coverage, previous_coverage)
    write_json(unified_cut_plan_path(project), merged)
    write_json(rough_cut_plan_path(project), rough)
    from otio_app.services.without_voiceover_enhanced.coverage_gap_external_export import (
        persist_coverage_gaps,
    )

    persist_coverage_gaps(project, coverage)
    write_json(pause_directives_path(project), {"directives": []})
    return merged


def generate_intro_unified_cut(
    project: Project,
    *,
    provider: str = "openai",
    model: str = "gpt-5.6-terra",
    llm_callable: Callable[..., Any] | None = None,
) -> IntroCutGenerateResult:
    """Nur Intro: Unified-LLM mit gebündeltem Inventar + strong-only."""
    assert_enhanced_work_root(project)
    locked = require_locked_script(project)
    intro_names = [
        name
        for name in (
            # list via segments
            {seg.folder_name for seg in locked.segments if is_intro_folder_name(seg.folder_name)}
        )
        if name
    ]
    folder_name = intro_names[0] if intro_names else ENHANCED_INTRO_FOLDER_NAME
    if not any(is_intro_folder_name(s.folder_name) for s in locked.segments):
        raise IntroCutError(
            "Kein Intro-Segment im Locked Script — zuerst Intro bestätigen "
            "und in Audio spiegeln."
        )

    bundled = build_bundled_inventory_for_intro(project)
    if not bundled.get("all_assets"):
        raise IntroCutError(
            "Gebündeltes Inventar ist leer — zuerst Slim-Inventare der Kapitel."
        )
    write_json(intro_bundled_inventory_path(project), bundled)

    result = generate_unified_cut_for_folder(
        project,
        folder_name,
        provider=provider,
        model=model,
        llm_callable=llm_callable,
    )
    if result.status != "PASS" or result.plan is None:
        raise IntroCutError(result.error or "Intro Unified Cut fehlgeschlagen.")

    options = load_cut_plan_options(project)
    plan = enforce_intro_strong_only(result.plan, options=options)
    envelope_errors = intro_envelope_asset_errors(plan)
    if envelope_errors:
        raise IntroCutError(" · ".join(envelope_errors))
    persist_intro_unified_plan(project, plan)
    gap_count = sum(1 for s in plan.slots if str(s.asset_fit) == "none")
    return IntroCutGenerateResult(
        plan=plan,
        slot_count=len(plan.slots),
        gap_count=gap_count,
        bundled_inventory=bundled,
    )


def _is_intro_shot(shot: ResolvedShot) -> bool:
    if is_intro_folder_name(shot.folder_name) or is_intro_folder_name(shot.chapter_id):
        return True
    sid = str(shot.shot_id or "").lower()
    return sid.startswith("intro_") or sid.startswith("intro/")


def _is_intro_audio(segment: ResolvedAudioSegment) -> bool:
    sid = str(segment.segment_id or "").lower()
    return (
        sid.startswith("intro")
        or "intro_segment" in sid
        or sid.startswith("intro_")
    )


def resolve_intro_timeline(
    project: Project,
    *,
    provider: str = "openai",
    model: str = "gpt-5.6-terra",
    llm_callable: Callable[..., Any] | None = None,
) -> ResolvedTimelineDocument:
    """Intro-only Python Timing aus ``intro_unified_cut_plan.json``.

    Nutzt den Intro-Plan (ohne Cut-Plan shot_min) und Intro-Hold-Settings
    (Vorlauf / Nachlauf min–max). Schreibt nur ``intro_resolved_timeline.json``
    — die Gesamt-Timeline bleibt unverändert.
    """
    del provider, model, llm_callable  # API-Kompatibilität zur UI.
    from otio_app.services.without_voiceover_enhanced.unified_timeline_service import (
        UnifiedTimelineError,
        resolve_unified_timeline,
    )

    assert_enhanced_work_root(project)
    plan = load_model(intro_unified_cut_plan_path(project), UnifiedCutPlanDocument)
    if plan is None or not plan.slots:
        raise IntroCutError(
            "Intro-Cut-Plan fehlt — zuerst „Intro: LLM Schnitt“."
        )
    options = load_cut_plan_options(project)
    preroll, _post_default, _lo, _hi = intro_hold_timings(options)
    plan = enforce_intro_strong_only(plan, options=options)
    envelope_errors = intro_envelope_asset_errors(plan)
    if envelope_errors:
        raise IntroCutError(" · ".join(envelope_errors))
    postroll = clamp_intro_closing_hold(
        plan.voiceover_postroll_sec, options=options
    )
    try:
        resolved = resolve_unified_timeline(
            project,
            plan=plan,
            allow_open_gaps=True,
            persist=False,
            include_chapter=is_intro_folder_name,
            preroll_override=preroll,
            postroll_override=postroll,
            # Intro nutzt eigenen Prompt (strong-only) — keine KF-Body-
            # Pflichtfelder (closing_fallback_asset_fit / …).
            apply_keyword_flow_rules=False,
        )
    except UnifiedTimelineError as exc:
        raise IntroCutError(str(exc)) from exc

    # Manual/Funnel Accepted in Intro-Placeholders mergen (Zeiten bleiben).
    from otio_app.services.without_voiceover_enhanced.gap_merge_service import (
        merge_export_ready_gaps_into_timeline,
    )

    try:
        resolved, _merge_report = merge_export_ready_gaps_into_timeline(
            project,
            timeline=resolved,
            unified=plan,
            require_closed_none=False,
            persist=False,
            persist_report=True,
        )
    except Exception:  # noqa: BLE001 — Merge soft
        pass

    intro_resolved = filter_resolved_timeline_to_intro(resolved)
    if not intro_resolved.shots and not intro_resolved.audio_segments:
        raise IntroCutError(
            "Intro-Timing lieferte keine Shots/Audio — Plan und Segment-Timings prüfen."
        )
    # Explizite Intro-Hüllen (nicht Kapitel-Settings).
    intro_resolved = intro_resolved.model_copy(
        update={
            "voiceover_preroll_sec": preroll,
            "voiceover_postroll_sec": postroll,
            "errors": list(resolved.errors or []),
            "repairs": list(resolved.repairs or []),
        }
    )
    from otio_app.services.without_voiceover_enhanced.chapter_cut_service import (
        _reconcile_chapter_envelope_ends,
        production_blocking_placeholder_labels,
    )

    intro_resolved = _reconcile_chapter_envelope_ends(intro_resolved)
    blockers = production_blocking_placeholder_labels(
        intro_resolved, folder_name="Intro", project=project
    )
    if blockers:
        preview = ", ".join(blockers[:8])
        more = f" (+{len(blockers) - 8})" if len(blockers) > 8 else ""
        write_json(intro_resolved_timeline_path(project), intro_resolved)
        raise IntroCutError(
            "Intro-Timing nicht exportfähig: Placeholder/Shortfall "
            f"({preview}{more}). Unter „Zu kurze Clips ansehen“ liegt das "
            "vorgesehene Video. Längeres Material, dann Timing erneut."
        )
    write_json(intro_resolved_timeline_path(project), intro_resolved)
    return intro_resolved


def filter_resolved_timeline_to_intro(
    resolved: ResolvedTimelineDocument,
) -> ResolvedTimelineDocument:
    """Schneidet Resolved Timeline auf Intro zu und nullt den Cursor auf 0."""
    intro_envs = [
        ch
        for ch in resolved.chapters
        if is_intro_folder_name(ch.folder_name or ch.chapter_id)
    ]

    if intro_envs:
        env = intro_envs[0]
        origin = float(env.chapter_video_start)
        end = float(env.chapter_video_end)
        segment_ids = {str(s) for s in (env.segment_ids or []) if s}
        shots = [
            s
            for s in resolved.shots
            if _is_intro_shot(s)
            or (
                s.timeline_start_seconds >= origin - 1e-3
                and s.timeline_start_seconds < end - 1e-3
                and (
                    is_intro_folder_name(s.folder_name)
                    or is_intro_folder_name(s.chapter_id)
                    or str(s.shot_id).lower().startswith("intro_")
                )
            )
        ]
        # Fallback: alles im Intro-Video-Fenster, wenn IDs fehlen.
        if not shots:
            shots = [
                s
                for s in resolved.shots
                if s.timeline_start_seconds >= origin - 1e-3
                and s.timeline_end_seconds <= end + 1e-3
            ]
        audios = [
            a
            for a in resolved.audio_segments
            if _is_intro_audio(a)
            or (segment_ids and str(a.segment_id) in segment_ids)
            or (
                a.timeline_start_seconds >= env.chapter_audio_start - 1e-3
                and a.timeline_end_seconds <= env.chapter_audio_end + 1e-3
            )
        ]
        # Nur Intro-Audio behalten (keine Kapitel-Clips durch Zeitfenster).
        audios = [a for a in audios if _is_intro_audio(a) or (
            segment_ids and str(a.segment_id) in segment_ids
        )]
        if not audios:
            audios = [
                a
                for a in resolved.audio_segments
                if a.timeline_start_seconds >= env.chapter_audio_start - 1e-3
                and a.timeline_end_seconds <= env.chapter_audio_end + 1e-3
            ]
        shifted_env = env.model_copy(
            update={
                "chapter_video_start": 0.0,
                "chapter_audio_start": round(env.chapter_audio_start - origin, 6),
                "chapter_audio_end": round(env.chapter_audio_end - origin, 6),
                "chapter_video_end": round(env.chapter_video_end - origin, 6),
            }
        )
        return _shift_resolved(resolved, shots, audios, [shifted_env], origin)

    shots = [s for s in resolved.shots if _is_intro_shot(s)]
    audios = [a for a in resolved.audio_segments if _is_intro_audio(a)]
    if not shots and not audios:
        return ResolvedTimelineDocument(
            schema_version=resolved.schema_version,
            script_version=resolved.script_version,
            fps=resolved.fps,
            total_duration_seconds=0.0,
            audio_segments=[],
            shots=[],
            chapters=[],
            voiceover_preroll_sec=INTRO_OPENING_HOLD_SEC,
            voiceover_postroll_sec=INTRO_CLOSING_HOLD_DEFAULT_SEC,
            repairs=[],
            errors=["Kein Intro-Kapitel in der aufgelösten Timeline."],
        )
    origin = 0.0
    if shots:
        origin = min(s.timeline_start_seconds for s in shots)
    elif audios:
        origin = min(a.timeline_start_seconds for a in audios)
    # Audio-Start als 0-Referenz bevorzugen, wenn Video früher beginnt (Vorlauf).
    if shots and audios:
        origin = min(
            min(s.timeline_start_seconds for s in shots),
            min(a.timeline_start_seconds for a in audios),
        )
    return _shift_resolved(resolved, shots, audios, [], origin)


def _shift_resolved(
    resolved: ResolvedTimelineDocument,
    shots: list[ResolvedShot],
    audios: list[ResolvedAudioSegment],
    chapters: list[ResolvedChapterEnvelope],
    origin: float,
) -> ResolvedTimelineDocument:
    shifted_shots = [
        s.model_copy(
            update={
                "timeline_start_seconds": round(s.timeline_start_seconds - origin, 6),
                "timeline_end_seconds": round(s.timeline_end_seconds - origin, 6),
            }
        )
        for s in shots
    ]
    shifted_audio = [
        a.model_copy(
            update={
                "timeline_start_seconds": round(a.timeline_start_seconds - origin, 6),
                "timeline_end_seconds": round(a.timeline_end_seconds - origin, 6),
            }
        )
        for a in audios
    ]
    total = 0.0
    if chapters:
        total = max(total, chapters[-1].chapter_video_end)
    if shifted_shots:
        total = max(total, max(s.timeline_end_seconds for s in shifted_shots))
    if shifted_audio:
        total = max(
            total,
            max(
                a.timeline_end_seconds + a.pause_after_seconds for a in shifted_audio
            ),
        )
    return ResolvedTimelineDocument(
        schema_version=resolved.schema_version,
        script_version=resolved.script_version,
        fps=resolved.fps,
        total_duration_seconds=round(total, 6),
        audio_segments=shifted_audio,
        shots=shifted_shots,
        chapters=chapters,
        voiceover_preroll_sec=INTRO_OPENING_HOLD_SEC,
        voiceover_postroll_sec=clamp_intro_closing_hold(
            resolved.voiceover_postroll_sec
        ),
        repairs=list(resolved.repairs or []),
        errors=[
            e
            for e in (resolved.errors or [])
            if "Intro" in e or "intro" in e.lower()
        ],
    )


def export_intro_otio(
    project: Project,
    *,
    basename: str = "enhanced_intro",
    allow_errors: bool = True,
) -> Path:
    """Separater OTIO-Export nur für Intro — lässt die Gesamt-Timeline unangetastet.

    Standard ``allow_errors=True``: Lücken/Placeholders im Intro sind ok.
    Nutzt ``intro_resolved_timeline.json`` nur wenn sie zum aktuellen
    ``intro_unified_cut_plan.json`` passt (gleiche Shot-/Slot-Anzahl).
    Sonst: frisch aus dem Plan resolven — verhindert OTIO mit altem 10-Shot-
    Timing nach neuem 7-Slot-LLM-Plan.
    """
    assert_enhanced_work_root(project)
    plan = load_model(intro_unified_cut_plan_path(project), UnifiedCutPlanDocument)
    intro_resolved = load_model(
        intro_resolved_timeline_path(project), ResolvedTimelineDocument
    )
    needs_resolve = (
        intro_resolved is None
        or (not intro_resolved.shots and not intro_resolved.audio_segments)
        or not intro_resolved_matches_plan(plan, intro_resolved, project=project)
    )
    if needs_resolve:
        # Frisches Intro-Timing aus dem aktuellen Plan.
        try:
            intro_resolved = resolve_intro_timeline(project)
        except IntroCutError:
            if plan is not None:
                raise EnhancedOtioExportError(
                    "Intro-Plan und Resolved passen nicht zusammen / Timing "
                    "fehlgeschlagen — bitte „Intro: Python Timing“ erneut. "
                    f"(Plan: {len(plan.slots)} Slots, Resolved: "
                    f"{len(intro_resolved.shots) if intro_resolved else 0} Shots)."
                ) from None
            full = load_model(resolved_timeline_path(project), ResolvedTimelineDocument)
            if full is None:
                raise EnhancedOtioExportError(
                    "Intro-Timeline fehlt — zuerst „Intro: LLM Schnitt“ und "
                    "„Intro: Python Timing“."
                ) from None
            intro_resolved = filter_resolved_timeline_to_intro(full)
            write_json(intro_resolved_timeline_path(project), intro_resolved)
    if not intro_resolved.shots and not intro_resolved.audio_segments:
        raise EnhancedOtioExportError(
            "Kein Intro in der Timeline — weder Kapitel „Intro“ noch "
            "Intro_*-Shots/Audio gefunden."
        )

    shot_ids = {s.shot_id for s in intro_resolved.shots}
    audio_ids = {a.segment_id for a in intro_resolved.audio_segments}
    # Defense: niemals Kapitel-Clips mitexportieren.
    leaked = [
        s.shot_id
        for s in intro_resolved.shots
        if not _is_intro_shot(s)
        and not any(
            is_intro_folder_name(ch.folder_name or ch.chapter_id)
            for ch in intro_resolved.chapters
        )
    ]
    if any(
        not _is_intro_audio(a)
        and not str(a.segment_id).lower().startswith("intro")
        for a in intro_resolved.audio_segments
    ):
        intro_resolved = intro_resolved.model_copy(
            update={
                "audio_segments": [
                    a for a in intro_resolved.audio_segments if _is_intro_audio(a)
                ]
            }
        )
        audio_ids = {a.segment_id for a in intro_resolved.audio_segments}

    if leaked:
        # Zu aggressives Zeitfenster — nur explizite Intro-Shots behalten.
        intro_resolved = intro_resolved.model_copy(
            update={"shots": [s for s in intro_resolved.shots if _is_intro_shot(s)]}
        )
        shot_ids = {s.shot_id for s in intro_resolved.shots}

    if not shot_ids and not audio_ids:
        raise EnhancedOtioExportError(
            "Intro-Filter lieferte keine Clips — Export abgebrochen."
        )

    return export_otio_from_resolved_timeline(
        project,
        basename=basename.strip() or "enhanced_intro",
        allow_errors=allow_errors,
        resolved=intro_resolved,
        timeline_name=f"{project.name} intro",
    )
