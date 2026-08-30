"""Stock-IDs ohne Datei bleiben im Inventar; nur Pfade werden korrigiert."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from otio_app.analysis_models import AssetFolderAnalysis, AssetMediaAnalysis
from otio_app.defaults import DEFAULT_ENHANCED_WORK_SUBDIR, VOICEOVER_GENERATION_SUBDIR
from otio_app.models import Project, ProjectMode
from otio_app.project_layout import get_folder_inventory_path
from otio_app.services.inventory_loader import (
    load_folder_inventory_file,
    materialize_folder_inventory_from_cache,
    prune_unresolvable_supplement_assets,
    save_folder_inventory,
)
from otio_app.services.inventory_prompt_view import (
    load_slim_folder_inventory_file,
    slim_inventory_path_for,
)
from otio_app.services.media_inventory_cache import (
    CACHE_SCOPE_SUPPLEMENT,
    media_cache_path,
    save_cached_media,
)
from otio_app.services.without_voiceover_enhanced.models import (
    CutBoundary,
    CutSlot,
    UnifiedCutPlanDocument,
)
from otio_app.services.without_voiceover_enhanced.unified_cut_plan import (
    demote_slots_with_unknown_local_assets,
)

FOLDER = "Vogel"
GHOST_ID = "openverse_e61610da-6d0c-41f4-bf76-ecb24278f193"


def _project(tmp_path: Path, *, language: str = "IT") -> Project:
    root = tmp_path / "proj"
    work = root / DEFAULT_ENHANCED_WORK_SUBDIR
    (root / FOLDER).mkdir(parents=True)
    work.mkdir(parents=True)
    return Project(
        id="prune-ghost",
        name="prune-ghost",
        project_root=str(root),
        work_dir=str(work),
        language=language,
        project_mode=ProjectMode.WITHOUT_VOICEOVER_ENHANCED,
        asset_subdir_names=[FOLDER],
        selected_asset_subdirs=[FOLDER],
        fps=25.0,
    )


def _original(project: Project) -> AssetMediaAnalysis:
    media = project.project_root_path / FOLDER / "clip.mp4"
    media.write_bytes(b"\x00" * 128)
    asset = AssetMediaAnalysis(
        path=str(media),
        description="Original",
        asset_id="vogel_clip",
        analysis_status="complete",
        asset_origin="local_original",
    )
    save_cached_media(media_cache_path(project, FOLDER, media), asset)
    return asset


def _ghost_asset() -> AssetMediaAnalysis:
    return AssetMediaAnalysis(
        path="/does/not/exist/openverse_e61610da.jpg",
        description="Bird that was never downloaded",
        asset_id=GHOST_ID,
        media_type="photo",
        analysis_status="complete",
        asset_origin="openverse",
        approved_for_cut_plan=True,
    )


def _save_inventory(project: Project, assets: list[AssetMediaAnalysis]) -> Path:
    path = get_folder_inventory_path(project.work_dir_path, FOLDER)
    save_folder_inventory(
        path,
        AssetFolderAnalysis(
            folder=FOLDER,
            media_files=[asset.path for asset in assets],
            assets=assets,
        ),
    )
    return path


def _slim_ids(inv_path: Path) -> set[str]:
    doc = load_slim_folder_inventory_file(slim_inventory_path_for(inv_path))
    ids: set[str] = set()
    for item in (doc or {}).get("assets") or []:
        if isinstance(item, dict) and item.get("id"):
            ids.add(str(item["id"]))
    return ids


def test_prune_keeps_missing_openverse_id_in_inventory_and_slim(tmp_path: Path) -> None:
    project = _project(tmp_path)
    original = _original(project)
    inv_path = _save_inventory(project, [original, _ghost_asset()])
    assert GHOST_ID in _slim_ids(inv_path)

    dropped = prune_unresolvable_supplement_assets(project, FOLDER)

    assert dropped == []
    loaded = load_folder_inventory_file(inv_path)
    assert loaded is not None
    ids = {asset.asset_id for asset in loaded.assets}
    assert ids == {"vogel_clip", GHOST_ID}
    assert GHOST_ID in _slim_ids(inv_path)


def test_prune_keeps_row_when_file_lives_in_sibling_language(tmp_path: Path) -> None:
    from otio_app.services.without_voiceover_enhanced.paths import (
        STOCK_DOWNLOADS_SUBDIR,
        STOCK_SUBDIR,
    )

    project = _project(tmp_path, language="IT")
    original = _original(project)
    cid = "wikimedia_45709027"
    de_file = (
        project.work_dir_path
        / "DE"
        / VOICEOVER_GENERATION_SUBDIR
        / STOCK_SUBDIR
        / STOCK_DOWNLOADS_SUBDIR
        / "Vogel_gap_005"
        / cid
        / f"{cid}.jpg"
    )
    de_file.parent.mkdir(parents=True)
    de_file.write_bytes(b"\x00" * 80)
    missing_it = (
        project.work_dir_path
        / "IT"
        / VOICEOVER_GENERATION_SUBDIR
        / STOCK_SUBDIR
        / STOCK_DOWNLOADS_SUBDIR
        / "Vogel_gap_005"
        / cid
        / f"{cid}.jpg"
    )
    _save_inventory(
        project,
        [
            original,
            AssetMediaAnalysis(
                path=str(missing_it),
                description="stork",
                asset_id=cid,
                media_type="photo",
                analysis_status="complete",
                asset_origin="wikimedia",
            ),
        ],
    )

    dropped = prune_unresolvable_supplement_assets(project, FOLDER)

    assert dropped == []
    loaded = load_folder_inventory_file(
        get_folder_inventory_path(project.work_dir_path, FOLDER)
    )
    assert loaded is not None
    row = next(asset for asset in loaded.assets if asset.asset_id == cid)
    assert Path(row.path).resolve() == de_file.resolve()


def test_prune_rewrites_path_when_clean_mp4_exists(tmp_path: Path) -> None:
    from otio_app.project_layout import get_folder_clean_output_dir

    project = _project(tmp_path)
    original = _original(project)
    cid = GHOST_ID
    missing_jpg = project.work_dir_path / "stock" / "downloads" / "gone" / f"{cid}.jpg"
    clean = get_folder_clean_output_dir(project.work_dir_path, FOLDER) / f"{cid}.mp4"
    clean.parent.mkdir(parents=True)
    clean.write_bytes(b"\x00" * 96)
    _save_inventory(
        project,
        [
            original,
            AssetMediaAnalysis(
                path=str(missing_jpg),
                description="bird",
                asset_id=cid,
                media_type="photo",
                analysis_status="complete",
                asset_origin="openverse",
            ),
        ],
    )

    dropped = prune_unresolvable_supplement_assets(project, FOLDER)

    assert dropped == []
    loaded = load_folder_inventory_file(
        get_folder_inventory_path(project.work_dir_path, FOLDER)
    )
    assert loaded is not None
    row = next(asset for asset in loaded.assets if asset.asset_id == cid)
    assert Path(row.path).resolve() == clean.resolve()


def test_materialize_keeps_existing_inventory_rows_even_if_file_missing(
    tmp_path: Path,
) -> None:
    """Rebuild darf vorhandene Stock-Zeilen nicht still löschen."""
    project = _project(tmp_path)
    original = _original(project)
    _save_inventory(project, [original, _ghost_asset()])

    item, error = materialize_folder_inventory_from_cache(project, FOLDER, allow_partial=True)

    assert error is None
    assert item is not None
    ids = {asset.asset_id for asset in item.assets}
    assert GHOST_ID in ids
    assert "vogel_clip" in ids


def test_materialize_restores_missing_file_from_supplement_cache(
    tmp_path: Path,
) -> None:
    project = _project(tmp_path)
    original = _original(project)
    _save_inventory(project, [original])
    ghost = _ghost_asset()
    save_cached_media(
        media_cache_path(
            project,
            FOLDER,
            Path(ghost.path),
            scope=CACHE_SCOPE_SUPPLEMENT,
        ),
        ghost,
    )

    item, error = materialize_folder_inventory_from_cache(project, FOLDER, allow_partial=True)

    assert error is None
    assert item is not None
    ids = {asset.asset_id for asset in item.assets}
    assert GHOST_ID in ids
    assert "vogel_clip" in ids


def test_demote_slots_with_unknown_local_assets_clears_ghost_slot() -> None:
    plan = UnifiedCutPlanDocument(
        script_version="v1",
        boundaries=[
            CutBoundary(
                cut_id="c0",
                sentence_id="s1",
                position="start",
                alignment="sentence_boundary",
            ),
            CutBoundary(
                cut_id="c1",
                sentence_id="s2",
                position="end",
                alignment="sentence_boundary",
            ),
            CutBoundary(
                cut_id="c2",
                sentence_id="s3",
                position="end",
                alignment="sentence_boundary",
            ),
        ],
        slots=[
            CutSlot(
                slot_id="Vogel_slot_009",
                local_asset_id=GHOST_ID,
                asset_fit="strong",
                asset_fit_reason="looks like a bird",
                visual_intent="stork",
            ),
            CutSlot(
                slot_id="Vogel_slot_010",
                local_asset_id="vogel_clip",
                asset_fit="strong",
                asset_fit_reason="local",
                visual_intent="clip",
            ),
        ],
        closing_fallback_asset_id=GHOST_ID,
    )

    updated, notes = demote_slots_with_unknown_local_assets(plan, {"vogel_clip"})

    assert "Vogel_slot_009" in notes
    ghost = next(slot for slot in updated.slots if slot.slot_id == "Vogel_slot_009")
    assert ghost.local_asset_id is None
    assert ghost.asset_fit == "none"
    assert ghost.coverage_gap_id
    keep = next(slot for slot in updated.slots if slot.slot_id == "Vogel_slot_010")
    assert keep.local_asset_id == "vogel_clip"
    assert updated.closing_fallback_asset_id is None


def test_generate_chapter_unified_cut_keeps_ghost_id_for_llm(tmp_path: Path) -> None:
    from otio_app.services.without_voiceover_enhanced.chapter_cut_service import (
        generate_chapter_unified_cut,
    )
    from otio_app.services.without_voiceover_enhanced.cut_plan_service import (
        FolderUnifiedCutResult,
    )

    project = _project(tmp_path)
    original = _original(project)
    _save_inventory(project, [original, _ghost_asset()])
    seen_ids: list[set[str]] = []

    plan = UnifiedCutPlanDocument(
        script_version="v1",
        boundaries=[
            CutBoundary(
                cut_id="c0",
                sentence_id="s1",
                position="start",
                alignment="sentence_boundary",
            ),
            CutBoundary(
                cut_id="c1",
                sentence_id="s2",
                position="end",
                alignment="sentence_boundary",
            ),
        ],
        slots=[
            CutSlot(
                slot_id="Vogel_slot_001",
                local_asset_id="vogel_clip",
                asset_fit="strong",
                visual_intent="clip",
            )
        ],
    )

    def fake_generate(proj, folder_name, **_kwargs):
        loaded = load_folder_inventory_file(
            get_folder_inventory_path(proj.work_dir_path, folder_name)
        )
        seen_ids.append({asset.asset_id for asset in (loaded.assets if loaded else [])})
        return FolderUnifiedCutResult(
            folder_name=folder_name,
            status="PASS",
            plan=plan,
            slot_count=1,
            gap_count=0,
        )

    with patch(
        "otio_app.services.without_voiceover_enhanced.chapter_cut_service.generate_unified_cut_for_folder",
        side_effect=fake_generate,
    ), patch(
        "otio_app.services.without_voiceover_enhanced.chapter_cut_service.load_prior_chapter_plans",
        return_value=[],
    ), patch(
        "otio_app.services.without_voiceover_enhanced.chapter_cut_service.persist_chapter_unified_plan",
        return_value=plan,
    ):
        generate_chapter_unified_cut(project, FOLDER)

    assert seen_ids
    assert GHOST_ID in seen_ids[0]
    assert "vogel_clip" in seen_ids[0]


def test_resolve_chapter_timeline_does_not_prune_or_demote_inventory(
    tmp_path: Path,
) -> None:
    from otio_app.services.without_voiceover_enhanced.chapter_cut_service import (
        persist_chapter_unified_plan,
        resolve_chapter_timeline,
    )
    from otio_app.services.without_voiceover_enhanced.models import (
        ResolvedTimelineDocument,
    )

    project = _project(tmp_path)
    original = _original(project)
    _save_inventory(project, [original, _ghost_asset()])
    plan = UnifiedCutPlanDocument(
        script_version="v1",
        boundaries=[
            CutBoundary(
                cut_id="c0",
                sentence_id="s1",
                position="start",
                alignment="sentence_boundary",
            ),
            CutBoundary(
                cut_id="c1",
                sentence_id="s2",
                position="end",
                alignment="sentence_boundary",
            ),
        ],
        slots=[
            CutSlot(
                slot_id="Vogel_slot_009",
                local_asset_id=GHOST_ID,
                asset_fit="strong",
                asset_fit_reason="bird",
                visual_intent="stork",
            )
        ],
    )
    persist_chapter_unified_plan(
        project, FOLDER, plan, refresh_merged=False, reset_open_gaps=False
    )
    dummy = ResolvedTimelineDocument(
        script_version="v1",
        fps=25.0,
        total_duration_seconds=2.0,
        shots=[],
        audio_segments=[],
    )

    with patch(
        "otio_app.services.without_voiceover_enhanced.coverage_gap_external_export.ingest_coverage_gap_inbox",
        return_value=None,
    ), patch(
        "otio_app.services.without_voiceover_enhanced.unified_timeline_service.resolve_unified_timeline",
        return_value=dummy,
    ), patch(
        "otio_app.services.without_voiceover_enhanced.gap_merge_service.merge_export_ready_gaps_into_timeline",
        return_value=(dummy, None),
    ):
        resolve_chapter_timeline(project, FOLDER)

    saved = load_folder_inventory_file(
        get_folder_inventory_path(project.work_dir_path, FOLDER)
    )
    assert saved is not None
    assert GHOST_ID in {asset.asset_id for asset in saved.assets}

    from otio_app.services.without_voiceover_enhanced.chapter_cut_service import (
        load_chapter_unified_plan,
    )

    updated = load_chapter_unified_plan(project, FOLDER)
    assert updated is not None
    slot = updated.slots[0]
    assert slot.local_asset_id == GHOST_ID
    assert slot.asset_fit == "strong"
