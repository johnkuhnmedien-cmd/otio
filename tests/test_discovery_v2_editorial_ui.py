"""Phase 9 Editorial UI wiring and no-I/O rendering tests."""

from __future__ import annotations

from types import SimpleNamespace

from otio_app.discovery_v2.application.editorial_service import EditorialView
from otio_app.discovery_v2.ui import editorial_page
from otio_app.models import Project, ProjectMode
from otio_app.ui.navigation import (
    DISCOVERY_V2_WORKFLOW_PAGES,
    PAGE_DISCOVERY_EDITORIAL,
    PAGE_DISCOVERY_SETTINGS,
)


class _FakeContext:
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


class _FakeStreamlit:
    def __init__(self) -> None:
        self.buttons: list[str] = []
        self.messages: list[str] = []
        self.session_state: dict = {}

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

    def code(self, *args, **kwargs):
        return None

    def dataframe(self, *args, **kwargs):
        return None

    def form(self, *args, **kwargs):
        return _FakeContext()

    def container(self):
        return _FakeContext()

    def expander(self, *args, **kwargs):
        return _FakeContext()

    def columns(self, count):
        return [_FakeContext() for _ in range(count)]

    def text_input(self, label, value="", **kwargs):
        return value

    def text_area(self, label, value="", **kwargs):
        return value

    def number_input(self, label, value=0, **kwargs):
        return value

    def checkbox(self, label, value=False, **kwargs):
        return value

    def form_submit_button(self, label, **kwargs):
        self.buttons.append(label)
        return False

    def button(self, label, **kwargs):
        self.buttons.append(label)
        return False

    def rerun(self):
        return None


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


def test_editorial_ui_renders_without_starting_jobs_or_gateway(tmp_path, monkeypatch) -> None:
    fake_st = _FakeStreamlit()
    project = _project(tmp_path)
    monkeypatch.setattr(editorial_page, "st", fake_st)
    monkeypatch.setattr(editorial_page, "active_discovery_project", lambda: project)
    monkeypatch.setattr(
        editorial_page,
        "get_editorial_view",
        lambda p: EditorialView(ok=True, can_start_narrative=True),
    )
    monkeypatch.setattr(
        editorial_page,
        "get_supplementation_view",
        lambda p: SimpleNamespace(
            ok=True,
            gaps=[],
            candidates_by_gap={},
            script_locks=[],
            active_run=None,
            message=None,
        ),
    )
    monkeypatch.setattr(
        editorial_page,
        "resolve_editorial_script_lock_gate",
        lambda p, **kwargs: SimpleNamespace(
            resolution=None,
            has_effective_current_lock=False,
            effective_lock=None,
            historical_locks=(),
            current_preview=SimpleNamespace(
                ok=False,
                lock_fingerprint=None,
                fingerprint_display=None,
                fulfilled_requirements=[],
                blocking_requirements=["aktuelles Script"],
                blockers=["script_missing"],
            ),
            current_fingerprint=None,
            required_risk_keys=(),
            confirmed_risk_keys=(),
            confirmations_complete=False,
            can_create_lock=False,
            blocking_reason_codes=(),
            diagnostics=[],
        ),
    )
    for name in (
        "start_narrative_run",
        "start_script_run",
        "start_structure_run",
        "start_coverage_run",
        "save_project_brief",
        "save_user_script_edit",
        "select_hook",
        "create_script_lock",
        "accept_gap_unresolved",
    ):
        monkeypatch.setattr(
            editorial_page,
            name,
            lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("UI auto-called service mutation")),
        )
    editorial_page.render_discovery_editorial_page()
    assert "Narrative erzeugen" in fake_st.buttons
    assert any("fake-editorial-v1" in message for message in fake_st.messages)


def test_editorial_navigation_is_before_settings() -> None:
    assert PAGE_DISCOVERY_EDITORIAL in DISCOVERY_V2_WORKFLOW_PAGES
    assert DISCOVERY_V2_WORKFLOW_PAGES.index(PAGE_DISCOVERY_EDITORIAL) < DISCOVERY_V2_WORKFLOW_PAGES.index(PAGE_DISCOVERY_SETTINGS)
