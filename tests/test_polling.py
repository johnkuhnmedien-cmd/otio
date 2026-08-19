"""Tests für selbst aktualisierenden Job-Fortschritt ohne verschlucktes st.rerun()."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

from otio_app.ui import polling


def test_poll_while_running_button_does_not_call_rerun(monkeypatch) -> None:
    session: dict = {}
    monkeypatch.setattr(polling, "is_shutting_down", lambda: False)
    monkeypatch.setattr(polling.st, "caption", lambda *a, **k: None)
    monkeypatch.setattr(polling.st, "button", lambda *a, **k: True)
    monkeypatch.setattr(polling.st, "session_state", session)
    monkeypatch.setattr(polling, "_running_job_tick", polling._running_job_tick_impl)
    rerun = MagicMock()
    monkeypatch.setattr(polling.st, "rerun", rerun)

    polling.poll_while_running(lambda: None, lambda: True, refresh_key="job_refresh")

    rerun.assert_not_called()
    assert session["job_refresh__tick"] == 1


def test_poll_while_running_skips_button_when_idle(monkeypatch) -> None:
    monkeypatch.setattr(polling, "is_shutting_down", lambda: False)
    monkeypatch.setattr(polling.st, "caption", lambda *a, **k: None)
    button = MagicMock(return_value=False)
    monkeypatch.setattr(polling.st, "button", button)

    polling.poll_while_running(lambda: None, lambda: False, refresh_key="idle")

    button.assert_not_called()


def test_running_job_tick_reruns_app_when_job_ends(monkeypatch) -> None:
    monkeypatch.setattr(polling, "is_shutting_down", lambda: False)
    rerun = MagicMock()
    monkeypatch.setattr(polling.st, "rerun", rerun)
    render = MagicMock()

    polling._running_job_tick_impl(render, lambda: False, "ended")

    rerun.assert_called_once()
    render.assert_not_called()


def test_fragment_rerun_every_falls_back_without_fragment_api(monkeypatch) -> None:
    monkeypatch.setattr(polling.st, "fragment", None, raising=False)

    def sample() -> str:
        return "ok"

    wrapped = polling.fragment_rerun_every(2.0)(sample)
    assert wrapped is sample or wrapped() == "ok"


def test_clean_media_running_ui_uses_poll_without_extra_rerun_button() -> None:
    source = Path("otio_app/ui/clean_media.py").read_text(encoding="utf-8")
    assert "poll_while_running" in source
    assert 'key=f"clean_refresh_{project.id}"' not in source
    assert "_clean_media_running_panel" in source


def test_analysis_banner_covers_clean_media_and_assets() -> None:
    source = Path("otio_app/ui/analysis_jobs_ui.py").read_text(encoding="utf-8")
    assert "get_clean_media_job_manager" in source
    assert "Clean Media läuft" in source
    assert "Asset-Analyse läuft" in source
    assert "skip_kinds" in source
    assert "poll_while_running" in source


def test_workflow_pages_show_self_updating_job_banners() -> None:
    source = Path("otio_app/ui/routing.py").read_text(encoding="utf-8")
    assert "show_jobs_banner: bool = True" in source
    assert 'jobs_banner_skip=("clean",)' in source
    assert 'jobs_banner_skip=("voice", "assets", "recovery")' in source
    assert "PAGE_ADOBE_IMPORT," in source
    assert "render_adobe_research_import_page," in source
    assert "show_auto_run_panel=False" in source
    assert "begin_ui_script_run" in source
    assert "reconcile_all_jobs()" in source
    nav = Path("otio_app/ui/routing.py").read_text(encoding="utf-8")
    begin_at = nav.find("begin_ui_script_run()")
    activity_at = nav.find("render_activity_panel()")
    assert begin_at != -1 and activity_at != -1
    assert begin_at < activity_at
    activity = Path("otio_app/ui/activity.py").read_text(encoding="utf-8")
    assert "reconcile_all_jobs()" not in activity


def test_funnel_lite_monitor_does_not_sleep_rerun() -> None:
    source = Path("otio_app/ui/without_voiceover_enhanced/cut_plan_tab.py").read_text(
        encoding="utf-8"
    )
    assert "time.sleep(1.5)" not in source
    assert "enh_funnel_lite_poll_" in source


def test_adobe_import_running_ui_uses_poll_without_sleep_rerun() -> None:
    source = Path("otio_app/ui/adobe_research_import_page.py").read_text(encoding="utf-8")
    assert "poll_while_running" in source
    assert "adobe_research_poll_" in source
    assert "time.sleep(1.0)" not in source
    assert "_render_adobe_import_running_panel" in source


def test_analysis_banner_running_rows_include_progress_bars() -> None:
    source = Path("otio_app/ui/analysis_jobs_ui.py").read_text(encoding="utf-8")
    assert "progress=_banner_fraction" in source
    assert "st.progress" in source
    assert "Clean Media läuft" in source
    assert "Asset-Analyse läuft" in source
