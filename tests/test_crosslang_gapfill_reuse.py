"""Charakterisierung: Wiederverwendung gefüllter Coverage Gaps über Sprachen.

Szenario: ein Medienordner, zwei Projekte (DE + EN). Im DE-Projekt wurden
Coverage Gaps über den Supplement-Funnel gefüllt. Diese Tests halten fest, was
das EN-Projekt von diesen Assets tatsächlich sieht — und wo der Stand heute
nicht nachhaltig ist.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from otio_app.analysis_models import AssetFolderAnalysis, AssetMediaAnalysis
from otio_app.defaults import DEFAULT_ENHANCED_WORK_SUBDIR
from otio_app.models import Project, ProjectMode
from otio_app.project_layout import get_folder_inventory_path
from otio_app.services.inventory_loader import (
    load_folder_inventory,
    save_folder_inventory,
    sync_folder_inventory_with_status,
)
from otio_app.services.inventory_prompt_view import slim_inventory_path_for
from otio_app.services.media_inventory_cache import media_cache_path, save_cached_media
from otio_app.services.without_voiceover_enhanced.models import StockCandidate
from otio_app.services.without_voiceover_enhanced.paths import accepted_supplements_path
from otio_app.services.without_voiceover_enhanced.supplement_resolve_service import (
    _import_into_inventory,
)

FOLDER = "Cliffs of Moher"
SUPPLEMENT_ASSET_ID = "pexels_video_27608379"


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


def _fill_gap_like_the_funnel(project: Project) -> Path:
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
    _import_into_inventory(
        project,
        folder_name=FOLDER,
        candidate=candidate,
        media_path=media,
        frames=[],
        # Der Funnel übergibt hier ``record.reason`` — die Ranking-Begründung
        # in Projektsprache, keine Asset-Beschreibung.
        description="Zeigt die geforderte Küstenlinie aus der Luft; passt zur Passage.",
        validation_status="PASS",
        validation_score=0.87,
    )
    return media


def test_shared_inventory_exposes_gapfill_to_sibling_language(tmp_path):
    """Medien + Inventar sind sprachübergreifend geteilt — das funktioniert."""
    de = _project(tmp_path, "de")
    _seed_analyzed_original(de)
    supplement = _fill_gap_like_the_funnel(de)

    en = _project(tmp_path, "en")
    assert en.work_dir_path == de.work_dir_path

    paths = [asset.path for asset in load_folder_inventory(en, FOLDER).assets]
    assert str(supplement) in paths


def test_acceptance_ledger_is_language_scoped(tmp_path):
    """Die Acceptance-Liste ist pro Sprache — EN erbt keine DE-Freigaben."""
    de = _project(tmp_path, "de")
    en = _project(tmp_path, "en")
    assert accepted_supplements_path(de) != accepted_supplements_path(en)


def test_gapfill_row_lacks_v3_analysis_parameters(tmp_path):
    """Gap-Fills landen ohne die Parameter der regulären Analyse im Inventar."""
    de = _project(tmp_path, "de")
    _seed_analyzed_original(de)
    _fill_gap_like_the_funnel(de)

    by_id = {a.asset_id: a for a in load_folder_inventory(de, FOLDER).assets}
    original = by_id["orig_clip"]
    supplement = by_id[SUPPLEMENT_ASSET_ID]

    for field in (
        "duration_seconds",
        "usable_in_s",
        "content_tags",
        "motion",
        "framing",
        "caption",
        "analysis_schema_version",
    ):
        assert getattr(original, field), f"Setup: {field} fehlt am Original"
        assert not getattr(supplement, field), (
            f"{field} ist am Gap-Fill unerwartet gesetzt"
        )


def test_gapfill_reaches_llm_slim_payload_without_selection_signals(tmp_path):
    """Im Slim-Dokument (LLM-Sicht) fehlen dem Gap-Fill die Auswahlsignale."""
    de = _project(tmp_path, "de")
    _seed_analyzed_original(de)
    _fill_gap_like_the_funnel(de)

    inv_path = get_folder_inventory_path(de.work_dir_path, FOLDER)
    slim = json.loads(slim_inventory_path_for(inv_path).read_text(encoding="utf-8"))
    rows = {entry["id"]: entry for entry in slim["assets"]}

    assert set(rows) == {"orig_clip", SUPPLEMENT_ASSET_ID}
    assert set(rows[SUPPLEMENT_ASSET_ID]) == {"id", "file", "type", "caption"}
    for key in ("duration_s", "tags", "motion", "framing", "usable_in_s"):
        assert key in rows["orig_clip"]


@pytest.mark.xfail(
    reason=(
        "is_external_inventory_media_path() kennt nur `_supplemental/` und "
        "`cut_plan/supplement_assets/`. Enhanced-Gap-Fills liegen unter "
        "`_otio_enhanced/clean/<Ordner>/` und werden beim Rebuild aus dem "
        "Cache verworfen."
    ),
    strict=True,
)
def test_folder_sync_preserves_gapfill_rows(tmp_path):
    """Ein Ordner-Sync darf gefüllte Gaps nicht aus dem Inventar entfernen."""
    de = _project(tmp_path, "de")
    _seed_analyzed_original(de)
    supplement = _fill_gap_like_the_funnel(de)

    sync_folder_inventory_with_status(de, FOLDER)

    paths = [asset.path for asset in load_folder_inventory(de, FOLDER).assets]
    assert str(supplement) in paths
