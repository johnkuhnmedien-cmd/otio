"""Validation Repair Phase 6/7: automatische Auflösung eines einzelnen
Validation-Repair-Requests (BLACK_GAP mit Foto-first-Suche + Fenster-
Reparatur, ASSET_REUSE_DISTANCE mit vollem Segment-Ersatz)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from otio_app.analysis_models import SupplementCandidate
from otio_app.models import Project, ProjectMode
from otio_app.services.voiceover_generation.cut_plan_builder import load_cut_plan_draft, save_cut_plan_draft
from otio_app.services.voiceover_generation.cut_plan_models import (
    CutPlanDocument,
    CutPlanItem,
    CutPlanSourceRef,
    VisualSegment,
)
from otio_app.services.voiceover_generation.cut_plan_supplement_bridge import (
    record_supplement_manifest_entry,
)
from otio_app.services.voiceover_generation.cut_plan_supplement_models import (
    CutPlanSupplementAsset,
    CutPlanSupplementManifestEntry,
)
from otio_app.services.voiceover_generation.cut_plan_validation_repair import (
    load_cut_plan_validation_repair_requests,
    save_cut_plan_validation_repair_requests,
)
from otio_app.services.voiceover_generation.cut_plan_validation_repair_models import (
    CutPlanValidationRepairRequest,
    CutPlanValidationRepairRequestsDocument,
)
from otio_app.services.voiceover_generation.cut_plan_validation_repair_resolve_service import (
    auto_resolve_all_validation_repair_requests,
    auto_resolve_validation_repair_request,
)

_MODULE = "otio_app.services.voiceover_generation.cut_plan_validation_repair_resolve_service"
FOLDER_A = "Grand Canyon"


def _make_project(tmp_path: Path) -> Project:
    project_root = tmp_path / "USA"
    (project_root / FOLDER_A).mkdir(parents=True)
    return Project(
        id="repair-resolve-project",
        name="Repair Resolve Test",
        project_root=str(project_root),
        work_dir=str(project_root / "_otio"),
        project_mode=ProjectMode.WITHOUT_VOICEOVER,
        asset_subdir_names=[FOLDER_A],
        selected_asset_subdirs=[FOLDER_A],
    )


_SNAPSHOT = {"shot_min_sec": 2.0, "shot_max_sec": 10.0, "video_head_trim_sec": 1.0}


def _segment(**overrides) -> VisualSegment:
    defaults = dict(
        segment_id="seg", timeline_in_sec=0.0, timeline_out_sec=5.0, duration_sec=5.0,
        asset_id="asset_a", asset_path="/fake/a.jpg", asset_type="image", source_in_sec=0.0,
        source_out_sec=5.0, track="V1", reason="primary_asset",
    )
    defaults.update(overrides)
    return VisualSegment(**defaults)


def _item(**overrides) -> CutPlanItem:
    defaults = dict(
        cut_item_id="cut_gap", source_refs=[CutPlanSourceRef(source_sentence_id="s1", text="Text")],
        source_scope="folder", folder_name=FOLDER_A, text="Ein Satz.", visual_intent="Weiter Blick.",
        timeline_start_sec=0.0, timeline_end_sec=5.0, duration_sec=5.0,
        chosen_asset_id="", asset_selection_status="SUPPLEMENT_REQUIRED",
    )
    defaults.update(overrides)
    return CutPlanItem(**defaults)


def _setup_black_gap(tmp_path: Path, *, gap_start=5.0, gap_end=5.4) -> tuple[Project, str]:
    project = _make_project(tmp_path)
    prev_item = _item(cut_item_id="cut_prev", planned_visual_segments=[
        _segment(segment_id="seg_prev", timeline_in_sec=0.0, timeline_out_sec=gap_start, duration_sec=gap_start)
    ], timeline_start_sec=0.0, timeline_end_sec=gap_start, duration_sec=gap_start)
    target_item = _item(cut_item_id="cut_gap", timeline_start_sec=gap_start, timeline_end_sec=gap_end,
                         duration_sec=gap_end - gap_start)
    next_item = _item(cut_item_id="cut_next", planned_visual_segments=[
        _segment(segment_id="seg_next", timeline_in_sec=gap_end, timeline_out_sec=gap_end + 5.0, duration_sec=5.0)
    ], timeline_start_sec=gap_end, timeline_end_sec=gap_end + 5.0, duration_sec=5.0)

    cut_plan = CutPlanDocument(
        project_id=project.id, items=[prev_item, target_item, next_item], settings_snapshot=dict(_SNAPSHOT)
    )
    save_cut_plan_draft(project, cut_plan)

    repair_request = CutPlanValidationRepairRequest(
        repair_id="repair_black_gap_cut_gap", repair_type="BLACK_GAP", cut_item_id="cut_gap",
        source_scope="folder", folder_name=FOLDER_A, text="Ein Satz.", visual_intent="Weiter Blick.",
        gap_start_sec=gap_start, gap_end_sec=gap_end,
    )
    requests_document = CutPlanValidationRepairRequestsDocument(project_id=project.id, requests=[repair_request])
    save_cut_plan_validation_repair_requests(project, requests_document)
    return project, repair_request.repair_id


def _setup_asset_reuse_distance(tmp_path: Path) -> tuple[Project, str]:
    project = _make_project(tmp_path)
    item = _item(
        cut_item_id="cut_reused", duration_sec=5.0, timeline_start_sec=0.0, timeline_end_sec=5.0,
        chosen_asset_id="asset_overused", asset_selection_status="PRIMARY_USED",
        planned_visual_segments=[
            _segment(segment_id="cut_reused_seg_01", timeline_in_sec=0.0, timeline_out_sec=5.0, duration_sec=5.0,
                      asset_id="asset_overused", asset_type="video", asset_path="/fake/overused.mp4",
                      source_in_sec=1.0, source_out_sec=6.0)
        ],
    )
    cut_plan = CutPlanDocument(project_id=project.id, items=[item], settings_snapshot=dict(_SNAPSHOT))
    save_cut_plan_draft(project, cut_plan)

    repair_request = CutPlanValidationRepairRequest(
        repair_id="repair_asset_reuse_distance_cut_reused", repair_type="ASSET_REUSE_DISTANCE",
        cut_item_id="cut_reused", source_scope="folder", folder_name=FOLDER_A, text="Ein Satz.",
        visual_intent="Weiter Blick.", needed_duration_sec=5.0,
    )
    requests_document = CutPlanValidationRepairRequestsDocument(project_id=project.id, requests=[repair_request])
    save_cut_plan_validation_repair_requests(project, requests_document)
    return project, repair_request.repair_id


def _manifest_entry(**overrides) -> CutPlanSupplementManifestEntry:
    defaults = dict(
        asset_id="supplement_pexels_1", provider="pexels", provider_asset_id="1",
        asset_path="/fake/manifest_asset.jpg", asset_type="image", duration_sec=0.0,
        folder_name=FOLDER_A,
    )
    defaults.update(overrides)
    return CutPlanSupplementManifestEntry(**defaults)


def _raw_candidate(candidate_id: str, *, provider_asset_id: str, media_type: str) -> SupplementCandidate:
    return SupplementCandidate(
        candidate_id=candidate_id,
        supplement_request_id="",
        provider="pexels",
        provider_asset_id=provider_asset_id,
        media_type=media_type,
        width=1920,
        height=1080,
        duration_sec=10.0 if media_type == "video" else 0.0,
        download_url="",
        download_enabled=True,
        is_mock=False,
        requires_user_approval=False,
        license="pexels",
        source_page_url=f"https://pexels.com/{candidate_id}",
        folder_name=FOLDER_A,
        match_score=1.0,
        title=f"Fake {candidate_id}",
    )


def _fake_asset(candidate_id: str, *, asset_type: str = "image", asset_path: str = "/fake/repair.jpg") -> CutPlanSupplementAsset:
    return CutPlanSupplementAsset(
        asset_id=f"supplement_pexels_{candidate_id}", request_id="repair", candidate_id=candidate_id,
        provider="pexels", asset_path=asset_path, asset_type=asset_type, duration_sec=10.0,
    )


def _analysis(status: str, **overrides) -> dict:
    defaults = dict(description="Beschreibung", status=status, score=0.9, reason="Passt.")
    defaults.update(overrides)
    return defaults


# --- UNSAFE_TO_REPAIR ---


def test_unsafe_to_repair_when_neighbors_lack_room(tmp_path: Path) -> None:
    """Beide Nachbarn sind bereits am shot_min_sec-Minimum -> keine sichere
    Reparatur möglich, keine Suche wird überhaupt ausgelöst."""
    project = _make_project(tmp_path)
    prev_item = _item(cut_item_id="cut_prev", planned_visual_segments=[
        _segment(segment_id="seg_prev", timeline_in_sec=0.0, timeline_out_sec=2.0, duration_sec=2.0)
    ])
    target_item = _item(cut_item_id="cut_gap", timeline_start_sec=2.0, timeline_end_sec=2.4, duration_sec=0.4)
    next_item = _item(cut_item_id="cut_next", planned_visual_segments=[
        _segment(segment_id="seg_next", timeline_in_sec=2.4, timeline_out_sec=4.4, duration_sec=2.0)
    ])
    cut_plan = CutPlanDocument(
        project_id=project.id, items=[prev_item, target_item, next_item], settings_snapshot=dict(_SNAPSHOT)
    )
    save_cut_plan_draft(project, cut_plan)
    repair_request = CutPlanValidationRepairRequest(
        repair_id="repair_black_gap_cut_gap", repair_type="BLACK_GAP", cut_item_id="cut_gap",
        source_scope="folder", folder_name=FOLDER_A, gap_start_sec=2.0, gap_end_sec=2.4,
    )
    save_cut_plan_validation_repair_requests(
        project, CutPlanValidationRepairRequestsDocument(project_id=project.id, requests=[repair_request])
    )

    with patch(f"{_MODULE}.get_supplement_adapter") as mock_get_adapter:
        result = auto_resolve_validation_repair_request(project, "repair_black_gap_cut_gap")

    assert result.status == "UNSAFE_TO_REPAIR"
    mock_get_adapter.assert_not_called()
    reloaded = load_cut_plan_validation_repair_requests(project)
    assert reloaded.requests[0].status == "UNSAFE_TO_REPAIR"


# --- Lokale Wiederverwendung ---


def test_black_gap_accepted_via_local_reuse_without_external_search(tmp_path: Path) -> None:
    project, repair_id = _setup_black_gap(tmp_path)
    image_path = tmp_path / "reusable.jpg"
    image_path.write_bytes(b"FAKE_IMAGE")
    record_supplement_manifest_entry(project, _manifest_entry(asset_path=str(image_path), asset_type="image"))

    with (
        patch(f"{_MODULE}.get_supplement_adapter") as mock_get_adapter,
        patch(
            f"{_MODULE}.download_cut_plan_supplement_candidate",
            side_effect=lambda p, rid, c: _fake_asset(c.candidate_id, asset_type="image", asset_path=str(image_path)),
        ),
        patch(f"{_MODULE}._describe_and_validate_repair_asset", return_value=_analysis("PASS")),
    ):
        result = auto_resolve_validation_repair_request(project, repair_id)

    assert result.status == "ACCEPTED"
    mock_get_adapter.assert_not_called()

    draft = load_cut_plan_draft(project)
    target_item = next(item for item in draft.items if item.cut_item_id == "cut_gap")
    assert len(target_item.planned_visual_segments) == 1
    assert target_item.planned_visual_segments[0].reason == "black_gap_repair_supplement"


def test_black_gap_prefers_image_over_video_among_local_reuse_candidates(tmp_path: Path) -> None:
    project, repair_id = _setup_black_gap(tmp_path)
    image_path = tmp_path / "image.jpg"
    image_path.write_bytes(b"FAKE_IMAGE")
    video_path = tmp_path / "video.mp4"
    video_path.write_bytes(b"FAKE_VIDEO")
    record_supplement_manifest_entry(
        project, _manifest_entry(asset_id="supplement_pexels_video", provider_asset_id="video_1",
                                  asset_path=str(video_path), asset_type="video", duration_sec=10.0)
    )
    record_supplement_manifest_entry(
        project, _manifest_entry(asset_id="supplement_pexels_image", provider_asset_id="image_1",
                                  asset_path=str(image_path), asset_type="image")
    )

    accepted_paths: list[str] = []

    def _fake_download(project_arg, rid, candidate):
        accepted_paths.append(candidate.asset_type)
        path = image_path if candidate.asset_type == "image" else video_path
        return _fake_asset(candidate.candidate_id, asset_type=candidate.asset_type, asset_path=str(path))

    with (
        patch(f"{_MODULE}.get_supplement_adapter") as mock_get_adapter,
        patch(f"{_MODULE}.download_cut_plan_supplement_candidate", side_effect=_fake_download),
        patch(f"{_MODULE}._describe_and_validate_repair_asset", return_value=_analysis("PASS")),
    ):
        result = auto_resolve_validation_repair_request(project, repair_id)

    assert result.status == "ACCEPTED"
    mock_get_adapter.assert_not_called()
    assert accepted_paths[0] == "image"  # Foto wurde vor Video versucht


# --- Externe Suche: Foto-first für BLACK_GAP ---


def test_black_gap_external_search_tries_photo_before_video(tmp_path: Path) -> None:
    project, repair_id = _setup_black_gap(tmp_path)

    searched_asset_types: list[str] = []

    def _fake_adapter_factory(provider):
        adapter = MagicMock()

        def _search(request):
            searched_asset_types.append(request.required_asset_type)
            if request.required_asset_type == "image":
                return [_raw_candidate("cand_img", provider_asset_id="img_1", media_type="image")]
            return [_raw_candidate("cand_vid", provider_asset_id="vid_1", media_type="video")]

        adapter.search.side_effect = _search
        return adapter

    with (
        patch(f"{_MODULE}.get_supplement_adapter", side_effect=_fake_adapter_factory),
        patch(
            f"{_MODULE}.download_cut_plan_supplement_candidate",
            side_effect=lambda p, rid, c: _fake_asset(c.candidate_id, asset_type=c.asset_type),
        ),
        patch(f"{_MODULE}._describe_and_validate_repair_asset", return_value=_analysis("PASS")),
    ):
        result = auto_resolve_validation_repair_request(project, repair_id)

    assert result.status == "ACCEPTED"
    assert searched_asset_types[0] == "image"  # erste Suchstufe ist Foto, nicht Video
    assert result.attempts[0].asset_type == "image"


def test_asset_reuse_distance_external_search_tries_video_before_photo(tmp_path: Path) -> None:
    project, repair_id = _setup_asset_reuse_distance(tmp_path)

    searched_asset_types: list[str] = []

    def _fake_adapter_factory(provider):
        adapter = MagicMock()

        def _search(request):
            searched_asset_types.append(request.required_asset_type)
            if request.required_asset_type == "video":
                return [_raw_candidate("cand_vid", provider_asset_id="vid_1", media_type="video")]
            return [_raw_candidate("cand_img", provider_asset_id="img_1", media_type="image")]

        adapter.search.side_effect = _search
        return adapter

    with (
        patch(f"{_MODULE}.get_supplement_adapter", side_effect=_fake_adapter_factory),
        patch(
            f"{_MODULE}.download_cut_plan_supplement_candidate",
            side_effect=lambda p, rid, c: _fake_asset(c.candidate_id, asset_type=c.asset_type, asset_path="/fake/new.mp4"),
        ),
        patch(f"{_MODULE}._describe_and_validate_repair_asset", return_value=_analysis("PASS")),
    ):
        result = auto_resolve_validation_repair_request(project, repair_id)

    assert result.status == "ACCEPTED"
    assert searched_asset_types[0] == "video"


def test_asset_reuse_distance_accepted_replaces_item_segment(tmp_path: Path) -> None:
    project, repair_id = _setup_asset_reuse_distance(tmp_path)

    with (
        patch(f"{_MODULE}.get_supplement_adapter") as mock_get_adapter,
        patch(
            f"{_MODULE}.download_cut_plan_supplement_candidate",
            side_effect=lambda p, rid, c: _fake_asset(c.candidate_id, asset_type="video", asset_path="/fake/new.mp4"),
        ),
        patch(f"{_MODULE}._describe_and_validate_repair_asset", return_value=_analysis("PASS")),
    ):
        mock_get_adapter.return_value.search.return_value = [
            _raw_candidate("cand_new", provider_asset_id="new_1", media_type="video")
        ]
        result = auto_resolve_validation_repair_request(project, repair_id)

    assert result.status == "ACCEPTED"
    draft = load_cut_plan_draft(project)
    item = next(i for i in draft.items if i.cut_item_id == "cut_reused")
    assert len(item.planned_visual_segments) == 1
    assert item.chosen_asset_id != "asset_overused"
    assert item.asset_selection_status == "SUPPLEMENT_USED"


# --- NO_MATCH ---


def test_no_match_when_no_candidate_passes(tmp_path: Path) -> None:
    project, repair_id = _setup_black_gap(tmp_path)

    with (
        patch(f"{_MODULE}.get_supplement_adapter") as mock_get_adapter,
        patch(
            f"{_MODULE}.download_cut_plan_supplement_candidate",
            side_effect=lambda p, rid, c: _fake_asset(c.candidate_id),
        ),
        patch(f"{_MODULE}._describe_and_validate_repair_asset", return_value=_analysis("FAIL")),
    ):
        mock_get_adapter.return_value.search.return_value = [
            _raw_candidate("cand_1", provider_asset_id="a1", media_type="image")
        ]
        result = auto_resolve_validation_repair_request(project, repair_id)

    assert result.status == "NO_MATCH"
    reloaded = load_cut_plan_validation_repair_requests(project)
    assert reloaded.requests[0].status == "NO_MATCH"


# --- ACCEPT_FAILED -> nächster Kandidat ---


def test_apply_failure_tries_next_candidate(tmp_path: Path) -> None:
    project, repair_id = _setup_black_gap(tmp_path)

    call_count = {"n": 0}

    def _apply_side_effect(*args, **kwargs):
        call_count["n"] += 1
        if call_count["n"] == 1:
            raise ValueError("Reparatur-Kandidat zu kurz.")
        return load_cut_plan_draft(project)

    with (
        patch(f"{_MODULE}.get_supplement_adapter") as mock_get_adapter,
        patch(
            f"{_MODULE}.download_cut_plan_supplement_candidate",
            side_effect=lambda p, rid, c: _fake_asset(c.candidate_id),
        ),
        patch(f"{_MODULE}._describe_and_validate_repair_asset", return_value=_analysis("PASS")),
        patch(f"{_MODULE}.apply_black_gap_repair", side_effect=_apply_side_effect),
    ):
        mock_get_adapter.return_value.search.return_value = [
            _raw_candidate("cand_1", provider_asset_id="a1", media_type="image"),
            _raw_candidate("cand_2", provider_asset_id="a2", media_type="image"),
        ]
        result = auto_resolve_validation_repair_request(project, repair_id)

    assert result.status == "ACCEPTED"
    assert len(result.attempts) == 2
    assert result.attempts[0].validation_status == "ACCEPT_FAILED"
    assert result.attempts[1].validation_status == "PASS"


# --- Batch ---


def test_batch_skips_already_accepted_requests(tmp_path: Path) -> None:
    project, repair_id = _setup_black_gap(tmp_path)
    requests_document = load_cut_plan_validation_repair_requests(project)
    already_accepted = requests_document.requests[0].model_copy(
        update={"repair_id": "repair_already_done", "status": "ACCEPTED"}
    )
    save_cut_plan_validation_repair_requests(
        project, requests_document.model_copy(update={"requests": [requests_document.requests[0], already_accepted]})
    )

    with (
        patch(f"{_MODULE}.get_supplement_adapter") as mock_get_adapter,
        patch(
            f"{_MODULE}.download_cut_plan_supplement_candidate",
            side_effect=lambda p, rid, c: _fake_asset(c.candidate_id),
        ),
        patch(f"{_MODULE}._describe_and_validate_repair_asset", return_value=_analysis("PASS")),
    ):
        mock_get_adapter.return_value.search.return_value = [
            _raw_candidate("cand_1", provider_asset_id="a1", media_type="image")
        ]
        results = auto_resolve_all_validation_repair_requests(project)

    assert len(results) == 1
    assert results[0].repair_id == repair_id


def test_raises_when_repair_request_not_found(tmp_path: Path) -> None:
    project, _ = _setup_black_gap(tmp_path)
    with pytest.raises(ValueError, match="nicht gefunden"):
        auto_resolve_validation_repair_request(project, "repair_does_not_exist")


def test_raises_when_no_requests_document(tmp_path: Path) -> None:
    project = _make_project(tmp_path)
    with pytest.raises(ValueError, match="Keine Validation Repair Requests"):
        auto_resolve_validation_repair_request(project, "repair_x")
