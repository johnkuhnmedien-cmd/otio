"""Phase 5: Intro-Hook-Service — Generierung, Asset-Validierung, Confirm."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from otio_app.analysis_models import AssetFolderAnalysis, AssetMediaAnalysis
from otio_app.defaults import (
    VO_ERROR_INVALID_ASSET_ID,
    VO_ERROR_INVALID_FOLDER_REFERENCE,
    VO_ERROR_INVALID_SENTENCE_REFERENCE,
    VO_ERROR_MISSING_ASSET_MAPPING,
    VO_ERROR_MISSING_SUPPLEMENT_REASON,
)
from otio_app.models import Project, ProjectMode
from otio_app.project_layout import (
    get_folder_inventory_path,
    get_intro_hook_candidates_path,
    get_intro_hook_confirmed_path,
    get_llm_runs_dir,
)
from otio_app.services.plan_llm_client import PlanLlmNotConfiguredError, PlanLlmResponse
from otio_app.services.voiceover_generation.dramaturgy_service import save_confirmed_dramaturgy
from otio_app.services.voiceover_generation.intro_hook_service import (
    build_intro_hook_candidates,
    confirm_intro_hook,
    load_confirmed_intro_hook,
    load_intro_hook_candidates,
    regenerate_intro_hook_candidates,
    unconfirm_intro_hook,
    validate_intro_hook_candidate,
)
from otio_app.services.voiceover_generation.intro_hook_settings_service import (
    load_intro_hook_settings,
    save_intro_hook_settings,
)
from otio_app.services.voiceover_generation.llm_trace_service import STATUS_FAIL, STATUS_PARSE_FAILED, STATUS_PASS
from otio_app.services.voiceover_generation.models import (
    DramaturgyFolderEntry,
    DramaturgyPlan,
    IntroHookCandidate,
    IntroHookVisualBeat,
)
from otio_app.services.voiceover_generation.voiceover_author_service import (
    generate_folder_voiceover,
)
from otio_app.services.voiceover_generation.voiceover_review_service import confirm_folder_voiceover
from otio_app.services.voiceover_generation.folder_voiceover_settings_service import (
    build_default_folder_voiceover_settings,
    save_folder_voiceover_settings,
)

_AUTHOR_MODULE = "otio_app.services.voiceover_generation.voiceover_author_service"
_INTRO_MODULE = "otio_app.services.voiceover_generation.intro_hook_service"


def _make_project_with_confirmed_folder_voiceovers(tmp_path: Path) -> Project:
    project_root = tmp_path / "USA"
    project_root.mkdir()
    folders = ["Grand Canyon", "Yellowstone"]
    for folder in folders:
        (project_root / folder).mkdir()

    project = Project(
        id="intro-project",
        name="Intro Test",
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
            assets=[AssetMediaAnalysis(path=f"{folder}/clip1.mp4", description=f"Aufnahme von {folder}.")],
        )
        path.write_text(analysis.model_dump_json(indent=2), encoding="utf-8")

    plan = DramaturgyPlan(
        project_id=project.id,
        recommended_folder_order=[
            DramaturgyFolderEntry(
                folder_name=folder, order_index=index, enabled=True,
                recommended_word_count=100, recommended_min_words=90, recommended_max_words=110,
            )
            for index, folder in enumerate(folders, start=1)
        ],
    )
    save_confirmed_dramaturgy(project, plan)
    save_folder_voiceover_settings(project, build_default_folder_voiceover_settings(project))

    author_response = json.dumps(
        {
            "voiceover_text_full": "Zwischen den Felswänden scheint das Licht von innen zu leuchten heute.",
            "sentence_items": [
                {
                    "sentence_id": "sentence_001",
                    "text": "Zwischen den Felswänden scheint das Licht von innen zu leuchten heute.",
                    "primary_asset_id": "asset_clip1",
                    "asset_confidence": 0.9,
                }
            ],
        }
    )
    fake_response = PlanLlmResponse(provider="anthropic", model="claude-sonnet-5", raw_text=author_response)
    with patch(f"{_AUTHOR_MODULE}.generate_plan_text_with_metadata", return_value=fake_response):
        for folder in folders:
            generate_folder_voiceover(project, folder, provider="anthropic", model="claude-sonnet-5")
            confirm_folder_voiceover(project, folder)

    return project


VALID_INTRO_RESPONSE = json.dumps(
    {
        "candidates": [
            {
                "hook_id": f"hook_{i:03d}",
                "hook_text": f"Ein Ort voller Geheimnisse wartet Nummer {i} auf jeden mutigen Reisenden heute schon lange.",
                "hook_type": "mystery",
                "used_folders": ["Grand Canyon"],
                "used_sentence_ids": ["sentence_001"],
                "visual_beats": [
                    {
                        "hook_beat_id": f"hook_beat_{i:03d}",
                        "text": "Ein Ort voller Geheimnisse.",
                        "visual_intent": "establishing",
                        "source_folder_name": "Grand Canyon",
                        "source_sentence_id": "sentence_001",
                        "primary_asset_id": "asset_clip1",
                        "backup_asset_ids": [],
                        "asset_match_reason": "Passt zur Einleitung.",
                        "asset_confidence": 0.85,
                        "needs_supplement_asset": False,
                        "supplement_reason": "",
                    }
                ],
                "hook_potential_score": 0.8,
                "reason": "Starker Einstieg.",
                "risks": [],
            }
            for i in range(1, 6)
        ]
    }
)


def _fake_response(raw_text: str = VALID_INTRO_RESPONSE) -> PlanLlmResponse:
    return PlanLlmResponse(provider="anthropic", model="claude-sonnet-5", raw_text=raw_text)


def test_build_writes_intro_hook_candidates_json(tmp_path: Path) -> None:
    project = _make_project_with_confirmed_folder_voiceovers(tmp_path)
    with patch(f"{_INTRO_MODULE}.generate_plan_text_with_metadata", return_value=_fake_response()):
        result = build_intro_hook_candidates(project, provider="anthropic", model="claude-sonnet-5")

    assert result.status == STATUS_PASS
    assert result.document is not None
    assert len(result.document.candidates) == 5
    path = get_intro_hook_candidates_path(project.language_work_dir_path)
    assert path.is_file()


def test_candidates_contain_llm_run_id(tmp_path: Path) -> None:
    project = _make_project_with_confirmed_folder_voiceovers(tmp_path)
    with patch(f"{_INTRO_MODULE}.generate_plan_text_with_metadata", return_value=_fake_response()):
        result = build_intro_hook_candidates(project, provider="anthropic", model="claude-sonnet-5")

    assert result.document.llm_run_id == result.llm_run_id
    run_dir = get_llm_runs_dir(project.language_work_dir_path) / result.llm_run_id
    assert (run_dir / "prompt.txt").is_file()
    assert (run_dir / "raw_llm_response.json").is_file()
    assert (run_dir / "parsed_llm_response.json").is_file()
    manifest = json.loads((run_dir / "llm_request_manifest.json").read_text(encoding="utf-8"))
    assert manifest["stage"] == "intro_hook"


def test_invalid_json_returns_parse_failed_and_keeps_existing_candidates(tmp_path: Path) -> None:
    project = _make_project_with_confirmed_folder_voiceovers(tmp_path)
    with patch(f"{_INTRO_MODULE}.generate_plan_text_with_metadata", return_value=_fake_response()):
        first = build_intro_hook_candidates(project, provider="anthropic", model="claude-sonnet-5")

    with patch(
        f"{_INTRO_MODULE}.generate_plan_text_with_metadata",
        return_value=_fake_response("not valid json {{"),
    ):
        second = build_intro_hook_candidates(project, provider="anthropic", model="claude-sonnet-5")

    assert second.status == STATUS_PARSE_FAILED
    assert second.document is None

    loaded = load_intro_hook_candidates(project)
    assert loaded.llm_run_id == first.llm_run_id


def test_missing_api_key_returns_fail(tmp_path: Path) -> None:
    project = _make_project_with_confirmed_folder_voiceovers(tmp_path)
    with patch(
        f"{_INTRO_MODULE}.generate_plan_text_with_metadata",
        side_effect=PlanLlmNotConfiguredError("ANTHROPIC_API_KEY ist nicht gesetzt."),
    ):
        result = build_intro_hook_candidates(project, provider="anthropic", model="claude-sonnet-5")
    assert result.status == STATUS_FAIL
    assert result.document is None


def test_generic_llm_exception_returns_fail_status(tmp_path: Path) -> None:
    """Jeder unerwartete LLM-/SDK-/Netzwerkfehler soll als kontrollierter FAIL
    zurückkommen statt die Streamlit-Seite crashen zu lassen."""
    project = _make_project_with_confirmed_folder_voiceovers(tmp_path)
    with patch(
        f"{_INTRO_MODULE}.generate_plan_text_with_metadata",
        side_effect=RuntimeError("Unerwarteter SDK-Fehler."),
    ):
        result = build_intro_hook_candidates(project, provider="anthropic", model="claude-sonnet-5")
    assert result.status == STATUS_FAIL
    assert result.document is None


def test_build_raises_when_folders_not_all_confirmed(tmp_path: Path) -> None:
    project_root = tmp_path / "USA"
    project_root.mkdir()
    (project_root / "Grand Canyon").mkdir()
    project = Project(
        id="incomplete-project",
        name="Incomplete",
        project_root=str(project_root),
        work_dir=str(project_root / "_otio"),
        project_mode=ProjectMode.WITHOUT_VOICEOVER,
        asset_subdir_names=["Grand Canyon"],
        selected_asset_subdirs=["Grand Canyon"],
    )
    plan = DramaturgyPlan(
        project_id=project.id,
        recommended_folder_order=[
            DramaturgyFolderEntry(folder_name="Grand Canyon", order_index=1, enabled=True)
        ],
    )
    save_confirmed_dramaturgy(project, plan)
    with pytest.raises(ValueError):
        build_intro_hook_candidates(project, provider="anthropic", model="claude-sonnet-5")


def _sample_settings():
    from otio_app.services.voiceover_generation.models import IntroHookSettings

    return IntroHookSettings(project_id="p1", target_words=70, min_words=60, max_words=80)


def test_validate_detects_invalid_asset_id() -> None:
    candidate = IntroHookCandidate(
        hook_id="hook_001",
        hook_text="x " * 65,
        word_count=65,
        visual_beats=[
            IntroHookVisualBeat(hook_beat_id="b1", primary_asset_id="asset_does_not_exist")
        ],
    )
    risks = validate_intro_hook_candidate(
        candidate,
        confirmed_folder_names={"Grand Canyon"},
        valid_sentence_ids_by_folder={"Grand Canyon": {"sentence_001"}},
        valid_asset_ids={"asset_clip1"},
        settings=_sample_settings(),
    )
    assert any(risk.startswith(VO_ERROR_INVALID_ASSET_ID) for risk in risks)


def test_validate_detects_invalid_folder_reference() -> None:
    candidate = IntroHookCandidate(
        hook_id="hook_001",
        hook_text="x " * 65,
        word_count=65,
        used_folders=["Nonexistent Folder"],
    )
    risks = validate_intro_hook_candidate(
        candidate,
        confirmed_folder_names={"Grand Canyon"},
        valid_sentence_ids_by_folder={"Grand Canyon": {"sentence_001"}},
        valid_asset_ids={"asset_clip1"},
        settings=_sample_settings(),
    )
    assert any(risk.startswith(VO_ERROR_INVALID_FOLDER_REFERENCE) for risk in risks)


def test_validate_detects_invalid_sentence_reference() -> None:
    candidate = IntroHookCandidate(
        hook_id="hook_001",
        hook_text="x " * 65,
        word_count=65,
        used_sentence_ids=["sentence_999"],
    )
    risks = validate_intro_hook_candidate(
        candidate,
        confirmed_folder_names={"Grand Canyon"},
        valid_sentence_ids_by_folder={"Grand Canyon": {"sentence_001"}},
        valid_asset_ids={"asset_clip1"},
        settings=_sample_settings(),
    )
    assert any(risk.startswith(VO_ERROR_INVALID_SENTENCE_REFERENCE) for risk in risks)


def test_validate_detects_missing_asset_mapping() -> None:
    candidate = IntroHookCandidate(
        hook_id="hook_001",
        hook_text="x " * 65,
        word_count=65,
        visual_beats=[
            IntroHookVisualBeat(hook_beat_id="b1", primary_asset_id="", needs_supplement_asset=False)
        ],
    )
    risks = validate_intro_hook_candidate(
        candidate,
        confirmed_folder_names={"Grand Canyon"},
        valid_sentence_ids_by_folder={"Grand Canyon": {"sentence_001"}},
        valid_asset_ids={"asset_clip1"},
        settings=_sample_settings(),
    )
    assert any(risk.startswith(VO_ERROR_MISSING_ASSET_MAPPING) for risk in risks)


def test_validate_detects_missing_supplement_reason() -> None:
    candidate = IntroHookCandidate(
        hook_id="hook_001",
        hook_text="x " * 65,
        word_count=65,
        visual_beats=[
            IntroHookVisualBeat(
                hook_beat_id="b1", primary_asset_id="", needs_supplement_asset=True, supplement_reason=""
            )
        ],
    )
    risks = validate_intro_hook_candidate(
        candidate,
        confirmed_folder_names={"Grand Canyon"},
        valid_sentence_ids_by_folder={"Grand Canyon": {"sentence_001"}},
        valid_asset_ids={"asset_clip1"},
        settings=_sample_settings(),
    )
    assert any(risk.startswith(VO_ERROR_MISSING_SUPPLEMENT_REASON) for risk in risks)


def test_candidate_count_mismatch_is_documented_as_risk(tmp_path: Path) -> None:
    """Bewusste Entscheidung (§12.18): Bei != 5 Kandidaten wird trotzdem
    gespeichert (kein harter Abbruch), aber mit einem dokumentierten
    Hinweis auf Dokument-Ebene."""
    project = _make_project_with_confirmed_folder_voiceovers(tmp_path)
    payload = json.loads(VALID_INTRO_RESPONSE)
    payload["candidates"] = payload["candidates"][:3]
    with patch(f"{_INTRO_MODULE}.generate_plan_text_with_metadata", return_value=_fake_response(json.dumps(payload))):
        result = build_intro_hook_candidates(project, provider="anthropic", model="claude-sonnet-5")

    assert result.status == STATUS_PASS
    assert len(result.document.candidates) == 3
    assert any("CANDIDATE_COUNT_MISMATCH" in risk for risk in result.document.risks)


def test_zero_candidates_does_not_overwrite_existing_and_returns_fail(tmp_path: Path) -> None:
    project = _make_project_with_confirmed_folder_voiceovers(tmp_path)
    with patch(f"{_INTRO_MODULE}.generate_plan_text_with_metadata", return_value=_fake_response()):
        first = build_intro_hook_candidates(project, provider="anthropic", model="claude-sonnet-5")

    with patch(
        f"{_INTRO_MODULE}.generate_plan_text_with_metadata",
        return_value=_fake_response(json.dumps({"candidates": []})),
    ):
        second = build_intro_hook_candidates(project, provider="anthropic", model="claude-sonnet-5")

    assert second.status == STATUS_FAIL
    assert second.document is None
    loaded = load_intro_hook_candidates(project)
    assert loaded.llm_run_id == first.llm_run_id


def test_confirm_intro_hook_writes_confirmed_file(tmp_path: Path) -> None:
    project = _make_project_with_confirmed_folder_voiceovers(tmp_path)
    with patch(f"{_INTRO_MODULE}.generate_plan_text_with_metadata", return_value=_fake_response()):
        build_intro_hook_candidates(project, provider="anthropic", model="claude-sonnet-5")

    confirmed = confirm_intro_hook(project, "hook_001")
    assert confirmed.hook_id == "hook_001"
    path = get_intro_hook_confirmed_path(project.language_work_dir_path)
    assert path.is_file()

    loaded = load_confirmed_intro_hook(project)
    assert loaded is not None
    assert loaded.hook_id == "hook_001"
    assert loaded.status == "CONFIRMED"


def test_confirm_can_use_edited_text(tmp_path: Path) -> None:
    project = _make_project_with_confirmed_folder_voiceovers(tmp_path)
    with patch(f"{_INTRO_MODULE}.generate_plan_text_with_metadata", return_value=_fake_response()):
        build_intro_hook_candidates(project, provider="anthropic", model="claude-sonnet-5")

    confirmed = confirm_intro_hook(project, "hook_001", edited_hook_text="Ein komplett neuer Hook-Text hier.")
    assert confirmed.hook_text == "Ein komplett neuer Hook-Text hier."
    assert confirmed.word_count == 5


def test_regenerate_does_not_overwrite_confirmed_hook(tmp_path: Path) -> None:
    project = _make_project_with_confirmed_folder_voiceovers(tmp_path)
    with patch(f"{_INTRO_MODULE}.generate_plan_text_with_metadata", return_value=_fake_response()):
        build_intro_hook_candidates(project, provider="anthropic", model="claude-sonnet-5")
    confirm_intro_hook(project, "hook_001")

    with patch(f"{_INTRO_MODULE}.generate_plan_text_with_metadata", return_value=_fake_response()):
        regenerate_intro_hook_candidates(project, provider="anthropic", model="claude-sonnet-5")

    confirmed = load_confirmed_intro_hook(project)
    assert confirmed is not None
    assert confirmed.hook_id == "hook_001"  # unverändert trotz Neu-Generierung


def test_unconfirm_removes_confirmed_hook(tmp_path: Path) -> None:
    project = _make_project_with_confirmed_folder_voiceovers(tmp_path)
    with patch(f"{_INTRO_MODULE}.generate_plan_text_with_metadata", return_value=_fake_response()):
        build_intro_hook_candidates(project, provider="anthropic", model="claude-sonnet-5")
    confirm_intro_hook(project, "hook_001")
    unconfirm_intro_hook(project)

    assert load_confirmed_intro_hook(project) is None
    assert not get_intro_hook_confirmed_path(project.language_work_dir_path).is_file()


def test_no_edit_plan_documents_created(tmp_path: Path) -> None:
    project = _make_project_with_confirmed_folder_voiceovers(tmp_path)
    with patch(f"{_INTRO_MODULE}.generate_plan_text_with_metadata", return_value=_fake_response()):
        build_intro_hook_candidates(project, provider="anthropic", model="claude-sonnet-5")
    confirm_intro_hook(project, "hook_001")

    assert not (project.language_work_dir_path / "edit_plan").exists()
    assert not (project.language_work_dir_path / "exports").exists()


def test_intro_settings_used_for_language_and_word_counts(tmp_path: Path) -> None:
    project = _make_project_with_confirmed_folder_voiceovers(tmp_path)
    settings = load_intro_hook_settings(project)
    save_intro_hook_settings(project, settings.model_copy(update={"target_words": 55, "min_words": 45, "max_words": 65}))

    with patch(f"{_INTRO_MODULE}.generate_plan_text_with_metadata", return_value=_fake_response()):
        result = build_intro_hook_candidates(project, provider="anthropic", model="claude-sonnet-5")

    assert result.document.target_words == 55
    assert result.document.min_words == 45
    assert result.document.max_words == 65


def test_no_api_key_leak_in_trace_files(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    project = _make_project_with_confirmed_folder_voiceovers(tmp_path)
    secret_key = "sk-ant-SECRET-INTRO-DO-NOT-LEAK"
    monkeypatch.setenv("ANTHROPIC_API_KEY", secret_key)

    from unittest.mock import MagicMock

    block = MagicMock(type="text", text=VALID_INTRO_RESPONSE)
    mock_response = MagicMock(content=[block], usage=MagicMock(input_tokens=10, output_tokens=20))

    with patch("anthropic.Anthropic") as mock_anthropic:
        mock_anthropic.return_value.messages.create.return_value = mock_response
        result = build_intro_hook_candidates(project, provider="anthropic", model="claude-sonnet-5")

    assert result.status == STATUS_PASS
    run_dir = get_llm_runs_dir(project.language_work_dir_path) / result.llm_run_id
    for path in run_dir.rglob("*"):
        if path.is_file():
            assert secret_key not in path.read_text(encoding="utf-8"), f"API-Key geleakt in {path}"
    candidates_path = get_intro_hook_candidates_path(project.language_work_dir_path)
    assert secret_key not in candidates_path.read_text(encoding="utf-8")
