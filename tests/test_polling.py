"""Tests für Job-Aktualisieren ohne verschlucktes st.rerun()."""

from __future__ import annotations

from unittest.mock import MagicMock

from otio_app.ui import polling


def test_poll_while_running_button_does_not_call_rerun(monkeypatch) -> None:
    session: dict = {}
    monkeypatch.setattr(polling, "is_shutting_down", lambda: False)
    monkeypatch.setattr(polling.st, "caption", lambda *a, **k: None)
    monkeypatch.setattr(polling.st, "button", lambda *a, **k: True)
    monkeypatch.setattr(polling.st, "session_state", session)
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
