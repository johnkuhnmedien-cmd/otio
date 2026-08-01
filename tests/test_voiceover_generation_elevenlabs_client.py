"""Phase 6: ElevenLabs-Settings-Service und TTS-Client."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from otio_app.models import Project, ProjectMode
from otio_app.project_layout import get_elevenlabs_settings_path, get_voiceover_generation_dir
from otio_app.services.voiceover_generation.elevenlabs_client import (
    ElevenLabsTtsError,
    audio_extension_for_output_format,
    build_tts_request_metadata,
    is_elevenlabs_configured,
    synthesize_speech_with_timestamps,
)
from otio_app.services.voiceover_generation.elevenlabs_settings_service import (
    default_elevenlabs_settings,
    load_elevenlabs_settings,
    save_elevenlabs_settings,
)
from otio_app.services.voiceover_generation.models import ElevenLabsSettings

_CLIENT_MODULE = "otio_app.services.voiceover_generation.elevenlabs_client"


def _make_project(tmp_path: Path) -> Project:
    project_root = tmp_path / "USA"
    project_root.mkdir()
    return Project(
        id="elevenlabs-project",
        name="ElevenLabs Test",
        project_root=str(project_root),
        work_dir=str(project_root / "_otio"),
        project_mode=ProjectMode.WITHOUT_VOICEOVER,
        asset_subdir_names=["Grand Canyon"],
        selected_asset_subdirs=["Grand Canyon"],
    )


# --- Settings ---


def test_default_settings_have_expected_values(tmp_path: Path) -> None:
    project = _make_project(tmp_path)
    settings = default_elevenlabs_settings(project)
    assert settings.model_id == "eleven_multilingual_v2"
    assert settings.output_format == "wav_48000"
    assert settings.stability == 0.5
    assert settings.similarity_boost == 0.75
    assert settings.style == 0.0
    assert settings.use_speaker_boost is True
    assert settings.speed == 1.0
    assert settings.language_code == ""
    assert settings.voice_id == ""


def test_save_and_load_settings_roundtrip(tmp_path: Path) -> None:
    project = _make_project(tmp_path)
    settings = ElevenLabsSettings(
        project_id=project.id, voice_id="voice-abc", model_id="eleven_turbo_v2_5",
        stability=0.3, language_code="de",
    )
    save_elevenlabs_settings(project, settings)

    loaded = load_elevenlabs_settings(project)
    assert loaded.voice_id == "voice-abc"
    assert loaded.model_id == "eleven_turbo_v2_5"
    assert loaded.stability == 0.3
    assert loaded.language_code == "de"

    path = get_elevenlabs_settings_path(project.language_work_dir_path)
    assert path.is_file()
    assert path.is_relative_to(get_voiceover_generation_dir(project.language_work_dir_path))


def test_load_settings_returns_default_when_missing(tmp_path: Path) -> None:
    project = _make_project(tmp_path)
    settings = load_elevenlabs_settings(project)
    assert settings.voice_id == ""


def test_settings_file_never_contains_api_key(tmp_path: Path) -> None:
    project = _make_project(tmp_path)
    save_elevenlabs_settings(project, ElevenLabsSettings(project_id=project.id, voice_id="voice-abc"))
    path = get_elevenlabs_settings_path(project.language_work_dir_path)
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert "api_key" not in json.dumps(payload).lower()
    assert "xi-api-key" not in json.dumps(payload).lower()


# --- API-Key-Status ---


def test_is_elevenlabs_configured_true_when_key_set(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ELEVENLABS_API_KEY", "sk_test_key")
    assert is_elevenlabs_configured() is True


def test_is_elevenlabs_configured_false_when_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ELEVENLABS_API_KEY", raising=False)
    assert is_elevenlabs_configured() is False


# --- audio_extension_for_output_format ---


def test_audio_extension_for_mp3_format() -> None:
    ext, uncertain = audio_extension_for_output_format("mp3_44100_128")
    assert ext == ".mp3"
    assert uncertain is False


def test_audio_extension_for_wav_48000_format() -> None:
    ext, uncertain = audio_extension_for_output_format("wav_48000")
    assert ext == ".wav"
    assert uncertain is False


def test_audio_extension_for_pcm_format() -> None:
    ext, uncertain = audio_extension_for_output_format("pcm_16000")
    assert ext == ".wav"
    assert uncertain is False


def test_audio_extension_for_unknown_format_is_uncertain() -> None:
    ext, uncertain = audio_extension_for_output_format("some_future_format")
    assert ext == ".mp3"
    assert uncertain is True


# --- Request-Metadaten ohne Key-Leak ---


def test_build_tts_request_metadata_never_contains_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ELEVENLABS_API_KEY", "sk_super_secret_leak_test")
    settings = ElevenLabsSettings(project_id="p1", voice_id="voice-abc")
    metadata = build_tts_request_metadata("Hallo Welt", settings)
    serialized = json.dumps(metadata)
    assert "sk_super_secret_leak_test" not in serialized
    assert "xi-api-key" not in serialized.lower()
    assert metadata["text_length"] == len("Hallo Welt")


def test_build_tts_request_metadata_omits_empty_language_code() -> None:
    settings = ElevenLabsSettings(project_id="p1", voice_id="voice-abc", language_code="")
    metadata = build_tts_request_metadata("Text", settings)
    assert "language_code" not in metadata


def test_build_tts_request_metadata_includes_language_code_when_set() -> None:
    settings = ElevenLabsSettings(project_id="p1", voice_id="voice-abc", language_code="de")
    metadata = build_tts_request_metadata("Text", settings)
    assert metadata["language_code"] == "de"


# --- synthesize_speech_with_timestamps ---


def _sample_response_payload() -> dict:
    import base64

    return {
        "audio_base64": base64.b64encode(b"FAKE_MP3_BYTES").decode("ascii"),
        "alignment": {
            "characters": ["H", "i"],
            "character_start_times_seconds": [0.0, 0.1],
            "character_end_times_seconds": [0.1, 0.2],
        },
        "normalized_alignment": {
            "characters": ["H", "i"],
            "character_start_times_seconds": [0.0, 0.1],
            "character_end_times_seconds": [0.1, 0.2],
        },
    }


def test_synthesize_raises_when_api_key_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ELEVENLABS_API_KEY", raising=False)
    settings = ElevenLabsSettings(project_id="p1", voice_id="voice-abc")
    with pytest.raises(ElevenLabsTtsError, match="ELEVENLABS_API_KEY"):
        synthesize_speech_with_timestamps("Text", settings)


def test_synthesize_raises_when_voice_id_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ELEVENLABS_API_KEY", "sk_test")
    settings = ElevenLabsSettings(project_id="p1", voice_id="")
    with pytest.raises(ElevenLabsTtsError, match="Voice-ID"):
        synthesize_speech_with_timestamps("Text", settings)


def test_synthesize_parses_audio_and_alignment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ELEVENLABS_API_KEY", "sk_test_key")
    settings = ElevenLabsSettings(project_id="p1", voice_id="voice-abc")

    mock_response = MagicMock(status_code=200)
    mock_response.json.return_value = _sample_response_payload()

    with patch(f"{_CLIENT_MODULE}.requests.post", return_value=mock_response) as mock_post:
        result = synthesize_speech_with_timestamps("Hi", settings)

    assert result.audio_bytes == b"FAKE_MP3_BYTES"
    assert result.alignment["characters"] == ["H", "i"]
    assert result.normalized_alignment["characters"] == ["H", "i"]
    call_kwargs = mock_post.call_args.kwargs
    assert call_kwargs["headers"]["xi-api-key"] == "sk_test_key"
    assert "with-timestamps" in mock_post.call_args.args[0]


def test_synthesize_error_never_contains_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ELEVENLABS_API_KEY", "sk_super_secret_error_test")
    settings = ElevenLabsSettings(project_id="p1", voice_id="voice-abc")

    mock_response = MagicMock(status_code=401, text="Unauthorized: invalid key")
    with patch(f"{_CLIENT_MODULE}.requests.post", return_value=mock_response):
        with pytest.raises(ElevenLabsTtsError) as exc_info:
            synthesize_speech_with_timestamps("Text", settings)

    assert "sk_super_secret_error_test" not in str(exc_info.value)


def test_synthesize_raises_when_no_audio_in_response(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ELEVENLABS_API_KEY", "sk_test")
    settings = ElevenLabsSettings(project_id="p1", voice_id="voice-abc")

    mock_response = MagicMock(status_code=200)
    mock_response.json.return_value = {"alignment": {}, "normalized_alignment": {}}
    with patch(f"{_CLIENT_MODULE}.requests.post", return_value=mock_response):
        with pytest.raises(ElevenLabsTtsError, match="audio_base64"):
            synthesize_speech_with_timestamps("Text", settings)


def test_synthesize_response_metadata_excludes_audio_base64(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ELEVENLABS_API_KEY", "sk_test")
    settings = ElevenLabsSettings(project_id="p1", voice_id="voice-abc")

    mock_response = MagicMock(status_code=200)
    mock_response.json.return_value = _sample_response_payload()
    with patch(f"{_CLIENT_MODULE}.requests.post", return_value=mock_response):
        result = synthesize_speech_with_timestamps("Hi", settings)

    assert "audio_base64" not in result.response_metadata
    assert "alignment" not in result.response_metadata
