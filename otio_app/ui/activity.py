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
    reconcile_all_jobs,
    running_job_count,
)

logger = logging.getLogger("otio_app.activity")

_RUN_LOG_KEY = "_otio_run_log"
_RUN_COUNT_KEY = "_otio_run_count"
_LOG_FILE_NAME = "ui_activity.log"


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


def render_activity_panel(*, expanded: bool = False) -> None:
    """Zeigt laufende Jobs, Thread-Status und letzte Script-Läufe."""
    reconcile_all_jobs()
    running = running_job_count()
    run_count = int(st.session_state.get(_RUN_COUNT_KEY, 0))

    with st.expander("🔍 Hintergrund-Aktivität", expanded=expanded or running > 0):
        st.caption(
            f"Script-Läufe diese Sitzung: **{run_count}** · "
            f"als laufend gemeldete Jobs: **{running}**"
        )
        if run_count > 80:
            st.warning(
                "Sehr viele Script-Läufe — oft durch Auto-Refresh oder hängende Jobs. "
                "Unten **Alle Jobs zurücksetzen** probieren und App neu starten."
            )

        activities = collect_job_activity()
        if not activities:
            st.success("Keine Job-Zustände im Speicher — nichts blockiert im Hintergrund.")
        else:
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

        if st.button("Alle Hintergrund-Jobs zurücksetzen", key="force_reset_all_jobs"):
            reset_count = force_reset_all_jobs()
            line = (
                f"{datetime.now(timezone.utc).isoformat()} · "
                f"force_reset_all_jobs → {reset_count}"
            )
            _append_log_file(line)
            st.success(f"{reset_count} Job(s) zurückgesetzt.")
            st.rerun()
