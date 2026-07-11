"""Nutzervorgabe (Juli 2026, "wir haben gar kein closing asset nach dem
letzten Satz, der die Pause ausfüllt"): vollständiger End-to-End-Test der
Closing-Shot-Pipeline über die REALEN Produktionsfunktionen (nicht nur
synthetisch konstruierte Modelle) — vom LLM-Autor-Response über Confirm,
Vertonung (TTS-Mock), finalen Projektplan bis zum Cut-Plan-Draft inkl.
Asset-Auswahl und Validierung.

Ergänzt die eher chirurgischen/synthetischen Tests in
test_cut_plan_closing_shot_wiring.py (dort werden ConfirmedFolderPlanItem/
AlignmentItem direkt konstruiert) um einen Nachweis, dass die komplette
Kette der ECHTEN Funktionsaufrufe zusammenpasst (Feldnamen, Serialisierung,
Staleness-Hashes etc.)."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

from otio_app.analysis_models import AssetFolderAnalysis, AssetMediaAnalysis
from otio_app.defaults import CUT_PLAN_ERROR_BLACK_GAP_DURING_VOICEOVER
from otio_app.models import Project, ProjectMode
from otio_app.project_layout import get_folder_inventory_path
from otio_app.services.plan_llm_client import PlanLlmResponse
from otio_app.services.voiceover_generation.cut_plan_builder import (
    apply_asset_selection_to_draft,
    build_cut_plan_draft,
    save_cut_plan_draft,
    validate_cut_plan_draft,
)
from otio_app.services.voiceover_generation.cut_plan_models import CutPlanSettings
from otio_app.services.voiceover_generation.cut_plan_settings_service import save_cut_plan_settings
from otio_app.services.voiceover_generation.dramaturgy_service import save_confirmed_dramaturgy
from otio_app.services.voiceover_generation.elevenlabs_settings_service import save_elevenlabs_settings
from otio_app.services.voiceover_generation.final_plan_service import (
    build_confirmed_voiceover_project_plan,
    save_confirmed_voiceover_project_plan,
)
from otio_app.services.voiceover_generation.folder_voiceover_settings_service import (
    build_default_folder_voiceover_settings,
    save_folder_voiceover_settings,
)
from otio_app.services.voiceover_generation.intro_hook_service import confirm_intro_hook, save_intro_hook_candidates
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

FOLDER_A = "Grand Canyon"
FOLDER_B = "Antelope Canyon"

# Bewusst DIESELBE Phrase für beide Ordner — der TTS-Mock (siehe
# _fake_tts_result) liefert eine feste Zeichen-für-Zeichen-Alignment für
# genau diesen Text; identischer Text für beide Ordner spart eine zweite
# Mock-Variante, ohne die Aussagekraft des Tests zu verändern (es geht um
# das WIRING, nicht um unterschiedliche Satzinhalte).
SENTENCE_TEXT = "Zwischen den Felswaenden scheint das Licht von innen zu leuchten heute."


def _fake_tts_result(audio_bytes: bytes = b"FAKE_AUDIO"):
    from otio_app.services.voiceover_generation.elevenlabs_client import ElevenLabsTtsResult

    return ElevenLabsTtsResult(
        audio_bytes=audio_bytes,
        alignment={
            "characters": list(SENTENCE_TEXT),
            "character_start_times_seconds": [i * 0.05 for i in range(len(SENTENCE_TEXT))],
            "character_end_times_seconds": [(i + 1) * 0.05 for i in range(len(SENTENCE_TEXT))],
        },
        normalized_alignment={},
        response_metadata={"status_code": 200},
    )


def _make_project(tmp_path: Path) -> Project:
    project_root = tmp_path / "USA"
    project_root.mkdir()
    (project_root / FOLDER_A).mkdir()
    (project_root / FOLDER_B).mkdir()

    project = Project(
        id="closing-shot-e2e-project",
        name="Closing Shot End-to-End Test",
        project_root=str(project_root),
        work_dir=str(project_root / "_otio"),
        project_mode=ProjectMode.WITHOUT_VOICEOVER,
        asset_subdir_names=[FOLDER_A, FOLDER_B],
        selected_asset_subdirs=[FOLDER_A, FOLDER_B],
    )
    for folder_name, filenames in (
        (FOLDER_A, ["sentence_a.jpg", "closing_a.jpg"]),
        (FOLDER_B, ["sentence_b.jpg"]),
    ):
        for filename in filenames:
            (project.project_root_path / folder_name / filename).write_bytes(b"FAKE_MEDIA_BYTES")
        path = get_folder_inventory_path(project.work_dir_path, folder_name)
        path.parent.mkdir(parents=True, exist_ok=True)
        analysis = AssetFolderAnalysis(
            folder=folder_name,
            assets=[
                AssetMediaAnalysis(path=f"{folder_name}/{filename}", description=filename)
                for filename in filenames
            ],
        )
        path.write_text(analysis.model_dump_json(indent=2), encoding="utf-8")

    save_project_brief(project, ProjectBrief(project_id=project.id, video_title="Wunder der Wüste", language="DE"))

    plan = DramaturgyPlan(
        project_id=project.id,
        project_title="Wunder der Wüste",
        recommended_folder_order=[
            DramaturgyFolderEntry(
                folder_name=FOLDER_A, order_index=1, enabled=True, dramaturgy_role="opener",
                recommended_word_count=100, recommended_min_words=90, recommended_max_words=110,
            ),
            DramaturgyFolderEntry(
                folder_name=FOLDER_B, order_index=2, enabled=True, dramaturgy_role="setup",
                recommended_word_count=100, recommended_min_words=90, recommended_max_words=110,
            ),
        ],
    )
    save_confirmed_dramaturgy(project, plan)
    save_folder_voiceover_settings(project, build_default_folder_voiceover_settings(project))
    save_elevenlabs_settings(
        project, ElevenLabsSettings(project_id=project.id, voice_id="voice-abc", model_id="eleven_multilingual_v2")
    )
    return project


def _confirm_and_synthesize_folder(
    project: Project, folder_name: str, *, primary_asset_id: str, closing_asset_id: str = ""
) -> None:
    payload: dict = {
        "voiceover_text_full": SENTENCE_TEXT,
        "sentence_items": [
            {
                "sentence_id": "sentence_001",
                "beat_id": "beat_001",
                "text": SENTENCE_TEXT,
                "visual_intent": "establishing",
                "primary_asset_id": primary_asset_id,
                "backup_asset_ids": [],
                "asset_confidence": 0.9,
                "needs_supplement_asset": False,
                "supplement_reason": "",
            }
        ],
    }
    if closing_asset_id:
        payload["closing_visual_plan"] = {
            "visual_intent": "aerial establishing shot to close the section",
            "primary_asset_id": closing_asset_id,
            "asset_strategy_reason": "Ruhiger Abschluss, unterschiedlich vom letzten Satz.",
        }
    author_response = json.dumps(payload)
    fake_response = PlanLlmResponse(provider="anthropic", model="claude-sonnet-5", raw_text=author_response)
    with patch(f"{_AUTHOR_MODULE}.generate_plan_text_with_metadata", return_value=fake_response):
        generate_folder_voiceover(project, folder_name, provider="anthropic", model="claude-sonnet-5")
    confirm_folder_voiceover(project, folder_name)

    with (
        patch(f"{_TTS_MODULE}.synthesize_speech_with_timestamps", return_value=_fake_tts_result()),
        patch(f"{_TTS_MODULE}.probe_duration_seconds", return_value=5.0),
    ):
        synthesize_folder_voiceover(project, folder_name)


def _confirm_and_synthesize_intro(project: Project) -> None:
    """Minimaler Intro-Hook — nicht Gegenstand dieses Tests, aber laut
    `final_plan_service.validate_voiceover_project_plan_readiness`
    Voraussetzung dafür, dass der finale Plan überhaupt AUDIO_READY (und
    damit für den Cut Plan nutzbar) werden kann."""
    candidates_doc = IntroHookCandidatesDocument(
        project_id=project.id,
        candidates=[
            IntroHookCandidate(
                hook_id="hook_001",
                hook_text=SENTENCE_TEXT,
                hook_type="mystery",
                used_folders=[FOLDER_A],
                visual_beats=[
                    IntroHookVisualBeat(
                        hook_beat_id="hook_beat_001", text="x", primary_asset_id="asset_sentence_a",
                        asset_confidence=0.8,
                    )
                ],
            )
        ],
        llm_run_id="fake-run",
    )
    save_intro_hook_candidates(project, candidates_doc)
    confirm_intro_hook(project, "hook_001")

    with (
        patch(f"{_TTS_MODULE}.synthesize_speech_with_timestamps", return_value=_fake_tts_result()),
        patch(f"{_TTS_MODULE}.probe_duration_seconds", return_value=5.0),
    ):
        synthesize_intro(project)


def test_full_pipeline_from_llm_response_to_validated_cut_plan_with_closing_shot(tmp_path: Path) -> None:
    """Die vollständige Kette: Autor-LLM (Mock) -> Bestätigen -> Vertonen ->
    finaler Projektplan -> Cut-Plan-Draft -> Asset-Auswahl -> Validierung.
    FOLDER_A bekommt einen Closing Shot, FOLDER_B nicht (Kontrast/Realismus:
    nicht jeder Ordner braucht/hat bereits einen)."""
    project = _make_project(tmp_path)
    _confirm_and_synthesize_intro(project)
    _confirm_and_synthesize_folder(
        project, FOLDER_A, primary_asset_id="asset_sentence_a", closing_asset_id="asset_closing_a"
    )
    _confirm_and_synthesize_folder(project, FOLDER_B, primary_asset_id="asset_sentence_b")

    final_plan = build_confirmed_voiceover_project_plan(project)
    save_confirmed_voiceover_project_plan(project, final_plan)

    folder_a_plan_item = next(f for f in final_plan.folders if f.folder_name == FOLDER_A)
    assert folder_a_plan_item.closing_visual_plan.primary_asset_id == "asset_closing_a"
    folder_b_plan_item = next(f for f in final_plan.folders if f.folder_name == FOLDER_B)
    assert folder_b_plan_item.closing_visual_plan.primary_asset_id == ""

    save_cut_plan_settings(
        project,
        CutPlanSettings(project_id=project.id, initial_audio_offset_sec=1.0, pause_between_sections_sec=2.5),
    )
    draft = build_cut_plan_draft(project)
    save_cut_plan_draft(project, draft)

    closing_items = [item for item in draft.items if item.is_closing_shot]
    assert len(closing_items) == 1
    assert closing_items[0].folder_name == FOLDER_A
    assert closing_items[0].primary_asset_id == "asset_closing_a"

    updated = apply_asset_selection_to_draft(project)
    closing_item = next(item for item in updated.items if item.is_closing_shot)
    assert closing_item.asset_selection_status == "PRIMARY_USED"
    assert closing_item.chosen_asset_id == "asset_closing_a"
    assert closing_item.planned_visual_segments
    assert "section_pause_hold" in closing_item.planned_visual_segments[-1].reason.split("+")

    _, report = validate_cut_plan_draft(project)
    # Die SEKTIONSPAUSE zwischen FOLDER_A und FOLDER_B (6.0s-8.5s) muss durch
    # den Closing Shot vollständig abgedeckt sein — der Closing Shot betrifft
    # NUR diese Pause. FOLDER_B (letzter Ordner, ohne eigenen Closing Shot)
    # kann weiterhin eine eigene, unabhängige BLACK_GAP-Meldung für seinen
    # eigenen Audio-Tail haben (kein Closing Shot dort geplant) — das ist
    # erwartet, nicht Gegenstand dieses Tests.
    section_pause_gaps = [
        error
        for error in report.blockers
        if error.type == CUT_PLAN_ERROR_BLACK_GAP_DURING_VOICEOVER
        and "Pause zwischen Sektionen" in error.message
    ]
    assert section_pause_gaps == []


def test_full_pipeline_without_closing_shot_still_works_unchanged(tmp_path: Path) -> None:
    """Rückwärtskompatibilität: ohne closing_visual_plan im Autor-Response
    (älterer Prompt-Stand/vorhandene Drafts) entsteht KEIN Closing-Item —
    die bestehende Pipeline bleibt unverändert nutzbar."""
    project = _make_project(tmp_path)
    _confirm_and_synthesize_intro(project)
    _confirm_and_synthesize_folder(project, FOLDER_A, primary_asset_id="asset_sentence_a")
    _confirm_and_synthesize_folder(project, FOLDER_B, primary_asset_id="asset_sentence_b")

    final_plan = build_confirmed_voiceover_project_plan(project)
    save_confirmed_voiceover_project_plan(project, final_plan)
    save_cut_plan_settings(project, CutPlanSettings(project_id=project.id))
    draft = build_cut_plan_draft(project)
    save_cut_plan_draft(project, draft)

    assert not any(item.is_closing_shot for item in draft.items)
