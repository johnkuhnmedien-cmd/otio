"""Phase-1 tests for Enhanced map-card planning (no renderer)."""

from __future__ import annotations

from pathlib import Path

import pytest

from otio_app.defaults import DEFAULT_ENHANCED_WORK_SUBDIR
from otio_app.models import Project, ProjectMode
from otio_app.project_layout import get_dramaturgy_plan_confirmed_path
from otio_app.services.voiceover_generation.dramaturgy_service import (
    save_confirmed_dramaturgy,
)
from otio_app.services.voiceover_generation.models import (
    DramaturgyFolderEntry,
    DramaturgyPlan,
)
from otio_app.services.without_voiceover_enhanced.enhanced_auto_run_service import (
    AUTO_RUN_STEPS,
)
from otio_app.services.without_voiceover_enhanced.maps.geocode_service import (
    lookup_missing_coordinates,
    nominatim_geocode,
)
from otio_app.services.without_voiceover_enhanced.maps.models import (
    COORDINATE_STATUS_CONFIRMED,
    COORDINATE_STATUS_MANUAL,
    COORDINATE_STATUS_NEEDS_REVIEW,
    COORDINATE_STATUS_RESOLVED,
    MAP_ANIMATION_OPENING,
    MAP_ANIMATION_TRANSITION,
    MAP_DURATION_FRAMES,
    MAP_FPS,
    MAP_RESOLUTION_4K,
    MAP_RESOLUTION_HD,
    RENDER_STATUS_BLOCKED,
    RENDER_STATUS_DONE,
    RENDER_STATUS_IDLE,
    MapCoordinateRecord,
    MapCoordinatesDocument,
    MapRenderSettings,
)
from otio_app.services.without_voiceover_enhanced.maps.plan_service import (
    MapPlanError,
    apply_geocode_hits,
    build_map_plan,
    clamp_max_parallel,
    compute_plan_hash,
    confirm_map_place_coordinates,
    load_map_coordinates,
    map_heading,
    map_output_filename,
    rebuild_saved_map_plan,
    save_map_coordinates,
    save_map_plan,
    status_after_saving_coordinates,
    update_coordinate_record,
)
from otio_app.ui.navigation import (
    PAGE_MAPS,
    VOICEOVER_GEN_ENHANCED_WORKFLOW_PAGES,
    VOICEOVER_GEN_WORKFLOW_PAGES,
)


def _project(
    tmp_path: Path,
    folders: list[str],
    *,
    language: str = "fr",
    video_place: str = "Greece",
) -> Project:
    root = tmp_path / "proj"
    work = root / DEFAULT_ENHANCED_WORK_SUBDIR
    work.mkdir(parents=True)
    for folder in folders:
        (root / folder).mkdir(parents=True, exist_ok=True)
    return Project(
        name="Map Plan Test",
        project_root=str(root),
        work_dir=str(work),
        project_mode=ProjectMode.WITHOUT_VOICEOVER_ENHANCED,
        language=language,
        video_place=video_place,
        asset_subdir_names=folders,
        selected_asset_subdirs=folders,
        fps=25.0,
    )


def _confirm(
    project: Project,
    folders: list[str],
    *,
    language: str = "FR",
    enabled: dict[str, bool] | None = None,
) -> DramaturgyPlan:
    flags = enabled or {}
    plan = DramaturgyPlan(
        project_id=project.id,
        language=language,
        project_title="Map Test",
        core_promise="Travel",
        narrative_arc="Route",
        recommended_folder_order=[
            DramaturgyFolderEntry(
                folder_name=folder,
                order_index=index,
                enabled=flags.get(folder, True),
                dramaturgy_role="hook" if index == 0 else "development",
                reason=f"Kapitel {folder}",
            )
            for index, folder in enumerate(folders)
        ],
    )
    return save_confirmed_dramaturgy(project, plan)


def _coords(
    project: Project,
    places: dict[str, tuple[float, float, str, float]],
) -> MapCoordinatesDocument:
    records = {}
    for chapter_id, (lat, lon, status, confidence) in places.items():
        records[chapter_id] = MapCoordinateRecord(
            chapter_id=chapter_id,
            original_label=chapter_id,
            display_label=chapter_id,
            latitude=lat,
            longitude=lon,
            confidence=confidence,
            status=status,
            source="test",
            country_context=project.video_place,
        )
    return MapCoordinatesDocument(
        project_id=project.id,
        country=project.video_place,
        places=records,
    )


def test_opening_map_for_first_enabled_chapter(tmp_path: Path) -> None:
    folders = ["Mount Athos", "Meteora"]
    project = _project(tmp_path, folders)
    _confirm(project, folders)
    plan = build_map_plan(
        project,
        coordinates=_coords(
            project,
            {
                "Mount Athos": (40.27, 24.21, COORDINATE_STATUS_MANUAL, 1.0),
                "Meteora": (39.72, 21.63, COORDINATE_STATUS_MANUAL, 1.0),
            },
        ),
    )
    opening = plan.maps[0]
    assert opening.chapter_ordinal == 1
    assert opening.chapter_id == "Mount Athos"
    assert opening.animation_mode == MAP_ANIMATION_OPENING
    assert opening.from_chapter_id == ""
    assert opening.start_latitude == opening.end_latitude == 40.27
    assert opening.start_longitude == opening.end_longitude == 24.21
    assert opening.render_status == RENDER_STATUS_IDLE
    assert opening.duration_in_frames == MAP_DURATION_FRAMES
    assert opening.fps == MAP_FPS
    assert opening.heading == "Itinéraire"


def test_transition_map_uses_previous_then_current_chapter(tmp_path: Path) -> None:
    folders = ["Mount Athos", "Meteora"]
    project = _project(tmp_path, folders)
    _confirm(project, folders)
    plan = build_map_plan(
        project,
        coordinates=_coords(
            project,
            {
                "Mount Athos": (40.27, 24.21, COORDINATE_STATUS_MANUAL, 1.0),
                "Meteora": (39.72, 21.63, COORDINATE_STATUS_MANUAL, 1.0),
            },
        ),
    )
    transition = plan.maps[1]
    assert transition.chapter_ordinal == 2
    assert transition.chapter_id == "Meteora"
    assert transition.animation_mode == MAP_ANIMATION_TRANSITION
    assert transition.from_chapter_id == "Mount Athos"
    assert transition.from_original_chapter_label == "Mount Athos"
    assert transition.start_latitude == 40.27
    assert transition.end_latitude == 39.72
    assert transition.render_status == RENDER_STATUS_IDLE


def test_plan_follows_dramaturgy_json_order_and_skips_disabled(tmp_path: Path) -> None:
    folders = ["Mount Athos", "Skipped Isle", "Meteora"]
    project = _project(tmp_path, folders)
    _confirm(project, folders, enabled={"Skipped Isle": False})
    plan = build_map_plan(project)
    assert [item.chapter_id for item in plan.maps] == ["Mount Athos", "Meteora"]
    assert plan.chapter_count == 2
    assert plan.maps[1].from_chapter_id == "Mount Athos"
    assert "Skipped Isle" not in {item.chapter_id for item in plan.maps}


def test_visible_heading_is_localized_filename_keeps_original_json_name(
    tmp_path: Path,
) -> None:
    folders = ["Mount Athos", "Meteora"]
    project = _project(tmp_path, folders, language="it")
    _confirm(project, folders, language="IT")
    update_coordinate_record(
        project,
        chapter_id="Mount Athos",
        original_label="Mount Athos",
        display_label="Monte Athos",
        latitude=40.27,
        longitude=24.21,
    )
    plan = build_map_plan(project)
    opening = plan.maps[0]
    assert opening.heading == "Itinerario"
    assert opening.localized_display_label == "Mount Athos"
    assert opening.original_chapter_label == "Mount Athos"
    assert opening.output_filename == "it_Mount Athos_Map.mp4"
    assert opening.language == "IT"
    assert map_output_filename("FR", "Mount Athos") == "fr_Mount Athos_Map.mp4"
    assert map_output_filename("EN", "Achill Island") == "en_Achill Island_Map.mp4"
    assert map_heading("DE") == "Reiseroute"
    assert map_heading("ES") == "Ruta de viaje"
    assert map_heading("PT") == "Rota de viagem"
    assert map_heading("EN") == "Travel Route"
    assert map_heading("JP") == "旅行ルート"
    assert map_heading("KR") == "여행 경로"
    assert map_heading("ja") == "旅行ルート"


def test_italian_map_localizes_german_country_and_chapter_labels(tmp_path: Path) -> None:
    folders = ["Peričnik-Wasserfall", "Jezersko"]
    project = _project(
        tmp_path, folders, language="it", video_place="Slowenien"
    )
    _confirm(project, folders, language="IT")
    plan = build_map_plan(
        project,
        coordinates=_coords(
            project,
            {
                "Peričnik-Wasserfall": (46.44, 13.89, COORDINATE_STATUS_MANUAL, 1.0),
                "Jezersko": (46.40, 14.50, COORDINATE_STATUS_MANUAL, 1.0),
            },
        ),
    )
    opening = plan.maps[0]
    transition = plan.maps[1]
    assert opening.heading == "Itinerario"
    assert opening.country == "Slowenien"
    assert opening.localized_display_label == "Cascata di Peričnik"
    assert opening.original_chapter_label == "Peričnik-Wasserfall"
    assert opening.output_filename == "it_Peričnik-Wasserfall_Map.mp4"
    assert transition.from_localized_display_label == "Cascata di Peričnik"
    assert transition.localized_display_label == "Jezersko"
    assert transition.from_original_chapter_label == "Peričnik-Wasserfall"


def test_italian_plan_ignores_english_osm_display_names(tmp_path: Path) -> None:
    folders = [
        "Škocjan-Höhlen",
        "Höhle von Postojna",
        "Bohinjer See",
        "Logar-Tal",
        "Vršič-Pass",
        "Triglav-Nationalpark",
        "Tolminer Klammen",
    ]
    project = _project(tmp_path, folders, language="it", video_place="Slowenien")
    _confirm(project, folders, language="IT")
    coords = _coords(
        project,
        {
            folder: (46.0 + index * 0.01, 14.0 + index * 0.01, COORDINATE_STATUS_MANUAL, 1.0)
            for index, folder in enumerate(folders)
        },
    )
    coords.places["Bohinjer See"].display_label = "Lake Bohinj"
    coords.places["Logar-Tal"].display_label = "Logarska Dolina"
    coords.places["Tolminer Klammen"].display_label = "Tolmin Gorge"
    coords.places["Höhle von Postojna"].display_label = "Postojna Cave"
    plan = build_map_plan(project, coordinates=coords)
    by_id = {item.chapter_id: item.localized_display_label for item in plan.maps}
    assert by_id["Škocjan-Höhlen"] == "Grotte di Škocjan"
    assert by_id["Höhle von Postojna"] == "Grotta di Postojna"
    assert by_id["Bohinjer See"] == "Lago di Bohinj"
    assert by_id["Logar-Tal"] == "Valle di Logar"
    assert by_id["Vršič-Pass"] == "Passo Vršič"
    assert by_id["Triglav-Nationalpark"] == "Parco nazionale del Triglav"
    assert by_id["Tolminer Klammen"] == "Gole di Tolmin"


def test_hd_and_4k_parallel_caps() -> None:
    assert clamp_max_parallel(MAP_RESOLUTION_HD, 8) == 4
    assert clamp_max_parallel(MAP_RESOLUTION_HD, 1) == 1
    assert clamp_max_parallel(MAP_RESOLUTION_4K, 8) == 2
    assert clamp_max_parallel(MAP_RESOLUTION_4K, None) == 2


def test_build_plan_clamps_parallelism(tmp_path: Path) -> None:
    folders = ["Mount Athos"]
    project = _project(tmp_path, folders)
    _confirm(project, folders)
    hd_plan = build_map_plan(
        project,
        settings=MapRenderSettings(resolution=MAP_RESOLUTION_HD, max_parallel=9),
    )
    assert hd_plan.settings.max_parallel == 4
    assert hd_plan.maps[0].width == 1920
    assert hd_plan.maps[0].height == 1080
    k_plan = build_map_plan(
        project,
        settings=MapRenderSettings(
            resolution=MAP_RESOLUTION_4K, max_parallel=9, show_vehicle=True
        ),
    )
    assert k_plan.settings.max_parallel == 2
    assert k_plan.maps[0].width == 3840
    assert k_plan.maps[0].height == 2160
    assert k_plan.maps[0].show_vehicle is True


def test_missing_coordinates_block_only_affected_maps(tmp_path: Path) -> None:
    folders = ["Mount Athos", "Meteora"]
    project = _project(tmp_path, folders)
    _confirm(project, folders)
    plan = build_map_plan(
        project,
        coordinates=_coords(
            project,
            {"Mount Athos": (40.27, 24.21, COORDINATE_STATUS_MANUAL, 1.0)},
        ),
    )
    assert plan.maps[0].render_status == RENDER_STATUS_IDLE
    assert plan.maps[1].render_status == RENDER_STATUS_BLOCKED
    assert "Meteora" in plan.maps[1].blocked_reason
    assert plan.maps[0].blocked_reason == ""


def test_low_confidence_geocode_blocks_without_auto_render(tmp_path: Path) -> None:
    folders = ["Mystery Place"]
    project = _project(tmp_path, folders)
    _confirm(project, folders, language="EN")
    apply_geocode_hits(
        project,
        {
            "Mystery Place": {
                "latitude": 1.0,
                "longitude": 2.0,
                "confidence": 0.2,
                "original_label": "Mystery Place",
                "display_label": "Mystery Place",
            }
        },
    )
    plan = build_map_plan(project)
    assert plan.maps[0].end_coordinate_status == COORDINATE_STATUS_NEEDS_REVIEW
    assert plan.maps[0].render_status == RENDER_STATUS_BLOCKED


def test_saving_unchanged_valid_coordinates_sets_confirmed() -> None:
    previous = MapCoordinateRecord(
        chapter_id="Karpathos",
        original_label="Karpathos",
        display_label="Karpathos",
        latitude=35.507,
        longitude=27.213,
        confidence=0.55,
        status=COORDINATE_STATUS_NEEDS_REVIEW,
        source="geocode",
    )
    status, confidence, source = status_after_saving_coordinates(
        35.507, 27.213, previous
    )
    assert status == COORDINATE_STATUS_CONFIRMED
    assert confidence == 1.0
    assert source == "geocode"


def test_confirming_review_coordinates_unblocks_map_render(tmp_path: Path) -> None:
    folders = ["Karpathos", "Symi"]
    project = _project(tmp_path, folders)
    _confirm(project, folders)
    apply_geocode_hits(
        project,
        {
            "Karpathos": {
                "latitude": 35.507,
                "longitude": 27.213,
                "confidence": 0.55,
                "original_label": "Karpathos",
                "display_label": "Karpathos",
            },
            "Symi": {
                "latitude": 36.614,
                "longitude": 27.838,
                "confidence": 0.55,
                "original_label": "Symi",
                "display_label": "Symi",
            },
        },
    )
    blocked = build_map_plan(project)
    assert blocked.maps[0].end_coordinate_status == COORDINATE_STATUS_NEEDS_REVIEW
    assert blocked.maps[1].end_coordinate_status == COORDINATE_STATUS_NEEDS_REVIEW
    assert all(item.render_status == RENDER_STATUS_BLOCKED for item in blocked.maps)

    coords = load_map_coordinates(project)
    karpathos = coords.places["Karpathos"]
    coords, plan = confirm_map_place_coordinates(
        project,
        chapter_id="Karpathos",
        original_label="Karpathos",
        display_label="Karpathos",
        latitude=karpathos.latitude,
        longitude=karpathos.longitude,
        previous=blocked,
    )
    assert coords.places["Karpathos"].status == COORDINATE_STATUS_CONFIRMED
    assert plan.maps[0].render_status == RENDER_STATUS_IDLE
    assert plan.maps[0].blocked_reason == ""
    assert plan.maps[1].render_status == RENDER_STATUS_BLOCKED

    coords, plan = confirm_map_place_coordinates(
        project,
        chapter_id="Symi",
        original_label="Symi",
        display_label="Symi",
        latitude=coords.places["Symi"].latitude,
        longitude=coords.places["Symi"].longitude,
        previous=plan,
    )
    assert coords.places["Symi"].status == COORDINATE_STATUS_CONFIRMED
    assert all(item.render_status == RENDER_STATUS_IDLE for item in plan.maps)
    assert all(item.blocked_reason == "" for item in plan.maps)

    next_places = {}
    for chapter_id, record in coords.places.items():
        status, confidence, source = status_after_saving_coordinates(
            record.latitude, record.longitude, record
        )
        next_places[chapter_id] = record.model_copy(
            update={"status": status, "confidence": confidence, "source": source}
        )
    saved = save_map_coordinates(
        project, coords.model_copy(update={"places": next_places})
    )
    rebuilt = rebuild_saved_map_plan(project, coordinates=saved, previous=plan)
    assert all(
        item.end_coordinate_status == COORDINATE_STATUS_CONFIRMED
        for item in rebuilt.maps
    )
    assert all(item.render_status == RENDER_STATUS_IDLE for item in rebuilt.maps)


def test_identical_plan_hash_reuses_completed_output(tmp_path: Path) -> None:
    folders = ["Mount Athos"]
    project = _project(tmp_path, folders)
    _confirm(project, folders)
    coordinates = _coords(
        project,
        {"Mount Athos": (40.27, 24.21, COORDINATE_STATUS_MANUAL, 1.0)},
    )
    first = build_map_plan(project, coordinates=coordinates)
    first.maps[0].render_status = RENDER_STATUS_DONE
    first.maps[0].output_path = "/tmp/fr_Mount Athos_Map.mp4"
    reused = build_map_plan(project, coordinates=coordinates, previous=first)
    assert reused.maps[0].plan_hash == first.maps[0].plan_hash
    assert reused.maps[0].plan_hash == compute_plan_hash(reused.maps[0])
    assert reused.maps[0].content_hash == reused.maps[0].plan_hash
    assert reused.maps[0].render_status == RENDER_STATUS_DONE
    assert reused.maps[0].output_path == "/tmp/fr_Mount Athos_Map.mp4"


def test_build_and_save_plan_does_not_change_confirmed_dramaturgy(tmp_path: Path) -> None:
    folders = ["Mount Athos", "Meteora"]
    project = _project(tmp_path, folders)
    _confirm(project, folders)
    path = get_dramaturgy_plan_confirmed_path(project.language_work_dir_path)
    before = path.read_bytes()
    plan = build_map_plan(project)
    save_map_plan(project, plan)
    update_coordinate_record(
        project,
        chapter_id="Mount Athos",
        original_label="Mount Athos",
        display_label="Mont Athos",
        latitude=40.27,
        longitude=24.21,
    )
    assert path.read_bytes() == before
    assert path.is_file()


def test_lookup_missing_coordinates_uses_injected_geocoder(tmp_path: Path) -> None:
    folders = ["Mount Athos", "Meteora"]
    project = _project(tmp_path, folders)
    _confirm(project, folders)
    update_coordinate_record(
        project,
        chapter_id="Mount Athos",
        original_label="Mount Athos",
        display_label="Mont Athos",
        latitude=40.27,
        longitude=24.21,
    )
    calls: list[str] = []

    def fake_geocode(place: str, country: str) -> dict:
        calls.append(place)
        assert country == "Greece"
        return {
            "latitude": 39.72,
            "longitude": 21.63,
            "confidence": 0.9,
            "original_label": place,
            "display_label": place,
        }

    plan = build_map_plan(project)
    coords, rebuilt, errors = lookup_missing_coordinates(
        project,
        plan=plan,
        geocode_fn=fake_geocode,
    )
    assert errors == []
    assert calls == ["Meteora"]
    assert coords.places["Meteora"].status == COORDINATE_STATUS_RESOLVED
    assert rebuilt.maps[0].render_status == RENDER_STATUS_IDLE
    assert rebuilt.maps[1].render_status == RENDER_STATUS_IDLE
    assert rebuilt.maps[0].localized_display_label == "Mount Athos"


def test_nominatim_geocode_parses_payload(monkeypatch: pytest.MonkeyPatch) -> None:
    class _Resp:
        status_code = 200

        def raise_for_status(self) -> None:
            return None

        def json(self) -> list[dict]:
            return [{"lat": "40.15", "lon": "24.32", "importance": 0.88}]

    monkeypatch.setattr(
        "otio_app.services.without_voiceover_enhanced.maps.geocode_service.requests.get",
        lambda *_args, **_kwargs: _Resp(),
    )
    hit = nominatim_geocode("Mount Athos", "Greece")
    assert hit["latitude"] == 40.15
    assert hit["longitude"] == 24.32
    assert hit["confidence"] == 0.88
    assert hit["ambiguous"] is False


def test_missing_confirmed_dramaturgy_raises(tmp_path: Path) -> None:
    project = _project(tmp_path, ["Mount Athos"])
    with pytest.raises(MapPlanError):
        build_map_plan(project)


def test_maps_page_is_enhanced_only_and_maps_run_before_timing() -> None:
    assert PAGE_MAPS in VOICEOVER_GEN_ENHANCED_WORKFLOW_PAGES
    assert PAGE_MAPS not in VOICEOVER_GEN_WORKFLOW_PAGES
    ids = [step_id for step_id, _label in AUTO_RUN_STEPS]
    assert "maps" in ids
    assert ids.index("funnel") < ids.index("maps") < ids.index("timing")
    dram_index = VOICEOVER_GEN_ENHANCED_WORKFLOW_PAGES.index("③ Dramaturgie")
    assert VOICEOVER_GEN_ENHANCED_WORKFLOW_PAGES[dram_index + 1] == PAGE_MAPS
    assert VOICEOVER_GEN_ENHANCED_WORKFLOW_PAGES[dram_index + 2] == "④ Folder Voice-overs"


def test_maps_tab_render_buttons_are_clickable_without_rerun() -> None:
    import inspect

    from otio_app.ui.without_voiceover_enhanced.maps_tab import (
        render_enhanced_maps_page,
    )

    source = inspect.getsource(render_enhanced_maps_page)
    assert "Alle Karten rendern" in source
    assert "Nur fehlende/fehlerhafte Karten rendern" in source
    assert "Render abbrechen" in source
    assert "Der Renderer folgt in Phase 2" not in source
    assert "disabled=True" not in source
    assert "poll_while_running" in source
    assert "Koordinaten bestätigen" in source
    assert "confirm_map_place_coordinates" in source
    assert "status_after_saving_coordinates" in source
    render_section = source.split('st.subheader("Rendern")', 1)[1]
    assert "st.rerun()" not in render_section
