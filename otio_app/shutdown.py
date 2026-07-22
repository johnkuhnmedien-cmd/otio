"""Koordiniertes Herunterfahren — verhindert hängende Streamlit-Fragmente bei Ctrl+C."""

from __future__ import annotations

import atexit
import os
import signal
import threading

_shutting_down = False
_hard_exit_timer: threading.Timer | None = None
_handlers_registered = False


def is_shutting_down() -> bool:
    return _shutting_down


def request_shutdown(*, hard_exit_delay: float = 1.0) -> None:
    """Signalisiert Shutdown, bricht Jobs ab und erzwingt nach kurzer Zeit Prozessende."""
    global _shutting_down, _hard_exit_timer
    if _shutting_down:
        return
    _shutting_down = True
    cancel_all_background_jobs()

    if _hard_exit_timer is None:
        _hard_exit_timer = threading.Timer(hard_exit_delay, lambda: os._exit(0))
        _hard_exit_timer.daemon = True
        _hard_exit_timer.start()


def cancel_all_background_jobs() -> None:
    from otio_app.services.asset_analysis_job import get_asset_analysis_job_manager
    from otio_app.services.clean_media_job import get_clean_media_job_manager
    from otio_app.services.otio_export_job import get_otio_export_job_manager
    from otio_app.services.voice_analysis_job import get_voice_analysis_job_manager

    get_clean_media_job_manager().cancel_all_running()
    get_voice_analysis_job_manager().cancel_all_running()
    get_asset_analysis_job_manager().cancel_all_running()
    get_otio_export_job_manager().cancel_all_running()


def _handle_signal(signum: int, frame) -> None:  # noqa: ARG001
    if _shutting_down:
        os._exit(0)
    request_shutdown()


def register_shutdown_handlers() -> None:
    global _handlers_registered
    if _handlers_registered:
        return
    _handlers_registered = True
    atexit.register(cancel_all_background_jobs)

    # Streamlit (and similar hosts) execute app.py in a script-runner thread.
    # signal.signal() is only valid on the process main thread.
    if threading.current_thread() is not threading.main_thread():
        return

    try:
        signal.signal(signal.SIGINT, _handle_signal)
        signal.signal(signal.SIGTERM, _handle_signal)
    except ValueError:
        pass
