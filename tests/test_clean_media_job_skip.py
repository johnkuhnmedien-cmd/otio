"""Clean-Media-Job: bereits bereite Ordner überspringen."""

from __future__ import annotations

import time
from pathlib import Path
from unittest.mock import patch

from otio_app.analysis_models import CleanMediaEntry, CleanMediaManifest
from otio_app.models import Project
from otio_app.services.clean_media import CLEAN_STATUS_OK, save_clean_media_manifest
from otio_app.services.clean_media_job import (
    CleanMediaJobMode,
    JobStatus,
    get_clean_media_job_manager,
)


def _project(tmp_path: Path) -> Project:
    root = tmp_path / "USA"
    ready = root / "Grand Canyon"
    open_folder = root / "Antelope Canyon"
    ready.mkdir(parents=True)
    open_folder.mkdir(parents=True)
    (ready / "clip.mp4").write_bytes(b"ready")
    (open_folder / "clip.mp4").write_bytes(b"open")
    work = root / "_otio"
    work.mkdir(parents=True)
    return Project(
        id="clean-skip-test",
        name="USA",
        project_root=str(root),
        work_dir=str(work),
        asset_subdir_names=["Grand Canyon", "Antelope Canyon"],
        selected_asset_subdirs=["Grand Canyon", "Antelope Canyon"],
    )


def test_process_job_skips_ready_folders(tmp_path: Path) -> None:
    project = _project(tmp_path)
    ready_original = project.project_root_path / "Grand Canyon" / "clip.mp4"
    save_clean_media_manifest(
        project.work_dir_path / "clean_media" / "Grand_Canyon.json",
        CleanMediaManifest(
            project_id=project.id,
            folder="Grand Canyon",
            entries=[
                CleanMediaEntry(
                    original_path=str(ready_original.resolve()),
                    status=CLEAN_STATUS_OK,
                )
            ],
        ),
    )

    manager = get_clean_media_job_manager()
    processed: list[str] = []

    def fake_process(project_arg, folder_name, **kwargs):
        processed.append(folder_name)
        return CleanMediaManifest(project_id=project_arg.id, folder=folder_name, entries=[])

    with (
        patch(
            "otio_app.services.clean_media_job.get_project_by_id",
            return_value=project,
        ),
        patch(
            "otio_app.services.clean_media_job.process_folder",
            side_effect=fake_process,
        ),
    ):
        assert manager.start(
            project,
            ["Grand Canyon", "Antelope Canyon"],
            mode=CleanMediaJobMode.PROCESS,
        )
        for _ in range(50):
            state = manager.get_state(project.id)
            if state is not None and state.status != JobStatus.RUNNING:
                break
            time.sleep(0.05)

    state = manager.get_state(project.id)
    assert state is not None
    assert state.status == JobStatus.COMPLETED
    assert "Grand Canyon" in state.skipped_folders
    assert processed == ["Antelope Canyon"]
    manager.dismiss(project.id)
