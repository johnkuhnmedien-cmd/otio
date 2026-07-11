"""Phase 7: Final Output — final_plan_service.py (Plan-Aggregation, Readiness, Exporte)."""

from __future__ import annotations

import csv
import io
import json
from pathlib import Path
from unittest.mock import patch

from otio_app.analysis_models import AssetFolderAnalysis, AssetMediaAnalysis
from otio_app.defaults import (
    PLAN_STATUS_AUDIO_PENDING,
    PLAN_STATUS_AUDIO_READY,
    PLAN_STATUS_NEEDS_REVIEW,
    PLAN_STATUS_READY_FOR_CUT,
    PLAN_STATUS_TEXT_READY,
    VO_ERROR_MISSING_ASSET_MAPPING,
    VO_ERROR_MISSING_SUPPLEMENT_REASON,
)
from otio_app.models import Project, ProjectMode
from otio_app.project_layout import (
    get_confirmed_voiceover_project_plan_path,
    get_folder_inventory_path,
    get_voiceover_project_plan_csv_path,
    get_voiceover_project_plan_json_path,
    get_voiceover_project_plan_md_path,
)
from otio_app.services.plan_llm_client import PlanLlmResponse
from otio_app.services.voiceover_generation.dramaturgy_service import save_confirmed_dramaturgy
from otio_app.services.voiceover_generation.elevenlabs_settings_service import save_elevenlabs_settings
from otio_app.services.voiceover_generation.final_plan_service import (
    build_confirmed_voiceover_project_plan,
    export_voiceover_project_plan_csv,
    export_voiceover_project_plan_json,
    export_voiceover_project_plan_markdown,
    is_project_plan_stale,
    load_confirmed_voiceover_project_plan,
    save_confirmed_voiceover_project_plan,
)
from otio_app.services.voiceover_generation.folder_voiceover_settings_service import (
    build_default_folder_voiceover_settings,
    save_folder_voiceover_settings,
)
from otio_app.services.voiceover_generation.intro_hook_service import (
    confirm_intro_hook,
    save_intro_hook_candidates,
)
from otio_app.services.voiceover_generation.models import (
    DramaturgyFolderEntry,
    DramaturgyPlan,
    ElevenLabsSettings,
    IntroHookCandidate,
    IntroHookCandidatesDocument,
    IntroHookVisualBeat,
)
from otio_app.services.voiceover_generation.project_brief_service import save_project_brief
from otio_app.services.voiceover_generation.models import ProjectBrief
from otio_app.services.voiceover_generation.tts_orchestration_service import (
    synthesize_folder_voiceover,
    synthesize_intro,
)
from otio_app.services.voiceover_generation.voiceover_author_service import generate_folder_voiceover
from otio_app.services.voiceover_generation.voiceover_review_service import confirm_folder_voiceover

_AUTHOR_MODULE = "otio_app.services.voiceover_generation.voiceover_author_service"
_TTS_MODULE = "otio_app.services.voiceover_generation.tts_orchestration_service"

FOLDER_NAME = "Grand Canyon"


def _fake_tts_result(audio_bytes: bytes = b"FAKE_AUDIO"):
    from otio_app.services.voiceover_generation.elevenlabs_client import ElevenLabsTtsResult

    return ElevenLabsTtsResult(
        audio_bytes=audio_bytes,
        alignment={
            "characters": list("Zwischen den Felswaenden scheint das Licht von innen zu leuchten heute."),
            "character_start_times_seconds": [
                i * 0.05
                for i in range(len("Zwischen den Felswaenden scheint das Licht von innen zu leuchten heute."))
            ],
            "character_end_times_seconds": [
                (i + 1) * 0.05
                for i in range(len("Zwischen den Felswaenden scheint das Licht von innen zu leuchten heute."))
            ],
        },
        normalized_alignment={},
        response_metadata={"status_code": 200},
    )


def _make_base_project(tmp_path: Path) -> Project:
    project_root = tmp_path / "USA"
    project_root.mkdir()
    (project_root / FOLDER_NAME).mkdir()

    project = Project(
        id="final-plan-project",
        name="Final Plan Test",
        project_root=str(project_root),
        work_dir=str(project_root / "_otio"),
        project_mode=ProjectMode.WITHOUT_VOICEOVER,
        asset_subdir_names=[FOLDER_NAME],
        selected_asset_subdirs=[FOLDER_NAME],
    )
    path = get_folder_inventory_path(project.work_dir_path, FOLDER_NAME)
    path.parent.mkdir(parents=True, exist_ok=True)
    analysis = AssetFolderAnalysis(
        folder=FOLDER_NAME,
        assets=[AssetMediaAnalysis(path=f"{FOLDER_NAME}/clip1.mp4", description="Aufnahme der Schlucht.")],
    )
    path.write_text(analysis.model_dump_json(indent=2), encoding="utf-8")

    save_project_brief(project, ProjectBrief(project_id=project.id, video_title="Wunder der Wüste", language="DE"))

    plan = DramaturgyPlan(
        project_id=project.id,
        project_title="Wunder der Wüste",
        recommended_folder_order=[
            DramaturgyFolderEntry(
                folder_name=FOLDER_NAME,
                order_index=1,
                enabled=True,
                dramaturgy_role="opener",
                recommended_word_count=100,
                recommended_min_words=90,
                recommended_max_words=110,
            )
        ],
    )
    save_confirmed_dramaturgy(project, plan)
    save_folder_voiceover_settings(project, build_default_folder_voiceover_settings(project))
    save_elevenlabs_settings(
        project, ElevenLabsSettings(project_id=project.id, voice_id="voice-abc", model_id="eleven_multilingual_v2")
    )
    return project


def _confirm_folder_voiceover(project: Project) -> None:
    author_response = json.dumps(
        {
            "voiceover_text_full": "Zwischen den Felswaenden scheint das Licht von innen zu leuchten heute.",
            "sentence_items": [
                {
                    "sentence_id": "sentence_001",
                    "beat_id": "beat_001",
                    "text": "Zwischen den Felswaenden scheint das Licht von innen zu leuchten heute.",
                    "visual_intent": "establishing",
                    "primary_asset_id": "asset_clip1",
                    "backup_asset_ids": [],
                    "asset_confidence": 0.9,
                    "needs_supplement_asset": False,
                    "supplement_reason": "",
                }
            ],
        }
    )
    fake_response = PlanLlmResponse(provider="anthropic", model="claude-sonnet-5", raw_text=author_response)
    with patch(f"{_AUTHOR_MODULE}.generate_plan_text_with_metadata", return_value=fake_response):
        generate_folder_voiceover(project, FOLDER_NAME, provider="anthropic", model="claude-sonnet-5")
    confirm_folder_voiceover(project, FOLDER_NAME)


def _confirm_intro(project: Project, *, with_asset_mapping: bool = True) -> None:
    visual_beats = (
        [
            IntroHookVisualBeat(
                hook_beat_id="hook_beat_001",
                text="Ein Ort voller Geheimnisse.",
                primary_asset_id="asset_clip1" if with_asset_mapping else "",
                needs_supplement_asset=not with_asset_mapping,
                supplement_reason="Kein passendes Motiv gefunden." if not with_asset_mapping else "",
                asset_confidence=0.8,
            )
        ]
    )
    candidates_doc = IntroHookCandidatesDocument(
        project_id=project.id,
        candidates=[
            IntroHookCandidate(
                hook_id="hook_001",
                hook_text="Ein Ort voller Geheimnisse wartet auf jeden mutigen Reisenden heute schon lange.",
                hook_type="mystery",
                used_folders=[FOLDER_NAME],
                visual_beats=visual_beats,
            )
        ],
        llm_run_id="fake-run",
    )
    save_intro_hook_candidates(project, candidates_doc)
    confirm_intro_hook(project, "hook_001")


def _synthesize_everything(project: Project) -> None:
    with (
        patch(f"{_TTS_MODULE}.synthesize_speech_with_timestamps", return_value=_fake_tts_result()),
        patch(f"{_TTS_MODULE}.probe_duration_seconds", return_value=5.0),
    ):
        synthesize_intro(project)
        synthesize_folder_voiceover(project, FOLDER_NAME)


def _make_fully_ready_project(tmp_path: Path) -> Project:
    project = _make_base_project(tmp_path)
    _confirm_folder_voiceover(project)
    _confirm_intro(project)
    _synthesize_everything(project)
    return project


# --- Plan-Aggregation ---


def test_plan_built_from_confirmed_artifacts(tmp_path: Path) -> None:
    project = _make_fully_ready_project(tmp_path)
    plan = build_confirmed_voiceover_project_plan(project)
    assert plan.project_title == "Wunder der Wüste"
    assert len(plan.folders) == 1
    assert plan.folders[0].folder_name == FOLDER_NAME


def test_plan_contains_intro_hook_text(tmp_path: Path) -> None:
    project = _make_fully_ready_project(tmp_path)
    plan = build_confirmed_voiceover_project_plan(project)
    assert "Ein Ort voller Geheimnisse" in plan.intro.hook_text


def test_plan_folder_carries_closing_visual_plan_from_draft(tmp_path: Path) -> None:
    """Nutzervorgabe (Juli 2026): ClosingVisualPlan muss vom bestätigten
    Folder-Voice-over-Draft in den ConfirmedFolderPlanItem übernommen
    werden — Grundlage für das Cut-Plan-Wiring."""
    project = _make_base_project(tmp_path)
    author_response = json.dumps(
        {
            "voiceover_text_full": "Zwischen den Felswaenden scheint das Licht von innen zu leuchten heute.",
            "sentence_items": [
                {
                    "sentence_id": "sentence_001",
                    "text": "Zwischen den Felswaenden scheint das Licht von innen zu leuchten heute.",
                    "primary_asset_id": "asset_clip1",
                }
            ],
            "closing_visual_plan": {
                "visual_intent": "aerial establishing",
                "primary_asset_id": "asset_clip1",
                "asset_strategy_reason": "Ruhiger Abschluss.",
            },
        }
    )
    fake_response = PlanLlmResponse(provider="anthropic", model="claude-sonnet-5", raw_text=author_response)
    with patch(f"{_AUTHOR_MODULE}.generate_plan_text_with_metadata", return_value=fake_response):
        generate_folder_voiceover(project, FOLDER_NAME, provider="anthropic", model="claude-sonnet-5")
    confirm_folder_voiceover(project, FOLDER_NAME)
    _confirm_intro(project)
    _synthesize_everything(project)

    plan = build_confirmed_voiceover_project_plan(project)
    assert plan.folders[0].closing_visual_plan.primary_asset_id == "asset_clip1"
    assert plan.folders[0].closing_visual_plan.visual_intent == "aerial establishing"


def test_plan_folders_in_confirmed_order(tmp_path: Path) -> None:
    project = _make_fully_ready_project(tmp_path)
    plan = build_confirmed_voiceover_project_plan(project)
    assert [f.order_index for f in plan.folders] == [1]
    assert [f.folder_name for f in plan.folders] == [FOLDER_NAME]


def test_plan_contains_sentence_items(tmp_path: Path) -> None:
    project = _make_fully_ready_project(tmp_path)
    plan = build_confirmed_voiceover_project_plan(project)
    assert len(plan.folders[0].sentence_items) == 1
    assert plan.folders[0].sentence_items[0].sentence_id == "sentence_001"


def test_plan_contains_primary_and_backup_asset_ids(tmp_path: Path) -> None:
    project = _make_fully_ready_project(tmp_path)
    plan = build_confirmed_voiceover_project_plan(project)
    sentence = plan.folders[0].sentence_items[0]
    assert sentence.primary_asset_id == "asset_clip1"
    assert sentence.backup_asset_ids == []


def test_plan_contains_needs_supplement_and_reason(tmp_path: Path) -> None:
    project = _make_base_project(tmp_path)
    _confirm_folder_voiceover(project)
    _confirm_intro(project, with_asset_mapping=False)
    _synthesize_everything(project)
    plan = build_confirmed_voiceover_project_plan(project)
    beat = plan.intro.visual_beats[0]
    assert beat.needs_supplement_asset is True
    assert beat.supplement_reason == "Kein passendes Motiv gefunden."


def test_plan_contains_audio_paths_from_manifest(tmp_path: Path) -> None:
    project = _make_fully_ready_project(tmp_path)
    plan = build_confirmed_voiceover_project_plan(project)
    assert plan.folders[0].audio_path
    assert Path(plan.folders[0].audio_path).is_file()


def test_plan_contains_alignment_items_with_start_and_end(tmp_path: Path) -> None:
    project = _make_fully_ready_project(tmp_path)
    plan = build_confirmed_voiceover_project_plan(project)
    alignment_item = plan.folders[0].alignment_items[0]
    assert alignment_item.audio_end_sec > alignment_item.audio_start_sec >= 0


def test_plan_contains_intro_visual_beats(tmp_path: Path) -> None:
    project = _make_fully_ready_project(tmp_path)
    plan = build_confirmed_voiceover_project_plan(project)
    assert len(plan.intro.visual_beats) == 1
    assert plan.intro.visual_beats[0].hook_beat_id == "hook_beat_001"


def test_plan_contains_intro_alignment(tmp_path: Path) -> None:
    project = _make_fully_ready_project(tmp_path)
    plan = build_confirmed_voiceover_project_plan(project)
    assert len(plan.intro.alignment_items) == 1


# --- Readiness / Status ---


def test_ready_for_cut_only_when_audio_and_alignment_complete(tmp_path: Path) -> None:
    project = _make_fully_ready_project(tmp_path)
    plan = build_confirmed_voiceover_project_plan(project)
    assert plan.status == PLAN_STATUS_READY_FOR_CUT
    assert plan.readiness.all_required_audio_ready is True
    assert plan.readiness.all_alignments_ready is True


def test_missing_audio_is_not_ready_for_cut(tmp_path: Path) -> None:
    project = _make_base_project(tmp_path)
    _confirm_folder_voiceover(project)
    _confirm_intro(project)
    # Kein Audio erzeugt.
    plan = build_confirmed_voiceover_project_plan(project)
    assert plan.status in (PLAN_STATUS_TEXT_READY, PLAN_STATUS_AUDIO_PENDING, PLAN_STATUS_NEEDS_REVIEW)
    assert plan.status != PLAN_STATUS_READY_FOR_CUT


def test_partial_audio_is_audio_pending(tmp_path: Path) -> None:
    project = _make_base_project(tmp_path)
    _confirm_folder_voiceover(project)
    _confirm_intro(project)
    # Nur Intro vertont, Ordner nicht.
    with (
        patch(f"{_TTS_MODULE}.synthesize_speech_with_timestamps", return_value=_fake_tts_result()),
        patch(f"{_TTS_MODULE}.probe_duration_seconds", return_value=5.0),
    ):
        synthesize_intro(project)
    plan = build_confirmed_voiceover_project_plan(project)
    assert plan.status == PLAN_STATUS_AUDIO_PENDING
    assert plan.status != PLAN_STATUS_READY_FOR_CUT


def test_stale_audio_prevents_ready_for_cut(tmp_path: Path) -> None:
    project = _make_fully_ready_project(tmp_path)
    from otio_app.services.voiceover_generation.tts_orchestration_service import (
        load_audio_manifest,
        save_audio_manifest,
    )

    manifest = load_audio_manifest(project)
    updated_items = [
        item.model_copy(update={"status": "STALE"}) if item.folder_name == FOLDER_NAME else item
        for item in manifest.items
    ]
    save_audio_manifest(project, manifest.model_copy(update={"items": updated_items}))

    plan = build_confirmed_voiceover_project_plan(project)
    assert plan.status != PLAN_STATUS_READY_FOR_CUT
    assert any(error.type == "AUDIO_STALE" for error in plan.blockers)


def test_failed_audio_prevents_ready_for_cut(tmp_path: Path) -> None:
    project = _make_fully_ready_project(tmp_path)
    from otio_app.services.voiceover_generation.tts_orchestration_service import (
        load_audio_manifest,
        save_audio_manifest,
    )

    manifest = load_audio_manifest(project)
    updated_items = [
        item.model_copy(update={"status": "FAILED"}) if item.folder_name == FOLDER_NAME else item
        for item in manifest.items
    ]
    save_audio_manifest(project, manifest.model_copy(update={"items": updated_items}))

    plan = build_confirmed_voiceover_project_plan(project)
    assert plan.status != PLAN_STATUS_READY_FOR_CUT
    assert any(error.type == "AUDIO_FAILED" for error in plan.blockers)


def test_audio_ready_with_warnings_prevents_ready_for_cut_and_warns(tmp_path: Path) -> None:
    """Bewusste Entscheidung (§13.15): AUDIO_READY_WITH_WARNINGS blockiert
    READY_FOR_CUT (fehlende Dauer wäre für den Schnitt riskant) UND erzeugt
    eine sichtbare Warnung — kein harter BLOCKER, da das Audio technisch da ist."""
    project = _make_base_project(tmp_path)
    _confirm_folder_voiceover(project)
    _confirm_intro(project)
    with (
        patch(f"{_TTS_MODULE}.synthesize_speech_with_timestamps", return_value=_fake_tts_result()),
        patch(f"{_TTS_MODULE}.probe_duration_seconds", return_value=5.0),
    ):
        synthesize_intro(project)
    with (
        patch(f"{_TTS_MODULE}.synthesize_speech_with_timestamps", return_value=_fake_tts_result()),
        patch(f"{_TTS_MODULE}.probe_duration_seconds", return_value=None),
    ):
        synthesize_folder_voiceover(project, FOLDER_NAME)

    plan = build_confirmed_voiceover_project_plan(project)
    assert plan.status == PLAN_STATUS_AUDIO_READY
    assert plan.status != PLAN_STATUS_READY_FOR_CUT
    assert any(error.type == "AUDIO_DURATION_MISSING" for error in plan.warnings)


def test_missing_asset_mapping_creates_blocker(tmp_path: Path) -> None:
    project = _make_base_project(tmp_path)
    author_response = json.dumps(
        {
            "voiceover_text_full": "Ein Satz ohne jede Asset-Zuordnung heute schon lange.",
            "sentence_items": [
                {
                    "sentence_id": "sentence_001",
                    "text": "Ein Satz ohne jede Asset-Zuordnung heute schon lange.",
                    "primary_asset_id": "",
                    "needs_supplement_asset": False,
                }
            ],
        }
    )
    fake_response = PlanLlmResponse(provider="anthropic", model="claude-sonnet-5", raw_text=author_response)
    with patch(f"{_AUTHOR_MODULE}.generate_plan_text_with_metadata", return_value=fake_response):
        generate_folder_voiceover(project, FOLDER_NAME, provider="anthropic", model="claude-sonnet-5")
    confirm_folder_voiceover(project, FOLDER_NAME)
    _confirm_intro(project)

    plan = build_confirmed_voiceover_project_plan(project)
    assert any(error.type == VO_ERROR_MISSING_ASSET_MAPPING for error in plan.blockers)
    assert plan.status == PLAN_STATUS_NEEDS_REVIEW


def test_needs_supplement_without_reason_creates_warning(tmp_path: Path) -> None:
    project = _make_base_project(tmp_path)
    author_response = json.dumps(
        {
            "voiceover_text_full": "Ein Satz mit fehlender Supplement-Begruendung heute schon lange.",
            "sentence_items": [
                {
                    "sentence_id": "sentence_001",
                    "text": "Ein Satz mit fehlender Supplement-Begruendung heute schon lange.",
                    "primary_asset_id": "",
                    "needs_supplement_asset": True,
                    "supplement_reason": "",
                }
            ],
        }
    )
    fake_response = PlanLlmResponse(provider="anthropic", model="claude-sonnet-5", raw_text=author_response)
    with patch(f"{_AUTHOR_MODULE}.generate_plan_text_with_metadata", return_value=fake_response):
        generate_folder_voiceover(project, FOLDER_NAME, provider="anthropic", model="claude-sonnet-5")
    confirm_folder_voiceover(project, FOLDER_NAME)
    _confirm_intro(project)

    plan = build_confirmed_voiceover_project_plan(project)
    assert any(error.type == VO_ERROR_MISSING_SUPPLEMENT_REASON for error in plan.warnings)


# --- Source Artifacts / Staleness ---


def test_source_artifact_hashes_are_saved(tmp_path: Path) -> None:
    project = _make_fully_ready_project(tmp_path)
    plan = build_confirmed_voiceover_project_plan(project)
    hashes = plan.source_artifacts.get("created_from_hashes", {})
    assert hashes.get("dramaturgy")
    assert hashes.get("folder_voiceovers")
    assert hashes.get("intro_hook")


def test_plan_is_stale_after_source_artifact_changes(tmp_path: Path) -> None:
    project = _make_fully_ready_project(tmp_path)
    plan = build_confirmed_voiceover_project_plan(project)
    save_confirmed_voiceover_project_plan(project, plan)

    assert is_project_plan_stale(project, plan) is False

    save_project_brief(project, ProjectBrief(project_id=project.id, video_title="Neuer Titel", language="EN"))
    assert is_project_plan_stale(project, plan) is True


# --- Exports ---


def test_json_export_is_written(tmp_path: Path) -> None:
    project = _make_fully_ready_project(tmp_path)
    plan = build_confirmed_voiceover_project_plan(project)
    path = export_voiceover_project_plan_json(project, plan)
    assert path == get_voiceover_project_plan_json_path(project.work_dir_path)
    assert path.is_file()


def test_markdown_export_is_written(tmp_path: Path) -> None:
    project = _make_fully_ready_project(tmp_path)
    plan = build_confirmed_voiceover_project_plan(project)
    path = export_voiceover_project_plan_markdown(project, plan)
    assert path == get_voiceover_project_plan_md_path(project.work_dir_path)
    assert path.is_file()


def test_csv_export_is_written(tmp_path: Path) -> None:
    project = _make_fully_ready_project(tmp_path)
    plan = build_confirmed_voiceover_project_plan(project)
    path = export_voiceover_project_plan_csv(project, plan)
    assert path == get_voiceover_project_plan_csv_path(project.work_dir_path)
    assert path.is_file()


def test_csv_contains_one_row_per_sentence_item(tmp_path: Path) -> None:
    project = _make_fully_ready_project(tmp_path)
    plan = build_confirmed_voiceover_project_plan(project)
    path = export_voiceover_project_plan_csv(project, plan)
    rows = list(csv.DictReader(io.StringIO(path.read_text(encoding="utf-8"))))
    folder_rows = [row for row in rows if row["scope"] == "folder"]
    assert len(folder_rows) == 1
    assert folder_rows[0]["sentence_id"] == "sentence_001"


def test_csv_contains_intro_visual_beat_rows(tmp_path: Path) -> None:
    project = _make_fully_ready_project(tmp_path)
    plan = build_confirmed_voiceover_project_plan(project)
    path = export_voiceover_project_plan_csv(project, plan)
    rows = list(csv.DictReader(io.StringIO(path.read_text(encoding="utf-8"))))
    intro_rows = [row for row in rows if row["scope"] == "intro"]
    assert len(intro_rows) == 1
    assert intro_rows[0]["folder_name"] == "000_intro"
    assert intro_rows[0]["item_id"] == "hook_beat_001"


def test_markdown_contains_intro_and_folders(tmp_path: Path) -> None:
    project = _make_fully_ready_project(tmp_path)
    plan = build_confirmed_voiceover_project_plan(project)
    path = export_voiceover_project_plan_markdown(project, plan)
    content = path.read_text(encoding="utf-8")
    assert "## Intro" in content
    assert "Ein Ort voller Geheimnisse" in content
    assert f"### 1. {FOLDER_NAME}" in content


# --- Protection / no side effects ---


def test_no_edit_plan_documents_created(tmp_path: Path) -> None:
    project = _make_fully_ready_project(tmp_path)
    plan = build_confirmed_voiceover_project_plan(project)
    save_confirmed_voiceover_project_plan(project, plan)
    export_voiceover_project_plan_json(project, plan)
    export_voiceover_project_plan_markdown(project, plan)
    export_voiceover_project_plan_csv(project, plan)

    assert not (project.work_dir_path / "edit_plan").exists()
    assert not (project.work_dir_path / "exports").exists()


def test_original_media_not_touched(tmp_path: Path) -> None:
    project = _make_fully_ready_project(tmp_path)
    original_path = project.project_root_path / FOLDER_NAME / "clip1.mp4"
    build_confirmed_voiceover_project_plan(project)
    assert not original_path.exists()


def test_export_contains_no_api_keys(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("ELEVENLABS_API_KEY", "sk_super_secret_final_plan_leak")
    project = _make_fully_ready_project(tmp_path)
    plan = build_confirmed_voiceover_project_plan(project)
    json_path = export_voiceover_project_plan_json(project, plan)
    md_path = export_voiceover_project_plan_markdown(project, plan)
    csv_path = export_voiceover_project_plan_csv(project, plan)

    for path in (json_path, md_path, csv_path):
        assert "sk_super_secret_final_plan_leak" not in path.read_text(encoding="utf-8")


def test_export_contains_no_raw_llm_responses(tmp_path: Path) -> None:
    project = _make_fully_ready_project(tmp_path)
    plan = build_confirmed_voiceover_project_plan(project)
    json_path = export_voiceover_project_plan_json(project, plan)
    content = json_path.read_text(encoding="utf-8")
    assert "raw_llm_response" not in content
    assert "prompt_hash" not in content


def test_confirmed_plan_save_and_load_roundtrip(tmp_path: Path) -> None:
    project = _make_fully_ready_project(tmp_path)
    plan = build_confirmed_voiceover_project_plan(project)
    save_confirmed_voiceover_project_plan(project, plan)

    loaded = load_confirmed_voiceover_project_plan(project)
    assert loaded is not None
    assert loaded.status == plan.status
    path = get_confirmed_voiceover_project_plan_path(project.work_dir_path)
    assert path.is_file()


def test_load_confirmed_plan_returns_none_when_missing(tmp_path: Path) -> None:
    project = _make_base_project(tmp_path)
    assert load_confirmed_voiceover_project_plan(project) is None


# --- Audit-Hardening: expliziter AUDIO_READY_WITH_WARNINGS-Blocker (§1) ---


def test_audio_ready_with_warnings_with_real_duration_still_blocks_ready_for_cut(tmp_path: Path) -> None:
    """Edge Case aus dem Audit: AUDIO_READY_WITH_WARNINGS MUSS READY_FOR_CUT
    explizit verhindern — auch wenn (hypothetisch, hier manuell erzwungen)
    eine reale Dauer > 0, Alignment und vollständiges Asset-Mapping vorliegen
    und keine sonstigen Blocker existieren. Vor dem Fix schlug dieser Test
    fehl (Status wurde faelschlich READY_FOR_CUT)."""
    project = _make_fully_ready_project(tmp_path)
    from otio_app.services.voiceover_generation.tts_orchestration_service import (
        load_audio_manifest,
        save_audio_manifest,
    )

    baseline_plan = build_confirmed_voiceover_project_plan(project)
    assert baseline_plan.status == PLAN_STATUS_READY_FOR_CUT  # Ausgangslage: alles sauber

    manifest = load_audio_manifest(project)
    updated_items = [
        item.model_copy(update={"status": "AUDIO_READY_WITH_WARNINGS"})
        if item.folder_name == FOLDER_NAME
        else item
        for item in manifest.items
    ]
    save_audio_manifest(project, manifest.model_copy(update={"items": updated_items}))

    plan = build_confirmed_voiceover_project_plan(project)
    folder_item = next(f for f in plan.folders if f.folder_name == FOLDER_NAME)
    # Dauer und Alignment bleiben unveraendert (real > 0 / vorhanden) —
    # trotzdem darf der Status nicht READY_FOR_CUT sein.
    assert folder_item.audio_status == "AUDIO_READY_WITH_WARNINGS"
    assert folder_item.audio_duration_sec > 0
    assert folder_item.alignment_items

    assert plan.status != PLAN_STATUS_READY_FOR_CUT
    assert plan.readiness.all_required_audio_ready is False
    assert any(error.type == "AUDIO_DURATION_MISSING" for error in plan.warnings)
    assert not plan.blockers  # nur Warning, kein Blocker -> NEEDS_REVIEW waere falsch


def test_missing_alignment_with_present_audio_prevents_ready_for_cut(tmp_path: Path) -> None:
    """Audit-Lücke (§5.2): Audio vorhanden, aber Alignment fehlt (z. B.
    gelöscht/nicht gebaut) -> darf niemals READY_FOR_CUT ergeben."""
    project = _make_fully_ready_project(tmp_path)
    baseline_plan = build_confirmed_voiceover_project_plan(project)
    assert baseline_plan.status == PLAN_STATUS_READY_FOR_CUT

    alignment_path = Path(baseline_plan.folders[0].alignment_path)
    assert alignment_path.is_file()
    alignment_path.unlink()

    plan = build_confirmed_voiceover_project_plan(project)
    assert plan.folders[0].alignment_items == []
    assert plan.status != PLAN_STATUS_READY_FOR_CUT
    assert any(error.type == "MISSING_ALIGNMENT" for error in plan.warnings)


# --- Audit-Hardening: Konstanten statt Literale (§2) ---


def test_final_plan_service_uses_status_constants_not_raw_literals() -> None:
    """Statischer Guard: Status-/Error-Typ-Strings, für die defaults.py eine
    Konstante bereitstellt, dürfen in final_plan_service.py nicht mehr als
    rohes String-Literal auftauchen (Audit-Fund §2)."""
    import inspect

    from otio_app.services.voiceover_generation import final_plan_service

    source = inspect.getsource(final_plan_service)
    forbidden_literal_patterns = [
        'type="MISSING_CONFIRMED_DRAMATURGY"',
        'type="MISSING_CONFIRMED_INTRO"',
        'type="MISSING_FOLDER_VOICEOVER"',
        'type="EMPTY_TEXT"',
        'type="EMPTY_VISUAL_BEATS"',
        'type="EMPTY_SENTENCE_ITEMS"',
        'type="AUDIO_FAILED"',
        'type="AUDIO_STALE"',
        'type="AUDIO_DURATION_MISSING"',
        'type="MISSING_ALIGNMENT"',
        'type="MISSING_AUDIO"',
        'type="NEEDS_USER_REVIEW"',
        'severity="WARNING"',
        'severity="BLOCKER"',
        'asset_mapping_status = "BLOCKED"',
        'asset_mapping_status = "WARNINGS"',
        'asset_mapping_status = "PASS"',
        'readiness_status = "BLOCKED"',
        'readiness_status = "WARNING"',
        'readiness_status = "READY"',
        'return "MISSING_AUDIO"',
        'return "BLOCKED"',
        'return "WARNING"',
        'return "STALE_AUDIO"',
        'return "MISSING_ALIGNMENT"',
        'return "READY"',
        'validation_status = "UNKNOWN"',
        'scope == "intro"',
        'scope == "folder"',
    ]
    for pattern in forbidden_literal_patterns:
        assert pattern not in source, (
            f"final_plan_service.py verwendet noch das Literal {pattern!r} statt einer Konstante aus defaults.py."
        )


def test_readiness_error_types_match_defaults_constants(tmp_path: Path) -> None:
    """Stellt sicher, dass die tatsächlich erzeugten ReadinessError.type-Werte
    den defaults.py-Konstanten entsprechen (nicht nur zufällig übereinstimmen)."""
    from otio_app.defaults import (
        READINESS_ERROR_MISSING_CONFIRMED_DRAMATURGY,
        READINESS_ERROR_MISSING_CONFIRMED_INTRO,
    )

    project_root = tmp_path / "USA"
    (project_root / FOLDER_NAME).mkdir(parents=True)
    project = Project(
        id="no-dramaturgy-project",
        name="No Dramaturgy",
        project_root=str(project_root),
        work_dir=str(project_root / "_otio"),
        project_mode=ProjectMode.WITHOUT_VOICEOVER,
        asset_subdir_names=[FOLDER_NAME],
        selected_asset_subdirs=[FOLDER_NAME],
    )
    plan = build_confirmed_voiceover_project_plan(project)
    blocker_types = {error.type for error in plan.blockers}
    assert READINESS_ERROR_MISSING_CONFIRMED_DRAMATURGY in blocker_types
    assert READINESS_ERROR_MISSING_CONFIRMED_INTRO in blocker_types


# --- Audit-Hardening: Markdown zeigt fehlende aktive Ordner (§3) ---


def test_markdown_shows_active_unconfirmed_folder_as_incomplete(tmp_path: Path) -> None:
    """2 aktive Ordner laut bestätigter Dramaturgie, nur 1 bestätigtes
    Voice-over -> confirmed_voiceover_project_plan.json enthält weiterhin nur
    den bestätigten Ordner, aber das Markdown macht den fehlenden Ordner in
    einer eigenen Sektion sichtbar (Audit-Fund §3 / §12)."""
    second_folder = "Antelope Canyon"
    project_root = tmp_path / "USA"
    (project_root / FOLDER_NAME).mkdir(parents=True)
    (project_root / second_folder).mkdir(parents=True)
    project = Project(
        id="two-folder-project",
        name="Two Folder Test",
        project_root=str(project_root),
        work_dir=str(project_root / "_otio"),
        project_mode=ProjectMode.WITHOUT_VOICEOVER,
        asset_subdir_names=[FOLDER_NAME, second_folder],
        selected_asset_subdirs=[FOLDER_NAME, second_folder],
    )
    for folder in (FOLDER_NAME, second_folder):
        inv_path = get_folder_inventory_path(project.work_dir_path, folder)
        inv_path.parent.mkdir(parents=True, exist_ok=True)
        analysis = AssetFolderAnalysis(
            folder=folder, assets=[AssetMediaAnalysis(path=f"{folder}/clip1.mp4", description="Aufnahme.")]
        )
        inv_path.write_text(analysis.model_dump_json(indent=2), encoding="utf-8")

    save_project_brief(project, ProjectBrief(project_id=project.id, video_title="Zwei Ordner", language="DE"))
    save_confirmed_dramaturgy(
        project,
        DramaturgyPlan(
            project_id=project.id,
            project_title="Zwei Ordner",
            recommended_folder_order=[
                DramaturgyFolderEntry(folder_name=FOLDER_NAME, order_index=1, enabled=True),
                DramaturgyFolderEntry(folder_name=second_folder, order_index=2, enabled=True),
            ],
        ),
    )
    save_folder_voiceover_settings(project, build_default_folder_voiceover_settings(project))

    _confirm_folder_voiceover(project)  # bestätigt nur FOLDER_NAME, nicht second_folder
    _confirm_intro(project)

    plan = build_confirmed_voiceover_project_plan(project)
    assert len(plan.folders) == 1  # confirmed_voiceover_project_plan.json bleibt "nur bestätigte"
    assert plan.folders[0].folder_name == FOLDER_NAME

    path = export_voiceover_project_plan_markdown(project, plan)
    content = path.read_text(encoding="utf-8")
    assert "## Fehlende / unvollständige aktive Ordner" in content
    assert second_folder in content
    assert "MISSING_FOLDER_VOICEOVER" in content


# --- Audit-Hardening: Intro readiness_status nicht irreführend (§4) ---


def test_intro_readiness_status_is_warning_when_visual_beat_has_weak_asset_match(tmp_path: Path) -> None:
    project = _make_base_project(tmp_path)
    _confirm_folder_voiceover(project)
    candidates_doc = IntroHookCandidatesDocument(
        project_id=project.id,
        candidates=[
            IntroHookCandidate(
                hook_id="hook_001",
                hook_text="Ein Ort voller Geheimnisse wartet auf jeden mutigen Reisenden heute schon lange.",
                hook_type="mystery",
                used_folders=[FOLDER_NAME],
                visual_beats=[
                    IntroHookVisualBeat(
                        hook_beat_id="hook_beat_001",
                        text="x",
                        primary_asset_id="asset_clip1",
                        asset_confidence=0.05,
                    )
                ],
            )
        ],
        llm_run_id="fake-run",
    )
    save_intro_hook_candidates(project, candidates_doc)
    confirm_intro_hook(project, "hook_001")
    _synthesize_everything(project)

    plan = build_confirmed_voiceover_project_plan(project)
    assert any(
        error.type == "WEAK_ASSET_MATCH" and error.scope == "sentence" and error.sentence_id == "hook_beat_001"
        for error in plan.warnings
    )
    assert plan.intro.readiness_status == "WARNING"


def test_intro_readiness_status_is_blocked_when_visual_beat_has_no_asset_mapping(tmp_path: Path) -> None:
    """Fehlendes Asset-Mapping (kein primary_asset_id UND
    needs_supplement_asset=False) ist ein echter Blocker — intro.readiness_status
    darf dann nicht mehr READY sein (Audit-Fund §4)."""
    project = _make_base_project(tmp_path)
    _confirm_folder_voiceover(project)
    candidates_doc = IntroHookCandidatesDocument(
        project_id=project.id,
        candidates=[
            IntroHookCandidate(
                hook_id="hook_001",
                hook_text="Ein Ort voller Geheimnisse wartet auf jeden mutigen Reisenden heute schon lange.",
                hook_type="mystery",
                used_folders=[FOLDER_NAME],
                visual_beats=[
                    IntroHookVisualBeat(
                        hook_beat_id="hook_beat_001",
                        text="x",
                        primary_asset_id="",
                        needs_supplement_asset=False,
                    )
                ],
            )
        ],
        llm_run_id="fake-run",
    )
    save_intro_hook_candidates(project, candidates_doc)
    confirm_intro_hook(project, "hook_001")
    _synthesize_everything(project)

    plan = build_confirmed_voiceover_project_plan(project)
    assert any(error.type == VO_ERROR_MISSING_ASSET_MAPPING for error in plan.blockers)
    assert plan.intro.readiness_status == "BLOCKED"
    assert plan.status == PLAN_STATUS_NEEDS_REVIEW


def test_final_plan_service_never_references_production_edit_plan_symbols() -> None:
    """Statischer Schutz: final_plan_service.py darf keine Produktions-Symbole
    aus edit_plan_builder.py / otio_exporter.py referenzieren (§12)."""
    import inspect

    from otio_app.services.voiceover_generation import final_plan_service

    source = inspect.getsource(final_plan_service)
    for forbidden in (
        "save_edit_plan",
        "build_edit_plan",
        "_set_draft",
        "export_otio_timeline",
        "merge_confirmed_edit_plans",
        "persist_accepted_edit_plan",
        "edit_plan_builder",
        "otio_exporter",
    ):
        assert forbidden not in source, f"final_plan_service referenziert verbotenes Symbol '{forbidden}'."


def test_with_voiceover_workflow_unaffected(tmp_path: Path) -> None:
    """Regression: Diese Phase darf den bestehenden with_voiceover-Workflow
    (edit_plan_builder / otio_exporter) nicht berühren."""
    from otio_app.services import edit_plan_builder, otio_exporter

    assert hasattr(edit_plan_builder, "build_edit_plan")
    assert hasattr(edit_plan_builder, "save_edit_plan")
    assert hasattr(otio_exporter, "build_otio_timeline")
