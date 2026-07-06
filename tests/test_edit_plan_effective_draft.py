"""Tests für _effective_draft (Session-Entwurf vs. gespeicherte Datei)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import streamlit as st

from otio_app.analysis_models import EditPlanDocument, EditPlanShot
from otio_app.models import Project
from otio_app.ui.edit_plan import _effective_draft, _set_draft


def _project(tmp_path: Path) -> Project:
    root = tmp_path / "USA"
    root.mkdir()
    return Project(
        id="effective-draft-test",
        name="Test",
        project_root=str(root),
        work_dir=str(root / "_otio"),
        asset_subdir_names=["Badlands National Park"],
        selected_asset_subdirs=["Badlands National Park"],
    )


def _shot(index: int) -> EditPlanShot:
    return EditPlanShot(
        voice_file="v",
        folder="Badlands National Park",
        voice_start_sec=float(index),
        voice_end_sec=float(index + 1),
        duration_sec=1.0,
        asset_id=f"asset_{index}",
    )


def test_effective_draft_prefers_newer_saved_over_stale_session_draft(tmp_path: Path) -> None:
    """Regression: Löst den Fall aus dem Bugreport — ein Auto-Replan von der
    Supplement-Assets-Seite hat einen frischeren Schnittplan direkt
    gespeichert, während im Browser noch ein älterer Entwurf im
    session_state lag. Der Prüfen & Speichern-Tab zeigte dann fälschlich
    'Inventory changed' / 'Beats mit SUPPLEMENT_REQUIRED' basierend auf dem
    veralteten Entwurf."""
    project = _project(tmp_path)
    st.session_state.clear()

    now = datetime.now(timezone.utc)
    stale_draft = EditPlanDocument(
        generated_at=now - timedelta(minutes=5),
        project_id=project.id,
        folder_name="Badlands National Park",
        confirmed=False,
        shots=[_shot(0)],
        inventory_hash_at_plan_time="old-hash",
    )
    _set_draft(stale_draft, "Badlands National Park")

    fresh_saved = EditPlanDocument(
        generated_at=now,
        project_id=project.id,
        folder_name="Badlands National Park",
        confirmed=False,
        shots=[_shot(0), _shot(1), _shot(2)],
        inventory_hash_at_plan_time="new-hash",
    )

    effective = _effective_draft(project.id, "Badlands National Park", fresh_saved)
    assert effective is not None
    assert len(effective.shots) == 3
    assert effective.inventory_hash_at_plan_time == "new-hash"


def test_effective_draft_keeps_newer_session_draft_over_older_saved(tmp_path: Path) -> None:
    """Ein Entwurf, an dem der Nutzer GERADE arbeitet (neuer als die zuletzt
    gespeicherte Datei), darf nicht durch eine ältere gespeicherte Version
    ersetzt werden."""
    project = _project(tmp_path)
    st.session_state.clear()

    now = datetime.now(timezone.utc)
    fresh_draft = EditPlanDocument(
        generated_at=now,
        project_id=project.id,
        folder_name="Badlands National Park",
        confirmed=False,
        shots=[_shot(0), _shot(1)],
        inventory_hash_at_plan_time="new-hash",
    )
    _set_draft(fresh_draft, "Badlands National Park")

    older_saved = EditPlanDocument(
        generated_at=now - timedelta(hours=1),
        project_id=project.id,
        folder_name="Badlands National Park",
        confirmed=True,
        shots=[_shot(0)],
        inventory_hash_at_plan_time="old-hash",
    )

    effective = _effective_draft(project.id, "Badlands National Park", older_saved)
    assert effective is not None
    assert len(effective.shots) == 2
    assert effective.inventory_hash_at_plan_time == "new-hash"


def test_effective_draft_returns_saved_when_no_session_draft(tmp_path: Path) -> None:
    project = _project(tmp_path)
    st.session_state.clear()
    saved = EditPlanDocument(
        project_id=project.id,
        folder_name="Badlands National Park",
        confirmed=True,
        shots=[_shot(0)],
    )
    effective = _effective_draft(project.id, "Badlands National Park", saved)
    assert effective is saved


def test_effective_draft_returns_draft_when_no_saved(tmp_path: Path) -> None:
    project = _project(tmp_path)
    st.session_state.clear()
    draft = EditPlanDocument(
        project_id=project.id,
        folder_name="Badlands National Park",
        confirmed=False,
        shots=[_shot(0)],
    )
    _set_draft(draft, "Badlands National Park")
    effective = _effective_draft(project.id, "Badlands National Park", None)
    assert effective is not None
    assert len(effective.shots) == 1
