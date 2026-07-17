from __future__ import annotations

from types import SimpleNamespace

from otio_app.discovery_v2.application.visual_edit_plan_service import VisualEditView
from otio_app.discovery_v2.ui import visual_edit_page
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


def test_smoke_h_visual_edit_ui_double_render_has_no_gateway_job_or_otio(tmp_path, monkeypatch) -> None:
    fake_st = _FakeStreamlit()
    project = _project(tmp_path)
    calls = {"view": 0, "plan": 0, "humanity": 0, "feasibility": 0, "propose": 0, "repair": 0}
    gate = SimpleNamespace(
        script_lock_id="lock-1",
        narration_timeline_id="timeline-1",
        total_duration_seconds=10.0,
        total_frames=250,
        input_fingerprint="fp",
    )
    view = VisualEditView(ok=True, input_gate=gate, can_start_plan=True)

    def fake_view(arg):
        assert arg == project
        calls["view"] += 1
        return view

    monkeypatch.setattr(visual_edit_page, "st", fake_st)
    monkeypatch.setattr(visual_edit_page, "active_discovery_project", lambda: project)
    monkeypatch.setattr(visual_edit_page, "get_visual_edit_view", fake_view)
    monkeypatch.setattr(visual_edit_page, "start_visual_edit_plan_run", lambda *a, **k: calls.__setitem__("plan", calls["plan"] + 1))
    monkeypatch.setattr(visual_edit_page, "start_humanity_review_run", lambda *a, **k: calls.__setitem__("humanity", calls["humanity"] + 1))
    monkeypatch.setattr(visual_edit_page, "start_feasibility_check_run", lambda *a, **k: calls.__setitem__("feasibility", calls["feasibility"] + 1))
    monkeypatch.setattr(visual_edit_page, "propose_editorial_repairs", lambda *a, **k: calls.__setitem__("propose", calls["propose"] + 1))
    monkeypatch.setattr(visual_edit_page, "apply_selected_repair_proposals", lambda *a, **k: calls.__setitem__("repair", calls["repair"] + 1))

    visual_edit_page.render_discovery_visual_edit_page()
    visual_edit_page.render_discovery_visual_edit_page()

    assert calls == {"view": 2, "plan": 0, "humanity": 0, "feasibility": 0, "propose": 0, "repair": 0}
    assert "Visual Edit Plan erzeugen" in fake_st.buttons
    assert "Humanity & Authenticity pruefen" in fake_st.buttons
    assert "Technische Machbarkeit pruefen" in fake_st.buttons
    assert "Ausgewaehlte Reparaturen anwenden" in fake_st.buttons
