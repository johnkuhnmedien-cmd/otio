"""Transiente API-Fehler dürfen keinen Ordner entwerten.

Aus einem echten Lauf: 39 von 40 Assets analysiert, eines scheiterte an
``503 UNAVAILABLE. Deadline expired before operation could complete.`` Damit war
der Ordner nicht mehr vollständig und ``sync_folder_inventory_with_status`` hat
seine Inventar-JSON entfernt — samt der Zeilen beschaffter Assets.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from google.genai import errors

from otio_app.analysis_models import AssetFolderAnalysis, AssetMediaAnalysis
from otio_app.defaults import DEFAULT_ENHANCED_WORK_SUBDIR
from otio_app.models import Project, ProjectMode
from otio_app.project_layout import get_folder_inventory_path
from otio_app.services.asset_analyzer import analyze_asset_folders
from otio_app.services.gemini_client import (
    MediaFrameAnalysis,
    is_transient_api_error,
)
from otio_app.services.inventory_loader import (
    load_folder_inventory,
    save_folder_inventory,
)
from otio_app.services.media_inventory_cache import media_cache_path, save_cached_media
from otio_app.services.supplement_inventory import (
    SupplementProvenance,
    ingest_supplement_asset,
)

FOLDER = "Athens"


def _server_error() -> errors.ServerError:
    return errors.ServerError(
        503,
        {
            "error": {
                "code": 503,
                "message": "Deadline expired before operation could complete.",
                "status": "UNAVAILABLE",
            }
        },
    )


def _project(tmp_path: Path) -> Project:
    root = tmp_path / "Griechenland"
    work = root / DEFAULT_ENHANCED_WORK_SUBDIR
    (root / FOLDER).mkdir(parents=True)
    work.mkdir(parents=True)
    return Project(
        id="retry",
        name="Griechenland",
        project_root=str(root),
        work_dir=str(work),
        language="en",
        mode=ProjectMode.WITHOUT_VOICEOVER_ENHANCED,
        asset_subdir_names=[FOLDER],
        selected_asset_subdirs=[FOLDER],
    )


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr("otio_app.services.asset_analyzer.time.sleep", lambda _s: None)


@pytest.fixture
def frames(monkeypatch: pytest.MonkeyPatch):
    def fake_extract(media_path: Path, output_dir: Path, count: int, *, should_cancel=None):
        output_dir.mkdir(parents=True, exist_ok=True)
        frame = output_dir / "frame_001.jpg"
        frame.write_bytes(b"jpeg")
        return [frame]

    monkeypatch.setattr("otio_app.services.asset_analyzer.extract_frames", fake_extract)


def _legacy_original(project: Project, name: str) -> Path:
    media = project.project_root_path / FOLDER / name
    media.write_bytes(b"\x00" * 1024)
    legacy = AssetMediaAnalysis(
        path=str(media),
        description=f"Alte Beschreibung {name}",
        asset_id=Path(name).stem,
        analysis_status="complete",
    )
    save_cached_media(media_cache_path(project, FOLDER, media), legacy)
    return media


def test_is_transient_api_error_classification():
    assert is_transient_api_error(_server_error())
    assert is_transient_api_error(errors.ClientError(429, {"error": {"code": 429}}))
    assert is_transient_api_error(TimeoutError("timed out"))
    assert not is_transient_api_error(
        errors.ClientError(400, {"error": {"code": 400, "status": "INVALID_ARGUMENT"}})
    )
    assert not is_transient_api_error(ValueError("kaputtes JSON"))


def test_transient_error_is_retried_and_folder_stays_complete(tmp_path, frames, monkeypatch):
    project = _project(tmp_path)
    media = _legacy_original(project, "Athens_Asset00010.mov")
    save_folder_inventory(
        get_folder_inventory_path(project.work_dir_path, FOLDER),
        AssetFolderAnalysis(folder=FOLDER, media_files=[str(media)]),
    )

    calls = {"n": 0}

    def flaky(media_name, folder_name, frame_paths, language, *, model=None):
        calls["n"] += 1
        if calls["n"] < 3:
            raise _server_error()
        return MediaFrameAnalysis.successful(description=f"Neu: {media_name}")

    monkeypatch.setattr(
        "otio_app.services.asset_analyzer.analyze_media_from_frames", flaky
    )

    _document, report = analyze_asset_folders(project, [FOLDER], use_api=True)

    assert calls["n"] == 3
    assert report.media_failed == 0
    assert report.media_analyzed == 1
    assert get_folder_inventory_path(project.work_dir_path, FOLDER).is_file()
    assert load_folder_inventory(project, FOLDER).assets[0].description == (
        "Neu: Athens_Asset00010.mov"
    )


def test_permanent_error_is_not_retried(tmp_path, frames, monkeypatch):
    project = _project(tmp_path)
    _legacy_original(project, "Athens_Asset00011.mov")
    calls = {"n": 0}

    def broken(media_name, folder_name, frame_paths, language, *, model=None):
        calls["n"] += 1
        raise errors.ClientError(400, {"error": {"code": 400, "status": "INVALID_ARGUMENT"}})

    monkeypatch.setattr(
        "otio_app.services.asset_analyzer.analyze_media_from_frames", broken
    )

    _document, report = analyze_asset_folders(project, [FOLDER], use_api=True)

    assert calls["n"] == 1
    assert report.media_failed == 1


def test_supplements_return_after_a_failed_folder_run(tmp_path, frames, monkeypatch):
    """Kernzusage: ein entwertetes Inventar verliert keine beschafften Assets.

    Der Supplement-Cache ist der haltbare Speicher — nach einem erfolgreichen
    zweiten Lauf steht die Zeile wieder im Inventar, ohne neuen LLM-Aufruf.
    """
    project = _project(tmp_path)
    # Fortlaufende Nummern: eine Luecke wuerde die Phantom-Erkennung fuer
    # fehlende Ordnerdateien ausloesen (discover_folder_media_paths).
    good = _legacy_original(project, "Athens_Asset00001.mov")
    bad = _legacy_original(project, "Athens_Asset00002.mov")

    monkeypatch.setattr(
        "otio_app.services.asset_analyzer.analyze_media_from_frames",
        lambda media_name, folder_name, frame_paths, language, *, model=None: (
            MediaFrameAnalysis.successful(description=f"Neu: {media_name}")
        ),
    )
    supplement = project.work_dir_path / "clean" / FOLDER / "manual_athens_slot_007.mov"
    supplement.parent.mkdir(parents=True, exist_ok=True)
    supplement.write_bytes(b"\x00" * 2048)
    ingest_supplement_asset(
        project,
        folder_name=FOLDER,
        media_path=supplement,
        provenance=SupplementProvenance(
            asset_id="manual_athens_slot_007",
            asset_origin="manual",
            intake_note="Beschaffung",
        ),
    )
    assert str(supplement) in [
        a.path for a in load_folder_inventory(project, FOLDER).assets
    ]

    # Lauf mit einem dauerhaft scheiternden Asset → Ordner nicht vollständig.
    def one_broken(media_name, folder_name, frame_paths, language, *, model=None):
        if media_name == bad.name:
            raise errors.ClientError(
                400, {"error": {"code": 400, "status": "INVALID_ARGUMENT"}}
            )
        return MediaFrameAnalysis.successful(description=f"Neu: {media_name}")

    monkeypatch.setattr(
        "otio_app.services.asset_analyzer.analyze_media_from_frames", one_broken
    )
    analyze_asset_folders(project, [FOLDER], use_api=True)

    inventory_path = get_folder_inventory_path(project.work_dir_path, FOLDER)
    assert inventory_path.is_file()
    rows_partial = {a.path: a for a in load_folder_inventory(project, FOLDER).assets}
    assert str(good) in rows_partial
    assert str(supplement) in rows_partial
    assert str(bad) not in rows_partial
    assert supplement.is_file(), "die Mediendatei bleibt in jedem Fall liegen"

    # Zweiter Lauf, diesmal ohne Fehler.
    monkeypatch.setattr(
        "otio_app.services.asset_analyzer.analyze_media_from_frames",
        lambda media_name, folder_name, frame_paths, language, *, model=None: (
            MediaFrameAnalysis.successful(description=f"Neu: {media_name}")
        ),
    )
    analyze_asset_folders(project, [FOLDER], use_api=True)

    rows = {a.path: a for a in load_folder_inventory(project, FOLDER).assets}
    assert set(rows) == {str(good), str(bad), str(supplement)}
    restored = rows[str(supplement)]
    assert restored.asset_origin == "manual"
    assert restored.supplement_intake_note == "Beschaffung"
    assert restored.analysis_signature is not None
