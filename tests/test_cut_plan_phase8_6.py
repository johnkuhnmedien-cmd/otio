"""Phase 8.6: Isolierte Supplement Bridge für den Cut Plan.

Noch KEIN Confirm/Lock, kein EditPlanDocument, kein OTIO-Export, kein
LLM-Konfliktlöser, keine Vermischung mit der bestehenden Produktions-
Supplement-Pipeline, kein automatisches Schreiben in reguläre Folder-
Inventories."""

from __future__ import annotations

import inspect
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from otio_app.analysis_models import (
    AssetFolderAnalysis,
    AssetMediaAnalysis,
    SupplementAssetSidecar,
    SupplementCandidate,
)
from otio_app.defaults import (
    CUT_PLAN_ASSET_SELECTION_SUPPLEMENT_USED,
    CUT_PLAN_ERROR_BLACK_GAP_DURING_VOICEOVER,
    CUT_PLAN_ERROR_MISSING_ALIGNMENT,
    CUT_PLAN_ERROR_SUPPLEMENT_REQUIRED,
)
from otio_app.models import Project, ProjectMode
from otio_app.project_layout import (
    get_edit_plan_dir,
    get_exports_dir,
    get_folder_inventory_path,
    get_supplement_dir,
)
from otio_app.services.supplement_sources.base import SupplementAsset
from otio_app.services.voiceover_generation.cut_plan_builder import (
    build_cut_plan_draft,
    load_cut_plan_draft,
    save_cut_plan_draft,
)
from otio_app.services.voiceover_generation.cut_plan_models import (
    CutPlanAudioItem,
    CutPlanDocument,
    CutPlanItem,
    CutPlanSourceRef,
)
from otio_app.services.voiceover_generation.cut_plan_settings_service import save_cut_plan_settings
from otio_app.services.voiceover_generation.cut_plan_models import CutPlanSettings
from otio_app.services.voiceover_generation.cut_plan_supplement_bridge import (
    accept_cut_plan_supplement_candidate,
    apply_accepted_supplement_to_cut_plan_item,
    build_supplement_requests_from_cut_plan,
    count_unapplied_accepted_supplement_requests,
    download_cut_plan_supplement_candidate,
    effective_cut_plan_supplement_request_status,
    find_reusable_supplement_manifest_entry,
    load_cut_plan_supplement_candidates_for_request,
    load_cut_plan_supplement_manifest,
    load_cut_plan_supplement_requests,
    merge_prior_supplement_request_state,
    reapply_accepted_supplements_to_cut_plan,
    record_supplement_manifest_entry,
    record_supplement_manifest_validation,
    save_cut_plan_supplement_manifest,
    save_cut_plan_supplement_requests,
    search_candidates_for_cut_plan_request,
    stable_supplement_asset_id,
)
from otio_app.services.voiceover_generation.cut_plan_supplement_models import (
    CutPlanSupplementAsset,
    CutPlanSupplementManifestEntry,
    CutPlanSupplementRequest,
)
from otio_app.services.voiceover_generation.final_plan_service import (
    save_confirmed_voiceover_project_plan,
)
from otio_app.services.voiceover_generation.models import (
    AlignmentItem,
    ConfirmedFolderPlanItem,
    ConfirmedIntroPlanItem,
    ConfirmedVoiceoverProjectPlan,
    SentenceItem,
)
from otio_app.ui.voiceover_generation.cut_plan_tab import render_cut_plan_page

FOLDER_A = "Grand Canyon"

_BRIDGE_MODULE = "otio_app.services.voiceover_generation.cut_plan_supplement_bridge"


def _make_project(tmp_path: Path) -> Project:
    project_root = tmp_path / "USA"
    (project_root / FOLDER_A).mkdir(parents=True)
    return Project(
        id="cut-plan-supplement-project",
        name="Cut Plan Supplement Test",
        project_root=str(project_root),
        work_dir=str(project_root / "_otio"),
        project_mode=ProjectMode.WITHOUT_VOICEOVER,
        asset_subdir_names=[FOLDER_A],
        selected_asset_subdirs=[FOLDER_A],
    )


def _write_inventory(project: Project, filenames: list[str] | None = None) -> None:
    entries = []
    for filename in filenames or []:
        (project.project_root_path / FOLDER_A / filename).write_bytes(b"FAKE_MEDIA_BYTES")
        entries.append(AssetMediaAnalysis(path=f"{FOLDER_A}/{filename}", description=filename))
    inv_path = get_folder_inventory_path(project.work_dir_path, FOLDER_A)
    inv_path.parent.mkdir(parents=True, exist_ok=True)
    inv_path.write_text(
        AssetFolderAnalysis(folder=FOLDER_A, assets=entries).model_dump_json(indent=2), encoding="utf-8"
    )


def _write_audio(project: Project, name: str = "folder.mp3") -> Path:
    audio_dir = project.language_work_dir_path / "voiceover_generation" / "audio"
    audio_dir.mkdir(parents=True, exist_ok=True)
    path = audio_dir / name
    path.write_bytes(b"FAKE_AUDIO_BYTES")
    return path


def _project_with_supplement_required_draft(tmp_path: Path) -> Project:
    """Ein Projekt mit genau einem Folder-Item, das needs_supplement_asset=true
    hat (kein lokales Asset verfügbar) — führt nach dem Draft-Bau direkt zu
    einem SUPPLEMENT_REQUIRED-Bedarf, ohne Asset-Auswahl laufen zu müssen."""
    project = _make_project(tmp_path)
    _write_inventory(project, [])
    audio_path = _write_audio(project)

    folder = ConfirmedFolderPlanItem(
        folder_name=FOLDER_A,
        order_index=1,
        audio_path=str(audio_path),
        audio_duration_sec=5.0,
        sentence_items=[
            SentenceItem(
                sentence_id="sentence_001",
                text="Ein Canyon im Abendlicht.",
                visual_intent="wide canyon shot at sunset",
                needs_supplement_asset=True,
                supplement_reason="No local asset available.",
            )
        ],
        alignment_items=[
            AlignmentItem(sentence_id="sentence_001", audio_start_sec=0.0, audio_end_sec=5.0, duration_sec=5.0)
        ],
    )
    plan = ConfirmedVoiceoverProjectPlan(
        project_id=project.id, project_title="Test", status="AUDIO_READY",
        intro=ConfirmedIntroPlanItem(), folders=[folder],
    )
    save_confirmed_voiceover_project_plan(project, plan)
    save_cut_plan_settings(project, CutPlanSettings(project_id=project.id))
    draft = build_cut_plan_draft(project)
    save_cut_plan_draft(project, draft)
    return project


def _fake_candidate(
    *, candidate_id: str = "cand_fake01", media_type: str = "video", duration_sec: float = 10.0,
    request_id: str = "cutreq_x", provider_asset_id: str = "", folder_name: str = "",
) -> SupplementCandidate:
    return SupplementCandidate(
        candidate_id=candidate_id,
        supplement_request_id=request_id,
        provider="pexels",
        provider_asset_id=provider_asset_id,
        title="Fake Canyon",
        media_type=media_type,
        width=1920,
        height=1080,
        duration_sec=duration_sec,
        download_url="https://example.com/fake.mp4",
        download_enabled=True,
        is_mock=False,
        requires_user_approval=False,
        match_score=0.9,
        folder_name=folder_name,
    )


def _patch_project_selector(project: Project, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("otio_app.ui.project_context.list_projects", lambda: [project])
    monkeypatch.setattr(
        "otio_app.ui.project_context.get_project_by_id",
        lambda project_id: project if project_id == project.id else None,
    )
    monkeypatch.setattr("streamlit.session_state", {"active_project_id": project.id}, raising=False)


def _minimal_cut_plan(project: Project, **overrides) -> CutPlanDocument:
    defaults = dict(project_id=project.id, timeline_fps=25)
    defaults.update(overrides)
    return CutPlanDocument(**defaults)


def _minimal_item(**overrides) -> CutPlanItem:
    defaults = dict(
        cut_item_id="cut_001", source_refs=[CutPlanSourceRef(source_sentence_id="s1", text="Text")],
        source_scope="folder", folder_name=FOLDER_A, text="Ein Satz.", timeline_start_sec=1.0,
        timeline_end_sec=6.0, duration_sec=5.0, audio_start_sec=0.0, audio_end_sec=5.0,
        chosen_asset_id="", asset_selection_status="SUPPLEMENT_REQUIRED",
        needs_supplement_asset=True, supplement_reason="No local asset available.",
        blockers=[CUT_PLAN_ERROR_SUPPLEMENT_REQUIRED],
    )
    defaults.update(overrides)
    return CutPlanItem(**defaults)


def _minimal_audio_item(**overrides) -> CutPlanAudioItem:
    defaults = dict(
        scope="folder", folder_name=FOLDER_A, audio_path="/fake/a.mp3", timeline_start_sec=1.0,
        timeline_end_sec=6.0, duration_sec=5.0, source_in_sec=0.0, track="A1",
    )
    defaults.update(overrides)
    return CutPlanAudioItem(**defaults)


def _accepted_asset(**overrides) -> CutPlanSupplementAsset:
    defaults = dict(
        asset_id="cut_supplement_cutreq_cut_001_cand_1", request_id="cutreq_cut_001", candidate_id="cand_1",
        provider="pexels", asset_path="/fake/downloaded.jpg", asset_type="image", duration_sec=0.0,
    )
    defaults.update(overrides)
    return CutPlanSupplementAsset(**defaults)


def _supplement_request(**overrides) -> CutPlanSupplementRequest:
    defaults = dict(request_id="cutreq_cut_001", cut_item_id="cut_001", folder_name=FOLDER_A)
    defaults.update(overrides)
    return CutPlanSupplementRequest(**defaults)


# --- 1-4: Request-Erzeugung ---


def test_requests_are_built_from_supplement_required_items(tmp_path: Path) -> None:
    project = _make_project(tmp_path)
    item = _minimal_item()
    cut_plan = _minimal_cut_plan(project, items=[item])

    document = build_supplement_requests_from_cut_plan(project, cut_plan)
    assert len(document.requests) == 1
    assert document.requests[0].cut_item_id == "cut_001"


def test_requests_are_deduplicated_by_cut_item_id(tmp_path: Path) -> None:
    project = _make_project(tmp_path)
    item = _minimal_item()
    # Simuliert denselben cut_item_id doppelt (z. B. durch fehlerhaften Aufrufer).
    cut_plan = _minimal_cut_plan(project, items=[item, item.model_copy()])

    document = build_supplement_requests_from_cut_plan(project, cut_plan)
    assert len(document.requests) == 1


def test_request_contains_visual_intent_text_and_duration(tmp_path: Path) -> None:
    project = _make_project(tmp_path)
    item = _minimal_item(visual_intent="wide canyon shot", text="Ein Canyon im Abendlicht.", duration_sec=7.5)
    cut_plan = _minimal_cut_plan(project, items=[item])

    document = build_supplement_requests_from_cut_plan(project, cut_plan)
    request = document.requests[0]
    assert request.visual_intent == "wide canyon shot"
    assert request.text == "Ein Canyon im Abendlicht."
    assert request.needed_duration_sec == pytest.approx(7.5)
    assert request.reason == "No local asset available."


def test_requests_are_built_for_items_blocked_by_reuse_policy(tmp_path: Path) -> None:
    """Phase A (Nutzervorgabe): ein Item, das NICHT ursprünglich
    needs_supplement_asset=true war, sondern erst durch die
    Asset-Auswahl (Reuse-/Usage-Regel-Verletzung, siehe
    cut_plan_asset_selector._supplement_required_copy) supplementierbar
    wurde, muss trotzdem einen Supplement Request bekommen — mit dem
    konkreten Reuse-Grund als request.reason, nicht dem generischen
    Standardtext."""
    project = _make_project(tmp_path)
    item = _minimal_item(
        asset_selection_reason="Alle Kandidaten verletzen Usage-Regeln (ASSET_REUSE_DISTANCE_TOO_SHORT).",
        supplement_reason=(
            "Passende lokale Assets sind vorhanden, verletzen aber die Wiederverwendungs-Regeln "
            "(ASSET_REUSE_DISTANCE_TOO_SHORT). Es wird ein zusätzliches, DISTINKTES Ersatz-Asset für "
            "diese Szene benötigt (kein bereits verwendetes Motiv erneut)."
        ),
        warnings=["ASSET_REUSE_DISTANCE_TOO_SHORT"],
    )
    cut_plan = _minimal_cut_plan(project, items=[item])

    document = build_supplement_requests_from_cut_plan(project, cut_plan)
    assert len(document.requests) == 1
    request = document.requests[0]
    assert "DISTINKTES" in request.reason
    assert "ASSET_REUSE_DISTANCE_TOO_SHORT" in request.reason


def test_supplement_requests_file_is_written(tmp_path: Path) -> None:
    project = _project_with_supplement_required_draft(tmp_path)
    draft = load_cut_plan_draft(project)
    document = build_supplement_requests_from_cut_plan(project, draft)
    path = save_cut_plan_supplement_requests(project, document)
    assert path.is_file()
    assert path.name == "supplement_requests.from_cut_plan.json"
    reloaded = load_cut_plan_supplement_requests(project)
    assert reloaded is not None
    assert len(reloaded.requests) == 1


# --- Phase F: Requests aus Validierungs-Blockern (nicht nur Asset-Auswahl) ---


def test_requests_are_built_for_missing_asset_mapping_found_only_at_validation_time(tmp_path: Path) -> None:
    """Ein Item, dessen Asset-Auswahl NICHT SUPPLEMENT_REQUIRED gesetzt hat
    (needs_supplement_asset=False, status != SUPPLEMENT_REQUIRED), aber
    dessen chosen_asset_id leer ist und das per vollständiger Validierung
    (attach_validation_to_cut_plan) mit MISSING_ASSET_MAPPING geflaggt
    wurde, muss trotzdem einen Supplement Request bekommen."""
    project = _make_project(tmp_path)
    item = _minimal_item(
        chosen_asset_id="", asset_selection_status="UNRESOLVED", needs_supplement_asset=False,
        supplement_reason="", asset_selection_reason="", blockers=["MISSING_ASSET_MAPPING"],
    )
    cut_plan = _minimal_cut_plan(project, items=[item])

    document = build_supplement_requests_from_cut_plan(project, cut_plan)
    assert len(document.requests) == 1
    assert "MISSING_ASSET_MAPPING" in document.requests[0].reason


def test_requests_are_not_built_for_missing_asset_mapping_when_timing_blocked(tmp_path: Path) -> None:
    """Ein Item mit einem ECHTEN Timing-Problem (fehlendes Alignment) darf
    NICHT als Supplement-Bedarf missverstanden werden — timeline_start_sec/
    duration_sec sind in diesem Fall typischerweise 0.0, ein Supplement
    Request wäre sinnlos."""
    project = _make_project(tmp_path)
    item = _minimal_item(
        chosen_asset_id="", asset_selection_status="BLOCKED", needs_supplement_asset=False,
        supplement_reason="", asset_selection_reason="", duration_sec=0.0, timeline_start_sec=0.0,
        timeline_end_sec=0.0, blockers=["MISSING_ASSET_MAPPING", "MISSING_ALIGNMENT"],
    )
    cut_plan = _minimal_cut_plan(project, items=[item])

    document = build_supplement_requests_from_cut_plan(project, cut_plan)
    assert len(document.requests) == 0


def test_requests_are_built_for_reuse_distance_blocker_found_only_at_validation_time(tmp_path: Path) -> None:
    """Ein Item mit BEREITS gewähltem Asset (PRIMARY_USED), das erst bei
    der vollständigen Validierung (über ALLE platzierten VisualSegments,
    siehe validate_asset_usage) als zu früh wiederverwendet erkannt wird —
    choose_asset_for_cut_item selbst hatte das nicht gesehen (sequenzielle,
    nur rückwärts schauende Prüfung) — muss trotzdem supplementiert werden
    können, auch wenn ein Asset bereits zugewiesen ist."""
    project = _make_project(tmp_path)
    item = _minimal_item(
        chosen_asset_id="asset_already_chosen", asset_selection_status="PRIMARY_USED",
        needs_supplement_asset=False, supplement_reason="", asset_selection_reason="",
        blockers=["ASSET_REUSE_DISTANCE_TOO_SHORT"],
    )
    cut_plan = _minimal_cut_plan(project, items=[item])

    document = build_supplement_requests_from_cut_plan(project, cut_plan)
    assert len(document.requests) == 1
    assert "ASSET_REUSE_DISTANCE_TOO_SHORT" in document.requests[0].reason


def test_requests_are_not_built_for_already_serviced_items(tmp_path: Path) -> None:
    """Ein Item, das bereits über ein Supplement/generischen Fallback/
    manuelle Zuweisung versorgt ist, wird NICHT stillschweigend erneut
    supplementiert, nur weil die Validierung zusätzlich einen asset-
    bezogenen Blocker meldet — 'Ersetzen' bleibt eine bewusste
    Nutzeraktion (force_replace)."""
    project = _make_project(tmp_path)
    for status in ("SUPPLEMENT_USED", "GENERIC_FALLBACK_USED", "MANUAL_ASSET_USED"):
        item = _minimal_item(
            cut_item_id=f"cut_{status}", chosen_asset_id="asset_x", asset_selection_status=status,
            needs_supplement_asset=False, supplement_reason="", asset_selection_reason="",
            blockers=["ASSET_REUSE_DISTANCE_TOO_SHORT"],
        )
        cut_plan = _minimal_cut_plan(project, items=[item])
        document = build_supplement_requests_from_cut_plan(project, cut_plan)
        assert len(document.requests) == 0, f"unexpected request for status={status}"


def test_requests_are_not_built_when_duration_is_zero_even_without_timing_blocker(tmp_path: Path) -> None:
    """Defensive Absicherung: auch ohne einen expliziten Timing-Blocker
    darf ein Item mit duration_sec<=0 keinen Supplement Request auslösen
    (es gäbe nichts Sinnvolles zu beschaffen)."""
    project = _make_project(tmp_path)
    item = _minimal_item(
        chosen_asset_id="", asset_selection_status="UNRESOLVED", needs_supplement_asset=False,
        supplement_reason="", asset_selection_reason="", duration_sec=0.0,
        blockers=["MISSING_ASSET_MAPPING"],
    )
    cut_plan = _minimal_cut_plan(project, items=[item])

    document = build_supplement_requests_from_cut_plan(project, cut_plan)
    assert len(document.requests) == 0


def test_requests_are_built_for_items_with_black_gap_blocker_from_validation(tmp_path: Path) -> None:
    """Phase G: seit validate_no_black_gap_during_voiceover das
    verantwortliche Item ermittelt, muss build_supplement_requests_from_
    cut_plan (Phase F) dafür einen Supplement Request erzeugen."""
    project = _make_project(tmp_path)
    item = _minimal_item(
        chosen_asset_id="", asset_selection_status="UNRESOLVED", needs_supplement_asset=False,
        supplement_reason="", asset_selection_reason="", blockers=["BLACK_GAP_DURING_VOICEOVER"],
    )
    cut_plan = _minimal_cut_plan(project, items=[item])

    document = build_supplement_requests_from_cut_plan(project, cut_plan)
    assert len(document.requests) == 1
    assert "BLACK_GAP_DURING_VOICEOVER" in document.requests[0].reason


def test_requests_are_not_built_for_items_without_any_asset_related_blocker(tmp_path: Path) -> None:
    """Ein völlig unauffälliges, korrekt versorgtes Item darf keinen
    Supplement Request erzeugen — Regressionsschutz gegen zu aggressive
    Phase-F-Erkennung."""
    project = _make_project(tmp_path)
    item = _minimal_item(
        chosen_asset_id="asset_x", asset_selection_status="PRIMARY_USED",
        needs_supplement_asset=False, supplement_reason="", asset_selection_reason="", blockers=[],
    )
    cut_plan = _minimal_cut_plan(project, items=[item])

    document = build_supplement_requests_from_cut_plan(project, cut_plan)
    assert len(document.requests) == 0


# --- 5-6: Isolation / kein automatischer Search-Trigger ---


def test_no_file_written_under_supplement_dir(tmp_path: Path) -> None:
    project = _project_with_supplement_required_draft(tmp_path)
    draft = load_cut_plan_draft(project)
    document = build_supplement_requests_from_cut_plan(project, draft)
    save_cut_plan_supplement_requests(project, document)
    assert not get_supplement_dir(project.language_work_dir_path).exists()


def test_search_is_not_triggered_automatically_on_request_build(tmp_path: Path) -> None:
    project = _make_project(tmp_path)
    item = _minimal_item()
    cut_plan = _minimal_cut_plan(project, items=[item])

    with patch(f"{_BRIDGE_MODULE}.get_supplement_adapter") as mock_get_adapter:
        build_supplement_requests_from_cut_plan(project, cut_plan)
    mock_get_adapter.assert_not_called()


# --- 7-9: Kandidatensuche (Mock-Provider, nur bei explizitem Aufruf) ---


def test_search_with_mock_provider_saves_candidates(tmp_path: Path) -> None:
    project = _project_with_supplement_required_draft(tmp_path)
    draft = load_cut_plan_draft(project)
    document = build_supplement_requests_from_cut_plan(project, draft)
    save_cut_plan_supplement_requests(project, document)
    request_id = document.requests[0].request_id

    mock_adapter = MagicMock()
    mock_adapter.search.return_value = [_fake_candidate(request_id=request_id)]

    with patch(f"{_BRIDGE_MODULE}.get_supplement_adapter", return_value=mock_adapter):
        result = search_candidates_for_cut_plan_request(project, request_id, {"provider": "pexels"})

    assert result.status == "READY"
    assert len(result.candidates) == 1
    mock_adapter.search.assert_called_once()


# --- Phase 11.1/11.2: LLM-Suchqueries + Video/Foto + 5 Kandidaten ---


def test_search_uses_any_asset_type_and_five_max_candidates_by_default(tmp_path: Path) -> None:
    """Phase 11.2: Standardwerte fuer den Cut-Plan-Workflow (Video+Foto,
    5 statt 3 Kandidaten) — unabhaengig davon, ob eine LLM-Query erzeugt
    wurde."""
    project = _project_with_supplement_required_draft(tmp_path)
    draft = load_cut_plan_draft(project)
    document = build_supplement_requests_from_cut_plan(project, draft)
    save_cut_plan_supplement_requests(project, document)
    request_id = document.requests[0].request_id

    mock_adapter = MagicMock()
    mock_adapter.search.return_value = [_fake_candidate(request_id=request_id)]

    with patch(f"{_BRIDGE_MODULE}.get_supplement_adapter", return_value=mock_adapter):
        search_candidates_for_cut_plan_request(project, request_id, {"provider": "pexels"})

    sent_request = mock_adapter.search.call_args[0][0]
    assert sent_request.required_asset_type == "any"
    assert sent_request.max_candidates == 5


def test_search_skips_llm_query_generation_without_provider_and_model(tmp_path: Path) -> None:
    """Ohne query_llm_provider/query_llm_model (Standard) wird KEIN LLM-
    Aufruf ausgeloest — exaktes altes Verhalten fuer alle bestehenden
    Aufrufer (u. a. alle anderen Tests in dieser Datei)."""
    project = _project_with_supplement_required_draft(tmp_path)
    draft = load_cut_plan_draft(project)
    document = build_supplement_requests_from_cut_plan(project, draft)
    save_cut_plan_supplement_requests(project, document)
    request_id = document.requests[0].request_id

    mock_adapter = MagicMock()
    mock_adapter.search.return_value = [_fake_candidate(request_id=request_id)]

    with (
        patch(f"{_BRIDGE_MODULE}.get_supplement_adapter", return_value=mock_adapter),
        patch(f"{_BRIDGE_MODULE}.generate_cut_plan_supplement_queries") as mock_llm_query,
    ):
        search_candidates_for_cut_plan_request(project, request_id, {"provider": "pexels"})

    mock_llm_query.assert_not_called()
    sent_request = mock_adapter.search.call_args[0][0]
    assert sent_request.llm_generated_queries == []


def test_search_calls_llm_query_generation_and_passes_queries_to_adapter(tmp_path: Path) -> None:
    """Phase 11.1: mit query_llm_provider/query_llm_model wird vor der
    Provider-Suche ein LLM-Aufruf ausgeloest, dessen Queries in
    SupplementRequest.llm_generated_queries landen UND auf dem
    CutPlanSupplementRequest fuer die UI-Anzeige persistiert werden."""
    project = _project_with_supplement_required_draft(tmp_path)
    draft = load_cut_plan_draft(project)
    document = build_supplement_requests_from_cut_plan(project, draft)
    save_cut_plan_supplement_requests(project, document)
    request_id = document.requests[0].request_id

    mock_adapter = MagicMock()
    mock_adapter.search.return_value = [_fake_candidate(request_id=request_id)]

    from otio_app.services.voiceover_generation.cut_plan_supplement_query_service import (
        CutPlanSupplementQueryResult,
    )

    fake_result = CutPlanSupplementQueryResult(
        status="PASS",
        queries=["Grand Canyon rock formation", "Grand Canyon carved road", "Grand Canyon historic trail"],
        run_id="run_fake_123",
        provider="gemini",
        model="gemini-3.1-flash-lite",
    )

    with (
        patch(f"{_BRIDGE_MODULE}.get_supplement_adapter", return_value=mock_adapter),
        patch(f"{_BRIDGE_MODULE}.generate_cut_plan_supplement_queries", return_value=fake_result) as mock_llm_query,
    ):
        search_candidates_for_cut_plan_request(
            project,
            request_id,
            {"provider": "pexels"},
            query_llm_provider="gemini",
            query_llm_model="gemini-3.1-flash-lite",
        )

    mock_llm_query.assert_called_once()
    sent_request = mock_adapter.search.call_args[0][0]
    assert sent_request.llm_generated_queries == fake_result.queries

    reloaded = load_cut_plan_supplement_requests(project)
    persisted = next(r for r in reloaded.requests if r.request_id == request_id)
    assert persisted.llm_queries == fake_result.queries
    assert persisted.llm_query_status == "PASS"
    assert persisted.llm_query_run_id == "run_fake_123"


def test_search_reuses_persisted_llm_queries_when_skip_flag_set(tmp_path: Path) -> None:
    """Phase 12.9: skip_llm_query_generation nutzt bereits persistierte
    llm_queries statt einen erneuten LLM-Aufruf auszulösen."""
    project = _project_with_supplement_required_draft(tmp_path)
    draft = load_cut_plan_draft(project)
    document = build_supplement_requests_from_cut_plan(project, draft)
    save_cut_plan_supplement_requests(project, document)
    request_id = document.requests[0].request_id

    from otio_app.services.voiceover_generation.cut_plan_supplement_bridge import (
        update_cut_plan_supplement_request,
    )

    update_cut_plan_supplement_request(
        project,
        request_id,
        llm_queries=["Grand Canyon rock formation"],
        llm_query_status="PASS",
        llm_query_run_id="run_existing",
    )

    mock_adapter = MagicMock()
    mock_adapter.search.return_value = [_fake_candidate(request_id=request_id)]

    with (
        patch(f"{_BRIDGE_MODULE}.get_supplement_adapter", return_value=mock_adapter),
        patch(f"{_BRIDGE_MODULE}.generate_cut_plan_supplement_queries") as mock_llm_query,
    ):
        search_candidates_for_cut_plan_request(
            project,
            request_id,
            {"provider": "pexels"},
            query_llm_provider="gemini",
            query_llm_model="gemini-3.1-flash-lite",
            skip_llm_query_generation=True,
        )

    mock_llm_query.assert_not_called()
    sent_request = mock_adapter.search.call_args[0][0]
    assert sent_request.llm_generated_queries == ["Grand Canyon rock formation"]


def test_search_falls_back_to_deterministic_query_when_llm_query_fails(tmp_path: Path) -> None:
    """Schlaegt die LLM-Query-Generierung fehl, wird trotzdem gesucht — nur
    ohne llm_generated_queries (deterministischer Fallback in
    build_pexels_query_variants greift automatisch)."""
    project = _project_with_supplement_required_draft(tmp_path)
    draft = load_cut_plan_draft(project)
    document = build_supplement_requests_from_cut_plan(project, draft)
    save_cut_plan_supplement_requests(project, document)
    request_id = document.requests[0].request_id

    mock_adapter = MagicMock()
    mock_adapter.search.return_value = [_fake_candidate(request_id=request_id)]

    from otio_app.services.voiceover_generation.cut_plan_supplement_query_service import (
        CutPlanSupplementQueryResult,
    )

    fake_result = CutPlanSupplementQueryResult(
        status="FAIL", queries=[], run_id="run_fail_456", error="network down"
    )

    with (
        patch(f"{_BRIDGE_MODULE}.get_supplement_adapter", return_value=mock_adapter),
        patch(f"{_BRIDGE_MODULE}.generate_cut_plan_supplement_queries", return_value=fake_result),
    ):
        result = search_candidates_for_cut_plan_request(
            project,
            request_id,
            {"provider": "pexels"},
            query_llm_provider="gemini",
            query_llm_model="gemini-3.1-flash-lite",
        )

    assert result.status == "READY"
    sent_request = mock_adapter.search.call_args[0][0]
    assert sent_request.llm_generated_queries == []

    reloaded = load_cut_plan_supplement_requests(project)
    persisted = next(r for r in reloaded.requests if r.request_id == request_id)
    assert persisted.llm_query_status == "FAIL"
    assert persisted.llm_query_error == "network down"
    assert persisted.llm_queries == []


# --- Phase 9 (Asset-bewusste Cut-Plan-Vorbereitung): supplement_search_hint ---


def _project_with_supplement_required_draft_and_hint(
    tmp_path: Path, *, supplement_search_hint: str
) -> Project:
    """Wie _project_with_supplement_required_draft, aber mit einem bereits
    beim Skriptschreiben vorbereiteten Suchvorschlag (visual_asset_plan.
    supplement_search_hint) auf dem einzigen Sentence-Item."""
    from otio_app.services.voiceover_generation.models import VisualAssetPlanHint

    project = _make_project(tmp_path)
    _write_inventory(project, [])
    audio_path = _write_audio(project)

    folder = ConfirmedFolderPlanItem(
        folder_name=FOLDER_A,
        order_index=1,
        audio_path=str(audio_path),
        audio_duration_sec=5.0,
        sentence_items=[
            SentenceItem(
                sentence_id="sentence_001",
                text="Ein Canyon im Abendlicht.",
                visual_intent="wide canyon shot at sunset",
                needs_supplement_asset=True,
                supplement_reason="No local asset available.",
                visual_asset_plan=VisualAssetPlanHint(supplement_search_hint=supplement_search_hint),
            )
        ],
        alignment_items=[
            AlignmentItem(sentence_id="sentence_001", audio_start_sec=0.0, audio_end_sec=5.0, duration_sec=5.0)
        ],
    )
    plan = ConfirmedVoiceoverProjectPlan(
        project_id=project.id, project_title="Test", status="AUDIO_READY",
        intro=ConfirmedIntroPlanItem(), folders=[folder],
    )
    save_confirmed_voiceover_project_plan(project, plan)
    save_cut_plan_settings(project, CutPlanSettings(project_id=project.id))
    draft = build_cut_plan_draft(project)
    save_cut_plan_draft(project, draft)
    return project


def test_build_supplement_requests_copies_supplement_search_hint(tmp_path: Path) -> None:
    project = _project_with_supplement_required_draft_and_hint(
        tmp_path, supplement_search_hint="Grand Canyon rim wide shot"
    )
    draft = load_cut_plan_draft(project)
    document = build_supplement_requests_from_cut_plan(project, draft)
    assert document.requests[0].supplement_search_hint == "Grand Canyon rim wide shot"


def test_build_supplement_requests_defaults_hint_to_empty_when_absent(tmp_path: Path) -> None:
    project = _project_with_supplement_required_draft(tmp_path)
    draft = load_cut_plan_draft(project)
    document = build_supplement_requests_from_cut_plan(project, draft)
    assert document.requests[0].supplement_search_hint == ""


def test_search_uses_supplement_search_hint_as_sole_query_without_llm_generation(
    tmp_path: Path,
) -> None:
    """Ohne query_llm_provider/query_llm_model wird trotzdem der bereits
    vorbereitete Suchvorschlag als Query verwendet — unabhängig vom
    separaten Query-Generierungs-LLM-Aufruf."""
    project = _project_with_supplement_required_draft_and_hint(
        tmp_path, supplement_search_hint="Grand Canyon rim wide shot"
    )
    draft = load_cut_plan_draft(project)
    document = build_supplement_requests_from_cut_plan(project, draft)
    save_cut_plan_supplement_requests(project, document)
    request_id = document.requests[0].request_id

    mock_adapter = MagicMock()
    mock_adapter.search.return_value = [_fake_candidate(request_id=request_id)]

    with (
        patch(f"{_BRIDGE_MODULE}.get_supplement_adapter", return_value=mock_adapter),
        patch(f"{_BRIDGE_MODULE}.generate_cut_plan_supplement_queries") as mock_llm_query,
    ):
        search_candidates_for_cut_plan_request(project, request_id, {"provider": "pexels"})

    mock_llm_query.assert_not_called()
    sent_request = mock_adapter.search.call_args[0][0]
    assert sent_request.llm_generated_queries == ["Grand Canyon rim wide shot"]


def test_search_prepends_supplement_search_hint_before_llm_generated_queries(
    tmp_path: Path,
) -> None:
    """Der vorbereitete Suchvorschlag hat Priorität VOR den nachträglich
    per LLM generierten Queries — beide werden kombiniert, nicht ersetzt."""
    project = _project_with_supplement_required_draft_and_hint(
        tmp_path, supplement_search_hint="Grand Canyon rim wide shot"
    )
    draft = load_cut_plan_draft(project)
    document = build_supplement_requests_from_cut_plan(project, draft)
    save_cut_plan_supplement_requests(project, document)
    request_id = document.requests[0].request_id

    mock_adapter = MagicMock()
    mock_adapter.search.return_value = [_fake_candidate(request_id=request_id)]

    from otio_app.services.voiceover_generation.cut_plan_supplement_query_service import (
        CutPlanSupplementQueryResult,
    )

    fake_result = CutPlanSupplementQueryResult(
        status="PASS",
        queries=["Grand Canyon rock formation", "Grand Canyon carved road"],
        run_id="run_fake_789",
        provider="gemini",
        model="gemini-3.1-flash-lite",
    )

    with (
        patch(f"{_BRIDGE_MODULE}.get_supplement_adapter", return_value=mock_adapter),
        patch(f"{_BRIDGE_MODULE}.generate_cut_plan_supplement_queries", return_value=fake_result),
    ):
        search_candidates_for_cut_plan_request(
            project,
            request_id,
            {"provider": "pexels"},
            query_llm_provider="gemini",
            query_llm_model="gemini-3.1-flash-lite",
        )

    sent_request = mock_adapter.search.call_args[0][0]
    assert sent_request.llm_generated_queries == [
        "Grand Canyon rim wide shot",
        "Grand Canyon rock formation",
        "Grand Canyon carved road",
    ]

    # Das reine LLM-Query-Trace-Feld bleibt unverändert nur das Ergebnis des
    # separaten Query-Generierungs-Aufrufs (ohne den Hint).
    reloaded = load_cut_plan_supplement_requests(project)
    persisted = next(r for r in reloaded.requests if r.request_id == request_id)
    assert persisted.llm_queries == fake_result.queries


def test_search_uses_hint_alone_when_llm_query_generation_fails(tmp_path: Path) -> None:
    """Schlägt die separate Query-Generierung fehl, bleibt der vorbereitete
    Suchvorschlag trotzdem als Query erhalten (robuster als der reine
    deterministische Fallback allein)."""
    project = _project_with_supplement_required_draft_and_hint(
        tmp_path, supplement_search_hint="Grand Canyon rim wide shot"
    )
    draft = load_cut_plan_draft(project)
    document = build_supplement_requests_from_cut_plan(project, draft)
    save_cut_plan_supplement_requests(project, document)
    request_id = document.requests[0].request_id

    mock_adapter = MagicMock()
    mock_adapter.search.return_value = [_fake_candidate(request_id=request_id)]

    from otio_app.services.voiceover_generation.cut_plan_supplement_query_service import (
        CutPlanSupplementQueryResult,
    )

    fake_result = CutPlanSupplementQueryResult(status="FAIL", queries=[], error="network down")

    with (
        patch(f"{_BRIDGE_MODULE}.get_supplement_adapter", return_value=mock_adapter),
        patch(f"{_BRIDGE_MODULE}.generate_cut_plan_supplement_queries", return_value=fake_result),
    ):
        search_candidates_for_cut_plan_request(
            project,
            request_id,
            {"provider": "pexels"},
            query_llm_provider="gemini",
            query_llm_model="gemini-3.1-flash-lite",
        )

    sent_request = mock_adapter.search.call_args[0][0]
    assert sent_request.llm_generated_queries == ["Grand Canyon rim wide shot"]


def test_candidates_are_saved_to_candidates_file(tmp_path: Path) -> None:
    project = _project_with_supplement_required_draft(tmp_path)
    draft = load_cut_plan_draft(project)
    document = build_supplement_requests_from_cut_plan(project, draft)
    save_cut_plan_supplement_requests(project, document)
    request_id = document.requests[0].request_id

    mock_adapter = MagicMock()
    mock_adapter.search.return_value = [_fake_candidate(request_id=request_id)]

    with patch(f"{_BRIDGE_MODULE}.get_supplement_adapter", return_value=mock_adapter):
        search_candidates_for_cut_plan_request(project, request_id, {"provider": "pexels"})

    from otio_app.project_layout import get_cut_plan_supplement_candidates_path

    path = get_cut_plan_supplement_candidates_path(project.language_work_dir_path)
    assert path.is_file()
    reloaded = load_cut_plan_supplement_candidates_for_request(project, request_id)
    assert reloaded is not None
    assert len(reloaded.candidates) == 1


def test_provider_error_produces_failed_without_leaking_api_key(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    project = _project_with_supplement_required_draft(tmp_path)
    draft = load_cut_plan_draft(project)
    document = build_supplement_requests_from_cut_plan(project, draft)
    save_cut_plan_supplement_requests(project, document)
    request_id = document.requests[0].request_id

    monkeypatch.setattr(f"{_BRIDGE_MODULE}.get_api_key", lambda key: "SECRET_TOKEN_VALUE" if key == "PEXELS_API_KEY" else None)

    mock_adapter = MagicMock()
    mock_adapter.search.side_effect = RuntimeError("HTTP 401 for key SECRET_TOKEN_VALUE")

    with patch(f"{_BRIDGE_MODULE}.get_supplement_adapter", return_value=mock_adapter):
        result = search_candidates_for_cut_plan_request(project, request_id, {"provider": "pexels"})

    assert result.status == "FAILED"
    assert "SECRET_TOKEN_VALUE" not in result.error_message
    assert "[REDACTED]" in result.error_message


# --- Phase E: Supplement-Manifest (Dedup + Wiederverwendung) ---


def _project_with_request(tmp_path: Path, *, folder_name: str = FOLDER_A) -> tuple[Project, str]:
    """Baut ein Projekt mit genau einem offenen Supplement Request und gibt
    (project, request_id) zurück — Helper für die Phase-E-Manifest-Tests."""
    project = _make_project(tmp_path)
    _write_inventory(project, [])
    audio_path = _write_audio(project)
    folder = ConfirmedFolderPlanItem(
        folder_name=folder_name,
        order_index=1,
        audio_path=str(audio_path),
        audio_duration_sec=5.0,
        sentence_items=[
            SentenceItem(
                sentence_id="sentence_001", text="Ein Satz.", visual_intent="x",
                needs_supplement_asset=True, supplement_reason="No local asset.",
            )
        ],
        alignment_items=[
            AlignmentItem(sentence_id="sentence_001", audio_start_sec=0.0, audio_end_sec=5.0, duration_sec=5.0)
        ],
    )
    plan = ConfirmedVoiceoverProjectPlan(
        project_id=project.id, intro=ConfirmedIntroPlanItem(), folders=[folder]
    )
    save_confirmed_voiceover_project_plan(project, plan)
    save_cut_plan_settings(project, CutPlanSettings(project_id=project.id))
    draft = build_cut_plan_draft(project)
    save_cut_plan_draft(project, draft)
    document = build_supplement_requests_from_cut_plan(project, draft)
    save_cut_plan_supplement_requests(project, document)
    return project, document.requests[0].request_id


def test_stable_supplement_asset_id_uses_provider_asset_id_when_available() -> None:
    asset_id = stable_supplement_asset_id("adobe_stock", "1974039129", "cutreq_x", "cand_1")
    assert asset_id == "supplement_adobe_stock_1974039129"


def test_stable_supplement_asset_id_falls_back_to_request_based_id_when_no_provider_asset_id() -> None:
    asset_id = stable_supplement_asset_id("pexels", "", "cutreq_x", "cand_1")
    assert asset_id == "cut_supplement_cutreq_x_cand_1"


def test_manifest_roundtrip_load_and_save(tmp_path: Path) -> None:
    project, _request_id = _project_with_request(tmp_path)
    entry = CutPlanSupplementManifestEntry(
        asset_id="supplement_pexels_123", provider="pexels", provider_asset_id="123",
        asset_path="/fake/path.jpg", asset_type="image", folder_name=FOLDER_A,
    )
    save_cut_plan_supplement_manifest(project, load_cut_plan_supplement_manifest(project).model_copy(update={"entries": [entry]}))
    reloaded = load_cut_plan_supplement_manifest(project)
    assert len(reloaded.entries) == 1
    assert reloaded.entries[0].provider_asset_id == "123"


def test_manifest_load_returns_empty_document_when_no_file_exists(tmp_path: Path) -> None:
    project, _request_id = _project_with_request(tmp_path)
    manifest = load_cut_plan_supplement_manifest(project)
    assert manifest.entries == []
    assert manifest.project_id == project.id


def test_record_supplement_manifest_entry_appends_new_entry(tmp_path: Path) -> None:
    project, _request_id = _project_with_request(tmp_path)
    entry = CutPlanSupplementManifestEntry(
        asset_id="supplement_pexels_123", provider="pexels", provider_asset_id="123",
        asset_path="/fake/path.jpg", asset_type="image",
    )
    record_supplement_manifest_entry(project, entry)
    manifest = load_cut_plan_supplement_manifest(project)
    assert len(manifest.entries) == 1
    assert manifest.entries[0].provider_asset_id == "123"


def test_record_supplement_manifest_entry_dedups_same_provider_asset_id(tmp_path: Path) -> None:
    project, _request_id = _project_with_request(tmp_path)
    first = CutPlanSupplementManifestEntry(
        asset_id="supplement_pexels_123", provider="pexels", provider_asset_id="123",
        asset_path="/fake/old.jpg", asset_type="image",
    )
    second = CutPlanSupplementManifestEntry(
        asset_id="supplement_pexels_123", provider="pexels", provider_asset_id="123",
        asset_path="/fake/new.jpg", asset_type="image",
    )
    record_supplement_manifest_entry(project, first)
    record_supplement_manifest_entry(project, second)
    manifest = load_cut_plan_supplement_manifest(project)
    assert len(manifest.entries) == 1
    assert manifest.entries[0].asset_path == "/fake/new.jpg"


def test_record_supplement_manifest_entry_keeps_entries_without_provider_asset_id_separate(
    tmp_path: Path
) -> None:
    project, _request_id = _project_with_request(tmp_path)
    first = CutPlanSupplementManifestEntry(
        asset_id="cut_supplement_a_b", provider="pexels", provider_asset_id="",
        asset_path="/fake/a.jpg", asset_type="image",
    )
    second = CutPlanSupplementManifestEntry(
        asset_id="cut_supplement_c_d", provider="pexels", provider_asset_id="",
        asset_path="/fake/b.jpg", asset_type="image",
    )
    record_supplement_manifest_entry(project, first)
    record_supplement_manifest_entry(project, second)
    manifest = load_cut_plan_supplement_manifest(project)
    assert len(manifest.entries) == 2


def test_find_reusable_supplement_manifest_entry_matches_provider_and_id(tmp_path: Path) -> None:
    project, _request_id = _project_with_request(tmp_path)
    entry = CutPlanSupplementManifestEntry(
        asset_id="supplement_adobe_stock_555", provider="adobe_stock", provider_asset_id="555",
        asset_path="/fake/x.mp4", asset_type="video",
    )
    record_supplement_manifest_entry(project, entry)

    found = find_reusable_supplement_manifest_entry(project, "adobe_stock", "555")
    assert found is not None
    assert found.asset_path == "/fake/x.mp4"

    assert find_reusable_supplement_manifest_entry(project, "pexels", "555") is None  # anderer Provider
    assert find_reusable_supplement_manifest_entry(project, "adobe_stock", "999") is None  # andere ID


def test_find_reusable_supplement_manifest_entry_returns_none_for_empty_provider_asset_id(
    tmp_path: Path
) -> None:
    project, _request_id = _project_with_request(tmp_path)
    assert find_reusable_supplement_manifest_entry(project, "adobe_stock", "") is None


# --- Phase I: Validierungs-Metadaten im Manifest ---


def test_record_supplement_manifest_validation_appends_validation(tmp_path: Path) -> None:
    project, request_id = _project_with_request(tmp_path)
    record_supplement_manifest_entry(
        project,
        CutPlanSupplementManifestEntry(
            asset_id="supplement_pexels_777", provider="pexels", provider_asset_id="777",
            asset_path="/fake/x.mp4", asset_type="video", folder_name=FOLDER_A,
        ),
    )

    record_supplement_manifest_validation(
        project, provider="pexels", provider_asset_id="777", request_id=request_id,
        validation_status="PASS", validation_score=0.9, validation_reason="Passt gut.",
        description="Drone shot.", accepted=True,
    )

    manifest = load_cut_plan_supplement_manifest(project)
    entry = next(e for e in manifest.entries if e.provider_asset_id == "777")
    assert len(entry.validations) == 1
    validation = entry.validations[0]
    assert validation.request_id == request_id
    assert validation.validation_status == "PASS"
    assert validation.validation_score == pytest.approx(0.9)
    assert validation.validation_reason == "Passt gut."
    assert validation.description == "Drone shot."
    assert validation.accepted is True


def test_record_supplement_manifest_validation_replaces_existing_entry_for_same_request(tmp_path: Path) -> None:
    """Ein zweiter Validierungsversuch fuer DENSELBEN Request ersetzt den
    vorherigen, statt Duplikate anzusammeln."""
    project, request_id = _project_with_request(tmp_path)
    record_supplement_manifest_entry(
        project,
        CutPlanSupplementManifestEntry(
            asset_id="supplement_pexels_777", provider="pexels", provider_asset_id="777",
            asset_path="/fake/x.mp4", asset_type="video", folder_name=FOLDER_A,
        ),
    )
    record_supplement_manifest_validation(
        project, provider="pexels", provider_asset_id="777", request_id=request_id,
        validation_status="FAIL",
    )
    record_supplement_manifest_validation(
        project, provider="pexels", provider_asset_id="777", request_id=request_id,
        validation_status="PASS",
    )

    manifest = load_cut_plan_supplement_manifest(project)
    entry = next(e for e in manifest.entries if e.provider_asset_id == "777")
    assert len(entry.validations) == 1
    assert entry.validations[0].validation_status == "PASS"


def test_record_supplement_manifest_validation_keeps_separate_entries_per_request(tmp_path: Path) -> None:
    project, request_id = _project_with_request(tmp_path)
    record_supplement_manifest_entry(
        project,
        CutPlanSupplementManifestEntry(
            asset_id="supplement_pexels_777", provider="pexels", provider_asset_id="777",
            asset_path="/fake/x.mp4", asset_type="video", folder_name=FOLDER_A,
        ),
    )
    record_supplement_manifest_validation(
        project, provider="pexels", provider_asset_id="777", request_id="cutreq_a", validation_status="PASS",
    )
    record_supplement_manifest_validation(
        project, provider="pexels", provider_asset_id="777", request_id="cutreq_b", validation_status="FAIL",
    )

    manifest = load_cut_plan_supplement_manifest(project)
    entry = next(e for e in manifest.entries if e.provider_asset_id == "777")
    assert len(entry.validations) == 2
    statuses = {v.request_id: v.validation_status for v in entry.validations}
    assert statuses == {"cutreq_a": "PASS", "cutreq_b": "FAIL"}


def test_record_supplement_manifest_validation_is_noop_without_provider_asset_id(tmp_path: Path) -> None:
    project, request_id = _project_with_request(tmp_path)
    record_supplement_manifest_validation(
        project, provider="pexels", provider_asset_id="", request_id=request_id, validation_status="PASS",
    )
    manifest = load_cut_plan_supplement_manifest(project)
    assert manifest.entries == []


def test_record_supplement_manifest_validation_is_noop_when_no_matching_entry(tmp_path: Path) -> None:
    project, request_id = _project_with_request(tmp_path)
    record_supplement_manifest_validation(
        project, provider="pexels", provider_asset_id="does-not-exist", request_id=request_id,
        validation_status="PASS",
    )
    manifest = load_cut_plan_supplement_manifest(project)
    assert manifest.entries == []


def _fake_acquire_video(candidate, destination_folder):
    destination_folder.mkdir(parents=True, exist_ok=True)
    target = destination_folder / f"{candidate.candidate_id}.mp4"
    target.write_bytes(b"FAKE_VIDEO_BYTES")
    sidecar = SupplementAssetSidecar(
        asset_id="asset_x", supplement_request_id=candidate.supplement_request_id, provider="pexels",
    )
    return SupplementAsset(local_path=target, sidecar=sidecar)


def test_download_records_manifest_entry_when_provider_asset_id_present(tmp_path: Path) -> None:
    project, request_id = _project_with_request(tmp_path)
    fake_candidate = _fake_candidate(
        candidate_id="cand_1", request_id=request_id, provider_asset_id="777", folder_name=FOLDER_A,
    )
    cut_plan_candidate = _to_cut_plan_candidate_for_test(request_id, "pexels", fake_candidate)

    mock_adapter = MagicMock()
    mock_adapter.acquire.side_effect = _fake_acquire_video
    with patch(f"{_BRIDGE_MODULE}.get_supplement_adapter", return_value=mock_adapter):
        download_cut_plan_supplement_candidate(project, request_id, cut_plan_candidate)

    manifest = load_cut_plan_supplement_manifest(project)
    assert len(manifest.entries) == 1
    assert manifest.entries[0].provider_asset_id == "777"
    assert mock_adapter.acquire.call_count == 1


def test_download_reuses_manifest_entry_instead_of_downloading_again(tmp_path: Path) -> None:
    project, request_id_1 = _project_with_request(tmp_path, folder_name=FOLDER_A)
    # Zweiter, unabhängiger Request (z. B. ein anderer Satz) — bekommt
    # dasselbe externe Provider-Asset zugewiesen.
    request_id_2 = "cutreq_other"
    from otio_app.services.voiceover_generation.cut_plan_supplement_bridge import update_cut_plan_supplement_request
    requests_document = load_cut_plan_supplement_requests(project)
    requests_document.requests.append(
        CutPlanSupplementRequest(request_id=request_id_2, cut_item_id="cut_other", folder_name=FOLDER_A)
    )
    save_cut_plan_supplement_requests(project, requests_document)

    fake_candidate = _fake_candidate(
        candidate_id="cand_1", request_id=request_id_1, provider_asset_id="777", folder_name=FOLDER_A,
    )
    cut_plan_candidate_1 = _to_cut_plan_candidate_for_test(request_id_1, "pexels", fake_candidate)
    cut_plan_candidate_2 = _to_cut_plan_candidate_for_test(request_id_2, "pexels", fake_candidate)

    mock_adapter = MagicMock()
    mock_adapter.acquire.side_effect = _fake_acquire_video
    with patch(f"{_BRIDGE_MODULE}.get_supplement_adapter", return_value=mock_adapter):
        first_asset = download_cut_plan_supplement_candidate(project, request_id_1, cut_plan_candidate_1)
        second_asset = download_cut_plan_supplement_candidate(project, request_id_2, cut_plan_candidate_2)

    # Der Adapter darf für dasselbe Provider-Asset nur EINMAL tatsächlich
    # aufgerufen worden sein — der zweite Download wird aus dem Manifest
    # kopiert statt erneut lizenziert/heruntergeladen.
    assert mock_adapter.acquire.call_count == 1
    assert first_asset.asset_id == second_asset.asset_id == "supplement_pexels_777"
    assert Path(second_asset.asset_path).is_file()
    assert Path(second_asset.asset_path) != Path(first_asset.asset_path)


def test_download_without_provider_asset_id_uses_legacy_asset_id_format(tmp_path: Path) -> None:
    project, request_id = _project_with_request(tmp_path)
    fake_candidate = _fake_candidate(candidate_id="cand_1", request_id=request_id, provider_asset_id="")
    cut_plan_candidate = _to_cut_plan_candidate_for_test(request_id, "pexels", fake_candidate)

    mock_adapter = MagicMock()
    mock_adapter.acquire.side_effect = _fake_acquire_video
    with patch(f"{_BRIDGE_MODULE}.get_supplement_adapter", return_value=mock_adapter):
        asset = download_cut_plan_supplement_candidate(project, request_id, cut_plan_candidate)

    assert asset.asset_id == f"cut_supplement_{request_id}_cand_1"
    # Kein provider_asset_id -> kein Manifest-Eintrag (kein sinnvoller Dedup-Schlüssel).
    assert load_cut_plan_supplement_manifest(project).entries == []


def _to_cut_plan_candidate_for_test(request_id: str, provider: str, raw: SupplementCandidate):
    from otio_app.services.voiceover_generation.cut_plan_supplement_models import CutPlanSupplementCandidate

    return CutPlanSupplementCandidate(
        candidate_id=raw.candidate_id,
        request_id=request_id,
        provider=provider,
        title=raw.title,
        asset_type=raw.media_type,
        width=raw.width,
        height=raw.height,
        duration_sec=raw.duration_sec,
        license=raw.license,
        source_url=raw.source_page_url,
        score=raw.match_score,
        provider_candidate_snapshot=raw.model_dump(mode="json"),
    )


# --- 10-14: Kandidat akzeptieren ---


def _apply_accept(
    project: Project, cut_plan: CutPlanDocument, request: CutPlanSupplementRequest, asset: CutPlanSupplementAsset
) -> CutPlanDocument:
    return apply_accepted_supplement_to_cut_plan_item(project, cut_plan, request, asset)


def test_accept_downloads_asset_under_cut_plan_supplement_assets_dir(tmp_path: Path) -> None:
    project = _project_with_supplement_required_draft(tmp_path)
    draft = load_cut_plan_draft(project)
    document = build_supplement_requests_from_cut_plan(project, draft)
    save_cut_plan_supplement_requests(project, document)
    request_id = document.requests[0].request_id

    fake_candidate = _fake_candidate(request_id=request_id, media_type="image", duration_sec=0.0)
    mock_adapter = MagicMock()
    mock_adapter.search.return_value = [fake_candidate]

    with patch(f"{_BRIDGE_MODULE}.get_supplement_adapter", return_value=mock_adapter):
        search_candidates_for_cut_plan_request(project, request_id, {"provider": "pexels"})

    candidate_id = fake_candidate.candidate_id
    downloaded_path = (
        project.language_work_dir_path / "voiceover_generation" / "cut_plan" / "supplement_assets" / request_id / "fake.jpg"
    )

    def _fake_acquire(candidate, destination_folder):
        destination_folder.mkdir(parents=True, exist_ok=True)
        target = destination_folder / "fake.jpg"
        target.write_bytes(b"FAKE_IMAGE_BYTES")
        sidecar = SupplementAssetSidecar(asset_id="asset_x", supplement_request_id=request_id, provider="pexels")
        return SupplementAsset(local_path=target, sidecar=sidecar)

    mock_adapter.acquire.side_effect = _fake_acquire

    with patch(f"{_BRIDGE_MODULE}.get_supplement_adapter", return_value=mock_adapter):
        updated = accept_cut_plan_supplement_candidate(project, request_id, candidate_id)

    assert downloaded_path.is_file()
    item = next(i for i in updated.items if i.cut_item_id == document.requests[0].cut_item_id)
    assert str(downloaded_path) == item.planned_visual_segments[0].asset_path


def test_accept_allows_manifest_reuse_candidate_without_candidates_document(tmp_path: Path) -> None:
    """Phase E/J/K: lokal rekonstruierte reuse_*-Kandidaten existieren nur im
    Auto-Resolver — accept darf sie ohne persistierte Kandidaten-Datei
    übernehmen, wenn candidate + downloaded_asset übergeben werden."""
    project = _project_with_supplement_required_draft(tmp_path)
    draft = load_cut_plan_draft(project)
    document = build_supplement_requests_from_cut_plan(project, draft)
    save_cut_plan_supplement_requests(project, document)
    request_id = document.requests[0].request_id
    request = document.requests[0]

    reuse_candidate = _to_cut_plan_candidate_for_test(
        request_id,
        "pexels",
        SupplementCandidate(
            candidate_id="reuse_pexels_19150364",
            supplement_request_id=request_id,
            provider="pexels",
            provider_asset_id="19150364",
            media_type="video",
            width=1920,
            height=1080,
            duration_sec=12.91,
            download_url="",
            download_enabled=True,
            is_mock=False,
            requires_user_approval=False,
            license="pexels",
            source_page_url="https://pexels.com/video/19150364",
            folder_name=request.folder_name,
            match_score=1.0,
            title="Reuse",
        ),
    )
    downloaded_asset = CutPlanSupplementAsset(
        asset_id="supplement_pexels_19150364",
        request_id=request_id,
        candidate_id=reuse_candidate.candidate_id,
        provider="pexels",
        asset_path=str(
            project.work_dir_path
            / "voiceover_generation"
            / "cut_plan"
            / "supplement_assets"
            / request_id
            / "reuse.mp4"
        ),
        asset_type="video",
        duration_sec=12.91,
        width=1920,
        height=1080,
        license="pexels",
        source_url="https://pexels.com/video/19150364",
        status="ACQUIRED",
    )
    Path(downloaded_asset.asset_path).parent.mkdir(parents=True, exist_ok=True)
    Path(downloaded_asset.asset_path).write_bytes(b"FAKE_VIDEO")

    updated = accept_cut_plan_supplement_candidate(
        project,
        request_id,
        reuse_candidate.candidate_id,
        downloaded_asset=downloaded_asset,
        candidate=reuse_candidate,
    )

    assert load_cut_plan_supplement_candidates_for_request(project, request_id) is None
    item = next(i for i in updated.items if i.cut_item_id == request.cut_item_id)
    assert item.asset_selection_status == CUT_PLAN_ASSET_SELECTION_SUPPLEMENT_USED
    assert item.chosen_asset_id == downloaded_asset.asset_id


def test_accepted_supplement_creates_synthetic_asset_id() -> None:
    request = _supplement_request(request_id="cutreq_cut_001", cut_item_id="cut_001")
    asset = _accepted_asset(asset_id="cut_supplement_cutreq_cut_001_cand_1")
    item = _minimal_item()
    cut_plan = CutPlanDocument(project_id="p1", items=[item])

    project = MagicMock()
    project.id = "p1"

    updated = apply_accepted_supplement_to_cut_plan_item(project, cut_plan, request, asset)
    updated_item = updated.items[0]
    assert updated_item.chosen_asset_id == "cut_supplement_cutreq_cut_001_cand_1"
    assert updated_item.chosen_asset_id.startswith("cut_supplement_")


def test_item_gets_asset_selection_status_supplement_used() -> None:
    request = _supplement_request()
    asset = _accepted_asset()
    item = _minimal_item()
    cut_plan = CutPlanDocument(project_id="p1", items=[item])
    project = MagicMock()
    project.id = "p1"

    updated = apply_accepted_supplement_to_cut_plan_item(project, cut_plan, request, asset)
    assert updated.items[0].asset_selection_status == CUT_PLAN_ASSET_SELECTION_SUPPLEMENT_USED


def test_item_gets_chosen_asset_id() -> None:
    request = _supplement_request()
    asset = _accepted_asset(asset_id="cut_supplement_x_y")
    item = _minimal_item()
    cut_plan = CutPlanDocument(project_id="p1", items=[item])
    project = MagicMock()
    project.id = "p1"

    updated = apply_accepted_supplement_to_cut_plan_item(project, cut_plan, request, asset)
    assert updated.items[0].chosen_asset_id == "cut_supplement_x_y"


def test_visual_segment_created_with_reason_supplement_asset() -> None:
    request = _supplement_request()
    asset = _accepted_asset(asset_type="image")
    item = _minimal_item()
    cut_plan = CutPlanDocument(project_id="p1", items=[item])
    project = MagicMock()
    project.id = "p1"

    updated = apply_accepted_supplement_to_cut_plan_item(project, cut_plan, request, asset)
    segment = updated.items[0].planned_visual_segments[0]
    assert "supplement_asset" in segment.reason.split("+")


# --- 15-17: Video-/Bildregeln ---


def test_image_supplement_can_be_held_indefinitely() -> None:
    request = _supplement_request()
    asset = _accepted_asset(asset_type="image", duration_sec=0.0)
    item = _minimal_item(duration_sec=30.0, timeline_end_sec=31.0, audio_end_sec=30.0)
    cut_plan = CutPlanDocument(project_id="p1", items=[item])
    project = MagicMock()
    project.id = "p1"

    updated = apply_accepted_supplement_to_cut_plan_item(project, cut_plan, request, asset)
    segment = updated.items[0].planned_visual_segments[0]
    assert segment.source_in_sec == 0.0
    assert segment.source_out_sec == pytest.approx(30.0)


def test_video_supplement_checked_against_duration_after_head_trim() -> None:
    request = _supplement_request()
    asset = _accepted_asset(asset_type="video", duration_sec=10.0)  # 10s - 1.0s head trim = 9s usable
    item = _minimal_item(duration_sec=5.0, timeline_end_sec=6.0, audio_end_sec=5.0)
    cut_plan = CutPlanDocument(
        project_id="p1", items=[item], settings_snapshot={"video_head_trim_sec": 1.0}
    )
    project = MagicMock()
    project.id = "p1"

    updated = apply_accepted_supplement_to_cut_plan_item(project, cut_plan, request, asset)
    segment = updated.items[0].planned_visual_segments[0]
    assert segment.source_in_sec == pytest.approx(1.0)
    assert segment.source_out_sec == pytest.approx(6.0)  # 1.0 + 5.0


def test_too_short_video_supplement_is_not_silently_accepted() -> None:
    request = _supplement_request()
    asset = _accepted_asset(asset_type="video", duration_sec=3.0)  # 3s - 1.0s head trim = 2s usable
    item = _minimal_item(duration_sec=5.0, timeline_end_sec=6.0, audio_end_sec=5.0)
    cut_plan = CutPlanDocument(
        project_id="p1", items=[item], settings_snapshot={"video_head_trim_sec": 1.0}
    )
    project = MagicMock()
    project.id = "p1"

    with pytest.raises(ValueError, match="zu kurz"):
        apply_accepted_supplement_to_cut_plan_item(project, cut_plan, request, asset)
    # Item darf nicht mutiert worden sein (kein stilles Teil-Update).
    assert cut_plan.items[0].asset_selection_status == "SUPPLEMENT_REQUIRED"


# --- 18-21: Blocker-Bereinigung, Usage, Coverage ---


def test_supplement_required_blocker_removed_for_resolved_item() -> None:
    request = _supplement_request()
    asset = _accepted_asset(asset_type="image")
    item = _minimal_item(blockers=[CUT_PLAN_ERROR_SUPPLEMENT_REQUIRED])
    cut_plan = CutPlanDocument(project_id="p1", items=[item])
    project = MagicMock()
    project.id = "p1"

    updated = apply_accepted_supplement_to_cut_plan_item(project, cut_plan, request, asset)
    assert CUT_PLAN_ERROR_SUPPLEMENT_REQUIRED not in updated.items[0].blockers


def test_other_blockers_remain_after_accept() -> None:
    request = _supplement_request()
    asset = _accepted_asset(asset_type="image")
    item = _minimal_item(
        blockers=[CUT_PLAN_ERROR_SUPPLEMENT_REQUIRED, CUT_PLAN_ERROR_MISSING_ALIGNMENT]
    )
    cut_plan = CutPlanDocument(project_id="p1", items=[item])
    project = MagicMock()
    project.id = "p1"

    updated = apply_accepted_supplement_to_cut_plan_item(project, cut_plan, request, asset)
    assert CUT_PLAN_ERROR_MISSING_ALIGNMENT in updated.items[0].blockers
    assert CUT_PLAN_ERROR_SUPPLEMENT_REQUIRED not in updated.items[0].blockers
    assert updated.status == "NEEDS_REVIEW"


def test_asset_usage_summary_updated_after_supplement() -> None:
    request = _supplement_request()
    asset = _accepted_asset(asset_id="cut_supplement_new_asset", asset_type="image")
    item = _minimal_item()
    cut_plan = CutPlanDocument(project_id="p1", items=[item], asset_usage_summary={})
    project = MagicMock()
    project.id = "p1"

    updated = apply_accepted_supplement_to_cut_plan_item(project, cut_plan, request, asset)
    assert updated.asset_usage_summary.get("cut_supplement_new_asset") == 1


def test_visual_coverage_extensions_run_after_supplement() -> None:
    """Das akzeptierte Supplement-Segment liegt am Timeline-Anfang (Item
    startet nicht bei 0.0, weil initial_audio_offset_sec > 0) — die Coverage-
    Erweiterung aus Phase 8.5 muss danach automatisch erneut laufen."""
    request = _supplement_request()
    asset = _accepted_asset(asset_type="image", duration_sec=0.0)
    item = _minimal_item(timeline_start_sec=1.0, timeline_end_sec=6.0, duration_sec=5.0)
    audio_item = _minimal_audio_item(timeline_start_sec=1.0, timeline_end_sec=6.0)
    cut_plan = CutPlanDocument(
        project_id="p1", items=[item], audio_items=[audio_item],
        settings_snapshot={"initial_audio_offset_sec": 1.0},
    )
    project = MagicMock()
    project.id = "p1"

    updated = apply_accepted_supplement_to_cut_plan_item(project, cut_plan, request, asset)
    segment = updated.items[0].planned_visual_segments[0]
    assert segment.timeline_in_sec == 0.0
    assert "initial_preroll_extension" in segment.reason.split("+")


# --- Vorab-Hardening (Phase 8.7): mehrfach akzeptierte Supplement Candidates ---


def _search_and_accept_image(
    project: Project, request_id: str, *, candidate_id: str = "cand_fake01", force_replace: bool = False
):
    fake_candidate = _fake_candidate(candidate_id=candidate_id, request_id=request_id, media_type="image", duration_sec=0.0)
    mock_adapter = MagicMock()
    mock_adapter.search.return_value = [fake_candidate]

    def _fake_acquire(candidate, destination_folder):
        destination_folder.mkdir(parents=True, exist_ok=True)
        target = destination_folder / f"{candidate.candidate_id}.jpg"
        target.write_bytes(b"FAKE_IMAGE_BYTES")
        sidecar = SupplementAssetSidecar(asset_id="asset_x", supplement_request_id=request_id, provider="pexels")
        return SupplementAsset(local_path=target, sidecar=sidecar)

    mock_adapter.acquire.side_effect = _fake_acquire

    with patch(f"{_BRIDGE_MODULE}.get_supplement_adapter", return_value=mock_adapter):
        search_candidates_for_cut_plan_request(project, request_id, {"provider": "pexels"})
        return accept_cut_plan_supplement_candidate(project, request_id, candidate_id, force_replace=force_replace)


def test_second_accept_without_force_replace_is_blocked(tmp_path: Path) -> None:
    project = _project_with_supplement_required_draft(tmp_path)
    draft = load_cut_plan_draft(project)
    document = build_supplement_requests_from_cut_plan(project, draft)
    save_cut_plan_supplement_requests(project, document)
    request_id = document.requests[0].request_id

    _search_and_accept_image(project, request_id, candidate_id="cand_first")

    with pytest.raises(ValueError, match="already has an accepted asset"):
        _search_and_accept_image(project, request_id, candidate_id="cand_second")


def test_accept_with_force_replace_replaces_deliberately(tmp_path: Path) -> None:
    project = _project_with_supplement_required_draft(tmp_path)
    draft = load_cut_plan_draft(project)
    document = build_supplement_requests_from_cut_plan(project, draft)
    save_cut_plan_supplement_requests(project, document)
    request_id = document.requests[0].request_id
    cut_item_id = document.requests[0].cut_item_id

    _search_and_accept_image(project, request_id, candidate_id="cand_first")
    first_request = load_cut_plan_supplement_requests(project).requests[0]
    assert first_request.accepted_candidate_id == "cand_first"

    updated = _search_and_accept_image(project, request_id, candidate_id="cand_second", force_replace=True)
    item = next(i for i in updated.items if i.cut_item_id == cut_item_id)
    assert "cand_second" in item.chosen_asset_id

    second_request = load_cut_plan_supplement_requests(project).requests[0]
    assert second_request.accepted_candidate_id == "cand_second"


def test_old_item_state_not_silently_overwritten_on_blocked_second_accept(tmp_path: Path) -> None:
    project = _project_with_supplement_required_draft(tmp_path)
    draft = load_cut_plan_draft(project)
    document = build_supplement_requests_from_cut_plan(project, draft)
    save_cut_plan_supplement_requests(project, document)
    request_id = document.requests[0].request_id
    cut_item_id = document.requests[0].cut_item_id

    _search_and_accept_image(project, request_id, candidate_id="cand_first")
    draft_after_first_accept = load_cut_plan_draft(project)
    item_after_first = next(i for i in draft_after_first_accept.items if i.cut_item_id == cut_item_id)
    assert "cand_first" in item_after_first.chosen_asset_id

    with pytest.raises(ValueError):
        _search_and_accept_image(project, request_id, candidate_id="cand_second")

    draft_after_blocked_second = load_cut_plan_draft(project)
    item_after_blocked = next(i for i in draft_after_blocked_second.items if i.cut_item_id == cut_item_id)
    # Der ursprüngliche, erste akzeptierte Zustand darf unverändert bleiben.
    assert item_after_blocked.chosen_asset_id == item_after_first.chosen_asset_id


# --- 22-24: UI ---


def test_ui_shows_supplement_requests_section(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    project = _project_with_supplement_required_draft(tmp_path)
    _patch_project_selector(project, monkeypatch)
    monkeypatch.setattr("streamlit.button", lambda *a, **k: False)
    monkeypatch.setattr("streamlit.rerun", lambda: None)

    render_cut_plan_page()  # darf nicht werfen; Supplement-Requests-Bereich wird gerendert


def test_ui_shows_adobe_and_pexels_readiness_status(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Phase 12.4a: Adobe-/Pexels-Bereitschaft wird sichtbar angezeigt statt
    nur eines pauschalen PEXELS_API_KEY-Checks."""
    project = _project_with_supplement_required_draft(tmp_path)
    draft = load_cut_plan_draft(project)
    document = build_supplement_requests_from_cut_plan(project, draft)
    save_cut_plan_supplement_requests(project, document)
    monkeypatch.setenv("ADOBE_STOCK_API_KEY", "test-key")
    monkeypatch.delenv("ADOBE_STOCK_ACCESS_TOKEN", raising=False)
    monkeypatch.delenv("PEXELS_API_KEY", raising=False)
    _patch_project_selector(project, monkeypatch)
    monkeypatch.setattr("streamlit.button", lambda *a, **k: False)
    monkeypatch.setattr("streamlit.rerun", lambda: None)
    captions: list[str] = []
    monkeypatch.setattr("streamlit.caption", lambda msg, **k: captions.append(msg))

    render_cut_plan_page()

    assert any("Adobe Stock" in c and "ADOBE_STOCK_ACCESS_TOKEN" in c for c in captions)
    assert any("Pexels" in c for c in captions)


def test_ui_shows_warning_when_no_supplement_provider_configured(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = _project_with_supplement_required_draft(tmp_path)
    draft = load_cut_plan_draft(project)
    document = build_supplement_requests_from_cut_plan(project, draft)
    save_cut_plan_supplement_requests(project, document)
    monkeypatch.delenv("ADOBE_STOCK_API_KEY", raising=False)
    monkeypatch.delenv("PEXELS_API_KEY", raising=False)
    _patch_project_selector(project, monkeypatch)
    monkeypatch.setattr("streamlit.button", lambda *a, **k: False)
    monkeypatch.setattr("streamlit.rerun", lambda: None)
    warnings: list[str] = []
    monkeypatch.setattr("streamlit.warning", lambda msg, **k: warnings.append(msg))

    render_cut_plan_page()

    assert any("Weder Adobe Stock noch Pexels" in w for w in warnings)


def test_ui_provider_selector_defaults_to_adobe_when_ready(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Nutzervorgabe: Adobe Stock ist die bevorzugte Voreinstellung für die
    manuelle Kandidatensuche, solange es konfiguriert ist."""
    project = _project_with_supplement_required_draft(tmp_path)
    draft = load_cut_plan_draft(project)
    document = build_supplement_requests_from_cut_plan(project, draft)
    save_cut_plan_supplement_requests(project, document)
    monkeypatch.setenv("ADOBE_STOCK_API_KEY", "test-key")
    monkeypatch.setenv("PEXELS_API_KEY", "test-key")
    _patch_project_selector(project, monkeypatch)
    monkeypatch.setattr("streamlit.button", lambda *a, **k: False)
    monkeypatch.setattr("streamlit.rerun", lambda: None)
    selectbox_calls: list[dict] = []

    def _fake_selectbox(label, *args, **kwargs):
        selectbox_calls.append(kwargs)
        options = kwargs.get("options", [])
        index = kwargs.get("index", 0)
        return options[index] if options else None

    monkeypatch.setattr("streamlit.selectbox", _fake_selectbox)

    render_cut_plan_page()

    matching = [c for c in selectbox_calls if c.get("options") == ["adobe_stock", "pexels"]]
    assert matching, f"Provider-Selectbox nicht gefunden unter: {selectbox_calls}"
    assert matching[0]["index"] == 0  # adobe_stock zuerst, da bereit


def test_ui_shows_candidate_list_when_present(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    project = _project_with_supplement_required_draft(tmp_path)
    draft = load_cut_plan_draft(project)
    document = build_supplement_requests_from_cut_plan(project, draft)
    save_cut_plan_supplement_requests(project, document)
    request_id = document.requests[0].request_id

    mock_adapter = MagicMock()
    mock_adapter.search.return_value = [_fake_candidate(request_id=request_id)]
    with patch(f"{_BRIDGE_MODULE}.get_supplement_adapter", return_value=mock_adapter):
        search_candidates_for_cut_plan_request(project, request_id, {"provider": "pexels"})

    _patch_project_selector(project, monkeypatch)
    monkeypatch.setattr("streamlit.button", lambda *a, **k: False)
    monkeypatch.setattr("streamlit.rerun", lambda: None)

    render_cut_plan_page()  # darf nicht werfen; Kandidatenliste wird gerendert


def test_ui_shows_query_model_selector_and_fallback_hint(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Phase 11.1: Modellwahl fuer die Suchquery-Generierung ist vorhanden;
    solange nie gesucht wurde, wird ein Hinweis auf die deterministische
    Ersatz-Query gezeigt statt einer irrefuehrenden 'Query-Vorschau', die de
    facto nie zum Einsatz kommt."""
    project = _project_with_supplement_required_draft(tmp_path)
    draft = load_cut_plan_draft(project)
    document = build_supplement_requests_from_cut_plan(project, draft)
    save_cut_plan_supplement_requests(project, document)

    _patch_project_selector(project, monkeypatch)
    monkeypatch.setattr("streamlit.button", lambda *a, **k: False)
    monkeypatch.setattr("streamlit.rerun", lambda: None)
    captions: list[str] = []
    monkeypatch.setattr("streamlit.caption", lambda msg, **k: captions.append(msg))

    render_cut_plan_page()  # darf nicht werfen

    assert any("Modell (LLM-Suchqueries)" in c or "Noch keine LLM-Suchqueries" in c for c in captions)


def test_ui_shows_supplement_search_hint_caption_when_present(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Phase 9: der bereits vorbereitete Suchvorschlag muss für den Nutzer
    sichtbar sein, bevor überhaupt gesucht wird."""
    project = _project_with_supplement_required_draft_and_hint(
        tmp_path, supplement_search_hint="Grand Canyon rim wide shot"
    )
    draft = load_cut_plan_draft(project)
    document = build_supplement_requests_from_cut_plan(project, draft)
    save_cut_plan_supplement_requests(project, document)

    _patch_project_selector(project, monkeypatch)
    monkeypatch.setattr("streamlit.button", lambda *a, **k: False)
    monkeypatch.setattr("streamlit.rerun", lambda: None)
    captions: list[str] = []
    monkeypatch.setattr("streamlit.caption", lambda msg, **k: captions.append(msg))

    render_cut_plan_page()  # darf nicht werfen

    assert any("Grand Canyon rim wide shot" in c for c in captions)
    assert any("Bereits beim Skriptschreiben vorbereiteter Suchvorschlag" in c for c in captions)


def test_ui_omits_supplement_search_hint_caption_when_absent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = _project_with_supplement_required_draft(tmp_path)
    draft = load_cut_plan_draft(project)
    document = build_supplement_requests_from_cut_plan(project, draft)
    save_cut_plan_supplement_requests(project, document)

    _patch_project_selector(project, monkeypatch)
    monkeypatch.setattr("streamlit.button", lambda *a, **k: False)
    monkeypatch.setattr("streamlit.rerun", lambda: None)
    captions: list[str] = []
    monkeypatch.setattr("streamlit.caption", lambda msg, **k: captions.append(msg))

    render_cut_plan_page()  # darf nicht werfen

    assert not any("Bereits beim Skriptschreiben vorbereiteter Suchvorschlag" in c for c in captions)


def test_search_click_passes_saved_query_llm_settings_to_bridge(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Die UI muss provider/model aus den gespeicherten Modell-Einstellungen
    (Rolle cut_plan_supplement_query) an search_candidates_for_cut_plan_
    request weiterreichen, damit Phase 11.1 tatsaechlich greift."""
    from otio_app.services.voiceover_generation.model_settings_service import (
        load_model_settings,
        save_model_settings,
    )

    project = _project_with_supplement_required_draft(tmp_path)
    draft = load_cut_plan_draft(project)
    document = build_supplement_requests_from_cut_plan(project, draft)
    save_cut_plan_supplement_requests(project, document)
    request_id = document.requests[0].request_id

    settings = load_model_settings(project)
    updated = settings.model_copy(
        update={
            "cut_plan_supplement_query": settings.cut_plan_supplement_query.model_copy(
                update={"provider": "gemini", "model": "gemini-3.1-pro-preview"}
            )
        }
    )
    save_model_settings(project, updated)

    search_key = f"cut_plan_supplement_search_{project.id}_{request_id}"

    def _fake_button(label, *args, **kwargs):
        return kwargs.get("key") == search_key

    with patch(
        "otio_app.ui.voiceover_generation.cut_plan_tab.search_candidates_for_cut_plan_request"
    ) as mock_search:
        mock_search.return_value = MagicMock(status="READY", candidates=[], error_message="")
        _patch_project_selector(project, monkeypatch)
        monkeypatch.setattr("streamlit.button", _fake_button)
        monkeypatch.setattr("streamlit.rerun", lambda: None)

        render_cut_plan_page()

    mock_search.assert_called_once()
    _, kwargs = mock_search.call_args
    assert kwargs["query_llm_provider"] == "gemini"
    assert kwargs["query_llm_model"] == "gemini-3.1-pro-preview"


def test_ui_shows_auto_resolve_button_and_result_trace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Phase 11.3: der neue Einzel-Request-Auto-Resolver ist per Button
    erreichbar und persistiert eine Ergebnis-Trace, die auch bei einem
    spaeteren Rendering (ohne erneuten Klick) angezeigt wird."""
    project = _project_with_supplement_required_draft(tmp_path)
    draft = load_cut_plan_draft(project)
    document = build_supplement_requests_from_cut_plan(project, draft)
    save_cut_plan_supplement_requests(project, document)
    request_id = document.requests[0].request_id

    auto_resolve_key = f"cut_plan_supplement_auto_resolve_{project.id}_{request_id}"

    def _fake_button(label, *args, **kwargs):
        return kwargs.get("key") == auto_resolve_key

    from otio_app.services.voiceover_generation.cut_plan_supplement_auto_resolve_service import (
        AUTO_RESOLVE_STATUS_NO_MATCH,
        CutPlanSupplementAutoResolveResult,
    )
    from otio_app.services.voiceover_generation.cut_plan_supplement_models import (
        CutPlanSupplementAutoResolveAttempt,
    )

    fake_result = CutPlanSupplementAutoResolveResult(
        status=AUTO_RESOLVE_STATUS_NO_MATCH,
        request_id=request_id,
        attempts=[
            CutPlanSupplementAutoResolveAttempt(
                candidate_id="cand_1",
                provider="pexels",
                asset_type="video",
                validation_status="FAIL",
                validation_score=0.1,
                validation_reason="Passt nicht.",
            )
        ],
    )

    warnings: list[str] = []
    with patch(
        "otio_app.ui.voiceover_generation.cut_plan_tab.auto_resolve_cut_plan_supplement_request",
        return_value=fake_result,
    ) as mock_auto_resolve:
        _patch_project_selector(project, monkeypatch)
        monkeypatch.setattr("streamlit.button", _fake_button)
        monkeypatch.setattr("streamlit.rerun", lambda: None)
        monkeypatch.setattr("streamlit.warning", lambda msg: warnings.append(msg))

        render_cut_plan_page()

    mock_auto_resolve.assert_called_once()
    assert any("Kein Stock-Kandidat hat die Prüfung bestanden" in msg for msg in warnings)

    # Zweiter Rendering-Durchlauf ohne Klick: eine bereits PERSISTIERTE Trace
    # (wie sie die echte auto_resolve_cut_plan_supplement_request-Funktion
    # schreiben würde — hier direkt gesetzt, da der Aufruf oben gemockt war)
    # muss weiterhin angezeigt werden.
    from otio_app.services.voiceover_generation.cut_plan_supplement_bridge import (
        update_cut_plan_supplement_request,
    )

    update_cut_plan_supplement_request(
        project,
        request_id,
        auto_resolve_status=fake_result.status,
        auto_resolve_attempts=fake_result.attempts,
    )
    reloaded = load_cut_plan_supplement_requests(project)
    persisted = next(r for r in reloaded.requests if r.request_id == request_id)
    assert persisted.auto_resolve_status == AUTO_RESOLVE_STATUS_NO_MATCH
    assert persisted.auto_resolve_attempts[0].candidate_id == "cand_1"

    _patch_project_selector(project, monkeypatch)
    monkeypatch.setattr("streamlit.button", lambda *a, **k: False)
    monkeypatch.setattr("streamlit.rerun", lambda: None)
    render_cut_plan_page()  # darf nicht werfen; Trace wird erneut angezeigt


def test_ui_shows_generic_fallback_success_message_for_auto_resolve(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Phase 11.4: wenn der Auto-Resolver GENERIC_FALLBACK_USED liefert,
    zeigt die UI eine klar unterscheidbare Erfolgsmeldung statt der
    ACCEPTED-Meldung."""
    project = _project_with_supplement_required_draft(tmp_path)
    draft = load_cut_plan_draft(project)
    document = build_supplement_requests_from_cut_plan(project, draft)
    save_cut_plan_supplement_requests(project, document)
    request_id = document.requests[0].request_id

    auto_resolve_key = f"cut_plan_supplement_auto_resolve_{project.id}_{request_id}"

    def _fake_button(label, *args, **kwargs):
        return kwargs.get("key") == auto_resolve_key

    from otio_app.services.voiceover_generation.cut_plan_supplement_auto_resolve_service import (
        AUTO_RESOLVE_STATUS_GENERIC_FALLBACK_USED,
        CutPlanSupplementAutoResolveResult,
    )

    fake_result = CutPlanSupplementAutoResolveResult(
        status=AUTO_RESOLVE_STATUS_GENERIC_FALLBACK_USED,
        request_id=request_id,
        accepted_asset_id="asset_generic_establishing",
        attempts=[],
    )

    successes: list[str] = []
    with patch(
        "otio_app.ui.voiceover_generation.cut_plan_tab.auto_resolve_cut_plan_supplement_request",
        return_value=fake_result,
    ):
        _patch_project_selector(project, monkeypatch)
        monkeypatch.setattr("streamlit.button", _fake_button)
        monkeypatch.setattr("streamlit.rerun", lambda: None)
        monkeypatch.setattr("streamlit.success", lambda msg: successes.append(msg))

        render_cut_plan_page()

    assert any("asset_generic_establishing" in msg for msg in successes)
    assert any("generisches Ordner-Asset" in msg for msg in successes)


def test_ui_generic_fallback_button_calls_service_and_shows_result(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Der eigenständige Button 'Generisches Ordner-Asset verwenden' löst
    KEINE Stock-Suche aus, sondern ruft direkt apply_generic_fallback_for_
    cut_plan_request auf."""
    project = _project_with_supplement_required_draft(tmp_path)
    draft = load_cut_plan_draft(project)
    document = build_supplement_requests_from_cut_plan(project, draft)
    save_cut_plan_supplement_requests(project, document)
    request_id = document.requests[0].request_id

    generic_key = f"cut_plan_supplement_generic_fallback_{project.id}_{request_id}"

    def _fake_button(label, *args, **kwargs):
        return kwargs.get("key") == generic_key

    from otio_app.services.generic_outro_selector import GenericAssetCandidate

    fake_candidate = GenericAssetCandidate(
        path="/fake/establishing.mp4",
        asset_id="asset_establishing",
        description="Establishing shot",
        score=0.8,
        selection_reason="Neutraler Shot.",
        warnings=[],
    )

    successes: list[str] = []
    with patch(
        "otio_app.ui.voiceover_generation.cut_plan_tab.apply_generic_fallback_for_cut_plan_request",
        return_value=(None, fake_candidate),
    ) as mock_fallback:
        _patch_project_selector(project, monkeypatch)
        monkeypatch.setattr("streamlit.button", _fake_button)
        monkeypatch.setattr("streamlit.rerun", lambda: None)
        monkeypatch.setattr("streamlit.success", lambda msg: successes.append(msg))

        render_cut_plan_page()

    mock_fallback.assert_called_once_with(project, request_id)
    assert any("asset_establishing" in msg for msg in successes)


def test_ui_shows_bulk_auto_resolve_button_and_summary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Phase 11.5: Batch-Button 'Alle fehlenden Supplement-Assets
    automatisch suchen' ruft auto_resolve_all_cut_plan_supplement_requests
    auf und zeigt eine zusammenfassende Erfolgsmeldung."""
    project = _project_with_supplement_required_draft(tmp_path)
    draft = load_cut_plan_draft(project)
    document = build_supplement_requests_from_cut_plan(project, draft)
    save_cut_plan_supplement_requests(project, document)

    bulk_key = f"cut_plan_supplement_auto_resolve_all_{project.id}"

    def _fake_button(label, *args, **kwargs):
        return kwargs.get("key") == bulk_key

    from otio_app.services.voiceover_generation.cut_plan_supplement_auto_resolve_service import (
        AUTO_RESOLVE_STATUS_ACCEPTED,
        AUTO_RESOLVE_STATUS_GENERIC_FALLBACK_USED,
        AUTO_RESOLVE_STATUS_NO_MATCH,
        CutPlanSupplementAutoResolveResult,
    )

    fake_results = [
        CutPlanSupplementAutoResolveResult(status=AUTO_RESOLVE_STATUS_ACCEPTED, request_id="cutreq_1"),
        CutPlanSupplementAutoResolveResult(status=AUTO_RESOLVE_STATUS_GENERIC_FALLBACK_USED, request_id="cutreq_2"),
        CutPlanSupplementAutoResolveResult(status=AUTO_RESOLVE_STATUS_NO_MATCH, request_id="cutreq_3"),
    ]

    successes: list[str] = []
    with patch(
        "otio_app.ui.voiceover_generation.cut_plan_tab.auto_resolve_all_cut_plan_supplement_requests",
        return_value=fake_results,
    ) as mock_batch:
        _patch_project_selector(project, monkeypatch)
        monkeypatch.setattr("streamlit.button", _fake_button)
        monkeypatch.setattr("streamlit.rerun", lambda: None)
        monkeypatch.setattr("streamlit.success", lambda msg: successes.append(msg))

        render_cut_plan_page()

    mock_batch.assert_called_once()
    assert any("3 Request(s) bearbeitet" in msg for msg in successes)
    assert any("1 automatisch akzeptiert" in msg for msg in successes)
    assert any("1 generisches Ordner-Asset verwendet" in msg for msg in successes)
    assert any("1 ohne Treffer" in msg for msg in successes)


def test_ui_bulk_auto_resolve_button_disabled_when_no_open_requests(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = _project_with_supplement_required_draft(tmp_path)
    draft = load_cut_plan_draft(project)
    document = build_supplement_requests_from_cut_plan(project, draft)
    document = document.model_copy(
        update={"requests": [r.model_copy(update={"accepted_asset_id": "asset_x"}) for r in document.requests]}
    )
    save_cut_plan_supplement_requests(project, document)

    captured_disabled: list[bool] = []

    def _capture_button(label, *args, **kwargs):
        if kwargs.get("key", "").startswith("cut_plan_supplement_auto_resolve_all_"):
            captured_disabled.append(kwargs.get("disabled", False))
        return False

    _patch_project_selector(project, monkeypatch)
    monkeypatch.setattr("streamlit.button", _capture_button)
    monkeypatch.setattr("streamlit.rerun", lambda: None)

    render_cut_plan_page()

    assert captured_disabled == [True]


def test_ui_shows_unaccept_button_and_calls_service(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Phase 11.6: 'Übernahme zurücknehmen' erscheint nur, wenn ein Asset
    bereits übernommen wurde, und ruft unaccept_cut_plan_supplement_request auf."""
    project = _project_with_supplement_required_draft(tmp_path)
    draft = load_cut_plan_draft(project)
    document = build_supplement_requests_from_cut_plan(project, draft)
    document = document.model_copy(
        update={
            "requests": [
                r.model_copy(update={"accepted_asset_id": "asset_x", "accepted_asset_path": "/fake/x.mp4"})
                for r in document.requests
            ]
        }
    )
    save_cut_plan_supplement_requests(project, document)
    request_id = document.requests[0].request_id

    unaccept_key = f"cut_plan_supplement_unaccept_{project.id}_{request_id}"

    def _fake_button(label, *args, **kwargs):
        return kwargs.get("key") == unaccept_key

    successes: list[str] = []
    with patch(
        "otio_app.ui.voiceover_generation.cut_plan_tab.unaccept_cut_plan_supplement_request"
    ) as mock_unaccept:
        _patch_project_selector(project, monkeypatch)
        monkeypatch.setattr("streamlit.button", _fake_button)
        monkeypatch.setattr("streamlit.rerun", lambda: None)
        monkeypatch.setattr("streamlit.success", lambda msg: successes.append(msg))

        render_cut_plan_page()

    mock_unaccept.assert_called_once_with(project, request_id)
    assert any("zurückgenommen" in msg for msg in successes)


def test_ui_manual_asset_assignment_button_calls_service(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Phase 11.6: manuelle Asset-Zuweisung ruft apply_manual_asset_for_
    cut_plan_request mit dem in der Selectbox gewählten Asset auf."""
    project = _project_with_supplement_required_draft(tmp_path)
    _write_inventory(project, ["establishing.mp4"])
    draft = load_cut_plan_draft(project)
    document = build_supplement_requests_from_cut_plan(project, draft)
    save_cut_plan_supplement_requests(project, document)
    request_id = document.requests[0].request_id

    assign_key = f"cut_plan_supplement_manual_assign_{project.id}_{request_id}"
    select_key = f"cut_plan_supplement_manual_select_{project.id}_{request_id}"

    def _fake_button(label, *args, **kwargs):
        return kwargs.get("key") == assign_key

    def _fake_selectbox(label, *args, **kwargs):
        options = kwargs.get("options", [])
        return options[0] if options else None

    successes: list[str] = []
    with (
        patch(
            "otio_app.ui.voiceover_generation.cut_plan_tab.list_manual_asset_options_for_request"
        ) as mock_list_options,
        patch(
            "otio_app.ui.voiceover_generation.cut_plan_tab.apply_manual_asset_for_cut_plan_request"
        ) as mock_apply,
    ):
        from otio_app.services.voiceover_generation.cut_plan_generic_fallback_service import ManualAssetOption

        mock_list_options.return_value = [
            ManualAssetOption(
                asset_id="asset_establishing",
                path="/fake/establishing.mp4",
                description="Establishing shot",
                media_type="video",
                duration_sec=20.0,
                likely_usable=True,
            )
        ]
        _patch_project_selector(project, monkeypatch)
        monkeypatch.setattr("streamlit.button", _fake_button)
        monkeypatch.setattr("streamlit.selectbox", _fake_selectbox)
        monkeypatch.setattr("streamlit.rerun", lambda: None)
        monkeypatch.setattr("streamlit.success", lambda msg: successes.append(msg))

        render_cut_plan_page()

    mock_apply.assert_called_once_with(
        project, request_id, asset_id="asset_establishing", asset_path="/fake/establishing.mp4"
    )
    assert any("manuell zugewiesen" in msg for msg in successes)


def test_ui_shows_revalidate_hint_after_accept(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    project = _project_with_supplement_required_draft(tmp_path)
    draft = load_cut_plan_draft(project)
    document = build_supplement_requests_from_cut_plan(project, draft)
    save_cut_plan_supplement_requests(project, document)
    request_id = document.requests[0].request_id
    cut_item_id = document.requests[0].cut_item_id

    fake_candidate = _fake_candidate(request_id=request_id, media_type="image", duration_sec=0.0)
    mock_adapter = MagicMock()
    mock_adapter.search.return_value = [fake_candidate]
    with patch(f"{_BRIDGE_MODULE}.get_supplement_adapter", return_value=mock_adapter):
        search_candidates_for_cut_plan_request(project, request_id, {"provider": "pexels"})
    candidate_id = fake_candidate.candidate_id

    def _fake_acquire(candidate, destination_folder):
        destination_folder.mkdir(parents=True, exist_ok=True)
        target = destination_folder / "fake.jpg"
        target.write_bytes(b"FAKE_IMAGE_BYTES")
        sidecar = SupplementAssetSidecar(asset_id="asset_x", supplement_request_id=request_id, provider="pexels")
        return SupplementAsset(local_path=target, sidecar=sidecar)

    mock_adapter.acquire.side_effect = _fake_acquire

    success_messages: list[str] = []
    accept_key = f"cut_plan_supplement_accept_{project.id}_{request_id}_{candidate_id}"

    def _fake_button(label, *args, **kwargs):
        return kwargs.get("key") == accept_key

    with patch(f"{_BRIDGE_MODULE}.get_supplement_adapter", return_value=mock_adapter):
        _patch_project_selector(project, monkeypatch)
        monkeypatch.setattr("streamlit.button", _fake_button)
        monkeypatch.setattr("streamlit.rerun", lambda: None)
        monkeypatch.setattr("streamlit.success", lambda msg: success_messages.append(msg))

        render_cut_plan_page()

    assert any("erneut validieren" in msg for msg in success_messages)
    updated_draft = load_cut_plan_draft(project)
    updated_item = next(i for i in updated_draft.items if i.cut_item_id == cut_item_id)
    assert updated_item.asset_selection_status == CUT_PLAN_ASSET_SELECTION_SUPPLEMENT_USED


# --- 25-29: Schutz bestehender Pipeline ---


def test_no_edit_plan_document_created(tmp_path: Path) -> None:
    project = _project_with_supplement_required_draft(tmp_path)
    draft = load_cut_plan_draft(project)
    document = build_supplement_requests_from_cut_plan(project, draft)
    save_cut_plan_supplement_requests(project, document)
    assert not get_edit_plan_dir(project.language_work_dir_path).exists()


def test_no_otio_export_triggered(tmp_path: Path) -> None:
    project = _project_with_supplement_required_draft(tmp_path)
    draft = load_cut_plan_draft(project)
    document = build_supplement_requests_from_cut_plan(project, draft)
    save_cut_plan_supplement_requests(project, document)
    assert not get_exports_dir(project.language_work_dir_path).exists()


def test_no_regular_inventory_files_modified(tmp_path: Path) -> None:
    project = _project_with_supplement_required_draft(tmp_path)
    inv_path = get_folder_inventory_path(project.work_dir_path, FOLDER_A)
    original = inv_path.read_text(encoding="utf-8")

    draft = load_cut_plan_draft(project)
    document = build_supplement_requests_from_cut_plan(project, draft)
    save_cut_plan_supplement_requests(project, document)
    request_id = document.requests[0].request_id

    fake_candidate = _fake_candidate(request_id=request_id, media_type="image", duration_sec=0.0)
    mock_adapter = MagicMock()
    mock_adapter.search.return_value = [fake_candidate]

    def _fake_acquire(candidate, destination_folder):
        destination_folder.mkdir(parents=True, exist_ok=True)
        target = destination_folder / "fake.jpg"
        target.write_bytes(b"FAKE_IMAGE_BYTES")
        sidecar = SupplementAssetSidecar(asset_id="asset_x", supplement_request_id=request_id, provider="pexels")
        return SupplementAsset(local_path=target, sidecar=sidecar)

    mock_adapter.acquire.side_effect = _fake_acquire

    with patch(f"{_BRIDGE_MODULE}.get_supplement_adapter", return_value=mock_adapter):
        search_candidates_for_cut_plan_request(project, request_id, {"provider": "pexels"})
        accept_cut_plan_supplement_candidate(project, request_id, fake_candidate.candidate_id)

    assert inv_path.read_text(encoding="utf-8") == original


def test_no_original_media_modified(tmp_path: Path) -> None:
    project = _project_with_supplement_required_draft(tmp_path)
    audio_path = _write_audio(project, name="untouched.mp3")
    original = audio_path.read_bytes()

    draft = load_cut_plan_draft(project)
    document = build_supplement_requests_from_cut_plan(project, draft)
    save_cut_plan_supplement_requests(project, document)

    assert audio_path.read_bytes() == original


def test_no_files_under_edit_plan_or_exports_dirs(tmp_path: Path) -> None:
    project = _project_with_supplement_required_draft(tmp_path)
    draft = load_cut_plan_draft(project)
    document = build_supplement_requests_from_cut_plan(project, draft)
    save_cut_plan_supplement_requests(project, document)
    request_id = document.requests[0].request_id

    mock_adapter = MagicMock()
    mock_adapter.search.return_value = [_fake_candidate(request_id=request_id)]
    with patch(f"{_BRIDGE_MODULE}.get_supplement_adapter", return_value=mock_adapter):
        search_candidates_for_cut_plan_request(project, request_id, {"provider": "pexels"})

    assert not get_edit_plan_dir(project.language_work_dir_path).exists()
    assert not get_exports_dir(project.language_work_dir_path).exists()


# --- 30-31: Struktureller Schutz / Regression ---

_FORBIDDEN_SYMBOLS = (
    "build_edit_plan",
    "save_edit_plan",
    "edit_plan_builder",
    "otio_exporter",
    "export_otio_timeline",
    "mark_edit_plans_stale_for_folder",
    "replan_folder_after_supplement",
    "extend_folder_inventory",
    "_set_draft",
    "merge_confirmed_edit_plans",
)


def test_cut_plan_modules_never_reference_forbidden_production_symbols() -> None:
    import re

    import otio_app.services.voiceover_generation.cut_plan_asset_selector as asset_selector_module
    import otio_app.services.voiceover_generation.cut_plan_builder as builder_module
    import otio_app.services.voiceover_generation.cut_plan_supplement_bridge as bridge_module
    import otio_app.services.voiceover_generation.cut_plan_supplement_models as supplement_models_module
    import otio_app.services.voiceover_generation.cut_plan_timeline_service as timeline_module
    import otio_app.services.voiceover_generation.cut_plan_validator as validator_module
    import otio_app.services.voiceover_generation.cut_plan_visual_coverage as coverage_module
    import otio_app.ui.voiceover_generation.cut_plan_tab as tab_module

    for module in (
        asset_selector_module, builder_module, bridge_module, supplement_models_module,
        timeline_module, validator_module, coverage_module, tab_module,
    ):
        source = inspect.getsource(module)
        for forbidden in _FORBIDDEN_SYMBOLS:
            assert not re.search(rf"\b{re.escape(forbidden)}\b", source), (
                f"{module.__name__} referenziert verbotenes Symbol '{forbidden}'."
            )


def test_bridge_does_not_call_production_supplement_orchestration() -> None:
    import otio_app.services.voiceover_generation.cut_plan_supplement_bridge as bridge_module

    source = inspect.getsource(bridge_module)
    forbidden_orchestration = (
        "search_supplement_candidates",
        "acquire_supplement_candidate",
        "run_full_supplement_pipeline_for_folder",
        "analyze_and_update_inventory_for_folder",
        "acquire_top_candidates",
    )
    for forbidden in forbidden_orchestration:
        assert forbidden not in source, f"cut_plan_supplement_bridge.py referenziert '{forbidden}'."


def test_with_voiceover_workflow_unaffected() -> None:
    from otio_app.services import edit_plan_builder, otio_exporter

    assert hasattr(edit_plan_builder, "build_edit_plan")
    assert hasattr(edit_plan_builder, "save_edit_plan")
    assert hasattr(otio_exporter, "build_otio_timeline")


# --- Supplement-Wiederverwendung + stale Validation-Blocker (Juli 2026) ---


def test_apply_accept_clears_stale_black_gap_blocker_from_item(tmp_path: Path) -> None:
    project = _project_with_supplement_required_draft(tmp_path)
    draft = load_cut_plan_draft(project)
    document = build_supplement_requests_from_cut_plan(project, draft)
    request = document.requests[0]
    item = next(i for i in draft.items if i.cut_item_id == request.cut_item_id)
    stale_item = item.model_copy(
        update={"blockers": [CUT_PLAN_ERROR_SUPPLEMENT_REQUIRED, CUT_PLAN_ERROR_BLACK_GAP_DURING_VOICEOVER]}
    )
    stale_draft = draft.model_copy(
        update={
            "items": [stale_item if i.cut_item_id == item.cut_item_id else i for i in draft.items],
            "blockers": [],
        }
    )
    asset = CutPlanSupplementAsset(
        asset_id="supplement_test_image",
        request_id=request.request_id,
        candidate_id="cand_1",
        provider="pexels",
        asset_path=str(tmp_path / "fake.jpg"),
        asset_type="image",
        duration_sec=0.0,
    )
    (tmp_path / "fake.jpg").write_bytes(b"img")

    updated = apply_accepted_supplement_to_cut_plan_item(project, stale_draft, request, asset)
    updated_item = next(i for i in updated.items if i.cut_item_id == request.cut_item_id)
    assert updated_item.planned_visual_segments
    assert CUT_PLAN_ERROR_BLACK_GAP_DURING_VOICEOVER not in updated_item.blockers
    assert CUT_PLAN_ERROR_SUPPLEMENT_REQUIRED not in updated_item.blockers
    assert not any(error.type == CUT_PLAN_ERROR_BLACK_GAP_DURING_VOICEOVER for error in updated.blockers)


def test_merge_prior_supplement_request_state_preserves_acceptance(tmp_path: Path) -> None:
    project = _project_with_supplement_required_draft(tmp_path)
    draft = load_cut_plan_draft(project)
    fresh = build_supplement_requests_from_cut_plan(project, draft)
    asset_path = tmp_path / "accepted.jpg"
    asset_path.write_bytes(b"img")
    prior = fresh.model_copy(
        update={
            "requests": [
                fresh.requests[0].model_copy(
                    update={
                        "status": "ACCEPTED",
                        "accepted_candidate_id": "cand_old",
                        "accepted_asset_id": "supplement_pexels_old",
                        "accepted_asset_path": str(asset_path),
                        "llm_queries": ["Grand Canyon sunset"],
                        "llm_query_status": "PASS",
                    }
                )
            ]
        }
    )
    merged = merge_prior_supplement_request_state(fresh, prior)
    assert merged.requests[0].accepted_asset_id == "supplement_pexels_old"
    assert merged.requests[0].accepted_asset_path == str(asset_path)
    assert merged.requests[0].llm_queries == ["Grand Canyon sunset"]


def test_merge_prior_supplement_request_state_normalizes_stale_status_to_accepted(tmp_path: Path) -> None:
    project = _project_with_supplement_required_draft(tmp_path)
    draft = load_cut_plan_draft(project)
    fresh = build_supplement_requests_from_cut_plan(project, draft)
    asset_path = tmp_path / "accepted.jpg"
    asset_path.write_bytes(b"img")
    prior = fresh.model_copy(
        update={
            "requests": [
                fresh.requests[0].model_copy(
                    update={
                        "status": "CANDIDATES_FOUND",
                        "accepted_asset_id": "supplement_pexels_old",
                        "accepted_asset_path": str(asset_path),
                    }
                )
            ]
        }
    )
    merged = merge_prior_supplement_request_state(fresh, prior)
    assert merged.requests[0].status == "ACCEPTED"


def test_effective_supplement_request_status_uses_accepted_asset_id(tmp_path: Path) -> None:
    request = _supplement_request(
        status="CANDIDATES_FOUND",
        accepted_asset_id="supplement_pexels_1",
        accepted_asset_path="/fake/asset.mp4",
    )
    assert effective_cut_plan_supplement_request_status(request) == "ACCEPTED"


def test_reapply_accepted_supplements_to_cut_plan_applies_segments(tmp_path: Path) -> None:
    project = _project_with_supplement_required_draft(tmp_path)
    draft = load_cut_plan_draft(project)
    document = build_supplement_requests_from_cut_plan(project, draft)
    asset_path = tmp_path / "accepted.jpg"
    asset_path.write_bytes(b"img")
    document = document.model_copy(
        update={
            "requests": [
                document.requests[0].model_copy(
                    update={
                        "accepted_asset_id": "supplement_pexels_reapply",
                        "accepted_asset_path": str(asset_path),
                        "accepted_candidate_id": "cand_reapply",
                        "status": "ACCEPTED",
                    }
                )
            ]
        }
    )
    save_cut_plan_supplement_requests(project, document)
    assert count_unapplied_accepted_supplement_requests(draft, document) == 1

    updated, applied, skipped = reapply_accepted_supplements_to_cut_plan(project)
    assert applied == [document.requests[0].cut_item_id]
    assert skipped == []
    item = next(i for i in updated.items if i.cut_item_id == document.requests[0].cut_item_id)
    assert item.planned_visual_segments
    assert item.asset_selection_status == CUT_PLAN_ASSET_SELECTION_SUPPLEMENT_USED
    assert count_unapplied_accepted_supplement_requests(updated, document) == 0


def test_apply_accept_extends_segment_to_visual_window_when_enabled(tmp_path: Path) -> None:
    project = _project_with_supplement_required_draft(tmp_path)
    draft = load_cut_plan_draft(project)
    item_a, item_b = draft.items[0], draft.items[1] if len(draft.items) > 1 else None
    if item_b is None:
        item_b = CutPlanItem(
            cut_item_id="cut_002_sentence_002",
            source_scope="folder",
            folder_name=FOLDER_A,
            text="Zweiter Satz.",
            timeline_start_sec=item_a.timeline_end_sec + 2.0,
            timeline_end_sec=item_a.timeline_end_sec + 7.0,
            duration_sec=5.0,
            audio_start_sec=item_a.timeline_end_sec + 2.0,
            audio_end_sec=item_a.timeline_end_sec + 7.0,
        )
        draft = draft.model_copy(update={"items": [item_a, item_b]})
    draft = draft.model_copy(
        update={
            "settings_snapshot": {
                **draft.settings_snapshot,
                "extend_visual_window_to_next_sentence": True,
                "max_sentence_pause_extension_sec": 3.0,
            }
        }
    )
    document = build_supplement_requests_from_cut_plan(project, draft)
    request = document.requests[0]
    asset = CutPlanSupplementAsset(
        asset_id="supplement_test_image",
        request_id=request.request_id,
        candidate_id="cand_1",
        provider="pexels",
        asset_path=str(tmp_path / "fake.jpg"),
        asset_type="image",
        duration_sec=0.0,
    )
    (tmp_path / "fake.jpg").write_bytes(b"img")

    updated = apply_accepted_supplement_to_cut_plan_item(project, draft, request, asset)
    updated_item = next(i for i in updated.items if i.cut_item_id == request.cut_item_id)
    segment = updated_item.planned_visual_segments[0]
    assert segment.timeline_out_sec == pytest.approx(item_b.timeline_start_sec)
    assert segment.timeline_out_sec > updated_item.timeline_end_sec + 0.5
