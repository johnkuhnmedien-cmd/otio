"""Projekt bearbeiten must not crash Discovery shell with switch_page(analysen)."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from otio_app.models import ProjectMode
from otio_app.ui import routing
from otio_app.ui.navigation import ACTIVE_PROJECT_KEY, PAGE_ANALYSIS


class _FakeSt:
    def __init__(self) -> None:
        self.session_state: dict = {}
        self.switch_calls: list = []

    def switch_page(self, target):
        self.switch_calls.append(target)
        # Simulate Streamlit rejecting bare url_path strings that are not files.
        if isinstance(target, str) and target == "analysen":
            raise RuntimeError("Could not find page: analysen")


def test_activate_discovery_project_queues_overview_not_analysen(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = _FakeSt()
    monkeypatch.setattr(routing, "st", fake)
    monkeypatch.setattr(
        routing,
        "bind_active_discovery_project",
        lambda project_id, page_slug=None: fake.session_state.update(
            {
                ACTIVE_PROJECT_KEY: project_id,
                "_discovery_v2_route_page_slug": page_slug or "overview",
            }
        ),
    )
    project = SimpleNamespace(
        id="discovery-project-1",
        is_discovery_v2=True,
        project_mode=ProjectMode.DISCOVERY_V2,
    )
    routing.activate_project_for_editing(project)
    assert fake.session_state[ACTIVE_PROJECT_KEY] == "discovery-project-1"
    assert fake.session_state[routing.PENDING_SWITCH_URL_KEY] == "discovery-v2"
    assert fake.session_state["workbench_project_id"] == "discovery-project-1"


def test_activate_classic_project_queues_analysen(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = _FakeSt()
    monkeypatch.setattr(routing, "st", fake)
    project = SimpleNamespace(
        id="classic-project-1",
        is_discovery_v2=False,
        project_mode=ProjectMode.WITH_VOICEOVER,
    )
    routing.activate_project_for_editing(project)
    assert fake.session_state[ACTIVE_PROJECT_KEY] == "classic-project-1"
    assert fake.session_state[routing.PENDING_SWITCH_URL_KEY] == "analysen"
    assert fake.session_state["sidebar_nav"] == PAGE_ANALYSIS


def test_consume_pending_switch_uses_page_object_not_bare_analysen_string(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = _FakeSt()
    fake.session_state[routing.PENDING_SWITCH_URL_KEY] = "discovery-v2"
    monkeypatch.setattr(routing, "st", fake)
    page = SimpleNamespace(url_path="discovery-v2")
    other = SimpleNamespace(url_path="projekte")
    routing._consume_pending_page_switch([other, page])
    assert fake.switch_calls == [page]
    assert routing.PENDING_SWITCH_URL_KEY not in fake.session_state


def test_consume_pending_switch_does_not_raise_when_analysen_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = _FakeSt()
    fake.session_state[routing.PENDING_SWITCH_URL_KEY] = "analysen"
    monkeypatch.setattr(routing, "st", fake)
    # Discovery pages only — analysen absent (the real crash scenario).
    pages = [SimpleNamespace(url_path="projekte"), SimpleNamespace(url_path="discovery-v2")]
    routing._consume_pending_page_switch(pages)
    # Falls back to string switch which is caught; no exception escapes.
    assert routing.PENDING_SWITCH_URL_KEY not in fake.session_state


def test_app_project_list_does_not_hardcode_switch_page_analysen() -> None:
    source = open("app.py", encoding="utf-8").read()
    executable = [
        line
        for line in source.splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]
    assert all('st.switch_page("analysen")' not in line for line in executable)
    assert "activate_project_for_editing(project)" in source
