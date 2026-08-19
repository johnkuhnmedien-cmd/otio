"""Das Wiederherstellungs-Skript wählt das richtige Projekt und schreibt nichts im Dry-Run."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from otio_app.analysis_models import AssetFolderAnalysis, AssetMediaAnalysis
from otio_app.defaults import DEFAULT_ENHANCED_WORK_SUBDIR, VOICEOVER_GENERATION_SUBDIR
from otio_app.models import ProjectCreate, ProjectMode
from otio_app.project_layout import get_folder_inventory_path
from otio_app.project_repository import create_project
from otio_app.services.inventory_loader import (
    load_folder_inventory,
    save_folder_inventory,
)
from otio_app.services.media_inventory_cache import media_cache_path, save_cached_media
from scripts.recover_supplement_inventory import _select_project, main

FOLDER = "Cliffs of Moher"


@pytest.fixture
def temp_database(tmp_path, monkeypatch: pytest.MonkeyPatch) -> Path:
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    monkeypatch.setattr("otio_app.config.DATA_DIR", data_dir)
    return data_dir / "projects.db"


def _legacy_layout(tmp_path: Path) -> tuple[Path, Path]:
    """Medienordner mit analysiertem Original und beschafftem Asset ohne Zeile."""
    root = tmp_path / "Irland"
    work = root / DEFAULT_ENHANCED_WORK_SUBDIR
    (root / FOLDER).mkdir(parents=True)
    work.mkdir(parents=True)
    original = root / FOLDER / "orig_clip.mp4"
    original.write_bytes(b"\x00" * 1024)

    clean = work / "clean" / "Cliffs_of_Moher" / "pexels_27608379_clean.mp4"
    clean.parent.mkdir(parents=True)
    clean.write_bytes(b"\x00" * 2048)

    ledger = (
        work / "DE" / VOICEOVER_GENERATION_SUBDIR / "stock" / "accepted_supplements.json"
    )
    ledger.parent.mkdir(parents=True)
    ledger.write_text(
        json.dumps(
            {
                "schema_version": "enhanced-accepted-supplements-v1",
                "script_version": "v1",
                "supplements": [
                    {
                        "candidate_id": "pexels_video_27608379",
                        "provider": "pexels",
                        "provider_asset_id": "27608379",
                        "media_type": "video",
                        "gap_id": "Cliffs_of_Moher_gap_slot_003",
                        "local_media_path": str(clean),
                        "media_validation_status": "export_ready",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    return root, clean


def _register(root: Path, language: str):
    project = create_project(
        ProjectCreate(
            name=f"Irland {language.upper()}",
            project_root=str(root),
            work_dir=str(root / DEFAULT_ENHANCED_WORK_SUBDIR),
            project_mode=ProjectMode.WITHOUT_VOICEOVER_ENHANCED,
            language=language,
        ),
        asset_subdir_names=[FOLDER],
        selected_asset_subdirs=[FOLDER],
    )
    original = root / FOLDER / "orig_clip.mp4"
    asset = AssetMediaAnalysis(
        path=str(original),
        description="Original",
        asset_id="orig_clip",
        analysis_status="complete",
    )
    save_cached_media(media_cache_path(project, FOLDER, original), asset)
    save_folder_inventory(
        get_folder_inventory_path(project.work_dir_path, FOLDER),
        AssetFolderAnalysis(folder=FOLDER, media_files=[str(original)], assets=[asset]),
    )
    return project


def test_select_project_prefers_requested_language(tmp_path, temp_database):
    root, _clean = _legacy_layout(tmp_path)
    _register(root, "de")
    _register(root, "en")

    selected = _select_project(str(root), "en")

    assert selected is not None
    assert selected.language == "en"


def test_select_project_reports_unknown_root(tmp_path, temp_database, capsys):
    assert _select_project(str(tmp_path / "gibt-es-nicht"), None) is None
    assert "Kein Projekt" in capsys.readouterr().out


def test_dry_run_lists_missing_assets_without_writing(
    tmp_path,
    temp_database,
    capsys,
    monkeypatch: pytest.MonkeyPatch,
):
    root, clean = _legacy_layout(tmp_path)
    project = _register(root, "de")

    monkeypatch.setattr(
        "sys.argv",
        [
            "recover_supplement_inventory.py",
            "--project-root",
            str(root),
            "--language",
            "de",
            "--dry-run",
        ],
    )
    assert main() == 0

    output = capsys.readouterr().out
    assert "pexels_27608379_clean.mp4" in output
    assert FOLDER in output
    paths = [asset.path for asset in load_folder_inventory(project, FOLDER).assets]
    assert str(clean) not in paths
