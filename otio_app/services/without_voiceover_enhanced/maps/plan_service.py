"""Kartenplan aus der bestätigten Dramaturgie — schreibt die Dramaturgie nie."""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime, timezone
from typing import Callable

from otio_app.models import Project
from otio_app.services.voiceover_generation.dramaturgy_service import (
    load_confirmed_dramaturgy,
)
from otio_app.services.voiceover_generation.models import DramaturgyPlan
from otio_app.services.voiceover_generation.project_brief_defaults_service import (
    normalize_brief_language,
)
from otio_app.services.without_voiceover_enhanced.io_utils import load_model, write_json
from otio_app.services.without_voiceover_enhanced.maps.models import (
    CONFIDENCE_AUTO_RENDER_MIN,
    COORDINATE_STATUS_CONFIRMED,
    COORDINATE_STATUS_MANUAL,
    COORDINATE_STATUS_MISSING,
    COORDINATE_STATUS_NEEDS_REVIEW,
    COORDINATE_STATUS_RESOLVED,
    MAP_4K_HEIGHT,
    MAP_4K_WIDTH,
    MAP_ANIMATION_OPENING,
    MAP_ANIMATION_TRANSITION,
    MAP_DURATION_FRAMES,
    MAP_FPS,
    MAP_HD_HEIGHT,
    MAP_HD_WIDTH,
    MAP_HEADING_BY_LANGUAGE,
    MAP_MAX_PARALLEL_4K,
    MAP_MAX_PARALLEL_HD,
    MAP_RESOLUTION_4K,
    MAP_RESOLUTION_HD,
    MAP_STYLE_VERSION,
    ENGINE_STYLE_VERSION,
    RENDER_STATUS_BLOCKED,
    RENDER_STATUS_DONE,
    RENDER_STATUS_IDLE,
    MapCoordinateRecord,
    MapCoordinatesDocument,
    MapPlanDocument,
    MapPlanItem,
    MapRenderSettings,
)
from otio_app.services.without_voiceover_enhanced.paths import (
    map_coordinates_path,
    map_output_dir,
    map_plan_path,
    map_settings_path,
)

MAP_ID_NAMESPACE = uuid.UUID("7e4c1f8a-2d9b-4c6e-8f31-9a0b5d7e2c14")

GeocodeHit = dict[str, object]
GeocodeFn = Callable[[str, str], GeocodeHit | None]


class MapPlanError(RuntimeError):
    pass


def map_language_prefix(language: str) -> str:
    return normalize_brief_language(language).lower()


def map_heading(language: str) -> str:
    key = normalize_brief_language(language)
    return MAP_HEADING_BY_LANGUAGE.get(key, MAP_HEADING_BY_LANGUAGE["EN"])


def map_output_filename(language: str, original_chapter_name: str) -> str:
    prefix = map_language_prefix(language)
    label = (original_chapter_name or "").strip() or "chapter"
    return f"{prefix}_{label}_Map.mp4"


def clamp_max_parallel(resolution: str, requested: int | None) -> int:
    cap = (
        MAP_MAX_PARALLEL_4K
        if (resolution or "").strip().lower() == MAP_RESOLUTION_4K
        else MAP_MAX_PARALLEL_HD
    )
    if requested is None:
        return cap
    try:
        value = int(requested)
    except (TypeError, ValueError):
        return cap
    return max(1, min(value, cap))


def _resolution_size(resolution: str) -> tuple[int, int]:
    if (resolution or "").strip().lower() == MAP_RESOLUTION_4K:
        return MAP_4K_WIDTH, MAP_4K_HEIGHT
    return MAP_HD_WIDTH, MAP_HD_HEIGHT


def _enabled_chapters(plan: DramaturgyPlan) -> list:
    return sorted(
        (entry for entry in plan.recommended_folder_order if entry.enabled),
        key=lambda entry: entry.order_index,
    )


def dramaturgy_fingerprint(plan: DramaturgyPlan) -> str:
    payload = [
        {
            "folder_name": entry.folder_name,
            "order_index": entry.order_index,
            "enabled": entry.enabled,
        }
        for entry in sorted(plan.recommended_folder_order, key=lambda e: e.order_index)
    ]
    blob = json.dumps(
        {"language": plan.language, "folders": payload},
        ensure_ascii=False,
        sort_keys=True,
    )
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def compute_plan_hash(item: MapPlanItem) -> str:
    payload = {
        "chapter_id": item.chapter_id,
        "chapter_ordinal": item.chapter_ordinal,
        "chapter_count": item.chapter_count,
        "original_chapter_label": item.original_chapter_label,
        "localized_display_label": item.localized_display_label,
        "heading": item.heading,
        "language": item.language,
        "country": item.country,
        "from_chapter_id": item.from_chapter_id,
        "start_latitude": item.start_latitude,
        "start_longitude": item.start_longitude,
        "end_latitude": item.end_latitude,
        "end_longitude": item.end_longitude,
        "animation_mode": item.animation_mode,
        "show_vehicle": item.show_vehicle,
        "duration_in_frames": item.duration_in_frames,
        "fps": item.fps,
        "resolution": item.resolution,
        "width": item.width,
        "height": item.height,
        "style_version": item.style_version,
        "engine_style_version": ENGINE_STYLE_VERSION,
        "output_filename": item.output_filename,
    }
    blob = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def map_item_hash_is_current(item: MapPlanItem) -> bool:
    stored = str(item.plan_hash or "").strip()
    return bool(stored) and stored == compute_plan_hash(item)


def _record_ready(record: MapCoordinateRecord | None) -> bool:
    if record is None:
        return False
    if record.latitude is None or record.longitude is None:
        return False
    if record.status in {COORDINATE_STATUS_MANUAL, COORDINATE_STATUS_CONFIRMED}:
        return True
    if record.status == COORDINATE_STATUS_RESOLVED:
        return record.confidence >= CONFIDENCE_AUTO_RENDER_MIN
    return False


def _place_for(
    coordinates: MapCoordinatesDocument,
    chapter_id: str,
    original_label: str,
    country: str,
) -> MapCoordinateRecord:
    existing = coordinates.places.get(chapter_id)
    if existing is not None:
        return existing
    return MapCoordinateRecord(
        chapter_id=chapter_id,
        original_label=original_label,
        display_label=original_label,
        country_context=country,
        status=COORDINATE_STATUS_MISSING,
    )


def _blocked_reason(*, start: MapCoordinateRecord, end: MapCoordinateRecord, same: bool) -> str:
    missing: list[str] = []
    if not _record_ready(start):
        missing.append(start.original_label or start.chapter_id)
    if not same and not _record_ready(end):
        missing.append(end.original_label or end.chapter_id)
    if not missing:
        return ""
    unique = list(dict.fromkeys(missing))
    return "Koordinaten unsicher oder fehlend: " + ", ".join(unique)


def build_map_plan(
    project: Project,
    *,
    settings: MapRenderSettings | None = None,
    coordinates: MapCoordinatesDocument | None = None,
    previous: MapPlanDocument | None = None,
) -> MapPlanDocument:
    """Baut den Plan im Speicher. Schreibt nichts und ändert die Dramaturgie nicht."""
    confirmed = load_confirmed_dramaturgy(project)
    if confirmed is None:
        raise MapPlanError("Keine bestätigte Dramaturgie — zuerst Schritt ③ abschließen.")
    chapters = _enabled_chapters(confirmed)
    if not chapters:
        raise MapPlanError("Die bestätigte Dramaturgie hat keine aktiven Kapitel.")

    resolved_settings = settings or load_map_settings(project)
    resolution = (
        MAP_RESOLUTION_4K
        if resolved_settings.resolution == MAP_RESOLUTION_4K
        else MAP_RESOLUTION_HD
    )
    resolved_settings = resolved_settings.model_copy(
        update={
            "resolution": resolution,
            "max_parallel": clamp_max_parallel(resolution, resolved_settings.max_parallel),
        }
    )
    width, height = _resolution_size(resolution)
    language = normalize_brief_language(confirmed.language or project.language)
    heading = map_heading(language)
    country = (project.video_place or "").strip()
    coords = coordinates or load_map_coordinates(project)
    previous_by_id = {
        item.chapter_id: item for item in (previous.maps if previous is not None else [])
    }
    chapter_count = len(chapters)
    items: list[MapPlanItem] = []
    for ordinal, entry in enumerate(chapters, start=1):
        chapter_id = entry.folder_name
        original = entry.folder_name
        place = _place_for(coords, chapter_id, original, country)
        display = (place.display_label or original).strip() or original
        if ordinal == 1:
            animation = MAP_ANIMATION_OPENING
            from_id = ""
            start_place = place
            end_place = place
        else:
            animation = MAP_ANIMATION_TRANSITION
            prev = chapters[ordinal - 2]
            from_id = prev.folder_name
            start_place = _place_for(coords, from_id, prev.folder_name, country)
            end_place = place
        same = animation == MAP_ANIMATION_OPENING
        blocked = _blocked_reason(start=start_place, end=end_place, same=same)
        item = MapPlanItem(
            map_sequence_id=str(
                uuid.uuid5(
                    MAP_ID_NAMESPACE,
                    f"{project.id}:{chapter_id}:{ordinal}:{animation}",
                )
            ),
            project_id=project.id,
            chapter_id=chapter_id,
            chapter_ordinal=ordinal,
            chapter_count=chapter_count,
            original_chapter_label=original,
            localized_display_label=display,
            heading=heading,
            language=language,
            country=country,
            from_chapter_id=from_id,
            from_original_chapter_label=(
                "" if animation == MAP_ANIMATION_OPENING else start_place.original_label
            ),
            from_localized_display_label=(
                ""
                if animation == MAP_ANIMATION_OPENING
                else (start_place.display_label or start_place.original_label)
            ),
            start_latitude=start_place.latitude,
            start_longitude=start_place.longitude,
            end_latitude=end_place.latitude,
            end_longitude=end_place.longitude,
            start_coordinate_status=start_place.status,
            end_coordinate_status=end_place.status,
            animation_mode=animation,
            show_vehicle=bool(resolved_settings.show_vehicle),
            duration_in_frames=MAP_DURATION_FRAMES,
            fps=MAP_FPS,
            resolution=resolution,
            width=width,
            height=height,
            style_version=MAP_STYLE_VERSION,
            output_filename=map_output_filename(language, original),
            render_status=RENDER_STATUS_BLOCKED if blocked else RENDER_STATUS_IDLE,
            blocked_reason=blocked,
        )
        item.plan_hash = compute_plan_hash(item)
        prior = previous_by_id.get(chapter_id)
        if (
            prior is not None
            and prior.plan_hash == item.plan_hash
            and prior.render_status == RENDER_STATUS_DONE
            and prior.output_path
        ):
            item.render_status = RENDER_STATUS_DONE
            item.output_path = prior.output_path
            item.media_hash = prior.media_hash
            item.progress = 1.0
            item.blocked_reason = ""
            item.error_detail = ""
        items.append(item)

    return MapPlanDocument(
        project_id=project.id,
        language=language,
        country=country,
        heading=heading,
        chapter_count=chapter_count,
        dramaturgy_fingerprint=dramaturgy_fingerprint(confirmed),
        settings=resolved_settings,
        style_version=MAP_STYLE_VERSION,
        maps=items,
    )


def load_map_settings(project: Project) -> MapRenderSettings:
    loaded = load_model(map_settings_path(project), MapRenderSettings)
    if loaded is None:
        return MapRenderSettings()
    resolution = (
        MAP_RESOLUTION_4K if loaded.resolution == MAP_RESOLUTION_4K else MAP_RESOLUTION_HD
    )
    return loaded.model_copy(
        update={
            "resolution": resolution,
            "max_parallel": clamp_max_parallel(resolution, loaded.max_parallel),
        }
    )


def save_map_settings(project: Project, settings: MapRenderSettings) -> MapRenderSettings:
    resolution = (
        MAP_RESOLUTION_4K if settings.resolution == MAP_RESOLUTION_4K else MAP_RESOLUTION_HD
    )
    normalized = settings.model_copy(
        update={
            "resolution": resolution,
            "max_parallel": clamp_max_parallel(resolution, settings.max_parallel),
        }
    )
    write_json(map_settings_path(project), normalized)
    return normalized


def load_map_coordinates(project: Project) -> MapCoordinatesDocument:
    loaded = load_model(map_coordinates_path(project), MapCoordinatesDocument)
    if loaded is None:
        return MapCoordinatesDocument(
            project_id=project.id,
            country=(project.video_place or "").strip(),
        )
    return loaded


def save_map_coordinates(
    project: Project, document: MapCoordinatesDocument
) -> MapCoordinatesDocument:
    normalized = document.model_copy(
        update={
            "project_id": project.id,
            "updated_at": datetime.now(timezone.utc),
        }
    )
    write_json(map_coordinates_path(project), normalized)
    return normalized


def load_map_plan(project: Project) -> MapPlanDocument | None:
    return load_model(map_plan_path(project), MapPlanDocument)


def save_map_plan(project: Project, document: MapPlanDocument) -> MapPlanDocument:
    normalized = document.model_copy(update={"project_id": project.id})
    write_json(map_plan_path(project), normalized)
    return normalized


def status_after_saving_coordinates(
    latitude: float | None,
    longitude: float | None,
    previous: MapCoordinateRecord | None = None,
) -> tuple[str, float, str]:
    """Gültige gespeicherte Koordinaten gelten als bestätigt, auch ohne Änderung."""
    if latitude is None or longitude is None:
        source = previous.source if previous is not None else ""
        return COORDINATE_STATUS_MISSING, 0.0, source
    same_point = (
        previous is not None
        and previous.has_coordinates
        and previous.latitude == latitude
        and previous.longitude == longitude
    )
    if same_point and previous is not None and previous.source:
        source = previous.source
    else:
        source = "manual"
    return COORDINATE_STATUS_CONFIRMED, 1.0, source


def rebuild_saved_map_plan(
    project: Project,
    *,
    coordinates: MapCoordinatesDocument,
    settings: MapRenderSettings | None = None,
    previous: MapPlanDocument | None = None,
) -> MapPlanDocument:
    resolved_settings = settings if settings is not None else load_map_settings(project)
    prev = previous if previous is not None else load_map_plan(project)
    plan = build_map_plan(
        project,
        settings=resolved_settings,
        coordinates=coordinates,
        previous=prev,
    )
    return save_map_plan(project, plan)


def confirm_map_place_coordinates(
    project: Project,
    *,
    chapter_id: str,
    original_label: str,
    display_label: str,
    latitude: float | None,
    longitude: float | None,
    source: str = "confirmed",
    note: str = "",
    settings: MapRenderSettings | None = None,
    previous: MapPlanDocument | None = None,
) -> tuple[MapCoordinatesDocument, MapPlanDocument]:
    """Bestätigt gültige Koordinaten, speichert sie und gibt den Kartenplan frei."""
    if latitude is None or longitude is None:
        label = (original_label or chapter_id).strip() or chapter_id
        raise MapPlanError(f"Ort „{label}“ hat keine gültigen Koordinaten.")
    coords = update_coordinate_record(
        project,
        chapter_id=chapter_id,
        original_label=original_label,
        display_label=display_label,
        latitude=latitude,
        longitude=longitude,
        status=COORDINATE_STATUS_CONFIRMED,
        source=source or "confirmed",
        note=note,
        confidence=1.0,
    )
    plan = rebuild_saved_map_plan(
        project,
        coordinates=coords,
        settings=settings,
        previous=previous,
    )
    return coords, plan


def confirm_all_valid_map_coordinates(
    project: Project,
    *,
    settings: MapRenderSettings | None = None,
    previous: MapPlanDocument | None = None,
) -> tuple[MapCoordinatesDocument, MapPlanDocument]:
    """Bestätigt alle Orte mit gültigen Koordinaten und gibt den Plan frei."""
    coords = load_map_coordinates(project)
    changed = False
    next_places = dict(coords.places)
    for chapter_id, record in coords.places.items():
        if not record.has_coordinates:
            continue
        if record.status in {COORDINATE_STATUS_CONFIRMED, COORDINATE_STATUS_MANUAL}:
            continue
        next_places[chapter_id] = record.model_copy(
            update={
                "status": COORDINATE_STATUS_CONFIRMED,
                "confidence": 1.0,
                "source": record.source or "confirmed",
            }
        )
        changed = True
    if changed:
        coords = save_map_coordinates(
            project, coords.model_copy(update={"places": next_places})
        )
    plan = rebuild_saved_map_plan(
        project,
        coordinates=coords,
        settings=settings,
        previous=previous,
    )
    return coords, plan


def update_coordinate_record(
    project: Project,
    *,
    chapter_id: str,
    original_label: str,
    display_label: str,
    latitude: float | None,
    longitude: float | None,
    status: str = COORDINATE_STATUS_MANUAL,
    source: str = "manual",
    note: str = "",
    confidence: float = 1.0,
) -> MapCoordinatesDocument:
    document = load_map_coordinates(project)
    country = (project.video_place or "").strip()
    document.country = country
    document.places[chapter_id] = MapCoordinateRecord(
        chapter_id=chapter_id,
        original_label=original_label,
        display_label=(display_label or original_label).strip() or original_label,
        latitude=latitude,
        longitude=longitude,
        confidence=confidence,
        status=status,
        source=source,
        country_context=country,
        note=note,
    )
    return save_map_coordinates(project, document)


def apply_geocode_hits(
    project: Project,
    hits: dict[str, GeocodeHit],
) -> MapCoordinatesDocument:
    """Übernimmt Geocoder-Treffer. Unsichere Orte bleiben needs_review."""
    document = load_map_coordinates(project)
    country = (project.video_place or "").strip()
    document.country = country
    for chapter_id, hit in hits.items():
        existing = document.places.get(chapter_id)
        original = str(hit.get("original_label") or (existing.original_label if existing else chapter_id))
        display = str(hit.get("display_label") or (existing.display_label if existing else original))
        lat = hit.get("latitude")
        lon = hit.get("longitude")
        try:
            confidence = float(hit.get("confidence") or 0.0)
        except (TypeError, ValueError):
            confidence = 0.0
        ambiguous = bool(hit.get("ambiguous"))
        has_point = lat is not None and lon is not None
        if not has_point:
            status = COORDINATE_STATUS_MISSING
        elif ambiguous or confidence < CONFIDENCE_AUTO_RENDER_MIN:
            status = COORDINATE_STATUS_NEEDS_REVIEW
        else:
            status = COORDINATE_STATUS_RESOLVED
        document.places[chapter_id] = MapCoordinateRecord(
            chapter_id=chapter_id,
            original_label=original,
            display_label=display or original,
            latitude=float(lat) if lat is not None else None,
            longitude=float(lon) if lon is not None else None,
            confidence=confidence,
            status=status,
            source=str(hit.get("source") or "geocode"),
            country_context=country,
            note=str(hit.get("note") or ""),
        )
    return save_map_coordinates(project, document)


def output_path_for(project: Project, filename: str) -> str:
    return str(map_output_dir(project) / filename)


def unique_chapter_places(plan: MapPlanDocument) -> list[tuple[str, str, str]]:
    """Unique ``(chapter_id, original_label, display_label)`` in plan order."""
    seen: set[str] = set()
    rows: list[tuple[str, str, str]] = []
    for item in plan.maps:
        candidates = (
            (item.chapter_id, item.original_chapter_label, item.localized_display_label),
            (
                item.from_chapter_id,
                item.from_original_chapter_label,
                item.from_localized_display_label,
            ),
        )
        for chapter_id, original, display in candidates:
            if not chapter_id or chapter_id in seen:
                continue
            seen.add(chapter_id)
            rows.append((chapter_id, original or chapter_id, display or original or chapter_id))
    return rows
