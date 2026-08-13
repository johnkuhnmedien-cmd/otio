"""Bestandsprojekt: beschaffte Assets nach dem alten Sync-Verlust zurückholen."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from otio_app.analysis_models import (
    AssetFolderAnalysis,
    AssetMediaAnalysis,
    CleanMediaEntry,
    CleanMediaManifest,
)
from otio_app.defaults import DEFAULT_ENHANCED_WORK_SUBDIR, VOICEOVER_GENERATION_SUBDIR
from otio_app.models import Project, ProjectMode
from otio_app.project_layout import (
    get_folder_clean_manifest_path,
    get_folder_inventory_path,
)
from otio_app.services.gemini_client import MediaFrameAnalysis
from otio_app.services.inventory_loader import (
    load_folder_inventory,
    save_folder_inventory,
)
from otio_app.services.media_inventory_cache import media_cache_path, save_cached_media
from otio_app.services.supplement_inventory import list_supplement_assets
from otio_app.services.supplement_recovery import (
    recover_supplements_into_inventory,
    scan_recoverable_supplements,
)

FOLDER = "Cliffs of Moher"


def _project(tmp_path: Path, language: str) -> Project:
    root = tmp_path / "Irland"
    work = root / DEFAULT_ENHANCED_WORK_SUBDIR
    (root / FOLDER).mkdir(parents=True, exist_ok=True)
    work.mkdir(parents=True, exist_ok=True)
    return Project(
        id=f"proj-{language}",
        name="Irland",
        project_root=str(root),
        work_dir=str(work),
        language=language,
        mode=ProjectMode.WITHOUT_VOICEOVER_ENHANCED,
        asset_subdir_names=[FOLDER],
        selected_asset_subdirs=[FOLDER],
    )


@pytest.fixture
def analysis_stub(monkeypatch: pytest.MonkeyPatch):
    def fake_extract(media_path: Path, output_dir: Path, count: int, *, should_cancel=None):
        output_dir.mkdir(parents=True, exist_ok=True)
        frame = output_dir / "frame_001.jpg"
        frame.write_bytes(b"jpeg")
        return [frame]

    def fake_analyze(media_name, folder_name, frame_paths, language, *, model=None):
        return MediaFrameAnalysis.successful(
            description=f"Analyse {media_name}",
            content_tags=["cliff", "aerial"],
        )

    monkeypatch.setattr("otio_app.services.asset_analyzer.extract_frames", fake_extract)
    monkeypatch.setattr(
        "otio_app.services.asset_analyzer.analyze_media_from_frames", fake_analyze
    )


def _green_folder(project: Project) -> Path:
    media = project.project_root_path / FOLDER / "orig_clip.mp4"
    media.write_bytes(b"\x00" * 1024)
    asset = AssetMediaAnalysis(
        path=str(media),
        description="Original",
        asset_id="orig_clip",
        analysis_status="complete",
    )
    save_cached_media(media_cache_path(project, FOLDER, media), asset)
    save_folder_inventory(
        get_folder_inventory_path(project.work_dir_path, FOLDER),
        AssetFolderAnalysis(folder=FOLDER, media_files=[str(media)], assets=[asset]),
    )
    return media


def _clean_file(project: Project, name: str) -> Path:
    target = project.work_dir_path / "clean" / "Cliffs_of_Moher" / name
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(b"\x00" * 2048)
    return target


def _write_accepted_ledger(project: Project, language: str, supplements: list[dict]) -> Path:
    path = (
        project.work_dir_path
        / language
        / VOICEOVER_GENERATION_SUBDIR
        / "stock"
        / "accepted_supplements.json"
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "schema_version": "enhanced-accepted-supplements-v1",
                "script_version": "v1",
                "supplements": supplements,
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return path


def _legacy_de_project_with_lost_rows(tmp_path: Path) -> tuple[Project, Path]:
    """DE-Projekt: Asset beschafft, Inventarzeile durch den alten Sync verloren."""
    de = _project(tmp_path, "de")
    _green_folder(de)
    media = _clean_file(de, "pexels_27608379_clean.mp4")
    _write_accepted_ledger(
        de,
        "DE",
        [
            {
                "candidate_id": "pexels_video_27608379",
                "provider": "pexels",
                "provider_asset_id": "27608379",
                "title": "Aerial view of cliffs",
                "media_type": "video",
                "gap_id": "Cliffs_of_Moher_gap_slot_003",
                "local_media_path": str(media),
                "media_validation_status": "export_ready",
                "license": "Pexels License",
                "attribution": "Jane Doe",
            }
        ],
    )
    return de, media


def test_scan_finds_asset_missing_from_inventory(tmp_path):
    de, media = _legacy_de_project_with_lost_rows(tmp_path)

    items, report = scan_recoverable_supplements(de)

    assert report.scanned == 1
    assert not report.unresolved
    item = items[0]
    assert item.media_path == media
    assert item.folder_name == FOLDER
    assert item.in_inventory is False
    assert item.source == "accepted:DE"


def test_recovery_restores_row_with_full_analysis(tmp_path, analysis_stub):
    de, media = _legacy_de_project_with_lost_rows(tmp_path)

    report = recover_supplements_into_inventory(de)

    assert report.recovered == 1
    assert report.analyzed == 1
    assert report.failed == 0

    by_id = {a.asset_id: a for a in load_folder_inventory(de, FOLDER).assets}
    restored = by_id["pexels_video_27608379"]
    assert restored.path == str(media)
    assert restored.analysis_schema_version == "asset-analysis-v3"
    assert restored.content_tags == ["cliff", "aerial"]
    assert restored.asset_origin == "pexels"
    assert restored.license_metadata["provider_asset_id"] == "27608379"
    assert restored.license_metadata["attribution"] == "Jane Doe"


def test_recovered_asset_is_visible_in_sibling_language(tmp_path, analysis_stub):
    """Genau der Punkt: das EN-Projekt sieht das nachgetragene Asset."""
    de, media = _legacy_de_project_with_lost_rows(tmp_path)
    recover_supplements_into_inventory(de)

    en = _project(tmp_path, "en")
    paths = [asset.path for asset in load_folder_inventory(en, FOLDER).assets]
    assert str(media) in paths
    assert [s.needs_analysis for s in list_supplement_assets(en, FOLDER)] == [False]


def test_recovery_can_run_from_the_english_project(tmp_path, analysis_stub):
    """Die Wiederherstellung liest die Acceptance-Listen aller Sprachen."""
    de, media = _legacy_de_project_with_lost_rows(tmp_path)
    en = _project(tmp_path, "en")

    report = recover_supplements_into_inventory(en)

    assert report.recovered == 1
    paths = [asset.path for asset in load_folder_inventory(de, FOLDER).assets]
    assert str(media) in paths


def test_recovery_is_idempotent(tmp_path, analysis_stub):
    de, _media = _legacy_de_project_with_lost_rows(tmp_path)
    recover_supplements_into_inventory(de)

    second = recover_supplements_into_inventory(de)

    assert second.recovered == 1
    # Zweiter Lauf nutzt den Cache statt eines neuen LLM-Aufrufs.
    assert second.analyzed == 0
    assert second.already_complete == 1
    supplements = [
        a for a in load_folder_inventory(de, FOLDER).assets if a.asset_origin == "pexels"
    ]
    assert len(supplements) == 1


def test_dry_run_changes_nothing(tmp_path, analysis_stub):
    de, media = _legacy_de_project_with_lost_rows(tmp_path)

    report = recover_supplements_into_inventory(de, dry_run=True)

    assert report.scanned == 1
    assert report.recovered == 0
    paths = [asset.path for asset in load_folder_inventory(de, FOLDER).assets]
    assert str(media) not in paths


def test_clean_manifest_recovers_inbox_asset_without_ledger(tmp_path, analysis_stub):
    """Inbox-Material ohne Acceptance-Eintrag kommt übers Clean-Manifest zurück."""
    de = _project(tmp_path, "de")
    original = _green_folder(de)
    inbox_source = de.work_dir_path / "DE" / VOICEOVER_GENERATION_SUBDIR / "coverage"
    inbox_source.mkdir(parents=True, exist_ok=True)
    dropped = inbox_source / "recherche_clip.mp4"
    dropped.write_bytes(b"\x00" * 1024)
    clean = _clean_file(de, "recherche_clip_clean.mp4")

    save_folder_inventory(
        get_folder_inventory_path(de.work_dir_path, FOLDER),
        load_folder_inventory(de, FOLDER),
    )
    manifest = CleanMediaManifest(
        project_id=de.id,
        folder=FOLDER,
        entries=[
            CleanMediaEntry(
                original_path=str(original),
                clean_path=str(_clean_file(de, "orig_clip_clean.mp4")),
                status="clean",
            ),
            CleanMediaEntry(
                original_path=str(dropped),
                clean_path=str(clean),
                status="clean",
            ),
        ],
    )
    manifest_path = get_folder_clean_manifest_path(de.work_dir_path, FOLDER)
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(manifest.model_dump_json(), encoding="utf-8")

    items, _report = scan_recoverable_supplements(de)
    recovered_paths = {str(item.media_path) for item in items}

    # Die Clean-Fassung des Originals ist kein beschafftes Material.
    assert str(clean) in recovered_paths
    assert str(_clean_file(de, "orig_clip_clean.mp4")) not in recovered_paths

    report = recover_supplements_into_inventory(de)
    assert report.recovered == 1
    paths = [asset.path for asset in load_folder_inventory(de, FOLDER).assets]
    assert str(clean) in paths


def test_stock_download_without_clean_copy_is_recovered(tmp_path, analysis_stub):
    de = _project(tmp_path, "de")
    _green_folder(de)
    download = (
        de.work_dir_path
        / "DE"
        / VOICEOVER_GENERATION_SUBDIR
        / "stock"
        / "downloads"
        / "Cliffs_of_Moher_gap_slot_007"
        / "pexels_video_555"
        / "pexels_555.mp4"
    )
    download.parent.mkdir(parents=True, exist_ok=True)
    download.write_bytes(b"\x00" * 1024)

    report = recover_supplements_into_inventory(de)

    assert report.recovered == 1
    paths = [asset.path for asset in load_folder_inventory(de, FOLDER).assets]
    assert str(download) in paths


def _three_recoverable_assets(tmp_path: Path) -> Project:
    de = _project(tmp_path, "de")
    _green_folder(de)
    supplements = []
    for index in (1, 2, 3):
        media = _clean_file(de, f"pexels_{index}.mp4")
        supplements.append(
            {
                "candidate_id": f"pexels_video_{index}",
                "provider": "pexels",
                "provider_asset_id": str(index),
                "media_type": "video",
                "gap_id": f"Cliffs_of_Moher_gap_slot_00{index}",
                "local_media_path": str(media),
                "media_validation_status": "export_ready",
            }
        )
    _write_accepted_ledger(de, "DE", supplements)
    return de


def test_progress_reports_every_asset(tmp_path, analysis_stub):
    de = _three_recoverable_assets(tmp_path)
    events: list[tuple[str, dict]] = []

    report = recover_supplements_into_inventory(
        de, on_progress=lambda event, payload: events.append((event, payload))
    )

    assert report.recovered == 3
    assert [event for event, _payload in events] == [
        "start",
        "item_start",
        "item_done",
        "item_start",
        "item_done",
        "item_start",
        "item_done",
        "complete",
    ]
    start_payload = events[0][1]
    assert start_payload["total"] == 3
    assert events[1][1]["media_name"] == "pexels_1.mp4"
    assert events[1][1]["folder"] == FOLDER


def test_limit_runs_in_batches(tmp_path, analysis_stub):
    de = _three_recoverable_assets(tmp_path)

    first = recover_supplements_into_inventory(de, limit=2)
    assert first.pending == 2
    assert first.recovered == 2

    second = recover_supplements_into_inventory(de, limit=2)
    # Der offene Rest kommt zuerst; fertige Assets kosten nur einen Cache-Treffer.
    assert second.analyzed == 1
    assert second.already_complete == 1

    inventory_ids = {
        asset.asset_id
        for asset in load_folder_inventory(de, FOLDER).assets
        if asset.asset_origin == "pexels"
    }
    assert inventory_ids == {
        "pexels_video_1",
        "pexels_video_2",
        "pexels_video_3",
    }


def test_cancel_keeps_finished_assets(tmp_path, analysis_stub):
    de = _three_recoverable_assets(tmp_path)
    seen: list[str] = []

    def should_cancel() -> bool:
        # Nach dem ersten fertigen Asset abbrechen.
        return len(seen) >= 1

    def on_progress(event: str, payload: dict) -> None:
        if event == "item_done":
            seen.append(str(payload.get("media_name", "")))

    report = recover_supplements_into_inventory(
        de, on_progress=on_progress, should_cancel=should_cancel
    )

    assert report.cancelled is True
    assert report.recovered == 1
    supplements = [
        asset
        for asset in load_folder_inventory(de, FOLDER).assets
        if asset.asset_origin == "pexels"
    ]
    assert len(supplements) == 1
    assert supplements[0].analysis_schema_version == "asset-analysis-v3"


def test_unresolvable_folder_is_reported_not_guessed(tmp_path):
    """Ohne Ordnerbezug wird gemeldet statt falsch einsortiert."""
    de = _project(tmp_path, "de")
    _green_folder(de)
    stray = (
        de.work_dir_path
        / "DE"
        / VOICEOVER_GENERATION_SUBDIR
        / "stock"
        / "downloads"
        / "gap_unknown"
        / "pexels_video_777"
        / "pexels_777.mp4"
    )
    stray.parent.mkdir(parents=True, exist_ok=True)
    stray.write_bytes(b"\x00" * 1024)

    items, report = scan_recoverable_supplements(de)

    assert items == []
    assert len(report.unresolved) == 1
    assert "pexels_777.mp4" in report.unresolved[0]
