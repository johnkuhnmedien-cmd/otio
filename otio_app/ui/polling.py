"""Job-Fortschritt — selbst aktualisierend, ohne zweiten st.rerun() am Button.

Ein Button-Klick startet in Streamlit bereits einen Script-Lauf. Ein zusätzliches
``st.rerun()`` in derselben Ausführung (besonders unter ``st.navigation``) kann
den Klick verschlucken — der Fortschritt bleibt stehen.

Laufende Jobs hängen in einem ``st.fragment(run_every=…)``, das den Fortschritt
alle zwei Sekunden neu zeichnet. **Aktualisieren** bleibt als Fallback.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime

import streamlit as st

from otio_app.shutdown import is_shutting_down

DEFAULT_POLL_SECONDS = 2.0


def fragment_rerun_every(run_every: float = DEFAULT_POLL_SECONDS):
    """``st.fragment(run_every=…)`` — ohne Fragment-API unverändert durchreichen."""
    fragment = getattr(st, "fragment", None)
    if not callable(fragment):
        return lambda fn: fn
    try:
        return fragment(run_every=run_every)
    except TypeError:
        try:
            return fragment()
        except TypeError:
            return lambda fn: fn


def _running_job_tick_impl(
    render_fn: Callable[[], None],
    is_running_fn: Callable[[], bool],
    refresh_key: str,
) -> None:
    """Nur der laufende Block — zeichnet sich selbst neu, bis der Job endet."""
    if is_shutting_down():
        return
    if not is_running_fn():
        st.rerun()
        return
    render_fn()
    now = datetime.now().strftime("%H:%M:%S")
    st.caption(
        f"Anzeige aktualisiert sich selbst · Stand **{now}**. "
        "Falls nicht: **Aktualisieren**."
    )
    if st.button("🔄 Aktualisieren", key=refresh_key):
        tick_key = f"{refresh_key}__tick"
        st.session_state[tick_key] = int(st.session_state.get(tick_key, 0)) + 1


_running_job_tick = fragment_rerun_every(DEFAULT_POLL_SECONDS)(_running_job_tick_impl)


def poll_while_running(
    render_fn: Callable[[], None],
    is_running_fn: Callable[[], bool],
    *,
    interval_seconds: float = DEFAULT_POLL_SECONDS,  # noqa: ARG001 — API compat
    refresh_key: str = "job_refresh",
) -> None:
    """Rendert Job-UI; bei laufendem Job alle ~2s, ohne extra Rerun am Button."""
    if is_shutting_down():
        return
    if not is_running_fn():
        render_fn()
        return
    _running_job_tick(render_fn, is_running_fn, refresh_key)


def running_job_fragment(*, interval_seconds: float = DEFAULT_POLL_SECONDS):
    """Abwärtskompatibel — wie ``fragment_rerun_every``."""
    return fragment_rerun_every(interval_seconds)
