"""Beschaffte Assets sind sprachübergreifend nutzbare Inventar-Bürger.

Szenario: ein Medienordner, zwei Projekte (DE + EN). Im DE-Projekt werden
Coverage Gaps gefüllt — über den Supplement-Funnel in der App und über die
Coverage-Gap-Inbox einer externen Recherche. Das EN-Projekt muss diese Assets
mit denselben Parametern sehen wie jedes selbst gedrehte Original.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from otio_app.analysis_models import AssetFolderAnalysis, AssetMediaAnalysis
from otio_app.defaults import DEFAULT_ENHANCED_WORK_SUBDIR
from otio_app.models import Project, ProjectMode
from otio_app.project_layout import get_folder_inventory_path
from otio_app.services.gemini_client import MediaFrameAnalysis
from otio_app.services.inventory_loader import (
    load_folder_inventory,
    save_folder_inventory,
    sync_folder_inventory_with_status,
)
from otio_app.services.inventory_prompt_view import slim_inventory_path_for
from otio_app.services.media_inventory_cache import (
    discover_folder_media_paths,
    media_cache_path,
    save_cached_media,
)
from otio_app.services.supplement_inventory import (
    INTAKE_SOURCE_INBOX,
    list_supplement_assets,
)
from otio_app.services.without_voiceover_enhanced.models import StockCandidate
from otio_app.services.without_voiceover_enhanced.paths import accepted_supplements_path
from otio_app.services.without_voiceover_enhanced.supplement_resolve_service import (
    _import_into_inventory,
)

FOLDER = "Cliffs of Moher"
SUPPLEMENT_ASSET_ID = "pexels_video_27608379"
INTAKE_NOTE = "Zeigt die geforderte Küstenlinie aus der Luft; passt zur Passage."


def _project(tmp_path: Path, language: str) -> Project:
    """Zwei Sprachprojekte auf demselben Medienordner (shared work dir)."""
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
    """Frame-Extraktion und Vision-Analyse durch deterministische Stubs ersetzen."""

    def fake_extract(media_path: Path, output_dir: Path, count: int, *, should_cancel=None):
        output_dir.mkdir(parents=True, exist_ok=True)
        frame = output_dir / "frame_001.jpg"
        frame.write_bytes(b"jpeg")
        return [frame]

    calls: list[str] = []

    def fake_analyze(
        media_name: str,
        folder_name: str,
        frame_paths: list[Path],
        language: str,
        *,
        model: str | None = None,
    ) -> MediaFrameAnalysis:
        calls.append(media_name)
        return MediaFrameAnalysis.successful(
            description=f"Bildbeschreibung für {media_name}",
            caption=f"Kurzfassung {media_name}",
            content_tags=["cliff", "aerial", "ocean"],
            motion="drone",
            framing="wide",
        )

    monkeypatch.setattr("otio_app.services.asset_analyzer.extract_frames", fake_extract)
    monkeypatch.setattr(
        "otio_app.services.asset_analyzer.analyze_media_from_frames", fake_analyze
    )
    monkeypatch.setattr(
        "otio_app.services.without_voiceover_enhanced.supplement_resolve_service."
        "is_gemini_configured",
        lambda: True,
    )
    return calls


def _seed_analyzed_original(project: Project) -> Path:
    """Original-Asset wie nach regulärer Asset-Analyse v3 (inkl. Cache)."""
    media = project.project_root_path / FOLDER / "orig_clip.mp4"
    media.write_bytes(b"\x00" * 2048)
    asset = AssetMediaAnalysis(
        path=str(media),
        description="Klippen bei Sonnenuntergang, Drohnenflug entlang der Kante.",
        caption="Drohnenflug entlang der Klippen",
        asset_id="orig_clip",
        media_type="video",
        duration_seconds=12.0,
        usable_in_s=0.5,
        content_tags=["cliff", "ocean", "sunset"],
        motion="drone",
        framing="wide",
        analysis_status="complete",
        analysis_schema_version="asset-analysis-v3",
    )
    save_cached_media(media_cache_path(project, FOLDER, media), asset)
    save_folder_inventory(
        get_folder_inventory_path(project.work_dir_path, FOLDER),
        AssetFolderAnalysis(
            folder=FOLDER,
            description="",
            media_files=[str(media)],
            assets=[asset],
        ),
    )
    return media


def _fill_gap_like_the_funnel(
    project: Project,
    *,
    intake_source: str | None = None,
) -> Path:
    """Nachbau von ``supplement_funnel_service._persist_export_ready``."""
    clean_dir = project.work_dir_path / "clean" / FOLDER
    clean_dir.mkdir(parents=True, exist_ok=True)
    media = clean_dir / "pexels_27608379_clean.mp4"
    media.write_bytes(b"\x00" * 4096)
    candidate = StockCandidate(
        candidate_id=SUPPLEMENT_ASSET_ID,
        provider="pexels",
        provider_asset_id="27608379",
        title="Aerial view of cliffs",
        media_type="video",
        gap_id="gap_slot_003",
        local_media_path=str(media),
    )
    kwargs = {"intake_source": intake_source} if intake_source else {}
    _import_into_inventory(
        project,
        folder_name=FOLDER,
        candidate=candidate,
        media_path=media,
        frames=[],
        # Der Funnel übergibt hier seine Ranking-Begründung.
        description=INTAKE_NOTE,
        validation_status="PASS",
        validation_score=0.87,
        **kwargs,
    )
    return media


def _supplement_row(project: Project) -> AssetMediaAnalysis:
    by_id = {a.asset_id: a for a in load_folder_inventory(project, FOLDER).assets}
    return by_id[SUPPLEMENT_ASSET_ID]


def test_shared_inventory_exposes_gapfill_to_sibling_language(tmp_path, analysis_stub):
    de = _project(tmp_path, "de")
    _seed_analyzed_original(de)
    supplement = _fill_gap_like_the_funnel(de)

    en = _project(tmp_path, "en")
    assert en.work_dir_path == de.work_dir_path

    paths = [asset.path for asset in load_folder_inventory(en, FOLDER).assets]
    assert str(supplement) in paths


def test_acceptance_ledger_is_language_scoped(tmp_path):
    """Die Freigabe pro Gap bleibt sprachgebunden — das Asset selbst nicht."""
    de = _project(tmp_path, "de")
    en = _project(tmp_path, "en")
    assert accepted_supplements_path(de) != accepted_supplements_path(en)


def test_gapfill_carries_same_analysis_parameters_as_original(tmp_path, analysis_stub):
    """Beschafftes Material bekommt dieselbe Analyse wie ein Original."""
    de = _project(tmp_path, "de")
    _seed_analyzed_original(de)
    _fill_gap_like_the_funnel(de)

    supplement = _supplement_row(de)
    assert supplement.analysis_schema_version == "asset-analysis-v3"
    assert supplement.analysis_signature is not None
    assert supplement.analysis_parse_ok is True
    assert supplement.caption
    assert supplement.content_tags == ["cliff", "aerial", "ocean"]
    assert supplement.motion_profile is not None
    assert supplement.framing_profile is not None
    assert supplement.quality_profile is not None


def test_ranking_reason_does_not_become_the_description(tmp_path, analysis_stub):
    """Die Beschaffungsbegründung ist Metadatum, nicht Bildbeschreibung."""
    de = _project(tmp_path, "de")
    _seed_analyzed_original(de)
    _fill_gap_like_the_funnel(de)

    supplement = _supplement_row(de)
    assert supplement.description.startswith("Bildbeschreibung für")
    assert supplement.supplement_intake_note == INTAKE_NOTE
    assert supplement.supplement_intake_source == "funnel"


def test_provenance_survives_the_analysis(tmp_path, analysis_stub):
    de = _project(tmp_path, "de")
    _seed_analyzed_original(de)
    _fill_gap_like_the_funnel(de)

    supplement = _supplement_row(de)
    assert supplement.asset_origin == "pexels"
    assert supplement.provider == "pexels"
    assert supplement.approved_for_cut_plan is True
    assert supplement.license_metadata["provider_asset_id"] == "27608379"


def test_gapfill_reaches_llm_slim_payload_with_selection_signals(tmp_path, analysis_stub):
    """Im Slim-Dokument steht das Gap-Asset gleichwertig neben dem Original."""
    de = _project(tmp_path, "de")
    _seed_analyzed_original(de)
    _fill_gap_like_the_funnel(de)

    inv_path = get_folder_inventory_path(de.work_dir_path, FOLDER)
    slim = json.loads(slim_inventory_path_for(inv_path).read_text(encoding="utf-8"))
    rows = {entry["id"]: entry for entry in slim["assets"]}

    assert set(rows) == {"orig_clip", SUPPLEMENT_ASSET_ID}
    supplement_row = rows[SUPPLEMENT_ASSET_ID]
    for key in ("caption", "tags", "motion", "framing", "quality"):
        assert key in supplement_row, f"{key} fehlt in der LLM-Sicht"


def test_folder_sync_preserves_gapfill_rows(tmp_path, analysis_stub):
    """Ein Ordner-Sync darf gefüllte Gaps nicht aus dem Inventar entfernen."""
    de = _project(tmp_path, "de")
    _seed_analyzed_original(de)
    supplement = _fill_gap_like_the_funnel(de)

    sync_folder_inventory_with_status(de, FOLDER)

    paths = [asset.path for asset in load_folder_inventory(de, FOLDER).assets]
    assert str(supplement) in paths


def test_gapfill_row_returns_after_inventory_loss(tmp_path, analysis_stub):
    """Der Supplement-Cache holt eine verlorene Inventarzeile zurück."""
    de = _project(tmp_path, "de")
    _seed_analyzed_original(de)
    supplement = _fill_gap_like_the_funnel(de)

    inventory = load_folder_inventory(de, FOLDER)
    without_supplement = inventory.model_copy(
        update={
            "assets": [a for a in inventory.assets if a.path != str(supplement)],
            "media_files": [p for p in inventory.media_files if p != str(supplement)],
        }
    )
    save_folder_inventory(
        get_folder_inventory_path(de.work_dir_path, FOLDER), without_supplement
    )

    sync_folder_inventory_with_status(de, FOLDER)

    paths = [asset.path for asset in load_folder_inventory(de, FOLDER).assets]
    assert str(supplement) in paths


def test_supplement_never_becomes_a_phantom_original(tmp_path, analysis_stub):
    """Analyse-Cache und Frames dürfen keine fehlende Originaldatei vortäuschen."""
    de = _project(tmp_path, "de")
    original = _seed_analyzed_original(de)
    _fill_gap_like_the_funnel(de)

    discovered = discover_folder_media_paths(de, FOLDER)
    assert discovered == [original]


def test_inbox_asset_is_ingested_like_a_funnel_asset(tmp_path, analysis_stub):
    """Externe Recherche über die Inbox nutzt dasselbe Eingangstor."""
    de = _project(tmp_path, "de")
    _seed_analyzed_original(de)
    _fill_gap_like_the_funnel(de, intake_source=INTAKE_SOURCE_INBOX)

    supplement = _supplement_row(de)
    assert supplement.supplement_intake_source == INTAKE_SOURCE_INBOX
    assert supplement.analysis_schema_version == "asset-analysis-v3"


def test_unanalyzed_supplement_is_detected_as_open(tmp_path, analysis_stub):
    """Ohne API-Schlüssel importiertes Material bleibt sichtbar offen."""
    de = _project(tmp_path, "de")
    _seed_analyzed_original(de)

    import otio_app.services.without_voiceover_enhanced.supplement_resolve_service as srs

    original_flag = srs.is_gemini_configured
    srs.is_gemini_configured = lambda: False
    try:
        _fill_gap_like_the_funnel(de)
    finally:
        srs.is_gemini_configured = original_flag

    statuses = list_supplement_assets(de, FOLDER)
    assert [status.needs_analysis for status in statuses] == [True]
    assert statuses[0].cache_status.status in {"legacy", "stale", "failed"}


def test_second_project_analyses_open_supplement_in_normal_run(tmp_path, analysis_stub):
    """Das zweite Sprachprojekt holt die Analyse im Analysen-Tab nach."""
    from otio_app.services.asset_analyzer import analyze_asset_folders

    de = _project(tmp_path, "de")
    _seed_analyzed_original(de)

    import otio_app.services.without_voiceover_enhanced.supplement_resolve_service as srs

    original_flag = srs.is_gemini_configured
    srs.is_gemini_configured = lambda: False
    try:
        _fill_gap_like_the_funnel(de)
    finally:
        srs.is_gemini_configured = original_flag

    en = _project(tmp_path, "en")
    assert [s.needs_analysis for s in list_supplement_assets(en, FOLDER)] == [True]

    _document, report = analyze_asset_folders(en, [FOLDER], use_api=True)

    assert report.media_failed == 0
    assert [s.needs_analysis for s in list_supplement_assets(en, FOLDER)] == [False]

    supplement = _supplement_row(en)
    assert supplement.analysis_schema_version == "asset-analysis-v3"
    assert supplement.content_tags == ["cliff", "aerial", "ocean"]
    # Herkunft überlebt die nachgeholte Analyse.
    assert supplement.asset_origin == "pexels"
    assert supplement.supplement_intake_note == INTAKE_NOTE
