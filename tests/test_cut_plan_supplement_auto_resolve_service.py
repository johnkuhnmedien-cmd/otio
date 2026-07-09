"""Phase 11.3: Auto-Resolver für EINEN Cut-Plan-Supplement-Request."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from otio_app.models import Project, ProjectMode
from otio_app.services.voiceover_generation.cut_plan_supplement_auto_resolve_service import (
    AUTO_RESOLVE_STATUS_ACCEPTED,
    AUTO_RESOLVE_STATUS_FAILED,
    AUTO_RESOLVE_STATUS_NO_MATCH,
    _describe_downloaded_asset,
    _validate_description,
    auto_resolve_cut_plan_supplement_request,
)
from otio_app.services.voiceover_generation.cut_plan_supplement_bridge import (
    build_supplement_requests_from_cut_plan,
    load_cut_plan_supplement_requests,
    save_cut_plan_supplement_requests,
)
from otio_app.services.voiceover_generation.cut_plan_supplement_models import (
    CutPlanSupplementAsset,
    CutPlanSupplementCandidate,
    CutPlanSupplementCandidatesDocument,
    CutPlanSupplementRequest,
)
from otio_app.services.voiceover_generation.cut_plan_models import CutPlanDocument, CutPlanItem, CutPlanSourceRef

_MODULE = "otio_app.services.voiceover_generation.cut_plan_supplement_auto_resolve_service"
FOLDER_A = "Havasu Falls"


def _make_project(tmp_path: Path) -> Project:
    project_root = tmp_path / "USA"
    (project_root / FOLDER_A).mkdir(parents=True)
    return Project(
        id="auto-resolve-project",
        name="Auto Resolve Test",
        project_root=str(project_root),
        work_dir=str(project_root / "_otio"),
        project_mode=ProjectMode.WITHOUT_VOICEOVER,
        asset_subdir_names=[FOLDER_A],
        selected_asset_subdirs=[FOLDER_A],
    )


def _minimal_item(**overrides) -> CutPlanItem:
    defaults = dict(
        cut_item_id="cut_001",
        source_refs=[CutPlanSourceRef(source_sentence_id="s1", text="Text")],
        source_scope="folder",
        folder_name=FOLDER_A,
        text="Noch vor kurzem stand ich am fallenden Wasser der Havasu Falls.",
        visual_intent="Wasserfall, Person spuert die Kuehle",
        timeline_start_sec=1.0,
        timeline_end_sec=6.0,
        duration_sec=5.0,
        audio_start_sec=0.0,
        audio_end_sec=5.0,
        chosen_asset_id="",
        asset_selection_status="SUPPLEMENT_REQUIRED",
        needs_supplement_asset=True,
        supplement_reason="No local asset available.",
        blockers=["CUT_PLAN_ERROR_SUPPLEMENT_REQUIRED"],
    )
    defaults.update(overrides)
    return CutPlanItem(**defaults)


def _setup_request(tmp_path: Path) -> tuple[Project, str]:
    project = _make_project(tmp_path)
    item = _minimal_item()
    cut_plan = CutPlanDocument(project_id=project.id, timeline_fps=25, items=[item])
    document = build_supplement_requests_from_cut_plan(project, cut_plan)
    save_cut_plan_supplement_requests(project, document)
    return project, document.requests[0].request_id


def _fake_candidate(candidate_id: str, request_id: str) -> CutPlanSupplementCandidate:
    return CutPlanSupplementCandidate(
        candidate_id=candidate_id,
        request_id=request_id,
        provider="pexels",
        title=f"Fake {candidate_id}",
        asset_type="video",
        duration_sec=10.0,
    )


def _fake_candidates_document(request_id: str, candidate_ids: list[str]) -> CutPlanSupplementCandidatesDocument:
    return CutPlanSupplementCandidatesDocument(
        project_id="auto-resolve-project",
        request_id=request_id,
        provider="pexels",
        candidates=[_fake_candidate(cid, request_id) for cid in candidate_ids],
        status="READY",
    )


def _fake_asset(candidate_id: str, request_id: str) -> CutPlanSupplementAsset:
    return CutPlanSupplementAsset(
        asset_id=f"cut_supplement_{request_id}_{candidate_id}",
        request_id=request_id,
        candidate_id=candidate_id,
        provider="pexels",
        asset_path=f"/fake/{candidate_id}.mp4",
        asset_type="video",
        duration_sec=10.0,
    )


# --- auto_resolve_cut_plan_supplement_request: Ablaufsteuerung ---


def test_auto_resolve_accepts_first_candidate_with_pass(tmp_path: Path) -> None:
    project, request_id = _setup_request(tmp_path)
    candidates_doc = _fake_candidates_document(request_id, ["cand_1", "cand_2"])

    with (
        patch(f"{_MODULE}.search_candidates_for_cut_plan_request", return_value=candidates_doc),
        patch(f"{_MODULE}.download_cut_plan_supplement_candidate", side_effect=lambda p, rid, c: _fake_asset(c.candidate_id, rid)),
        patch(f"{_MODULE}._describe_downloaded_asset", return_value="Ein Wasserfall mit einer Person."),
        patch(f"{_MODULE}._validate_description", return_value={"status": "PASS", "score": 0.9, "reason": "Passt."}),
        patch(f"{_MODULE}.accept_cut_plan_supplement_candidate") as mock_accept,
    ):
        result = auto_resolve_cut_plan_supplement_request(
            project, request_id, query_llm_provider="gemini", query_llm_model="gemini-3.1-flash-lite"
        )

    assert result.status == AUTO_RESOLVE_STATUS_ACCEPTED
    assert result.accepted_candidate_id == "cand_1"
    assert len(result.attempts) == 1
    assert result.attempts[0].validation_status == "PASS"
    mock_accept.assert_called_once()
    _, kwargs = mock_accept.call_args
    assert kwargs["downloaded_asset"].candidate_id == "cand_1"

    reloaded = load_cut_plan_supplement_requests(project)
    persisted = next(r for r in reloaded.requests if r.request_id == request_id)
    assert persisted.auto_resolve_status == AUTO_RESOLVE_STATUS_ACCEPTED
    assert len(persisted.auto_resolve_attempts) == 1


def test_auto_resolve_tries_next_candidate_when_first_fails_validation(tmp_path: Path) -> None:
    project, request_id = _setup_request(tmp_path)
    candidates_doc = _fake_candidates_document(request_id, ["cand_1", "cand_2"])

    validation_results = [
        {"status": "FAIL", "score": 0.1, "reason": "Passt nicht."},
        {"status": "PASS", "score": 0.9, "reason": "Passt."},
    ]

    with (
        patch(f"{_MODULE}.search_candidates_for_cut_plan_request", return_value=candidates_doc),
        patch(f"{_MODULE}.download_cut_plan_supplement_candidate", side_effect=lambda p, rid, c: _fake_asset(c.candidate_id, rid)),
        patch(f"{_MODULE}._describe_downloaded_asset", return_value="Beschreibung"),
        patch(f"{_MODULE}._validate_description", side_effect=validation_results),
        patch(f"{_MODULE}.accept_cut_plan_supplement_candidate") as mock_accept,
    ):
        result = auto_resolve_cut_plan_supplement_request(
            project, request_id, query_llm_provider="gemini", query_llm_model="gemini-3.1-flash-lite"
        )

    assert result.status == AUTO_RESOLVE_STATUS_ACCEPTED
    assert result.accepted_candidate_id == "cand_2"
    assert len(result.attempts) == 2
    assert result.attempts[0].validation_status == "FAIL"
    assert result.attempts[1].validation_status == "PASS"
    mock_accept.assert_called_once()


def test_auto_resolve_returns_no_match_when_no_candidate_passes(tmp_path: Path) -> None:
    project, request_id = _setup_request(tmp_path)
    candidates_doc = _fake_candidates_document(request_id, ["cand_1", "cand_2"])

    with (
        patch(f"{_MODULE}.search_candidates_for_cut_plan_request", return_value=candidates_doc),
        patch(f"{_MODULE}.download_cut_plan_supplement_candidate", side_effect=lambda p, rid, c: _fake_asset(c.candidate_id, rid)),
        patch(f"{_MODULE}._describe_downloaded_asset", return_value="Beschreibung"),
        patch(f"{_MODULE}._validate_description", return_value={"status": "WEAK_PASS", "score": 0.6, "reason": "Nur teilweise."}),
        patch(f"{_MODULE}.accept_cut_plan_supplement_candidate") as mock_accept,
    ):
        result = auto_resolve_cut_plan_supplement_request(
            project, request_id, query_llm_provider="gemini", query_llm_model="gemini-3.1-flash-lite"
        )

    assert result.status == AUTO_RESOLVE_STATUS_NO_MATCH
    assert result.accepted_candidate_id == ""
    assert len(result.attempts) == 2
    mock_accept.assert_not_called()

    reloaded = load_cut_plan_supplement_requests(project)
    persisted = next(r for r in reloaded.requests if r.request_id == request_id)
    assert persisted.auto_resolve_status == AUTO_RESOLVE_STATUS_NO_MATCH


def test_auto_resolve_returns_no_match_when_search_finds_no_candidates(tmp_path: Path) -> None:
    project, request_id = _setup_request(tmp_path)
    empty_doc = CutPlanSupplementCandidatesDocument(
        project_id=project.id, request_id=request_id, provider="pexels", candidates=[], status="NO_RESULTS"
    )

    with (
        patch(f"{_MODULE}.search_candidates_for_cut_plan_request", return_value=empty_doc),
        patch(f"{_MODULE}.download_cut_plan_supplement_candidate") as mock_download,
    ):
        result = auto_resolve_cut_plan_supplement_request(
            project, request_id, query_llm_provider="gemini", query_llm_model="gemini-3.1-flash-lite"
        )

    assert result.status == AUTO_RESOLVE_STATUS_NO_MATCH
    mock_download.assert_not_called()


def test_auto_resolve_handles_download_failure_and_continues(tmp_path: Path) -> None:
    project, request_id = _setup_request(tmp_path)
    candidates_doc = _fake_candidates_document(request_id, ["cand_1", "cand_2"])

    def _download_side_effect(project_arg, rid, candidate):
        if candidate.candidate_id == "cand_1":
            raise RuntimeError("download failed")
        return _fake_asset(candidate.candidate_id, rid)

    with (
        patch(f"{_MODULE}.search_candidates_for_cut_plan_request", return_value=candidates_doc),
        patch(f"{_MODULE}.download_cut_plan_supplement_candidate", side_effect=_download_side_effect),
        patch(f"{_MODULE}._describe_downloaded_asset", return_value="Beschreibung"),
        patch(f"{_MODULE}._validate_description", return_value={"status": "PASS", "score": 0.9, "reason": "Passt."}),
        patch(f"{_MODULE}.accept_cut_plan_supplement_candidate") as mock_accept,
    ):
        result = auto_resolve_cut_plan_supplement_request(
            project, request_id, query_llm_provider="gemini", query_llm_model="gemini-3.1-flash-lite"
        )

    assert result.status == AUTO_RESOLVE_STATUS_ACCEPTED
    assert result.accepted_candidate_id == "cand_2"
    assert result.attempts[0].validation_status == "DOWNLOAD_FAILED"
    assert result.attempts[1].validation_status == "PASS"
    mock_accept.assert_called_once()


def test_auto_resolve_search_exception_returns_failed_without_raising(tmp_path: Path) -> None:
    project, request_id = _setup_request(tmp_path)

    with patch(f"{_MODULE}.search_candidates_for_cut_plan_request", side_effect=RuntimeError("network down")):
        result = auto_resolve_cut_plan_supplement_request(
            project, request_id, query_llm_provider="gemini", query_llm_model="gemini-3.1-flash-lite"
        )

    assert result.status == AUTO_RESOLVE_STATUS_FAILED
    assert "network down" in result.error


# --- _validate_description / _describe_downloaded_asset: Bausteine ---


def _fake_request() -> CutPlanSupplementRequest:
    return CutPlanSupplementRequest(
        request_id="cutreq_x",
        cut_item_id="cut_001",
        folder_name=FOLDER_A,
        text="Noch vor kurzem stand ich am fallenden Wasser der Havasu Falls.",
        visual_intent="Wasserfall, Person spuert die Kuehle",
        reason="Fehlt Material",
    )


def test_validate_description_empty_description_is_fail() -> None:
    result = _validate_description(description="", request=_fake_request(), gemini_model="gemini-3.1-flash-lite")
    assert result["status"] == "FAIL"


def test_validate_description_uses_gemini_when_configured() -> None:
    with (
        patch(f"{_MODULE}.is_gemini_configured", return_value=True),
        patch(
            f"{_MODULE}.validate_supplement_asset_match",
            return_value={"status": "PASS", "score": 0.95, "reason": "Gemini sagt: passt."},
        ) as mock_validate,
    ):
        result = _validate_description(
            description="Ein Wasserfall.", request=_fake_request(), gemini_model="gemini-3.1-flash-lite"
        )
    assert result["status"] == "PASS"
    mock_validate.assert_called_once()


def test_validate_description_falls_back_to_heuristic_without_gemini() -> None:
    """Ohne konfiguriertes Gemini wird NIE automatisch PASS zurückgegeben —
    nur die heuristische Fallback-Bewertung (max. WEAK_PASS) — damit ohne
    GEMINI_API_KEY nie automatisch akzeptiert wird (safe by default)."""
    with patch(f"{_MODULE}.is_gemini_configured", return_value=False):
        result = _validate_description(
            description="Ein Wasserfall, eine Person spuert die Kuehle des Wassers.",
            request=_fake_request(),
            gemini_model="gemini-3.1-flash-lite",
        )
    assert result["status"] != "PASS"
    assert result["status"] in {"WEAK_PASS", "NEEDS_USER_REVIEW", "FAIL"}


def test_describe_downloaded_asset_returns_empty_when_file_missing(tmp_path: Path) -> None:
    project = _make_project(tmp_path)
    description = _describe_downloaded_asset(
        project,
        request_id="cutreq_x",
        candidate_id="cand_1",
        folder_name=FOLDER_A,
        asset_path=str(tmp_path / "does_not_exist.mp4"),
        gemini_model="gemini-3.1-flash-lite",
    )
    assert description == ""


def test_describe_downloaded_asset_returns_empty_without_gemini(tmp_path: Path) -> None:
    project = _make_project(tmp_path)
    asset_path = tmp_path / "fake.jpg"
    asset_path.write_bytes(b"FAKE_IMAGE_BYTES")

    with (
        patch(f"{_MODULE}.extract_frames", return_value=[asset_path]),
        patch(f"{_MODULE}.is_gemini_configured", return_value=False),
    ):
        description = _describe_downloaded_asset(
            project,
            request_id="cutreq_x",
            candidate_id="cand_1",
            folder_name=FOLDER_A,
            asset_path=str(asset_path),
            gemini_model="gemini-3.1-flash-lite",
        )
    assert description == ""


def test_describe_downloaded_asset_calls_gemini_when_configured(tmp_path: Path) -> None:
    project = _make_project(tmp_path)
    asset_path = tmp_path / "fake.jpg"
    asset_path.write_bytes(b"FAKE_IMAGE_BYTES")

    with (
        patch(f"{_MODULE}.extract_frames", return_value=[asset_path]),
        patch(f"{_MODULE}.is_gemini_configured", return_value=True),
        patch(f"{_MODULE}.describe_media_from_frames", return_value="Ein Wasserfall.") as mock_describe,
    ):
        description = _describe_downloaded_asset(
            project,
            request_id="cutreq_x",
            candidate_id="cand_1",
            folder_name=FOLDER_A,
            asset_path=str(asset_path),
            gemini_model="gemini-3.1-flash-lite",
        )
    assert description == "Ein Wasserfall."
    mock_describe.assert_called_once()
