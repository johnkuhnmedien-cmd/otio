"""Auto-Refresh für laufende Jobs — nur Fragment, nicht die ganze App."""

from __future__ import annotations

from collections.abc import Callable
from datetime import timedelta

import streamlit as st

from otio_app.shutdown import is_shutting_down

DEFAULT_POLL_SECONDS = 2.0


def poll_while_running(
    render_fn: Callable[[], None],
    is_running_fn: Callable[[], bool],
    *,
    interval_seconds: float = DEFAULT_POLL_SECONDS,
) -> None:
    """Aktualisiert nur den Job-Bereich, solange ein Hintergrund-Job läuft."""
    if is_shutting_down():
        return

    if not is_running_fn():
        render_fn()
        return

    if not hasattr(st, "fragment"):
        render_fn()
        return

    @st.fragment(run_every=timedelta(seconds=interval_seconds))
    def _polling_fragment() -> None:
        if is_shutting_down() or not is_running_fn():
            return
        render_fn()

    _polling_fragment()


def running_job_fragment(*, interval_seconds: float = DEFAULT_POLL_SECONDS):
    """Abwärtskompatibler Decorator — bevorzugt poll_while_running() mit is_running_fn."""

    def decorator(func):
        def wrapper(*args, **kwargs):
            if is_shutting_down():
                return
            if hasattr(st, "fragment"):
                fragment_func = st.fragment(run_every=timedelta(seconds=interval_seconds))(
                    func
                )
                return fragment_func(*args, **kwargs)
            return func(*args, **kwargs)

        wrapper.__name__ = func.__name__
        wrapper.__doc__ = func.__doc__
        return wrapper

    return decorator
