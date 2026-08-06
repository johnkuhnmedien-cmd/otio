"""Enhanced Funnel: Provider-Reuse, Inventar-Cleanup, Usage-Keys."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from otio_app.analysis_models import AssetFolderAnalysis, AssetMediaAnalysis
from otio_app.defaults import DEFAULT_ENHANCED_WORK_SUBDIR, VOICEOVER_GENERATION_SUBDIR
from otio_app.models import Project, ProjectMode
from otio_app.project_layout import get_folder_inventory_path
from otio_app.services.inventory_loader import save_folder_inventory
from otio_app.services.without_voiceover_enhanced.enhanced_supplement_dedupe import (
    cleanup_enhanced_inventory_duplicates,
    find_existing_enhanced_provider_asset,
    parse_provider_identity_from_asset_id,
    preferred_inventory_asset_id,
    provider_identity_for_candidate,
    reuse_identity_key,
    scan_enhanced_inventory_duplicates,
)
from otio_app.services.without_voiceover_enhanced.models import (
    AcceptedSupplementsDocument,
    StockCandidate,
)
from otio_app.services.without_voiceover_enhanced.paths import (
    ACCEPTED_SUPPLEMENTS_FILENAME,
    STOCK_SUBDIR,
)
from otio_app.services.without_voiceover_enhanced.supplement_funnel_service import (
    download_full_candidate_safe,
)
from otio_app.services.without_voiceover_enhanced.unified_cut_plan import (
    enforce_asset_reuse_as_coverage_gaps,
)
from otio_app.services.without_voiceover_enhanced.models import (
    CutBoundary,
    CutSlot,
    UnifiedCutPlanDocument,
)


def _project(tmp_path: Path, *, language: str = "DE") -> Project:
    root = tmp_path / "Irland"
    root.mkdir(parents=True)
    work = root / DEFAULT_ENHANCED_WORK_SUBDIR
    work.mkdir(parents=True)
    folder = root / "Cliffs of Moher"
    folder.mkdir(parents=True)
    return Project(
        id="enh-dedupe",
        name="Irland",
        project_root=str(root),
        work_dir=str(work),
        language=language,
        mode=ProjectMode.WITHOUT_VOICEOVER_ENHANCED,
        asset_subdir_names=["Cliffs of Moher"],
        selected_asset_subdirs=["Cliffs of Moher"],
    )


def _candidate(provider_asset_id: str = "27608379") -> StockCandidate:
    return StockCandidate(
        candidate_id=f"pexels_video_{provider_asset_id}",
        provider="pexels",
        provider_asset_id=provider_asset_id,
        title="cliffs",
        media_type="video",
        download_url="https://example.com/video.mp4",
        source_page="https://example.com/page",
    )


def test_reuse_identity_key_collapses_pexels_aliases() -> None:
    assert reuse_identity_key("pexels_video_27608379") == "pexels:27608379"
    assert reuse_identity_key("supplement_pexels_27608379") == "pexels:27608379"
    assert (
        reuse_identity_key("pexels_video_27608379")
        == reuse_identity_key("supplement_pexels_27608379")
    )
    assert parse_provider_identity_from_asset_id("manual_cliffs_abc") is None
    assert reuse_identity_key("manual_cliffs_abc") == "manual_cliffs_abc"


def test_find_existing_from_inventory(tmp_path: Path) -> None:
    project = _project(tmp_path)
    media = Path(project.project_root) / "Cliffs of Moher" / "clip.mp4"
    media.write_bytes(b"video-bytes")
    inv = AssetFolderAnalysis(
        folder="Cliffs of Moher",
        assets=[
            AssetMediaAnalysis(
                path=str(media),
                asset_id="pexels_video_27608379",
                provider="pexels",
                approved_for_cut_plan=True,
                analysis_status="complete",
                license_metadata={
                    "provider": "pexels",
                    "provider_asset_id": "27608379",
                },
            )
        ],
        media_files=[str(media)],
    )
    save_folder_inventory(
        get_folder_inventory_path(project.work_dir_path, "Cliffs of Moher"), inv
    )
    found = find_existing_enhanced_provider_asset(
        project,
        provider="pexels",
        provider_asset_id="27608379",
        folder_name="Cliffs of Moher",
    )
    assert found is not None
    assert found.path == media
    assert found.source == "inventory"


def test_find_existing_from_sibling_language_accepted(tmp_path: Path) -> None:
    project = _project(tmp_path, language="EN")
    media = Path(project.project_root) / "Cliffs of Moher" / "shared.mp4"
    media.write_bytes(b"shared-video")
    # Sibling DE accepted list
    de_accepted = (
        Path(project.work_dir)
        / "DE"
        / VOICEOVER_GENERATION_SUBDIR
        / STOCK_SUBDIR
        / ACCEPTED_SUPPLEMENTS_FILENAME
    )
    de_accepted.parent.mkdir(parents=True, exist_ok=True)
    cand = _candidate()
    cand.local_media_path = str(media)
    cand.media_validation_status = "export_ready"
    de_accepted.write_text(
        AcceptedSupplementsDocument(
            script_version="v1", supplements=[cand]
        ).model_dump_json(indent=2),
        encoding="utf-8",
    )
    found = find_existing_enhanced_provider_asset(
        project,
        provider="pexels",
        provider_asset_id="27608379",
        folder_name="Cliffs of Moher",
    )
    assert found is not None
    assert found.path == media
    assert found.source.startswith("accepted:")


def test_download_reuses_without_network(tmp_path: Path) -> None:
    project = _project(tmp_path)
    media = Path(project.project_root) / "Cliffs of Moher" / "clip.mp4"
    media.write_bytes(b"video-bytes")
    inv = AssetFolderAnalysis(
        folder="Cliffs of Moher",
        assets=[
            AssetMediaAnalysis(
                path=str(media),
                asset_id="supplement_pexels_27608379",
                provider="pexels",
                approved_for_cut_plan=True,
                license_metadata={
                    "provider": "pexels",
                    "provider_asset_id": "27608379",
                },
            )
        ],
        media_files=[str(media)],
    )
    save_folder_inventory(
        get_folder_inventory_path(project.work_dir_path, "Cliffs of Moher"), inv
    )
    with patch(
        "otio_app.services.without_voiceover_enhanced.supplement_funnel_service.fetch_full_media_bytes"
    ) as fetch:
        path = download_full_candidate_safe(
            project,
            _candidate(),
            gap_id="Cliffs_gap_001",
            folder_name="Cliffs of Moher",
        )
        fetch.assert_not_called()
    assert path == media


def test_scan_finds_same_path_different_ids(tmp_path: Path) -> None:
    """Gleicher Dateipfad, zwei Asset-IDs — auch ohne Provider-Metadata."""
    project = _project(tmp_path)
    media = Path(project.project_root) / "Cliffs of Moher" / "same.mp4"
    media.write_bytes(b"same-bytes")
    inv = AssetFolderAnalysis(
        folder="Cliffs of Moher",
        assets=[
            AssetMediaAnalysis(path=str(media), asset_id="manual_a"),
            AssetMediaAnalysis(path=str(media), asset_id="manual_b"),
        ],
        media_files=[str(media)],
    )
    save_folder_inventory(
        get_folder_inventory_path(project.work_dir_path, "Cliffs of Moher"), inv
    )
    groups = scan_enhanced_inventory_duplicates(project)
    assert len(groups) == 1
    assert set(groups[0].remove_asset_ids) | {groups[0].keep_asset_id} == {
        "manual_a",
        "manual_b",
    }


def test_scan_discovers_inventory_json_when_project_lists_empty(tmp_path: Path) -> None:
    project = _project(tmp_path)
    project.asset_subdir_names = []
    project.selected_asset_subdirs = []
    media = Path(project.project_root) / "Cliffs of Moher" / "x.mp4"
    media.write_bytes(b"x")
    inv = AssetFolderAnalysis(
        folder="Cliffs of Moher",
        assets=[
            AssetMediaAnalysis(
                path=str(media),
                asset_id="pexels_video_1",
                provider="pexels",
                license_metadata={
                    "provider": "pexels",
                    "provider_asset_id": "1",
                },
            ),
            AssetMediaAnalysis(
                path=str(media),
                asset_id="supplement_pexels_1",
                provider="pexels",
                license_metadata={
                    "provider": "pexels",
                    "provider_asset_id": "1",
                },
            ),
        ],
        media_files=[str(media)],
    )
    save_folder_inventory(
        get_folder_inventory_path(project.work_dir_path, "Cliffs of Moher"), inv
    )
    groups = scan_enhanced_inventory_duplicates(project)
    assert len(groups) == 1


def test_cleanup_collapses_inventory_duplicates(tmp_path: Path) -> None:
    project = _project(tmp_path)
    keep = Path(project.project_root) / "Cliffs of Moher" / "keep.mp4"
    drop = Path(project.project_root) / "Cliffs of Moher" / "drop.mp4"
    keep.write_bytes(b"keep-video-longer-content")
    drop.write_bytes(b"drop")
    inv = AssetFolderAnalysis(
        folder="Cliffs of Moher",
        assets=[
            AssetMediaAnalysis(
                path=str(drop),
                asset_id="pexels_video_99",
                provider="pexels",
                license_metadata={
                    "provider": "pexels",
                    "provider_asset_id": "99",
                },
            ),
            AssetMediaAnalysis(
                path=str(keep),
                asset_id="supplement_pexels_99",
                provider="pexels",
                approved_for_cut_plan=True,
                supplement_validation_status="PASS",
                license_metadata={
                    "provider": "pexels",
                    "provider_asset_id": "99",
                },
            ),
        ],
        media_files=[str(drop), str(keep)],
    )
    save_folder_inventory(
        get_folder_inventory_path(project.work_dir_path, "Cliffs of Moher"), inv
    )
    groups = scan_enhanced_inventory_duplicates(project)
    assert len(groups) == 1
    assert groups[0].keep_asset_id == "supplement_pexels_99"
    assert "pexels_video_99" in groups[0].remove_asset_ids

    report = cleanup_enhanced_inventory_duplicates(project, dry_run=False)
    assert report.inventory_pruned == 1
    from otio_app.services.inventory_loader import load_folder_inventory

    after = load_folder_inventory(project, "Cliffs of Moher")
    assert after is not None
    assert len(after.assets) == 1
    assert after.assets[0].asset_id == "supplement_pexels_99"


def test_enforce_reuse_treats_provider_aliases_as_same() -> None:
    bounds = [
        CutBoundary(
            cut_id="c0",
            sentence_id="s1",
            position="start",
            alignment="sentence_boundary",
        ),
        CutBoundary(
            cut_id="c1",
            sentence_id="s1",
            position="middle",
            alignment="sentence_boundary",
        ),
        CutBoundary(
            cut_id="c2",
            sentence_id="s1",
            position="end",
            alignment="sentence_boundary",
        ),
    ]
    plan = UnifiedCutPlanDocument(
        script_version="v1",
        boundaries=bounds,
        slots=[
            CutSlot(
                slot_id="slot_0",
                local_asset_id="pexels_video_1",
                asset_fit="strong",
                visual_intent="a",
            ),
            CutSlot(
                slot_id="slot_1",
                local_asset_id="supplement_pexels_1",
                asset_fit="strong",
                visual_intent="b",
            ),
        ],
        closing_fallback_asset_id="fb",
        closing_fallback_asset_fit="strong",
        closing_fallback_asset_fit_reason="r",
        closing_fallback_visual_intent="v",
    )
    out, notes = enforce_asset_reuse_as_coverage_gaps(
        plan,
        max_asset_usage=2,
        min_asset_reuse_distance_shots=4,
        prefer_closing_fallback=False,
    )
    assert out.slots[0].local_asset_id == "pexels_video_1"
    assert out.slots[1].local_asset_id is None
    assert out.slots[1].asset_fit == "none"
    assert notes


def test_preferred_inventory_asset_id_keeps_funnel_id(tmp_path: Path) -> None:
    project = _project(tmp_path)
    cand = _candidate("42")
    assert preferred_inventory_asset_id(project, cand) == "pexels_video_42"
    assert provider_identity_for_candidate(cand).key == "pexels:42"
    # Gepolsterte Funnel-ID ≠ provider_asset_id — trotzdem Funnel-ID behalten
    padded = StockCandidate(
        candidate_id="pexels_photo_001",
        provider="pexels",
        provider_asset_id="1001",
        media_type="photo",
    )
    assert preferred_inventory_asset_id(project, padded) == "pexels_photo_001"
    # Ohne candidate_id → stabile Form
    bare = StockCandidate(
        candidate_id="",
        provider="pexels",
        provider_asset_id="42",
        media_type="video",
    )
    assert preferred_inventory_asset_id(project, bare) == "supplement_pexels_42"
