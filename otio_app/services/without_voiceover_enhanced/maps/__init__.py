"""Kartenplanung für OTIO Enhanced — isoliert, ohne Auto-Lauf.

Phase 1 erzeugt nur den deterministischen Plan und die Koordinaten-UI.
Rendern (Remotion/Thomas) kommt in Phase 2 und startet nur per Klick.

Kapitel-ID ist der Original-``folder_name`` aus der bestätigten Dramaturgie.
Karten gehören nicht zum Auto-Lauf, damit nichts ohne ausdrücklichen Klick
gerendert oder überschrieben wird.
"""

from otio_app.services.without_voiceover_enhanced.maps.models import (
    COORDINATE_STATUS_MANUAL,
    COORDINATE_STATUS_MISSING,
    COORDINATE_STATUS_NEEDS_REVIEW,
    COORDINATE_STATUS_RESOLVED,
    MAP_ANIMATION_OPENING,
    MAP_ANIMATION_TRANSITION,
    MAP_DURATION_FRAMES,
    MAP_FPS,
    MAP_HEADING_BY_LANGUAGE,
    MAP_MAX_PARALLEL_4K,
    MAP_MAX_PARALLEL_HD,
    MAP_RESOLUTION_4K,
    MAP_RESOLUTION_HD,
    MapCoordinateRecord,
    MapCoordinatesDocument,
    MapPlanDocument,
    MapPlanItem,
    MapRenderSettings,
)
from otio_app.services.without_voiceover_enhanced.maps.geocode_service import (
    GeocodeError,
    lookup_missing_coordinates,
    nominatim_geocode,
)
from otio_app.services.without_voiceover_enhanced.maps.plan_service import (
    MapPlanError,
    apply_geocode_hits,
    build_map_plan,
    clamp_max_parallel,
    compute_plan_hash,
    dramaturgy_fingerprint,
    load_map_coordinates,
    load_map_plan,
    load_map_settings,
    map_heading,
    map_language_prefix,
    map_output_filename,
    save_map_coordinates,
    save_map_plan,
    save_map_settings,
    unique_chapter_places,
    update_coordinate_record,
)

__all__ = [
    "COORDINATE_STATUS_MANUAL",
    "COORDINATE_STATUS_MISSING",
    "COORDINATE_STATUS_NEEDS_REVIEW",
    "COORDINATE_STATUS_RESOLVED",
    "MAP_ANIMATION_OPENING",
    "MAP_ANIMATION_TRANSITION",
    "MAP_DURATION_FRAMES",
    "MAP_FPS",
    "MAP_HEADING_BY_LANGUAGE",
    "MAP_MAX_PARALLEL_4K",
    "MAP_MAX_PARALLEL_HD",
    "MAP_RESOLUTION_4K",
    "MAP_RESOLUTION_HD",
    "MapCoordinateRecord",
    "MapCoordinatesDocument",
    "MapPlanDocument",
    "GeocodeError",
    "MapPlanError",
    "MapPlanItem",
    "MapRenderSettings",
    "apply_geocode_hits",
    "build_map_plan",
    "clamp_max_parallel",
    "compute_plan_hash",
    "dramaturgy_fingerprint",
    "load_map_coordinates",
    "load_map_plan",
    "load_map_settings",
    "lookup_missing_coordinates",
    "map_heading",
    "map_language_prefix",
    "map_output_filename",
    "nominatim_geocode",
    "save_map_coordinates",
    "save_map_plan",
    "save_map_settings",
    "unique_chapter_places",
    "update_coordinate_record",
]
