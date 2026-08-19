"""Phase 2: Style-Profile-Erzeugung via LLM inkl. Traceability."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from otio_app.models import Project, ProjectMode
from otio_app.project_layout import get_llm_runs_dir, get_voiceover_style_profile_path
from otio_app.services.plan_llm_client import PlanLlmResponse
from otio_app.services.voiceover_generation.llm_trace_service import (
    STATUS_FAIL,
    STATUS_PARSE_FAILED,
    STATUS_PASS,
)
from otio_app.services.voiceover_generation.models import ProjectBrief, VoiceoverStyleReferences
from otio_app.services.voiceover_generation.style_profile_service import (
    build_style_profile,
    load_style_profile,
    parse_style_profile_response,
    save_style_profile,
)
from otio_app.services.voiceover_generation.models import VoiceoverStyleProfile


def _make_project(tmp_path: Path) -> Project:
    project_root = tmp_path / "USA"
    project_root.mkdir()
    return Project(
        id="style-profile-project",
        name="Style Profile Test",
        project_root=str(project_root),
        work_dir=str(project_root / "_otio"),
        project_mode=ProjectMode.WITHOUT_VOICEOVER,
        asset_subdir_names=["Grand Canyon"],
        selected_asset_subdirs=["Grand Canyon"],
    )


def _brief() -> ProjectBrief:
    return ProjectBrief(project_id="style-profile-project", video_title="Test", language="DE")


def _refs() -> VoiceoverStyleReferences:
    return VoiceoverStyleReferences(
        project_id="style-profile-project",
        intro_reference_texts=["Ein Ort voller Geheimnisse."],
    )


VALID_RESPONSE_JSON = json.dumps(
    {
        "language": "DE",
        "overall_tone": "calm, cinematic",
        "narration_style": "third person",
        "sentence_length": "medium",
        "pacing": "slow",
        "imagery_style": "sensory metaphors",
        "intro_hook_style": "open question",
        "segment_style": "descriptive",
        "do": ["use vivid imagery"],
        "dont": ["use clickbait"],
        "forbidden_phrases": ["breathtaking"],
        "avoid_copying_reference_text": True,
        "style_summary_for_prompts": "Calm, cinematic, third-person narration.",
    }
)


def test_parse_style_profile_response_valid_json() -> None:
    payload = parse_style_profile_response(VALID_RESPONSE_JSON)
    assert payload["overall_tone"] == "calm, cinematic"


def test_parse_style_profile_response_invalid_json_raises() -> None:
    with pytest.raises(ValueError):
        parse_style_profile_response("not json at all {{{")


def test_parse_style_profile_response_rejects_non_object() -> None:
    with pytest.raises(ValueError):
        parse_style_profile_response("[1, 2, 3]")


def test_load_style_profile_returns_none_when_missing(tmp_path: Path) -> None:
    project = _make_project(tmp_path)
    assert load_style_profile(project) is None


def test_save_and_load_style_profile_roundtrip(tmp_path: Path) -> None:
    project = _make_project(tmp_path)
    profile = VoiceoverStyleProfile(project_id="wrong-id", overall_tone="calm")
    saved = save_style_profile(project, profile)
    assert saved.project_id == project.id

    loaded = load_style_profile(project)
    assert loaded is not None
    assert loaded.overall_tone == "calm"


def test_build_style_profile_uses_provider_and_model_from_settings(tmp_path: Path) -> None:
    project = _make_project(tmp_path)
    fake_response = PlanLlmResponse(
        provider="anthropic",
        model="claude-sonnet-5",
        raw_text=VALID_RESPONSE_JSON,
        latency_ms=250,
        token_usage={"input_tokens": 100, "output_tokens": 50},
        resolved_model_id="anthropic:claude-sonnet-5",
    )
    with patch(
        "otio_app.services.voiceover_generation.style_profile_service.generate_plan_text_with_metadata",
        return_value=fake_response,
    ) as mock_generate:
        result = build_style_profile(
            project,
            project_brief=_brief(),
            style_references=_refs(),
            provider="anthropic",
            model="claude-sonnet-5",
        )

    assert result.status == STATUS_PASS
    call_kwargs = mock_generate.call_args.kwargs
    assert call_kwargs["model"] == "anthropic:claude-sonnet-5"


def test_build_style_profile_success_writes_style_profile_json(tmp_path: Path) -> None:
    project = _make_project(tmp_path)
    fake_response = PlanLlmResponse(
        provider="anthropic", model="claude-sonnet-5", raw_text=VALID_RESPONSE_JSON
    )
    with patch(
        "otio_app.services.voiceover_generation.style_profile_service.generate_plan_text_with_metadata",
        return_value=fake_response,
    ):
        result = build_style_profile(
            project,
            project_brief=_brief(),
            style_references=_refs(),
            provider="anthropic",
            model="claude-sonnet-5",
        )

    assert result.status == STATUS_PASS
    assert result.profile is not None
    assert result.profile.style_summary_for_prompts == "Calm, cinematic, third-person narration."
    assert result.profile.llm_run_id == result.llm_run_id

    path = get_voiceover_style_profile_path(project.language_work_dir_path)
    assert path.is_file()
    persisted = json.loads(path.read_text(encoding="utf-8"))
    assert persisted["llm_run_id"] == result.llm_run_id


def test_build_style_profile_invalid_json_does_not_overwrite_existing_profile(
    tmp_path: Path,
) -> None:
    project = _make_project(tmp_path)
    existing = save_style_profile(
        project, VoiceoverStyleProfile(project_id=project.id, overall_tone="ORIGINAL")
    )

    fake_response = PlanLlmResponse(
        provider="anthropic", model="claude-sonnet-5", raw_text="not valid json {{"
    )
    with patch(
        "otio_app.services.voiceover_generation.style_profile_service.generate_plan_text_with_metadata",
        return_value=fake_response,
    ):
        result = build_style_profile(
            project,
            project_brief=_brief(),
            style_references=_refs(),
            provider="anthropic",
            model="claude-sonnet-5",
        )

    assert result.status == STATUS_PARSE_FAILED
    assert result.profile is None

    # Bestehendes Profil bleibt unverändert.
    reloaded = load_style_profile(project)
    assert reloaded is not None
    assert reloaded.overall_tone == "ORIGINAL"
    assert reloaded.generated_at == existing.generated_at


def test_build_style_profile_missing_api_key_returns_fail_status(tmp_path: Path) -> None:
    project = _make_project(tmp_path)
    from otio_app.services.plan_llm_client import PlanLlmNotConfiguredError

    with patch(
        "otio_app.services.voiceover_generation.style_profile_service.generate_plan_text_with_metadata",
        side_effect=PlanLlmNotConfiguredError("ANTHROPIC_API_KEY ist nicht gesetzt."),
    ):
        result = build_style_profile(
            project,
            project_brief=_brief(),
            style_references=_refs(),
            provider="anthropic",
            model="claude-sonnet-5",
        )

    assert result.status == STATUS_FAIL
    assert result.profile is None
    assert "ANTHROPIC_API_KEY" in result.error


def test_build_style_profile_generic_llm_exception_returns_fail_status_instead_of_crashing(
    tmp_path: Path,
) -> None:
    """Jeder unerwartete Fehler aus dem LLM-/SDK-Aufruf (Netzwerk, Rate-Limit,
    SDK-Ausnahmen etc.) muss als kontrollierter FAIL-Status zurückkommen —
    nicht nur der eng gefasste PlanLlmNotConfiguredError-Fall. Sonst crasht
    die Streamlit-Seite mit einer rohen Ausnahme."""
    project = _make_project(tmp_path)
    with patch(
        "otio_app.services.voiceover_generation.style_profile_service.generate_plan_text_with_metadata",
        side_effect=ConnectionError("Verbindung zum LLM-Provider fehlgeschlagen."),
    ):
        result = build_style_profile(
            project,
            project_brief=_brief(),
            style_references=_refs(),
            provider="anthropic",
            model="claude-sonnet-5",
        )

    assert result.status == STATUS_FAIL
    assert result.profile is None
    assert "Verbindung zum LLM-Provider fehlgeschlagen" in result.error


def test_build_style_profile_creates_llm_run_artifacts(tmp_path: Path) -> None:
    project = _make_project(tmp_path)
    fake_response = PlanLlmResponse(
        provider="anthropic", model="claude-sonnet-5", raw_text=VALID_RESPONSE_JSON
    )
    with patch(
        "otio_app.services.voiceover_generation.style_profile_service.generate_plan_text_with_metadata",
        return_value=fake_response,
    ):
        result = build_style_profile(
            project,
            project_brief=_brief(),
            style_references=_refs(),
            provider="anthropic",
            model="claude-sonnet-5",
        )

    run_dir = get_llm_runs_dir(project.language_work_dir_path) / result.llm_run_id
    assert (run_dir / "prompt.txt").is_file()
    assert (run_dir / "raw_llm_response.json").is_file()
    assert (run_dir / "parsed_llm_response.json").is_file()
    assert (run_dir / "llm_request_manifest.json").is_file()

    manifest = json.loads((run_dir / "llm_request_manifest.json").read_text(encoding="utf-8"))
    assert manifest["provider"] == "anthropic"
    assert manifest["model"] == "claude-sonnet-5"
    assert manifest["prompt_hash"]
    assert manifest["status"] == STATUS_PASS


def test_build_style_profile_never_leaks_api_key_into_trace_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """End-to-end (nur der äußere Anthropic-Client gemockt): Der reale API-Key
    darf in keiner der geschriebenen Trace-Dateien auftauchen."""
    project = _make_project(tmp_path)
    secret_key = "sk-ant-SECRET-DO-NOT-LEAK-12345"
    monkeypatch.setenv("ANTHROPIC_API_KEY", secret_key)

    block = MagicMock(type="text", text=VALID_RESPONSE_JSON)
    mock_response = MagicMock(content=[block], usage=MagicMock(input_tokens=10, output_tokens=20))

    with patch("anthropic.Anthropic") as mock_anthropic:
        stream_cm = MagicMock()
        stream_cm.__enter__.return_value.get_final_message.return_value = mock_response
        stream_cm.__exit__.return_value = False
        mock_anthropic.return_value.messages.stream.return_value = stream_cm
        result = build_style_profile(
            project,
            project_brief=_brief(),
            style_references=_refs(),
            provider="anthropic",
            model="claude-sonnet-5",
        )

    assert result.status == STATUS_PASS
    run_dir = get_llm_runs_dir(project.language_work_dir_path) / result.llm_run_id
    for path in run_dir.rglob("*"):
        if path.is_file():
            content = path.read_text(encoding="utf-8")
            assert secret_key not in content, f"API-Key geleakt in {path}"

    style_profile_path = get_voiceover_style_profile_path(project.language_work_dir_path)
    assert secret_key not in style_profile_path.read_text(encoding="utf-8")
