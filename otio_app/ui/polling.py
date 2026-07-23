"""Job-Fortschritt — ohne Auto-Polling (verhindert Rerun-Stürme und heiße CPUs)."""

from __future__ import annotations

from collections.abc import Callable

import streamlit as st

from otio_app.shutdown import is_shutting_down

DEFAULT_POLL_SECONDS = 2.0


def poll_while_running(
    render_fn: Callable[[], None],
    is_running_fn: Callable[[], bool],
    *,
    interval_seconds: float = DEFAULT_POLL_SECONDS,  # noqa: ARG001 — API compat
    refresh_key: str = "job_refresh",
) -> None:
    """Rendert Job-UI einmal; optional manuell aktualisieren statt run_every."""
    if is_shutting_down():
        return

    render_fn()

    if not is_running_fn():
        return

    st.caption("Job läuft im Hintergrund — Fortschritt mit **Aktualisieren** holen.")
    if st.button("🔄 Aktualisieren", key=refresh_key):
        st.rerun()


def running_job_fragment(*, interval_seconds: float = DEFAULT_POLL_SECONDS):  # noqa: ARG001
    """Abwärtskompatibel — ruft nur noch render_fn ohne Fragment auf."""

    def decorator(func):
        def wrapper(*args, **kwargs):
            if is_shutting_down():
                return None
            return func(*args, **kwargs)

        wrapper.__name__ = func.__name__
        wrapper.__doc__ = func.__doc__
        return wrapper

    return decorator
