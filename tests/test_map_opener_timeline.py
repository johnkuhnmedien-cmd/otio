"""Gerenderte Karten müssen in Timeline/OTIO landen — auch ohne Keyword Flow."""

from __future__ import annotations

from pathlib import Path

import pytest

from otio_app.defaults import DEFAULT_ENHANCED_WORK_SUBDIR
from otio_app.models import Project, ProjectMode
from otio_app.services.without_voiceover_enhanced.chapter_cut_service import (
    get_chapter_cut_status,
)
from otio_app.services.without_voiceover_enhanced.enhanced_auto_run_service import (
    otio_export_complete,
)
from otio_app.services.without_voiceover_enhanced.intro_cut_service import (
    intro_resolved_timeline_path,
)
from otio_app.services.without_voiceover_enhanced.io_utils import write_json
from otio_app.services.without_voiceover_enhanced.keyword_flow_maps import (
    MapOpenerDecision,
    _list_map_media_for_chapter,
    chapter_needs_map_opener_retiming,
    decide_map_opener,
    duration_from_probe_payload,
    map_opener_duration_slack_seconds,
)
from otio_app.services.without_voiceover_enhanced.models import (
    CutBoundary,
    CutSlot,
    ResolvedShot,
    ResolvedTimelineDocument,
    UnifiedCutPlanDocument,
)
from otio_app.services.without_voiceover_enhanced.paths import (
    chapter_resolved_timeline_path,
    chapter_unified_cut_plan_path,
    exports_dir,
    map_output_dir,
    resolved_timeline_path,
)


def _project(tmp_path: Path, folders: list[str] | None = None) -> Project:
    names = folders or ["Yosemite"]
    root = tmp_path / "proj"
    work = root / DEFAULT_ENHANCED_WORK_SUBDIR
    work.mkdir(parents=True)
    for folder in names:
        (root / folder).mkdir(parents=True, exist_ok=True)
    return Project(
        name="MapTimeline",
        project_root=str(root),
        work_dir=str(work),
        project_mode=ProjectMode.WITHOUT_VOICEOVER_ENHANCED,
        language="de",
        asset_subdir_names=names,
        selected_asset_subdirs=names,
        fps=25.0,
    )


def _plan(slug: str, slots: int = 1) -> UnifiedCutPlanDocument:
    bounds = [
        CutBoundary(
            cut_id=f"{slug}_cut_{index:03d}",
            sentence_id=f"{slug}_seg__s00{index + 1}",
            position="start" if index == 0 else "end",
            alignment="sentence_boundary",
        )
        for index in range(slots + 1)
    ]
    bounds[-1] = bounds[-1].model_copy(update={"position": "end"})
    return UnifiedCutPlanDocument(
        script_version="v1",
        boundaries=bounds,
        slots=[
            CutSlot(
                slot_id=f"{slug}_slot_{index:03d}",
                local_asset_id="a1",
                asset_fit="strong",
                asset_fit_reason="test",
                visual_intent="valley",
            )
            for index in range(1, slots + 1)
        ],
    )


def _resolved_without_map(slug: str = "Yosemite") -> ResolvedTimelineDocument:
    return ResolvedTimelineDocument(
        script_version="v1",
        fps=25.0,
        total_duration_seconds=5.0,
        shots=[
            ResolvedShot(
                shot_id=f"{slug}_slot_001",
                asset_id="a1",
                timeline_start_seconds=0.0,
                timeline_end_seconds=5.0,
                source_start_seconds=0.0,
                source_end_seconds=5.0,
                chapter_id=slug,
                folder_name=slug,
            )
        ],
    )


def test_duration_from_probe_payload_prefers_frame_count() -> None:
    duration, fps = duration_from_probe_payload(
        {
            "streams": [
                {
                    "duration": "8.96",
                    "nb_frames": "225",
                    "avg_frame_rate": "25/1",
                    "width": 1920,
                    "height": 1080,
                }
            ],
            "format": {"duration": "8.96"},
        }
    )
    assert duration == pytest.approx(9.0)
    assert fps == pytest.approx(25.0)


def test_one_frame_short_map_is_usable() -> None:
    slack = map_opener_duration_slack_seconds(25.0)
    assert 8.96 + slack >= 9.0
    assert 4.0 + slack < 9.0


def test_decide_map_opener_accepts_one_frame_short(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = _project(tmp_path, ["ChapterA", "Maps"])
    maps = Path(project.project_root) / "Maps"
    maps.mkdir(exist_ok=True)
    media = maps / "ChapterA.mp4"
    media.write_bytes(b"fake")
    monkeypatch.setattr(
        "otio_app.services.without_voiceover_enhanced.keyword_flow_maps._probe_map_media",
        lambda _path: {
            "ok": True,
            "width": 1920,
            "height": 1080,
            "duration_seconds": 8.96,
            "fps": 25.0,
        },
    )
    decision = decide_map_opener(project, "ChapterA")
    assert decision.status == "used"
    assert decision.opener_seconds == pytest.approx(9.0)


def test_enhanced_output_matches_plan_chapter_id_slug(tmp_path: Path) -> None:
    project = _project(tmp_path, ["Bleder See"])
    output = map_output_dir(project)
    output.mkdir(parents=True)
    path = output / "de_Bleder See_Map.mp4"
    path.write_bytes(b"map")
    matches = _list_map_media_for_chapter(project, "Bleder See")
    assert len(matches) == 1
    assert matches[0]["path"] == str(path)


def test_chapter_needs_retiming_when_map_missing_from_resolved(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = _project(tmp_path)
    resolved = _resolved_without_map()
    monkeypatch.setattr(
        "otio_app.services.without_voiceover_enhanced.keyword_flow_maps._list_map_media_for_chapter",
        lambda *_args, **_kwargs: [{"path": "/tmp/map.mp4", "asset_id": "map"}],
    )
    monkeypatch.setattr(
        "otio_app.services.without_voiceover_enhanced.keyword_flow_maps.decide_map_opener",
        lambda *_args, **_kwargs: MapOpenerDecision(
            chapter_id="Yosemite",
            status="used",
            media_path="/tmp/map.mp4",
            asset_id="map",
        ),
    )
    assert chapter_needs_map_opener_retiming(project, "Yosemite", resolved) is True


def test_chapter_status_reopens_timing_when_map_is_rendered(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = _project(tmp_path)
    folder = "Yosemite"
    write_json(chapter_unified_cut_plan_path(project, folder), _plan(folder))
    write_json(chapter_resolved_timeline_path(project, folder), _resolved_without_map())
    monkeypatch.setattr(
        "otio_app.services.without_voiceover_enhanced.keyword_flow_maps._list_map_media_for_chapter",
        lambda *_args, **_kwargs: [{"path": "/tmp/map.mp4", "asset_id": "map"}],
    )
    monkeypatch.setattr(
        "otio_app.services.without_voiceover_enhanced.keyword_flow_maps.decide_map_opener",
        lambda *_args, **_kwargs: MapOpenerDecision(
            chapter_id=folder,
            status="used",
            media_path="/tmp/map.mp4",
            asset_id="map",
        ),
    )
    status = get_chapter_cut_status(project, folder)
    assert status.has_plan
    assert status.has_resolved
    assert status.matches is False
    assert "Karten gerendert" in status.timing_mismatch_detail


def test_otio_export_complete_false_when_timing_open(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = _project(tmp_path)
    intro = intro_resolved_timeline_path(project)
    intro.parent.mkdir(parents=True, exist_ok=True)
    intro.write_text("{}", encoding="utf-8")
    out_dir = exports_dir(project)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / f"{project.name}_enhanced.otio").write_text("otio", encoding="utf-8")
    monkeypatch.setattr(
        "otio_app.services.without_voiceover_enhanced.enhanced_auto_run_service.list_chapters_needing_python_timing",
        lambda _project: ["Yosemite"],
    )
    assert otio_export_complete(project) is False


def test_otio_export_complete_false_when_resolved_newer_than_otio(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = _project(tmp_path)
    intro = intro_resolved_timeline_path(project)
    intro.parent.mkdir(parents=True, exist_ok=True)
    intro.write_text("{}", encoding="utf-8")
    out_dir = exports_dir(project)
    out_dir.mkdir(parents=True, exist_ok=True)
    otio_path = out_dir / f"{project.name}_enhanced.otio"
    otio_path.write_text("otio", encoding="utf-8")
    monkeypatch.setattr(
        "otio_app.services.without_voiceover_enhanced.enhanced_auto_run_service.list_chapters_needing_python_timing",
        lambda _project: [],
    )
    monkeypatch.setattr(
        "otio_app.services.without_voiceover_enhanced.enhanced_auto_run_service.list_body_chapter_names",
        lambda _project: [],
    )
    newer = resolved_timeline_path(project)
    newer.parent.mkdir(parents=True, exist_ok=True)
    newer.write_text("{}", encoding="utf-8")
    stamp = otio_path.stat().st_mtime + 5
    import os

    os.utime(newer, (stamp, stamp))
    assert otio_export_complete(project) is False
