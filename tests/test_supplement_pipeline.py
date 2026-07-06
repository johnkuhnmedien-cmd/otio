"""Tests für Supplement-Pipeline und harte max_asset_usage-Regel."""

from __future__ import annotations

import json
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
    CANDIDATE_STATUS_MOCK_ONLY,
    PROVIDER_STATUS_CONFIG_MISSING,
    RIGHTS_STATUS_APPROVED,
    RIGHTS_STATUS_NEEDS_LICENSE_REVIEW,
    SUPPLEMENT_SOURCE_ADOBE,
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
    import_manual_supplement_asset,
    load_sidecar,
    save_sidecar,
    search_supplement_candidates,
)
from otio_app.services.supplement_requests import load_supplement_requests, upsert_requests
from otio_app.services.supplement_search import build_keyword_query
from otio_app.services.supplement_search import build_pexels_primary_query, build_pexels_query_variants
from otio_app.services.supplement_sources.adobe_stock import AdobeStockAdapter
from otio_app.services.supplement_sources.google_search import GoogleSearchAdapter
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
    with pytest.raises(PermissionError, match="Mock"):
        acquire_supplement_candidate(project, candidate, request)


def test_google_candidate_needs_license_review(tmp_path: Path) -> None:
    request = SupplementRequest(
        supplement_request_id="supp_req_google",
        section_id="section_antelope_canyon",
        folder_name="Antelope Canyon",
        beat_id="beat_003",
        passage_text="Test",
        selected_source=SUPPLEMENT_SOURCE_GOOGLE,
    )
    candidate = GoogleSearchAdapter().search(request)[0]
    assert "Rechteprüfung" in candidate.license
    assert candidate.requires_user_approval is True
    assert candidate.status == CANDIDATE_STATUS_MOCK_ONLY
    assert candidate.download_enabled is False
    assert "example.com" not in candidate.download_url


def test_google_candidate_allows_manual_import_only(tmp_path: Path) -> None:
    project = _project(tmp_path)
    source = tmp_path / "google-source.jpg"
    source.write_bytes(b"google-image")
    request = SupplementRequest(
        supplement_request_id="supp_req_google_discovery",
        section_id="section_antelope_canyon",
        folder_name="Antelope Canyon",
        beat_id="beat_003",
        passage_text="Test",
        selected_source=SUPPLEMENT_SOURCE_GOOGLE,
    )
    candidate = GoogleSearchAdapter().search(request)[0]
    assert candidate.download_url == ""
    with pytest.raises(PermissionError, match="Mock"):
        acquire_supplement_candidate(project, candidate, request)
    asset = import_manual_supplement_asset(
        project,
        request=request,
        source_path=source,
        source_url=candidate.source_page_url,
        source_provider=SUPPLEMENT_SOURCE_GOOGLE,
        acquisition_method="manual_download",
    )
    assert asset.local_path.is_file()
    assert asset.sidecar.provider == SUPPLEMENT_SOURCE_GOOGLE
    assert asset.sidecar.rights_status == RIGHTS_STATUS_NEEDS_LICENSE_REVIEW
    assert asset.sidecar.approval_status == "MANUAL_IMPORTED"


def test_candidates_are_filtered_by_selected_source() -> None:
    from otio_app.ui.supplement_assets import _candidates_for_source

    candidates = [
        SupplementCandidate(
            candidate_id="c_google",
            supplement_request_id="req1",
            provider=SUPPLEMENT_SOURCE_GOOGLE,
        ),
        SupplementCandidate(
            candidate_id="c_adobe",
            supplement_request_id="req1",
            provider=SUPPLEMENT_SOURCE_ADOBE,
            is_mock=False,
            status="CANDIDATE_FOUND",
        ),
    ]
    filtered = _candidates_for_source(
        candidates,
        request_id="req1",
        selected_source=SUPPLEMENT_SOURCE_ADOBE,
    )
    assert [candidate.candidate_id for candidate in filtered] == ["c_adobe"]


def test_mock_candidates_hidden_when_demo_mode_false() -> None:
    from otio_app.ui.supplement_assets import _candidates_for_source

    candidates = [
        SupplementCandidate(
            candidate_id="mock",
            supplement_request_id="req1",
            provider=SUPPLEMENT_SOURCE_ADOBE,
            is_mock=True,
            status="CANDIDATE_MOCK_ONLY",
        )
    ]
    assert _candidates_for_source(
        candidates,
        request_id="req1",
        selected_source=SUPPLEMENT_SOURCE_ADOBE,
    ) == []
    assert _candidates_for_source(
        candidates,
        request_id="req1",
        selected_source=SUPPLEMENT_SOURCE_ADOBE,
        demo_mode=True,
    )


def test_provider_tab_labels_and_status_chain() -> None:
    from otio_app.services.supplement_sources import get_provider_readiness
    from otio_app.ui.supplement_assets import _provider_tab_label, _status_chain

    assert "Pexels" in _provider_tab_label(
        SUPPLEMENT_SOURCE_PEXELS,
        get_provider_readiness(SUPPLEMENT_SOURCE_PEXELS),
    )
    chain = _status_chain(
        SupplementRequest(
            supplement_request_id="req",
            section_id="section",
            folder_name="Antelope Canyon",
            beat_id="beat",
            passage_text="text",
            status="READY_FOR_REPLAN",
        )
    )
    assert "Inventory aktualisiert" in chain
    assert "Schnittplan neu vorschlagen" in chain


def test_pexels_without_api_key_is_config_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("PEXELS_API_KEY", raising=False)
    adapter = PexelsAdapter()
    readiness = adapter.readiness()
    assert readiness.status == PROVIDER_STATUS_CONFIG_MISSING
    request = SupplementRequest(
        supplement_request_id="supp_req_pexels_missing",
        section_id="section_antelope_canyon",
        folder_name="Antelope Canyon",
        beat_id="beat_003",
        passage_text="Test",
        selected_source=SUPPLEMENT_SOURCE_PEXELS,
    )
    assert adapter.search(request) == []


def test_pexels_ready_maps_real_response(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PEXELS_API_KEY", "test-key")
    seen_urls: list[str] = []

    class FakeResponse:
        status = 200
        headers = {"Content-Type": "application/json"}

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self):
            return (
                b'{"total_results":1,"videos":[{"id":123,"url":"https://www.pexels.com/video/123",'
                b'"image":"https://images.pexels.com/preview.jpg","width":1920,"height":1080,'
                b'"duration":12,"user":{"name":"Creator","url":"https://www.pexels.com/@creator"},'
                b'"video_files":[{"id":1,"quality":"sd","file_type":"video/mp4","width":640,"height":360,'
                b'"fps":24,"link":"https://videos.pexels.com/sd.mp4"},'
                b'{"id":2,"quality":"hd","file_type":"video/mp4","width":1920,"height":1080,'
                b'"fps":30,"link":"https://videos.pexels.com/hd.mp4"}]}]}'
            )

    def fake_urlopen(request, timeout=20):
        seen_urls.append(request.full_url)
        return FakeResponse()

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    request = SupplementRequest(
        supplement_request_id="supp_req_api",
        section_id="section_antelope_canyon",
        folder_name="Antelope Canyon",
        location_name="Antelope Canyon",
        beat_id="beat_api",
        passage_text="Narrow light",
        visual_requirement="slot canyon light",
        selected_source=SUPPLEMENT_SOURCE_PEXELS,
    )
    candidates = PexelsAdapter().search(request)
    assert seen_urls
    assert "https://api.pexels.com/v1/videos/search" in seen_urls[0]
    assert candidates
    candidate = candidates[0]
    assert candidate.is_mock is False
    assert candidate.status == "CANDIDATE_FOUND"
    assert candidate.download_url == "https://videos.pexels.com/hd.mp4"
    assert candidate.pexels_video_file_id == "2"
    assert candidate.creator == "Creator"
    assert candidate.creator_url
    assert candidate.query_used.startswith("Antelope Canyon")
    assert candidate.location_match in {"exact", "likely"}


def test_pexels_query_variants_start_short_with_location() -> None:
    request = SupplementRequest(
        supplement_request_id="supp_req_query",
        section_id="section_antelope_canyon",
        folder_name="Antelope Canyon",
        location_name="Antelope Canyon",
        beat_id="beat",
        passage_text="Wegen der charakteristischen Felsspalten steht eine Person im Canyon.",
        visual_requirement="Ein Mensch in engen Felsspalten",
    )
    variants = build_pexels_query_variants(request)
    assert variants[0] == "Antelope Canyon person narrow slot canyon"
    assert all("Antelope Canyon" in query for query in variants[:7])
    assert not any("charakteristischen" in query or "steht" in query for query in variants[:7])
    assert build_pexels_primary_query(request) == "Antelope Canyon person narrow slot canyon"


def test_pexels_ui_default_query_uses_short_location_query() -> None:
    from otio_app.ui.supplement_assets import _default_query_for_provider

    request = SupplementRequest(
        supplement_request_id="supp_req_ui_query",
        section_id="section_antelope_canyon",
        folder_name="Antelope Canyon",
        location_name="Antelope Canyon",
        beat_id="beat",
        passage_text="Wegen der charakteristischen Felsspalten steht eine Person im Canyon.",
        visual_requirement="Ein Mensch in engen Felsspalten",
        search_queries={"en": ["Antelope Canyon person narrow charakteristischen slot canyon canyons steht"]},
    )
    assert _default_query_for_provider(request, SUPPLEMENT_SOURCE_PEXELS) == "Antelope Canyon person narrow slot canyon"


def test_pexels_rejects_portrait_video(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PEXELS_API_KEY", "test-key")

    class FakeResponse:
        status = 200
        headers = {"Content-Type": "application/json"}

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self):
            return (
                b'{"videos":[{"id":321,"url":"https://www.pexels.com/video/321",'
                b'"image":"https://images.pexels.com/preview.jpg","width":1080,"height":1920,'
                b'"duration":9,"user":{"name":"Creator","url":"https://www.pexels.com/@creator"},'
                b'"video_files":[{"id":1,"quality":"hd","file_type":"video/mp4","width":1080,"height":1920,'
                b'"fps":30,"link":"https://videos.pexels.com/portrait.mp4"}]}]}'
            )

    monkeypatch.setattr("urllib.request.urlopen", lambda *_args, **_kwargs: FakeResponse())
    request = SupplementRequest(
        supplement_request_id="supp_req_portrait",
        section_id="section_antelope_canyon",
        folder_name="Antelope Canyon",
        beat_id="beat",
        passage_text="Test",
        visual_requirement="Antelope Canyon",
        selected_source=SUPPLEMENT_SOURCE_PEXELS,
        required_asset_type="video",
    )
    candidate = PexelsAdapter().search(request)[0]
    assert candidate.status == "REJECTED_ASPECT_RATIO"
    assert candidate.download_enabled is False
    assert candidate.is_16_9 is False


def test_video_preferred_falls_back_to_photos_and_writes_debug(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = _project(tmp_path)
    monkeypatch.setenv("PEXELS_API_KEY", "test-key")
    calls: list[str] = []

    class FakeResponse:
        status = 200
        headers = {"Content-Type": "application/json"}

        def __init__(self, body: bytes):
            self._body = body

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self):
            return self._body

    def fake_urlopen(request, timeout=20):
        calls.append(request.full_url)
        if "/videos/search" in request.full_url:
            return FakeResponse(b'{"videos":[]}')
        return FakeResponse(
            b'{"photos":[{"id":456,"url":"https://www.pexels.com/photo/456",'
            b'"alt":"Antelope Canyon sandstone","width":3840,"height":2160,'
            b'"photographer":"Photo Creator","photographer_url":"https://www.pexels.com/@photo",'
            b'"src":{"original":"https://images.pexels.com/photo.jpg","medium":"https://images.pexels.com/medium.jpg"}}]}'
        )

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    request = SupplementRequest(
        supplement_request_id="supp_req_photo_fallback",
        section_id="section_antelope_canyon",
        folder_name="Antelope Canyon",
        beat_id="beat",
        passage_text="Test",
        visual_requirement="Antelope Canyon sandstone",
        selected_source=SUPPLEMENT_SOURCE_PEXELS,
        required_asset_type="video_preferred",
    )
    upsert_requests(project, [request])
    candidates = search_supplement_candidates(project, request)
    assert any("/videos/search" in url for url in calls)
    assert any("/v1/search" in url for url in calls)
    assert candidates[0].media_type == "image"
    assert candidates[0].is_16_9 is True
    from otio_app.project_layout import get_pexels_debug_report_path

    report = json.loads(get_pexels_debug_report_path(project.work_dir_path).read_text(encoding="utf-8"))
    assert report["raw_video_result_count"] == 0
    assert report["raw_photo_result_count"] == 1
    assert report["final_photo_candidate_count"] == 1


def test_pexels_photo_timeline_item_uses_vintage_background(tmp_path: Path) -> None:
    from otio_app.analysis_models import EditPlanSettings
    from otio_app.services.timeline_plan_builder import build_timeline_items_for_folder

    project = _project(tmp_path)
    image_path = tmp_path / "photo.jpg"
    image_path.write_bytes(b"jpeg")
    voice_path = tmp_path / "voice.wav"
    voice_path.write_bytes(b"wav")
    shot = EditPlanShot(
        voice_file=str(voice_path),
        folder="Antelope Canyon",
        voice_start_sec=0.0,
        voice_end_sec=4.0,
        duration_sec=4.0,
        asset_path=str(image_path),
        asset_id="asset_pexels_photo",
        asset_origin="pexels",
        asset_source="pexels",
        provider="pexels",
        media_type="image",
        rights_status=RIGHTS_STATUS_APPROVED,
    )
    items, _voiceover, _errors = build_timeline_items_for_folder(
        [shot],
        folder_name="Antelope Canyon",
        voice_file=str(voice_path),
        settings=EditPlanSettings(section_outro_sec=0.0),
        folder_assets=[],
    )
    item = next(entry for entry in items if entry.asset_id == "asset_pexels_photo")
    assert item.type == "image_with_background"
    assert item.asset_type == "image"
    assert item.background_style == "vintage"
    assert item.transform.scaling_mode == "fit"
    assert item.transform.zoom_x == 0.8
    assert item.transform.zoom_y == 0.8


def test_pexels_download_failure_writes_error_without_placeholder(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = _project(tmp_path)
    monkeypatch.setenv("PEXELS_API_KEY", "test-key")
    candidate = SupplementCandidate(
        candidate_id="cand_fail",
        supplement_request_id="supp_req_fail",
        provider=SUPPLEMENT_SOURCE_PEXELS,
        provider_asset_id="123",
        download_url="https://pexels.invalid/video.mp4",
        media_type="video",
        download_enabled=True,
        is_mock=False,
    )
    request = SupplementRequest(
        supplement_request_id="supp_req_fail",
        section_id="section_antelope_canyon",
        folder_name="Antelope Canyon",
        beat_id="beat_003",
        passage_text="Test",
        selected_source=SUPPLEMENT_SOURCE_PEXELS,
    )

    import urllib.error

    def fail_urlopen(*args, **kwargs):
        raise urllib.error.URLError("network failed")

    monkeypatch.setattr("urllib.request.urlopen", fail_urlopen)
    with pytest.raises(RuntimeError, match="Pexels-Download"):
        acquire_supplement_candidate(project, candidate, request)

    from otio_app.project_layout import get_provider_supplemental_dir, get_supplement_errors_path

    provider_dir = get_provider_supplemental_dir(
        project.project_root_path,
        "Antelope Canyon",
        SUPPLEMENT_SOURCE_PEXELS,
    )
    assert not list(provider_dir.glob("*")) if provider_dir.exists() else True
    assert get_supplement_errors_path(project.work_dir_path).is_file()


def test_manual_import_creates_sidecar(tmp_path: Path) -> None:
    project = _project(tmp_path)
    source = tmp_path / "downloaded.mp4"
    source.write_bytes(b"manual")
    request = SupplementRequest(
        supplement_request_id="supp_req_manual",
        section_id="section_antelope_canyon",
        folder_name="Antelope Canyon",
        beat_id="beat_003",
        passage_text="Test",
    )
    asset = import_manual_supplement_asset(
        project,
        request=request,
        source_path=source,
        source_url="https://example.com/source",
        rights_status=RIGHTS_STATUS_APPROVED,
    )
    assert asset.local_path.is_file()
    assert asset.sidecar.provider == "manual"
    assert asset.sidecar.rights_status == RIGHTS_STATUS_APPROVED
    assert load_sidecar(asset.local_path) is not None


def test_nano_banana_mock_does_not_generate_productive_asset(tmp_path: Path) -> None:
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
    candidate = NanoBananaAdapter().search(request)[0]
    assert candidate.is_mock is True
    assert candidate.download_enabled is False
    with pytest.raises(PermissionError, match="nicht produktiv"):
        NanoBananaAdapter().generate(request, dest)


def test_inventory_extended_and_delta_written(tmp_path: Path) -> None:
    project = _project(tmp_path)
    asset = AssetMediaAnalysis(
        path=str(tmp_path / "new.mp4"),
        description="Gewitter über Wüste",
        frames_used=[str(tmp_path / "frame.jpg")],
        asset_id="asset_supp_001",
        asset_origin="pexels",
        rights_status=RIGHTS_STATUS_APPROVED,
        analysis_status="complete",
        supplement_validation_status="PASS",
        approved_for_cut_plan=True,
    )
    Path(asset.path).write_bytes(b"valid-media")
    from otio_app.analysis_models import SupplementAssetSidecar
    from otio_app.services.supplement_pipeline import save_sidecar

    save_sidecar(
        SupplementAssetSidecar(
            asset_id=asset.asset_id,
            supplement_request_id="supp_req_inventory",
            provider="pexels",
            local_path=asset.path,
            rights_status=RIGHTS_STATUS_APPROVED,
        )
    )
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
    assert found == []
    loaded = load_supplement_requests(project)
    assert loaded.candidates == []


def test_keyword_query_prefers_short_visual_terms() -> None:
    query = build_keyword_query(
        folder_name="Antelope Canyon",
        visual_requirement="Ein Mensch steht in engen Felsspalten mit Licht.",
        passage_text="Wegen der spirituellen Bedeutung führen Navajo-Guides durch die engen Gänge.",
    )
    assert query.startswith("Antelope Canyon")
    assert "narrow" in query
    assert "light" in query


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
