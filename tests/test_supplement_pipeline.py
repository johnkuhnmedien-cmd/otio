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
    acquire_top_candidates,
    acquire_top_candidates_for_folder,
    analyze_and_update_inventory_for_folder,
    analyze_supplement_asset,
    extend_folder_inventory,
    import_manual_supplement_asset,
    load_sidecar,
    run_full_supplement_pipeline_for_folder,
    save_sidecar,
    search_supplement_candidates,
)
from otio_app.services.supplement_requests import (
    load_supplement_requests,
    requests_for_folder,
    upsert_requests,
)
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


def test_pexels_search_caps_candidates_at_three(monkeypatch: pytest.MonkeyPatch) -> None:
    """Pro Szene/Request duerfen hoechstens 3 Kandidaten zur Auswahl stehen."""
    monkeypatch.setenv("PEXELS_API_KEY", "test-key")

    def make_video(video_id: int) -> bytes:
        return (
            b'{"id":%d,"url":"https://www.pexels.com/video/%d","image":"https://images.pexels.com/p.jpg",'
            b'"width":1920,"height":1080,"duration":10,'
            b'"user":{"name":"Creator","url":"https://www.pexels.com/@creator"},'
            b'"video_files":[{"id":1,"quality":"hd","file_type":"video/mp4","width":1920,"height":1080,'
            b'"fps":30,"link":"https://videos.pexels.com/%d.mp4"}]}' % (video_id, video_id, video_id)
        )

    class FakeResponse:
        status = 200
        headers = {"Content-Type": "application/json"}

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self):
            videos = b",".join(make_video(i) for i in range(6))
            return b'{"videos":[' + videos + b"]}"

    monkeypatch.setattr("urllib.request.urlopen", lambda *_args, **_kwargs: FakeResponse())
    request = SupplementRequest(
        supplement_request_id="supp_req_cap",
        section_id="section_antelope_canyon",
        folder_name="Antelope Canyon",
        location_name="Antelope Canyon",
        beat_id="beat",
        passage_text="Test",
        visual_requirement="Antelope Canyon",
        selected_source=SUPPLEMENT_SOURCE_PEXELS,
        required_asset_type="video",
    )
    candidates = PexelsAdapter().search(request)
    assert len(candidates) == 3


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


def test_pexels_http_error_surfaces_as_request_error_not_silent_zero(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Ein HTTP-Fehler (z. B. 401) darf nicht als stilles '0 gefunden' erscheinen."""
    project = _project(tmp_path)
    monkeypatch.setenv("PEXELS_API_KEY", "bad-key")

    import urllib.error

    def fail_urlopen(request, timeout=20):
        raise urllib.error.HTTPError(
            request.full_url, 401, "Unauthorized", {}, None
        )

    monkeypatch.setattr("urllib.request.urlopen", fail_urlopen)
    request = SupplementRequest(
        supplement_request_id="supp_req_http_error",
        section_id="section_antelope_canyon",
        folder_name="Antelope Canyon",
        location_name="Antelope Canyon",
        beat_id="beat",
        passage_text="Test",
        visual_requirement="Antelope Canyon",
        selected_source=SUPPLEMENT_SOURCE_PEXELS,
    )
    upsert_requests(project, [request])

    candidates = search_supplement_candidates(project, request)
    assert candidates == []

    updated = requests_for_folder(load_supplement_requests(project), "Antelope Canyon")[0]
    assert updated.status == "ACQUIRE_FAILED"
    assert "401" in updated.last_error

    from otio_app.project_layout import get_pexels_debug_report_path

    report = json.loads(get_pexels_debug_report_path(project.work_dir_path).read_text(encoding="utf-8"))
    assert report["errors"]
    assert report["errors"][0]["status"] == 401


def test_pexels_request_sends_browser_user_agent(monkeypatch: pytest.MonkeyPatch) -> None:
    """Pexels/Cloudflare blockiert den Standard-Python-User-Agent (HTTP 403, code 1010)."""
    monkeypatch.setenv("PEXELS_API_KEY", "test-key")
    captured_headers: list[dict] = []

    class FakeResponse:
        status = 200
        headers = {"Content-Type": "application/json"}

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self):
            return b'{"videos":[]}'

    def fake_urlopen(request, timeout=20):
        captured_headers.append(dict(request.header_items()))
        return FakeResponse()

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    request = SupplementRequest(
        supplement_request_id="supp_req_ua",
        section_id="section_antelope_canyon",
        folder_name="Antelope Canyon",
        beat_id="beat",
        passage_text="Test",
        visual_requirement="Antelope Canyon",
        selected_source=SUPPLEMENT_SOURCE_PEXELS,
        required_asset_type="video",
    )
    PexelsAdapter().search(request)
    assert captured_headers
    header_keys = {key.lower(): value for key, value in captured_headers[0].items()}
    assert "user-agent" in header_keys
    assert "python-urllib" not in header_keys["user-agent"].lower()


def test_pexels_adapter_does_not_raise_on_http_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PEXELS_API_KEY", "bad-key")
    import urllib.error

    def fail_urlopen(request, timeout=20):
        raise urllib.error.HTTPError(request.full_url, 429, "Too Many Requests", {}, None)

    monkeypatch.setattr("urllib.request.urlopen", fail_urlopen)
    request = SupplementRequest(
        supplement_request_id="supp_req_429",
        section_id="section_antelope_canyon",
        folder_name="Antelope Canyon",
        beat_id="beat",
        passage_text="Test",
        visual_requirement="Antelope Canyon",
        selected_source=SUPPLEMENT_SOURCE_PEXELS,
    )
    candidates = PexelsAdapter().search(request)
    assert candidates == []


def test_sidecar_accepts_float_pexels_aspect_ratio() -> None:
    """Regression: SupplementAssetSidecar.aspect_ratio darf kein str-only-Feld sein
    (Kollision mit Nano-Banana-Feld hat frueher Downloads mit float aspect_ratio blockiert)."""
    from otio_app.analysis_models import SupplementAssetSidecar

    sidecar = SupplementAssetSidecar(
        asset_id="asset_pexels_1",
        supplement_request_id="supp_req_1",
        provider=SUPPLEMENT_SOURCE_PEXELS,
        aspect_ratio=1.777778,
    )
    assert sidecar.aspect_ratio == pytest.approx(1.777778)


def _pexels_candidate(index: int, *, location_match: str = "exact") -> SupplementCandidate:
    return SupplementCandidate(
        candidate_id=f"cand_{index}",
        supplement_request_id="supp_req_auto",
        provider=SUPPLEMENT_SOURCE_PEXELS,
        provider_asset_id=str(1000 + index),
        title=f"Video {index}",
        download_url=f"https://videos.pexels.com/video-{index}.mp4",
        download_enabled=True,
        is_mock=False,
        location_match=location_match,
        media_type="video",
    )


def _mock_asset_for_candidate(candidate: SupplementCandidate, tmp_path: Path):
    from otio_app.analysis_models import SupplementAssetSidecar
    from otio_app.services.supplement_sources.base import SupplementAsset

    local = tmp_path / f"asset_{candidate.provider_asset_id}.mp4"
    local.write_bytes(b"video")
    sidecar = SupplementAssetSidecar(
        asset_id=f"asset_pexels_{candidate.provider_asset_id}",
        supplement_request_id=candidate.supplement_request_id,
        provider=SUPPLEMENT_SOURCE_PEXELS,
        provider_asset_id=candidate.provider_asset_id,
        local_path=str(local),
        rights_status=RIGHTS_STATUS_APPROVED,
    )
    return SupplementAsset(local_path=local, sidecar=sidecar)


def test_acquire_top_candidates_downloads_up_to_three(tmp_path: Path) -> None:
    project = _project(tmp_path)
    request = SupplementRequest(
        supplement_request_id="supp_req_auto",
        section_id="section_antelope_canyon",
        folder_name="Antelope Canyon",
        beat_id="beat",
        passage_text="Test",
        selected_source=SUPPLEMENT_SOURCE_PEXELS,
    )
    candidates = [_pexels_candidate(i) for i in range(5)]

    with patch.object(PexelsAdapter, "acquire") as mock_acquire:
        mock_acquire.side_effect = lambda candidate, _dest: _mock_asset_for_candidate(candidate, tmp_path)
        results = acquire_top_candidates(project, candidates, request, max_count=3)

    assert len(results) == 3
    assert all(asset is not None for _c, asset, _e in results)
    assert mock_acquire.call_count == 3


def test_acquire_top_candidates_downloads_fewer_when_available(tmp_path: Path) -> None:
    project = _project(tmp_path)
    request = SupplementRequest(
        supplement_request_id="supp_req_auto_two",
        section_id="section_antelope_canyon",
        folder_name="Antelope Canyon",
        beat_id="beat",
        passage_text="Test",
        selected_source=SUPPLEMENT_SOURCE_PEXELS,
    )
    candidates = [_pexels_candidate(i) for i in range(2)]

    with patch.object(PexelsAdapter, "acquire") as mock_acquire:
        mock_acquire.side_effect = lambda candidate, _dest: _mock_asset_for_candidate(candidate, tmp_path)
        results = acquire_top_candidates(project, candidates, request, max_count=3)

    assert len(results) == 2
    assert mock_acquire.call_count == 2


def test_acquire_top_candidates_with_single_candidate(tmp_path: Path) -> None:
    project = _project(tmp_path)
    request = SupplementRequest(
        supplement_request_id="supp_req_auto_one",
        section_id="section_antelope_canyon",
        folder_name="Antelope Canyon",
        beat_id="beat",
        passage_text="Test",
        selected_source=SUPPLEMENT_SOURCE_PEXELS,
    )
    candidates = [_pexels_candidate(0)]

    with patch.object(PexelsAdapter, "acquire") as mock_acquire:
        mock_acquire.side_effect = lambda candidate, _dest: _mock_asset_for_candidate(candidate, tmp_path)
        results = acquire_top_candidates(project, candidates, request, max_count=3)

    assert len(results) == 1
    assert results[0][1] is not None


def test_acquire_top_candidates_skips_missing_location_match(tmp_path: Path) -> None:
    project = _project(tmp_path)
    request = SupplementRequest(
        supplement_request_id="supp_req_auto_missing",
        section_id="section_antelope_canyon",
        folder_name="Antelope Canyon",
        beat_id="beat",
        passage_text="Test",
        selected_source=SUPPLEMENT_SOURCE_PEXELS,
    )
    candidates = [_pexels_candidate(0, location_match="missing"), _pexels_candidate(1)]

    with patch.object(PexelsAdapter, "acquire") as mock_acquire:
        mock_acquire.side_effect = lambda candidate, _dest: _mock_asset_for_candidate(candidate, tmp_path)
        results = acquire_top_candidates(project, candidates, request, max_count=3)

    assert len(results) == 1
    assert results[0][0].provider_asset_id == "1001"


def test_acquire_top_candidates_continues_after_individual_failure(tmp_path: Path) -> None:
    project = _project(tmp_path)
    request = SupplementRequest(
        supplement_request_id="supp_req_auto_fail",
        section_id="section_antelope_canyon",
        folder_name="Antelope Canyon",
        beat_id="beat",
        passage_text="Test",
        selected_source=SUPPLEMENT_SOURCE_PEXELS,
    )
    candidates = [_pexels_candidate(i) for i in range(3)]

    def flaky_acquire(candidate, _dest):
        if candidate.provider_asset_id == "1001":
            raise RuntimeError("Pexels-Download fehlgeschlagen: boom")
        return _mock_asset_for_candidate(candidate, tmp_path)

    with patch.object(PexelsAdapter, "acquire", side_effect=flaky_acquire):
        results = acquire_top_candidates(project, candidates, request, max_count=3)

    assert len(results) == 3
    successes = [r for r in results if r[1] is not None]
    failures = [r for r in results if r[1] is None]
    assert len(successes) == 2
    assert len(failures) == 1
    assert "boom" in failures[0][2]


def test_revalidate_supplement_asset_heuristic_fail_without_match() -> None:
    """Ohne Gemini muss die heuristische Prüfung ein unpassendes Asset ablehnen,
    statt es allein wegen Aspect-Ratio/Location als 'passend' gelten zu lassen."""
    from otio_app.services.supplement_pipeline import revalidate_supplement_asset_against_request

    request = SupplementRequest(
        supplement_request_id="supp_req_validate",
        section_id="section_antelope_canyon",
        folder_name="Antelope Canyon",
        beat_id="beat",
        passage_text="Ein Gewitter zieht Wasser tief in den Canyon.",
        visual_requirement="Gewitter über der Wüste und Wasser im Canyon",
    )
    with patch("otio_app.services.supplement_pipeline.is_gemini_configured", return_value=False):
        result = revalidate_supplement_asset_against_request(
            description="Ein ruhiger sonniger Strand mit Palmen und blauem Meer.",
            request=request,
        )
    assert result["status"] in {"FAIL", "NEEDS_USER_REVIEW"}


def test_revalidate_supplement_asset_heuristic_pass_with_overlap() -> None:
    from otio_app.services.supplement_pipeline import revalidate_supplement_asset_against_request

    request = SupplementRequest(
        supplement_request_id="supp_req_validate_ok",
        section_id="section_antelope_canyon",
        folder_name="Antelope Canyon",
        beat_id="beat",
        passage_text="Eine Person geht durch den engen Slot Canyon.",
        visual_requirement="Person narrow slot canyon walking",
    )
    with patch("otio_app.services.supplement_pipeline.is_gemini_configured", return_value=False):
        result = revalidate_supplement_asset_against_request(
            description="A person walking through a narrow slot canyon with sandstone walls.",
            request=request,
        )
    # Ohne Gemini ist dies nur eine Keyword-Heuristik — sie darf ein Asset mit
    # deutlicher Begriffsüberlappung nicht als FAIL ablehnen.
    assert result["status"] in {"WEAK_PASS", "PASS", "NEEDS_USER_REVIEW"}
    assert result["score"] > 0.3


def test_revalidate_supplement_asset_uses_gemini_when_configured() -> None:
    """Wenn Gemini konfiguriert ist, muss die echte Content-Validierung aufgerufen
    werden statt der Keyword-Heuristik."""
    from otio_app.services.supplement_pipeline import revalidate_supplement_asset_against_request

    request = SupplementRequest(
        supplement_request_id="supp_req_validate_gemini",
        section_id="section_antelope_canyon",
        folder_name="Antelope Canyon",
        beat_id="beat",
        passage_text="Ein Gewitter zieht Wasser tief in den Canyon.",
        visual_requirement="Gewitter über der Wüste und Wasser im Canyon",
    )
    with patch(
        "otio_app.services.supplement_pipeline.is_gemini_configured", return_value=True
    ), patch(
        "otio_app.services.supplement_pipeline.validate_supplement_asset_match",
        return_value={"status": "PASS", "score": 0.9, "reason": "Passt gut."},
    ) as mock_validate:
        result = revalidate_supplement_asset_against_request(
            description="Storm clouds over the desert with water flowing into the canyon.",
            request=request,
        )
    mock_validate.assert_called_once()
    assert result["status"] == "PASS"
    assert result["score"] == 0.9


def test_revalidate_supplement_asset_missing_request_needs_review() -> None:
    from otio_app.services.supplement_pipeline import revalidate_supplement_asset_against_request

    result = revalidate_supplement_asset_against_request(description="Some description", request=None)
    assert result["status"] == "NEEDS_USER_REVIEW"


def test_analyze_supplement_asset_sets_validation_from_request(tmp_path: Path) -> None:
    """analyze_supplement_asset muss die Sidecar-Validierung durch eine echte
    Prüfung gegen den ursprünglichen Satz ersetzen, nicht nur die
    Aspect-Ratio-Heuristik vom Download übernehmen."""
    project = _project(tmp_path)
    from otio_app.analysis_models import SupplementAssetSidecar

    request = SupplementRequest(
        supplement_request_id="supp_req_analyze",
        section_id="section_antelope_canyon",
        folder_name="Antelope Canyon",
        beat_id="beat",
        passage_text="Ein Gewitter zieht Wasser tief in den Canyon.",
        visual_requirement="Gewitter über der Wüste und Wasser im Canyon",
    )
    upsert_requests(project, [request])

    media_path = tmp_path / "clip.mp4"
    media_path.write_bytes(b"fake-video-bytes")
    sidecar = SupplementAssetSidecar(
        asset_id="asset_pexels_999",
        supplement_request_id=request.supplement_request_id,
        provider=SUPPLEMENT_SOURCE_PEXELS,
        local_path=str(media_path),
        rights_status=RIGHTS_STATUS_APPROVED,
        # Sidecar behauptet PASS allein aufgrund von Aspect Ratio/Location — muss
        # durch die inhaltliche Prüfung ersetzt werden.
        supplement_validation_status="PASS",
        approved_for_cut_plan=True,
    )

    with patch(
        "otio_app.services.supplement_pipeline.extract_frames",
        return_value=[tmp_path / "frame1.jpg"],
    ), patch(
        "otio_app.services.supplement_pipeline.is_gemini_configured",
        return_value=False,
    ):
        asset = analyze_supplement_asset(
            project,
            folder_name="Antelope Canyon",
            local_path=media_path,
            sidecar=sidecar,
        )

    # Ohne Gemini-Beschreibung (Platzhaltertext) darf das Asset nicht automatisch
    # als PASS/approved gelten.
    assert asset.approved_for_cut_plan is False
    assert asset.supplement_validation_status != "PASS" or not asset.approved_for_cut_plan

    reloaded_sidecar = load_sidecar(media_path)
    assert reloaded_sidecar is not None
    assert reloaded_sidecar.approved_for_cut_plan == asset.approved_for_cut_plan


def test_extend_folder_inventory_rejects_non_pass_supplement_asset(tmp_path: Path) -> None:
    project = _project(tmp_path)
    asset = AssetMediaAnalysis(
        path=str(tmp_path / "bad.mp4"),
        description="Ein ruhiger Strand.",
        frames_used=[str(tmp_path / "frame.jpg")],
        asset_id="asset_bad",
        asset_origin="pexels",
        rights_status=RIGHTS_STATUS_APPROVED,
        analysis_status="complete",
        supplement_validation_status="NEEDS_USER_REVIEW",
        approved_for_cut_plan=False,
    )
    Path(asset.path).write_bytes(b"valid-media")
    from otio_app.analysis_models import SupplementAssetSidecar

    save_sidecar(
        SupplementAssetSidecar(
            asset_id=asset.asset_id,
            supplement_request_id="supp_req_bad",
            provider="pexels",
            local_path=asset.path,
            rights_status=RIGHTS_STATUS_APPROVED,
        )
    )
    with pytest.raises(ValueError, match="nicht.*freigegeben"):
        extend_folder_inventory(project, folder_name="Antelope Canyon", asset=asset)


def test_acquire_top_candidates_for_folder_downloads_across_open_requests(tmp_path: Path) -> None:
    project = _project(tmp_path)
    request_a = SupplementRequest(
        supplement_request_id="supp_req_folder_a",
        section_id="section_antelope_canyon",
        folder_name="Antelope Canyon",
        beat_id="beat_a",
        passage_text="Test A",
        visual_requirement="Antelope Canyon narrow",
    )
    request_b = SupplementRequest(
        supplement_request_id="supp_req_folder_b",
        section_id="section_antelope_canyon",
        folder_name="Antelope Canyon",
        beat_id="beat_b",
        passage_text="Test B",
        visual_requirement="Antelope Canyon sandstone",
    )
    upsert_requests(project, [request_a, request_b])

    def fake_search(_project, request):
        return [_pexels_candidate(0), _pexels_candidate(1)]

    with patch.object(PexelsAdapter, "readiness") as mock_readiness, patch(
        "otio_app.services.supplement_pipeline.search_supplement_candidates",
        side_effect=fake_search,
    ), patch.object(PexelsAdapter, "acquire") as mock_acquire:
        from otio_app.services.supplement_sources.base import ProviderReadiness

        mock_readiness.return_value = ProviderReadiness(
            provider=SUPPLEMENT_SOURCE_PEXELS,
            status="READY",
            message="ok",
            acquire_enabled=True,
        )
        mock_acquire.side_effect = lambda candidate, _dest: _mock_asset_for_candidate(candidate, tmp_path)
        summary = acquire_top_candidates_for_folder(project, "Antelope Canyon", max_per_request=3)

    assert len(summary) == 2
    assert all(not entry["skipped"] for entry in summary)
    assert all(entry["downloaded"] == 2 for entry in summary)


def test_acquire_top_candidates_for_folder_skips_already_acquired(tmp_path: Path) -> None:
    project = _project(tmp_path)
    request = SupplementRequest(
        supplement_request_id="supp_req_folder_done",
        section_id="section_antelope_canyon",
        folder_name="Antelope Canyon",
        beat_id="beat",
        passage_text="Test",
        status="ANALYSIS_COMPLETE",
    )
    upsert_requests(project, [request])

    summary = acquire_top_candidates_for_folder(project, "Antelope Canyon", max_per_request=3)
    assert len(summary) == 1
    assert summary[0]["skipped"] is True


def test_acquire_top_candidates_for_folder_processes_stale_selected_source(tmp_path: Path) -> None:
    """Regression: st.tabs() rendert alle Tabs im selben Skriptlauf, wodurch
    selected_source frueher auf den zuletzt gerenderten Tab (z. B. 'manual')
    ueberschrieben wurde, obwohl kein Asset uebernommen wurde. Solche Requests
    duerfen vom Ordner-Auto-Download nicht faelschlich uebersprungen werden."""
    project = _project(tmp_path)
    request = SupplementRequest(
        supplement_request_id="supp_req_folder_stale",
        section_id="section_antelope_canyon",
        folder_name="Antelope Canyon",
        beat_id="beat",
        passage_text="Test",
        selected_source=SUPPLEMENT_SOURCE_GOOGLE,
        status="PENDING_SOURCE_SELECTION",
    )
    upsert_requests(project, [request])

    def fake_search(_project, req):
        return [_pexels_candidate(0)]

    with patch.object(PexelsAdapter, "readiness") as mock_readiness, patch(
        "otio_app.services.supplement_pipeline.search_supplement_candidates",
        side_effect=fake_search,
    ), patch.object(PexelsAdapter, "acquire") as mock_acquire:
        from otio_app.services.supplement_sources.base import ProviderReadiness

        mock_readiness.return_value = ProviderReadiness(
            provider=SUPPLEMENT_SOURCE_PEXELS,
            status="READY",
            message="ok",
            acquire_enabled=True,
        )
        mock_acquire.side_effect = lambda candidate, _dest: _mock_asset_for_candidate(candidate, tmp_path)
        summary = acquire_top_candidates_for_folder(project, "Antelope Canyon", max_per_request=3)

    assert len(summary) == 1
    assert summary[0]["skipped"] is False
    assert summary[0]["downloaded"] == 1


def test_run_full_supplement_pipeline_downloads_analyzes_and_updates_inventory(
    tmp_path: Path,
) -> None:
    """Ein-Klick-Ablauf: Suchen + Herunterladen + Analysieren + Inventory in einem
    Aufruf für alle offenen Anfragen eines Ordners."""
    project = _project(tmp_path)
    request = SupplementRequest(
        supplement_request_id="supp_req_full_pipeline",
        section_id="section_antelope_canyon",
        folder_name="Antelope Canyon",
        beat_id="beat",
        passage_text="Eine Person geht durch den engen Slot Canyon.",
        visual_requirement="Person narrow slot canyon walking",
    )
    upsert_requests(project, [request])

    def fake_search(_project, req):
        return [_pexels_candidate(0)]

    def fake_acquire(candidate, _dest):
        asset = _mock_asset_for_candidate(candidate, tmp_path)
        # Datei muss im tatsächlichen Provider-Ordner liegen, damit die
        # Analyse-/Inventory-Schleife sie findet.
        real_dir = project.project_root_path / "Antelope Canyon" / "_supplemental" / "_pexels"
        real_dir.mkdir(parents=True, exist_ok=True)
        real_path = real_dir / asset.local_path.name
        real_path.write_bytes(asset.local_path.read_bytes())
        return asset.__class__(
            local_path=real_path,
            sidecar=asset.sidecar.model_copy(update={"local_path": str(real_path)}),
        )

    with patch.object(PexelsAdapter, "readiness") as mock_readiness, patch(
        "otio_app.services.supplement_pipeline.search_supplement_candidates",
        side_effect=fake_search,
    ), patch.object(PexelsAdapter, "acquire", side_effect=fake_acquire), patch(
        "otio_app.services.supplement_pipeline.extract_frames",
        return_value=[tmp_path / "frame1.jpg"],
    ), patch(
        "otio_app.services.supplement_pipeline.is_gemini_configured",
        return_value=False,
    ):
        from otio_app.services.supplement_sources.base import ProviderReadiness

        mock_readiness.return_value = ProviderReadiness(
            provider=SUPPLEMENT_SOURCE_PEXELS,
            status="READY",
            message="ok",
            acquire_enabled=True,
        )
        result = run_full_supplement_pipeline_for_folder(project, "Antelope Canyon", max_per_request=3)

    assert result["total_downloaded"] == 1
    assert result["analyzed"] == 1
    # Ohne Gemini bleibt die Beschreibung ein Platzhalter → keine PASS-Validierung
    # → korrekt NICHT ins Inventory übernommen (kein falsches Grün).
    assert result["inventory_added"] == 0
    assert result["inventory_skipped"]


def test_analyze_and_update_inventory_for_folder_returns_untouched_when_empty(
    tmp_path: Path,
) -> None:
    project = _project(tmp_path)
    result = analyze_and_update_inventory_for_folder(project, "Antelope Canyon")
    assert result["touched"] is False
    assert result["analyzed"] == 0
    assert result["inventory_added"] == 0


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
