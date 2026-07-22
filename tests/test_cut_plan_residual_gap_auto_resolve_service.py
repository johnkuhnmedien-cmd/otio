"""Commit 5: Residual Gap Auto-Resolve — lokale Wiederverwendung zuerst,
dann externe Provider-Suche mit Gemini-Prüfung, akzeptiert den ersten
PASS-Kandidaten. Alle externen Aufrufe (Adapter, Gemini) sind gemockt."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

from otio_app.analysis_models import SupplementCandidate
from otio_app.models import Project, ProjectMode
from otio_app.services.voiceover_generation.cut_plan_builder import load_cut_plan_draft, save_cut_plan_draft
from otio_app.services.voiceover_generation.cut_plan_models import (
    CutPlanDocument,
    CutPlanItem,
    CutPlanSourceRef,
    VisualSegment,
)
from otio_app.services.voiceover_generation.cut_plan_residual_gap_auto_resolve_service import (
    AUTO_RESOLVE_STATUS_ACCEPTED,
    AUTO_RESOLVE_STATUS_NO_MATCH,
    auto_resolve_all_residual_gap_requests,
    auto_resolve_residual_gap_request,
)
from otio_app.services.voiceover_generation.cut_plan_residual_gap_requests import (
    build_residual_gap_requests_from_cut_plan,
    load_residual_gap_requests,
    save_residual_gap_requests,
)
from otio_app.services.voiceover_generation.cut_plan_supplement_models import CutPlanSupplementManifestEntry

FOLDER_A = "Grand Canyon"
_MODULE = "otio_app.services.voiceover_generation.cut_plan_residual_gap_auto_resolve_service"


def _make_project(tmp_path: Path) -> Project:
    project_root = tmp_path / "USA"
    (project_root / FOLDER_A).mkdir(parents=True)
    return Project(
        id="cut-plan-residual-gap-resolve-project",
        name="Residual Gap Resolve Test",
        project_root=str(project_root),
        work_dir=str(project_root / "_otio"),
        project_mode=ProjectMode.WITHOUT_VOICEOVER,
        asset_subdir_names=[FOLDER_A],
        selected_asset_subdirs=[FOLDER_A],
    )


def _item(**overrides) -> CutPlanItem:
    defaults = dict(
        cut_item_id="cut_1", source_refs=[CutPlanSourceRef(source_sentence_id="s1", text="Text")],
        source_scope="folder", folder_name=FOLDER_A, text="Ein Satz über den Grand Canyon.",
        visual_intent="wide canyon shot", timeline_start_sec=0.0, timeline_end_sec=5.0, duration_sec=5.0,
        chosen_asset_id="supplement_pexels_1", asset_selection_status="SUPPLEMENT_USED",
    )
    defaults.update(overrides)
    return CutPlanItem(**defaults)


def _segment(**overrides) -> VisualSegment:
    defaults = dict(
        segment_id="seg_1", timeline_in_sec=0.0, timeline_out_sec=5.0, duration_sec=5.0,
        asset_id="supplement_pexels_1", asset_path="/fake/a.jpg", asset_type="image", source_in_sec=0.0,
        source_out_sec=5.0, track="V1", reason="supplement_asset",
    )
    defaults.update(overrides)
    return VisualSegment(**defaults)


def _setup_residual_request(tmp_path: Path) -> tuple[Project, str]:
    project = _make_project(tmp_path)
    item = _item(planned_visual_segments=[_segment(timeline_in_sec=0.0, timeline_out_sec=5.0)])
    next_item = _item(cut_item_id="cut_2", timeline_start_sec=20.0, timeline_end_sec=25.0, planned_visual_segments=[])
    cut_plan = CutPlanDocument(
        project_id=project.id, items=[item, next_item],
        settings_snapshot={
            "extend_visual_window_to_next_sentence": True, "max_sentence_pause_extension_sec": 15.0,
            "shot_max_sec": 10.0,
        },
    )
    save_cut_plan_draft(project, cut_plan)
    document = build_residual_gap_requests_from_cut_plan(project, cut_plan)
    assert len(document.requests) == 1
    save_residual_gap_requests(project, document)
    return project, document.requests[0].request_id


def _fake_candidate(**overrides) -> SupplementCandidate:
    defaults = dict(
        candidate_id="cand_1", supplement_request_id="residual_cut_1", provider="pexels", provider_asset_id="999",
        title="Canyon", media_type="image", width=1920, height=1080, duration_sec=0.0,
        download_url="https://example.com/fake.jpg", download_enabled=True, is_mock=False,
        requires_user_approval=False, match_score=0.9, folder_name=FOLDER_A,
    )
    defaults.update(overrides)
    return SupplementCandidate(**defaults)


# --- auto_resolve_residual_gap_request ---


def test_auto_resolve_skips_when_already_accepted(tmp_path: Path) -> None:
    project, request_id = _setup_residual_request(tmp_path)
    document = load_residual_gap_requests(project)
    document = document.model_copy(
        update={
            "requests": [
                r.model_copy(update={"accepted_asset_id": "existing", "accepted_asset_path": "/fake/x.jpg"})
                for r in document.requests
            ]
        }
    )
    save_residual_gap_requests(project, document)

    with patch(f"{_MODULE}.find_reusable_local_supplement_candidates") as mock_reuse:
        result = auto_resolve_residual_gap_request(project, request_id)

    mock_reuse.assert_not_called()
    assert result.status == AUTO_RESOLVE_STATUS_ACCEPTED
    assert result.accepted_asset_id == "existing"


def test_auto_resolve_reuses_local_manifest_entry_without_provider_search(tmp_path: Path) -> None:
    project, request_id = _setup_residual_request(tmp_path)
    reused_path = tmp_path / "reused.jpg"
    reused_path.write_bytes(b"img")
    manifest_entry = CutPlanSupplementManifestEntry(
        asset_id="supplement_pexels_reuse", provider="pexels", provider_asset_id="777",
        asset_path=str(reused_path), asset_type="image", duration_sec=0.0, folder_name=FOLDER_A,
        first_request_id="cutreq_other", first_candidate_id="cand_other",
    )

    with (
        patch(f"{_MODULE}.find_reusable_local_supplement_candidates", return_value=[manifest_entry]) as mock_reuse,
        patch(f"{_MODULE}._describe_and_validate_downloaded_asset", return_value={"status": "PASS", "score": 0.9}),
        patch(f"{_MODULE}.get_supplement_adapter") as mock_adapter_getter,
    ):
        result = auto_resolve_residual_gap_request(project, request_id)

    mock_reuse.assert_called_once()
    mock_adapter_getter.assert_not_called()
    assert result.status == AUTO_RESOLVE_STATUS_ACCEPTED
    assert result.accepted_asset_id == "supplement_pexels_reuse"

    updated_draft = load_cut_plan_draft(project)
    item = next(i for i in updated_draft.items if i.cut_item_id == "cut_1")
    assert any(s.asset_id == "supplement_pexels_reuse" for s in item.planned_visual_segments)

    reloaded_requests = load_residual_gap_requests(project)
    assert reloaded_requests.requests[0].accepted_asset_id == "supplement_pexels_reuse"
    assert reloaded_requests.requests[0].status == "ACCEPTED"


def test_auto_resolve_searches_provider_and_accepts_pass_candidate(tmp_path: Path) -> None:
    project, request_id = _setup_residual_request(tmp_path)
    candidate = _fake_candidate()

    mock_adapter = MagicMock()
    mock_adapter.search.return_value = [candidate]

    def _fake_acquire(cand, destination_folder):
        destination_folder.mkdir(parents=True, exist_ok=True)
        target = destination_folder / "fake.jpg"
        target.write_bytes(b"FAKE")
        from otio_app.services.supplement_sources.base import SupplementAsset
        from otio_app.analysis_models import SupplementAssetSidecar

        sidecar = SupplementAssetSidecar(asset_id="asset_x", supplement_request_id=request_id, provider="pexels")
        return SupplementAsset(local_path=target, sidecar=sidecar)

    mock_adapter.acquire.side_effect = _fake_acquire

    with (
        patch(f"{_MODULE}.find_reusable_local_supplement_candidates", return_value=[]),
        patch(f"{_MODULE}.get_supplement_adapter", return_value=mock_adapter),
        patch(f"{_MODULE}._describe_and_validate_downloaded_asset", return_value={"status": "PASS", "score": 0.95}),
    ):
        result = auto_resolve_residual_gap_request(project, request_id)

    assert result.status == AUTO_RESOLVE_STATUS_ACCEPTED
    assert "999" in result.accepted_asset_id  # provider_asset_id des Fake-Kandidaten

    updated_draft = load_cut_plan_draft(project)
    item = next(i for i in updated_draft.items if i.cut_item_id == "cut_1")
    assert any(s.asset_id == result.accepted_asset_id for s in item.planned_visual_segments)


def test_auto_resolve_returns_no_match_when_nothing_passes(tmp_path: Path) -> None:
    project, request_id = _setup_residual_request(tmp_path)
    candidate = _fake_candidate()

    mock_adapter = MagicMock()
    mock_adapter.search.return_value = [candidate]

    def _fake_acquire(cand, destination_folder):
        destination_folder.mkdir(parents=True, exist_ok=True)
        target = destination_folder / "fake.jpg"
        target.write_bytes(b"FAKE")
        from otio_app.services.supplement_sources.base import SupplementAsset
        from otio_app.analysis_models import SupplementAssetSidecar

        sidecar = SupplementAssetSidecar(asset_id="asset_x", supplement_request_id=request_id, provider="pexels")
        return SupplementAsset(local_path=target, sidecar=sidecar)

    mock_adapter.acquire.side_effect = _fake_acquire

    with (
        patch(f"{_MODULE}.find_reusable_local_supplement_candidates", return_value=[]),
        patch(f"{_MODULE}.get_supplement_adapter", return_value=mock_adapter),
        patch(f"{_MODULE}._describe_and_validate_downloaded_asset", return_value={"status": "FAIL", "score": 0.1}),
    ):
        result = auto_resolve_residual_gap_request(project, request_id)

    assert result.status == AUTO_RESOLVE_STATUS_NO_MATCH
    reloaded = load_residual_gap_requests(project)
    assert reloaded.requests[0].status == "NO_MATCH"


# --- auto_resolve_all_residual_gap_requests ---


def test_auto_resolve_all_only_processes_open_requests(tmp_path: Path) -> None:
    project, request_id = _setup_residual_request(tmp_path)
    document = load_residual_gap_requests(project)
    document = document.model_copy(
        update={
            "requests": [
                r.model_copy(update={"accepted_asset_id": "existing", "accepted_asset_path": "/fake/x.jpg"})
                for r in document.requests
            ]
        }
    )
    save_residual_gap_requests(project, document)

    with patch(f"{_MODULE}.find_reusable_local_supplement_candidates") as mock_reuse:
        results = auto_resolve_all_residual_gap_requests(project)

    assert results == []
    mock_reuse.assert_not_called()


def test_auto_resolve_all_returns_empty_without_requests_document(tmp_path: Path) -> None:
    project = _make_project(tmp_path)
    results = auto_resolve_all_residual_gap_requests(project)
    assert results == []
