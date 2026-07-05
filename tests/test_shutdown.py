"""Tests für koordiniertes App-Shutdown."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import otio_app.shutdown as shutdown


def test_request_shutdown_sets_flag_and_schedules_hard_exit(monkeypatch):
    monkeypatch.setattr(shutdown, "_shutting_down", False)
    monkeypatch.setattr(shutdown, "_hard_exit_timer", None)

    timer = MagicMock()
    monkeypatch.setattr(shutdown.threading, "Timer", lambda delay, fn: timer)

    with patch.object(shutdown, "cancel_all_background_jobs") as cancel_jobs:
        shutdown.request_shutdown(hard_exit_delay=0.5)

    assert shutdown.is_shutting_down()
    cancel_jobs.assert_called_once()
    timer.daemon = True
    timer.start.assert_called_once()


def test_second_signal_exits_immediately(monkeypatch):
    monkeypatch.setattr(shutdown, "_shutting_down", True)
    with patch.object(shutdown.os, "_exit") as hard_exit:
        shutdown._handle_signal(2, None)
    hard_exit.assert_called_once_with(0)
