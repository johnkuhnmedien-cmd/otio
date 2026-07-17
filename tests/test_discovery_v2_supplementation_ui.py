"""Phase 10 supplementation UI wiring and no-provider-render tests."""

from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

from otio_app.discovery_v2.application.editorial_service import EditorialView
from otio_app.discovery_v2.application.supplementation_service import SupplementationView
from otio_app.discovery_v2.domain.supplementation import (
    CoverageGap,
    CoverageGapStatus,
    CoverageLevel,
)
from otio_app.discovery_v2.ui import editorial_page
from otio_app.models import Project, ProjectMode


class _FakeContext:
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


class _FakeStreamlit:
    def __init__(self) -> None:
        self.buttons: list[str] = []
        self.messages: list[str] = []
        self.checkboxes: list[tuple[str, bool]] = []

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

    def columns(self, count):
        return [_FakeContext() for _ in range(count)]

    def text_input(self, label, value="", **kwargs):
        return value

    def text_area(self, label, value="", **kwargs):
        return value

    def number_input(self, label, value=0, **kwargs):
        return value

    def form_submit_button(self, label, **kwargs):
        self.buttons.append(label)
        return False

    def button(self, label, **kwargs):
        self.buttons.append(label)
        return False

    def checkbox(self, label, value=False, **kwargs):
        self.checkboxes.append((label, value))
        return value


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


def test_supplementation_ui_renders_buttons_without_gateway_or_binary_preview(
    tmp_path, monkeypatch
) -> None:
    fake_st = _FakeStreamlit()
    project = _project(tmp_path)
    gap = CoverageGap(
        gap_id="gap-1",
        project_id=project.id,
        script_id="script-1",
        script_version=1,
        coverage_audit_id="coverage-1",
        visual_intent_id="intent-1",
        coverage_level=CoverageLevel.NOT_COVERED,
        status=CoverageGapStatus.OPEN,
        gap_version=1,
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )
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
        lambda p: SupplementationView(ok=True, gaps=[gap], can_start_supplementation=True),
    )
    for name in (
        "start_search_run",
        "start_candidate_validation_run",
        "record_candidate_decision",
        "materialize_gaps_from_current_coverage",
        "create_script_lock",
    ):
        monkeypatch.setattr(
            editorial_page,
            name,
            lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("UI auto-called mutation")),
        )
    monkeypatch.setattr(
        "otio_app.discovery_v2.adapters.stock_gateway.StockSearchGateway.search",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("gateway on render")),
    )

    editorial_page.render_discovery_editorial_page()

    assert "Ergaenzungskandidaten suchen" in fake_st.buttons
    assert "Skript fuer Voice und Timing sperren" in fake_st.buttons
    assert any("Fake-Ergaenzungskandidaten" in message for message in fake_st.messages)
    assert ("Skript fuer Voice und Timing sperren", False) in fake_st.checkboxes
