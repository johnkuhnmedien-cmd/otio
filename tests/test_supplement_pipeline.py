"""Tests für Supplement-Pipeline und harte max_asset_usage-Regel."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from otio_app.analysis_models import (
    AssetFolderAnalysis,
    AssetMediaAnalysis,
    EditPlanRule,
    EditPlanRulesDocument,
    EditPlanShot,
    SupplementCandidate,
    SupplementRequest,
    TimelineItem,
    TimelineItemTransform,
    VoiceSegment,
)
from otio_app.defaults import (
    RIGHTS_STATUS_APPROVED,
    RIGHTS_STATUS_NEEDS_LICENSE_REVIEW,
    SUPPLEMENT_SOURCE_GOOGLE,
    SUPPLEMENT_SOURCE_NANO_BANANA,
    SUPPLEMENT_SOURCE_PEXELS,
)
from otio_app.models import Project
from otio_app.services.asset_usage import (
    filter_assets_by_usage,
    usage_count_by_asset_id_from_shots,
    validate_max_asset_usage_blockers,
)
from otio_app.services.edit_plan_rules import RULE_MAX_ASSET_USES
from otio_app.services.generic_outro_selector import asset_id_for_path
from otio_app.services.inventory_hash import compute_folder_inventory_hash, inventory_hash_is_stale
from otio_app.services.supplement_coverage import (
    COVERAGE_SUPPLEMENT_REQUIRED,
    coverage_to_supplement_request,
    evaluate_segment_coverage,
    score_asset_match,
)
from otio_app.services.supplement_pipeline import (
    acquire_supplement_candidate,
    analyze_supplement_asset,
    extend_folder_inventory,
    load_sidecar,
    save_sidecar,
    search_supplement_candidates,
)
from otio_app.services.supplement_requests import load_supplement_requests, upsert_requests
from otio_app.services.supplement_sources.adobe_stock import AdobeStockAdapter
from otio_app.services.supplement_sources.pexels import PexelsAdapter


def _project(tmp_path: Path) -> Project:
    root = tmp_path / "USA"
    work = root / "_otio"
    (root / "Antelope Canyon").mkdir(parents=True)
    work.mkdir(parents=True)
    return Project(
        id="supp-test",
        name="USA",
        project_root=str(root),
        work_dir=str(work),
        asset_subdir_names=["Antelope Canyon"],
        selected_asset_subdirs=["Antelope Canyon"],
        width=1920,
        height=1080,
        fps=25,
    )


def _rules_max_one() -> EditPlanRulesDocument:
    return EditPlanRulesDocument(
        project_id="supp-test",
        rules=[
            EditPlanRule(
                id="r1",
                rule_type=RULE_MAX_ASSET_USES,
                enabled=True,
                params={"max_count": 1},
                label="Max 1",
            )
        ],
    )


def test_weak_local_match_creates_supplement_request() -> None:
    segment = VoiceSegment(start_sec=0.0, end_sec=7.0, text="Ein Gewitter leitete Wasser ins Canyon-Einzugsgebiet.")
    assets = [
        AssetMediaAnalysis(
            path="/a/canyon.mp4",
            description="Rote Felswände im Canyon bei Sonnenschein",
            asset_id="asset_canyon",
        )
    ]
    coverage = evaluate_segment_coverage(
        beat_id="beat_001",
        segment=segment,
        folder_name="Antelope Canyon",
        voice_file="/v.wav",
        assets=assets,
    )
    assert coverage.coverage_status == COVERAGE_SUPPLEMENT_REQUIRED
    request = coverage_to_supplement_request(coverage)
    assert request is not None
    assert request.beat_id == "beat_001"


def test_weak_local_asset_not_forced_by_high_threshold() -> None:
    score = score_asset_match(
        passage_text="Gewitter und Wasserflut",
        visual_requirement="Gewitter über Wüste",
        description="Sonniger Canyon ohne Wasser",
    )
    assert score < 0.55


def test_pexels_candidate_saved_with_sidecar(tmp_path: Path) -> None:
    project = _project(tmp_path)
    request = SupplementRequest(
        supplement_request_id="supp_req_001",
        section_id="section_antelope_canyon",
        folder_name="Antelope Canyon",
        beat_id="beat_001",
        passage_text="Gewitter",
        visual_requirement="Gewitter über Wüste",
        selected_source=SUPPLEMENT_SOURCE_PEXELS,
    )
    candidate = SupplementCandidate(
        candidate_id="cand_001",
        supplement_request_id=request.supplement_request_id,
        provider=SUPPLEMENT_SOURCE_PEXELS,
        provider_asset_id="12345",
        title="Flash flood",
        download_url="",
    )
    with patch.object(PexelsAdapter, "acquire") as mock_acquire:
        from otio_app.analysis_models import SupplementAssetSidecar
        from otio_app.services.supplement_sources.base import SupplementAsset

        dest = tmp_path / "out"
        dest.mkdir()
        local = dest / "video.mp4"
        local.write_bytes(b"pexels")
        sidecar = SupplementAssetSidecar(
            asset_id="asset_pexels_12345",
            supplement_request_id=request.supplement_request_id,
            provider=SUPPLEMENT_SOURCE_PEXELS,
            provider_asset_id="12345",
            local_path=str(local),
            rights_status=RIGHTS_STATUS_APPROVED,
        )
        mock_acquire.return_value = SupplementAsset(local_path=local, sidecar=sidecar)
        asset = acquire_supplement_candidate(project, candidate, request)
    assert asset.local_path.is_file()
    saved = save_sidecar(asset.sidecar)
    assert saved.is_file()
    assert load_sidecar(asset.local_path) is not None


def test_adobe_not_licensed_without_approval(tmp_path: Path) -> None:
    project = _project(tmp_path)
    request = SupplementRequest(
        supplement_request_id="supp_req_adobe",
        section_id="section_antelope_canyon",
        folder_name="Antelope Canyon",
        beat_id="beat_002",
        passage_text="Test",
    )
    candidate = AdobeStockAdapter().search(request)[0]
    with pytest.raises(PermissionError, match="Adobe Asset"):
        acquire_supplement_candidate(project, candidate, request)


def test_google_candidate_needs_license_review(tmp_path: Path) -> None:
    project = _project(tmp_path)
    request = SupplementRequest(
        supplement_request_id="supp_req_google",
        section_id="section_antelope_canyon",
        folder_name="Antelope Canyon",
        beat_id="beat_003",
        passage_text="Test",
        selected_source=SUPPLEMENT_SOURCE_GOOGLE,
    )
    from otio_app.services.supplement_sources.google_search import GoogleSearchAdapter

    candidate = GoogleSearchAdapter().search(request)[0]
    dest = project.project_root_path / "Antelope Canyon" / "_supplemental" / "_google_search"
    with patch(
        "otio_app.services.supplement_sources.google_search.GoogleSearchAdapter.acquire"
    ) as mock_acquire:
        from otio_app.analysis_models import SupplementAssetSidecar
        from otio_app.services.supplement_sources.base import SupplementAsset

        local = dest / "img.jpg"
        local.parent.mkdir(parents=True)
        local.write_bytes(b"j")
        sidecar = SupplementAssetSidecar(
            asset_id="asset_google_1",
            supplement_request_id=request.supplement_request_id,
            provider=SUPPLEMENT_SOURCE_GOOGLE,
            local_path=str(local),
            rights_status=RIGHTS_STATUS_NEEDS_LICENSE_REVIEW,
        )
        mock_acquire.return_value = SupplementAsset(local_path=local, sidecar=sidecar)
        asset = acquire_supplement_candidate(project, candidate, request)
    assert asset.sidecar.rights_status == RIGHTS_STATUS_NEEDS_LICENSE_REVIEW


def test_nano_banana_stores_prompt_metadata(tmp_path: Path) -> None:
    project = _project(tmp_path)
    request = SupplementRequest(
        supplement_request_id="supp_req_nano",
        section_id="section_antelope_canyon",
        folder_name="Antelope Canyon",
        beat_id="beat_004",
        passage_text="Wüstenflut",
        generation_prompt="Dramatic flash flood in desert canyon",
        selected_source=SUPPLEMENT_SOURCE_NANO_BANANA,
    )
    from otio_app.services.supplement_sources.nano_banana import NanoBananaAdapter

    dest = project.project_root_path / "Antelope Canyon" / "_supplemental" / "_nano_banana"
    asset = NanoBananaAdapter().generate(request, dest)
    assert asset.sidecar.prompt
    assert asset.sidecar.model
    assert asset.local_path.is_file()


def test_inventory_extended_and_delta_written(tmp_path: Path) -> None:
    project = _project(tmp_path)
    asset = AssetMediaAnalysis(
        path=str(tmp_path / "new.mp4"),
        description="Gewitter über Wüste",
        asset_id="asset_supp_001",
        asset_origin="pexels",
        rights_status=RIGHTS_STATUS_APPROVED,
    )
    Path(asset.path).write_bytes(b"v")
    extend_folder_inventory(project, folder_name="Antelope Canyon", asset=asset)
    from otio_app.project_layout import get_folder_inventory_delta_path, get_folder_inventory_path

    inventory_path = get_folder_inventory_path(project.work_dir_path, "Antelope Canyon")
    delta_path = get_folder_inventory_delta_path(project.work_dir_path, "Antelope Canyon")
    assert inventory_path.is_file()
    assert delta_path.is_file()


def test_inventory_hash_detects_stale_plan(tmp_path: Path) -> None:
    project = _project(tmp_path)
    item = AssetFolderAnalysis(
        folder="Antelope Canyon",
        assets=[AssetMediaAnalysis(path="/a.mp4", description="alt", asset_id="a1")],
    )
    old_hash = compute_folder_inventory_hash(item)
    item2 = AssetFolderAnalysis(
        folder="Antelope Canyon",
        assets=[
            AssetMediaAnalysis(path="/a.mp4", description="alt", asset_id="a1"),
            AssetMediaAnalysis(path="/b.mp4", description="neu", asset_id="a2"),
        ],
    )
    new_hash = compute_folder_inventory_hash(item2)
    assert old_hash != new_hash
    from otio_app.project_layout import get_folder_inventory_path
    from otio_app.services.inventory_loader import save_folder_inventory

    save_folder_inventory(get_folder_inventory_path(project.work_dir_path, "Antelope Canyon"), item2)
    assert inventory_hash_is_stale(project, "Antelope Canyon", old_hash)


def test_max_asset_usage_hard_blocker() -> None:
    rules = _rules_max_one()
    shots = [
        EditPlanShot(
            voice_file="/v.wav",
            folder="F",
            voice_start_sec=0,
            voice_end_sec=3,
            duration_sec=3,
            asset_path="/a/x.mp4",
            asset_id="asset_x",
        ),
        EditPlanShot(
            voice_file="/v.wav",
            folder="F",
            voice_start_sec=3,
            voice_end_sec=6,
            duration_sec=3,
            asset_path="/a/x.mp4",
            asset_id="asset_x",
        ),
    ]
    violations = validate_max_asset_usage_blockers(shots=shots, rules_doc=rules)
    assert len(violations) == 1
    assert violations[0].usage_count == 2
    assert violations[0].severity == "BLOCKER"


def test_filter_assets_by_usage_excludes_used_asset() -> None:
    assets = [{"path": "/a/x.mp4", "asset_id": "asset_x", "description": "x"}]
    filtered = filter_assets_by_usage(assets, usage={"asset_x": 1}, max_count=1)
    assert filtered == []


def test_supplement_requests_persisted(tmp_path: Path) -> None:
    project = _project(tmp_path)
    request = SupplementRequest(
        supplement_request_id="supp_req_persist",
        section_id="section_antelope_canyon",
        folder_name="Antelope Canyon",
        beat_id="beat_010",
        passage_text="Test",
        selected_source=SUPPLEMENT_SOURCE_PEXELS,
    )
    upsert_requests(project, [request])
    loaded = load_supplement_requests(project)
    assert any(entry.supplement_request_id == "supp_req_persist" for entry in loaded.requests)


def test_search_stores_candidates(tmp_path: Path) -> None:
    project = _project(tmp_path)
    request = SupplementRequest(
        supplement_request_id="supp_req_search",
        section_id="section_antelope_canyon",
        folder_name="Antelope Canyon",
        beat_id="beat_011",
        passage_text="Gewitter",
        visual_requirement="Storm clouds",
        selected_source=SUPPLEMENT_SOURCE_PEXELS,
    )
    upsert_requests(project, [request])
    found = search_supplement_candidates(project, request)
    assert found
    loaded = load_supplement_requests(project)
    assert loaded.candidates


def test_timeline_google_rights_blocks_validation() -> None:
    from otio_app.services.edit_plan_validator import ValidationStatus, validate_timeline_items
    from otio_app.analysis_models import EditPlanSettings

    item = TimelineItem(
        timeline_item_id="i1",
        type="video_shot",
        section_id="s1",
        folder_name="F",
        resolved_media_path="/a.mp4",
        duration_sec=5.0,
        final_duration_sec=5.0,
        rights_status=RIGHTS_STATUS_NEEDS_LICENSE_REVIEW,
        transform=TimelineItemTransform(),
    )
    result = validate_timeline_items([item], settings=EditPlanSettings())
    assert result.status == ValidationStatus.BLOCKED
