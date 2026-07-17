from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

from otio_app.discovery_v2.application.voice_generation_service import NarrationView
from otio_app.discovery_v2.domain.narration import (
    NarrationProjectState,
    VoiceOutputProfile,
    VoiceProfile,
    VoiceProfileStatus,
)
from otio_app.discovery_v2.ui import narration_page
from otio_app.models import Project, ProjectMode


class _FakeStreamlit:
    def __init__(self) -> None:
        self.buttons: list[str] = []
        self.messages: list[str] = []

    def title(self, text):
        self.messages.append(str(text))

    def info(self, text):
        self.messages.append(str(text))

    def warning(self, text):
        self.messages.append(str(text))

    def success(self, text):
        self.messages.append(str(text))

    def caption(self, text):
        self.messages.append(str(text))

    def subheader(self, text):
        self.messages.append(str(text))

    def markdown(self, text):
        self.messages.append(str(text))

    def write(self, text):
        self.messages.append(str(text))

    def dataframe(self, *args, **kwargs):
        return None

    def button(self, label, **kwargs):
        self.buttons.append(label)
        return False


def _project(tmp_path):
    root = tmp_path / "Project"
    root.mkdir()
    return Project(
        id="project-1",
        name="UI",
        project_root=str(root),
        work_dir=str(root / "_otio"),
        project_mode=ProjectMode.DISCOVERY_V2,
        asset_subdir_names=["Media"],
        selected_asset_subdirs=["Media"],
    )


def test_smoke_h_narration_ui_double_render_has_no_gateway_audio_or_job_start(
    tmp_path, monkeypatch
) -> None:
    fake_st = _FakeStreamlit()
    project = _project(tmp_path)
    calls = {"view": 0, "voice": 0, "pause": 0, "timing": 0}
    profile = VoiceProfile(
        voice_profile_id="profile-1",
        project_id=project.id,
        language="de",
        output_profile=VoiceOutputProfile(),
        status=VoiceProfileStatus.ACTIVE,
        created_at=datetime.now(timezone.utc),
    )
    lock = SimpleNamespace(
        lock_id="lock-1",
        script_id="script-1",
        script_version=1,
        lock_fingerprint="fp",
        status=SimpleNamespace(value="locked"),
    )
    view = NarrationView(
        ok=True,
        state=NarrationProjectState(project_id=project.id, updated_at=datetime.now(timezone.utc)),
        effective_lock=lock,
        voice_profile=profile,
        can_start_voice=True,
    )

    def fake_view(arg):
        assert arg == project
        calls["view"] += 1
        return view

    monkeypatch.setattr(narration_page, "st", fake_st)
    monkeypatch.setattr(narration_page, "active_discovery_project", lambda: project)
    monkeypatch.setattr(narration_page, "get_narration_view", fake_view)
    monkeypatch.setattr(
        narration_page,
        "start_voice_generation_run",
        lambda *args, **kwargs: calls.__setitem__("voice", calls["voice"] + 1),
    )
    monkeypatch.setattr(
        narration_page,
        "start_pause_direction_run",
        lambda *args, **kwargs: calls.__setitem__("pause", calls["pause"] + 1),
    )
    monkeypatch.setattr(
        narration_page,
        "start_narration_timing_run",
        lambda *args, **kwargs: calls.__setitem__("timing", calls["timing"] + 1),
    )
    narration_page.render_discovery_narration_page()
    narration_page.render_discovery_narration_page()
    assert calls == {"view": 2, "voice": 0, "pause": 0, "timing": 0}
    assert "Voice erzeugen" in fake_st.buttons
    assert "Pausenregie erzeugen" in fake_st.buttons
    assert "Narration Timing aufloesen" in fake_st.buttons
    assert any("Fake-Voice" in message for message in fake_st.messages)
