"""Phase 8A/8B: Assetanalyse-UI — No media I/O, explicit local prepare."""

from __future__ import annotations

import ast
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from otio_app.discovery_v2.application.analysis_prepare_service import (
    AnalysisPrepareEligibilityItemView,
    AnalysisPrepareStatusView,
)
from otio_app.discovery_v2.domain.asset_analysis import AnalysisEligibility
from otio_app.discovery_v2.ui import asset_analysis_page as analysis_ui
from otio_app.ui.navigation import (
    DISCOVERY_V2_NAVIGATION_OPTIONS,
    NAVIGATION_OPTIONS,
    PAGE_DISCOVERY_ASSET_ANALYSIS,
    VOICEOVER_GEN_NAVIGATION_OPTIONS,
)
import otio_app.ui.routing as routing
from otio_app.models import ProjectMode


class _FakeStreamlit:
    def __init__(self, *, clicked: bool = False) -> None:
        self.clicked = clicked
        self.titles: list[str] = []
        self.infos: list[str] = []
        self.warnings: list[str] = []
        self.successes: list[str] = []
        self.captions: list[str] = []
        self.markdowns: list[str] = []
        self.dataframes: list[Any] = []
        self.buttons: list[dict[str, Any]] = []
        self.session_state: dict = {}

    def title(self, text: str) -> None:
        self.titles.append(text)

    def subheader(self, text: str) -> None:
        self.captions.append(f"## {text}")

    def info(self, text: str) -> None:
        self.infos.append(text)

    def warning(self, text: str) -> None:
        self.warnings.append(text)

    def success(self, text: str) -> None:
        self.successes.append(text)

    def caption(self, text: str) -> None:
        self.captions.append(text)

    def markdown(self, text: str) -> None:
        self.markdowns.append(text)

    def write(self, *args: Any, **kwargs: Any) -> None:
        self.captions.append(str(args))

    def dataframe(self, data: Any, **kwargs: Any) -> None:
        self.dataframes.append(data)

    def button(self, label: str, **kwargs: Any) -> bool:
        self.buttons.append({"label": label, **kwargs})
        return self.clicked


def test_navigation_assetanalyse_discovery_only() -> None:
    assert PAGE_DISCOVERY_ASSET_ANALYSIS == "Assetanalyse"
    assert PAGE_DISCOVERY_ASSET_ANALYSIS in DISCOVERY_V2_NAVIGATION_OPTIONS
    assert PAGE_DISCOVERY_ASSET_ANALYSIS not in NAVIGATION_OPTIONS
    assert PAGE_DISCOVERY_ASSET_ANALYSIS not in VOICEOVER_GEN_NAVIGATION_OPTIONS


def test_discovery_pages_include_assetanalyse(monkeypatch: pytest.MonkeyPatch) -> None:
    class _FakePage:
        def __init__(self, render_fn, *, title, url_path="", default=False):
            self.title = title
            self.url_path = url_path

    monkeypatch.setattr(routing.st, "Page", _FakePage)
    pages = routing._build_discovery_v2_pages(lambda: None, lambda: None)
    titles = [p.title for p in pages]
    assert "Assetanalyse" in titles
    assert titles.index("Media Intake") < titles.index("Assetanalyse")


def test_classic_and_without_vo_unchanged(monkeypatch: pytest.MonkeyPatch) -> None:
    class _FakePage:
        def __init__(self, render_fn, *, title, url_path="", default=False):
            self.title = title

    monkeypatch.setattr(routing.st, "Page", _FakePage)
    with_titles = [p.title for p in routing._build_with_voiceover_pages(lambda: None, lambda: None)]
    without_titles = [
        p.title for p in routing._build_without_voiceover_pages(lambda: None, lambda: None)
    ]
    assert "Assetanalyse" not in with_titles
    assert "Assetanalyse" not in without_titles
    assert "② Zuordnung" in with_titles
    assert "① Project Brief" in without_titles


def test_ui_source_has_no_media_or_api_io() -> None:
    path = Path("otio_app/discovery_v2/ui/asset_analysis_page.py")
    source = path.read_text(encoding="utf-8")
    for needle in (
        "ffmpeg",
        "ffprobe",
        "Image.open",
        "compute_sha256",
        "stat(",
        "mtime",
        "requests.",
        "openai",
        "gemini",
        "insert_analysis_run",
        "subprocess",
        "Provider",
        "model_id",
    ):
        assert needle not in source
    assert "start_analysis_prepare" in source
    assert "start_model_analysis" in source
    tree = ast.parse(source)
    calls = [
        n.func.id
        for n in ast.walk(tree)
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
    ]
    assert "open" not in calls


@pytest.mark.parametrize(
    "clicked,can_start,expected_starts",
    [(False, True, 0), (True, False, 0), (True, True, 2)],
)
def test_render_explicit_prepare_button_no_autostart(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    clicked: bool,
    can_start: bool,
    expected_starts: int,
) -> None:
    fake_st = _FakeStreamlit(clicked=clicked)
    monkeypatch.setattr(analysis_ui, "st", fake_st)

    project = SimpleNamespace(
        id="p1",
        project_mode=ProjectMode.DISCOVERY_V2,
        name="P",
        language="de",
        project_root_path=tmp_path,
    )
    monkeypatch.setattr(analysis_ui, "active_discovery_project", lambda: project)
    monkeypatch.setattr(analysis_ui, "_render_prepare_review", lambda *_args: None)

    view = AnalysisPrepareStatusView(
        ok=True,
        message=None,
        plan_id="plan-1",
        chain_ok=True,
        can_start=can_start,
        items=[
            AnalysisPrepareEligibilityItemView(
                eligibility=AnalysisEligibility(
                    asset_id="a1",
                    working_media_id="wm1",
                    eligible=True,
                    expected_processing_profile_version="copy-v1",
                    actual_processing_profile_version="copy-v1",
                    media_kind="video",
                    source_group="Florida",
                    source_relative_path="Florida/clip.mp4",
                    output_sha256="abcd" * 16,
                    display_name="clip.mp4",
                )
            )
        ],
    )
    monkeypatch.setattr(analysis_ui, "get_analysis_prepare_view", lambda _p: view)
    starts: list[tuple[Any, bool]] = []

    def _fake_start(project_arg: Any, *, sync: bool = False):
        starts.append((project_arg, sync))
        return SimpleNamespace(started=True, message="gestartet", run=None)

    monkeypatch.setattr(analysis_ui, "start_analysis_prepare", _fake_start)

    analysis_ui.render_discovery_asset_analysis_page()
    analysis_ui.render_discovery_asset_analysis_page()  # wiederholter Rerun

    assert fake_st.titles == ["Assetanalyse", "Assetanalyse"]
    assert any("keine Medien an externe Dienste" in info for info in fake_st.infos)
    assert fake_st.dataframes
    assert len(fake_st.buttons) == 2
    assert len(starts) == expected_starts
    if not can_start:
        assert all(button["disabled"] is True for button in fake_st.buttons)


def test_eligibility_service_source_no_media_io() -> None:
    source = Path(
        "otio_app/discovery_v2/application/asset_analysis_eligibility_service.py"
    ).read_text(encoding="utf-8")
    for needle in (
        "ffmpeg",
        "ffprobe",
        "Image.open",
        "compute_sha256",
        "subprocess",
        "openai",
        "gemini",
        "start_copy",
        "start_remux",
    ):
        assert needle not in source
