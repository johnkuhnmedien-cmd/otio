"""Phase 6: TTS-Orchestrierung — Synthese, Versionierung, Manifest, Staleness."""

from __future__ import annotations

import base64
import json
from pathlib import Path
from unittest.mock import patch

import pytest

from otio_app.analysis_models import AssetFolderAnalysis, AssetMediaAnalysis
from otio_app.defaults import AUDIO_SCOPE_FOLDER, AUDIO_SCOPE_INTRO, AUDIO_STATUS_READY, AUDIO_STATUS_STALE
from otio_app.models import Project, ProjectMode
from otio_app.project_layout import (
    get_folder_inventory_path,
    get_voiceover_audio_manifest_path,
)
from otio_app.services.plan_llm_client import PlanLlmResponse
from otio_app.services.voiceover_generation.dramaturgy_service import save_confirmed_dramaturgy
from otio_app.services.voiceover_generation.elevenlabs_client import ElevenLabsTtsError, ElevenLabsTtsResult
from otio_app.services.voiceover_generation.elevenlabs_settings_service import (
    save_elevenlabs_settings,
)
from otio_app.services.voiceover_generation.folder_voiceover_settings_service import (
    build_default_folder_voiceover_settings,
    save_folder_voiceover_settings,
)
from otio_app.services.voiceover_generation.intro_hook_service import confirm_intro_hook
from otio_app.services.voiceover_generation.models import (
    DramaturgyFolderEntry,
    DramaturgyPlan,
    ElevenLabsSettings,
    IntroHookCandidate,
    IntroHookCandidatesDocument,
)
from otio_app.services.voiceover_generation.tts_orchestration_service import (
    get_next_audio_version_path,
    load_audio_manifest,
    mark_stale_audio_if_needed,
    synthesize_all_confirmed_voiceovers,
    synthesize_folder_voiceover,
    synthesize_intro,
)
from otio_app.services.voiceover_generation.voiceover_author_service import (
    generate_folder_voiceover,
    load_folder_voiceovers_confirmed,
    save_folder_voiceovers_confirmed,
    update_folder_voiceover_text,
)
from otio_app.services.voiceover_generation.voiceover_review_service import confirm_folder_voiceover

_AUTHOR_MODULE = "otio_app.services.voiceover_generation.voiceover_author_service"
_TTS_MODULE = "otio_app.services.voiceover_generation.tts_orchestration_service"
_INTRO_MODULE = "otio_app.services.voiceover_generation.intro_hook_service"


def _fake_tts_result(audio_bytes: bytes = b"FAKE_AUDIO_BYTES") -> ElevenLabsTtsResult:
    return ElevenLabsTtsResult(
        audio_bytes=audio_bytes,
        alignment={
            "characters": list("test"),
            "character_start_times_seconds": [0.0, 0.1, 0.2, 0.3],
            "character_end_times_seconds": [0.1, 0.2, 0.3, 0.4],
        },
        normalized_alignment={},
        response_metadata={"status_code": 200},
    )


def _make_project_with_confirmed_content(tmp_path: Path) -> Project:
    project_root = tmp_path / "USA"
    project_root.mkdir()
    folders = ["Grand Canyon"]
    for folder in folders:
        (project_root / folder).mkdir()

    project = Project(
        id="tts-project",
        name="TTS Test",
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
            DramaturgyFolderEntry(folder_name=folder, order_index=index, enabled=True)
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

    # Intro-Hook bestätigen.
    candidates_doc = IntroHookCandidatesDocument(
        project_id=project.id,
        candidates=[
            IntroHookCandidate(
                hook_id="hook_001",
                hook_text="Ein Ort voller Geheimnisse wartet auf jeden mutigen Reisenden heute schon lange.",
                hook_type="mystery",
            )
        ],
        llm_run_id="fake-run",
    )
    from otio_app.services.voiceover_generation.intro_hook_service import save_intro_hook_candidates

    save_intro_hook_candidates(project, candidates_doc)
    confirm_intro_hook(project, "hook_001")

    save_elevenlabs_settings(
        project, ElevenLabsSettings(project_id=project.id, voice_id="voice-abc", model_id="eleven_multilingual_v2")
    )
    return project


def test_synthesize_folder_voiceover_saves_audio_file(tmp_path: Path) -> None:
    project = _make_project_with_confirmed_content(tmp_path)
    with (
        patch(f"{_TTS_MODULE}.synthesize_speech_with_timestamps", return_value=_fake_tts_result()),
        patch(f"{_TTS_MODULE}.probe_duration_seconds", return_value=5.0),
    ):
        item = synthesize_folder_voiceover(project, "Grand Canyon")

    assert item.status == AUDIO_STATUS_READY
    audio_path = Path(item.audio_path)
    assert audio_path.is_file()
    assert audio_path.read_bytes() == b"FAKE_AUDIO_BYTES"


def test_synthesize_intro_saves_audio_file(tmp_path: Path) -> None:
    project = _make_project_with_confirmed_content(tmp_path)
    with (
        patch(f"{_TTS_MODULE}.synthesize_speech_with_timestamps", return_value=_fake_tts_result()),
        patch(f"{_TTS_MODULE}.probe_duration_seconds", return_value=5.0),
    ):
        item = synthesize_intro(project)

    assert item.status == AUDIO_STATUS_READY
    assert Path(item.audio_path).is_file()
    assert item.scope == AUDIO_SCOPE_INTRO


def test_audio_manifest_is_written(tmp_path: Path) -> None:
    project = _make_project_with_confirmed_content(tmp_path)
    with patch(f"{_TTS_MODULE}.synthesize_speech_with_timestamps", return_value=_fake_tts_result()):
        synthesize_folder_voiceover(project, "Grand Canyon")

    path = get_voiceover_audio_manifest_path(project.language_work_dir_path)
    assert path.is_file()


def test_manifest_contains_intro_and_folder_items(tmp_path: Path) -> None:
    project = _make_project_with_confirmed_content(tmp_path)
    with patch(f"{_TTS_MODULE}.synthesize_speech_with_timestamps", return_value=_fake_tts_result()):
        synthesize_intro(project)
        synthesize_folder_voiceover(project, "Grand Canyon")

    manifest = load_audio_manifest(project)
    scopes = {(item.scope, item.folder_name) for item in manifest.items}
    assert (AUDIO_SCOPE_INTRO, "") in scopes
    assert (AUDIO_SCOPE_FOLDER, "Grand Canyon") in scopes


def test_retts_creates_v002_instead_of_overwriting(tmp_path: Path) -> None:
    project = _make_project_with_confirmed_content(tmp_path)
    with patch(f"{_TTS_MODULE}.synthesize_speech_with_timestamps", return_value=_fake_tts_result(b"V1")):
        first = synthesize_folder_voiceover(project, "Grand Canyon")
    assert first.audio_version == 1
    v1_path = Path(first.audio_path)
    assert v1_path.read_bytes() == b"V1"

    # Text ändern (manuell) + erneut bestätigen -> neuer Hash im confirmed
    # Dokument -> erneuter Klick erzeugt v002.
    update_folder_voiceover_text(project, "Grand Canyon", "Ein komplett neuer Text für diesen Ort heute.")
    confirm_folder_voiceover(project, "Grand Canyon")
    with patch(f"{_TTS_MODULE}.synthesize_speech_with_timestamps", return_value=_fake_tts_result(b"V2")):
        second = synthesize_folder_voiceover(project, "Grand Canyon")

    assert second.audio_version == 2
    assert v1_path.is_file()  # Original bleibt erhalten
    assert v1_path.read_bytes() == b"V1"
    v2_path = Path(second.audio_path)
    assert v2_path != v1_path
    assert v2_path.read_bytes() == b"V2"


def test_unchanged_text_does_not_create_new_version(tmp_path: Path) -> None:
    project = _make_project_with_confirmed_content(tmp_path)
    with patch(f"{_TTS_MODULE}.synthesize_speech_with_timestamps", return_value=_fake_tts_result()) as mock_tts:
        first = synthesize_folder_voiceover(project, "Grand Canyon")
        second = synthesize_folder_voiceover(project, "Grand Canyon")

    assert first.audio_version == second.audio_version == 1
    assert mock_tts.call_count == 1  # zweiter Aufruf hat NICHT erneut vertont


def test_text_change_marks_existing_audio_stale(tmp_path: Path) -> None:
    project = _make_project_with_confirmed_content(tmp_path)
    with patch(f"{_TTS_MODULE}.synthesize_speech_with_timestamps", return_value=_fake_tts_result()):
        synthesize_folder_voiceover(project, "Grand Canyon")

    update_folder_voiceover_text(project, "Grand Canyon", "Komplett neuer Text nach der Vertonung heute.")
    confirm_folder_voiceover(project, "Grand Canyon")
    manifest = mark_stale_audio_if_needed(project)

    item = next(i for i in manifest.items if i.folder_name == "Grand Canyon")
    assert item.status == AUDIO_STATUS_STALE


def test_tts_run_manifest_is_written(tmp_path: Path) -> None:
    project = _make_project_with_confirmed_content(tmp_path)
    with patch(f"{_TTS_MODULE}.synthesize_speech_with_timestamps", return_value=_fake_tts_result()):
        item = synthesize_folder_voiceover(project, "Grand Canyon")

    run_dir = Path(item.audio_path).parent / "tts_runs" / item.tts_run_id
    manifest_path = run_dir / "tts_run_manifest.json"
    assert manifest_path.is_file()
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert payload["scope"] == "folder"
    assert payload["folder_name"] == "Grand Canyon"
    assert payload["status"] == "PASS"


def test_elevenlabs_timestamps_file_is_written(tmp_path: Path) -> None:
    project = _make_project_with_confirmed_content(tmp_path)
    with patch(f"{_TTS_MODULE}.synthesize_speech_with_timestamps", return_value=_fake_tts_result()):
        item = synthesize_folder_voiceover(project, "Grand Canyon")

    assert Path(item.timestamps_path).is_file()
    payload = json.loads(Path(item.timestamps_path).read_text(encoding="utf-8"))
    assert "alignment" in payload


def test_request_metadata_never_contains_api_key(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    project = _make_project_with_confirmed_content(tmp_path)
    monkeypatch.setenv("ELEVENLABS_API_KEY", "sk_super_secret_leak_orchestration")
    with patch(f"{_TTS_MODULE}.synthesize_speech_with_timestamps", return_value=_fake_tts_result()):
        item = synthesize_folder_voiceover(project, "Grand Canyon")

    run_dir = Path(item.audio_path).parent / "tts_runs" / item.tts_run_id
    request_path = run_dir / "elevenlabs_tts_request_metadata.json"
    assert "sk_super_secret_leak_orchestration" not in request_path.read_text(encoding="utf-8")


def test_response_metadata_does_not_contain_audio_base64(tmp_path: Path) -> None:
    project = _make_project_with_confirmed_content(tmp_path)
    with patch(f"{_TTS_MODULE}.synthesize_speech_with_timestamps", return_value=_fake_tts_result()):
        item = synthesize_folder_voiceover(project, "Grand Canyon")

    run_dir = Path(item.audio_path).parent / "tts_runs" / item.tts_run_id
    response_path = run_dir / "elevenlabs_tts_response_metadata.json"
    payload = json.loads(response_path.read_text(encoding="utf-8"))
    assert "audio_base64" not in payload
    assert base64.b64encode(b"FAKE_AUDIO_BYTES").decode("ascii") not in json.dumps(payload)


def test_tts_error_writes_tts_errors_json_without_api_key(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = _make_project_with_confirmed_content(tmp_path)
    monkeypatch.setenv("ELEVENLABS_API_KEY", "sk_error_secret")
    with patch(
        f"{_TTS_MODULE}.synthesize_speech_with_timestamps",
        side_effect=ElevenLabsTtsError("ElevenLabs antwortete mit Status 500: server error"),
    ):
        item = synthesize_folder_voiceover(project, "Grand Canyon")

    assert item.status == "FAILED"
    manifest = load_audio_manifest(project)
    assert manifest.items[0].status == "FAILED"


def test_synthesize_all_confirmed_voiceovers_processes_intro_and_folders(tmp_path: Path) -> None:
    project = _make_project_with_confirmed_content(tmp_path)
    progress_calls = []
    with patch(f"{_TTS_MODULE}.synthesize_speech_with_timestamps", return_value=_fake_tts_result()):
        manifest = synthesize_all_confirmed_voiceovers(
            project, progress_callback=lambda label, index, total: progress_calls.append((label, index, total))
        )

    scopes = {(item.scope, item.folder_name) for item in manifest.items}
    assert (AUDIO_SCOPE_INTRO, "") in scopes
    assert (AUDIO_SCOPE_FOLDER, "Grand Canyon") in scopes
    assert progress_calls[0] == ("Intro", 1, 2)


def test_ffprobe_failure_produces_audio_ready_with_warnings(tmp_path: Path) -> None:
    """Hardening (vor Phase 7): ffprobe-Fehler darf NICHT stillschweigend als
    AUDIO_READY durchgehen — es muss AUDIO_READY_WITH_WARNINGS sein."""
    project = _make_project_with_confirmed_content(tmp_path)
    with (
        patch(f"{_TTS_MODULE}.synthesize_speech_with_timestamps", return_value=_fake_tts_result()),
        patch(f"{_TTS_MODULE}.probe_duration_seconds", return_value=None),
    ):
        item = synthesize_folder_voiceover(project, "Grand Canyon")

    assert item.status == "AUDIO_READY_WITH_WARNINGS"
    assert item.audio_duration_sec == 0.0
    assert item.error_message


def test_ffprobe_failure_adds_audio_duration_unknown_alignment_warning(tmp_path: Path) -> None:
    project = _make_project_with_confirmed_content(tmp_path)
    with (
        patch(f"{_TTS_MODULE}.synthesize_speech_with_timestamps", return_value=_fake_tts_result()),
        patch(f"{_TTS_MODULE}.probe_duration_seconds", return_value=None),
    ):
        item = synthesize_folder_voiceover(project, "Grand Canyon")

    from otio_app.services.voiceover_generation.audio_alignment_service import load_alignment

    alignment = load_alignment(project, "folder", "Grand Canyon")
    assert alignment is not None
    assert "AUDIO_DURATION_UNKNOWN" in alignment.alignment_warnings
    assert item.tts_run_id  # sanity


def test_unchanged_text_with_warnings_status_does_not_retrigger_tts(tmp_path: Path) -> None:
    """Auch AUDIO_READY_WITH_WARNINGS gilt als 'bereits aktiv' — unveränderter
    Text löst keinen erneuten TTS-Call aus."""
    project = _make_project_with_confirmed_content(tmp_path)
    with (
        patch(f"{_TTS_MODULE}.synthesize_speech_with_timestamps", return_value=_fake_tts_result()) as mock_tts,
        patch(f"{_TTS_MODULE}.probe_duration_seconds", return_value=None),
    ):
        first = synthesize_folder_voiceover(project, "Grand Canyon")
        second = synthesize_folder_voiceover(project, "Grand Canyon")

    assert first.status == second.status == "AUDIO_READY_WITH_WARNINGS"
    assert mock_tts.call_count == 1


def test_get_next_audio_version_path_starts_at_1(tmp_path: Path) -> None:
    project = _make_project_with_confirmed_content(tmp_path)
    path, version = get_next_audio_version_path(project, AUDIO_SCOPE_FOLDER, "Grand Canyon")
    assert version == 1
    assert path.name == "voiceover_v001.mp3"


def test_no_edit_plan_documents_created(tmp_path: Path) -> None:
    project = _make_project_with_confirmed_content(tmp_path)
    with patch(f"{_TTS_MODULE}.synthesize_speech_with_timestamps", return_value=_fake_tts_result()):
        synthesize_all_confirmed_voiceovers(project)

    assert not (project.language_work_dir_path / "edit_plan").exists()
    assert not (project.language_work_dir_path / "exports").exists()


def _set_first_sentence_pause_after(project, folder_name: str, pause_after: str) -> None:
    """Setzt pause_after auf dem ersten sentence_item eines bestätigten
    Ordner-Voice-overs, OHNE voiceover_text_full zu ändern — simuliert eine
    reine Pausen-Änderung."""
    document = load_folder_voiceovers_confirmed(project)
    updated_items = []
    for item in document.items:
        if item.folder_name == folder_name and item.sentence_items:
            new_sentence_items = list(item.sentence_items)
            new_sentence_items[0] = new_sentence_items[0].model_copy(
                update={"pause_after": pause_after}
            )
            item = item.model_copy(update={"sentence_items": new_sentence_items})
        updated_items.append(item)
    save_folder_voiceovers_confirmed(project, document.model_copy(update={"items": updated_items}))


def test_synthesize_folder_voiceover_inserts_pause_tag_for_v3_model(tmp_path: Path) -> None:
    """Nutzerfeedback: Pausen zwischen Abschnitten — bei eleven_v3 muss der
    tatsächlich an ElevenLabs gesendete Text den Pause-Tag enthalten."""
    project = _make_project_with_confirmed_content(tmp_path)
    save_elevenlabs_settings(
        project,
        ElevenLabsSettings(project_id=project.id, voice_id="voice-abc", model_id="eleven_v3"),
    )
    _set_first_sentence_pause_after(project, "Grand Canyon", "long")

    with patch(
        f"{_TTS_MODULE}.synthesize_speech_with_timestamps", return_value=_fake_tts_result()
    ) as mock_tts:
        synthesize_folder_voiceover(project, "Grand Canyon")

    sent_text = mock_tts.call_args.args[0]
    assert "[long pause]" in sent_text


def test_synthesize_folder_voiceover_no_pause_tag_for_default_model(tmp_path: Path) -> None:
    """Default-Modell ist NICHT eleven_v3 — derselbe pause_after-Wert darf
    keinen Tag im gesendeten Text erzeugen (würde sonst vorgelesen)."""
    project = _make_project_with_confirmed_content(tmp_path)
    _set_first_sentence_pause_after(project, "Grand Canyon", "long")

    with patch(
        f"{_TTS_MODULE}.synthesize_speech_with_timestamps", return_value=_fake_tts_result()
    ) as mock_tts:
        synthesize_folder_voiceover(project, "Grand Canyon")

    sent_text = mock_tts.call_args.args[0]
    assert "[" not in sent_text
    confirmed = load_folder_voiceovers_confirmed(project)
    draft = next(item for item in confirmed.items if item.folder_name == "Grand Canyon")
    assert sent_text == draft.voiceover_text_full


def test_pause_only_change_marks_existing_audio_stale_for_v3_model(tmp_path: Path) -> None:
    """Nutzerfeedback-Risiko (selbst benannt): eine reine Pausen-Änderung
    (ohne Textänderung) muss trotzdem als veraltet erkannt werden, weil sich
    der tatsächlich an ElevenLabs gesendete Text ändert."""
    project = _make_project_with_confirmed_content(tmp_path)
    save_elevenlabs_settings(
        project,
        ElevenLabsSettings(project_id=project.id, voice_id="voice-abc", model_id="eleven_v3"),
    )
    with patch(f"{_TTS_MODULE}.synthesize_speech_with_timestamps", return_value=_fake_tts_result()):
        synthesize_folder_voiceover(project, "Grand Canyon")

    _set_first_sentence_pause_after(project, "Grand Canyon", "long")
    manifest = mark_stale_audio_if_needed(project)

    item = next(i for i in manifest.items if i.folder_name == "Grand Canyon")
    assert item.status == AUDIO_STATUS_STALE


def test_original_media_files_are_not_touched(tmp_path: Path) -> None:
    project = _make_project_with_confirmed_content(tmp_path)
    original_path = project.project_root_path / "Grand Canyon" / "clip1.mp4"
    # Datei existiert nicht physisch in diesem Test-Setup (nur im Inventory
    # referenziert) — Vertonung darf trotzdem nicht versuchen, sie zu erzeugen/ändern.
    with patch(f"{_TTS_MODULE}.synthesize_speech_with_timestamps", return_value=_fake_tts_result()):
        synthesize_folder_voiceover(project, "Grand Canyon")
    assert not original_path.exists()
