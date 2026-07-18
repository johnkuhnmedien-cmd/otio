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
from otio_app.discovery_v2.application.coverage_gap_service import (
    accept_gap_unresolved,
    assign_local_deeper_review,
    escalate_gap,
    evaluate_gap_accept_unresolved_eligibility,
    materialize_gaps_from_current_coverage,
)
from otio_app.discovery_v2.application.script_lock_service import (
    create_script_lock,
    preview_script_lock,
)
from otio_app.discovery_v2.application.supplementation_service import (
    create_graphic_plan,
    get_supplementation_view,
    record_candidate_decision,
    record_claim_decision,
    start_candidate_validation_run,
    start_search_run,
)
from otio_app.discovery_v2.ui.overview import active_discovery_project

_FLASH_KEY = "discovery_v2_editorial_flash"


def _consume_flash() -> None:
    message = st.session_state.pop(_FLASH_KEY, None)
    if message:
        st.success(str(message))


def _flash_and_rerun(message: str) -> None:
    st.session_state[_FLASH_KEY] = message
    st.rerun()


def render_discovery_editorial_page() -> None:
    st.title("Editorial")
    project = active_discovery_project()
    if project is None:
        return
    _consume_flash()

    st.info(
        "Lokaler Fake-Textadapter: Es werden keine Projektdaten an externe "
        "Dienste übertragen. (`fake-editorial-v1`, kein HTTP/SDK)"
    )
    st.info(
        "Phase 10 nutzt nur lokale Fake-Ergaenzungskandidaten. Keine Adobe-/Stock-"
        "Netzwerke, keine Lizenzierung, keine Preview-Binaerdaten werden geoeffnet."
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
    _render_supplementation(project, view)
    _render_script_lock(project, view)
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
        for item in bundle["claims"]:
            cols = st.columns(4)
            decisions = [
                ("Bestaetigt", "confirmed"),
                ("Abgelehnt", "rejected"),
                ("Als unsicher akzeptiert", "accepted_as_uncertain"),
                ("Revision erforderlich", "revision_required"),
            ]
            for column, (label, value) in zip(cols, decisions):
                with column:
                    if st.button(
                        label,
                        key=f"discovery_v2_claim_decision_{item['claim_id']}_{value}",
                    ):
                        record_claim_decision(
                            project,
                            script_id=script.script_id,
                            claim_id=item["claim_id"],
                            claim_text=item["statement"],
                            decision=value,
                            reason="UI-Entscheidung",
                        )
                        st.success("Claim-Entscheidung gespeichert.")
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


def _render_supplementation(project, view) -> None:
    st.subheader("Coverage Gaps und Supplementation")
    if st.button(
        "Coverage Gaps materialisieren",
        key="discovery_v2_materialize_gaps",
    ):
        result = materialize_gaps_from_current_coverage(project)
        st.success(result.message) if result.ok else st.warning(result.message)
    supp_view = get_supplementation_view(project)
    if not supp_view.ok:
        st.warning(supp_view.message or "Supplementation nicht verfuegbar.")
        return
    if supp_view.active_run is not None:
        st.caption(
            f"Aktiver Supplementation-Run: `{supp_view.active_run.run_id}` "
            f"({supp_view.active_run.scope}/{supp_view.active_run.status.value})"
        )
    gaps = supp_view.gaps
    if not gaps:
        st.write("Keine offenen Coverage Gaps materialisiert.")
        return
    gap_ids = [gap.gap_id for gap in gaps if gap.status.value != "superseded"]
    cols = st.columns(2)
    with cols[0]:
        if st.button(
            "Ergaenzungskandidaten suchen",
            key="discovery_v2_supplementation_search",
            disabled=not gap_ids,
        ):
            result = start_search_run(project, gap_ids=gap_ids, sync=False)
            st.success(result.message) if result.started else st.warning(result.message)
    with cols[1]:
        if st.button(
            "Kandidaten validieren",
            key="discovery_v2_supplementation_validate",
            disabled=not gap_ids,
        ):
            result = start_candidate_validation_run(project, gap_ids=gap_ids, sync=False)
            st.success(result.message) if result.started else st.warning(result.message)
    for gap in gaps:
        with st.container():
            st.markdown(
                f"**Gap {gap.visual_intent_id}** - {gap.coverage_level.value} / "
                f"{gap.status.value}"
            )
            if gap.risk_flags:
                st.caption("Risiken: " + ", ".join(risk.value for risk in gap.risk_flags))
            if gap.missing_properties:
                st.caption("Fehlt: " + ", ".join(gap.missing_properties))
            st.caption(f"Eskalation: {gap.current_escalation_step.value}")
            gap_cols = st.columns(4)
            with gap_cols[0]:
                if st.button(
                    "Lokale Assets erneut pruefen",
                    key=f"discovery_v2_gap_local_{gap.gap_id}",
                ):
                    result = assign_local_deeper_review(project, gap_id=gap.gap_id)
                    st.success(result.message) if result.ok else st.warning(result.message)
            with gap_cols[1]:
                if st.button(
                    "Naechste Eskalation",
                    key=f"discovery_v2_gap_escalate_{gap.gap_id}",
                ):
                    result = escalate_gap(project, gap_id=gap.gap_id)
                    st.success(result.message) if result.ok else st.warning(result.message)
            with gap_cols[2]:
                if st.button(
                    "Karte/Grafik planen",
                    key=f"discovery_v2_gap_graphic_{gap.gap_id}",
                ):
                    create_graphic_plan(
                        project,
                        gap_id=gap.gap_id,
                        description="Manueller GraphicPlan fuer Coverage Gap.",
                    )
                    st.success("GraphicPlan angelegt; keine Grafik erzeugt.")
            with gap_cols[3]:
                eligibility = evaluate_gap_accept_unresolved_eligibility(
                    project, gap_id=gap.gap_id
                )
                if eligibility.visible_risks:
                    st.caption(
                        "Akzeptierbare Risiken: "
                        + ", ".join(risk.value for risk in eligibility.visible_risks)
                    )
                if not eligibility.ok and eligibility.blockers:
                    st.caption("Risikoannahme blockiert: " + "; ".join(eligibility.blockers))
                confirm_accept = _checkbox(
                    "Ich akzeptiere dieses Coverage-Risiko unaufgeloest",
                    value=False,
                    key=f"discovery_v2_gap_accept_confirm_{gap.gap_id}",
                )
                if st.button(
                    "Risiko unaufgeloest akzeptieren",
                    key=f"discovery_v2_gap_accept_unresolved_{gap.gap_id}",
                    disabled=not (eligibility.ok and confirm_accept),
                ):
                    result = accept_gap_unresolved(
                        project,
                        gap_id=gap.gap_id,
                        confirmed_risks=[risk.value for risk in eligibility.visible_risks],
                        user_confirmed=bool(confirm_accept),
                    )
                    if result.ok:
                        _flash_and_rerun(result.message)
                    else:
                        st.warning(result.message)
            candidates = supp_view.candidates_by_gap.get(gap.gap_id, [])
            if candidates:
                st.dataframe(
                    [
                        {
                            "Kandidat": candidate.candidate_id,
                            "Preview-Metadaten": candidate.preview_ref or "—",
                            "Quelle": candidate.provider,
                            "Medienart": candidate.media_kind,
                            "Beschreibung": candidate.description,
                            "Dubletten": candidate.duplicate_status.value,
                            "Lizenz": candidate.license_status.value,
                            "Status": candidate.user_status.value,
                        }
                        for candidate in candidates
                    ],
                    use_container_width=True,
                    hide_index=True,
                )
                for candidate in candidates:
                    cand_cols = st.columns(3)
                    with cand_cols[0]:
                        if st.button(
                            "Fuer Import akzeptieren",
                            key=f"discovery_v2_candidate_accept_{candidate.candidate_id}",
                        ):
                            result = record_candidate_decision(
                                project,
                                candidate_id=candidate.candidate_id,
                                decision="accepted_for_import",
                                reason="UI-Entscheidung",
                            )
                            st.success(result.message) if result.ok else st.warning(result.message)
                    with cand_cols[1]:
                        if st.button(
                            "Ablehnen",
                            key=f"discovery_v2_candidate_reject_{candidate.candidate_id}",
                        ):
                            result = record_candidate_decision(
                                project,
                                candidate_id=candidate.candidate_id,
                                decision="rejected",
                                reason="UI-Entscheidung",
                            )
                            st.success(result.message) if result.ok else st.warning(result.message)
                    with cand_cols[2]:
                        if st.button(
                            "Pruefung noetig",
                            key=f"discovery_v2_candidate_review_{candidate.candidate_id}",
                        ):
                            result = record_candidate_decision(
                                project,
                                candidate_id=candidate.candidate_id,
                                decision="needs_review",
                                reason="UI-Entscheidung",
                            )
                            st.success(result.message) if result.ok else st.warning(result.message)


def _render_script_lock(project, view) -> None:
    st.subheader("Script Lock")
    supp_view = get_supplementation_view(project)
    if supp_view.script_locks:
        latest = supp_view.script_locks[0]
        st.caption(
            f"Aktueller Lock: {latest.lock_id} / {latest.status.value} / "
            f"Fingerprint {latest.lock_fingerprint[:12]}"
        )
    st.write(
        "Lock ist synchron und manuell. Die Checkbox ist absichtlich nicht vorselektiert."
    )
    preview = preview_script_lock(project)
    if preview.fulfilled_requirements:
        st.markdown("**Erfuellt**")
        for item in preview.fulfilled_requirements:
            st.write(f"✓ {item}")
    if preview.blocking_requirements:
        st.markdown("**Script Lock noch nicht moeglich**")
        for item in preview.blocking_requirements:
            st.write(f"✗ {item}")
    displayed_fingerprint = preview.lock_fingerprint
    if displayed_fingerprint:
        st.markdown("**Aktueller Lock-Stand**")
        st.code(f"Fingerprint: {preview.fingerprint_display or displayed_fingerprint[:12]}…")
        with st.expander("Technische Details anzeigen", expanded=False):
            st.code(displayed_fingerprint)
        st.session_state["discovery_v2_lock_displayed_fingerprint"] = displayed_fingerprint
    else:
        st.caption("Kein Fingerprint verfuegbar, solange Blocker offen sind.")
        st.session_state.pop("discovery_v2_lock_displayed_fingerprint", None)
    confirmed = _checkbox(
        "Ich bestaetige genau diesen aktuellen Stand.",
        value=False,
        key="discovery_v2_lock_confirmed",
    )
    risk_confirmations: dict[str, bool] = {}
    for gap in supp_view.gaps:
        if gap.status.value == "accepted_unresolved":
            for risk in gap.risk_flags:
                key = f"{gap.gap_id}:{risk.value}"
                risk_confirmations[key] = _checkbox(
                    f"Offenes Risiko bestaetigen: {risk.value} ({gap.visual_intent_id})",
                    value=False,
                    key=f"discovery_v2_lock_risk_{gap.gap_id}_{risk.value}",
                )
    if st.button(
        "Skript fuer Voice und Timing sperren",
        key="discovery_v2_create_script_lock",
        disabled=not bool(displayed_fingerprint),
    ):
        result = create_script_lock(
            project,
            user_confirmed=bool(confirmed),
            confirmed_fingerprint=st.session_state.get(
                "discovery_v2_lock_displayed_fingerprint"
            ),
            accepted_unresolved_risk_confirmations=risk_confirmations,
        )
        if result.ok:
            _flash_and_rerun(result.message)
        else:
            st.warning(result.message)


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


def _checkbox(label: str, value: bool = False, **kwargs) -> bool:
    checkbox = getattr(st, "checkbox", None)
    if checkbox is None:
        return value
    return bool(checkbox(label, value=value, **kwargs))


__all__ = ["render_discovery_editorial_page"]
