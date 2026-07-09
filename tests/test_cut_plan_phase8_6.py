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
    load_cut_plan_supplement_candidates_for_request,
    load_cut_plan_supplement_requests,
    save_cut_plan_supplement_requests,
    search_candidates_for_cut_plan_request,
)
from otio_app.services.voiceover_generation.cut_plan_supplement_models import (
    CutPlanSupplementAsset,
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
    audio_dir = project.work_dir_path / "voiceover_generation" / "audio"
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
    request_id: str = "cutreq_x",
) -> SupplementCandidate:
    return SupplementCandidate(
        candidate_id=candidate_id,
        supplement_request_id=request_id,
        provider="pexels",
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


# --- 5-6: Isolation / kein automatischer Search-Trigger ---


def test_no_file_written_under_supplement_dir(tmp_path: Path) -> None:
    project = _project_with_supplement_required_draft(tmp_path)
    draft = load_cut_plan_draft(project)
    document = build_supplement_requests_from_cut_plan(project, draft)
    save_cut_plan_supplement_requests(project, document)
    assert not get_supplement_dir(project.work_dir_path).exists()


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

    path = get_cut_plan_supplement_candidates_path(project.work_dir_path)
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
        project.work_dir_path / "voiceover_generation" / "cut_plan" / "supplement_assets" / request_id / "fake.jpg"
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
    item = _minimal_item(blockers=[CUT_PLAN_ERROR_SUPPLEMENT_REQUIRED, "SHOT_TOO_SHORT"])
    cut_plan = CutPlanDocument(project_id="p1", items=[item])
    project = MagicMock()
    project.id = "p1"

    updated = apply_accepted_supplement_to_cut_plan_item(project, cut_plan, request, asset)
    assert "SHOT_TOO_SHORT" in updated.items[0].blockers
    assert CUT_PLAN_ERROR_SUPPLEMENT_REQUIRED not in updated.items[0].blockers
    # Dokument-Status muss NEEDS_REVIEW bleiben, solange andere Blocker existieren.
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

    with pytest.raises(ValueError, match="already has an accepted candidate"):
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
    assert not get_edit_plan_dir(project.work_dir_path).exists()


def test_no_otio_export_triggered(tmp_path: Path) -> None:
    project = _project_with_supplement_required_draft(tmp_path)
    draft = load_cut_plan_draft(project)
    document = build_supplement_requests_from_cut_plan(project, draft)
    save_cut_plan_supplement_requests(project, document)
    assert not get_exports_dir(project.work_dir_path).exists()


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

    assert not get_edit_plan_dir(project.work_dir_path).exists()
    assert not get_exports_dir(project.work_dir_path).exists()


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
