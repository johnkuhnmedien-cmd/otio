"""Vertrag des Inventar-Eingangstors für beschaffte Assets."""

from __future__ import annotations

from pathlib import Path

import pytest

from otio_app.analysis_models import AssetFolderAnalysis, AssetMediaAnalysis
from otio_app.defaults import DEFAULT_ENHANCED_WORK_SUBDIR
from otio_app.models import Project, ProjectMode
from otio_app.project_layout import get_folder_inventory_path
from otio_app.services.gemini_client import MediaFrameAnalysis
from otio_app.services.inventory_loader import (
    folder_is_green,
    load_folder_inventory,
    save_folder_inventory,
)
from otio_app.services.media_inventory_cache import (
    CACHE_SCOPE_SUPPLEMENT,
    media_cache_path,
    save_cached_media,
    scan_folder_supplement_cache_assets,
)
from otio_app.services.supplement_inventory import (
    SupplementProvenance,
    ingest_supplement_asset,
    list_supplement_assets,
    upsert_supplement_into_inventory,
)

FOLDER = "Cliffs of Moher"


def _project(tmp_path: Path) -> Project:
    root = tmp_path / "Irland"
    work = root / DEFAULT_ENHANCED_WORK_SUBDIR
    (root / FOLDER).mkdir(parents=True, exist_ok=True)
    work.mkdir(parents=True, exist_ok=True)
    return Project(
        id="gate",
        name="Irland",
        project_root=str(root),
        work_dir=str(work),
        mode=ProjectMode.WITHOUT_VOICEOVER_ENHANCED,
        asset_subdir_names=[FOLDER],
        selected_asset_subdirs=[FOLDER],
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
        AssetFolderAnalysis(
            folder=FOLDER, media_files=[str(media)], assets=[asset]
        ),
    )
    return media


def _supplement_file(project: Project, name: str) -> Path:
    target = project.work_dir_path / "clean" / FOLDER / name
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(b"\x00" * 2048)
    return target


@pytest.fixture
def analysis_stub(monkeypatch: pytest.MonkeyPatch):
    def fake_extract(media_path: Path, output_dir: Path, count: int, *, should_cancel=None):
        output_dir.mkdir(parents=True, exist_ok=True)
        frame = output_dir / "frame_001.jpg"
        frame.write_bytes(b"jpeg")
        return [frame]

    def fake_analyze(media_name, folder_name, frame_paths, language, *, model=None):
        return MediaFrameAnalysis.successful(description=f"Analyse {media_name}")

    monkeypatch.setattr("otio_app.services.asset_analyzer.extract_frames", fake_extract)
    monkeypatch.setattr(
        "otio_app.services.asset_analyzer.analyze_media_from_frames", fake_analyze
    )


def _provenance(asset_id: str, **overrides) -> SupplementProvenance:
    base = {
        "asset_id": asset_id,
        "asset_origin": "pexels",
        "provider": "pexels",
        "provider_asset_id": asset_id.rsplit("_", 1)[-1],
        "intake_note": "Beschaffungsbegründung",
    }
    base.update(overrides)
    return SupplementProvenance(**base)


def test_ingest_writes_supplement_cache_and_inventory(tmp_path, analysis_stub):
    project = _project(tmp_path)
    _green_folder(project)
    media = _supplement_file(project, "pexels_111.mp4")

    result = ingest_supplement_asset(
        project,
        folder_name=FOLDER,
        media_path=media,
        provenance=_provenance("pexels_video_111"),
    )

    assert result.status == "analyzed"
    assert result.has_full_analysis
    cached = scan_folder_supplement_cache_assets(project, FOLDER)
    assert [Path(entry.path).name for entry in cached] == ["pexels_111.mp4"]
    paths = [asset.path for asset in load_folder_inventory(project, FOLDER).assets]
    assert str(media) in paths


def test_ingest_keeps_folder_green(tmp_path, analysis_stub):
    """Beschaffte Assets dürfen den Ordnerstatus nicht kippen."""
    project = _project(tmp_path)
    _green_folder(project)
    assert folder_is_green(project, FOLDER)

    ingest_supplement_asset(
        project,
        folder_name=FOLDER,
        media_path=_supplement_file(project, "pexels_222.mp4"),
        provenance=_provenance("pexels_video_222"),
    )

    assert folder_is_green(project, FOLDER)


def test_ingest_without_api_stays_open_and_keeps_note(tmp_path, monkeypatch):
    project = _project(tmp_path)
    _green_folder(project)
    media = _supplement_file(project, "pexels_333.mp4")

    result = ingest_supplement_asset(
        project,
        folder_name=FOLDER,
        media_path=media,
        provenance=_provenance("pexels_video_333", fallback_description="Titel"),
        use_api=False,
    )

    assert not result.has_full_analysis
    assert result.asset.supplement_intake_note == "Beschaffungsbegründung"
    open_paths = [
        status.media_path
        for status in list_supplement_assets(project, FOLDER)
        if status.needs_analysis
    ]
    assert open_paths == [media]


def test_ingest_replaces_same_provider_asset(tmp_path, analysis_stub):
    """Dasselbe Stock-Asset darf nicht zweimal im Inventar stehen."""
    project = _project(tmp_path)
    _green_folder(project)

    first = _supplement_file(project, "pexels_444.mp4")
    ingest_supplement_asset(
        project,
        folder_name=FOLDER,
        media_path=first,
        provenance=_provenance("pexels_video_444"),
    )
    second = _supplement_file(project, "pexels_444_reencoded.mp4")
    ingest_supplement_asset(
        project,
        folder_name=FOLDER,
        media_path=second,
        provenance=_provenance("supplement_pexels_444", provider_asset_id="444"),
    )

    inventory = load_folder_inventory(project, FOLDER)
    supplement_paths = [
        asset.path for asset in inventory.assets if asset.asset_origin == "pexels"
    ]
    assert supplement_paths == [str(second)]
    assert str(first) not in inventory.media_files


def test_ingest_rejects_missing_file(tmp_path):
    project = _project(tmp_path)
    with pytest.raises(ValueError):
        ingest_supplement_asset(
            project,
            folder_name=FOLDER,
            media_path=project.work_dir_path / "clean" / FOLDER / "nope.mp4",
            provenance=_provenance("pexels_video_999"),
        )


def test_upsert_preserves_originals_when_inventory_file_missing(tmp_path):
    """Ohne kanonische JSON darf der Upsert die Cache-Originale nicht verlieren."""
    project = _project(tmp_path)
    media = project.project_root_path / FOLDER / "orig_clip.mp4"
    media.write_bytes(b"\x00" * 1024)
    save_cached_media(
        media_cache_path(project, FOLDER, media),
        AssetMediaAnalysis(
            path=str(media),
            description="Original",
            asset_id="orig_clip",
            analysis_status="complete",
        ),
    )
    assert not get_folder_inventory_path(project.work_dir_path, FOLDER).is_file()

    supplement = _supplement_file(project, "pexels_555.mp4")
    upsert_supplement_into_inventory(
        project,
        folder_name=FOLDER,
        asset=AssetMediaAnalysis(
            path=str(supplement),
            description="Beschaffung",
            asset_id="pexels_video_555",
            asset_origin="pexels",
        ),
    )

    asset_ids = {a.asset_id for a in load_folder_inventory(project, FOLDER).assets}
    assert asset_ids == {"orig_clip", "pexels_video_555"}


def test_failed_analysis_reason_is_visible(tmp_path, monkeypatch):
    """Ein Fehlschlag muss lesbar sein, nicht nur „missing_signature"."""
    project = _project(tmp_path)
    _green_folder(project)
    media = _supplement_file(project, "kaputt.mp4")

    def boom(*_args, **_kwargs):
        raise RuntimeError("Frames konnten nicht extrahiert werden")

    monkeypatch.setattr("otio_app.services.asset_analyzer.extract_frames", boom)

    result = ingest_supplement_asset(
        project,
        folder_name=FOLDER,
        media_path=media,
        provenance=_provenance("pexels_video_888", fallback_description="Titel"),
    )

    assert not result.has_full_analysis
    status = next(
        item
        for item in list_supplement_assets(project, FOLDER)
        if item.media_path == media
    )
    assert status.needs_analysis
    assert "Frames konnten nicht extrahiert werden" in status.reason
    # Die Inventarzeile bleibt nutzbar — der Fehler steht nur im Cache.
    row = next(
        asset
        for asset in load_folder_inventory(project, FOLDER).assets
        if asset.path == str(media)
    )
    assert not row.error


def test_purge_removes_original_wrongly_kept_as_supplement(tmp_path):
    """Reparatur einer Altlast: Original war als beschafftes Asset geführt."""
    from otio_app.services.supplement_inventory import (
        purge_supplement_rows_for_own_material,
    )

    project = _project(tmp_path)
    original = _green_folder(project)

    # Zustand nachbauen, den eine frühere Programmversion erzeugt hat.
    inventory = load_folder_inventory(project, FOLDER)
    broken = inventory.assets[0].model_copy(
        update={"asset_origin": "generic_fallback", "analysis_signature": None}
    )
    save_folder_inventory(
        get_folder_inventory_path(project.work_dir_path, FOLDER),
        inventory.model_copy(update={"assets": [broken]}),
    )
    stale_cache = media_cache_path(
        project, FOLDER, original, scope=CACHE_SCOPE_SUPPLEMENT
    )
    save_cached_media(stale_cache, broken)

    assert list_supplement_assets(project, FOLDER) == []

    removed = purge_supplement_rows_for_own_material(project, FOLDER)

    assert removed == [str(original)]
    assert not stale_cache.is_file()
    assert load_folder_inventory(project, FOLDER).assets[0].asset_id == "orig_clip"


def test_reanalyzing_legacy_originals_keeps_supplements_untouched(tmp_path, monkeypatch):
    """Originale neu analysieren darf beschaffte Assets nicht kosten oder verlieren.

    Ausgangslage wie im echten Projekt: die Originale tragen eine Legacy-Analyse
    ohne Signatur, das beschaffte Asset ist schon vollständig v3. Ein
    Analyselauf muss die Originale nachziehen und das Supplement unverändert
    stehen lassen — ohne erneuten Gemini-Aufruf dafür.
    """
    from otio_app.services.asset_analyzer import analyze_asset_folders

    project = _project(tmp_path)

    # Original mit Legacy-Analyse: Beschreibung vorhanden, aber keine Signatur.
    original = project.project_root_path / FOLDER / "orig_clip.mp4"
    original.write_bytes(b"\x00" * 1024)
    legacy = AssetMediaAnalysis(
        path=str(original),
        description="Alte Beschreibung ohne Signatur",
        asset_id="orig_clip",
        analysis_status="complete",
    )
    save_cached_media(media_cache_path(project, FOLDER, original), legacy)
    save_folder_inventory(
        get_folder_inventory_path(project.work_dir_path, FOLDER),
        AssetFolderAnalysis(
            folder=FOLDER, media_files=[str(original)], assets=[legacy]
        ),
    )

    analysed_names: list[str] = []

    def fake_extract(media_path: Path, output_dir: Path, count: int, *, should_cancel=None):
        output_dir.mkdir(parents=True, exist_ok=True)
        frame = output_dir / "frame_001.jpg"
        frame.write_bytes(b"jpeg")
        return [frame]

    def fake_analyze(media_name, folder_name, frame_paths, language, *, model=None):
        analysed_names.append(media_name)
        return MediaFrameAnalysis.successful(description=f"Neu: {media_name}")

    monkeypatch.setattr("otio_app.services.asset_analyzer.extract_frames", fake_extract)
    monkeypatch.setattr(
        "otio_app.services.asset_analyzer.analyze_media_from_frames", fake_analyze
    )

    supplement = _supplement_file(project, "pexels_777.mp4")
    ingest_supplement_asset(
        project,
        folder_name=FOLDER,
        media_path=supplement,
        provenance=_provenance("pexels_video_777"),
    )
    analysed_names.clear()

    supplement_before = next(
        asset
        for asset in load_folder_inventory(project, FOLDER).assets
        if asset.path == str(supplement)
    )
    assert supplement_before.analysis_signature is not None

    analyze_asset_folders(project, [FOLDER], use_api=True)

    # Nur das Original wurde erneut an Gemini geschickt.
    assert analysed_names == ["orig_clip.mp4"]

    rows = {a.path: a for a in load_folder_inventory(project, FOLDER).assets}
    assert set(rows) == {str(original), str(supplement)}
    assert rows[str(original)].description == "Neu: orig_clip.mp4"
    assert rows[str(original)].analysis_signature is not None

    kept = rows[str(supplement)]
    assert kept.asset_origin == "pexels"
    assert kept.analysis_signature == supplement_before.analysis_signature
    assert kept.content_tags == supplement_before.content_tags
    assert kept.supplement_intake_note == supplement_before.supplement_intake_note


def test_supplement_cache_is_separate_from_primary_cache(tmp_path, analysis_stub):
    project = _project(tmp_path)
    _green_folder(project)
    media = _supplement_file(project, "pexels_666.mp4")

    ingest_supplement_asset(
        project,
        folder_name=FOLDER,
        media_path=media,
        provenance=_provenance("pexels_video_666"),
    )

    supplement_cache = media_cache_path(
        project, FOLDER, media, scope=CACHE_SCOPE_SUPPLEMENT
    )
    assert supplement_cache.is_file()
    assert supplement_cache.parent.name == "_supplements"
    primary_dir = supplement_cache.parent.parent
    assert sorted(p.name for p in primary_dir.glob("*.json")) == ["orig_clip.mp4.json"]
