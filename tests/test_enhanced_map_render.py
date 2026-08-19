"""Phase-2 tests for Enhanced map rendering (Remotion, jobs, cache, ffprobe)."""

from __future__ import annotations

import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from otio_app.defaults import DEFAULT_ENHANCED_WORK_SUBDIR
from otio_app.models import Project, ProjectMode
from otio_app.services.without_voiceover_enhanced.enhanced_auto_run_service import (
    AUTO_RUN_STEPS,
)
from otio_app.services.without_voiceover_enhanced.maps.map_render_job import (
    JobStatus,
    MapRenderJobDocument,
    get_map_render_job_manager,
    reset_map_render_job_manager_for_tests,
)
from otio_app.services.without_voiceover_enhanced.maps.models import (
    COORDINATE_STATUS_MANUAL,
    MAP_ANIMATION_OPENING,
    MAP_ANIMATION_TRANSITION,
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
    build_map_plan,
    save_map_plan,
)
from otio_app.services.job_registry import any_job_running
from otio_app.services.without_voiceover_enhanced.maps.remotion_payload import (
    country_numeric_id,
    remotion_payload,
    view_bounds,
)
from otio_app.services.without_voiceover_enhanced.maps.render_service import (
    MapRenderError,
    MapRenderer,
    packaged_renderer_root,
    selectable_maps,
)
from otio_app.services.without_voiceover_enhanced.paths import map_render_job_path
from otio_app.services.without_voiceover_enhanced.io_utils import write_json
from otio_app.services.voiceover_generation.dramaturgy_service import (
    save_confirmed_dramaturgy,
)
from otio_app.services.voiceover_generation.models import (
    DramaturgyFolderEntry,
    DramaturgyPlan,
)
from otio_app.ui.navigation import PAGE_MAPS, VOICEOVER_GEN_WORKFLOW_PAGES


def _project(tmp_path: Path, folders: list[str], *, language: str = "fr") -> Project:
    root = tmp_path / "proj"
    work = root / DEFAULT_ENHANCED_WORK_SUBDIR
    work.mkdir(parents=True)
    for folder in folders:
        (root / folder).mkdir(parents=True, exist_ok=True)
    return Project(
        name="Map Render Test",
        project_root=str(root),
        work_dir=str(work),
        project_mode=ProjectMode.WITHOUT_VOICEOVER_ENHANCED,
        language=language,
        video_place="Greece",
        asset_subdir_names=folders,
        selected_asset_subdirs=folders,
        fps=25.0,
    )


def _confirm(project: Project, folders: list[str], *, language: str = "FR") -> None:
    save_confirmed_dramaturgy(
        project,
        DramaturgyPlan(
            project_id=project.id,
            language=language,
            project_title="Map Test",
            recommended_folder_order=[
                DramaturgyFolderEntry(
                    folder_name=folder,
                    order_index=index,
                    enabled=True,
                )
                for index, folder in enumerate(folders)
            ],
        ),
    )


def _coords(project: Project, folders: list[str]) -> MapCoordinatesDocument:
    places = {}
    for index, folder in enumerate(folders):
        places[folder] = MapCoordinateRecord(
            chapter_id=folder,
            original_label=folder,
            display_label=folder,
            latitude=40.0 + index,
            longitude=22.0 + index,
            confidence=1.0,
            status=COORDINATE_STATUS_MANUAL,
            source="test",
            country_context=project.video_place,
        )
    return MapCoordinatesDocument(
        project_id=project.id, country=project.video_place, places=places
    )


def _probe(_path: Path) -> dict:
    return {
        "width": 1920,
        "height": 1080,
        "fps": 25.0,
        "frames": 225,
        "audio_stream_count": 0,
        "audio_codec": "",
    }


def _fake_root(tmp_path: Path) -> Path:
    root = tmp_path / "renderer"
    (root / "scripts").mkdir(parents=True)
    (root / "node_modules").mkdir()
    (root / "scripts" / "render.mjs").write_text("// fake\n", encoding="utf-8")
    (root / "package-lock.json").write_text("{}", encoding="utf-8")
    return root


def _renderer(tmp_path: Path, command_runner, *, media_probe=_probe, **kwargs) -> MapRenderer:
    return MapRenderer(
        renderer_root=_fake_root(tmp_path),
        command_runner=command_runner,
        media_probe=media_probe,
        **kwargs,
    )


@pytest.fixture
def isolated_manager(monkeypatch: pytest.MonkeyPatch):
    reset_map_render_job_manager_for_tests()
    yield
    reset_map_render_job_manager_for_tests()


def test_remotion_payload_opening_and_transition_and_filename_identity(tmp_path: Path) -> None:
    folders = ["Mount Athos", "Meteora"]
    project = _project(tmp_path, folders)
    _confirm(project, folders)
    plan = build_map_plan(project, coordinates=_coords(project, folders))
    opening = remotion_payload(plan.maps[0])
    transition = remotion_payload(plan.maps[1])
    assert opening["animationMode"] == "intro"
    assert opening["routeKind"] == "deterministic_ramp_zoom"
    assert opening["from"]["latitude"] == opening["to"]["latitude"]
    assert opening["exportLabel"] == "Mount Athos"
    assert opening["to"]["label"] == "Mount Athos"
    assert opening["styleVersion"] == "otio-vintage-map-v11"
    assert "thomas" not in str(opening).lower()
    assert transition["animationMode"] == "transition"
    assert transition["from"]["label"] == "Mount Athos"
    assert transition["to"]["label"] == "Meteora"
    assert plan.maps[0].output_filename == "fr_Mount Athos_Map.mp4"
    assert plan.maps[0].animation_mode == MAP_ANIMATION_OPENING
    assert plan.maps[1].animation_mode == MAP_ANIMATION_TRANSITION


def test_selectable_maps_skip_blocked_and_missing_mode(tmp_path: Path) -> None:
    folders = ["Mount Athos", "Meteora"]
    project = _project(tmp_path, folders)
    _confirm(project, folders)
    coordinates = _coords(project, ["Mount Athos"])
    plan = build_map_plan(project, coordinates=coordinates)
    assert plan.maps[0].render_status == RENDER_STATUS_IDLE
    assert plan.maps[1].render_status == RENDER_STATUS_BLOCKED
    assert [item.chapter_id for item in selectable_maps(plan.maps, mode="all")] == [
        "Mount Athos"
    ]
    assert selectable_maps(plan.maps, mode="missing")[0].chapter_id == "Mount Athos"
    assert selectable_maps(plan.maps, mode="one") == []
    assert [item.chapter_id for item in selectable_maps(plan.maps, mode="one", chapter_id="Mount Athos")] == [
        "Mount Athos"
    ]
    plan.maps[0].render_status = RENDER_STATUS_DONE
    plan.maps[0].output_path = str(tmp_path / "exists.mp4")
    (tmp_path / "exists.mp4").write_bytes(b"ok")
    assert selectable_maps(plan.maps, mode="missing") == []
    assert [item.chapter_id for item in selectable_maps(plan.maps, mode="one", chapter_id="Mount Athos")] == [
        "Mount Athos"
    ]


def test_view_bounds_usa_ireland_and_unknown() -> None:
    assert country_numeric_id("USA") == "840"
    assert country_numeric_id("Ireland") == "372"
    assert country_numeric_id("Atlantis") == "000"
    assert view_bounds("840", 0.0, 0.0, 1.0, 1.0) == [[-125.0, 24.0], [-66.0, 50.0]]
    assert view_bounds("372", 0.0, 0.0, 1.0, 1.0) == [[-11.2, 51.15], [-5.05, 55.85]]
    padded = view_bounds("000", 22.0, 40.0, 23.0, 41.0)
    assert padded[0][0] < 22.0
    assert padded[1][0] > 23.0


def test_render_reuses_identical_plan_hash(tmp_path: Path) -> None:
    folders = ["Mount Athos"]
    project = _project(tmp_path, folders)
    _confirm(project, folders)
    plan = build_map_plan(project, coordinates=_coords(project, folders))
    save_map_plan(project, plan)
    calls = {"n": 0}

    def runner(command, **_kwargs):
        calls["n"] += 1
        Path(command[command.index("--output") + 1]).write_bytes(b"deterministic-map")
        return subprocess.CompletedProcess(command, 0, "", "")

    renderer = _renderer(tmp_path, runner)
    first = renderer.render_item(project, plan.maps[0])
    second = renderer.render_item(project, plan.maps[0])
    assert calls["n"] == 1
    assert first["reused"] is False
    assert second["reused"] is True
    assert first["content_hash"] == second["content_hash"]
    assert Path(first["export_path"]).read_bytes() == b"deterministic-map"
    assert first["has_audio"] is False
    sidecar = Path(first["export_path"]).with_suffix(".mp4.meta.json")
    assert sidecar.is_file()
    third = renderer.render_item(project, plan.maps[0], overwrite=True)
    assert calls["n"] == 2
    assert third["reused"] is False


def test_validate_rejects_audio_and_wrong_fps(tmp_path: Path) -> None:
    folders = ["Mount Athos"]
    project = _project(tmp_path, folders)
    _confirm(project, folders)
    plan = build_map_plan(project, coordinates=_coords(project, folders))
    item = plan.maps[0]

    def runner(command, **_kwargs):
        Path(command[command.index("--output") + 1]).write_bytes(b"bad")
        return subprocess.CompletedProcess(command, 0, "", "")

    audio_renderer = _renderer(
        tmp_path,
        runner,
        media_probe=lambda _p: {
            "width": 1920,
            "height": 1080,
            "fps": 25.0,
            "frames": 225,
            "audio_stream_count": 1,
            "audio_codec": "aac",
        },
    )
    with pytest.raises(MapRenderError, match="Audiospur"):
        audio_renderer.render_item(project, item)

    fps_renderer = _renderer(
        tmp_path / "fps",
        runner,
        media_probe=lambda _p: {
            "width": 1920,
            "height": 1080,
            "fps": 30.0,
            "frames": 225,
            "audio_stream_count": 0,
            "audio_codec": "",
        },
    )
    with pytest.raises(MapRenderError, match="Bildrate"):
        fps_renderer.render_item(project, item)


def test_nice_prefix_and_monotonic_progress(tmp_path: Path) -> None:
    commands = []

    def runner(command, **_kwargs):
        commands.append(command)
        Path(command[command.index("--output") + 1]).write_bytes(b"x")
        return subprocess.CompletedProcess(command, 0, "", "")

    renderer = _renderer(tmp_path, runner)
    folders = ["Mount Athos"]
    project = _project(tmp_path, folders)
    _confirm(project, folders)
    plan = build_map_plan(project, coordinates=_coords(project, folders))
    progress: list[float] = []
    renderer.render_item(project, plan.maps[0], progress_callback=progress.append)
    assert commands[0][:4] == ["nice", "-n", "10", "node"]
    assert progress[-1] == 1.0
    assert progress == sorted(progress)


def test_renderer_timeout_kills_process_group() -> None:
    renderer = MapRenderer(
        renderer_root=Path(sys.prefix),
        render_timeout_seconds=0.05,
    )
    completed = renderer._run_renderer(
        [sys.executable, "-c", "import time; time.sleep(5)"],
        cwd=str(Path.cwd()),
        progress_callback=None,
    )
    assert completed.returncode != 0
    assert "Zeitlimit von 0.05 Sekunden" in completed.stderr


def test_progress_parser_never_goes_backwards() -> None:
    renderer = MapRenderer(renderer_root=Path(sys.prefix), render_timeout_seconds=5)
    script = (
        "import sys\n"
        "print('OTIO_MAP_RENDER_PROGRESS=0.4', flush=True)\n"
        "print('OTIO_MAP_RENDER_PROGRESS=0.2', flush=True)\n"
        "print('OTIO_MAP_RENDER_PROGRESS=0.9', flush=True)\n"
    )
    seen: list[float] = []
    completed = renderer._run_renderer(
        [sys.executable, "-c", script],
        cwd=str(Path.cwd()),
        progress_callback=seen.append,
    )
    assert completed.returncode == 0
    assert seen == sorted(seen)
    assert seen[-1] >= 0.9


def test_hd_max_four_and_4k_max_two_parallel(
    tmp_path: Path, isolated_manager, monkeypatch: pytest.MonkeyPatch
) -> None:
    folders = [f"Place {index}" for index in range(5)]
    project = _project(tmp_path, folders)
    _confirm(project, folders)
    monkeypatch.setattr(
        "otio_app.services.without_voiceover_enhanced.maps.map_render_job.get_project_by_id",
        lambda pid: project if pid == project.id else None,
    )
    current = 0
    max_seen = 0
    lock = threading.Lock()

    def make_runner():
        def runner(command, **_kwargs):
            nonlocal current, max_seen
            with lock:
                current += 1
                max_seen = max(max_seen, current)
            time.sleep(0.15)
            Path(command[command.index("--output") + 1]).write_bytes(b"p")
            with lock:
                current -= 1
            return subprocess.CompletedProcess(command, 0, "", "")

        return runner

    plan = build_map_plan(
        project,
        settings=MapRenderSettings(resolution=MAP_RESOLUTION_HD, max_parallel=8),
        coordinates=_coords(project, folders),
    )
    save_map_plan(project, plan)
    manager = get_map_render_job_manager()
    assert manager.start(project, mode="all", renderer=_renderer(tmp_path, make_runner()))
    deadline = time.time() + 8
    while manager.is_running(project.id) and time.time() < deadline:
        time.sleep(0.05)
    assert not manager.is_running(project.id)
    assert max_seen <= 4
    assert max_seen >= 2

    reset_map_render_job_manager_for_tests()
    current = 0
    max_seen = 0
    k_plan = build_map_plan(
        project,
        settings=MapRenderSettings(resolution=MAP_RESOLUTION_4K, max_parallel=8),
        coordinates=_coords(project, folders),
        previous=None,
    )
    for item in k_plan.maps:
        item.output_path = ""
        item.render_status = RENDER_STATUS_IDLE
        item.media_hash = ""
    save_map_plan(project, k_plan)
    manager = get_map_render_job_manager()
    four_k_probe = lambda _p: {
        "width": 3840,
        "height": 2160,
        "fps": 25.0,
        "frames": 225,
        "audio_stream_count": 0,
        "audio_codec": "",
    }
    assert manager.start(
        project,
        mode="all",
        renderer=_renderer(tmp_path / "4k", make_runner(), media_probe=four_k_probe),
    )
    deadline = time.time() + 8
    while manager.is_running(project.id) and time.time() < deadline:
        time.sleep(0.05)
    assert not manager.is_running(project.id)
    assert max_seen <= 2


def test_job_cancel_kills_subprocess(
    tmp_path: Path, isolated_manager, monkeypatch: pytest.MonkeyPatch
) -> None:
    folders = ["Mount Athos"]
    project = _project(tmp_path, folders)
    _confirm(project, folders)
    monkeypatch.setattr(
        "otio_app.services.without_voiceover_enhanced.maps.map_render_job.get_project_by_id",
        lambda pid: project if pid == project.id else None,
    )
    root = _fake_root(tmp_path)
    (root / "scripts" / "render.mjs").write_text(
        "import { writeFileSync } from 'node:fs';\n"
        "const output = process.argv[process.argv.indexOf('--output') + 1];\n"
        "await new Promise((resolve) => setTimeout(resolve, 20000));\n"
        "writeFileSync(output, 'late');\n",
        encoding="utf-8",
    )
    (root / "package.json").write_text(
        '{"name":"fake-map-renderer","type":"module"}\n',
        encoding="utf-8",
    )
    plan = build_map_plan(project, coordinates=_coords(project, folders))
    save_map_plan(project, plan)
    renderer = MapRenderer(renderer_root=root, media_probe=_probe, render_timeout_seconds=30)
    manager = get_map_render_job_manager()
    assert manager.start(project, mode="all", renderer=renderer)
    deadline = time.time() + 3
    while time.time() < deadline:
        state = manager.get_state(project.id)
        if state and state.items and list(state.items.values())[0].progress > 0:
            break
        if renderer._processes:
            break
        time.sleep(0.05)
    assert manager.request_cancel(project.id)
    deadline = time.time() + 5
    while manager.is_running(project.id) and time.time() < deadline:
        time.sleep(0.05)
    assert not manager.is_running(project.id)
    state = manager.get_state(project.id)
    assert state is not None
    assert state.status == JobStatus.CANCELLED
    assert not renderer._processes


def test_restart_marks_interrupted_job_cancelled(
    tmp_path: Path, isolated_manager, monkeypatch: pytest.MonkeyPatch
) -> None:
    folders = ["Mount Athos"]
    project = _project(tmp_path, folders)
    _confirm(project, folders)
    monkeypatch.setattr(
        "otio_app.services.without_voiceover_enhanced.maps.map_render_job.get_project_by_id",
        lambda pid: project if pid == project.id else None,
    )
    write_json(
        map_render_job_path(project),
        MapRenderJobDocument(
            project_id=project.id,
            status=JobStatus.RUNNING.value,
            mode="all",
            message="läuft",
            chapter_ids=["Mount Athos"],
            items={"Mount Athos": {"chapter_id": "Mount Athos", "status": "rendering", "progress": 0.4}},
        ),
    )
    reset_map_render_job_manager_for_tests()
    manager = get_map_render_job_manager()
    state = manager.get_state(project.id)
    assert state is not None
    assert state.status == JobStatus.CANCELLED
    assert "Neustart" in (state.error or "")
    plan = build_map_plan(project, coordinates=_coords(project, folders))
    save_map_plan(project, plan)
    calls = {"n": 0}

    def runner(command, **_kwargs):
        calls["n"] += 1
        Path(command[command.index("--output") + 1]).write_bytes(b"resume")
        return subprocess.CompletedProcess(command, 0, "", "")

    assert manager.start(project, mode="missing", renderer=_renderer(tmp_path, runner))
    deadline = time.time() + 5
    while manager.is_running(project.id) and time.time() < deadline:
        time.sleep(0.05)
    assert calls["n"] == 1
    assert manager.get_state(project.id).status == JobStatus.COMPLETED


def test_any_job_running_includes_map_jobs(
    tmp_path: Path, isolated_manager, monkeypatch: pytest.MonkeyPatch
) -> None:
    folders = ["Mount Athos"]
    project = _project(tmp_path, folders)
    _confirm(project, folders)
    monkeypatch.setattr(
        "otio_app.services.without_voiceover_enhanced.maps.map_render_job.get_project_by_id",
        lambda pid: project if pid == project.id else None,
    )
    started = threading.Event()

    def runner(command, **_kwargs):
        started.set()
        time.sleep(0.25)
        Path(command[command.index("--output") + 1]).write_bytes(b"busy")
        return subprocess.CompletedProcess(command, 0, "", "")

    plan = build_map_plan(project, coordinates=_coords(project, folders))
    save_map_plan(project, plan)
    manager = get_map_render_job_manager()
    assert manager.start(project, mode="all", renderer=_renderer(tmp_path, runner))
    assert started.wait(timeout=3)
    assert any_job_running(project.id)
    deadline = time.time() + 5
    while manager.is_running(project.id) and time.time() < deadline:
        time.sleep(0.05)
    assert not manager.is_running(project.id)


def test_shared_bundle_cache_js() -> None:
    renderer = packaged_renderer_root()
    test_file = renderer / "tests" / "bundle-cache.test.mjs"
    completed = subprocess.run(
        ["node", "--test", str(test_file)],
        cwd=str(renderer),
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert "parallel renders prepare one shared Remotion bundle" in (
        completed.stdout + completed.stderr
    )


def test_packaged_renderer_has_no_thomas_paths() -> None:
    root = packaged_renderer_root()
    assert root.is_dir()
    assert (root / "scripts" / "render.mjs").is_file()
    assert (root / "scripts" / "bundle-cache.mjs").is_file()
    forbidden = (
        "THOMAS_",
        "/Users/claudiakuhn",
        "thomas-map-renderer",
        "thomas-vintage-map",
        "generated-sounds",
    )
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        if any(part in {"node_modules", ".cache"} for part in path.parts):
            continue
        if path.suffix.lower() not in {".mjs", ".js", ".ts", ".tsx", ".json"}:
            continue
        text = path.read_text(encoding="utf-8")
        for token in forbidden:
            assert token not in text, f"{token} in {path}"
    assert "PAGE_MAPS" not in AUTO_RUN_STEPS[0]
    assert PAGE_MAPS not in VOICEOVER_GEN_WORKFLOW_PAGES
    assert "maps" not in {step_id for step_id, _label in AUTO_RUN_STEPS}
