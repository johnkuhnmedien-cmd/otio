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


def test_register_shutdown_handlers_skips_signals_off_main_thread(monkeypatch):
    monkeypatch.setattr(shutdown, "_handlers_registered", False)
    worker = MagicMock(name="worker_thread")
    main = MagicMock(name="main_thread")
    monkeypatch.setattr(shutdown.threading, "current_thread", lambda: worker)
    monkeypatch.setattr(shutdown.threading, "main_thread", lambda: main)

    with patch.object(shutdown.signal, "signal") as register_signal:
        shutdown.register_shutdown_handlers()

    register_signal.assert_not_called()
    assert shutdown._handlers_registered


def test_register_shutdown_handlers_tolerates_signal_value_error(monkeypatch):
    monkeypatch.setattr(shutdown, "_handlers_registered", False)
    monkeypatch.setattr(shutdown.threading, "current_thread", shutdown.threading.main_thread)

    def raise_value_error(*_args, **_kwargs):
        raise ValueError("signal only works in main thread of the main interpreter")

    with patch.object(shutdown.signal, "signal", side_effect=raise_value_error):
        shutdown.register_shutdown_handlers()

    assert shutdown._handlers_registered
