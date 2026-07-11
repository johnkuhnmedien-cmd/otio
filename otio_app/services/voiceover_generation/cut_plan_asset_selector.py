"""Phase 8.3: Asset-Auswahl, Fallback, Dauer-/Split-/Merge-Strategie.

Liest AUSSCHLIESSLICH bereits bestätigte Felder aus
`confirmed_voiceover_project_plan.json` (primary_asset_id, backup_asset_ids,
second_backup_asset_ids, planned_segments, needs_supplement_asset,
supplement_reason) sowie die Folder-Inventories (lesend). Erfindet KEINE
neuen Asset-IDs, verändert KEINE Inventory-Dateien, löst KEINE
Supplement-Suche/-Beschaffung aus (das kommt erst in einer späteren
Sub-Phase) und transcodiert/rendert nichts.

Phase 7 (Cut-Plan-Split-Fix): Kandidaten für ein gesplittetes Item werden
gegen die Dauer IHRES EIGENEN Segments geprüft (nicht mehr gegen die volle
Satzdauer, siehe _select_candidates_for_item) und second_backup_asset_ids
zählen als echte Alternativen im Fallback-Pool (siehe
_general_asset_pool). Sind vom Autor pro Shot geplante Assets vorhanden
(item.planned_segments), werden diese pro Segment bevorzugt (siehe
_candidate_order_for_segment) — bei allen vor Phase 7 erzeugten Items
(planned_segments leer) bleibt die Kandidaten-Reihenfolge identisch zum
bisherigen Verhalten, nur die Dauer-Prüfung ist korrigiert."""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

from otio_app.defaults import (
    AUDIO_SCOPE_FOLDER,
    CUT_PLAN_ASSET_SELECTION_BACKUP_USED,
    CUT_PLAN_ASSET_SELECTION_BLOCKED,
    CUT_PLAN_ASSET_SELECTION_PRIMARY_USED,
    CUT_PLAN_ASSET_SELECTION_SUPPLEMENT_REQUIRED,
    CUT_PLAN_DEFAULT_VIDEO_HEAD_TRIM_SEC,
    CUT_PLAN_DURATION_STRATEGY_MERGED,
    CUT_PLAN_DURATION_STRATEGY_SINGLE_SHOT,
    CUT_PLAN_DURATION_STRATEGY_SPLIT,
    CUT_PLAN_ERROR_AMBIGUOUS_ASSET_ID,
    CUT_PLAN_ERROR_ASSET_FILE_MISSING,
    CUT_PLAN_ERROR_ASSET_REUSE_DISTANCE_TOO_SHORT,
    CUT_PLAN_ERROR_ASSET_TOO_SHORT,
    CUT_PLAN_ERROR_INVALID_ASSET_ID,
    CUT_PLAN_ERROR_MAX_ASSET_USAGE_EXCEEDED,
    CUT_PLAN_ERROR_SHOT_TOO_SHORT,
    CUT_PLAN_ERROR_SUPPLEMENT_REASON_MISSING,
    CUT_PLAN_ERROR_SUPPLEMENT_REQUIRED,
    CUT_PLAN_STATUS_DRAFT,
    CUT_PLAN_STATUS_NEEDS_REVIEW,
    READINESS_SEVERITY_BLOCKER,
    READINESS_SEVERITY_WARNING,
)
from otio_app.models import Project
from otio_app.services.generic_outro_selector import asset_id_for_path
from otio_app.services.inventory_loader import load_folder_inventory
from otio_app.services.media_utils import is_image_media, probe_duration_seconds
from otio_app.services.voiceover_generation.cut_plan_models import (
    CutPlanDocument,
    CutPlanItem,
    CutPlanSettings,
    CutPlanValidationError,
    VisualSegment,
)
from otio_app.services.voiceover_generation.cut_plan_visual_coverage import apply_visual_coverage_extensions
from otio_app.services.voiceover_generation.final_plan_service import load_confirmed_voiceover_project_plan
from otio_app.services.voiceover_generation.models import ConfirmedVoiceoverProjectPlan

try:  # pragma: no cover - defensiv, falls die Utility-Signatur abweicht
    from otio_app.services.otio_media_transform import compute_fill_zoom_factor
except ImportError:  # pragma: no cover
    compute_fill_zoom_factor = None  # type: ignore[assignment]

__all__ = [
    "CutPlanAssetCandidate",
    "CutPlanAssetLookup",
    "UsageTracker",
    "apply_asset_selection_to_cut_plan",
    "load_asset_lookup_for_cut_plan",
    "resolve_asset_candidate",
    "choose_asset_for_cut_item",
    "build_visual_segments_for_item",
    "determine_duration_strategy",
    "compute_visual_window_end_sec",
    "compute_visual_window_duration_sec",
    "merge_mini_shots_with_next_item",
    "update_asset_usage_summary",
    "settings_from_snapshot",
]

# Segmentdauern innerhalb dieser Toleranz gelten als "am Frame" — vermeidet
# Rundungs-Fehlalarme bei der 1/3-Division für Fall-C-Splits.
_DURATION_EPSILON = 0.01


@dataclass
class CutPlanAssetCandidate:
    """Ergebnis einer lesenden Asset-Existenz-/Dauer-Prüfung — kein Pydantic-
    Modell, da rein intern und nie direkt persistiert."""

    asset_id: str
    asset_path: str
    folder_name: str
    asset_type: str  # "video" | "image" | ""
    duration_sec: float = 0.0
    width: int = 0
    height: int = 0
    is_image: bool = False
    is_video: bool = False
    exists: bool = False
    usable_duration_sec: float = 0.0
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class CutPlanAssetLookup:
    """asset_id -> Liste von Kandidaten (mehrere Einträge = Mehrdeutigkeit
    über mehrere Folder-Inventories hinweg, siehe §2)."""

    candidates_by_id: dict[str, list[CutPlanAssetCandidate]] = field(default_factory=dict)

    def add(self, candidate: CutPlanAssetCandidate) -> None:
        self.candidates_by_id.setdefault(candidate.asset_id, []).append(candidate)

    def is_ambiguous(self, asset_id: str) -> bool:
        candidates = self.candidates_by_id.get(asset_id, [])
        folder_names = {candidate.folder_name for candidate in candidates}
        return len(folder_names) > 1


@dataclass
class UsageTracker:
    """Globale Asset-Nutzung über den gesamten Cut Plan — Intro zählt mit
    (§5). visual_segment_index läuft über ALLE platzierten VisualSegments in
    Timeline-Reihenfolge, unabhängig davon, zu welchem CutPlanItem sie
    gehören."""

    count_by_asset_id: dict[str, int] = field(default_factory=dict)
    last_visual_segment_index_by_asset_id: dict[str, int] = field(default_factory=dict)
    visual_segment_index: int = 0

    def register(self, asset_id: str, *, count_as_usage: bool = True) -> None:
        if count_as_usage:
            self.count_by_asset_id[asset_id] = self.count_by_asset_id.get(asset_id, 0) + 1
        self.last_visual_segment_index_by_asset_id[asset_id] = self.visual_segment_index
        self.visual_segment_index += 1

    def distance_since_last_use(self, asset_id: str) -> int | None:
        last_index = self.last_visual_segment_index_by_asset_id.get(asset_id)
        if last_index is None:
            return None
        return self.visual_segment_index - last_index


def _build_candidate(
    project: Project, asset, folder_name: str, video_head_trim_sec: float
) -> CutPlanAssetCandidate:
    asset_id = asset.asset_id or asset_id_for_path(asset.path)
    full_path = project.project_root_path / asset.path
    exists = full_path.is_file()
    is_image = is_image_media(full_path)
    is_video = exists and not is_image

    duration_sec = 0.0
    if is_video and exists:
        probed = probe_duration_seconds(full_path)
        duration_sec = probed if probed is not None else 0.0

    if is_image:
        asset_type = "image"
        usable_duration_sec = math.inf  # Bilder gelten als beliebig lang haltbar (§3)
    elif is_video:
        asset_type = "video"
        usable_duration_sec = max(0.0, duration_sec - video_head_trim_sec)
    else:
        asset_type = asset.media_type or ""
        usable_duration_sec = 0.0

    return CutPlanAssetCandidate(
        asset_id=asset_id,
        asset_path=str(full_path),
        folder_name=folder_name,
        asset_type=asset_type,
        duration_sec=duration_sec,
        width=0,
        height=0,
        is_image=is_image,
        is_video=is_video,
        exists=exists,
        usable_duration_sec=usable_duration_sec,
        metadata={"description": asset.description, "asset_origin": asset.asset_origin},
    )


def load_asset_lookup_for_cut_plan(
    project: Project, source_plan: ConfirmedVoiceoverProjectPlan, cut_plan: CutPlanDocument
) -> CutPlanAssetLookup:
    """Lädt die Folder-Inventories aller relevanten Ordner (source_plan.folders
    plus source_plan.intro.used_folders für Intro-Beats, §2) rein lesend —
    verändert keine Inventory-Dateien."""
    video_head_trim_sec = float(
        cut_plan.settings_snapshot.get("video_head_trim_sec", CUT_PLAN_DEFAULT_VIDEO_HEAD_TRIM_SEC)
    )

    folder_names: set[str] = {folder.folder_name for folder in source_plan.folders}
    folder_names.update(source_plan.intro.used_folders)

    lookup = CutPlanAssetLookup()
    for folder_name in sorted(folder_names):
        inventory = load_folder_inventory(project, folder_name)
        for asset in inventory.assets:
            candidate = _build_candidate(project, asset, folder_name, video_head_trim_sec)
            lookup.add(candidate)
    return lookup


def resolve_asset_candidate(
    asset_id: str, lookup: CutPlanAssetLookup, *, preferred_folder_name: str = ""
) -> CutPlanAssetCandidate | None:
    """Löst eine asset_id gegen den Lookup auf. Bei Mehrdeutigkeit (dieselbe
    asset_id in mehreren Foldern) wird preferred_folder_name bevorzugt, sonst
    der erste Treffer (deterministisch nach folder_name sortiert) — die
    Mehrdeutigkeit selbst wird über lookup.is_ambiguous() sichtbar gemacht,
    nicht stillschweigend verschluckt."""
    candidates = lookup.candidates_by_id.get(asset_id)
    if not candidates:
        return None
    if len(candidates) == 1:
        return candidates[0]
    if preferred_folder_name:
        for candidate in candidates:
            if candidate.folder_name == preferred_folder_name:
                return candidate
    return sorted(candidates, key=lambda candidate: candidate.folder_name)[0]


def _same_visual_group(item: CutPlanItem, other_item: CutPlanItem) -> bool:
    """True, wenn `other_item` visuell an `item` anschließen darf — dieselbe
    Gruppierung wie beim Mini-Shot-Merge (`_can_merge_with_previous`/
    `_can_merge_with_next`): niemals über die Intro->Folder-Grenze und
    niemals über eine Folder-Grenze hinweg. Für das Visual-Window (§Phase 1)
    bewusst genauso streng, damit ein Satz nie in die Pause VOR einem
    thematisch neuen Ordner/Intro-Beat hineinwächst."""
    if other_item.source_scope != item.source_scope:
        return False
    if item.source_scope == AUDIO_SCOPE_FOLDER and other_item.folder_name != item.folder_name:
        return False
    return True


def compute_visual_window_end_sec(
    item: CutPlanItem, next_item: CutPlanItem | None, settings: CutPlanSettings
) -> float:
    """Nutzervorgabe (Juli 2026): Assets sollen generell bis zum Start des
    NÄCHSTEN Satzes weiterlaufen, statt exakt am eigenen Satzende
    (`item.timeline_end_sec`) zu enden — das deckt die natürliche
    Sprechpause zwischen zwei Sätzen visuell ab, statt sie als
    BLACK_GAP_DURING_VOICEOVER offen zu lassen.

    Reine, seiteneffektfreie Berechnung — ändert NICHTS an `item` oder
    `next_item`. Phase 1: nur die Berechnung; die Verdrahtung in
    `choose_asset_for_cut_item`/die Split-Segment-Dauer folgt erst in
    Phase 2.

    Gibt `item.timeline_end_sec` unverändert zurück (kein Fenster-Ausbau),
    wenn:
    - `settings.extend_visual_window_to_next_sentence` deaktiviert ist,
    - es kein nächstes Item gibt,
    - `next_item` in einer anderen Gruppe liegt (Intro<->Folder- oder
      Folder<->Folder-Grenze, siehe `_same_visual_group`),
    - `next_item` blockiert ist (z. B. MISSING_ALIGNMENT) und seine
      `timeline_start_sec` deshalb nicht verlässlich ist,
    - zwischen beiden Items keine positive Pause besteht (Overlap oder
      exakt anschließend — nichts zu füllen).

    Andernfalls wird die Pause bis maximal
    `settings.max_sentence_pause_extension_sec` ins Fenster von `item`
    hineingezogen — eine längere, redaktionell bedeutsame Pause (z. B.
    Kapitelwechsel) wird NICHT blind komplett überbrückt und bleibt für
    `validate_no_black_gap_during_voiceover`/die Validation-Repair-Pipeline
    sichtbar."""
    default_end = item.timeline_end_sec
    if not settings.extend_visual_window_to_next_sentence:
        return default_end
    if next_item is None:
        return default_end
    if not _same_visual_group(item, next_item):
        return default_end
    if next_item.blockers:
        return default_end

    pause_duration = next_item.timeline_start_sec - item.timeline_end_sec
    if pause_duration <= _DURATION_EPSILON:
        return default_end

    capped_pause = min(pause_duration, settings.max_sentence_pause_extension_sec)
    return item.timeline_end_sec + capped_pause


def compute_visual_window_duration_sec(
    item: CutPlanItem, next_item: CutPlanItem | None, settings: CutPlanSettings
) -> float:
    """`compute_visual_window_end_sec(...) - item.timeline_start_sec` — die
    Dauer, gegen die die Split-/Asset-Auswahl in Phase 2 planen soll,
    statt gegen `item.duration_sec` (das bleibt unverändert der reine
    Audio-Satz, siehe CutPlanItem-Docstring)."""
    window_end = compute_visual_window_end_sec(item, next_item, settings)
    return max(0.0, window_end - item.timeline_start_sec)


def determine_duration_strategy(item: CutPlanItem, settings: CutPlanSettings) -> str:
    """Reine Dauer-Klassifikation (Fall A/B/C) ohne Kenntnis des vorherigen
    Items — die MERGE-Entscheidung (Fall A) erfordert Kontext über das
    vorherige Item und wird deshalb vom Orchestrator
    (apply_asset_selection_to_cut_plan) getroffen, nicht hier."""
    duration = item.duration_sec
    if duration > settings.shot_max_sec + _DURATION_EPSILON:
        return CUT_PLAN_DURATION_STRATEGY_SPLIT
    return CUT_PLAN_DURATION_STRATEGY_SINGLE_SHOT


def _compute_split_segment_durations(
    total_duration: float, shot_min_sec: float, shot_max_sec: float
) -> list[float]:
    """Ziel: möglichst gleichmäßige Segmentdauer zwischen shot_min_sec und
    shot_max_sec (§6 Fall C). 14s -> 2x7s, 20s -> 3x~6.67s."""
    if total_duration <= shot_max_sec + _DURATION_EPSILON:
        return [total_duration]
    num_segments = max(2, math.ceil(total_duration / shot_max_sec))
    while num_segments > 1 and total_duration / num_segments < shot_min_sec:
        num_segments -= 1
    segment_duration = total_duration / num_segments
    return [segment_duration] * num_segments


def _build_transform_hint(candidate: CutPlanAssetCandidate, settings: CutPlanSettings) -> dict[str, Any]:
    """Best-effort Transform-Hinweis (§7) — nur befüllt, wenn Breite/Höhe
    zuverlässig bekannt sind (heute i. d. R. nicht der Fall für lokale
    Inventory-Assets). Erzeugt NIE einen Blocker und transcodiert nichts."""
    if compute_fill_zoom_factor is None or candidate.width <= 0 or candidate.height <= 0:
        return {}
    try:
        zoom_factor = compute_fill_zoom_factor(
            asset_width=candidate.width,
            asset_height=candidate.height,
            target_width=settings.timeline_width,
            target_height=settings.timeline_height,
        )
    except Exception:  # pragma: no cover - defensiv, darf nie einen Blocker erzeugen
        return {}
    if zoom_factor is None:
        return {}
    return {"scaling_mode": "fill", "zoom_factor": zoom_factor}


def build_visual_segments_for_item(
    project: Project, item: CutPlanItem, chosen_assets: list[CutPlanAssetCandidate], settings: CutPlanSettings
) -> list[VisualSegment]:
    """Baut ein VisualSegment pro Eintrag in chosen_assets. Bei einem
    Kandidaten -> ein Segment über die volle Item-Dauer (Fall A/B). Bei
    mehreren Kandidaten -> Split-Segmente mit möglichst gleichmäßiger Dauer
    (Fall C, §6).

    Bugfix (Nutzervorgabe Juli 2026, "Video soll weiterlaufen statt sich zu
    wiederholen"): wenn ein Split-Segment dasselbe Video wie ein FRÜHERES
    Segment DESSELBEN Items fortsetzt (`_select_candidates_for_item` konnte
    keinen eigenständigen Ersatz-Kandidaten finden und hat stattdessen den
    letzten erfolgreichen Kandidaten als Fortsetzung aufgefüllt), muss die
    Quelle dort weiterlaufen, wo das vorherige Segment aufgehört hat
    (`source_in_sec = vorheriges source_out_sec` desselben Assets) — vorher
    wurde `source_in_sec` für JEDES Segment neu auf `video_head_trim_sec`
    zurückgesetzt, wodurch eine Fortsetzung exakt dieselbe Stelle im Video
    noch einmal gezeigt hätte, statt tatsächlich weiterzulaufen. Reicht das
    Video für die kumulierte Fortsetzung nicht mehr aus, erkennt die
    bestehende Validierung (`source_out_sec > echte Videodauer`) das jetzt
    korrekt als ASSET_TOO_SHORT (vorher durch den Reset maskiert) — die
    bestehende Supplement-Pipeline greift dafür bereits (siehe
    cut_plan_supplement_bridge._ASSET_RELATED_SUPPLEMENTABLE_ERROR_TYPES).
    Bilder sind von diesem Fix inhaltlich nicht betroffen (kein Zeitverlauf,
    `source_in_sec` bleibt 0.0), werden aber aus Konsistenzgründen ebenfalls
    in derselben Struktur verfolgt."""
    total_duration = item.duration_sec
    if len(chosen_assets) <= 1:
        durations = [total_duration]
    else:
        durations = _compute_split_segment_durations(total_duration, settings.shot_min_sec, settings.shot_max_sec)

    segments: list[VisualSegment] = []
    cursor = item.timeline_start_sec
    last_source_out_by_asset_id: dict[str, float] = {}
    for index, duration in enumerate(durations):
        candidate = chosen_assets[min(index, len(chosen_assets) - 1)]
        segment_start = cursor
        segment_end = cursor + duration

        is_continuation_segment = candidate.asset_id in last_source_out_by_asset_id

        if candidate.is_video:
            source_in_sec = (
                last_source_out_by_asset_id[candidate.asset_id]
                if is_continuation_segment
                else settings.video_head_trim_sec
            )
            source_out_sec = source_in_sec + duration
        else:
            source_in_sec = 0.0
            source_out_sec = duration
        last_source_out_by_asset_id[candidate.asset_id] = source_out_sec

        if len(chosen_assets) <= 1:
            reason = "primary_asset" if candidate.asset_id == item.primary_asset_id else "backup_asset"
        elif is_continuation_segment:
            reason = "split_long_sentence_continuation"
        else:
            reason = "split_long_sentence"

        transform = _build_transform_hint(candidate, settings)

        segments.append(
            VisualSegment(
                segment_id=f"{item.cut_item_id}_seg_{index + 1:02d}",
                timeline_in_sec=segment_start,
                timeline_out_sec=segment_end,
                duration_sec=duration,
                asset_id=candidate.asset_id,
                asset_path=candidate.asset_path,
                asset_type="video" if candidate.is_video else "image",
                source_in_sec=source_in_sec,
                source_out_sec=source_out_sec,
                track="V1",
                transform=transform,
                background_style="",
                reason=reason,
            )
        )
        cursor = segment_end

    return segments


def _candidate_usability_error(candidate: CutPlanAssetCandidate, needed_duration_sec: float) -> str | None:
    if not candidate.exists:
        return CUT_PLAN_ERROR_ASSET_FILE_MISSING
    if candidate.is_video:
        if candidate.usable_duration_sec <= 0:
            return CUT_PLAN_ERROR_ASSET_TOO_SHORT
        if needed_duration_sec > candidate.usable_duration_sec + _DURATION_EPSILON:
            return CUT_PLAN_ERROR_ASSET_TOO_SHORT
    # Bilder gelten als beliebig lang haltbar -> kein Dauer-Check (§3).
    return None


def _usage_violation(
    asset_id: str, usage_tracker: UsageTracker, settings: CutPlanSettings, *, is_continuation: bool
) -> str | None:
    if is_continuation:
        return None  # bewusste Split-/Merge-Fortsetzung desselben CutPlanItems ist ausgenommen (§5)
    if usage_tracker.count_by_asset_id.get(asset_id, 0) >= settings.max_asset_usage:
        return CUT_PLAN_ERROR_MAX_ASSET_USAGE_EXCEEDED
    distance = usage_tracker.distance_since_last_use(asset_id)
    if distance is not None:
        # "No consecutive reuse" gilt immer (min. Abstand 1); min_asset_reuse_distance_shots
        # erhöht die Mindestdistanz zusätzlich, falls konfiguriert.
        min_required_distance = max(1, settings.min_asset_reuse_distance_shots)
        if distance <= min_required_distance:
            return CUT_PLAN_ERROR_ASSET_REUSE_DISTANCE_TOO_SHORT
    return None


def _dedupe_preserve_order(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if value and value not in seen:
            seen.add(value)
            result.append(value)
    return result


def _general_asset_pool(item: CutPlanItem) -> list[str]:
    """Flacher Fallback-Pool aller vom Autor vorgeschlagenen Kandidaten in
    Präferenz-Reihenfolge (Primary -> Backup -> Second Backup).

    Phase 7 (Cut-Plan-Split-Fix): second_backup_asset_ids zählen jetzt
    ebenfalls als echte Alternativen — vorher wurden sie hier komplett
    ignoriert, obwohl der Autor sie bewusst als genuinely passend markiert
    hat (siehe SentenceItem.second_backup_asset_ids, Phase 4)."""
    ordered = ([item.primary_asset_id] if item.primary_asset_id else [])
    ordered += list(item.backup_asset_ids)
    ordered += list(item.second_backup_asset_ids)
    return _dedupe_preserve_order(ordered)


def _candidate_order_for_segment(
    item: CutPlanItem, segment_index: int, general_pool: list[str]
) -> list[str]:
    """Kandidaten-Präferenzreihenfolge für GENAU EIN Segment (0-basierter
    Index innerhalb eines möglichen Splits).

    Phase 7: nutzt zuerst die vom Autor PRO SHOT geplanten Assets
    (item.planned_segments — leer bei allen vor Phase 7 erzeugten Items),
    danach den allgemeinen Fallback-Pool (dedupliziert, gleiche
    Präferenz-Reihenfolge wie zuvor). Für Ein-Shot-Items (segment_index=0,
    kein passender planned_segments-Eintrag) ist das Ergebnis identisch zum
    bisherigen Verhalten."""
    planned = next(
        (segment for segment in item.planned_segments if segment.segment_order == segment_index + 1),
        None,
    )
    ordered: list[str] = []
    if planned is not None:
        if planned.primary_asset_id:
            ordered.append(planned.primary_asset_id)
        ordered.extend(planned.backup_asset_ids)
    ordered.extend(general_pool)
    return _dedupe_preserve_order(ordered)


def _select_candidates_for_item(
    item: CutPlanItem,
    lookup: CutPlanAssetLookup,
    usage_tracker: UsageTracker,
    settings: CutPlanSettings,
    *,
    segment_durations: list[float],
    preferred_folder_name: str,
) -> tuple[list[CutPlanAssetCandidate], list[str], str | None, bool, bool]:
    """Wählt EINEN Kandidaten je Eintrag in segment_durations.

    Phase 7 (Cut-Plan-Split-Fix): jeder Kandidat wird gegen die Dauer
    SEINES EIGENEN Segments geprüft, nicht mehr gegen die volle Satzdauer —
    vorher wurde z. B. für einen 12s-Satz, der in 6s+6s gesplittet wird,
    jeder Kandidat fälschlich gegen die vollen 12s geprüft, wodurch zwei an
    sich passende 6-7s-Videos zu Unrecht abgelehnt wurden.

    Gibt (chosen_assets, warnings, last_failure_type, tried_any,
    primary_was_first_choice) zurück. Registriert erfolgreiche Kandidaten
    sofort im usage_tracker — die bestehenden Regeln (kein unmittelbares
    Wiederverwenden, max_asset_usage, min_asset_reuse_distance_shots)
    gelten dabei unverändert auch zwischen benachbarten Segmenten
    desselben Items."""
    general_pool = _general_asset_pool(item)
    num_segments = len(segment_durations)

    chosen_assets: list[CutPlanAssetCandidate] = []
    warnings: list[str] = []
    last_failure_type: str | None = None
    tried_any = False
    primary_was_first_choice = False

    for segment_index in range(num_segments):
        candidate_ids = _candidate_order_for_segment(item, segment_index, general_pool)
        segment_duration = segment_durations[segment_index]
        filled = False

        for asset_id in candidate_ids:
            tried_any = True
            candidate = resolve_asset_candidate(asset_id, lookup, preferred_folder_name=preferred_folder_name)
            if candidate is None:
                warnings.append(CUT_PLAN_ERROR_INVALID_ASSET_ID)
                last_failure_type = CUT_PLAN_ERROR_INVALID_ASSET_ID
                continue
            if lookup.is_ambiguous(asset_id):
                warnings.append(CUT_PLAN_ERROR_AMBIGUOUS_ASSET_ID)

            failure = _candidate_usability_error(candidate, segment_duration)
            if failure is not None:
                warnings.append(failure)
                last_failure_type = failure
                continue

            usage_failure = _usage_violation(asset_id, usage_tracker, settings, is_continuation=False)
            if usage_failure is not None:
                warnings.append(usage_failure)
                last_failure_type = usage_failure
                continue

            if segment_index == 0 and asset_id == item.primary_asset_id:
                primary_was_first_choice = True
            chosen_assets.append(candidate)
            usage_tracker.register(asset_id, count_as_usage=True)
            filled = True
            break

        if not filled:
            # Kein Kandidat für dieses Segment nutzbar -> restliche Segmente
            # unten per Continuation auffüllen (unverändertes Verhalten).
            break

    # Fehlende Segmente mit dem letzten erfolgreichen Kandidaten als
    # Split-Fortsetzung auffüllen (§6 Fall C) — zählt nicht als zusätzliche
    # redaktionelle Wiederverwendung.
    while chosen_assets and len(chosen_assets) < num_segments:
        continuation = chosen_assets[-1]
        chosen_assets.append(continuation)
        usage_tracker.register(continuation.asset_id, count_as_usage=False)

    return chosen_assets, warnings, last_failure_type, tried_any, primary_was_first_choice


def _preferred_folder_for_item(item: CutPlanItem) -> str:
    if item.source_scope == AUDIO_SCOPE_FOLDER:
        return item.folder_name
    # Intro: bevorzugt den Ordner, aus dem der jeweilige source_ref stammt (falls bekannt).
    for source_ref in item.source_refs:
        if source_ref.folder_name:
            return source_ref.folder_name
    return ""


def _blocked_copy(item: CutPlanItem, reason: str) -> CutPlanItem:
    return item.model_copy(
        update={
            "asset_selection_status": CUT_PLAN_ASSET_SELECTION_BLOCKED,
            "chosen_asset_id": "",
            "asset_selection_reason": reason,
            "fallback_reason": "",
            "planned_visual_segments": [],
        }
    )


def _supplement_required_copy(
    item: CutPlanItem,
    *,
    reason: str,
    extra_warnings: list[str],
    supplement_reason: str | None = None,
) -> CutPlanItem:
    """Baut die SUPPLEMENT_REQUIRED-Variante eines Items.

    Setzt IMMER needs_supplement_asset=True — das hält den nachgelagerten
    Validator konsistent: validate_cut_items prüft `chosen_asset_id` UND
    `needs_supplement_asset` unabhängig vom asset_selection_status, würde
    also ohne dieses Feld fälschlich zusätzlich MISSING_ASSET_MAPPING neben
    dem hier bereits gesetzten SUPPLEMENT_REQUIRED-Blocker melden (Phase A,
    Nutzervorgabe: Reuse-/Usage-Blocker sollen supplementierbar werden statt
    dauerhaft BLOCKED zu bleiben).

    supplement_reason (optional) überschreibt item.supplement_reason explizit
    — genutzt vom Reuse-/Usage-Verletzungsfall, damit die spätere Supplement-
    Suche (build_supplement_requests_from_cut_plan) einen konkreten, vom
    generischen asset_selection_reason unabhängigen Grund erhält (z. B.
    'Verletzt Reuse-Regel, benötigt ein DISTINKTES Ersatz-Asset')."""
    warnings = list(item.warnings) + extra_warnings
    blockers = list(item.blockers)
    if CUT_PLAN_ERROR_SUPPLEMENT_REQUIRED not in blockers:
        blockers.append(CUT_PLAN_ERROR_SUPPLEMENT_REQUIRED)
    update: dict[str, Any] = {
        "asset_selection_status": CUT_PLAN_ASSET_SELECTION_SUPPLEMENT_REQUIRED,
        "chosen_asset_id": "",
        "asset_selection_reason": reason,
        "fallback_reason": "",
        "needs_supplement_asset": True,
        "planned_visual_segments": [],
        "warnings": warnings,
        "blockers": blockers,
    }
    if supplement_reason is not None:
        update["supplement_reason"] = supplement_reason
    return item.model_copy(update=update)


def choose_asset_for_cut_item(
    project: Project,
    item: CutPlanItem,
    lookup: CutPlanAssetLookup,
    usage_tracker: UsageTracker,
    settings: CutPlanSettings,
) -> CutPlanItem:
    """Wählt ein Asset (primary -> backups -> Supplement) für EIN CutPlanItem
    und baut die planned_visual_segments. Ruft keine Supplement-Suche auf —
    bei needs_supplement_asset oder fehlendem nutzbarem Asset bleibt
    asset_selection_status = SUPPLEMENT_REQUIRED, chosen_asset_id leer."""
    duration_strategy = determine_duration_strategy(item, settings)

    # Ein bereits in Phase 8.2 gesetzter Blocker (z. B. MISSING_ALIGNMENT)
    # bedeutet: keine verlässliche Timeline-Zeit vorhanden -> keine
    # Asset-Auswahl versuchen.
    if item.blockers:
        blocked = _blocked_copy(item, "Zeit-Mapping aus Phase 8.2 ist blockiert (siehe Item-Blocker).")
        return blocked.model_copy(update={"duration_strategy": duration_strategy})

    if item.needs_supplement_asset:
        extra_warnings = []
        if not item.supplement_reason.strip():
            extra_warnings.append(CUT_PLAN_ERROR_SUPPLEMENT_REASON_MISSING)
        updated = _supplement_required_copy(
            item, reason="needs_supplement_asset ist gesetzt.", extra_warnings=extra_warnings
        )
        return updated.model_copy(update={"duration_strategy": duration_strategy})

    segment_durations = (
        _compute_split_segment_durations(item.duration_sec, settings.shot_min_sec, settings.shot_max_sec)
        if duration_strategy == CUT_PLAN_DURATION_STRATEGY_SPLIT
        else [item.duration_sec]
    )
    preferred_folder_name = _preferred_folder_for_item(item)

    chosen_assets, selection_warnings, last_failure_type, tried_any, primary_was_first_choice = (
        _select_candidates_for_item(
            item,
            lookup,
            usage_tracker,
            settings,
            segment_durations=segment_durations,
            preferred_folder_name=preferred_folder_name,
        )
    )

    warnings = list(item.warnings) + selection_warnings

    if not chosen_assets:
        if not tried_any:
            updated = _supplement_required_copy(
                item, reason="Weder primary_asset_id noch backup_asset_ids vorhanden.", extra_warnings=[]
            )
            return updated.model_copy(update={"duration_strategy": duration_strategy, "warnings": warnings})
        if last_failure_type in (
            CUT_PLAN_ERROR_MAX_ASSET_USAGE_EXCEEDED,
            CUT_PLAN_ERROR_ASSET_REUSE_DISTANCE_TOO_SHORT,
        ):
            # Phase A (Nutzervorgabe): früher landete dieser Fall dauerhaft
            # als BLOCKED — ein an sich passendes lokales Asset durfte wegen
            # der Reuse-/Usage-Regeln nicht verwendet werden, und es gab
            # keinen automatischen Ausweg außer manuellem Eingreifen. Jetzt
            # wird stattdessen ein Supplement Request ausgelöst: die
            # Auto-Resolve-Pipeline (Adobe/Pexels) sucht ein zusätzliches,
            # DISTINKTES Ersatz-Asset für genau dieses Item, statt eines der
            # bereits zu oft/zu früh verwendeten lokalen Assets zu erzwingen.
            updated = _supplement_required_copy(
                item,
                reason=f"Alle Kandidaten verletzen Usage-Regeln ({last_failure_type}).",
                # extra_warnings bewusst leer — last_failure_type steckt bereits in
                # selection_warnings/warnings (siehe unten, gleiche Konvention wie die
                # beiden anderen _supplement_required_copy-Aufrufe in dieser Funktion).
                extra_warnings=[],
                supplement_reason=(
                    "Passende lokale Assets sind vorhanden, verletzen aber die Wiederverwendungs-Regeln "
                    f"({last_failure_type}). Es wird ein zusätzliches, DISTINKTES Ersatz-Asset für diese "
                    "Szene benötigt (kein bereits verwendetes Motiv erneut)."
                ),
            )
            return updated.model_copy(update={"duration_strategy": duration_strategy, "warnings": warnings})
        updated = _supplement_required_copy(
            item,
            reason="Kein primary/backup Asset ist nutzbar (siehe Warnings).",
            extra_warnings=[],
        )
        return updated.model_copy(update={"duration_strategy": duration_strategy, "warnings": warnings})

    segments = build_visual_segments_for_item(project, item, chosen_assets, settings)
    chosen_primary = chosen_assets[0]

    if primary_was_first_choice:
        asset_selection_status = CUT_PLAN_ASSET_SELECTION_PRIMARY_USED
        asset_selection_reason = f"primary_asset_id '{chosen_primary.asset_id}' ist vorhanden und nutzbar."
        fallback_reason = ""
    else:
        asset_selection_status = CUT_PLAN_ASSET_SELECTION_BACKUP_USED
        asset_selection_reason = f"backup_asset_id '{chosen_primary.asset_id}' verwendet, da primary nicht nutzbar war."
        fallback_reason = (
            f"primary_asset_id '{item.primary_asset_id}' nicht verwendet: {last_failure_type or 'nicht vorhanden'}."
            if item.primary_asset_id
            else "Kein primary_asset_id vorhanden."
        )

    if duration_strategy == CUT_PLAN_DURATION_STRATEGY_SINGLE_SHOT and item.duration_sec < settings.shot_min_sec:
        if item.duration_sec < 1.0:
            item_blockers = list(item.blockers) + [CUT_PLAN_ERROR_SHOT_TOO_SHORT]
        else:
            item_blockers = list(item.blockers)
            warnings = warnings + [CUT_PLAN_ERROR_SHOT_TOO_SHORT]
    else:
        item_blockers = list(item.blockers)

    return item.model_copy(
        update={
            "duration_strategy": duration_strategy,
            "planned_visual_segments": segments,
            "chosen_asset_id": chosen_primary.asset_id,
            "asset_selection_status": asset_selection_status,
            "asset_selection_reason": asset_selection_reason,
            "fallback_reason": fallback_reason,
            "warnings": warnings,
            "blockers": item_blockers,
        }
    )


def _can_merge_with_previous(
    item: CutPlanItem, previous_item: CutPlanItem | None, settings: CutPlanSettings
) -> bool:
    if previous_item is None:
        return False
    if previous_item.source_scope != item.source_scope:
        return False  # nicht über Intro->Folder-Grenze mergen
    if item.source_scope == AUDIO_SCOPE_FOLDER and previous_item.folder_name != item.folder_name:
        return False  # nicht über Folder-Grenzen mergen
    if previous_item.asset_selection_status == CUT_PLAN_ASSET_SELECTION_BLOCKED:
        return False
    if not previous_item.planned_visual_segments:
        return False
    same_asset = (
        previous_item.primary_asset_id
        and previous_item.primary_asset_id == item.primary_asset_id
        or (previous_item.chosen_asset_id and previous_item.chosen_asset_id == item.chosen_asset_id)
    )
    if not same_asset:
        return False
    last_segment = previous_item.planned_visual_segments[-1]
    merged_duration = (last_segment.timeline_out_sec - last_segment.timeline_in_sec) + item.duration_sec
    if merged_duration > settings.shot_max_sec + _DURATION_EPSILON:
        return False
    return True


def _merge_with_previous(item: CutPlanItem, previous_item: CutPlanItem) -> CutPlanItem:
    """Modelliert den Merge als eigenen Segment-Eintrag für das aktuelle
    Item (reason='merged_short_sentence'), der denselben chosen_asset_id wie
    das vorherige Item weiterführt — keine erneute Usage-Zählung, da dies
    dieselbe redaktionelle Asset-Entscheidung fortsetzt (§6 Fall A).

    Bugfix (Nutzervorgabe Juli 2026, siehe build_visual_segments_for_item):
    bei einem Video muss die Quelle dort weiterlaufen, wo das vorherige
    Segment aufgehört hat (`previous_segment.source_out_sec`), NICHT wieder
    an dessen Anfang (`previous_segment.source_in_sec`) — sonst würde das
    gemergte Segment exakt dieselbe Stelle im Video noch einmal zeigen.
    Bilder bleiben unverändert bei `source_in_sec = 0.0` (kein Zeitverlauf,
    außerdem von validate_visual_segments hart gefordert)."""
    chosen_asset_id = previous_item.chosen_asset_id or previous_item.primary_asset_id
    previous_segment = previous_item.planned_visual_segments[-1]
    if previous_segment.asset_type == "video":
        source_in_sec = previous_segment.source_out_sec
    else:
        source_in_sec = 0.0
    merged_segment = VisualSegment(
        segment_id=f"{item.cut_item_id}_seg_01",
        timeline_in_sec=item.timeline_start_sec,
        timeline_out_sec=item.timeline_end_sec,
        duration_sec=item.duration_sec,
        asset_id=chosen_asset_id,
        asset_path=previous_segment.asset_path,
        asset_type=previous_segment.asset_type,
        source_in_sec=source_in_sec,
        source_out_sec=source_in_sec + item.duration_sec,
        track="V1",
        transform=dict(previous_segment.transform),
        background_style=previous_segment.background_style,
        reason="merged_short_sentence",
    )
    return item.model_copy(
        update={
            "duration_strategy": CUT_PLAN_DURATION_STRATEGY_MERGED,
            "planned_visual_segments": [merged_segment],
            "chosen_asset_id": chosen_asset_id,
            "asset_selection_status": previous_item.asset_selection_status,
            "asset_selection_reason": f"Mit vorherigem CutPlanItem gemerged (Asset '{chosen_asset_id}').",
            "fallback_reason": "",
            "warnings": list(item.warnings) + [CUT_PLAN_ERROR_SHOT_TOO_SHORT],
        }
    )


def _can_merge_with_next(
    item: CutPlanItem, next_item: CutPlanItem | None, settings: CutPlanSettings
) -> bool:
    """Spiegelbild von `_can_merge_with_previous` — geprüft für ein Item,
    dessen EIGENES VisualSegment nach der normalen Auswahl kürzer als 1.0s
    ist (harter SHOT_TOO_SHORT-Blocker, §12) UND das NICHT rückwärts mit dem
    vorherigen Item verschmolzen werden konnte (z. B. erstes Item eines
    Ordners, anderes Asset, geblockter Vorgänger). Phase C (Nutzervorgabe):
    solche Mini-Shots sollen nicht als Blocker stehen bleiben, wenn eine
    Verschmelzung mit dem NÄCHSTEN Item möglich ist — bewusst OHNE
    same_asset-Anforderung (anders als beim Rückwärts-Merge), weil der
    Mini-Shot ohnehin kein eigenes brauchbares Asset beisteuert."""
    if next_item is None:
        return False
    if next_item.source_scope != item.source_scope:
        return False  # nicht über Intro->Folder-Grenze mergen
    if item.source_scope == AUDIO_SCOPE_FOLDER and next_item.folder_name != item.folder_name:
        return False  # nicht über Folder-Grenzen mergen
    if next_item.asset_selection_status in (
        CUT_PLAN_ASSET_SELECTION_BLOCKED,
        CUT_PLAN_ASSET_SELECTION_SUPPLEMENT_REQUIRED,
    ):
        return False
    if not next_item.planned_visual_segments:
        return False
    next_segment = next_item.planned_visual_segments[0]
    merged_duration = item.duration_sec + (next_segment.timeline_out_sec - next_segment.timeline_in_sec)
    if merged_duration > settings.shot_max_sec + _DURATION_EPSILON:
        return False
    return True


def _merge_with_next(item: CutPlanItem, next_item: CutPlanItem) -> CutPlanItem:
    """Analog zu `_merge_with_previous` (siehe dort), nur in die andere
    Richtung: der Mini-Shot übernimmt das Asset des NÄCHSTEN Items, damit
    er visuell nahtlos in dessen Aufnahme übergeht, statt als eigenständiger
    Blocker (< 1.0s) stehen zu bleiben. Wie beim Rückwärts-Merge zählt dies
    NICHT als zusätzliche redaktionelle Wiederverwendung (reason=
    'merged_short_sentence' ist sowohl bei validate_visual_segments als auch
    bei validate_asset_usage als Fortsetzungs-Marker hinterlegt, siehe
    cut_plan_validator._CONTINUATION_REASONS).

    Bugfix (Nutzervorgabe Juli 2026, siehe build_visual_segments_for_item/
    _merge_with_previous): der Mini-Shot liegt auf der Timeline VOR
    `next_item` — bei einem Video muss seine Quelle deshalb GENAU DA enden,
    wo `next_segment` beginnt (`next_segment.source_in_sec`), damit der
    Übergang lückenlos weiterläuft, statt dieselbe Stelle zu wiederholen.
    Reicht die Kopfzeit des Videos davor nicht aus (negativer source_in_sec
    wäre ungültig), wird auf 0.0 geklemmt — führt im Extremfall zu einem
    kleinen Sprung statt einer Wiederholung, was für einen Mini-Shot
    (< 1.0s) vernachlässigbar ist. Bilder bleiben bei `source_in_sec = 0.0`
    (kein Zeitverlauf, außerdem von validate_visual_segments hart
    gefordert)."""
    chosen_asset_id = next_item.chosen_asset_id or next_item.primary_asset_id
    next_segment = next_item.planned_visual_segments[0]
    if next_segment.asset_type == "video":
        source_in_sec = max(0.0, next_segment.source_in_sec - item.duration_sec)
    else:
        source_in_sec = 0.0
    merged_segment = VisualSegment(
        segment_id=f"{item.cut_item_id}_seg_01",
        timeline_in_sec=item.timeline_start_sec,
        timeline_out_sec=item.timeline_end_sec,
        duration_sec=item.duration_sec,
        asset_id=chosen_asset_id,
        asset_path=next_segment.asset_path,
        asset_type=next_segment.asset_type,
        source_in_sec=source_in_sec,
        source_out_sec=source_in_sec + item.duration_sec,
        track="V1",
        transform=dict(next_segment.transform),
        background_style=next_segment.background_style,
        reason="merged_short_sentence",
    )
    remaining_warnings = [w for w in item.warnings if w != CUT_PLAN_ERROR_SHOT_TOO_SHORT]
    remaining_blockers = [b for b in item.blockers if b != CUT_PLAN_ERROR_SHOT_TOO_SHORT]
    return item.model_copy(
        update={
            "duration_strategy": CUT_PLAN_DURATION_STRATEGY_MERGED,
            "planned_visual_segments": [merged_segment],
            "chosen_asset_id": chosen_asset_id,
            "asset_selection_status": next_item.asset_selection_status,
            "asset_selection_reason": f"Mit nachfolgendem CutPlanItem gemerged (Asset '{chosen_asset_id}').",
            "fallback_reason": "",
            "warnings": remaining_warnings + [CUT_PLAN_ERROR_SHOT_TOO_SHORT],
            "blockers": remaining_blockers,
        }
    )


def merge_mini_shots_with_next_item(
    items: list[CutPlanItem], settings: CutPlanSettings
) -> list[CutPlanItem]:
    """Phase C (Nutzervorgabe): schließt die Lücke, die der Rückwärts-Merge
    in der Hauptschleife (siehe apply_asset_selection_to_cut_plan) nicht
    abdeckt — läuft NACH dieser Schleife, weil dafür das bereits gewählte
    VisualSegment des NÄCHSTEN Items bekannt sein muss. Betrifft
    ausschließlich SINGLE_SHOT-Items mit exakt einem VisualSegment, dessen
    Dauer den harten SHOT_TOO_SHORT-Blocker (< 1.0s) auslösen würde — die
    weichere Warn-Schwelle (>= 1.0s, aber < shot_min_sec) bleibt bewusst
    unverändert (redaktionell akzeptierte Flexibilität, §12)."""
    updated = list(items)
    for index, item in enumerate(updated):
        if item.duration_strategy != CUT_PLAN_DURATION_STRATEGY_SINGLE_SHOT:
            continue
        if len(item.planned_visual_segments) != 1:
            continue
        if item.planned_visual_segments[0].duration_sec >= 1.0 - _DURATION_EPSILON:
            continue
        next_item = updated[index + 1] if index + 1 < len(updated) else None
        if not _can_merge_with_next(item, next_item, settings):
            continue
        updated[index] = _merge_with_next(item, next_item)  # type: ignore[arg-type]
    return updated


def update_asset_usage_summary(cut_plan: CutPlanDocument) -> dict[str, int]:
    """Zählt tatsächlich platzierte VisualSegments pro asset_id — Split-/
    Merge-Fortsetzungen sind hier bewusst NICHT dedupliziert (das Segment ist
    sichtbar), anders als bei der redaktionellen Usage-Zählung während der
    Auswahl (siehe UsageTracker.register(count_as_usage=False))."""
    summary: dict[str, int] = {}
    for item in cut_plan.items:
        for segment in item.planned_visual_segments:
            if not segment.asset_id:
                continue
            summary[segment.asset_id] = summary.get(segment.asset_id, 0) + 1
    return summary


def settings_from_snapshot(project: Project, cut_plan: CutPlanDocument) -> CutPlanSettings:
    """Baut CutPlanSettings aus cut_plan.settings_snapshot — der eingefrorene
    Stand zum Zeitpunkt der Draft-Erzeugung, NICHT die ggf. inzwischen
    geänderte cut_plan_settings.json (Vorab-Hardening vor Phase 8.4, siehe
    is_cut_plan_settings_stale in cut_plan_builder.py)."""
    return CutPlanSettings(project_id=project.id, **cut_plan.settings_snapshot)


def aggregate_item_level_errors(
    items: list[CutPlanItem],
) -> tuple[list[CutPlanValidationError], list[CutPlanValidationError]]:
    """Sammelt item.warnings/item.blockers (einfache Typ-Strings) zu
    vollständigen CutPlanValidationError-Einträgen auf Dokument-Ebene.
    Gemeinsam genutzt von apply_asset_selection_to_cut_plan (Phase 8.3) und
    apply_accepted_supplement_to_cut_plan_item (Phase 8.6), damit beide
    Stellen dieselbe Aggregations-Semantik verwenden."""
    warnings: list[CutPlanValidationError] = []
    blockers: list[CutPlanValidationError] = []
    for item in items:
        scope = "sentence" if item.source_scope == AUDIO_SCOPE_FOLDER else "intro"
        for warning_type in item.warnings:
            warnings.append(
                CutPlanValidationError(
                    type=warning_type,
                    severity=READINESS_SEVERITY_WARNING,
                    scope=scope,
                    cut_item_id=item.cut_item_id,
                    folder_name=item.folder_name,
                    message=f"{item.cut_item_id}: {warning_type}",
                )
            )
        for blocker_type in item.blockers:
            blockers.append(
                CutPlanValidationError(
                    type=blocker_type,
                    severity=READINESS_SEVERITY_BLOCKER,
                    scope=scope,
                    cut_item_id=item.cut_item_id,
                    folder_name=item.folder_name,
                    message=f"{item.cut_item_id}: {blocker_type}",
                )
            )
    return warnings, blockers


def apply_asset_selection_to_cut_plan(project: Project, cut_plan: CutPlanDocument) -> CutPlanDocument:
    """Wendet Asset-Auswahl, Fallback-Logik sowie Dauer-/Split-/Merge-
    Strategie auf einen bestehenden Cut-Plan-Entwurf an. Lädt den
    bestätigten Voice-over-Projektplan selbst, verwendet aber IMMER
    cut_plan.settings_snapshot statt der aktuellen cut_plan_settings.json —
    reine Funktion, speichert nichts."""
    source_plan = load_confirmed_voiceover_project_plan(project)
    if source_plan is None:
        raise ValueError(
            "Kein bestätigter Voice-over-Projektplan (confirmed_voiceover_project_plan.json) vorhanden."
        )

    settings = settings_from_snapshot(project, cut_plan)
    lookup = load_asset_lookup_for_cut_plan(project, source_plan, cut_plan)
    usage_tracker = UsageTracker()

    updated_items: list[CutPlanItem] = []
    previous_item: CutPlanItem | None = None

    for item in cut_plan.items:
        duration_strategy = determine_duration_strategy(item, settings)
        merge_eligible = (
            not item.blockers
            and not item.needs_supplement_asset
            and duration_strategy == CUT_PLAN_DURATION_STRATEGY_SINGLE_SHOT
            and item.duration_sec < settings.shot_min_sec
            and _can_merge_with_previous(item, previous_item, settings)
        )
        if merge_eligible:
            updated_item = _merge_with_previous(item, previous_item)  # type: ignore[arg-type]
        else:
            updated_item = choose_asset_for_cut_item(project, item, lookup, usage_tracker, settings)
        updated_items.append(updated_item)
        previous_item = updated_item

    # Phase C (Nutzervorgabe): Mini-Shots (< 1.0s), die der Rückwärts-Merge
    # oben nicht abdecken konnte, werden hier — mit Kenntnis der bereits
    # gewählten NÄCHSTEN Items — nachträglich vorwärts verschmolzen, statt
    # als dauerhafter SHOT_TOO_SHORT-Blocker stehen zu bleiben.
    updated_items = merge_mini_shots_with_next_item(updated_items, settings)

    # Visual Coverage Fix (Phase 8.5): läuft NACH der Asset-Auswahl, BEVOR
    # validiert wird — erweitert bereits gewählte VisualSegments über den
    # initialen Audio-Vorlauf und die Sektions-Pausen, statt eine neue
    # redaktionelle Asset-Entscheidung zu treffen. Audio-Zeiten bleiben
    # unverändert.
    coverage_cut_plan = apply_visual_coverage_extensions(
        cut_plan.model_copy(update={"items": updated_items}), settings
    )
    updated_items = coverage_cut_plan.items

    asset_usage_summary = update_asset_usage_summary(coverage_cut_plan)

    warnings, blockers = aggregate_item_level_errors(updated_items)

    status = CUT_PLAN_STATUS_NEEDS_REVIEW if blockers else CUT_PLAN_STATUS_DRAFT

    return cut_plan.model_copy(
        update={
            "items": updated_items,
            "asset_usage_summary": asset_usage_summary,
            "warnings": warnings,
            "blockers": blockers,
            "status": status,
        }
    )
