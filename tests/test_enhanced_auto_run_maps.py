"""Auto-Lauf erzeugt, bestätigt und rendert Karten vor dem Python Timing."""

from __future__ import annotations

from pathlib import Path

from otio_app.defaults import DEFAULT_ENHANCED_WORK_SUBDIR
from otio_app.models import Project, ProjectMode
from otio_app.services.voiceover_generation.dramaturgy_service import (
    save_confirmed_dramaturgy,
)
from otio_app.services.voiceover_generation.models import (
    DramaturgyFolderEntry,
    DramaturgyPlan,
)
from otio_app.services.without_voiceover_enhanced.maps.auto_run_maps import (
    maps_complete,
    run_maps_for_auto_run,
)
from otio_app.services.without_voiceover_enhanced.maps.models import (
    COORDINATE_STATUS_CONFIRMED,
    COORDINATE_STATUS_NEEDS_REVIEW,
    RENDER_STATUS_IDLE,
)
from otio_app.services.without_voiceover_enhanced.maps.plan_service import (
    apply_geocode_hits,
    build_map_plan,
    load_map_coordinates,
    load_map_plan,
    save_map_plan,
)
from otio_app.services.without_voiceover_enhanced.paths import map_output_dir
from otio_app.services.without_voiceover_enhanced.keyword_flow_maps import (
    _list_map_media_for_chapter,
)


def _project(tmp_path: Path, folders: list[str]) -> Project:
    root = tmp_path / "proj"
    work = root / DEFAULT_ENHANCED_WORK_SUBDIR
    work.mkdir(parents=True)
    for folder in folders:
        (root / folder).mkdir(parents=True, exist_ok=True)
    return Project(
        name="Auto Maps",
        project_root=str(root),
        work_dir=str(work),
        project_mode=ProjectMode.WITHOUT_VOICEOVER_ENHANCED,
        language="fr",
        video_place="Greece",
        asset_subdir_names=folders,
        selected_asset_subdirs=folders,
        fps=25.0,
    )


def _confirm(project: Project, folders: list[str]) -> None:
    plan = DramaturgyPlan(
        project_id=project.id,
        language="FR",
        project_title="Maps",
        core_promise="Travel",
        narrative_arc="Route",
        recommended_folder_order=[
            DramaturgyFolderEntry(
                folder_name=folder,
                order_index=index,
                enabled=True,
                dramaturgy_role="hook" if index == 0 else "development",
                reason=f"Kapitel {folder}",
            )
            for index, folder in enumerate(folders)
        ],
    )
    save_confirmed_dramaturgy(project, plan)


class _FakeRenderer:
    def readiness(self) -> dict:
        return {
            "ready": True,
            "checks": {
                "renderer_entry_point": True,
                "renderer_dependencies": True,
                "node_binary": True,
                "ffprobe_binary": True,
                "nice_binary": True,
            },
        }

    def render_item(self, project: Project, item, **_kwargs) -> dict:
        output = map_output_dir(project)
        output.mkdir(parents=True, exist_ok=True)
        path = output / item.output_filename
        path.write_bytes(b"fake-map-mp4")
        return {
            "export_path": str(path),
            "content_hash": "hash",
            "reused": False,
        }


def test_auto_run_maps_confirms_geocode_and_renders(tmp_path: Path) -> None:
    folders = ["Karpathos", "Symi"]
    project = _project(tmp_path, folders)
    _confirm(project, folders)

    def fake_geocode(place: str, country: str) -> dict:
        assert country == "Greece"
        coords = {
            "Karpathos": (35.507, 27.213),
            "Symi": (36.614, 27.838),
        }
        lat, lon = coords[place]
        return {
            "latitude": lat,
            "longitude": lon,
            "confidence": 0.55,
            "original_label": place,
            "display_label": place,
        }

    messages: list[str] = []
    result = run_maps_for_auto_run(
        project,
        on_message=messages.append,
        geocode_fn=fake_geocode,
        renderer=_FakeRenderer(),
    )
    coords = load_map_coordinates(project)
    assert coords.places["Karpathos"].status == COORDINATE_STATUS_CONFIRMED
    assert coords.places["Symi"].status == COORDINATE_STATUS_CONFIRMED
    assert result["failed"] == []
    assert set(result["rendered"]) == {"Karpathos", "Symi"}
    assert result["blocked"] == []
    plan = result["plan"]
    assert all(item.render_status == "done" for item in plan.maps)
    assert all(Path(item.output_path).is_file() for item in plan.maps)
    assert maps_complete(project) is True
    assert any("Koordinaten prüfen" in line for line in messages)
    assert any("Koordinaten bestätigen" in line for line in messages)


def test_maps_complete_false_when_saved_plan_hash_is_stale(tmp_path: Path) -> None:
    folders = ["Karpathos"]
    project = _project(tmp_path, folders)
    _confirm(project, folders)
    result = run_maps_for_auto_run(
        project,
        geocode_fn=lambda place, country: {
            "latitude": 35.507,
            "longitude": 27.213,
            "confidence": 0.9,
            "original_label": place,
            "display_label": place,
        },
        renderer=_FakeRenderer(),
    )
    assert maps_complete(project) is True
    plan = result["plan"]
    plan.maps[0].plan_hash = "stale-from-previous-code"
    save_map_plan(project, plan)
    assert load_map_plan(project).maps[0].plan_hash == "stale-from-previous-code"
    assert maps_complete(project) is False


def test_needs_review_geocode_stays_blocked_until_confirm(tmp_path: Path) -> None:
    folders = ["Karpathos"]
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
            }
        },
    )
    plan = build_map_plan(project)
    save_map_plan(project, plan)
    assert plan.maps[0].end_coordinate_status == COORDINATE_STATUS_NEEDS_REVIEW
    assert plan.maps[0].render_status != RENDER_STATUS_IDLE


def test_enhanced_output_dir_is_preferred_over_project_maps_folder(
    tmp_path: Path,
) -> None:
    folders = ["Karpathos"]
    project = _project(tmp_path, folders)
    _confirm(project, folders)
    legacy = Path(project.project_root) / "Maps"
    legacy.mkdir()
    (legacy / "FR_Karpathos_Map.mp4").write_bytes(b"legacy")
    project = project.model_copy(
        update={
            "asset_subdir_names": ["Karpathos", "Maps"],
            "selected_asset_subdirs": ["Karpathos", "Maps"],
        }
    )
    output = map_output_dir(project)
    output.mkdir(parents=True)
    enhanced = output / "fr_Karpathos_Map.mp4"
    enhanced.write_bytes(b"enhanced")

    matches = _list_map_media_for_chapter(project, "Karpathos")
    assert len(matches) == 1
    assert matches[0]["path"] == str(enhanced)
    assert matches[0]["asset_id"].startswith("map::enhanced::")
