"""Auto-Refresh für laufende Jobs — nur Fragment, nicht die ganze App."""

from __future__ import annotations

from datetime import timedelta

import streamlit as st

DEFAULT_POLL_SECONDS = 2.0


def running_job_fragment(*, interval_seconds: float = DEFAULT_POLL_SECONDS):
    """Fragment-Decorator: aktualisiert nur den Job-Bereich, Stop bleibt bedienbar."""

    def decorator(func):
        if hasattr(st, "fragment"):
            return st.fragment(run_every=timedelta(seconds=interval_seconds))(func)
        return func

    return decorator
