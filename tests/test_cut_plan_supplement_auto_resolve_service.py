"""Phase 11.3: Auto-Resolver für EINEN Cut-Plan-Supplement-Request.

Nutzt einen KOMBINIERTEN Gemini-Aufruf (describe_and_validate_supplement_
asset) statt zwei getrennter Aufrufe (Beschreiben + Validieren)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from otio_app.models import Project, ProjectMode
from otio_app.services.generic_outro_selector import GenericAssetCandidate
from otio_app.services.voiceover_generation.cut_plan_supplement_auto_resolve_service import (
    AUTO_RESOLVE_STATUS_ACCEPTED,
    AUTO_RESOLVE_STATUS_GENERIC_FALLBACK_USED,
    AUTO_RESOLVE_STATUS_NO_MATCH,
    DEFAULT_AUTO_RESOLVE_VALIDATION_MODEL,
    _describe_and_validate_downloaded_asset,
    auto_resolve_cut_plan_supplement_request,
)
from otio_app.services.voiceover_generation.cut_plan_builder import save_cut_plan_draft
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
    save_cut_plan_draft(project, cut_plan)
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


def _analysis(status: str, *, score: float = 0.9, reason: str = "Passt.", description: str = "Beschreibung") -> dict:
    return {"description": description, "status": status, "score": score, "reason": reason}


# --- auto_resolve_cut_plan_supplement_request: Ablaufsteuerung ---


def test_auto_resolve_accepts_first_candidate_with_pass(tmp_path: Path) -> None:
    project, request_id = _setup_request(tmp_path)
    candidates_doc = _fake_candidates_document(request_id, ["cand_1", "cand_2"])

    with (
        patch(f"{_MODULE}.search_candidates_for_cut_plan_request", return_value=candidates_doc),
        patch(f"{_MODULE}.download_cut_plan_supplement_candidate", side_effect=lambda p, rid, c: _fake_asset(c.candidate_id, rid)),
        patch(f"{_MODULE}._describe_and_validate_downloaded_asset", return_value=_analysis("PASS")),
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

    analysis_results = [_analysis("FAIL", score=0.1, reason="Passt nicht."), _analysis("PASS")]

    with (
        patch(f"{_MODULE}.search_candidates_for_cut_plan_request", return_value=candidates_doc),
        patch(f"{_MODULE}.download_cut_plan_supplement_candidate", side_effect=lambda p, rid, c: _fake_asset(c.candidate_id, rid)),
        patch(f"{_MODULE}._describe_and_validate_downloaded_asset", side_effect=analysis_results),
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
        patch(f"{_MODULE}._describe_and_validate_downloaded_asset", return_value=_analysis("WEAK_PASS", score=0.6, reason="Nur teilweise.")),
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
        patch(f"{_MODULE}._describe_and_validate_downloaded_asset", return_value=_analysis("PASS")),
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


def test_auto_resolve_search_exception_tries_generic_fallback_then_no_match(tmp_path: Path) -> None:
    """Phase 11.4: ein Suchfehler löst jetzt ZUERST den generischen Ordner-
    Fallback aus (kein weiterer Provider-/LLM-Aufruf nötig) — erst wenn
    AUCH das nichts findet (hier: leeres Ordner-Inventory), bleibt es bei
    NO_MATCH. Die ursprüngliche Fehlermeldung bleibt zur Diagnose erhalten."""
    project, request_id = _setup_request(tmp_path)

    with patch(f"{_MODULE}.search_candidates_for_cut_plan_request", side_effect=RuntimeError("network down")):
        result = auto_resolve_cut_plan_supplement_request(
            project, request_id, query_llm_provider="gemini", query_llm_model="gemini-3.1-flash-lite"
        )

    assert result.status == AUTO_RESOLVE_STATUS_NO_MATCH
    assert "network down" in result.error


def test_auto_resolve_search_exception_uses_generic_fallback_when_available(tmp_path: Path) -> None:
    """Wenn der generische Fallback tatsächlich einen Kandidaten findet,
    wird er trotz Suchfehler verwendet — Ergebnis GENERIC_FALLBACK_USED."""
    project, request_id = _setup_request(tmp_path)
    fake_candidate = GenericAssetCandidate(
        path="/fake/generic.mp4",
        asset_id="asset_generic",
        description="Establishing shot",
        score=0.8,
        selection_reason="Neutraler Shot.",
        warnings=[],
    )

    with (
        patch(f"{_MODULE}.search_candidates_for_cut_plan_request", side_effect=RuntimeError("network down")),
        patch(f"{_MODULE}.apply_generic_fallback_for_cut_plan_request", return_value=(None, fake_candidate)),
    ):
        result = auto_resolve_cut_plan_supplement_request(
            project, request_id, query_llm_provider="gemini", query_llm_model="gemini-3.1-flash-lite"
        )

    assert result.status == AUTO_RESOLVE_STATUS_GENERIC_FALLBACK_USED
    assert result.accepted_asset_id == "asset_generic"


def test_auto_resolve_no_candidates_found_tries_generic_fallback(tmp_path: Path) -> None:
    project, request_id = _setup_request(tmp_path)
    empty_doc = CutPlanSupplementCandidatesDocument(
        project_id=project.id, request_id=request_id, provider="pexels", candidates=[], status="NO_RESULTS"
    )
    fake_candidate = GenericAssetCandidate(
        path="/fake/generic.mp4",
        asset_id="asset_generic",
        description="Establishing shot",
        score=0.8,
        selection_reason="Neutraler Shot.",
        warnings=[],
    )

    with (
        patch(f"{_MODULE}.search_candidates_for_cut_plan_request", return_value=empty_doc),
        patch(f"{_MODULE}.apply_generic_fallback_for_cut_plan_request", return_value=(None, fake_candidate)),
    ):
        result = auto_resolve_cut_plan_supplement_request(
            project, request_id, query_llm_provider="gemini", query_llm_model="gemini-3.1-flash-lite"
        )

    assert result.status == AUTO_RESOLVE_STATUS_GENERIC_FALLBACK_USED
    assert result.accepted_asset_id == "asset_generic"


def test_auto_resolve_uses_generic_fallback_when_no_stock_candidate_passes(tmp_path: Path) -> None:
    project, request_id = _setup_request(tmp_path)
    candidates_doc = _fake_candidates_document(request_id, ["cand_1"])
    fake_candidate = GenericAssetCandidate(
        path="/fake/generic.mp4",
        asset_id="asset_generic",
        description="Establishing shot",
        score=0.8,
        selection_reason="Neutraler Shot.",
        warnings=[],
    )

    with (
        patch(f"{_MODULE}.search_candidates_for_cut_plan_request", return_value=candidates_doc),
        patch(f"{_MODULE}.download_cut_plan_supplement_candidate", side_effect=lambda p, rid, c: _fake_asset(c.candidate_id, rid)),
        patch(f"{_MODULE}._describe_and_validate_downloaded_asset", return_value=_analysis("FAIL", score=0.1)),
        patch(f"{_MODULE}.apply_generic_fallback_for_cut_plan_request", return_value=(None, fake_candidate)) as mock_fallback,
        patch(f"{_MODULE}.accept_cut_plan_supplement_candidate") as mock_accept,
    ):
        result = auto_resolve_cut_plan_supplement_request(
            project, request_id, query_llm_provider="gemini", query_llm_model="gemini-3.1-flash-lite"
        )

    assert result.status == AUTO_RESOLVE_STATUS_GENERIC_FALLBACK_USED
    assert result.accepted_asset_id == "asset_generic"
    assert len(result.attempts) == 1  # der eine Stock-Versuch bleibt in der Trace erhalten
    mock_accept.assert_not_called()
    mock_fallback.assert_called_once()

    reloaded = load_cut_plan_supplement_requests(project)
    persisted = next(r for r in reloaded.requests if r.request_id == request_id)
    assert persisted.auto_resolve_status == AUTO_RESOLVE_STATUS_GENERIC_FALLBACK_USED


def test_auto_resolve_generic_fallback_exception_is_recorded_and_still_returns_no_match(tmp_path: Path) -> None:
    project, request_id = _setup_request(tmp_path)
    candidates_doc = _fake_candidates_document(request_id, ["cand_1"])

    with (
        patch(f"{_MODULE}.search_candidates_for_cut_plan_request", return_value=candidates_doc),
        patch(f"{_MODULE}.download_cut_plan_supplement_candidate", side_effect=lambda p, rid, c: _fake_asset(c.candidate_id, rid)),
        patch(f"{_MODULE}._describe_and_validate_downloaded_asset", return_value=_analysis("FAIL", score=0.1)),
        patch(f"{_MODULE}.apply_generic_fallback_for_cut_plan_request", side_effect=RuntimeError("fallback broke")),
    ):
        result = auto_resolve_cut_plan_supplement_request(
            project, request_id, query_llm_provider="gemini", query_llm_model="gemini-3.1-flash-lite"
        )

    assert result.status == AUTO_RESOLVE_STATUS_NO_MATCH
    assert any(a.validation_status == "GENERIC_FALLBACK_FAILED" for a in result.attempts)


def test_auto_resolve_uses_default_validation_model_when_not_overridden(tmp_path: Path) -> None:
    """Nutzerwunsch: Gemini 3 Flash Preview als fester Standard für die
    automatische Bild-/Video-Prüfung."""
    project, request_id = _setup_request(tmp_path)
    candidates_doc = _fake_candidates_document(request_id, ["cand_1"])

    with (
        patch(f"{_MODULE}.search_candidates_for_cut_plan_request", return_value=candidates_doc),
        patch(f"{_MODULE}.download_cut_plan_supplement_candidate", side_effect=lambda p, rid, c: _fake_asset(c.candidate_id, rid)),
        patch(f"{_MODULE}._describe_and_validate_downloaded_asset", return_value=_analysis("PASS")) as mock_describe_validate,
        patch(f"{_MODULE}.accept_cut_plan_supplement_candidate"),
    ):
        auto_resolve_cut_plan_supplement_request(
            project, request_id, query_llm_provider="gemini", query_llm_model="gemini-3.1-flash-lite"
        )

    _, kwargs = mock_describe_validate.call_args
    assert kwargs["validation_model"] == DEFAULT_AUTO_RESOLVE_VALIDATION_MODEL == "gemini-3-flash-preview"


# --- _describe_and_validate_downloaded_asset: kombinierter Gemini-Aufruf ---


def _fake_request() -> CutPlanSupplementRequest:
    return CutPlanSupplementRequest(
        request_id="cutreq_x",
        cut_item_id="cut_001",
        folder_name=FOLDER_A,
        text="Noch vor kurzem stand ich am fallenden Wasser der Havasu Falls.",
        visual_intent="Wasserfall, Person spuert die Kuehle",
        reason="Fehlt Material",
    )


def test_describe_and_validate_returns_fail_without_gemini_and_skips_frame_extraction(tmp_path: Path) -> None:
    """Ohne GEMINI_API_KEY: sofort FAIL, OHNE ffmpeg/Frame-Extraktion
    auszulösen (spart unnötige Arbeit, wenn ohnehin nicht geprüft werden
    kann) — und es gibt KEINEN heuristischen Ersatz, der versehentlich
    automatisch PASS liefern könnte (safe by default)."""
    project = _make_project(tmp_path)
    asset_path = tmp_path / "fake.mp4"
    asset_path.write_bytes(b"FAKE_VIDEO_BYTES")

    with (
        patch(f"{_MODULE}.is_gemini_configured", return_value=False),
        patch(f"{_MODULE}.extract_frames") as mock_extract,
    ):
        analysis = _describe_and_validate_downloaded_asset(
            project,
            request=_fake_request(),
            candidate_id="cand_1",
            asset_path=str(asset_path),
            validation_model="gemini-3-flash-preview",
        )

    assert analysis["status"] == "FAIL"
    assert analysis["description"] == ""
    mock_extract.assert_not_called()


def test_describe_and_validate_returns_fail_when_file_missing(tmp_path: Path) -> None:
    project = _make_project(tmp_path)
    with patch(f"{_MODULE}.is_gemini_configured", return_value=True):
        analysis = _describe_and_validate_downloaded_asset(
            project,
            request=_fake_request(),
            candidate_id="cand_1",
            asset_path=str(tmp_path / "does_not_exist.mp4"),
            validation_model="gemini-3-flash-preview",
        )
    assert analysis["status"] == "FAIL"


def test_describe_and_validate_calls_combined_gemini_function_once(tmp_path: Path) -> None:
    """Kernanforderung: EIN kombinierter Gemini-Aufruf statt zwei getrennter
    (Beschreiben + Validieren)."""
    project = _make_project(tmp_path)
    asset_path = tmp_path / "fake.jpg"
    asset_path.write_bytes(b"FAKE_IMAGE_BYTES")

    with (
        patch(f"{_MODULE}.is_gemini_configured", return_value=True),
        patch(f"{_MODULE}.extract_frames", return_value=[asset_path]),
        patch(
            f"{_MODULE}.describe_and_validate_supplement_asset",
            return_value=_analysis("PASS", description="Ein Wasserfall."),
        ) as mock_combined,
    ):
        analysis = _describe_and_validate_downloaded_asset(
            project,
            request=_fake_request(),
            candidate_id="cand_1",
            asset_path=str(asset_path),
            validation_model="gemini-3-flash-preview",
        )

    assert analysis["status"] == "PASS"
    assert analysis["description"] == "Ein Wasserfall."
    mock_combined.assert_called_once()
    _, kwargs = mock_combined.call_args
    assert kwargs["model"] == "gemini-3-flash-preview"
    assert kwargs["frame_paths"] == [asset_path]


def test_describe_and_validate_handles_gemini_exception_gracefully(tmp_path: Path) -> None:
    project = _make_project(tmp_path)
    asset_path = tmp_path / "fake.jpg"
    asset_path.write_bytes(b"FAKE_IMAGE_BYTES")

    with (
        patch(f"{_MODULE}.is_gemini_configured", return_value=True),
        patch(f"{_MODULE}.extract_frames", return_value=[asset_path]),
        patch(f"{_MODULE}.describe_and_validate_supplement_asset", side_effect=RuntimeError("network down")),
    ):
        analysis = _describe_and_validate_downloaded_asset(
            project,
            request=_fake_request(),
            candidate_id="cand_1",
            asset_path=str(asset_path),
            validation_model="gemini-3-flash-preview",
        )

    assert analysis["status"] == "FAIL"
    assert "network down" in analysis["reason"]
