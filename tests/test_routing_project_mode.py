"""Regressionstest: Navigation-Seiten je Projektmodus.

Sichert zu, dass die bestehende "Projekt mit Voice-Over"-Navigation durch die
Einführung von project_mode nicht verändert wird, und dass "Projekt ohne
Voice-Over" die richtigen Ersatzseiten bekommt (② Zuordnung, ②½ Supplement
Assets, ③ Schnittplan werden ersetzt — nicht ergänzt).
"""

from __future__ import annotations

from dataclasses import dataclass
from types import SimpleNamespace
from typing import Callable

import pytest

import otio_app.ui.routing as routing
from otio_app.models import ProjectMode


@dataclass
class _FakePage:
    render_fn: Callable
    title: str
    url_path: str = ""
    default: bool = False


@pytest.fixture(autouse=True)
def _fake_streamlit_page(monkeypatch: pytest.MonkeyPatch) -> None:
    """Ersetzt st.Page durch eine einfache Datenklasse.

    Echte StreamlitPage-Objekte lösen ihre Attribute (title/url_path) erst
    innerhalb eines laufenden Scripts auf — außerhalb eines echten App-Runs
    lösen sie AttributeError aus. Der Fake macht die Rückgabe von
    _build_*_pages() direkt inspizierbar.
    """

    def _fake_page(render_fn, *, title, url_path="", default=False):
        return _FakePage(render_fn=render_fn, title=title, url_path=url_path, default=default)

    monkeypatch.setattr(routing.st, "Page", _fake_page)


def _noop() -> None:
    return None


def test_with_voiceover_pages_unchanged() -> None:
    """Exakt die bisherige Seitenliste — nichts wurde umbenannt oder umsortiert."""
    pages = routing._build_with_voiceover_pages(_noop, _noop)
    titles = [page.title for page in pages]
    assert titles == [
        "Neues Projekt",
        "Gespeicherte Projekte",
        "⓪ Clean Media",
        "① Analysen",
        "② Zuordnung",
        "②½ Supplement Assets",
        "③ Schnittplan",
        "🔑 API-Schlüssel",
        "Systemstatus",
    ]


def test_with_voiceover_url_paths_unchanged() -> None:
    pages = routing._build_with_voiceover_pages(_noop, _noop)
    url_paths = [page.url_path for page in pages]
    assert url_paths == [
        "neues-projekt",
        "projekte",
        "clean-media",
        "analysen",
        "zuordnung",
        "supplement-assets",
        "schnittplan",
        "api-schluessel",
        "systemstatus",
    ]


def test_without_voiceover_pages_replace_mapping_supplement_editplan() -> None:
    pages = routing._build_without_voiceover_pages(_noop, _noop)
    titles = [page.title for page in pages]
    assert titles == [
        "Neues Projekt",
        "Gespeicherte Projekte",
        "⓪ Clean Media",
        "① Analysen",
        "① Project Brief",
        "② Style References",
        "③ Dramaturgie",
        "④ Folder Voice-overs",
        "⑤ Intro",
        "⑥ Audio / ElevenLabs",
        "⑦ Final Output",
        "⑧ Cut Plan",
        "🔑 API-Schlüssel",
        "Systemstatus",
    ]
    assert "② Zuordnung" not in titles
    assert "②½ Supplement Assets" not in titles
    assert "③ Schnittplan" not in titles


def test_without_voiceover_reuses_clean_media_and_analysis() -> None:
    """Clean Media und Analysen sind für beide Modi identisch (Wiederverwendung)."""
    with_pages = routing._build_with_voiceover_pages(_noop, _noop)
    without_pages = routing._build_without_voiceover_pages(_noop, _noop)
    with_titles = {page.title for page in with_pages}
    without_titles = {page.title for page in without_pages}
    shared = with_titles & without_titles
    assert shared == {
        "Neues Projekt",
        "Gespeicherte Projekte",
        "⓪ Clean Media",
        "① Analysen",
        "🔑 API-Schlüssel",
        "Systemstatus",
    }


def test_discovery_v2_pages_include_narration_after_editorial() -> None:
    pages = routing._build_discovery_v2_pages(_noop, _noop)
    titles = [page.title for page in pages]
    assert titles == [
        "Neues Projekt",
        "Gespeicherte Projekte",
        "Discovery V2 – Übersicht",
        "Medienbestand",
        "Technische Prüfung",
        "Media Intake",
        "Assetanalyse",
        "Editorial",
        "Narration",
        "Visual Edit",
        "Review & Export",
        "Projekteinstellungen",
        "🔑 API-Schlüssel",
        "Systemstatus",
    ]
    assert titles.index("Narration") == titles.index("Editorial") + 1
    assert titles.index("Visual Edit") == titles.index("Narration") + 1
    assert titles.index("Review & Export") == titles.index("Visual Edit") + 1


def test_active_project_mode_defaults_when_no_active_project(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(routing.st, "session_state", {})
    assert routing._active_project_mode() == ProjectMode.WITH_VOICEOVER


def test_active_project_mode_defaults_when_project_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(routing.st, "session_state", {"active_project_id": "missing"})
    monkeypatch.setattr(routing, "get_project_by_id", lambda project_id: None)
    assert routing._active_project_mode() == ProjectMode.WITH_VOICEOVER


def test_active_project_mode_reads_without_voiceover_project(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_project = SimpleNamespace(project_mode=ProjectMode.WITHOUT_VOICEOVER)
    monkeypatch.setattr(routing.st, "session_state", {"active_project_id": "p1"})
    monkeypatch.setattr(routing, "get_project_by_id", lambda project_id: fake_project)
    assert routing._active_project_mode() == ProjectMode.WITHOUT_VOICEOVER


def test_active_project_mode_reads_with_voiceover_project(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_project = SimpleNamespace(project_mode=ProjectMode.WITH_VOICEOVER)
    monkeypatch.setattr(routing.st, "session_state", {"active_project_id": "p1"})
    monkeypatch.setattr(routing, "get_project_by_id", lambda project_id: fake_project)
    assert routing._active_project_mode() == ProjectMode.WITH_VOICEOVER


class _FakeNavigation:
    def __init__(self, pages: list) -> None:
        self.pages = pages
        self.ran = False

    def run(self) -> None:
        self.ran = True


def test_run_app_navigation_dispatches_by_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    """run_app_navigation() muss je nach Modus den richtigen Seiten-Builder nutzen."""
    import contextlib

    captured: dict[str, list] = {}

    def _fake_navigation(pages, position="sidebar"):
        captured["pages"] = pages
        return _FakeNavigation(pages)

    monkeypatch.setattr(routing.st, "navigation", _fake_navigation)
    monkeypatch.setattr(routing.st, "sidebar", contextlib.nullcontext())
    monkeypatch.setattr(routing.st, "caption", lambda *_a, **_k: None)
    monkeypatch.setattr(routing, "render_activity_panel", lambda: None)
    monkeypatch.setattr(routing, "format_build_label", lambda: "test-build")

    # Kein aktives Projekt -> bestehender Workflow (Default).
    monkeypatch.setattr(routing.st, "session_state", {})
    routing.run_app_navigation(render_new_project=_noop, render_project_list=_noop)
    with_titles = [page.title for page in captured["pages"]]
    assert "② Zuordnung" in with_titles
    assert "Discovery V2 – Übersicht" not in with_titles

    # Aktives Projekt ohne Voice-Over -> neue Seitenliste.
    fake_project = SimpleNamespace(project_mode=ProjectMode.WITHOUT_VOICEOVER)
    monkeypatch.setattr(routing.st, "session_state", {"active_project_id": "p1"})
    monkeypatch.setattr(routing, "get_project_by_id", lambda project_id: fake_project)
    routing.run_app_navigation(render_new_project=_noop, render_project_list=_noop)
    without_titles = [page.title for page in captured["pages"]]
    assert "② Zuordnung" not in without_titles
    assert "① Project Brief" in without_titles
    assert "Discovery V2 – Übersicht" not in without_titles
