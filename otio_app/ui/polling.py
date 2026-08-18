"""Job-Fortschritt — manuell aktualisieren, ohne zweiten st.rerun().

Ein Button-Klick startet in Streamlit bereits einen Script-Lauf. Ein zusätzliches
``st.rerun()`` in derselben Ausführung (besonders unter ``st.navigation``) kann
den Klick verschlucken — der Fortschritt bleibt stehen.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime

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
    """Rendert Job-UI; Button löst den normalen Streamlit-Rerun aus (kein extra rerun)."""
    if is_shutting_down():
        return

    render_fn()

    if not is_running_fn():
        return

    now = datetime.now().strftime("%H:%M:%S")
    st.caption(
        f"Job läuft im Hintergrund — Stand **{now}**. "
        "Fortschritt mit **Aktualisieren** holen."
    )
    if st.button("🔄 Aktualisieren", key=refresh_key):
        tick_key = f"{refresh_key}__tick"
        st.session_state[tick_key] = int(st.session_state.get(tick_key, 0)) + 1


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
