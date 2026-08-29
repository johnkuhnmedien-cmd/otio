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
    VOICEOVER_GEN_DEFAULT_FOLDER_MAX_WORDS,
    VOICEOVER_GEN_DEFAULT_FOLDER_MIN_WORDS,
    VOICEOVER_GEN_DEFAULT_FOLDER_TARGET_WORDS,
)
from otio_app.models import Project, ProjectMode
from otio_app.project_layout import (
    get_folder_inventory_path,
    get_folder_voiceover_settings_path,
    get_folder_voiceovers_draft_path,
    get_llm_runs_dir,
)
from otio_app.services.plan_llm_client import PlanLlmNotConfiguredError, PlanLlmResponse
from otio_app.services.voiceover_generation.dramaturgy_service import save_confirmed_dramaturgy
from otio_app.services.voiceover_generation.folder_voiceover_settings_service import (
    build_default_folder_voiceover_settings,
    load_folder_voiceover_settings,
    save_folder_voiceover_settings,
)
from otio_app.services.voiceover_generation.llm_trace_service import STATUS_FAIL, STATUS_PARSE_FAILED, STATUS_PASS
from otio_app.services.voiceover_generation.models import (
    ClosingVisualPlan,
    DramaturgyFolderEntry,
    DramaturgyPlan,
    FolderVoiceoverDraft,
    SentenceItem,
    SentenceSegmentAssetPlan,
)
from otio_app.services.voiceover_generation.voiceover_author_service import (
    build_inventory_asset_context,
    generate_all_folder_voiceovers,
    generate_folder_voiceover,
    get_folder_voiceover_draft,
    is_draft_stale,
    load_folder_voiceovers_draft,
    regenerate_all_folder_voiceovers_with_standard_word_target,
    regenerate_folder_voiceover_with_standard_word_target,
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
    run_dir = get_llm_runs_dir(project.language_work_dir_path) / result.llm_run_id
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


# --- Phase 4 (Asset-bewusste Cut-Plan-Vorbereitung): second_backup_asset_ids / visual_asset_plan ---


def test_generated_draft_parses_second_backup_asset_ids(tmp_path: Path) -> None:
    project = _make_project(tmp_path, ["Grand Canyon"])
    response = json.loads(VALID_AUTHOR_RESPONSE)
    response["sentence_items"][0]["second_backup_asset_ids"] = ["asset_clip2"]
    with patch(
        f"{_SERVICE_MODULE}.generate_plan_text_with_metadata",
        return_value=_fake_response(json.dumps(response)),
    ):
        result = generate_folder_voiceover(project, "Grand Canyon", provider="anthropic", model="claude-sonnet-5")

    assert result.status == STATUS_PASS
    assert result.draft.sentence_items[0].second_backup_asset_ids == ["asset_clip2"]


def test_generated_draft_defaults_second_backup_asset_ids_to_empty_when_absent(
    tmp_path: Path,
) -> None:
    project = _make_project(tmp_path, ["Grand Canyon"])
    with patch(f"{_SERVICE_MODULE}.generate_plan_text_with_metadata", return_value=_fake_response()):
        result = generate_folder_voiceover(project, "Grand Canyon", provider="anthropic", model="claude-sonnet-5")

    assert result.draft.sentence_items[0].second_backup_asset_ids == []


def test_generated_draft_sanitizes_hallucinated_second_backup_asset_id(tmp_path: Path) -> None:
    """Analog zur bestehenden Sanitisierung von primary_asset_id/
    backup_asset_ids — eine halluzinierte second_backup_asset_id darf nicht
    unbemerkt im Draft landen."""
    project = _make_project(tmp_path, ["Grand Canyon"])
    response = json.loads(VALID_AUTHOR_RESPONSE)
    response["sentence_items"][0]["second_backup_asset_ids"] = ["asset_made_up_id", "asset_clip2"]
    with patch(
        f"{_SERVICE_MODULE}.generate_plan_text_with_metadata",
        return_value=_fake_response(json.dumps(response)),
    ):
        result = generate_folder_voiceover(project, "Grand Canyon", provider="anthropic", model="claude-sonnet-5")

    assert result.draft.sentence_items[0].second_backup_asset_ids == ["asset_clip2"]


def test_validate_asset_ids_detects_invalid_second_backup_asset_id(tmp_path: Path) -> None:
    project = _make_project(tmp_path, ["Grand Canyon"])
    draft = FolderVoiceoverDraft(
        project_id=project.id,
        folder_name="Grand Canyon",
        voiceover_text_full="Text",
        word_count=1,
        sentence_items=[
            SentenceItem(
                sentence_id="sentence_001",
                text="Text",
                primary_asset_id="asset_clip1",
                second_backup_asset_ids=["asset_does_not_exist"],
            )
        ],
    )
    errors = validate_asset_ids_against_inventory(project, "Grand Canyon", draft)
    assert any(error.type == VO_ERROR_INVALID_ASSET_ID for error in errors)


def test_generated_draft_parses_visual_asset_plan(tmp_path: Path) -> None:
    project = _make_project(tmp_path, ["Grand Canyon"])
    response = json.loads(VALID_AUTHOR_RESPONSE)
    response["sentence_items"][0]["visual_asset_plan"] = {
        "preferred_cut_count": 2,
        "reuse_risk": "high",
        "needs_visual_variety": True,
        "asset_strategy_reason": "Nur ein passendes Asset für diesen langen Satz.",
        "supplement_search_hint": "Grand Canyon rim wide shot",
    }
    with patch(
        f"{_SERVICE_MODULE}.generate_plan_text_with_metadata",
        return_value=_fake_response(json.dumps(response)),
    ):
        result = generate_folder_voiceover(project, "Grand Canyon", provider="anthropic", model="claude-sonnet-5")

    plan = result.draft.sentence_items[0].visual_asset_plan
    assert plan.preferred_cut_count == 2
    assert plan.reuse_risk == "high"
    assert plan.needs_visual_variety is True
    assert plan.asset_strategy_reason == "Nur ein passendes Asset für diesen langen Satz."
    assert plan.supplement_search_hint == "Grand Canyon rim wide shot"


def test_generated_draft_defaults_visual_asset_plan_when_absent(tmp_path: Path) -> None:
    project = _make_project(tmp_path, ["Grand Canyon"])
    with patch(f"{_SERVICE_MODULE}.generate_plan_text_with_metadata", return_value=_fake_response()):
        result = generate_folder_voiceover(project, "Grand Canyon", provider="anthropic", model="claude-sonnet-5")

    plan = result.draft.sentence_items[0].visual_asset_plan
    assert plan.preferred_cut_count == 1
    assert plan.reuse_risk == ""
    assert plan.needs_visual_variety is False
    assert plan.asset_strategy_reason == ""
    assert plan.supplement_search_hint == ""


def test_generated_draft_rejects_invalid_reuse_risk_value(tmp_path: Path) -> None:
    project = _make_project(tmp_path, ["Grand Canyon"])
    response = json.loads(VALID_AUTHOR_RESPONSE)
    response["sentence_items"][0]["visual_asset_plan"] = {"reuse_risk": "extremely_high_not_a_real_value"}
    with patch(
        f"{_SERVICE_MODULE}.generate_plan_text_with_metadata",
        return_value=_fake_response(json.dumps(response)),
    ):
        result = generate_folder_voiceover(project, "Grand Canyon", provider="anthropic", model="claude-sonnet-5")

    assert result.draft.sentence_items[0].visual_asset_plan.reuse_risk == ""


def test_generated_draft_handles_malformed_visual_asset_plan_gracefully(tmp_path: Path) -> None:
    """Ein visual_asset_plan, das kein dict ist (z. B. versehentlich ein
    String), darf das Parsen des ganzen sentence_item nicht scheitern lassen."""
    project = _make_project(tmp_path, ["Grand Canyon"])
    response = json.loads(VALID_AUTHOR_RESPONSE)
    response["sentence_items"][0]["visual_asset_plan"] = "not a dict"
    with patch(
        f"{_SERVICE_MODULE}.generate_plan_text_with_metadata",
        return_value=_fake_response(json.dumps(response)),
    ):
        result = generate_folder_voiceover(project, "Grand Canyon", provider="anthropic", model="claude-sonnet-5")

    assert result.status == STATUS_PASS
    assert result.draft.sentence_items[0].visual_asset_plan.preferred_cut_count == 1


# --- Phase 7 (Asset-bewusste Cut-Plan-Vorbereitung): planned_segments ---


def test_generated_draft_parses_planned_segments(tmp_path: Path) -> None:
    project = _make_project(tmp_path, ["Grand Canyon"])
    response = json.loads(VALID_AUTHOR_RESPONSE)
    response["sentence_items"][0]["planned_segments"] = [
        {"segment_order": 1, "primary_asset_id": "asset_clip1", "backup_asset_ids": ["asset_clip2"]},
        {"segment_order": 2, "primary_asset_id": "asset_clip2", "backup_asset_ids": []},
    ]
    with patch(
        f"{_SERVICE_MODULE}.generate_plan_text_with_metadata",
        return_value=_fake_response(json.dumps(response)),
    ):
        result = generate_folder_voiceover(project, "Grand Canyon", provider="anthropic", model="claude-sonnet-5")

    segments = result.draft.sentence_items[0].planned_segments
    assert len(segments) == 2
    assert segments[0].segment_order == 1
    assert segments[0].primary_asset_id == "asset_clip1"
    assert segments[0].backup_asset_ids == ["asset_clip2"]
    assert segments[1].segment_order == 2
    assert segments[1].primary_asset_id == "asset_clip2"


def test_generated_draft_defaults_planned_segments_to_empty_when_absent(tmp_path: Path) -> None:
    project = _make_project(tmp_path, ["Grand Canyon"])
    with patch(f"{_SERVICE_MODULE}.generate_plan_text_with_metadata", return_value=_fake_response()):
        result = generate_folder_voiceover(project, "Grand Canyon", provider="anthropic", model="claude-sonnet-5")

    assert result.draft.sentence_items[0].planned_segments == []


def test_generated_draft_sanitizes_hallucinated_planned_segment_asset_id(tmp_path: Path) -> None:
    project = _make_project(tmp_path, ["Grand Canyon"])
    response = json.loads(VALID_AUTHOR_RESPONSE)
    response["sentence_items"][0]["planned_segments"] = [
        {
            "segment_order": 1,
            "primary_asset_id": "asset_made_up_id",
            "backup_asset_ids": ["asset_clip2", "asset_also_made_up"],
        }
    ]
    with patch(
        f"{_SERVICE_MODULE}.generate_plan_text_with_metadata",
        return_value=_fake_response(json.dumps(response)),
    ):
        result = generate_folder_voiceover(project, "Grand Canyon", provider="anthropic", model="claude-sonnet-5")

    segment = result.draft.sentence_items[0].planned_segments[0]
    assert segment.primary_asset_id == ""
    assert segment.backup_asset_ids == ["asset_clip2"]


def test_generated_draft_ignores_malformed_planned_segments_entries(tmp_path: Path) -> None:
    """Ein planned_segments-Eintrag, der kein dict ist, darf das Parsen des
    restlichen sentence_item nicht scheitern lassen."""
    project = _make_project(tmp_path, ["Grand Canyon"])
    response = json.loads(VALID_AUTHOR_RESPONSE)
    response["sentence_items"][0]["planned_segments"] = [
        "not a dict",
        {"segment_order": 2, "primary_asset_id": "asset_clip2"},
    ]
    with patch(
        f"{_SERVICE_MODULE}.generate_plan_text_with_metadata",
        return_value=_fake_response(json.dumps(response)),
    ):
        result = generate_folder_voiceover(project, "Grand Canyon", provider="anthropic", model="claude-sonnet-5")

    assert result.status == STATUS_PASS
    segments = result.draft.sentence_items[0].planned_segments
    assert len(segments) == 1
    assert segments[0].segment_order == 2


def test_validate_asset_ids_detects_invalid_planned_segment_asset_id(tmp_path: Path) -> None:
    project = _make_project(tmp_path, ["Grand Canyon"])
    draft = FolderVoiceoverDraft(
        project_id=project.id,
        folder_name="Grand Canyon",
        voiceover_text_full="Text",
        word_count=1,
        sentence_items=[
            SentenceItem(
                sentence_id="sentence_001",
                text="Text",
                primary_asset_id="asset_clip1",
                planned_segments=[
                    SentenceSegmentAssetPlan(segment_order=1, primary_asset_id="asset_does_not_exist")
                ],
            )
        ],
    )
    errors = validate_asset_ids_against_inventory(project, "Grand Canyon", draft)
    assert any(error.type == VO_ERROR_INVALID_ASSET_ID for error in errors)


def test_validate_asset_ids_detects_invalid_planned_segment_backup_asset_id(tmp_path: Path) -> None:
    project = _make_project(tmp_path, ["Grand Canyon"])
    draft = FolderVoiceoverDraft(
        project_id=project.id,
        folder_name="Grand Canyon",
        voiceover_text_full="Text",
        word_count=1,
        sentence_items=[
            SentenceItem(
                sentence_id="sentence_001",
                text="Text",
                primary_asset_id="asset_clip1",
                planned_segments=[
                    SentenceSegmentAssetPlan(
                        segment_order=1,
                        primary_asset_id="asset_clip1",
                        backup_asset_ids=["asset_does_not_exist"],
                    )
                ],
            )
        ],
    )
    errors = validate_asset_ids_against_inventory(project, "Grand Canyon", draft)
    assert any(error.type == VO_ERROR_INVALID_ASSET_ID for error in errors)


# --- Closing Visual Plan (Nutzervorgabe Juli 2026: "kein closing asset nach
# dem letzten Satz, der die Pause ausfüllt") ---


def test_generated_draft_parses_closing_visual_plan(tmp_path: Path) -> None:
    project = _make_project(tmp_path, ["Grand Canyon"])
    response = json.loads(VALID_AUTHOR_RESPONSE)
    response["closing_visual_plan"] = {
        "visual_intent": "aerial establishing shot to close the section",
        "primary_asset_id": "asset_clip2",
        "backup_asset_ids": ["asset_clip1"],
        "second_backup_asset_ids": [],
        "needs_supplement_asset": False,
        "supplement_reason": "",
        "supplement_search_hint": "",
        "asset_strategy_reason": "Ruhiger Abschluss, unterschiedlich vom letzten Satz.",
    }
    with patch(
        f"{_SERVICE_MODULE}.generate_plan_text_with_metadata",
        return_value=_fake_response(json.dumps(response)),
    ):
        result = generate_folder_voiceover(project, "Grand Canyon", provider="anthropic", model="claude-sonnet-5")

    plan = result.draft.closing_visual_plan
    assert plan.visual_intent == "aerial establishing shot to close the section"
    assert plan.primary_asset_id == "asset_clip2"
    assert plan.backup_asset_ids == ["asset_clip1"]
    assert plan.needs_supplement_asset is False
    assert plan.asset_strategy_reason == "Ruhiger Abschluss, unterschiedlich vom letzten Satz."


def test_generated_draft_defaults_closing_visual_plan_when_absent(tmp_path: Path) -> None:
    project = _make_project(tmp_path, ["Grand Canyon"])
    with patch(f"{_SERVICE_MODULE}.generate_plan_text_with_metadata", return_value=_fake_response()):
        result = generate_folder_voiceover(project, "Grand Canyon", provider="anthropic", model="claude-sonnet-5")

    plan = result.draft.closing_visual_plan
    assert plan.visual_intent == ""
    assert plan.primary_asset_id == ""
    assert plan.backup_asset_ids == []
    assert plan.needs_supplement_asset is False


def test_generated_draft_handles_malformed_closing_visual_plan_gracefully(tmp_path: Path) -> None:
    project = _make_project(tmp_path, ["Grand Canyon"])
    response = json.loads(VALID_AUTHOR_RESPONSE)
    response["closing_visual_plan"] = "not a dict"
    with patch(
        f"{_SERVICE_MODULE}.generate_plan_text_with_metadata",
        return_value=_fake_response(json.dumps(response)),
    ):
        result = generate_folder_voiceover(project, "Grand Canyon", provider="anthropic", model="claude-sonnet-5")

    assert result.status == STATUS_PASS
    assert result.draft.closing_visual_plan.primary_asset_id == ""


def test_generated_draft_sanitizes_hallucinated_closing_visual_plan_asset_id(tmp_path: Path) -> None:
    project = _make_project(tmp_path, ["Grand Canyon"])
    response = json.loads(VALID_AUTHOR_RESPONSE)
    response["closing_visual_plan"] = {
        "primary_asset_id": "asset_made_up_id",
        "backup_asset_ids": ["asset_clip2", "asset_also_made_up"],
        "second_backup_asset_ids": ["asset_made_up_second"],
    }
    with patch(
        f"{_SERVICE_MODULE}.generate_plan_text_with_metadata",
        return_value=_fake_response(json.dumps(response)),
    ):
        result = generate_folder_voiceover(project, "Grand Canyon", provider="anthropic", model="claude-sonnet-5")

    plan = result.draft.closing_visual_plan
    assert plan.primary_asset_id == ""
    assert plan.backup_asset_ids == ["asset_clip2"]
    assert plan.second_backup_asset_ids == []


def test_validate_asset_ids_detects_invalid_closing_visual_plan_asset_id(tmp_path: Path) -> None:
    project = _make_project(tmp_path, ["Grand Canyon"])
    draft = FolderVoiceoverDraft(
        project_id=project.id,
        folder_name="Grand Canyon",
        voiceover_text_full="Text",
        word_count=1,
        sentence_items=[
            SentenceItem(sentence_id="sentence_001", text="Text", primary_asset_id="asset_clip1"),
        ],
        closing_visual_plan=ClosingVisualPlan(primary_asset_id="asset_does_not_exist"),
    )
    errors = validate_asset_ids_against_inventory(project, "Grand Canyon", draft)
    assert any(
        error.type == VO_ERROR_INVALID_ASSET_ID and error.sentence_id == "closing" for error in errors
    )


def test_validate_asset_ids_detects_missing_closing_visual_plan_supplement_reason(tmp_path: Path) -> None:
    project = _make_project(tmp_path, ["Grand Canyon"])
    draft = FolderVoiceoverDraft(
        project_id=project.id,
        folder_name="Grand Canyon",
        voiceover_text_full="Text",
        word_count=1,
        sentence_items=[
            SentenceItem(sentence_id="sentence_001", text="Text", primary_asset_id="asset_clip1"),
        ],
        closing_visual_plan=ClosingVisualPlan(needs_supplement_asset=True, supplement_reason=""),
    )
    errors = validate_asset_ids_against_inventory(project, "Grand Canyon", draft)
    assert any(
        error.type == VO_ERROR_MISSING_SUPPLEMENT_REASON and error.sentence_id == "closing" for error in errors
    )


# --- Phase 6 (Asset-bewusste Cut-Plan-Vorbereitung): kombinierte Regenerier-Aktion ---


def test_regenerate_folder_voiceover_with_standard_word_target_updates_settings_and_regenerates(
    tmp_path: Path,
) -> None:
    project = _make_project(tmp_path, ["Grand Canyon"])
    with patch(f"{_SERVICE_MODULE}.generate_plan_text_with_metadata", return_value=_fake_response()):
        result = regenerate_folder_voiceover_with_standard_word_target(
            project, "Grand Canyon", provider="anthropic", model="claude-sonnet-5"
        )

    assert result.status == STATUS_PASS

    settings_doc = load_folder_voiceover_settings(project)
    setting = next(s for s in settings_doc.settings if s.folder_name == "Grand Canyon")
    assert setting.target_words == VOICEOVER_GEN_DEFAULT_FOLDER_TARGET_WORDS
    assert setting.min_words == VOICEOVER_GEN_DEFAULT_FOLDER_MIN_WORDS
    assert setting.max_words == VOICEOVER_GEN_DEFAULT_FOLDER_MAX_WORDS


def test_regenerate_folder_voiceover_with_standard_word_target_raises_without_settings(
    tmp_path: Path,
) -> None:
    project = _make_project(tmp_path, ["Grand Canyon"])
    # Settings absichtlich löschen, um den Fehlerfall zu erzwingen.
    get_folder_voiceover_settings_path(project.work_dir_path).unlink()
    with pytest.raises(ValueError):
        regenerate_folder_voiceover_with_standard_word_target(
            project, "Grand Canyon", provider="anthropic", model="claude-sonnet-5"
        )


def test_regenerate_all_folder_voiceovers_with_standard_word_target_updates_all_enabled(
    tmp_path: Path,
) -> None:
    project = _make_project(tmp_path, ["Grand Canyon", "Yellowstone"])
    with patch(f"{_SERVICE_MODULE}.generate_plan_text_with_metadata", return_value=_fake_response()):
        results = regenerate_all_folder_voiceovers_with_standard_word_target(
            project, provider="anthropic", model="claude-sonnet-5"
        )

    assert len(results) == 2
    assert all(result.status == STATUS_PASS for result in results)

    settings_doc = load_folder_voiceover_settings(project)
    for setting in settings_doc.settings:
        assert setting.target_words == VOICEOVER_GEN_DEFAULT_FOLDER_TARGET_WORDS


def test_regenerate_all_with_standard_word_target_reports_progress(tmp_path: Path) -> None:
    project = _make_project(tmp_path, ["Grand Canyon", "Yellowstone"])
    progress_calls: list[tuple[str, int, int]] = []

    def _progress(folder_name: str, index: int, total: int) -> None:
        progress_calls.append((folder_name, index, total))

    with patch(f"{_SERVICE_MODULE}.generate_plan_text_with_metadata", return_value=_fake_response()):
        regenerate_all_folder_voiceovers_with_standard_word_target(
            project, provider="anthropic", model="claude-sonnet-5", progress_callback=_progress
        )

    assert progress_calls == [("Grand Canyon", 1, 2), ("Yellowstone", 2, 2)]


def test_generate_missing_api_key_returns_fail(tmp_path: Path) -> None:
    project = _make_project(tmp_path, ["Grand Canyon"])
    with patch(
        f"{_SERVICE_MODULE}.generate_plan_text_with_metadata",
        side_effect=PlanLlmNotConfiguredError("ANTHROPIC_API_KEY ist nicht gesetzt."),
    ) as mock_llm:
        result = generate_folder_voiceover(project, "Grand Canyon", provider="anthropic", model="claude-sonnet-5")
    assert result.status == STATUS_FAIL
    assert result.draft is None
    assert mock_llm.call_count == 2


def test_generate_generic_llm_exception_returns_fail_status(tmp_path: Path) -> None:
    """Jeder unerwartete LLM-/SDK-/Netzwerkfehler soll als kontrollierter FAIL
    zurückkommen statt die Streamlit-Seite crashen zu lassen."""
    project = _make_project(tmp_path, ["Grand Canyon"])
    with patch(
        f"{_SERVICE_MODULE}.generate_plan_text_with_metadata",
        side_effect=TimeoutError("LLM-Anfrage hat das Zeitlimit überschritten."),
    ) as mock_llm:
        result = generate_folder_voiceover(
            project, "Grand Canyon", provider="anthropic", model="claude-sonnet-5"
        )
    assert result.status == STATUS_FAIL
    assert result.draft is None
    assert mock_llm.call_count == 2


def test_generate_retries_llm_timeout_then_succeeds(tmp_path: Path) -> None:
    project = _make_project(tmp_path, ["Grand Canyon"])
    with patch(
        f"{_SERVICE_MODULE}.generate_plan_text_with_metadata",
        side_effect=[
            TimeoutError("LLM-Anfrage hat das Zeitlimit überschritten."),
            _fake_response(),
        ],
    ) as mock_llm:
        result = generate_folder_voiceover(
            project, "Grand Canyon", provider="anthropic", model="claude-sonnet-5"
        )
    assert result.status == STATUS_PASS
    assert result.draft is not None
    assert mock_llm.call_count == 2


def test_generate_retries_parse_error_then_succeeds(tmp_path: Path) -> None:
    project = _make_project(tmp_path, ["Grand Canyon"])
    with patch(
        f"{_SERVICE_MODULE}.generate_plan_text_with_metadata",
        side_effect=[
            _fake_response("not valid json {{"),
            _fake_response(),
        ],
    ) as mock_llm:
        result = generate_folder_voiceover(
            project, "Grand Canyon", provider="anthropic", model="claude-sonnet-5"
        )
    assert result.status == STATUS_PASS
    assert result.draft is not None
    assert mock_llm.call_count == 2


def test_generate_invalid_json_does_not_overwrite_existing_draft(tmp_path: Path) -> None:
    project = _make_project(tmp_path, ["Grand Canyon"])
    with patch(f"{_SERVICE_MODULE}.generate_plan_text_with_metadata", return_value=_fake_response()):
        first = generate_folder_voiceover(project, "Grand Canyon", provider="anthropic", model="claude-sonnet-5")

    with patch(
        f"{_SERVICE_MODULE}.generate_plan_text_with_metadata",
        return_value=_fake_response("not valid json {{"),
    ) as mock_llm:
        second = generate_folder_voiceover(project, "Grand Canyon", provider="anthropic", model="claude-sonnet-5")

    assert second.status == STATUS_PARSE_FAILED
    assert second.draft is None
    assert mock_llm.call_count == 2

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


def test_update_folder_voiceover_text_with_unchanged_text_does_not_reset_confirmed_status(
    tmp_path: Path,
) -> None:
    """Nutzerfeedback: 'Wenn ich alle auf einmal bestätigen will kommt der
    Status Needs validation' — Ursache war, dass ein erneutes Speichern
    UNVERÄNDERTEN Texts (z. B. via 'Alle Texte speichern') eine bereits
    erteilte Bestätigung stillschweigend zurücksetzte. Identischer Text darf
    den Status NICHT verändern."""
    from otio_app.services.voiceover_generation.voiceover_review_service import (
        confirm_folder_voiceover,
    )

    project = _make_project(tmp_path, ["Grand Canyon"])
    with patch(f"{_SERVICE_MODULE}.generate_plan_text_with_metadata", return_value=_fake_response()):
        generate_folder_voiceover(project, "Grand Canyon", provider="anthropic", model="claude-sonnet-5")
    confirmed = confirm_folder_voiceover(project, "Grand Canyon")
    assert confirmed.status == "CONFIRMED"

    unchanged = update_folder_voiceover_text(
        project, "Grand Canyon", confirmed.voiceover_text_full
    )
    assert unchanged.status == "CONFIRMED"
    assert unchanged.confirmed_at == confirmed.confirmed_at

    reloaded = get_folder_voiceover_draft(project, "Grand Canyon")
    assert reloaded.status == "CONFIRMED"


def test_update_folder_voiceover_text_with_actually_changed_text_still_resets_status(
    tmp_path: Path,
) -> None:
    """Gegenprobe: eine ECHTE Textänderung setzt weiterhin auf
    NEEDS_VALIDATION zurück — auch wenn der Ordner vorher bestätigt war."""
    from otio_app.services.voiceover_generation.voiceover_review_service import (
        confirm_folder_voiceover,
    )

    project = _make_project(tmp_path, ["Grand Canyon"])
    with patch(f"{_SERVICE_MODULE}.generate_plan_text_with_metadata", return_value=_fake_response()):
        generate_folder_voiceover(project, "Grand Canyon", provider="anthropic", model="claude-sonnet-5")
    confirm_folder_voiceover(project, "Grand Canyon")

    changed = update_folder_voiceover_text(project, "Grand Canyon", "Ein wirklich anderer Text.")
    assert changed.status == "NEEDS_VALIDATION"
    assert changed.voiceover_text_full == "Ein wirklich anderer Text."


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


def test_build_inventory_asset_context_duration_cache_avoids_reprobing_same_path(
    tmp_path: Path,
) -> None:
    """Performance-Fix (Juli 2026): mit einem gemeinsamen duration_cache wird
    probe_duration_seconds für denselben Video-Pfad nur EINMAL aufgerufen,
    auch wenn build_inventory_asset_context mehrfach für denselben Ordner
    aufgerufen wird (z. B. einmal pro Rendering-Durchlauf einer Seite)."""
    project = _make_project(tmp_path, ["Grand Canyon"])
    duration_cache: dict[str, float | None] = {}
    with patch(f"{_SERVICE_MODULE}.probe_duration_seconds", return_value=5.0) as probe_mock:
        build_inventory_asset_context(project, "Grand Canyon", duration_cache=duration_cache)
        build_inventory_asset_context(project, "Grand Canyon", duration_cache=duration_cache)

    # Zwei Assets (clip1.mp4, clip2.mp4) -> genau zwei Aufrufe insgesamt,
    # nicht vier (zwei pro Aufruf von build_inventory_asset_context).
    assert probe_mock.call_count == 2
    assert set(duration_cache) == {
        "Grand Canyon/clip1.mp4",
        "Grand Canyon/clip2.mp4",
    }


def test_build_inventory_asset_context_without_duration_cache_reprobes_every_call(
    tmp_path: Path,
) -> None:
    """Gegenprobe: ohne duration_cache (Standardverhalten, unverändert für
    bestehende Aufrufer) wird bei jedem Aufruf neu vermessen."""
    project = _make_project(tmp_path, ["Grand Canyon"])
    with patch(f"{_SERVICE_MODULE}.probe_duration_seconds", return_value=5.0) as probe_mock:
        build_inventory_asset_context(project, "Grand Canyon")
        build_inventory_asset_context(project, "Grand Canyon")

    assert probe_mock.call_count == 4


def test_is_draft_stale_accepts_preloaded_documents_and_skips_reloading(tmp_path: Path) -> None:
    """Performance-Fix (Juli 2026): wenn project_brief/style_profile/plan/
    settings_doc bereits vorliegen (z. B. einmal pro Seiten-Rendering
    geladen), ruft is_draft_stale die jeweiligen load_*-Funktionen NICHT
    erneut auf."""
    project = _make_project(tmp_path, ["Grand Canyon"])
    with patch(f"{_SERVICE_MODULE}.generate_plan_text_with_metadata", return_value=_fake_response()):
        result = generate_folder_voiceover(project, "Grand Canyon", provider="anthropic", model="claude-sonnet-5")

    from otio_app.services.voiceover_generation.dramaturgy_service import load_confirmed_dramaturgy
    from otio_app.services.voiceover_generation.folder_voiceover_settings_service import (
        load_folder_voiceover_settings,
    )
    from otio_app.services.voiceover_generation.project_brief_service import load_project_brief
    from otio_app.services.voiceover_generation.style_profile_service import load_style_profile

    preloaded_brief = load_project_brief(project)
    preloaded_style_profile = load_style_profile(project)
    preloaded_plan = load_confirmed_dramaturgy(project)
    preloaded_settings_doc = load_folder_voiceover_settings(project)

    with (
        patch(f"{_SERVICE_MODULE}.load_project_brief") as brief_mock,
        patch(f"{_SERVICE_MODULE}.load_style_profile") as style_mock,
        patch(f"{_SERVICE_MODULE}.load_confirmed_dramaturgy") as plan_mock,
        patch(f"{_SERVICE_MODULE}.load_folder_voiceover_settings") as settings_mock,
    ):
        stale = is_draft_stale(
            project,
            "Grand Canyon",
            result.draft,
            project_brief=preloaded_brief,
            style_profile=preloaded_style_profile,
            plan=preloaded_plan,
            settings_doc=preloaded_settings_doc,
        )

    assert stale is False
    brief_mock.assert_not_called()
    style_mock.assert_not_called()
    plan_mock.assert_not_called()
    settings_mock.assert_not_called()


def test_is_draft_stale_without_preloaded_documents_still_loads_them(tmp_path: Path) -> None:
    """Gegenprobe: ohne Angabe (Standardverhalten, unverändert für
    bestehende Aufrufer) lädt is_draft_stale weiterhin selbst von der Platte."""
    project = _make_project(tmp_path, ["Grand Canyon"])
    with patch(f"{_SERVICE_MODULE}.generate_plan_text_with_metadata", return_value=_fake_response()):
        result = generate_folder_voiceover(project, "Grand Canyon", provider="anthropic", model="claude-sonnet-5")

    assert is_draft_stale(project, "Grand Canyon", result.draft) is False


def test_upsert_preserves_other_folders(tmp_path: Path) -> None:
    project = _make_project(tmp_path, ["Grand Canyon", "Yellowstone"])
    with patch(f"{_SERVICE_MODULE}.generate_plan_text_with_metadata", return_value=_fake_response()):
        generate_folder_voiceover(project, "Grand Canyon", provider="anthropic", model="claude-sonnet-5")
        generate_folder_voiceover(project, "Yellowstone", provider="anthropic", model="claude-sonnet-5")

    document = load_folder_voiceovers_draft(project)
    assert {item.folder_name for item in document.items} == {"Grand Canyon", "Yellowstone"}
