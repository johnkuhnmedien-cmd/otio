"""Tests für Abbruch der Asset-Analyse."""

from __future__ import annotations

from otio_app.services.gemini_client import MediaFrameAnalysis

from pathlib import Path

import pytest

from otio_app.models import Project
from otio_app.services.asset_analyzer import analyze_asset_folders


def _sample_project(layout: dict[str, Path]) -> Project:
    return Project(
        id="test-project",
        name="Test",
        project_root=str(layout["project_root"]),
        work_dir=str(layout["work_dir"]),
        asset_subdir_names=["Grand Canyon"],
        selected_asset_subdirs=["Grand Canyon"],
    )


def test_analyze_asset_folders_can_be_cancelled(
    temp_project_layout: dict[str, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    folder = temp_project_layout["project_root"] / "Grand Canyon"
    (folder / "clip2.mp4").write_bytes(b"video2")
    (folder / "clip3.mp4").write_bytes(b"video3")

    calls: list[str] = []
    cancel_after = {"count": 0}

    def fake_extract(media_path: Path, output_dir: Path, count: int, *, should_cancel=None) -> list[Path]:
        output_dir.mkdir(parents=True, exist_ok=True)
        frame = output_dir / "frame_001.jpg"
        frame.write_bytes(b"jpeg")
        return [frame]

    def fake_describe(
        media_name: str,
        folder_name: str,
        frame_paths: list[Path],
        language: str,
        *,
        model: str | None = None,
    ) -> MediaFrameAnalysis:
        calls.append(media_name)
        cancel_after["count"] += 1
        return MediaFrameAnalysis.successful(description=f"Beschreibung für {media_name}")
    def should_cancel() -> bool:
        return cancel_after["count"] >= 1

    monkeypatch.setattr(
        "otio_app.services.asset_analyzer.extract_frames",
        fake_extract,
    )
    monkeypatch.setattr(
        "otio_app.services.asset_analyzer.analyze_media_from_frames",
        fake_describe,
    )

    project = _sample_project(temp_project_layout)
    phases: list[str] = []

    def on_progress(phase: str, _data: dict) -> None:
        phases.append(phase)

    _, report = analyze_asset_folders(
        project,
        ["Grand Canyon"],
        use_api=True,
        on_progress=on_progress,
        should_cancel=should_cancel,
    )

    assert calls == ["clip.mp4"]
    assert report.cancelled is True
    assert report.media_analyzed == 1
    assert "cancelled" in phases


def test_asset_analysis_job_manager_cancel(
    temp_project_layout: dict[str, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from otio_app.services.asset_analysis_job import JobStatus, get_asset_analysis_job_manager

    folder = temp_project_layout["project_root"] / "Grand Canyon"
    (folder / "clip2.mp4").write_bytes(b"video2")

    describe_calls = {"count": 0}

    def fake_extract(media_path: Path, output_dir: Path, count: int, *, should_cancel=None) -> list[Path]:
        output_dir.mkdir(parents=True, exist_ok=True)
        frame = output_dir / "frame_001.jpg"
        frame.write_bytes(b"jpeg")
        return [frame]

    def fake_describe(
        media_name: str,
        folder_name: str,
        frame_paths: list[Path],
        language: str,
        *,
        model: str | None = None,
    ) -> MediaFrameAnalysis:
        describe_calls["count"] += 1
        if describe_calls["count"] >= 1:
            get_asset_analysis_job_manager().request_cancel("test-project")
        return MediaFrameAnalysis.successful(description=f"Beschreibung für {media_name}")
    monkeypatch.setattr(
        "otio_app.services.asset_analyzer.extract_frames",
        fake_extract,
    )
    monkeypatch.setattr(
        "otio_app.services.asset_analyzer.analyze_media_from_frames",
        fake_describe,
    )
    project = _sample_project(temp_project_layout)

    monkeypatch.setattr(
        "otio_app.services.asset_analysis_job.update_project_status",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        "otio_app.services.asset_analysis_job.get_project_by_id",
        lambda project_id: project if project_id == project.id else None,
    )

    manager = get_asset_analysis_job_manager()
    manager.dismiss(project.id)

    assert manager.start(project, ["Grand Canyon"], "gemini-3.1-flash-lite") is True

    import time

    deadline = time.time() + 5.0
    state = manager.get_state(project.id)
    while time.time() < deadline:
        state = manager.get_state(project.id)
        if state is not None and state.status != JobStatus.RUNNING:
            break
        time.sleep(0.05)

    assert state is not None
    assert state.status == JobStatus.CANCELLED
    assert state.report is not None
    assert state.report.cancelled is True
