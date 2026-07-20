"""Enhanced Audio-Tab: ElevenLabs-Settings sichtbar + Voice-ID-Gate."""

from __future__ import annotations

from pathlib import Path

import pytest

from otio_app.defaults import DEFAULT_ENHANCED_WORK_SUBDIR
from otio_app.models import Project, ProjectMode
from otio_app.project_layout import get_elevenlabs_settings_path
from otio_app.services.voiceover_generation.elevenlabs_settings_service import (
    load_elevenlabs_settings,
    save_elevenlabs_settings,
)
from otio_app.services.voiceover_generation.models import ElevenLabsSettings
from otio_app.services.without_voiceover_enhanced.models import (
    EnhancedScriptDocument,
    ScriptSegment,
    VisualIntent,
)
from otio_app.services.without_voiceover_enhanced.script_lock_service import (
    lock_script,
    save_script_draft,
)
from otio_app.ui.voiceover_generation.elevenlabs_settings_ui import voice_id_is_set
from otio_app.ui.without_voiceover_enhanced.audio_tab import render_enhanced_audio_page


def _make_project(tmp_path: Path) -> Project:
    root = tmp_path / "USA"
    work = root / DEFAULT_ENHANCED_WORK_SUBDIR
    work.mkdir(parents=True)
    (root / "Antelope Canyon").mkdir()
    return Project(
        id="enh-audio-ui",
        name="Enhanced Audio UI",
        project_root=str(root),
        work_dir=str(work),
        project_mode=ProjectMode.WITHOUT_VOICEOVER_ENHANCED,
        language="en",
        asset_subdir_names=["Antelope Canyon"],
        selected_asset_subdirs=["Antelope Canyon"],
    )


def _patch_project_selector(project: Project, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("otio_app.ui.project_context.list_projects", lambda: [project])
    monkeypatch.setattr(
        "otio_app.ui.project_context.get_project_by_id",
        lambda project_id: project if project_id == project.id else None,
    )
    monkeypatch.setattr(
        "streamlit.session_state",
        {"active_project_id": project.id},
        raising=False,
    )


def _lock_minimal_script(project: Project) -> None:
    draft = EnhancedScriptDocument(
        narration_full="There's a place in the American Southwest.",
        segments=[
            ScriptSegment(
                segment_id="Antelope_Canyon_segment_001",
                text="There's a place in the American Southwest.",
                sequence_index=1,
                semantic_function="opener",
                folder_name="Antelope Canyon",
                folder_order_index=1,
            )
        ],
        visual_intents=[
            VisualIntent(intent_id="intent_001", description="canyon light")
        ],
    )
    save_script_draft(project, draft)
    lock_script(project)


def test_settings_form_available_without_locked_script(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = _make_project(tmp_path)
    monkeypatch.delenv("ELEVENLABS_API_KEY", raising=False)
    _patch_project_selector(project, monkeypatch)

    render_enhanced_audio_page()  # Settings müssen auch ohne Script Lock erreichbar sein

    assert not voice_id_is_set(project)


def test_voice_id_persists_under_enhanced_work_dir(tmp_path: Path) -> None:
    project = _make_project(tmp_path)
    save_elevenlabs_settings(
        project,
        ElevenLabsSettings(
            project_id=project.id,
            voice_id="voice-abc123",
            model_id="eleven_multilingual_v2",
        ),
    )

    path = get_elevenlabs_settings_path(project.language_work_dir_path)
    assert DEFAULT_ENHANCED_WORK_SUBDIR in path.parts
    loaded = load_elevenlabs_settings(project)
    assert loaded.voice_id == "voice-abc123"
    assert voice_id_is_set(project)


def test_page_renders_with_locked_script_and_missing_voice_id(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = _make_project(tmp_path)
    _lock_minimal_script(project)
    save_elevenlabs_settings(
        project, ElevenLabsSettings(project_id=project.id, voice_id="")
    )
    monkeypatch.setenv("ELEVENLABS_API_KEY", "sk_test")
    _patch_project_selector(project, monkeypatch)

    render_enhanced_audio_page()  # darf nicht werfen; Vertonen bleibt gated

    assert not voice_id_is_set(project)


def test_page_renders_when_voice_id_configured(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = _make_project(tmp_path)
    _lock_minimal_script(project)
    save_elevenlabs_settings(
        project,
        ElevenLabsSettings(project_id=project.id, voice_id="voice-ready"),
    )
    monkeypatch.setenv("ELEVENLABS_API_KEY", "sk_test")
    _patch_project_selector(project, monkeypatch)

    render_enhanced_audio_page()
    assert voice_id_is_set(project)
