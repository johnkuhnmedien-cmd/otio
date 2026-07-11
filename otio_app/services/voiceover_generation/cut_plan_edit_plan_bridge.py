"""Phase 9.1/9.2: Isolierte Brücke von cut_plan.confirmed.json zu einem
EditPlanDocument-kompatiblen Draft.

Der bestätigte Cut Plan bleibt die Quelle der Wahrheit. Diese Brücke
übersetzt ihn DETERMINISTISCH und rein python-seitig:

- CutPlanAudioItem -> Audio-TimelineItem (Track A1) + BridgeAudioPlanItem
  (strukturierte, providerunabhängige Audio-Beschreibung, Phase 9.2)
- VisualSegment -> Video-/Bild-TimelineItem (Track V1)
- CutPlanItem/source_refs -> Traceability (siehe cut_plan_edit_plan_trace.py)
- Warnings/Blocker des bestätigten Cut Plans -> Bridge-Validierung

Phase 9.2 härtet Phase 9.1: Frame-Rundung erfolgt jetzt per BOUNDARY-CHAINING
pro Track (siehe normalize_timeline_boundaries_by_track), damit unabhängige
Rundung keine 1-Frame-Gaps/-Overlaps mehr erzeugen kann.

Es wird NICHTS neu geplant: keine LLM-Aufrufe, keine erneute Asset-Auswahl,
keine Supplement-Suche, keine Provider-Calls. Die Modelle `EditPlanDocument`,
`TimelineItem` und `TimelineItemTransform` werden ausschließlich als reine
Datenstrukturen importiert — es wird KEINE der höherstufigen
Builder-/Save-/Export-Funktionen der bestehenden Produktions-EditPlan-
Pipeline aufgerufen oder verändert. Alle Artefakte liegen ausschließlich
unter `_otio/voiceover_generation/cut_plan/edit_plan_bridge/` — NIEMALS
unter `_otio/edit_plan/` oder `_otio/exports/`."""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

from otio_app.analysis_models import EditPlanDocument, EditPlanSettings, TimelineItem, TimelineItemTransform
from otio_app.api_providers import API_PROVIDERS
from otio_app.defaults import (
    AUDIO_SCOPE_INTRO,
    CUT_PLAN_STATUS_CONFIRMED,
    EDIT_PLAN_BRIDGE_CANDIDATE_STATUS_MARKER,
    EDIT_PLAN_BRIDGE_ERROR_ASSET_FILE_MISSING,
    EDIT_PLAN_BRIDGE_ERROR_AUDIO_FILE_MISSING,
    EDIT_PLAN_BRIDGE_ERROR_AUDIO_ITEM_MISSING,
    EDIT_PLAN_BRIDGE_ERROR_AUDIO_PLAN_MISMATCH,
    EDIT_PLAN_BRIDGE_ERROR_AUDIO_PLAN_MISSING,
    EDIT_PLAN_BRIDGE_ERROR_AUDIO_SOURCE_NOT_ZERO,
    EDIT_PLAN_BRIDGE_ERROR_BLACK_GAP_DURING_AUDIO,
    EDIT_PLAN_BRIDGE_ERROR_CONFIRMED_CUT_PLAN_HAS_BLOCKERS,
    EDIT_PLAN_BRIDGE_ERROR_CONFIRMED_CUT_PLAN_MISSING,
    EDIT_PLAN_BRIDGE_ERROR_CONFIRMED_CUT_PLAN_NOT_CONFIRMED,
    EDIT_PLAN_BRIDGE_ERROR_CONFIRMED_CUT_PLAN_STALE,
    EDIT_PLAN_BRIDGE_ERROR_NON_MONOTONIC_TIMELINE,
    EDIT_PLAN_BRIDGE_ERROR_SECRET_LEAK_DETECTED,
    EDIT_PLAN_BRIDGE_ERROR_SOURCE_RANGE_INVALID,
    EDIT_PLAN_BRIDGE_ERROR_TIMELINE_OVERLAP,
    EDIT_PLAN_BRIDGE_ERROR_VIDEO_SOURCE_EXCEEDS_DURATION,
    EDIT_PLAN_BRIDGE_ERROR_VISUAL_ITEM_MISSING,
    EDIT_PLAN_BRIDGE_ERROR_VISUAL_TIMELINE_GAP,
    EDIT_PLAN_BRIDGE_ERROR_ZERO_OR_NEGATIVE_DURATION,
    EDIT_PLAN_BRIDGE_TIMELINE_ITEM_TYPE_VOICEOVER_AUDIO,
    EDIT_PLAN_BRIDGE_VALIDATION_STATUS_BLOCKED,
    EDIT_PLAN_BRIDGE_VALIDATION_STATUS_PASS,
    EDIT_PLAN_BRIDGE_VALIDATION_STATUS_WARNING,
    READINESS_SEVERITY_BLOCKER,
    READINESS_SEVERITY_WARNING,
)
from otio_app.models import Project
from otio_app.project_layout import (
    get_cut_plan_confirmed_path,
    get_cut_plan_edit_plan_bridge_audio_plan_path,
    get_cut_plan_edit_plan_bridge_draft_path,
    get_cut_plan_edit_plan_bridge_trace_path,
    get_cut_plan_edit_plan_bridge_validation_report_path,
)
from otio_app.services.api_keys import get_api_key
from otio_app.services.media_utils import probe_duration_seconds
from otio_app.services.voiceover_generation.cut_plan_confirm_service import (
    is_confirmed_cut_plan_stale,
    load_confirmed_cut_plan,
)
from otio_app.services.voiceover_generation.cut_plan_edit_plan_models import (
    BridgeAudioPlanDocument,
    BridgeAudioPlanItem,
    EditPlanBridgeValidationError,
    EditPlanBridgeValidationReport,
)
from otio_app.services.voiceover_generation.cut_plan_models import CutPlanAudioItem, CutPlanDocument, CutPlanItem, VisualSegment
from otio_app.services.voiceover_generation.llm_trace_service import content_hash_of_model

__all__ = [
    "build_edit_plan_draft_from_confirmed_cut_plan",
    "build_bridge_audio_plan_from_confirmed_cut_plan",
    "load_edit_plan_bridge_draft",
    "save_edit_plan_bridge_draft",
    "load_bridge_audio_plan",
    "save_bridge_audio_plan",
    "is_edit_plan_bridge_stale",
    "validate_edit_plan_bridge",
    "load_edit_plan_bridge_validation_report",
    "normalize_timeline_boundaries_by_track",
    "normalize_visual_boundaries",
    "normalize_audio_boundaries",
    "extract_note_value",
    "round_to_frame",
    "ceil_to_frame",
    "round_audio_times_to_frame",
    "round_visual_times_to_frame",
    "safe_timeline_item_component",
]

_TIME_EPSILON = 1e-6
_GAP_TOLERANCE = 0.01
_SOURCE_CUT_PLAN_HASH_PREFIX = "source_cut_plan_hash="
_SOURCE_CUT_PLAN_PATH_PREFIX = "source_cut_plan_path="
_SOURCE_PIPELINE_PREFIX = "source_pipeline="
_BRIDGE_AUDIO_PLAN_PATH_PREFIX = "bridge_audio_plan_path="
_EDIT_PLAN_BRIDGE_TRACE_PATH_PREFIX = "edit_plan_bridge_trace_path="
_SOURCE_PIPELINE_VALUE = "voiceover_generation_cut_plan"


# --- Reine Frame-/Timing-Hilfsfunktionen (keine Seiteneffekte) ---


def round_to_frame(value_sec: float, fps: int) -> float:
    """Rundet auf den nächstgelegenen Frame — kann sowohl auf- als auch
    abrunden. Für Audio-Endzeiten ist das NICHT geeignet (siehe
    ceil_to_frame), da ein Abrunden die hörbare Audiodauer kürzen würde."""
    if fps <= 0:
        return value_sec
    frame_duration = 1.0 / fps
    return round(value_sec / frame_duration) * frame_duration


def ceil_to_frame(value_sec: float, fps: int) -> float:
    """Rundet IMMER auf (nie ab) — für Audio-Dauern, damit Voice-over nie
    durch Rundung gekürzt wird (§6). Ein winziger Toleranzwert verhindert,
    dass Werte, die durch Floating-Point-Rauschen minimal über einer exakten
    Framegrenze liegen, fälschlich einen ganzen zusätzlichen Frame addieren."""
    if fps <= 0:
        return value_sec
    frame_duration = 1.0 / fps
    frames = value_sec / frame_duration
    return math.ceil(frames - 1e-6) * frame_duration


def round_audio_times_to_frame(
    timeline_start_sec: float, timeline_end_sec: float, duration_sec: float, fps: int
) -> tuple[float, float, float, float, bool, float]:
    """Normalisiert Audio-Zeiten frame-genau, OHNE die Audiodauer jemals zu
    kürzen (§6/§7): source_in_sec bleibt 0.0, source_out_sec/Dauer werden
    AUFgerundet (nie abgerundet). timeline_end_sec wird aus der gerundeten
    Dauer neu abgeleitet, nicht unabhängig gerundet.

    Rückgabe: (timeline_in, timeline_out, source_in, source_out,
    frame_rounded, frame_rounding_delta_sec)."""
    original_duration = timeline_end_sec - timeline_start_sec
    rounded_timeline_start = round_to_frame(timeline_start_sec, fps)
    rounded_duration = ceil_to_frame(duration_sec, fps)
    rounded_timeline_end = rounded_timeline_start + rounded_duration
    source_in = 0.0
    source_out = rounded_duration

    delta = (rounded_timeline_end - rounded_timeline_start) - original_duration
    frame_rounded = (
        abs(rounded_timeline_start - timeline_start_sec) > _TIME_EPSILON
        or abs(rounded_duration - duration_sec) > _TIME_EPSILON
    )
    return rounded_timeline_start, rounded_timeline_end, source_in, source_out, frame_rounded, delta


def round_visual_times_to_frame(
    timeline_in_sec: float, timeline_out_sec: float, source_in_sec: float, source_out_sec: float, fps: int
) -> tuple[float, float, float, float, bool, float]:
    """Normalisiert Visual-Zeiten frame-genau. timeline_in/out werden auf den
    nächstgelegenen Frame gerundet (nie negative/Null-Dauer, §6) —
    source_out_sec wird aus source_in_sec + gerundeter Dauer neu abgeleitet,
    damit Timeline- und Source-Spanne konsistent dieselbe Länge behalten.

    Rückgabe: (timeline_in, timeline_out, source_in, source_out,
    frame_rounded, frame_rounding_delta_sec)."""
    original_duration = timeline_out_sec - timeline_in_sec
    frame_duration = 1.0 / fps if fps > 0 else 0.0

    rounded_in = round_to_frame(timeline_in_sec, fps)
    rounded_out = round_to_frame(timeline_out_sec, fps)
    if rounded_out <= rounded_in and frame_duration > 0:
        rounded_out = rounded_in + frame_duration
    rounded_duration = rounded_out - rounded_in

    rounded_source_in = round_to_frame(source_in_sec, fps)
    rounded_source_out = rounded_source_in + rounded_duration

    delta = rounded_duration - original_duration
    frame_rounded = (
        abs(rounded_in - timeline_in_sec) > _TIME_EPSILON
        or abs(rounded_out - timeline_out_sec) > _TIME_EPSILON
        or abs(rounded_source_in - source_in_sec) > _TIME_EPSILON
        or abs(rounded_source_out - source_out_sec) > _TIME_EPSILON
    )
    return rounded_in, rounded_out, rounded_source_in, rounded_source_out, frame_rounded, delta


# --- Boundary-Chaining pro Track (Phase 9.2 §1) ---
#
# Phase 9.1 rundete jedes Item unabhängig — validiert, aber in Randfällen
# konnten dadurch 1-Frame-Gaps/-Overlaps entstehen (zwei unabhängig
# gerundete Werte, die eigentlich an derselben Stelle liegen sollten, aber
# in unterschiedliche Richtungen runden). Die folgenden Funktionen nehmen
# bereits EINZELN frame-gerundete TimelineItems und verketten sie NACHGELAGERT
# pro Track, damit die tatsächlich geschriebenen Werte lückenlos anschließen.


def normalize_visual_boundaries(items: list[TimelineItem], fps: int) -> list[TimelineItem]:
    """V1: nach timeline_in_sec sortieren. Das erste Visual behält seinen
    gerundeten Start. Jedes folgende Visual beginnt exakt dort, wo das
    vorherige endet (kein Mikro-Gap/-Overlap mehr durch Rundung). Das Ende
    bleibt das ursprünglich gerundete Ende, mindestens aber start + 1 Frame.
    source_out_sec wird aus source_in_sec + neuer Timeline-Dauer neu
    abgeleitet — ob das bei Video die reale Dateidauer überschreitet, prüft
    die Validierung (§5), nicht diese reine Funktion."""
    if not items:
        return []
    frame_duration = 1.0 / fps if fps > 0 else 0.0
    sorted_items = sorted(items, key=lambda entry: entry.timeline_in_sec)

    chained: list[TimelineItem] = []
    previous_out: float | None = None
    for item in sorted_items:
        new_in = item.timeline_in_sec if previous_out is None else previous_out
        new_out = max(item.timeline_out_sec, new_in + frame_duration)
        new_duration = new_out - new_in
        new_source_out = item.source_in_sec + new_duration
        chained.append(
            item.model_copy(
                update={
                    "timeline_in_sec": new_in,
                    "timeline_out_sec": new_out,
                    "duration_sec": new_duration,
                    "final_duration_sec": new_duration,
                    "source_out_sec": new_source_out,
                }
            )
        )
        previous_out = new_out
    return chained


def normalize_audio_boundaries(items: list[TimelineItem], fps: int) -> list[TimelineItem]:
    """A1: KEIN Back-to-back-Chaining — geplante Pausen zwischen Sektionen
    bleiben bestehen. Nur falls unabhängige Rundung zweier Items einen
    Overlap erzeugt hat, wird der Start des ZWEITEN Items minimal auf das
    Ende des ersten verschoben — die Dauer bleibt dabei exakt erhalten
    (Audio wird nie gekürzt), nur die Position rutscht."""
    if not items:
        return []
    sorted_items = sorted(items, key=lambda entry: entry.timeline_in_sec)

    normalized: list[TimelineItem] = []
    previous_out: float | None = None
    for item in sorted_items:
        duration = item.timeline_out_sec - item.timeline_in_sec
        new_in = item.timeline_in_sec
        if previous_out is not None and new_in < previous_out - _TIME_EPSILON:
            new_in = previous_out  # Overlap durch Rundung beheben, ohne zu kürzen
        new_out = new_in + duration
        normalized.append(
            item.model_copy(
                update={
                    "timeline_in_sec": new_in,
                    "timeline_out_sec": new_out,
                    "duration_sec": duration,
                    "final_duration_sec": duration,
                }
            )
        )
        previous_out = new_out
    return normalized


def normalize_timeline_boundaries_by_track(items: list[TimelineItem], fps: int) -> list[TimelineItem]:
    """Orchestriert das Boundary-Chaining je Track: V1 wird lückenlos
    verkettet, A1 wird nur bei Rundungs-Overlaps minimal verschoben (Pausen
    bleiben erhalten), alle anderen Tracks (z. B. zukünftige V2-Titel)
    bleiben unverändert. Ergebnis chronologisch nach timeline_in_sec
    sortiert (Track-Reihenfolge ist für eine flache TimelineItem-Liste nicht
    bedeutungstragend)."""
    by_track: dict[str, list[TimelineItem]] = {}
    for item in items:
        by_track.setdefault(item.track, []).append(item)

    result: list[TimelineItem] = []
    for track, track_items in by_track.items():
        if track == "V1":
            result.extend(normalize_visual_boundaries(track_items, fps))
        elif track == "A1":
            result.extend(normalize_audio_boundaries(track_items, fps))
        else:
            result.extend(track_items)

    result.sort(key=lambda entry: (entry.timeline_in_sec, entry.track, entry.timeline_item_id))
    return result


def safe_timeline_item_component(value: str) -> str:
    cleaned = "".join(char if char.isalnum() or char in "-_" else "_" for char in value)
    return cleaned or "item"


def _transform_dict_to_model(transform: dict[str, Any]) -> TimelineItemTransform:
    """Reine Feld-Übertragung — KEINE neue Transform-Berechnung (§8)."""
    if not transform:
        return TimelineItemTransform()
    kwargs: dict[str, Any] = {}
    if "scaling_mode" in transform:
        kwargs["scaling_mode"] = transform["scaling_mode"]
    zoom_factor = transform.get("zoom_factor")
    if zoom_factor is not None:
        kwargs["zoom_x"] = float(zoom_factor)
        kwargs["zoom_y"] = float(zoom_factor)
    if "zoom_x" in transform:
        kwargs["zoom_x"] = float(transform["zoom_x"])
    if "zoom_y" in transform:
        kwargs["zoom_y"] = float(transform["zoom_y"])
    if "position_x" in transform:
        kwargs["position_x"] = float(transform["position_x"])
    if "position_y" in transform:
        kwargs["position_y"] = float(transform["position_y"])
    return TimelineItemTransform(**kwargs)


def _audio_timeline_item_id(audio_item: CutPlanAudioItem) -> str:
    scope_label = audio_item.scope or AUDIO_SCOPE_INTRO
    folder_component = safe_timeline_item_component(audio_item.folder_name) if audio_item.folder_name else "intro"
    return f"edit_audio_{scope_label}_{folder_component}"


def _audio_item_to_timeline_item(audio_item: CutPlanAudioItem, fps: int) -> TimelineItem:
    timeline_in, timeline_out, source_in, source_out, _frame_rounded, _delta = round_audio_times_to_frame(
        audio_item.timeline_start_sec, audio_item.timeline_end_sec, audio_item.duration_sec, fps
    )
    scope_label = audio_item.scope or AUDIO_SCOPE_INTRO
    return TimelineItem(
        timeline_item_id=_audio_timeline_item_id(audio_item),
        type=EDIT_PLAN_BRIDGE_TIMELINE_ITEM_TYPE_VOICEOVER_AUDIO,
        section_id=audio_item.folder_name or scope_label,
        folder_name=audio_item.folder_name,
        voice_file=audio_item.audio_path,
        resolved_media_path=audio_item.audio_path,
        original_asset_path=audio_item.audio_path,
        timeline_in_sec=timeline_in,
        timeline_out_sec=timeline_out,
        duration_sec=timeline_out - timeline_in,
        final_duration_sec=timeline_out - timeline_in,
        source_in_sec=source_in,
        source_out_sec=source_out,
        selection_reason="cut_plan_audio_item",
        track=audio_item.track or "A1",
    )


def _visual_segment_to_timeline_item(item: CutPlanItem, segment: VisualSegment, fps: int) -> TimelineItem:
    timeline_in, timeline_out, source_in, source_out, _frame_rounded, _delta = round_visual_times_to_frame(
        segment.timeline_in_sec, segment.timeline_out_sec, segment.source_in_sec, segment.source_out_sec, fps
    )
    item_type = "video_shot" if segment.asset_type == "video" else "image_shot"
    hook_beat_id = ""
    for source_ref in item.source_refs:
        if source_ref.source_hook_beat_id:
            hook_beat_id = source_ref.source_hook_beat_id
            break

    return TimelineItem(
        timeline_item_id=f"edit_{segment.segment_id}",
        type=item_type,
        section_id=item.cut_item_id,
        folder_name=item.folder_name,
        asset_id=segment.asset_id,
        resolved_media_path=segment.asset_path,
        original_asset_path=segment.asset_path,
        timeline_in_sec=timeline_in,
        timeline_out_sec=timeline_out,
        duration_sec=timeline_out - timeline_in,
        final_duration_sec=timeline_out - timeline_in,
        source_in_sec=source_in,
        source_out_sec=source_out,
        selection_reason=segment.reason,
        transform=_transform_dict_to_model(segment.transform),
        track=segment.track or "V1",
        asset_type=segment.asset_type,
        passage_text=item.text,
        background_style=segment.background_style,
        beat_id=hook_beat_id,
        supplement_request_id=item.supplement_request_id,
    )


def _validate_confirmed_cut_plan_for_bridge(project: Project, confirmed: CutPlanDocument | None) -> None:
    if confirmed is None:
        raise ValueError("Kein bestätigter Cut Plan (cut_plan.confirmed.json) vorhanden.")
    if is_confirmed_cut_plan_stale(project, confirmed):
        raise ValueError(
            "Der bestätigte Cut Plan ist veraltet (Voice-over-Projektplan oder Cut-Plan-Settings "
            "haben sich seit der Bestätigung geändert)."
        )
    if confirmed.status != CUT_PLAN_STATUS_CONFIRMED:
        raise ValueError(f"Cut Plan Status ist '{confirmed.status}', erwartet '{CUT_PLAN_STATUS_CONFIRMED}'.")
    if confirmed.blockers:
        raise ValueError(f"Bestätigter Cut Plan hat {len(confirmed.blockers)} Blocker.")


def _build_note(prefix: str, value: str) -> str:
    return f"{prefix}{value}"


def extract_note_value(notes: list[str], prefix: str) -> str:
    """Extrahiert deterministisch den Wert einer 'prefix<value>'-Notiz aus
    plan_generation_notes — stabile, parsebare Konvention (§4), solange
    EditPlanDocument kein generisches Metadaten-Feld hat."""
    for note in notes:
        if note.startswith(prefix):
            return note[len(prefix):]
    return ""


def _build_edit_plan_and_audio_plan(
    project: Project,
) -> tuple[CutPlanDocument, EditPlanDocument, BridgeAudioPlanDocument]:
    """Interner, gemeinsamer Kern für build_edit_plan_draft_from_confirmed_
    cut_plan UND build_bridge_audio_plan_from_confirmed_cut_plan — läuft
    EINMAL, damit beide Ergebnisse garantiert konsistent aus DENSELBEN
    (frame-gerundeten + boundary-gechainten) TimelineItems abgeleitet werden,
    statt durch zwei unabhängige Berechnungen zu driften."""
    confirmed = load_confirmed_cut_plan(project)
    _validate_confirmed_cut_plan_for_bridge(project, confirmed)
    assert confirmed is not None  # für Typprüfer; _validate wirft sonst

    fps = confirmed.timeline_fps or 25

    raw_items: list[TimelineItem] = [
        _audio_item_to_timeline_item(audio_item, fps) for audio_item in confirmed.audio_items
    ]
    for item in confirmed.items:
        for segment in item.planned_visual_segments:
            raw_items.append(_visual_segment_to_timeline_item(item, segment, fps))

    # Phase 9.2 §1: Boundary-Chaining NACH der individuellen Frame-Rundung,
    # damit unabhängige Rundung keine 1-Frame-Gaps/-Overlaps mehr erzeugt.
    final_items = normalize_timeline_boundaries_by_track(raw_items, fps)

    snapshot = confirmed.settings_snapshot or {}
    defaults = EditPlanSettings()
    edit_plan_settings = EditPlanSettings(
        shot_min_sec=snapshot.get("shot_min_sec", defaults.shot_min_sec),
        shot_max_sec=snapshot.get("shot_max_sec", defaults.shot_max_sec),
        video_head_trim_sec=snapshot.get("video_head_trim_sec", defaults.video_head_trim_sec),
    )

    confirmed_hash = content_hash_of_model(confirmed)
    notes = [
        _build_note(_SOURCE_PIPELINE_PREFIX, _SOURCE_PIPELINE_VALUE),
        _build_note(_SOURCE_CUT_PLAN_PATH_PREFIX, str(get_cut_plan_confirmed_path(project.work_dir_path))),
        _build_note(_SOURCE_CUT_PLAN_HASH_PREFIX, confirmed_hash),
        _build_note(
            _BRIDGE_AUDIO_PLAN_PATH_PREFIX, str(get_cut_plan_edit_plan_bridge_audio_plan_path(project.work_dir_path))
        ),
        _build_note(
            _EDIT_PLAN_BRIDGE_TRACE_PATH_PREFIX,
            str(get_cut_plan_edit_plan_bridge_trace_path(project.work_dir_path)),
        ),
    ]

    edit_plan = EditPlanDocument(
        project_id=project.id,
        folder_name=None,
        confirmed=False,
        settings=edit_plan_settings,
        voiceover=None,
        shots=[],
        timeline_items=final_items,
        candidate_status=EDIT_PLAN_BRIDGE_CANDIDATE_STATUS_MARKER,
        validation_status="",
        plan_generation_notes=notes,
    )

    final_audio_by_id = {item.timeline_item_id: item for item in final_items if item.track == "A1"}
    audio_plan_items: list[BridgeAudioPlanItem] = []
    for index, audio_item in enumerate(confirmed.audio_items):
        timeline_item_id = _audio_timeline_item_id(audio_item)
        final_item = final_audio_by_id.get(timeline_item_id)
        if final_item is None:
            continue  # defensiv: sollte durch den Aufbau oben nie eintreten
        audio_plan_items.append(
            BridgeAudioPlanItem(
                scope=audio_item.scope or AUDIO_SCOPE_INTRO,
                folder_name=audio_item.folder_name,
                audio_path=audio_item.audio_path,
                timeline_in_sec=final_item.timeline_in_sec,
                timeline_out_sec=final_item.timeline_out_sec,
                source_in_sec=final_item.source_in_sec,
                source_out_sec=final_item.source_out_sec,
                duration_sec=final_item.timeline_out_sec - final_item.timeline_in_sec,
                track=final_item.track,
                source_cut_plan_audio_index=index,
                timeline_item_id=timeline_item_id,
            )
        )
    bridge_audio_plan = BridgeAudioPlanDocument(
        project_id=project.id, source_cut_plan_hash=confirmed_hash, items=audio_plan_items
    )

    return confirmed, edit_plan, bridge_audio_plan


def build_edit_plan_draft_from_confirmed_cut_plan(project: Project) -> EditPlanDocument:
    """Liest AUSSCHLIESSLICH cut_plan.confirmed.json und übersetzt ihn
    deterministisch in einen EditPlanDocument-kompatiblen Draft. Reine
    Funktion — speichert nichts (siehe save_edit_plan_bridge_draft).

    Keine Asset-Auswahl, keine Cut-Plan-Validierung, keine Supplement-Suche,
    keine Provider-/LLM-Aufrufe. Wirft ValueError, wenn kein bestätigter Cut
    Plan vorhanden, veraltet, nicht CONFIRMED oder blockiert ist."""
    _confirmed, edit_plan, _audio_plan = _build_edit_plan_and_audio_plan(project)
    return edit_plan


def build_bridge_audio_plan_from_confirmed_cut_plan(project: Project) -> BridgeAudioPlanDocument:
    """Phase 9.2 §3: strukturierte, providerunabhängige Audio-Plan-Datei —
    abgeleitet aus DENSELBEN finalen (frame-gerundeten + boundary-gechainten)
    Audio-TimelineItems wie build_edit_plan_draft_from_confirmed_cut_plan,
    damit beide Artefakte garantiert konsistent bleiben. Reine Funktion —
    speichert nichts (siehe save_bridge_audio_plan)."""
    _confirmed, _edit_plan, audio_plan = _build_edit_plan_and_audio_plan(project)
    return audio_plan


def load_edit_plan_bridge_draft(project: Project) -> EditPlanDocument | None:
    path = get_cut_plan_edit_plan_bridge_draft_path(project.work_dir_path)
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return EditPlanDocument.model_validate(payload)
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError):
        return None


def save_edit_plan_bridge_draft(project: Project, edit_plan: EditPlanDocument) -> EditPlanDocument:
    normalized = edit_plan.model_copy(update={"project_id": project.id})
    path = get_cut_plan_edit_plan_bridge_draft_path(project.work_dir_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(normalized.model_dump_json(indent=2), encoding="utf-8")
    return normalized


def save_bridge_audio_plan(project: Project, audio_plan: BridgeAudioPlanDocument) -> BridgeAudioPlanDocument:
    normalized = audio_plan.model_copy(update={"project_id": project.id})
    path = get_cut_plan_edit_plan_bridge_audio_plan_path(project.work_dir_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(normalized.model_dump_json(indent=2), encoding="utf-8")
    return normalized


def load_bridge_audio_plan(project: Project) -> BridgeAudioPlanDocument | None:
    path = get_cut_plan_edit_plan_bridge_audio_plan_path(project.work_dir_path)
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return BridgeAudioPlanDocument.model_validate(payload)
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError):
        return None


def is_edit_plan_bridge_stale(project: Project, edit_plan: EditPlanDocument) -> bool:
    """True, wenn sich der bestätigte Cut Plan seit dem Bau dieses Bridge-
    Drafts geändert hat (z. B. erneut bestätigt wurde) oder gar nicht mehr
    existiert."""
    confirmed = load_confirmed_cut_plan(project)
    if confirmed is None:
        return True
    stored_hash = extract_note_value(edit_plan.plan_generation_notes, _SOURCE_CUT_PLAN_HASH_PREFIX)
    return stored_hash != content_hash_of_model(confirmed)


# --- Validierung (§10) ---


def _make_error(
    error_type: str,
    *,
    severity: str = READINESS_SEVERITY_WARNING,
    scope: str = "project",
    cut_item_id: str = "",
    visual_segment_id: str = "",
    timeline_item_id: str = "",
    message: str = "",
    fix_hint: str = "",
) -> EditPlanBridgeValidationError:
    return EditPlanBridgeValidationError(
        type=error_type,
        severity=severity,
        scope=scope,
        cut_item_id=cut_item_id,
        visual_segment_id=visual_segment_id,
        timeline_item_id=timeline_item_id,
        message=message,
        fix_hint=fix_hint,
    )


def _is_span_covered(coverage: list[tuple[float, float]], start: float, end: float, *, tolerance: float = 0.05) -> bool:
    sorted_intervals = sorted(coverage)
    cursor = start
    for interval_start, interval_end in sorted_intervals:
        if interval_end <= cursor + tolerance:
            continue
        if interval_start > cursor + tolerance:
            break
        cursor = max(cursor, interval_end)
        if cursor >= end - tolerance:
            return True
    return cursor >= end - tolerance


def _scan_for_leaked_secrets(edit_plan: EditPlanDocument) -> list[str]:
    """Defensiver Scan (§10/§14): der Bridge-Draft darf niemals API-Keys,
    rohe LLM-Antworten oder audio_base64 enthalten. Da die Bridge selbst nie
    solche Daten schreibt, ist dies primär eine defensive Absicherung."""
    findings: list[str] = []
    serialized = edit_plan.model_dump_json()
    if "audio_base64" in serialized:
        findings.append("Serialisierter Bridge-Draft enthält das Schlüsselwort 'audio_base64'.")
    for provider in API_PROVIDERS:
        value = get_api_key(provider.env_key)
        if value and value in serialized:
            findings.append(f"Serialisierter Bridge-Draft enthält einen API-Key-Wert ({provider.env_key}).")
    return findings


def validate_edit_plan_bridge(project: Project, edit_plan: EditPlanDocument) -> EditPlanBridgeValidationReport:
    """Validiert einen bereits gebauten Bridge-Draft (§10) und speichert das
    Ergebnis in edit_plan_bridge_validation_report.json.

    Phase 9.3 §1: ein kleiner In-Memory-Cache (dict[path, duration_sec |
    None]) sorgt dafür, dass probe_duration_seconds pro Video-Pfad nur
    einmal pro Validierungslauf aufgerufen wird — kein persistenter Cache,
    keine Architekturänderung (analog zu Phase 8.5 im Cut-Plan-Validator)."""
    warnings: list[EditPlanBridgeValidationError] = []
    blockers: list[EditPlanBridgeValidationError] = []
    duration_cache: dict[str, float | None] = {}

    def _cached_probe_duration(path_str: str) -> float | None:
        if path_str not in duration_cache:
            duration_cache[path_str] = probe_duration_seconds(Path(path_str))
        return duration_cache[path_str]

    confirmed = load_confirmed_cut_plan(project)
    if confirmed is None:
        blockers.append(
            _make_error(
                EDIT_PLAN_BRIDGE_ERROR_CONFIRMED_CUT_PLAN_MISSING,
                severity=READINESS_SEVERITY_BLOCKER,
                message="Kein bestätigter Cut Plan (cut_plan.confirmed.json) vorhanden.",
            )
        )
        report = _finalize_report(project, edit_plan, confirmed, warnings, blockers)
        save_edit_plan_bridge_validation_report(project, report)
        return report

    if is_confirmed_cut_plan_stale(project, confirmed):
        blockers.append(
            _make_error(
                EDIT_PLAN_BRIDGE_ERROR_CONFIRMED_CUT_PLAN_STALE,
                severity=READINESS_SEVERITY_BLOCKER,
                message="Der bestätigte Cut Plan ist veraltet.",
            )
        )
    if confirmed.status != CUT_PLAN_STATUS_CONFIRMED:
        blockers.append(
            _make_error(
                EDIT_PLAN_BRIDGE_ERROR_CONFIRMED_CUT_PLAN_NOT_CONFIRMED,
                severity=READINESS_SEVERITY_BLOCKER,
                message=f"Cut Plan Status ist '{confirmed.status}', erwartet 'CONFIRMED'.",
            )
        )
    if confirmed.blockers:
        blockers.append(
            _make_error(
                EDIT_PLAN_BRIDGE_ERROR_CONFIRMED_CUT_PLAN_HAS_BLOCKERS,
                severity=READINESS_SEVERITY_BLOCKER,
                message=f"Bestätigter Cut Plan hat {len(confirmed.blockers)} Blocker.",
            )
        )

    audio_items = [item for item in edit_plan.timeline_items if item.track == "A1"]
    visual_items = [item for item in edit_plan.timeline_items if item.track != "A1"]

    expected_audio_count = len(confirmed.audio_items)
    if len(audio_items) != expected_audio_count:
        blockers.append(
            _make_error(
                EDIT_PLAN_BRIDGE_ERROR_AUDIO_ITEM_MISSING,
                severity=READINESS_SEVERITY_BLOCKER,
                scope="audio",
                message=f"Erwartet {expected_audio_count} Audio-TimelineItems, gefunden {len(audio_items)}.",
            )
        )

    expected_visual_count = sum(len(item.planned_visual_segments) for item in confirmed.items)
    if len(visual_items) != expected_visual_count:
        blockers.append(
            _make_error(
                EDIT_PLAN_BRIDGE_ERROR_VISUAL_ITEM_MISSING,
                severity=READINESS_SEVERITY_BLOCKER,
                scope="visual",
                message=f"Erwartet {expected_visual_count} Visual-TimelineItems, gefunden {len(visual_items)}.",
            )
        )

    for timeline_item in edit_plan.timeline_items:
        if timeline_item.timeline_out_sec - timeline_item.timeline_in_sec <= 0:
            blockers.append(
                _make_error(
                    EDIT_PLAN_BRIDGE_ERROR_ZERO_OR_NEGATIVE_DURATION,
                    severity=READINESS_SEVERITY_BLOCKER,
                    scope="timeline",
                    timeline_item_id=timeline_item.timeline_item_id,
                    message=f"{timeline_item.timeline_item_id}: Timeline-Dauer <= 0.",
                )
            )
        if timeline_item.source_out_sec <= timeline_item.source_in_sec:
            blockers.append(
                _make_error(
                    EDIT_PLAN_BRIDGE_ERROR_SOURCE_RANGE_INVALID,
                    severity=READINESS_SEVERITY_BLOCKER,
                    scope="asset",
                    timeline_item_id=timeline_item.timeline_item_id,
                    message=f"{timeline_item.timeline_item_id}: source_out_sec <= source_in_sec.",
                )
            )

    for audio_timeline_item in audio_items:
        if abs(audio_timeline_item.source_in_sec) > _TIME_EPSILON:
            blockers.append(
                _make_error(
                    EDIT_PLAN_BRIDGE_ERROR_AUDIO_SOURCE_NOT_ZERO,
                    severity=READINESS_SEVERITY_BLOCKER,
                    scope="audio",
                    timeline_item_id=audio_timeline_item.timeline_item_id,
                    message=f"{audio_timeline_item.timeline_item_id}: source_in_sec != 0.0.",
                )
            )
        audio_path = audio_timeline_item.resolved_media_path or audio_timeline_item.voice_file
        if not audio_path or not Path(audio_path).is_file():
            blockers.append(
                _make_error(
                    EDIT_PLAN_BRIDGE_ERROR_AUDIO_FILE_MISSING,
                    severity=READINESS_SEVERITY_BLOCKER,
                    scope="audio",
                    timeline_item_id=audio_timeline_item.timeline_item_id,
                    message=f"{audio_timeline_item.timeline_item_id}: Audiodatei fehlt ('{audio_path}').",
                )
            )

    for visual_timeline_item in visual_items:
        asset_path = visual_timeline_item.resolved_media_path
        if not asset_path or not Path(asset_path).is_file():
            blockers.append(
                _make_error(
                    EDIT_PLAN_BRIDGE_ERROR_ASSET_FILE_MISSING,
                    severity=READINESS_SEVERITY_BLOCKER,
                    scope="asset",
                    timeline_item_id=visual_timeline_item.timeline_item_id,
                    message=f"{visual_timeline_item.timeline_item_id}: Asset-Datei fehlt ('{asset_path}').",
                )
            )
            continue
        if visual_timeline_item.type == "video_shot":
            real_duration = _cached_probe_duration(asset_path)
            if real_duration is not None and visual_timeline_item.source_out_sec > real_duration + _GAP_TOLERANCE:
                blockers.append(
                    _make_error(
                        EDIT_PLAN_BRIDGE_ERROR_VIDEO_SOURCE_EXCEEDS_DURATION,
                        severity=READINESS_SEVERITY_BLOCKER,
                        scope="asset",
                        timeline_item_id=visual_timeline_item.timeline_item_id,
                        message=f"{visual_timeline_item.timeline_item_id}: source_out_sec "
                        f"({visual_timeline_item.source_out_sec:.2f}s) > Video-Dauer ({real_duration:.2f}s) "
                        f"— vermutlich durch Boundary-Chaining verlängert.",
                    )
                )

    by_track: dict[str, list[TimelineItem]] = {}
    for timeline_item in edit_plan.timeline_items:
        by_track.setdefault(timeline_item.track, []).append(timeline_item)
    for track, track_items in by_track.items():
        sorted_items = sorted(track_items, key=lambda entry: entry.timeline_in_sec)
        for previous_item, next_item in zip(sorted_items, sorted_items[1:]):
            if next_item.timeline_in_sec < previous_item.timeline_in_sec - _TIME_EPSILON:
                blockers.append(
                    _make_error(
                        EDIT_PLAN_BRIDGE_ERROR_NON_MONOTONIC_TIMELINE,
                        severity=READINESS_SEVERITY_BLOCKER,
                        scope="timeline",
                        timeline_item_id=next_item.timeline_item_id,
                        message=f"Nicht-monotone Reihenfolge auf Track '{track}' bei "
                        f"'{next_item.timeline_item_id}'.",
                    )
                )
            elif next_item.timeline_in_sec < previous_item.timeline_out_sec - _TIME_EPSILON:
                blockers.append(
                    _make_error(
                        EDIT_PLAN_BRIDGE_ERROR_TIMELINE_OVERLAP,
                        severity=READINESS_SEVERITY_BLOCKER,
                        scope="timeline",
                        timeline_item_id=next_item.timeline_item_id,
                        message=f"Überlappung auf Track '{track}': '{previous_item.timeline_item_id}' und "
                        f"'{next_item.timeline_item_id}'.",
                    )
                )
            elif track == "V1" and next_item.timeline_in_sec > previous_item.timeline_out_sec + _GAP_TOLERANCE:
                # V1 muss nach dem Boundary-Chaining (§1) lückenlos sein — ein
                # verbleibender Gap deutet auf einen manuell veränderten oder
                # nicht durch die Bridge selbst erzeugten Draft hin.
                blockers.append(
                    _make_error(
                        EDIT_PLAN_BRIDGE_ERROR_VISUAL_TIMELINE_GAP,
                        severity=READINESS_SEVERITY_BLOCKER,
                        scope="visual",
                        timeline_item_id=next_item.timeline_item_id,
                        message=f"Lücke auf Track 'V1' zwischen '{previous_item.timeline_item_id}' und "
                        f"'{next_item.timeline_item_id}'.",
                    )
                )

    audio_plan = load_bridge_audio_plan(project)
    voiceover_audio_items = [
        item for item in audio_items if item.type == EDIT_PLAN_BRIDGE_TIMELINE_ITEM_TYPE_VOICEOVER_AUDIO
    ]
    if voiceover_audio_items and audio_plan is None:
        blockers.append(
            _make_error(
                EDIT_PLAN_BRIDGE_ERROR_AUDIO_PLAN_MISSING,
                severity=READINESS_SEVERITY_BLOCKER,
                scope="audio",
                message="Es gibt Audio-TimelineItems (voiceover_audio), aber keinen bridge_audio_plan.json.",
            )
        )
    elif audio_plan is not None:
        timeline_audio_ids = {item.timeline_item_id for item in voiceover_audio_items}
        audio_plan_ids = {entry.timeline_item_id for entry in audio_plan.items}
        for missing_id in sorted(timeline_audio_ids - audio_plan_ids):
            blockers.append(
                _make_error(
                    EDIT_PLAN_BRIDGE_ERROR_AUDIO_PLAN_MISMATCH,
                    severity=READINESS_SEVERITY_BLOCKER,
                    scope="audio",
                    timeline_item_id=missing_id,
                    message=f"TimelineItem '{missing_id}' (voiceover_audio) hat keinen entsprechenden "
                    "BridgeAudioPlan-Eintrag.",
                )
            )
        for orphan_id in sorted(audio_plan_ids - timeline_audio_ids):
            blockers.append(
                _make_error(
                    EDIT_PLAN_BRIDGE_ERROR_AUDIO_PLAN_MISMATCH,
                    severity=READINESS_SEVERITY_BLOCKER,
                    scope="audio",
                    timeline_item_id=orphan_id,
                    message=f"BridgeAudioPlan-Eintrag '{orphan_id}' hat kein entsprechendes TimelineItem.",
                )
            )
        if len(audio_plan.items) != len(confirmed.audio_items):
            blockers.append(
                _make_error(
                    EDIT_PLAN_BRIDGE_ERROR_AUDIO_PLAN_MISMATCH,
                    severity=READINESS_SEVERITY_BLOCKER,
                    scope="audio",
                    message=f"BridgeAudioPlan hat {len(audio_plan.items)} Eintrag/Einträge, "
                    f"bestätigter Cut Plan hat {len(confirmed.audio_items)} AudioItem(s).",
                )
            )

    visual_coverage = [(item.timeline_in_sec, item.timeline_out_sec) for item in visual_items]
    for audio_timeline_item in audio_items:
        if not _is_span_covered(
            visual_coverage, audio_timeline_item.timeline_in_sec, audio_timeline_item.timeline_out_sec
        ):
            blockers.append(
                _make_error(
                    EDIT_PLAN_BRIDGE_ERROR_BLACK_GAP_DURING_AUDIO,
                    severity=READINESS_SEVERITY_BLOCKER,
                    scope="timeline",
                    timeline_item_id=audio_timeline_item.timeline_item_id,
                    message=f"Visuelles Loch während Audio '{audio_timeline_item.timeline_item_id}'.",
                )
            )

    for leak_message in _scan_for_leaked_secrets(edit_plan):
        blockers.append(
            _make_error(
                EDIT_PLAN_BRIDGE_ERROR_SECRET_LEAK_DETECTED,
                severity=READINESS_SEVERITY_BLOCKER,
                scope="project",
                message=leak_message,
            )
        )

    report = _finalize_report(project, edit_plan, confirmed, warnings, blockers)
    save_edit_plan_bridge_validation_report(project, report)
    return report


def _finalize_report(
    project: Project,
    edit_plan: EditPlanDocument,
    confirmed: CutPlanDocument | None,
    warnings: list[EditPlanBridgeValidationError],
    blockers: list[EditPlanBridgeValidationError],
) -> EditPlanBridgeValidationReport:
    if blockers:
        status = EDIT_PLAN_BRIDGE_VALIDATION_STATUS_BLOCKED
    elif warnings:
        status = EDIT_PLAN_BRIDGE_VALIDATION_STATUS_WARNING
    else:
        status = EDIT_PLAN_BRIDGE_VALIDATION_STATUS_PASS
    return EditPlanBridgeValidationReport(
        project_id=project.id,
        source_cut_plan_hash=content_hash_of_model(confirmed) if confirmed is not None else "",
        edit_plan_hash=content_hash_of_model(edit_plan),
        status=status,
        warnings=warnings,
        blockers=blockers,
    )


def save_edit_plan_bridge_validation_report(
    project: Project, report: EditPlanBridgeValidationReport
) -> EditPlanBridgeValidationReport:
    normalized = report.model_copy(update={"project_id": project.id})
    path = get_cut_plan_edit_plan_bridge_validation_report_path(project.work_dir_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(normalized.model_dump_json(indent=2), encoding="utf-8")
    return normalized


def load_edit_plan_bridge_validation_report(project: Project) -> EditPlanBridgeValidationReport | None:
    path = get_cut_plan_edit_plan_bridge_validation_report_path(project.work_dir_path)
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return EditPlanBridgeValidationReport.model_validate(payload)
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError):
        return None
