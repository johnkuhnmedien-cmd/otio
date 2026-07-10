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
    VALIDATION_STATUS_ACCEPT_FAILED,
    VALIDATION_STATUS_TOO_SHORT,
    CutPlanSupplementAutoResolveResult,
    _describe_and_validate_downloaded_asset,
    auto_resolve_all_cut_plan_supplement_requests,
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


def _setup_two_requests(tmp_path: Path) -> tuple[Project, list[str]]:
    project = _make_project(tmp_path)
    items = [
        _minimal_item(cut_item_id="cut_001"),
        _minimal_item(cut_item_id="cut_002", timeline_start_sec=6.0, timeline_end_sec=11.0),
    ]
    cut_plan = CutPlanDocument(project_id=project.id, timeline_fps=25, items=items)
    save_cut_plan_draft(project, cut_plan)
    document = build_supplement_requests_from_cut_plan(project, cut_plan)
    save_cut_plan_supplement_requests(project, document)
    return project, [request.request_id for request in document.requests]


def _fake_candidate(candidate_id: str, request_id: str, *, provider: str = "pexels") -> CutPlanSupplementCandidate:
    return CutPlanSupplementCandidate(
        candidate_id=candidate_id,
        request_id=request_id,
        provider=provider,
        title=f"Fake {candidate_id}",
        asset_type="video",
        duration_sec=10.0,
    )


def _fake_candidates_document(
    request_id: str, candidate_ids: list[str], *, provider: str = "pexels"
) -> CutPlanSupplementCandidatesDocument:
    return CutPlanSupplementCandidatesDocument(
        project_id="auto-resolve-project",
        request_id=request_id,
        provider=provider,
        candidates=[_fake_candidate(cid, request_id, provider=provider) for cid in candidate_ids],
        status="READY",
    )


def _empty_candidates_document(request_id: str, *, provider: str) -> CutPlanSupplementCandidatesDocument:
    return CutPlanSupplementCandidatesDocument(
        project_id="auto-resolve-project", request_id=request_id, provider=provider, candidates=[], status="NO_RESULTS"
    )


def _search_only_finds_candidates_for(
    candidates_doc: CutPlanSupplementCandidatesDocument, *, only_provider: str, only_asset_type: str = "video"
):
    """Phase 12.5/12.7: baut einen provider- UND medientyp-bewussten
    side_effect für search_candidates_for_cut_plan_request — nur die
    EXAKTE Suchstufe (only_provider, only_asset_type) liefert candidates_doc,
    jede andere Suchstufe (anderer Provider ODER anderer Medientyp
    desselben Providers, siehe die Video-vor-Foto-Suchstufen in
    cut_plan_supplement_auto_resolve_service.py) liefert NO_RESULTS. Bildet
    realistisch ab, dass eine separate Video-/Foto-Suche i. d. R.
    UNTERSCHIEDLICHE Treffer liefert — Standard only_asset_type="video", da
    das der häufigste Fall ("Video wird direkt gefunden") ist."""

    def _side_effect(project_arg, request_id, provider_settings, **kwargs):
        provider = provider_settings["provider"]
        asset_type = provider_settings.get("required_asset_type", "")
        if provider == only_provider and asset_type == only_asset_type:
            return candidates_doc
        return _empty_candidates_document(request_id, provider=provider)

    return _side_effect


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
    # Phase 12.5: Adobe wird zuerst versucht — dieser Test simuliert, dass
    # NUR Adobe Kandidaten findet (beide WEAK_PASS); Pexels liefert nichts.
    candidates_doc = _fake_candidates_document(request_id, ["cand_1", "cand_2"], provider="adobe_stock")

    with (
        patch(
            f"{_MODULE}.search_candidates_for_cut_plan_request",
            side_effect=_search_only_finds_candidates_for(candidates_doc, only_provider="adobe_stock"),
        ),
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


def test_auto_resolve_skips_too_short_video_candidate_without_download(tmp_path: Path) -> None:
    """Phase 12.6: ein Video-Kandidat, der laut Provider-Metadaten
    (duration_sec) offensichtlich zu kurz für das Item ist (hier: Item
    braucht 5.0s, Kandidat hat nur 2.0s, davon geht video_head_trim_sec
    (Standard 1.0s) ab -> 1.0s verfügbar), wird NICHT heruntergeladen/
    lizenziert — schützt insbesondere Adobe-Lizenzkontingent."""
    project, request_id = _setup_request(tmp_path)
    too_short_candidate = _fake_candidate("cand_1", request_id).model_copy(update={"duration_sec": 2.0})
    fine_candidate = _fake_candidate("cand_2", request_id).model_copy(update={"duration_sec": 10.0})
    candidates_doc = CutPlanSupplementCandidatesDocument(
        project_id=project.id,
        request_id=request_id,
        provider="pexels",
        candidates=[too_short_candidate, fine_candidate],
        status="READY",
    )

    def _download_side_effect(project_arg, rid, candidate):
        assert candidate.candidate_id != "cand_1", "zu kurzer Kandidat darf nicht heruntergeladen werden"
        return _fake_asset(candidate.candidate_id, rid)

    with (
        patch(f"{_MODULE}.search_candidates_for_cut_plan_request", return_value=candidates_doc),
        patch(f"{_MODULE}.download_cut_plan_supplement_candidate", side_effect=_download_side_effect) as mock_download,
        patch(f"{_MODULE}._describe_and_validate_downloaded_asset", return_value=_analysis("PASS")),
        patch(f"{_MODULE}.accept_cut_plan_supplement_candidate") as mock_accept,
    ):
        result = auto_resolve_cut_plan_supplement_request(
            project, request_id, query_llm_provider="gemini", query_llm_model="gemini-3.1-flash-lite"
        )

    assert result.status == AUTO_RESOLVE_STATUS_ACCEPTED
    assert result.accepted_candidate_id == "cand_2"
    assert len(result.attempts) == 2
    assert result.attempts[0].validation_status == VALIDATION_STATUS_TOO_SHORT
    assert result.attempts[1].validation_status == "PASS"
    assert mock_download.call_count == 1
    mock_accept.assert_called_once()


def test_auto_resolve_accept_failure_does_not_crash_batch_and_tries_next_candidate(tmp_path: Path) -> None:
    """Phase 12.6: schlägt die finale Übernahme trotz Gemini-PASS fehl (z. B.
    weil die per ffprobe gemessene REALE Dauer doch zu kurz ist —
    apply_accepted_supplement_to_cut_plan_item wirft ValueError), darf das
    NICHT den gesamten Auto-Resolve-Lauf crashen. Stattdessen wird der
    Kandidat als ACCEPT_FAILED protokolliert und der nächste Kandidat
    versucht."""
    project, request_id = _setup_request(tmp_path)
    candidates_doc = _fake_candidates_document(request_id, ["cand_1", "cand_2"])

    def _accept_side_effect(project_arg, rid, candidate_id, downloaded_asset=None):
        if candidate_id == "cand_1":
            raise ValueError("Supplement-Kandidat zu kurz für Item 'cut_001': benötigt 5.00s, verfügbar 3.65s.")
        return None

    with (
        patch(f"{_MODULE}.search_candidates_for_cut_plan_request", return_value=candidates_doc),
        patch(f"{_MODULE}.download_cut_plan_supplement_candidate", side_effect=lambda p, rid, c: _fake_asset(c.candidate_id, rid)),
        patch(f"{_MODULE}._describe_and_validate_downloaded_asset", return_value=_analysis("PASS")),
        patch(f"{_MODULE}.accept_cut_plan_supplement_candidate", side_effect=_accept_side_effect) as mock_accept,
    ):
        result = auto_resolve_cut_plan_supplement_request(
            project, request_id, query_llm_provider="gemini", query_llm_model="gemini-3.1-flash-lite"
        )

    assert result.status == AUTO_RESOLVE_STATUS_ACCEPTED
    assert result.accepted_candidate_id == "cand_2"
    assert len(result.attempts) == 2
    assert result.attempts[0].validation_status == VALIDATION_STATUS_ACCEPT_FAILED
    assert "zu kurz" in result.attempts[0].validation_reason
    assert result.attempts[1].validation_status == "PASS"
    assert mock_accept.call_count == 2

    reloaded = load_cut_plan_supplement_requests(project)
    persisted = next(r for r in reloaded.requests if r.request_id == request_id)
    assert persisted.auto_resolve_status == AUTO_RESOLVE_STATUS_ACCEPTED


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
    # Phase 12.5: nur Adobe findet einen (nicht bestehenden) Kandidaten,
    # Pexels findet nichts -> danach greift der generische Fallback.
    candidates_doc = _fake_candidates_document(request_id, ["cand_1"], provider="adobe_stock")
    fake_candidate = GenericAssetCandidate(
        path="/fake/generic.mp4",
        asset_id="asset_generic",
        description="Establishing shot",
        score=0.8,
        selection_reason="Neutraler Shot.",
        warnings=[],
    )

    with (
        patch(
            f"{_MODULE}.search_candidates_for_cut_plan_request",
            side_effect=_search_only_finds_candidates_for(candidates_doc, only_provider="adobe_stock"),
        ),
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


def test_auto_resolve_tries_adobe_before_pexels(tmp_path: Path) -> None:
    """Phase 12.5, Nutzervorgabe: Adobe Stock wird immer ZUERST versucht,
    Pexels erst danach. Phase 12.7: pro Provider wird zusätzlich zuerst
    Video, dann Foto versucht — ergibt die vollständige Suchstufen-
    Reihenfolge Adobe-Video -> Adobe-Foto -> Pexels-Video -> Pexels-Foto."""
    project, request_id = _setup_request(tmp_path)
    empty_doc = _empty_candidates_document(request_id, provider="unused")
    stages_tried: list[tuple[str, str]] = []

    def _search_side_effect(project_arg, rid, provider_settings, **kwargs):
        stages_tried.append((provider_settings["provider"], provider_settings.get("required_asset_type", "")))
        return empty_doc

    fake_candidate = GenericAssetCandidate(
        path="/fake/generic.mp4",
        asset_id="asset_generic",
        description="Establishing shot",
        score=0.8,
        selection_reason="Neutraler Shot.",
        warnings=[],
    )

    with (
        patch(f"{_MODULE}.search_candidates_for_cut_plan_request", side_effect=_search_side_effect),
        patch(f"{_MODULE}.apply_generic_fallback_for_cut_plan_request", return_value=(None, fake_candidate)),
    ):
        auto_resolve_cut_plan_supplement_request(
            project, request_id, query_llm_provider="gemini", query_llm_model="gemini-3.1-flash-lite"
        )

    assert stages_tried == [
        ("adobe_stock", "video"),
        ("adobe_stock", "image"),
        ("pexels", "video"),
        ("pexels", "image"),
    ]


def test_auto_resolve_skips_pexels_when_adobe_candidate_already_passes(tmp_path: Path) -> None:
    """Wenn Adobe bereits einen bestehenden Kandidaten liefert, wird Pexels
    gar nicht erst durchsucht — kein unnötiger zweiter Provider-Aufruf."""
    project, request_id = _setup_request(tmp_path)
    adobe_doc = _fake_candidates_document(request_id, ["cand_1"], provider="adobe_stock")
    providers_tried: list[str] = []

    def _search_side_effect(project_arg, rid, provider_settings, **kwargs):
        providers_tried.append(provider_settings["provider"])
        return adobe_doc

    with (
        patch(f"{_MODULE}.search_candidates_for_cut_plan_request", side_effect=_search_side_effect),
        patch(f"{_MODULE}.download_cut_plan_supplement_candidate", side_effect=lambda p, rid, c: _fake_asset(c.candidate_id, rid)),
        patch(f"{_MODULE}._describe_and_validate_downloaded_asset", return_value=_analysis("PASS")),
        patch(f"{_MODULE}.accept_cut_plan_supplement_candidate") as mock_accept,
    ):
        result = auto_resolve_cut_plan_supplement_request(
            project, request_id, query_llm_provider="gemini", query_llm_model="gemini-3.1-flash-lite"
        )

    assert result.status == AUTO_RESOLVE_STATUS_ACCEPTED
    assert result.attempts[0].provider == "adobe_stock"
    assert providers_tried == ["adobe_stock"]
    mock_accept.assert_called_once()


def test_auto_resolve_tries_photo_only_after_video_fails_for_same_provider(tmp_path: Path) -> None:
    """Phase 12.7, Nutzervorgabe: pro Provider wird ZUERST Video versucht —
    erst wenn KEIN Video-Kandidat PASS erreicht, wird auf Foto DESSELBEN
    Providers gewechselt, BEVOR der nächste Provider (Pexels) versucht
    wird."""
    project, request_id = _setup_request(tmp_path)
    video_doc = CutPlanSupplementCandidatesDocument(
        project_id=project.id,
        request_id=request_id,
        provider="adobe_stock",
        candidates=[_fake_candidate("cand_video", request_id, provider="adobe_stock")],
        status="READY",
    )
    photo_candidate = _fake_candidate("cand_photo", request_id, provider="adobe_stock").model_copy(
        update={"asset_type": "image", "duration_sec": 0.0}
    )
    photo_doc = CutPlanSupplementCandidatesDocument(
        project_id=project.id,
        request_id=request_id,
        provider="adobe_stock",
        candidates=[photo_candidate],
        status="READY",
    )
    empty_doc = _empty_candidates_document(request_id, provider="pexels")
    stages_tried: list[tuple[str, str]] = []

    def _search_side_effect(project_arg, rid, provider_settings, **kwargs):
        provider = provider_settings["provider"]
        asset_type = provider_settings.get("required_asset_type", "")
        stages_tried.append((provider, asset_type))
        if provider == "adobe_stock" and asset_type == "video":
            return video_doc
        if provider == "adobe_stock" and asset_type == "image":
            return photo_doc
        return empty_doc

    with (
        patch(f"{_MODULE}.search_candidates_for_cut_plan_request", side_effect=_search_side_effect),
        patch(
            f"{_MODULE}.download_cut_plan_supplement_candidate",
            side_effect=lambda p, rid, c: _fake_asset(c.candidate_id, rid),
        ),
        patch(
            f"{_MODULE}._describe_and_validate_downloaded_asset",
            side_effect=[_analysis("FAIL", score=0.1), _analysis("PASS")],
        ),
        patch(f"{_MODULE}.accept_cut_plan_supplement_candidate") as mock_accept,
    ):
        result = auto_resolve_cut_plan_supplement_request(
            project, request_id, query_llm_provider="gemini", query_llm_model="gemini-3.1-flash-lite"
        )

    assert result.status == AUTO_RESOLVE_STATUS_ACCEPTED
    assert result.accepted_candidate_id == "cand_photo"
    # Pexels wurde nicht mehr benötigt — der Foto-Kandidat DESSELBEN
    # Providers hat bereits vor dem nächsten Provider bestanden.
    assert stages_tried == [("adobe_stock", "video"), ("adobe_stock", "image")]
    mock_accept.assert_called_once()


def test_auto_resolve_falls_through_to_pexels_when_adobe_search_raises(tmp_path: Path) -> None:
    """Ein Fehler bei Adobe (z. B. fehlender Access-Token/Netzwerkfehler)
    darf Pexels nicht blockieren — der Auto-Resolver versucht Pexels trotzdem."""
    project, request_id = _setup_request(tmp_path)
    pexels_doc = _fake_candidates_document(request_id, ["cand_1"], provider="pexels")

    def _search_side_effect(project_arg, rid, provider_settings, **kwargs):
        if provider_settings["provider"] == "adobe_stock":
            raise RuntimeError("ADOBE_STOCK_ACCESS_TOKEN fehlt")
        return pexels_doc

    with (
        patch(f"{_MODULE}.search_candidates_for_cut_plan_request", side_effect=_search_side_effect),
        patch(f"{_MODULE}.download_cut_plan_supplement_candidate", side_effect=lambda p, rid, c: _fake_asset(c.candidate_id, rid)),
        patch(f"{_MODULE}._describe_and_validate_downloaded_asset", return_value=_analysis("PASS")),
        patch(f"{_MODULE}.accept_cut_plan_supplement_candidate") as mock_accept,
    ):
        result = auto_resolve_cut_plan_supplement_request(
            project, request_id, query_llm_provider="gemini", query_llm_model="gemini-3.1-flash-lite"
        )

    assert result.status == AUTO_RESOLVE_STATUS_ACCEPTED
    assert result.attempts[0].provider == "pexels"
    mock_accept.assert_called_once()


def test_auto_resolve_requests_at_most_two_candidates_per_stage(tmp_path: Path) -> None:
    """Phase 12.7, Nutzervorgabe: pro Suchstufe (Provider + Medientyp) werden
    höchstens 2 Kandidaten angefragt — schützt Adobe-Lizenzkontingent vor
    unnötig vielen Versuchen."""
    project, request_id = _setup_request(tmp_path)
    empty_doc = _empty_candidates_document(request_id, provider="unused")
    captured_settings: list[dict] = []

    def _search_side_effect(project_arg, rid, provider_settings, **kwargs):
        captured_settings.append(dict(provider_settings))
        return empty_doc

    fake_candidate = GenericAssetCandidate(
        path="/fake/generic.mp4",
        asset_id="asset_generic",
        description="Establishing shot",
        score=0.8,
        selection_reason="Neutraler Shot.",
        warnings=[],
    )

    with (
        patch(f"{_MODULE}.search_candidates_for_cut_plan_request", side_effect=_search_side_effect),
        patch(f"{_MODULE}.apply_generic_fallback_for_cut_plan_request", return_value=(None, fake_candidate)),
    ):
        auto_resolve_cut_plan_supplement_request(
            project, request_id, query_llm_provider="gemini", query_llm_model="gemini-3.1-flash-lite"
        )

    assert len(captured_settings) == 4  # 2 Provider x 2 Medientypen
    for settings in captured_settings:
        assert settings["max_candidates"] == 2


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


# --- auto_resolve_all_cut_plan_supplement_requests (Phase 11.5, Batch) ---


def test_auto_resolve_all_processes_each_open_request_with_progress_callback(tmp_path: Path) -> None:
    project, request_ids = _setup_two_requests(tmp_path)
    progress_calls: list[tuple[str, int, int]] = []

    def _fake_resolve(project_arg, request_id, **kwargs):
        return CutPlanSupplementAutoResolveResult(status=AUTO_RESOLVE_STATUS_ACCEPTED, request_id=request_id)

    with patch(f"{_MODULE}.auto_resolve_cut_plan_supplement_request", side_effect=_fake_resolve) as mock_resolve:
        results = auto_resolve_all_cut_plan_supplement_requests(
            project,
            query_llm_provider="gemini",
            query_llm_model="gemini-3.1-flash-lite",
            progress_callback=lambda label, index, total: progress_calls.append((label, index, total)),
        )

    assert len(results) == 2
    assert {result.request_id for result in results} == set(request_ids)
    assert mock_resolve.call_count == 2
    assert progress_calls == [
        (request_ids[0], 1, 2),
        (request_ids[1], 2, 2),
    ]
    # query_llm_provider/model werden an jeden Einzel-Aufruf weitergereicht.
    for call in mock_resolve.call_args_list:
        assert call.kwargs["query_llm_provider"] == "gemini"
        assert call.kwargs["query_llm_model"] == "gemini-3.1-flash-lite"


def test_auto_resolve_all_skips_requests_that_already_have_an_asset(tmp_path: Path) -> None:
    project, request_ids = _setup_two_requests(tmp_path)
    from otio_app.services.voiceover_generation.cut_plan_supplement_bridge import (
        update_cut_plan_supplement_request,
    )

    update_cut_plan_supplement_request(project, request_ids[0], accepted_asset_id="asset_already_there")

    with patch(
        f"{_MODULE}.auto_resolve_cut_plan_supplement_request",
        return_value=CutPlanSupplementAutoResolveResult(status=AUTO_RESOLVE_STATUS_NO_MATCH, request_id="x"),
    ) as mock_resolve:
        results = auto_resolve_all_cut_plan_supplement_requests(
            project, query_llm_provider="gemini", query_llm_model="gemini-3.1-flash-lite"
        )

    assert len(results) == 1
    mock_resolve.assert_called_once()
    called_request_id = mock_resolve.call_args[0][1]
    assert called_request_id == request_ids[1]


def test_auto_resolve_all_returns_empty_list_without_requests_document(tmp_path: Path) -> None:
    project = _make_project(tmp_path)
    results = auto_resolve_all_cut_plan_supplement_requests(
        project, query_llm_provider="gemini", query_llm_model="gemini-3.1-flash-lite"
    )
    assert results == []


def test_auto_resolve_all_one_request_failing_does_not_stop_the_batch(tmp_path: Path) -> None:
    """auto_resolve_cut_plan_supplement_request wirft laut eigenem Vertrag
    nie eine Exception — aber falls doch (Programmfehler), soll der Batch
    trotzdem robust bleiben und den zweiten Request weiterhin versuchen.
    Hier simuliert über unterschiedliche Rückgabe je Request_id."""
    project, request_ids = _setup_two_requests(tmp_path)

    def _fake_resolve(project_arg, request_id, **kwargs):
        status = AUTO_RESOLVE_STATUS_NO_MATCH if request_id == request_ids[0] else AUTO_RESOLVE_STATUS_ACCEPTED
        return CutPlanSupplementAutoResolveResult(status=status, request_id=request_id)

    with patch(f"{_MODULE}.auto_resolve_cut_plan_supplement_request", side_effect=_fake_resolve):
        results = auto_resolve_all_cut_plan_supplement_requests(
            project, query_llm_provider="gemini", query_llm_model="gemini-3.1-flash-lite"
        )

    assert [r.status for r in results] == [AUTO_RESOLVE_STATUS_NO_MATCH, AUTO_RESOLVE_STATUS_ACCEPTED]


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
