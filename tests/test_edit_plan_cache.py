"""Tests für schnelles Schnittplan-Meta-Laden."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from otio_app.analysis_models import EditPlanDocument, EditPlanSettings, EditPlanShot
from otio_app.models import Project
from otio_app.services.edit_plan_builder import save_edit_plan
from otio_app.services.edit_plan_cache import (
    EditPlanFolderMeta,
    collect_folder_statuses,
    load_edit_plan_folder_meta,
    resolve_location_status_from_meta,
)
from otio_app.services.edit_plan_builder import EditPlanLocationState


def _project(tmp_path: Path) -> Project:
    root = tmp_path / "USA"
    root.mkdir()
    return Project(
        id="cache-test",
        name="USA",
        project_root=str(root),
        work_dir=str(root / "_otio"),
        asset_subdir_names=["Florida Keys"],
        selected_asset_subdirs=["Florida Keys"],
    )


def test_load_edit_plan_folder_meta_without_full_validation(tmp_path: Path) -> None:
    project = _project(tmp_path)
    document = EditPlanDocument(
        project_id=project.id,
        folder_name="Florida Keys",
        confirmed=True,
        settings=EditPlanSettings(),
        shots=[
            EditPlanShot(
                voice_file="/vo/test.wav",
                folder="Florida Keys",
                voice_start_sec=0.0,
                voice_end_sec=3.0,
                duration_sec=3.0,
                asset_path="/media/a.mp4",
            )
        ],
    )
    save_edit_plan(project, document, "Florida Keys")

    meta = load_edit_plan_folder_meta(project, "Florida Keys")
    assert meta.confirmed is True
    assert meta.shot_count == 1


def test_collect_folder_statuses_uses_meta(tmp_path: Path) -> None:
    project = _project(tmp_path)
    path = project.folder_edit_plan_path("Florida Keys")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "project_id": project.id,
                "folder_name": "Florida Keys",
                "confirmed": True,
                "settings": EditPlanSettings().model_dump(mode="json"),
                "shots": [{"voice_file": "a", "folder": "Florida Keys", "duration_sec": 1.0}],
            }
        ),
        encoding="utf-8",
    )

    statuses = collect_folder_statuses(
        project,
        project.id,
        ["Florida Keys"],
        get_draft=lambda _pid, _folder: None,
    )
    assert len(statuses) == 1
    assert statuses[0].shot_count == 1


def test_resolve_location_status_prefers_newer_meta_over_stale_session_draft() -> None:
    """Regression: Ein Session-Entwurf (z.B. vom Vorschlag-Tab in einer
    früheren Interaktion) gewann bisher IMMER, selbst wenn zwischenzeitlich
    z.B. der Supplement-Assets-Auto-Replan einen neueren Schnittplan direkt
    auf die Festplatte geschrieben hat. Das ließ die Orts-Auswahl einen
    veralteten Shot-Count/Status anzeigen."""
    now = datetime.now(timezone.utc)
    stale_draft = EditPlanDocument(
        generated_at=now - timedelta(minutes=10),
        project_id="p1",
        folder_name="Badlands National Park",
        confirmed=False,
        shots=[
            EditPlanShot(
                voice_file="v",
                folder="Badlands National Park",
                voice_start_sec=0.0,
                voice_end_sec=1.0,
                duration_sec=1.0,
            )
        ],
    )
    fresh_meta = EditPlanFolderMeta(
        folder_name="Badlands National Park",
        confirmed=False,
        shot_count=5,
        generated_at=now.isoformat(),
    )

    status = resolve_location_status_from_meta(
        "Badlands National Park", fresh_meta, draft=stale_draft
    )
    assert status.shot_count == 5
    assert status.state == EditPlanLocationState.DRAFT


def test_resolve_location_status_keeps_newer_session_draft_over_older_meta() -> None:
    """Ein tatsächlich NEUERER Session-Entwurf (in Bearbeitung, noch nicht
    gespeichert) muss weiterhin gegenüber einer älteren gespeicherten Datei
    gewinnen — z.B. während der Nutzer gerade unter „Vorschlag“ arbeitet."""
    now = datetime.now(timezone.utc)
    fresh_draft = EditPlanDocument(
        generated_at=now,
        project_id="p1",
        folder_name="Badlands National Park",
        confirmed=False,
        shots=[
            EditPlanShot(
                voice_file="v",
                folder="Badlands National Park",
                voice_start_sec=float(i),
                voice_end_sec=float(i + 1),
                duration_sec=1.0,
            )
            for i in range(7)
        ],
    )
    stale_meta = EditPlanFolderMeta(
        folder_name="Badlands National Park",
        confirmed=True,
        shot_count=3,
        generated_at=(now - timedelta(hours=1)).isoformat(),
    )

    status = resolve_location_status_from_meta(
        "Badlands National Park", stale_meta, draft=fresh_draft
    )
    assert status.shot_count == 7
    assert status.state == EditPlanLocationState.DRAFT
