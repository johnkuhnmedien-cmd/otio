"""Phase 4: Voice-over-Autor-Service — Generierung, Asset-Validierung, Staleness."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from otio_app.analysis_models import AssetFolderAnalysis, AssetMediaAnalysis
from otio_app.defaults import (
    VO_ERROR_INVALID_ASSET_ID,
    VO_ERROR_MISSING_ASSET_MAPPING,
    VO_ERROR_MISSING_SUPPLEMENT_REASON,
    VO_ERROR_WEAK_ASSET_MATCH,
)
from otio_app.models import Project, ProjectMode
from otio_app.project_layout import get_folder_inventory_path, get_folder_voiceovers_draft_path, get_llm_runs_dir
from otio_app.services.plan_llm_client import PlanLlmNotConfiguredError, PlanLlmResponse
from otio_app.services.voiceover_generation.dramaturgy_service import save_confirmed_dramaturgy
from otio_app.services.voiceover_generation.folder_voiceover_settings_service import (
    build_default_folder_voiceover_settings,
    save_folder_voiceover_settings,
)
from otio_app.services.voiceover_generation.llm_trace_service import STATUS_FAIL, STATUS_PARSE_FAILED, STATUS_PASS
from otio_app.services.voiceover_generation.models import (
    DramaturgyFolderEntry,
    DramaturgyPlan,
    FolderVoiceoverDraft,
    SentenceItem,
)
from otio_app.services.voiceover_generation.voiceover_author_service import (
    build_inventory_asset_context,
    generate_all_folder_voiceovers,
    generate_folder_voiceover,
    is_draft_stale,
    load_folder_voiceovers_draft,
    update_folder_voiceover_text,
    validate_asset_ids_against_inventory,
)

_SERVICE_MODULE = "otio_app.services.voiceover_generation.voiceover_author_service"


def _make_project(tmp_path: Path, folders: list[str], *, enabled: dict[str, bool] | None = None) -> Project:
    enabled = enabled or {folder: True for folder in folders}
    project_root = tmp_path / "USA"
    project_root.mkdir()
    for folder in folders:
        (project_root / folder).mkdir()
    project = Project(
        id="author-project",
        name="Author Test",
        project_root=str(project_root),
        work_dir=str(project_root / "_otio"),
        project_mode=ProjectMode.WITHOUT_VOICEOVER,
        asset_subdir_names=folders,
        selected_asset_subdirs=folders,
    )
    for folder in folders:
        path = get_folder_inventory_path(project.work_dir_path, folder)
        path.parent.mkdir(parents=True, exist_ok=True)
        analysis = AssetFolderAnalysis(
            folder=folder,
            assets=[
                AssetMediaAnalysis(path=f"{folder}/clip1.mp4", description=f"Weite Aufnahme von {folder}."),
                AssetMediaAnalysis(path=f"{folder}/clip2.mp4", description=f"Nahaufnahme in {folder}."),
            ],
        )
        path.write_text(analysis.model_dump_json(indent=2), encoding="utf-8")

    plan = DramaturgyPlan(
        project_id=project.id,
        recommended_folder_order=[
            DramaturgyFolderEntry(
                folder_name=folder,
                order_index=index,
                enabled=enabled.get(folder, True),
                recommended_word_count=100,
                recommended_min_words=90,
                recommended_max_words=110,
            )
            for index, folder in enumerate(folders, start=1)
        ],
    )
    save_confirmed_dramaturgy(project, plan)
    save_folder_voiceover_settings(project, build_default_folder_voiceover_settings(project))
    return project


VALID_AUTHOR_RESPONSE = json.dumps(
    {
        "voiceover_text_full": "Zwischen den Felswänden scheint das Licht von innen zu leuchten.",
        "sentence_items": [
            {
                "sentence_id": "sentence_001",
                "beat_id": "beat_001",
                "text": "Zwischen den Felswänden scheint das Licht von innen zu leuchten.",
                "visual_intent": "establishing",
                "primary_asset_id": "asset_clip1",
                "backup_asset_ids": ["asset_clip2"],
                "asset_match_reason": "Weite Aufnahme passt zur Einleitung.",
                "asset_confidence": 0.9,
                "estimated_duration_sec": 5.0,
                "must_show": [],
                "avoid_showing": [],
                "needs_supplement_asset": False,
                "supplement_reason": "",
                "source_inventory_asset_ids_considered": ["asset_clip1", "asset_clip2"],
            }
        ],
        "transition_from_previous_used": False,
        "callback_to_previous_used": False,
        "contrast_or_commonality_used": False,
        "risks": [],
    }
)


def _fake_response(raw_text: str = VALID_AUTHOR_RESPONSE) -> PlanLlmResponse:
    return PlanLlmResponse(provider="anthropic", model="claude-sonnet-5", raw_text=raw_text)


def test_build_inventory_asset_context_returns_deterministic_asset_ids(tmp_path: Path) -> None:
    project = _make_project(tmp_path, ["Grand Canyon"])
    context = build_inventory_asset_context(project, "Grand Canyon")
    asset_ids = {asset["asset_id"] for asset in context}
    assert asset_ids == {"asset_clip1", "asset_clip2"}


def test_generate_folder_voiceover_writes_draft_document(tmp_path: Path) -> None:
    project = _make_project(tmp_path, ["Grand Canyon"])
    with patch(f"{_SERVICE_MODULE}.generate_plan_text_with_metadata", return_value=_fake_response()):
        result = generate_folder_voiceover(project, "Grand Canyon", provider="anthropic", model="claude-sonnet-5")

    assert result.status == STATUS_PASS
    assert result.draft is not None
    path = get_folder_voiceovers_draft_path(project.work_dir_path)
    assert path.is_file()

    document = load_folder_voiceovers_draft(project)
    assert len(document.items) == 1
    assert document.items[0].folder_name == "Grand Canyon"


def test_generated_draft_contains_sentence_items(tmp_path: Path) -> None:
    project = _make_project(tmp_path, ["Grand Canyon"])
    with patch(f"{_SERVICE_MODULE}.generate_plan_text_with_metadata", return_value=_fake_response()):
        result = generate_folder_voiceover(project, "Grand Canyon", provider="anthropic", model="claude-sonnet-5")

    assert len(result.draft.sentence_items) == 1
    assert result.draft.sentence_items[0].sentence_id == "sentence_001"
    assert result.draft.sentence_items[0].primary_asset_id == "asset_clip1"


def test_generated_draft_contains_author_run_id(tmp_path: Path) -> None:
    project = _make_project(tmp_path, ["Grand Canyon"])
    with patch(f"{_SERVICE_MODULE}.generate_plan_text_with_metadata", return_value=_fake_response()):
        result = generate_folder_voiceover(project, "Grand Canyon", provider="anthropic", model="claude-sonnet-5")

    assert result.draft.author_run_id == result.llm_run_id
    run_dir = get_llm_runs_dir(project.work_dir_path) / result.llm_run_id
    assert (run_dir / "prompt.txt").is_file()
    assert (run_dir / "raw_llm_response.json").is_file()
    assert (run_dir / "parsed_llm_response.json").is_file()
    assert (run_dir / "llm_request_manifest.json").is_file()


def test_generated_draft_parses_transition_to_next_used(tmp_path: Path) -> None:
    """Nutzerfeedback: neue Spalte/Feld 'Übergang zum nächsten Kapitel' — das
    vom Modell selbst zurückgemeldete transition_to_next_used muss ins
    FolderVoiceoverDraft übernommen werden, genau wie die drei bestehenden
    *_used-Flags."""
    project = _make_project(tmp_path, ["Grand Canyon"])
    response_with_transition_to_next = json.loads(VALID_AUTHOR_RESPONSE)
    response_with_transition_to_next["transition_to_next_used"] = True
    raw_text = json.dumps(response_with_transition_to_next)
    with patch(
        f"{_SERVICE_MODULE}.generate_plan_text_with_metadata",
        return_value=_fake_response(raw_text),
    ):
        result = generate_folder_voiceover(project, "Grand Canyon", provider="anthropic", model="claude-sonnet-5")

    assert result.status == STATUS_PASS
    assert result.draft.transition_to_next_used is True


def test_generated_draft_defaults_transition_to_next_used_to_false_when_absent(
    tmp_path: Path,
) -> None:
    """VALID_AUTHOR_RESPONSE enthält (noch) kein transition_to_next_used —
    das Draft muss trotzdem sauber mit False befüllt werden, nicht crashen."""
    project = _make_project(tmp_path, ["Grand Canyon"])
    with patch(f"{_SERVICE_MODULE}.generate_plan_text_with_metadata", return_value=_fake_response()):
        result = generate_folder_voiceover(project, "Grand Canyon", provider="anthropic", model="claude-sonnet-5")

    assert result.draft.transition_to_next_used is False


def test_generated_draft_parses_valid_pause_after(tmp_path: Path) -> None:
    """Nutzerfeedback: Pausen zwischen Abschnitten — pause_after aus der
    Modell-Antwort muss ins SentenceItem übernommen werden."""
    project = _make_project(tmp_path, ["Grand Canyon"])
    response_with_pause = json.loads(VALID_AUTHOR_RESPONSE)
    response_with_pause["sentence_items"][0]["pause_after"] = "long"
    raw_text = json.dumps(response_with_pause)
    with patch(f"{_SERVICE_MODULE}.generate_plan_text_with_metadata", return_value=_fake_response(raw_text)):
        result = generate_folder_voiceover(project, "Grand Canyon", provider="anthropic", model="claude-sonnet-5")

    assert result.status == STATUS_PASS
    assert result.draft.sentence_items[0].pause_after == "long"


def test_generated_draft_defaults_pause_after_to_empty_when_absent(tmp_path: Path) -> None:
    project = _make_project(tmp_path, ["Grand Canyon"])
    with patch(f"{_SERVICE_MODULE}.generate_plan_text_with_metadata", return_value=_fake_response()):
        result = generate_folder_voiceover(project, "Grand Canyon", provider="anthropic", model="claude-sonnet-5")

    assert result.draft.sentence_items[0].pause_after == ""


def test_generated_draft_rejects_invalid_pause_after_value(tmp_path: Path) -> None:
    """Schutz vor beliebigem Text als ElevenLabs-Pause-Tag (siehe
    tts_text_builder) — ein nicht erlaubter Wert wird auf '' zurückgesetzt,
    statt unverändert übernommen zu werden."""
    project = _make_project(tmp_path, ["Grand Canyon"])
    response_with_invalid_pause = json.loads(VALID_AUTHOR_RESPONSE)
    response_with_invalid_pause["sentence_items"][0]["pause_after"] = "<script>alert(1)</script>"
    raw_text = json.dumps(response_with_invalid_pause)
    with patch(f"{_SERVICE_MODULE}.generate_plan_text_with_metadata", return_value=_fake_response(raw_text)):
        result = generate_folder_voiceover(project, "Grand Canyon", provider="anthropic", model="claude-sonnet-5")

    assert result.draft.sentence_items[0].pause_after == ""


def test_generate_raises_for_disabled_folder(tmp_path: Path) -> None:
    project = _make_project(tmp_path, ["Grand Canyon", "Yellowstone"], enabled={"Grand Canyon": True, "Yellowstone": False})
    with pytest.raises(ValueError):
        generate_folder_voiceover(project, "Yellowstone", provider="anthropic", model="claude-sonnet-5")


def test_generate_all_only_processes_enabled_folders(tmp_path: Path) -> None:
    project = _make_project(
        tmp_path, ["Grand Canyon", "Yellowstone"], enabled={"Grand Canyon": True, "Yellowstone": False}
    )
    with patch(f"{_SERVICE_MODULE}.generate_plan_text_with_metadata", return_value=_fake_response()):
        results = generate_all_folder_voiceovers(project, provider="anthropic", model="claude-sonnet-5")

    assert len(results) == 1
    assert results[0].draft.folder_name == "Grand Canyon"


def test_generate_all_reports_progress(tmp_path: Path) -> None:
    project = _make_project(tmp_path, ["Grand Canyon", "Yellowstone"])
    progress_calls = []
    with patch(f"{_SERVICE_MODULE}.generate_plan_text_with_metadata", return_value=_fake_response()):
        generate_all_folder_voiceovers(
            project,
            provider="anthropic",
            model="claude-sonnet-5",
            progress_callback=lambda folder, index, total: progress_calls.append((folder, index, total)),
        )
    assert progress_calls == [("Grand Canyon", 1, 2), ("Yellowstone", 2, 2)]


def test_sanitization_removes_invalid_asset_ids(tmp_path: Path) -> None:
    project = _make_project(tmp_path, ["Grand Canyon"])
    hallucinated_response = VALID_AUTHOR_RESPONSE.replace("asset_clip1", "asset_made_up_id")
    with patch(f"{_SERVICE_MODULE}.generate_plan_text_with_metadata", return_value=_fake_response(hallucinated_response)):
        result = generate_folder_voiceover(project, "Grand Canyon", provider="anthropic", model="claude-sonnet-5")

    assert result.status == STATUS_PASS
    # Die halluzinierte ID wurde entfernt (primary_asset_id == "").
    assert result.draft.sentence_items[0].primary_asset_id == ""


def test_validate_asset_ids_detects_invalid_asset_id(tmp_path: Path) -> None:
    project = _make_project(tmp_path, ["Grand Canyon"])
    draft = FolderVoiceoverDraft(
        project_id=project.id,
        folder_name="Grand Canyon",
        voiceover_text_full="Text",
        word_count=1,
        sentence_items=[
            SentenceItem(sentence_id="sentence_001", text="Text", primary_asset_id="asset_does_not_exist")
        ],
    )
    errors = validate_asset_ids_against_inventory(project, "Grand Canyon", draft)
    assert any(error.type == VO_ERROR_INVALID_ASSET_ID for error in errors)


def test_validate_asset_ids_detects_missing_asset_mapping(tmp_path: Path) -> None:
    project = _make_project(tmp_path, ["Grand Canyon"])
    draft = FolderVoiceoverDraft(
        project_id=project.id,
        folder_name="Grand Canyon",
        voiceover_text_full="Text",
        word_count=1,
        sentence_items=[
            SentenceItem(sentence_id="sentence_001", text="Text", primary_asset_id="", needs_supplement_asset=False)
        ],
    )
    errors = validate_asset_ids_against_inventory(project, "Grand Canyon", draft)
    assert any(error.type == VO_ERROR_MISSING_ASSET_MAPPING for error in errors)


def test_validate_asset_ids_detects_missing_supplement_reason(tmp_path: Path) -> None:
    project = _make_project(tmp_path, ["Grand Canyon"])
    draft = FolderVoiceoverDraft(
        project_id=project.id,
        folder_name="Grand Canyon",
        voiceover_text_full="Text",
        word_count=1,
        sentence_items=[
            SentenceItem(
                sentence_id="sentence_001", text="Text", primary_asset_id="",
                needs_supplement_asset=True, supplement_reason="",
            )
        ],
    )
    errors = validate_asset_ids_against_inventory(project, "Grand Canyon", draft)
    assert any(error.type == VO_ERROR_MISSING_SUPPLEMENT_REASON for error in errors)


def test_validate_asset_ids_detects_weak_asset_match(tmp_path: Path) -> None:
    project = _make_project(tmp_path, ["Grand Canyon"])
    draft = FolderVoiceoverDraft(
        project_id=project.id,
        folder_name="Grand Canyon",
        voiceover_text_full="Text",
        word_count=1,
        sentence_items=[
            SentenceItem(
                sentence_id="sentence_001", text="Text", primary_asset_id="asset_clip1", asset_confidence=0.1,
            )
        ],
    )
    errors = validate_asset_ids_against_inventory(project, "Grand Canyon", draft)
    assert any(error.type == VO_ERROR_WEAK_ASSET_MATCH for error in errors)


def test_generate_missing_api_key_returns_fail(tmp_path: Path) -> None:
    project = _make_project(tmp_path, ["Grand Canyon"])
    with patch(
        f"{_SERVICE_MODULE}.generate_plan_text_with_metadata",
        side_effect=PlanLlmNotConfiguredError("ANTHROPIC_API_KEY ist nicht gesetzt."),
    ):
        result = generate_folder_voiceover(project, "Grand Canyon", provider="anthropic", model="claude-sonnet-5")
    assert result.status == STATUS_FAIL
    assert result.draft is None


def test_generate_generic_llm_exception_returns_fail_status(tmp_path: Path) -> None:
    """Jeder unerwartete LLM-/SDK-/Netzwerkfehler soll als kontrollierter FAIL
    zurückkommen statt die Streamlit-Seite crashen zu lassen."""
    project = _make_project(tmp_path, ["Grand Canyon"])
    with patch(
        f"{_SERVICE_MODULE}.generate_plan_text_with_metadata",
        side_effect=TimeoutError("LLM-Anfrage hat das Zeitlimit überschritten."),
    ):
        result = generate_folder_voiceover(
            project, "Grand Canyon", provider="anthropic", model="claude-sonnet-5"
        )
    assert result.status == STATUS_FAIL
    assert result.draft is None


def test_generate_invalid_json_does_not_overwrite_existing_draft(tmp_path: Path) -> None:
    project = _make_project(tmp_path, ["Grand Canyon"])
    with patch(f"{_SERVICE_MODULE}.generate_plan_text_with_metadata", return_value=_fake_response()):
        first = generate_folder_voiceover(project, "Grand Canyon", provider="anthropic", model="claude-sonnet-5")

    with patch(
        f"{_SERVICE_MODULE}.generate_plan_text_with_metadata",
        return_value=_fake_response("not valid json {{"),
    ):
        second = generate_folder_voiceover(project, "Grand Canyon", provider="anthropic", model="claude-sonnet-5")

    assert second.status == STATUS_PARSE_FAILED
    assert second.draft is None

    document = load_folder_voiceovers_draft(project)
    assert document.items[0].voiceover_text_full == first.draft.voiceover_text_full


def test_update_folder_voiceover_text_sets_needs_validation(tmp_path: Path) -> None:
    project = _make_project(tmp_path, ["Grand Canyon"])
    with patch(f"{_SERVICE_MODULE}.generate_plan_text_with_metadata", return_value=_fake_response()):
        generate_folder_voiceover(project, "Grand Canyon", provider="anthropic", model="claude-sonnet-5")

    updated = update_folder_voiceover_text(project, "Grand Canyon", "Ein komplett neuer manueller Text.")
    assert updated.status == "NEEDS_VALIDATION"
    assert updated.word_count == 5
    assert updated.voiceover_text_full == "Ein komplett neuer manueller Text."


def test_draft_is_stale_after_settings_change(tmp_path: Path) -> None:
    project = _make_project(tmp_path, ["Grand Canyon"])
    with patch(f"{_SERVICE_MODULE}.generate_plan_text_with_metadata", return_value=_fake_response()):
        result = generate_folder_voiceover(project, "Grand Canyon", provider="anthropic", model="claude-sonnet-5")

    assert is_draft_stale(project, "Grand Canyon", result.draft) is False

    # Settings ändern -> Hash ändert sich -> Draft gilt als veraltet.
    settings_doc = build_default_folder_voiceover_settings(project)
    for setting in settings_doc.settings:
        if setting.folder_name == "Grand Canyon":
            setting.target_words = 999
    save_folder_voiceover_settings(project, settings_doc)

    assert is_draft_stale(project, "Grand Canyon", result.draft) is True


def test_upsert_preserves_other_folders(tmp_path: Path) -> None:
    project = _make_project(tmp_path, ["Grand Canyon", "Yellowstone"])
    with patch(f"{_SERVICE_MODULE}.generate_plan_text_with_metadata", return_value=_fake_response()):
        generate_folder_voiceover(project, "Grand Canyon", provider="anthropic", model="claude-sonnet-5")
        generate_folder_voiceover(project, "Yellowstone", provider="anthropic", model="claude-sonnet-5")

    document = load_folder_voiceovers_draft(project)
    assert {item.folder_name for item in document.items} == {"Grand Canyon", "Yellowstone"}
