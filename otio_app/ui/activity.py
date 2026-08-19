"""Diagnose: Hintergrund-Jobs und Streamlit-Aktivität."""

from __future__ import annotations

import logging
from collections import deque
from datetime import datetime, timezone
from pathlib import Path

import streamlit as st

from otio_app.project_repository import list_projects
from otio_app.services.job_registry import (
    collect_job_activity,
    force_reset_all_jobs,
    running_job_count,
)
from otio_app.ui.polling import poll_while_running

logger = logging.getLogger("otio_app.activity")

_RUN_LOG_KEY = "_otio_run_log"
_RUN_COUNT_KEY = "_otio_run_count"
_LOG_FILE_NAME = "ui_activity.log"


def log_heavy_operation(label: str, *, page: str = "") -> None:
    """Protokolliert teure Operationen (z. B. ffmpeg) für die Diagnose."""
    suffix = f" · page={page}" if page else ""
    line = f"{datetime.now(timezone.utc).isoformat()} · HEAVY · {label}{suffix}"
    log: deque[str] = st.session_state.get(_RUN_LOG_KEY, deque(maxlen=30))
    if not isinstance(log, deque):
        log = deque(maxlen=30)
    log.appendleft(line)
    st.session_state[_RUN_LOG_KEY] = log
    logger.warning(line)
    _append_log_file(line)


def record_script_run(page: str) -> None:
    """Zählt Script-Läufe und protokolliert die aktuelle Seite."""
    count = int(st.session_state.get(_RUN_COUNT_KEY, 0)) + 1
    st.session_state[_RUN_COUNT_KEY] = count

    entry = f"{datetime.now(timezone.utc).isoformat()} · run #{count} · page={page}"
    log: deque[str] = st.session_state.get(_RUN_LOG_KEY, deque(maxlen=30))
    if not isinstance(log, deque):
        log = deque(maxlen=30)
    log.appendleft(entry)
    st.session_state[_RUN_LOG_KEY] = log
    logger.info(entry)


def _append_log_file(line: str) -> None:
    for project in list_projects():
        log_path = project.work_dir_path / _LOG_FILE_NAME
        try:
            log_path.parent.mkdir(parents=True, exist_ok=True)
            with log_path.open("a", encoding="utf-8") as handle:
                handle.write(line + "\n")
            return
        except OSError:
            continue


def _render_activity_job_rows() -> None:
    activities = collect_job_activity()
    if not activities:
        st.success("Keine Job-Zustände im Speicher — nichts blockiert im Hintergrund.")
        return
    for activity in activities:
        thread_hint = (
            "Thread aktiv"
            if activity.thread_alive
            else "kein Thread"
            if activity.thread_alive is False
            else "—"
        )
        icon = "⏳" if activity.status == "running" else "✓"
        st.write(
            f"{icon} **{activity.kind}** · Projekt `{activity.project_id[:8]}…` · "
            f"Status `{activity.status}` · {activity.detail} · {thread_hint}"
        )


def render_activity_panel(
    *,
    expanded: bool = False,
    key_scope: str = "sidebar",
) -> None:
    """Zeigt laufende Jobs, Thread-Status und letzte Script-Läufe."""
    running = running_job_count()
    run_count = int(st.session_state.get(_RUN_COUNT_KEY, 0))
    scope = str(key_scope or "sidebar").strip() or "sidebar"

    with st.expander("🔍 Hintergrund-Aktivität", expanded=expanded or running > 0):
        st.caption(
            f"Script-Läufe diese Sitzung: **{run_count}** · "
            f"als laufend gemeldete Jobs: **{running}**"
        )
        if run_count > 80:
            st.warning(
                "Sehr viele Script-Läufe — oft durch Auto-Refresh oder hängende Jobs. "
                "Unten **Alle Jobs zurücksetzen** probieren. "
                "Wenn die App sich nicht mehr beenden lässt: Finder **OTIO starten.command** "
                "→ **Stoppen** oder **Neu starten** (beendet auch einen laufenden LLM-Call)."
            )

        if running > 0:
            poll_while_running(
                _render_activity_job_rows,
                lambda: running_job_count() > 0,
                refresh_key=f"activity_jobs_refresh_{scope}",
            )
        else:
            _render_activity_job_rows()

        log: deque[str] = st.session_state.get(_RUN_LOG_KEY, deque())
        if log:
            st.markdown("**Letzte Script-Läufe**")
            for line in list(log)[:8]:
                st.caption(line)

        log_paths = [
            project.work_dir_path / _LOG_FILE_NAME for project in list_projects()
        ]
        existing_logs = [path for path in log_paths if path.is_file()]
        if existing_logs:
            st.caption(
                "Logdatei: "
                + ", ".join(f"`{path}`" for path in existing_logs[:2])
            )

        if st.button(
            "Alle Hintergrund-Jobs zurücksetzen",
            key=f"force_reset_all_jobs_{scope}",
        ):
            reset_count = force_reset_all_jobs()
            line = (
                f"{datetime.now(timezone.utc).isoformat()} · "
                f"force_reset_all_jobs → {reset_count}"
            )
            _append_log_file(line)
            st.success(f"{reset_count} Job(s) zurückgesetzt.")
            st.rerun()

        st.caption(
            "App lässt sich im Terminal nicht mehr beenden: im Finder "
            "**OTIO starten.command** öffnen und **Stoppen** oder **Neu starten**."
        )
