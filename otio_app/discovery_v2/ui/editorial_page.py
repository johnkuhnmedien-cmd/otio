"""Streamlit page: Discovery V2 Editorial Core (fake text E2E)."""

from __future__ import annotations

import difflib

import streamlit as st

from otio_app.discovery_v2.application.editorial_service import (
    get_editorial_view,
    save_project_brief,
    save_user_script_edit,
    select_hook,
    start_coverage_run,
    start_narrative_run,
    start_script_run,
    start_structure_run,
)
from otio_app.discovery_v2.ui.overview import active_discovery_project


def render_discovery_editorial_page() -> None:
    st.title("Editorial")
    project = active_discovery_project()
    if project is None:
        return

    st.info(
        "Lokaler Fake-Textadapter: Es werden keine Projektdaten an externe "
        "Dienste übertragen. (`fake-editorial-v1`, kein HTTP/SDK)"
    )
    view = get_editorial_view(project)
    if not view.ok:
        st.warning(view.message or "Editorial-Ansicht nicht verfuegbar.")
        return
    if view.active_run is not None:
        st.caption(
            f"Aktiver Editorial-Run: `{view.active_run.run_id}` "
            f"({view.active_run.scope}/{view.active_run.status.value})"
        )
    if view.stale:
        st.warning("Editorial-Artefakte sind stale; Folgeaktionen bitte explizit neu starten.")

    _render_brief(project, view)
    _render_narrative(project, view)
    _render_script(project, view)
    _render_coverage(project, view)
    _render_runs(view)


def _render_brief(project, view) -> None:
    st.subheader("Project Brief")
    active = view.active_brief
    with st.form("discovery_v2_editorial_brief_form"):
        language = st.text_input("Sprache", value=(active.language if active else project.language))
        topic = st.text_input("Thema", value=(active.topic if active else ""))
        target_audience = st.text_input(
            "Zielpublikum",
            value=(active.target_audience if active else ""),
        )
        tone = st.text_input("Ton", value=(active.tone if active else "informativ"))
        duration = st.number_input(
            "Zieldauer Sekunden",
            min_value=0,
            value=int(active.desired_duration_seconds or 0) if active else 0,
        )
        geographic_frame = st.text_input(
            "Geografischer Rahmen",
            value=(active.geographic_frame if active and active.geographic_frame else ""),
        )
        must_include = st.text_area(
            "Must include (eine Zeile je Punkt)",
            value="\n".join(active.must_include) if active else "",
        )
        must_exclude = st.text_area(
            "Must exclude (eine Zeile je Punkt)",
            value="\n".join(active.must_exclude) if active else "",
        )
        notes = st.text_area("Notizen", value=(active.user_notes if active and active.user_notes else ""))
        submitted = st.form_submit_button("Project Brief speichern")
    if submitted:
        result = save_project_brief(
            project,
            language=language,
            topic=topic,
            target_audience=target_audience,
            tone=tone,
            desired_duration_seconds=(None if duration <= 0 else int(duration)),
            geographic_frame=geographic_frame,
            must_include=_lines(must_include),
            must_exclude=_lines(must_exclude),
            user_notes=notes,
        )
        if result.ok:
            st.success(result.message)
        else:
            st.warning(result.message)
    if view.briefs:
        st.dataframe(
            [
                {
                    "Version": brief.brief_version,
                    "Status": brief.status.value,
                    "Thema": brief.topic,
                    "Hash": brief.content_sha256[:12],
                }
                for brief in view.briefs
            ],
            use_container_width=True,
            hide_index=True,
        )


def _render_narrative(project, view) -> None:
    st.subheader("Narrative Plan und Hooks")
    if st.button(
        "Narrative erzeugen",
        disabled=not view.can_start_narrative,
        key="discovery_v2_editorial_start_narrative",
    ):
        result = start_narrative_run(project, sync=False)
        st.success(result.message) if result.started else st.warning(result.message)
    plan = view.narrative_plan
    if plan is not None:
        st.markdown(f"**Zentrale Frage:** {plan.central_question}")
        st.write(plan.editorial_thesis)
        if plan.uncertainties:
            st.caption("Unsicherheiten: " + "; ".join(plan.uncertainties))
    if view.hooks:
        for hook in view.hooks:
            selected = hook.hook_id == view.selected_hook_id or hook.user_status.value == "selected"
            with st.container():
                st.markdown(f"**{hook.hook_type}** {'(ausgewaehlt)' if selected else ''}")
                st.write(hook.hook_text)
                st.caption(f"Wirkung: {hook.intended_effect}")
                if st.button(
                    "Hook auswaehlen",
                    key=f"discovery_v2_select_hook_{hook.hook_id}",
                    disabled=selected,
                ):
                    result = select_hook(project, hook_id=hook.hook_id)
                    st.success(result.message) if result.ok else st.warning(result.message)


def _render_script(project, view) -> None:
    st.subheader("Script Draft, Claims, Beats, Intents")
    cols = st.columns(2)
    with cols[0]:
        if st.button(
            "Script erzeugen",
            disabled=not view.can_start_script,
            key="discovery_v2_editorial_start_script",
        ):
            result = start_script_run(project, sync=False)
            st.success(result.message) if result.started else st.warning(result.message)
    with cols[1]:
        if st.button(
            "Struktur aktualisieren",
            disabled=not view.can_start_structure,
            key="discovery_v2_editorial_start_structure",
        ):
            result = start_structure_run(project, sync=False)
            st.success(result.message) if result.started else st.warning(result.message)
    script = view.script
    if script is None:
        st.write("Noch kein Script Draft vorhanden.")
        return
    edited_text = st.text_area(
        f"Script v{script.script_version} ({script.status.value})",
        value=script.full_text,
        height=180,
        key=f"discovery_v2_script_text_{script.script_id}",
    )
    if st.button(
        "Nutzer-Edit als neue Version speichern",
        key="discovery_v2_editorial_save_script_edit",
        disabled=edited_text == script.full_text,
    ):
        result = save_user_script_edit(project, full_text=edited_text)
        st.success(result.message) if result.ok else st.warning(result.message)
    if len(view.script_versions) >= 2:
        st.markdown("**Diff zur vorherigen Version**")
        previous = view.script_versions[1]
        diff = "\n".join(
            difflib.unified_diff(
                previous.full_text.splitlines(),
                script.full_text.splitlines(),
                fromfile=f"v{previous.script_version}",
                tofile=f"v{script.script_version}",
                lineterm="",
            )
        )
        st.code(diff or "Keine Textdifferenz.", language="diff")
    bundle = view.script_bundle or {}
    if bundle.get("sentences"):
        st.markdown("**Saetze**")
        st.dataframe(bundle["sentences"], use_container_width=True, hide_index=True)
    if bundle.get("claims"):
        st.markdown("**Claims (nicht automatisch supported)**")
        st.dataframe(
            [
                {
                    "Claim": item["statement"],
                    "Status": item["status"],
                    "Confidence": item["confidence"],
                }
                for item in bundle["claims"]
            ],
            use_container_width=True,
            hide_index=True,
        )
    if bundle.get("visual_beats"):
        st.markdown("**Visual Beats**")
        st.dataframe(bundle["visual_beats"], use_container_width=True, hide_index=True)
    if bundle.get("visual_intents"):
        st.markdown("**Visual Intents**")
        st.dataframe(bundle["visual_intents"], use_container_width=True, hide_index=True)


def _render_coverage(project, view) -> None:
    st.subheader("Coverage Audit")
    if st.button(
        "Coverage pruefen",
        disabled=not view.can_start_coverage,
        key="discovery_v2_editorial_start_coverage",
    ):
        result = start_coverage_run(project, sync=False)
        st.success(result.message) if result.started else st.warning(result.message)
    audit = view.coverage_audit
    if audit is None:
        st.write("Noch kein Coverage Audit vorhanden.")
        return
    counts: dict[str, int] = {}
    for result in audit.results:
        counts[result.coverage_status.value] = counts.get(result.coverage_status.value, 0) + 1
    st.caption("Coverage-Zaehler: " + ", ".join(f"{k}={v}" for k, v in sorted(counts.items())))
    st.dataframe(
        [
            {
                "Intent": result.visual_intent_id,
                "Status": result.coverage_status.value,
                "Kandidaten": ", ".join(result.candidate_asset_ids) or "—",
                "Observationen": ", ".join(result.accepted_observation_ids) or "—",
                "Aktion": result.recommended_next_action,
            }
            for result in audit.results
        ],
        use_container_width=True,
        hide_index=True,
    )


def _render_runs(view) -> None:
    st.subheader("Run-Historie")
    if not view.runs:
        st.write("Noch keine Editorial-Runs vorhanden.")
        return
    st.dataframe(
        [
            {
                "Run": run.run_id,
                "Scope": run.scope,
                "Status": run.status.value,
                "Fehler": run.error_code or "—",
            }
            for run in view.runs
        ],
        use_container_width=True,
        hide_index=True,
    )


def _lines(value: str) -> list[str]:
    return [line.strip() for line in value.splitlines() if line.strip()]


__all__ = ["render_discovery_editorial_page"]
