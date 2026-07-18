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
    record_claim_decision_batch,
    start_candidate_validation_run,
    start_search_run,
)
from otio_app.discovery_v2.ui.flash import discovery_ui_flash_and_rerun
from otio_app.discovery_v2.ui.overview import active_discovery_project


def _flash_and_rerun(message: str, *, level: str = "success") -> None:
    """Compat wrapper used by R1.1 tests and local call sites."""
    discovery_ui_flash_and_rerun(message, level=level)  # type: ignore[arg-type]


def render_discovery_editorial_page() -> None:
    st.title("Editorial")
    project = active_discovery_project()
    if project is None:
        return

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
            _flash_and_rerun(result.message)
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
        if result.started:
            _flash_and_rerun(result.message, level="info")
        else:
            st.warning(result.message)
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
                    if result.ok:
                        _flash_and_rerun(result.message)
                    else:
                        st.warning(result.message)


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
            if result.started:
                _flash_and_rerun(result.message, level="info")
            else:
                st.warning(result.message)
    with cols[1]:
        if st.button(
            "Struktur aktualisieren",
            disabled=not view.can_start_structure,
            key="discovery_v2_editorial_start_structure",
        ):
            result = start_structure_run(project, sync=False)
            if result.started:
                _flash_and_rerun(result.message, level="info")
            else:
                st.warning(result.message)
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
        if result.ok:
            _flash_and_rerun(result.message)
        else:
            st.warning(result.message)
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
        st.markdown("**Claims (Modellstatus ≠ Nutzerentscheidung)**")
        latest = getattr(view, "latest_claim_decisions", {}) or {}
        claim_rows = []
        for item in bundle["claims"]:
            user_dec = latest.get(item["claim_id"])
            claim_rows.append(
                {
                    "Claim": item["statement"],
                    "Modellstatus": item["status"],
                    "Modell-Confidence": item.get("confidence", "—"),
                    "Nutzerentscheidung": (
                        "—" if user_dec is None else user_dec.decision.value
                    ),
                    "Entscheidungszeitpunkt": (
                        "—"
                        if user_dec is None
                        else user_dec.created_at.isoformat()
                    ),
                    "Aktuell entschieden": "ja" if user_dec is not None else "nein",
                    "Claim-ID": item["claim_id"],
                }
            )
        st.dataframe(claim_rows, use_container_width=True, hide_index=True)

        filter_label = "offen"
        if hasattr(st, "selectbox"):
            filter_label = st.selectbox(
                "Claim-Filter",
                ["alle", "offen", "uncertain", "user_confirmation_required", "conflict"],
                index=1,
                key="discovery_v2_claim_filter",
            )
        filtered_claims = []
        for item in bundle["claims"]:
            model_status = str(item.get("status") or "").lower()
            decided = item["claim_id"] in latest
            if filter_label == "alle":
                filtered_claims.append(item)
            elif filter_label == "offen" and not decided:
                filtered_claims.append(item)
            elif filter_label == "uncertain" and "uncertain" in model_status:
                filtered_claims.append(item)
            elif filter_label == "user_confirmation_required" and (
                "user_confirmation" in model_status or "confirmation" in model_status
            ):
                filtered_claims.append(item)
            elif filter_label == "conflict" and "conflict" in model_status:
                filtered_claims.append(item)

        visible_claim_ids = [item["claim_id"] for item in filtered_claims]
        select_all_claims = False
        if hasattr(st, "checkbox"):
            select_all_claims = bool(
                st.checkbox(
                    "Alle sichtbaren Claims auswählen",
                    value=False,
                    key="discovery_v2_claim_select_all",
                )
            )
        selected_claim_ids: list[str] = (
            list(visible_claim_ids) if select_all_claims else []
        )
        if hasattr(st, "multiselect") and not select_all_claims:
            selected_claim_ids = list(
                st.multiselect(
                    "Claims für Batch",
                    visible_claim_ids,
                    default=[],
                    key="discovery_v2_claim_batch_ids",
                )
            )
        st.caption(f"Ausgewählt: {len(selected_claim_ids)} Claim(s).")
        confirm_claims = False
        if hasattr(st, "checkbox"):
            confirm_claims = bool(
                st.checkbox(
                    (
                        f"{len(selected_claim_ids)} Claims werden entschieden. "
                        "Jede Entscheidung wird append-only protokolliert."
                    ),
                    value=False,
                    key="discovery_v2_claim_batch_confirm",
                    disabled=not selected_claim_ids,
                )
            )
        claim_by_id = {item["claim_id"]: item for item in bundle["claims"]}
        batch_payload = [
            {
                "claim_id": claim_id,
                "claim_text": str(claim_by_id[claim_id]["statement"]),
            }
            for claim_id in selected_claim_ids
            if claim_id in claim_by_id
        ]
        if st.button(
            "Ausgewählte bestätigen",
            key="discovery_v2_claim_batch_confirm_btn",
            disabled=not batch_payload or not confirm_claims,
        ):
            result = record_claim_decision_batch(
                project,
                script_id=script.script_id,
                claims=batch_payload,
                decision="confirmed",
                user_confirmed=confirm_claims,
                reason="UI-Batch",
            )
            if result.ok:
                _flash_and_rerun(result.message)
            else:
                st.warning(result.message)
        if st.button(
            "Ausgewählte als unsicher akzeptieren",
            key="discovery_v2_claim_batch_uncertain_btn",
            disabled=not batch_payload or not confirm_claims,
        ):
            result = record_claim_decision_batch(
                project,
                script_id=script.script_id,
                claims=batch_payload,
                decision="accepted_as_uncertain",
                user_confirmed=confirm_claims,
                reason="UI-Batch",
            )
            if result.ok:
                _flash_and_rerun(result.message)
            else:
                st.warning(result.message)
        if st.button(
            "Ausgewählte zurückweisen",
            key="discovery_v2_claim_batch_reject_btn",
            disabled=not batch_payload or not confirm_claims,
        ):
            result = record_claim_decision_batch(
                project,
                script_id=script.script_id,
                claims=batch_payload,
                decision="rejected",
                user_confirmed=confirm_claims,
                reason="UI-Batch",
            )
            if result.ok:
                _flash_and_rerun(result.message)
            else:
                st.warning(result.message)

        st.markdown("**Einzelentscheidung (inkl. Konflikte)**")
        for item in bundle["claims"]:
            user_dec = latest.get(item["claim_id"])
            st.caption(
                f"`{item['claim_id']}` · Modellstatus: `{item['status']}` · "
                f"Nutzerentscheidung: "
                f"`{'—' if user_dec is None else user_dec.decision.value}` · "
                f"Aktuell entschieden: `{'ja' if user_dec is not None else 'nein'}`"
            )
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
                        _flash_and_rerun("Claim-Entscheidung gespeichert.")
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
        if result.reused:
            _flash_and_rerun(result.message, level="info")
        elif result.started:
            _flash_and_rerun(result.message, level="info")
        else:
            st.warning(result.message)
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
        if result.ok:
            _flash_and_rerun(result.message)
        else:
            st.warning(result.message)
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
            if result.started:
                _flash_and_rerun(result.message, level="info")
            else:
                st.warning(result.message)
    with cols[1]:
        if st.button(
            "Kandidaten validieren",
            key="discovery_v2_supplementation_validate",
            disabled=not gap_ids,
        ):
            result = start_candidate_validation_run(project, gap_ids=gap_ids, sync=False)
            if result.started:
                _flash_and_rerun(result.message, level="info")
            else:
                st.warning(result.message)
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
                    if result.ok:
                        _flash_and_rerun(result.message)
                    else:
                        st.warning(result.message)
            with gap_cols[1]:
                if st.button(
                    "Naechste Eskalation",
                    key=f"discovery_v2_gap_escalate_{gap.gap_id}",
                ):
                    result = escalate_gap(project, gap_id=gap.gap_id)
                    if result.ok:
                        _flash_and_rerun(result.message)
                    else:
                        st.warning(result.message)
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
                    _flash_and_rerun("GraphicPlan angelegt; keine Grafik erzeugt.")
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
                            if result.ok:
                                _flash_and_rerun(result.message)
                            else:
                                st.warning(result.message)
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
                            if result.ok:
                                _flash_and_rerun(result.message)
                            else:
                                st.warning(result.message)
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
                            if result.ok:
                                _flash_and_rerun(result.message)
                            else:
                                st.warning(result.message)


def _render_script_lock(project, view) -> None:
    from otio_app.discovery_v2.domain.supplementation import (
        make_lock_risk_confirmation_key,
    )

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
            detail_risks = list(getattr(preview, "accepted_open_risks", None) or [])
            if detail_risks:
                st.caption("Kanonische Risikenschluessel (gap_id:risk_code)")
                for key in detail_risks:
                    st.code(key)
        st.session_state["discovery_v2_lock_displayed_fingerprint"] = displayed_fingerprint
    else:
        st.caption("Kein Fingerprint verfuegbar, solange fachliche Blocker offen sind.")
        st.session_state.pop("discovery_v2_lock_displayed_fingerprint", None)
    confirmed = _checkbox(
        "Ich bestaetige genau diesen aktuellen Stand.",
        value=False,
        key="discovery_v2_lock_confirmed",
    )
    risk_confirmations: dict[str, bool] = {}
    required_risk_keys = list(getattr(preview, "accepted_open_risks", None) or [])
    confirmation_blockers = list(getattr(preview, "confirmation_blockers", None) or [])
    gaps_by_id = {gap.gap_id: gap for gap in supp_view.gaps}
    for key in required_risk_keys:
        gap_id, risk_code = key.split(":", 1)
        gap = gaps_by_id.get(gap_id)
        intent_label = (
            gap.visual_intent_id if gap is not None else "unbekannter Intent"
        )
        risk_confirmations[key] = _checkbox(
            (
                f"Risiko bestaetigen: {risk_code} "
                f"(Intent {intent_label})"
            ),
            value=False,
            key=f"discovery_v2_lock_risk_{gap_id}_{risk_code}",
        )
        # Ensure the widget key stays bound to the canonical gap_id identity.
        _ = make_lock_risk_confirmation_key(gap_id, risk_code)
    if confirmation_blockers and displayed_fingerprint:
        st.caption(
            "Fingerprint ist sichtbar. Lock-Button bleibt deaktiviert, "
            "bis Stand und alle Risiken bestaetigt sind."
        )
    risks_ok = (not required_risk_keys) or all(
        risk_confirmations.get(key, False) for key in required_risk_keys
    )
    can_click = bool(displayed_fingerprint and confirmed and risks_ok)
    if st.button(
        "Skript fuer Voice und Timing sperren",
        key="discovery_v2_create_script_lock",
        disabled=not can_click,
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
