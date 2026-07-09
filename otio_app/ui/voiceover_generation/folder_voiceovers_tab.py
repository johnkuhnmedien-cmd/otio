"""Voice-over pro Ordner: Settings, Generierung, Review-/Correction-Loop, Bestätigung (Phase 4)."""

from __future__ import annotations

from dataclasses import dataclass, field

from otio_app.defaults import (
    DRAMATURGY_ROLES,
    ENERGY_CHOICES,
    FACTUALITY_MODE_CHOICES,
    VOICEOVER_STATUS_PASS,
)
from otio_app.models import Project
from otio_app.services.inventory_loader import folder_has_usable_inventory_data
from otio_app.services.voiceover_generation.dramaturgy_service import load_confirmed_dramaturgy
from otio_app.services.voiceover_generation.folder_voiceover_settings_service import (
    build_default_folder_voiceover_settings,
    load_folder_voiceover_settings,
    save_folder_voiceover_settings,
    update_folder_voiceover_settings,
)
from otio_app.services.voiceover_generation.llm_trace_service import STATUS_PASS
from otio_app.services.voiceover_generation.model_settings_service import (
    load_model_settings,
    save_model_settings,
)
from otio_app.services.voiceover_generation.models import (
    DramaturgyPlan,
    FolderVoiceoverDraft,
    FolderVoiceoverSettingsDocument,
    FolderVoiceoverValidationReportsDocument,
    ProjectBrief,
    VoiceoverStyleProfile,
)
from otio_app.services.voiceover_generation.project_brief_service import load_project_brief
from otio_app.services.voiceover_generation.style_profile_service import load_style_profile
from otio_app.services.voiceover_generation.voiceover_author_service import (
    generate_all_folder_voiceovers,
    generate_folder_voiceover,
    is_draft_stale,
    load_folder_voiceovers_confirmed,
    load_folder_voiceovers_draft,
    update_folder_voiceover_text,
)
from otio_app.services.voiceover_generation.voiceover_review_service import (
    confirm_all_folder_voiceovers,
    confirm_folder_voiceover,
    load_validation_reports,
    run_folder_voiceover_review_loop,
    unconfirm_all_folder_voiceovers,
    unconfirm_folder_voiceover,
    validate_all_folder_voiceovers,
)
from otio_app.ui.project_context import render_project_selector
from otio_app.ui.voiceover_generation._shared import (
    render_llm_model_selectbox,
    require_without_voiceover_mode,
    style_profile_metric_value,
)

import streamlit as st


@dataclass
class _StaleCheckContext:
    """Bündelt alles, was is_draft_stale sonst PRO ORDNER neu von der Platte
    laden bzw. per ffprobe neu vermessen würde. Wird EINMAL pro Seiten-
    Rendering in render_folder_voiceovers_page() erzeugt und an jeden
    Drafts-Eintrag weitergereicht (Nutzerfeedback: lange Ladezeiten bei
    vielen Ordnern, Juli 2026). duration_cache lebt nur für dieses eine
    Rendering — kein Caching über Streamlit-Reruns hinweg, damit geänderte
    Mediendateien immer korrekt neu erkannt werden."""

    project_brief: ProjectBrief
    style_profile: VoiceoverStyleProfile | None
    confirmed_plan: DramaturgyPlan
    settings_doc: FolderVoiceoverSettingsDocument
    duration_cache: dict[str, float | None] = field(default_factory=dict)


def _render_settings_table(project: Project, settings_doc: FolderVoiceoverSettingsDocument) -> list[dict]:
    rows = [
        {
            "enabled": setting.enabled,
            "order_index": setting.order_index,
            "folder_name": setting.folder_name,
            "dramaturgy_role": setting.dramaturgy_role,
            "target_words": setting.target_words,
            "min_words": setting.min_words,
            "max_words": setting.max_words,
            "transition_from_previous": setting.transition_from_previous,
            "transition_to_next": setting.transition_to_next,
            "callback_to_previous": setting.callback_to_previous,
            "use_contrast_with_previous": setting.use_contrast_with_previous,
            "use_commonality_with_previous": setting.use_commonality_with_previous,
            "factuality_mode": setting.factuality_mode,
            "energy": setting.energy,
            "must_include": ", ".join(setting.must_include),
            "must_avoid": ", ".join(setting.must_avoid),
            "folder_extra_prompt": setting.folder_extra_prompt,
        }
        for setting in sorted(settings_doc.settings, key=lambda setting: setting.order_index)
    ]
    return st.data_editor(
        rows,
        key=f"vo_fvo_settings_editor_{project.id}",
        num_rows="fixed",
        use_container_width=True,
        column_config={
            "enabled": st.column_config.CheckboxColumn("Aktiv"),
            "order_index": st.column_config.NumberColumn("Reihenfolge", min_value=1, step=1),
            "folder_name": st.column_config.TextColumn("Ordner", disabled=True),
            "dramaturgy_role": st.column_config.SelectboxColumn("Rolle", options=list(DRAMATURGY_ROLES)),
            "target_words": st.column_config.NumberColumn("Ziel-Wörter", min_value=0, step=5),
            "min_words": st.column_config.NumberColumn("Min. Wörter", min_value=0, step=5),
            "max_words": st.column_config.NumberColumn("Max. Wörter", min_value=0, step=5),
            "transition_from_previous": st.column_config.CheckboxColumn(
                "Übergang von vorher",
                help=(
                    "Segue am ANFANG des Textes, das an den vorherigen Ort anknüpft. "
                    "Ergibt beim ersten Ort keinen Sinn (nichts kommt davor)."
                ),
            ),
            "transition_to_next": st.column_config.CheckboxColumn(
                "Übergang zum nächsten Kapitel",
                help=(
                    "Kurzer, nicht spoilernder Teaser am ENDE des Textes, der auf den "
                    "nächsten Ort neugierig macht. Ergibt beim letzten Ort keinen Sinn "
                    "(nichts kommt danach)."
                ),
            ),
            "callback_to_previous": st.column_config.CheckboxColumn(
                "Rückbezug",
                help="Erwähnung des vorherigen Ortes SPÄTER im Text (nicht am Anfang wie der Übergang).",
            ),
            "use_contrast_with_previous": st.column_config.CheckboxColumn("Kontrast"),
            "use_commonality_with_previous": st.column_config.CheckboxColumn("Gemeinsamkeit"),
            "factuality_mode": st.column_config.SelectboxColumn(
                "Faktentreue", options=list(FACTUALITY_MODE_CHOICES)
            ),
            "energy": st.column_config.SelectboxColumn("Energie", options=list(ENERGY_CHOICES)),
            "must_include": st.column_config.TextColumn("Muss enthalten (Komma-getrennt)"),
            "must_avoid": st.column_config.TextColumn("Muss vermeiden (Komma-getrennt)"),
            "folder_extra_prompt": st.column_config.TextColumn("Zusatzprompt"),
        },
    )


def _render_model_settings(project: Project) -> tuple[str, str, str, str]:
    settings = load_model_settings(project)
    with st.expander("⚙️ Modelle (Autor & Review)", expanded=False):
        col1, col2 = st.columns(2)
        with col1:
            st.markdown("**Voice-over Autor**")
            author_settings = render_llm_model_selectbox(
                label="Modell (Autor)",
                role_settings=settings.voiceover_author,
                key=f"vo_fvo_author_model_{project.id}",
            )
        with col2:
            st.markdown("**Voice-over Review**")
            review_settings = render_llm_model_selectbox(
                label="Modell (Review)",
                role_settings=settings.voiceover_review,
                key=f"vo_fvo_review_model_{project.id}",
            )
        if st.button("Modelle speichern", key=f"vo_fvo_models_save_{project.id}"):
            updated = settings.model_copy(
                update={
                    "voiceover_author": author_settings,
                    "voiceover_review": review_settings,
                }
            )
            save_model_settings(project, updated)
            st.success("Modell-Einstellungen gespeichert.")
    return (
        author_settings.provider,
        author_settings.model,
        review_settings.provider,
        review_settings.model,
    )


def _render_folder_draft(
    project: Project,
    folder_name: str,
    draft: FolderVoiceoverDraft,
    *,
    author_provider: str,
    author_model: str,
    review_provider: str,
    review_model: str,
    reports_document: FolderVoiceoverValidationReportsDocument,
    is_confirmed: bool,
    stale_check_context: "_StaleCheckContext",
) -> None:
    if is_draft_stale(
        project,
        folder_name,
        draft,
        project_brief=stale_check_context.project_brief,
        style_profile=stale_check_context.style_profile,
        plan=stale_check_context.confirmed_plan,
        settings_doc=stale_check_context.settings_doc,
        duration_cache=stale_check_context.duration_cache,
    ):
        st.warning(
            "Dieser Voice-over-Entwurf basiert möglicherweise auf veralteten Einstellungen."
        )

    text_key = f"vo_fvo_text_{folder_name}_{project.id}"
    if text_key not in st.session_state:
        st.session_state[text_key] = draft.voiceover_text_full
    text_value = st.text_area("Voice-over-Text", key=text_key, height=200)

    col1, col2, col3 = st.columns(3)
    with col1:
        st.metric("Wortanzahl", draft.word_count)
    with col2:
        st.metric("Status", draft.status)
    with col3:
        st.caption(f"author_run_id: `{draft.author_run_id}`")

    if draft.sentence_items:
        rows = [
            {
                "sentence_id": item.sentence_id,
                "text": item.text,
                "visual_intent": item.visual_intent,
                "primary_asset_id": item.primary_asset_id,
                "backup_asset_ids": ", ".join(item.backup_asset_ids),
                "asset_confidence": item.asset_confidence,
                "needs_supplement_asset": item.needs_supplement_asset,
                "supplement_reason": item.supplement_reason,
            }
            for item in draft.sentence_items
        ]
        st.dataframe(rows, use_container_width=True, hide_index=True)
    else:
        st.caption("Keine sentence_items vorhanden.")

    col_save, col_regen, col_validate, col_confirm, col_unconfirm = st.columns(5)
    with col_save:
        if st.button("Text speichern", key=f"vo_fvo_save_text_{folder_name}_{project.id}"):
            if text_value == draft.voiceover_text_full:
                st.info("Keine Änderung — Status bleibt unangetastet.")
            else:
                update_folder_voiceover_text(project, folder_name, text_value)
                st.success("Text geändert und gespeichert (Status: NEEDS_VALIDATION).")
                st.rerun()
    with col_regen:
        if st.button("Erneut generieren", key=f"vo_fvo_regen_{folder_name}_{project.id}"):
            with st.spinner("Wird neu erzeugt…"):
                result = generate_folder_voiceover(
                    project, folder_name, provider=author_provider, model=author_model
                )
            if result.status == STATUS_PASS:
                st.success("Neu erzeugt.")
            else:
                st.error(f"Fehlgeschlagen ({result.status}): {result.error}")
            st.rerun()
    with col_validate:
        if st.button("Validieren", key=f"vo_fvo_validate_{folder_name}_{project.id}"):
            with st.spinner("Wird validiert (Review-/Correction-Loop)…"):
                report = run_folder_voiceover_review_loop(
                    project, folder_name, provider=review_provider, model=review_model
                )
            if report.status == VOICEOVER_STATUS_PASS:
                st.success("Validierung bestanden (PASS).")
            else:
                st.warning(f"Status: {report.status} nach {report.attempt_count} Versuch(en).")
            st.rerun()
    with col_confirm:
        if st.button("Bestätigen", key=f"vo_fvo_confirm_{folder_name}_{project.id}"):
            confirm_folder_voiceover(project, folder_name)
            st.success("Bestätigt.")
            st.rerun()
    with col_unconfirm:
        if is_confirmed:
            if st.button("Bestätigung zurücknehmen", key=f"vo_fvo_unconfirm_{folder_name}_{project.id}"):
                unconfirm_folder_voiceover(project, folder_name)
                st.info("Bestätigung zurückgenommen.")
                st.rerun()

    st.markdown("**Review**")
    report = reports_document.reports.get(folder_name)
    if report is None:
        st.caption("Noch nicht validiert.")
    else:
        st.write(f"Status: **{report.status}** · Versuche: {report.attempt_count}")
        if report.errors:
            st.error(
                "Fehler:\n" + "\n".join(f"- [{error.type}] {error.message}" for error in report.errors)
            )
        if report.warnings:
            st.warning(
                "Warnungen:\n"
                + "\n".join(f"- [{warning.type}] {warning.message}" for warning in report.warnings)
            )
        st.caption(f"review_run_ids: {', '.join(report.review_run_ids) or '—'}")
        st.caption(f"correction_run_ids: {', '.join(report.correction_run_ids) or '—'}")


def render_folder_voiceovers_page() -> None:
    st.header("④ Folder Voice-overs")

    project = render_project_selector("Projekt")
    if project is None:
        return
    if not require_without_voiceover_mode(project):
        return

    st.subheader("Voraussetzungen")
    brief = load_project_brief(project)
    style_profile = load_style_profile(project)
    confirmed_plan = load_confirmed_dramaturgy(project)
    settings_doc = load_folder_voiceover_settings(project)

    col1, col2, col3, col4 = st.columns(4)
    with col1:
        st.metric("Project Brief", "✓" if (brief.video_title or brief.tone_tags) else "—")
    with col2:
        st.metric("Style Profile", style_profile_metric_value(style_profile))
    with col3:
        st.metric("Dramaturgie bestätigt", "✓" if confirmed_plan is not None else "—")
    with col4:
        active_count = (
            sum(1 for entry in confirmed_plan.recommended_folder_order if entry.enabled)
            if confirmed_plan is not None
            else 0
        )
        st.metric("Aktive Ordner", active_count)

    if confirmed_plan is None:
        st.warning("Bitte zuerst die Dramaturgie bestätigen.")
        return

    if not brief.video_title and not brief.tone_tags:
        st.info(
            "Hinweis: Kein Project Brief ausgefüllt — Voice-overs werden mit neutralem "
            "Kontext erzeugt."
        )
    if style_profile is None:
        st.info("Hinweis: Kein Style Profile vorhanden — Voice-overs nutzen einen neutralen Stil.")

    active_entries = sorted(
        (entry for entry in confirmed_plan.recommended_folder_order if entry.enabled),
        key=lambda entry: entry.order_index,
    )
    inactive_entries = [
        entry for entry in confirmed_plan.recommended_folder_order if not entry.enabled
    ]
    if inactive_entries:
        st.caption(
            "Deaktivierte Ordner (nicht Teil der automatischen Generierung): "
            + ", ".join(entry.folder_name for entry in inactive_entries)
        )

    missing_inventory = [
        entry.folder_name
        for entry in active_entries
        if not folder_has_usable_inventory_data(project, entry.folder_name)
    ]
    if missing_inventory:
        st.warning("Für folgende aktive Ordner fehlt das Inventory: " + ", ".join(missing_inventory))

    st.subheader("Folder Voice-over Settings")
    if settings_doc is None:
        st.info("Noch keine Settings vorhanden.")
        if st.button(
            "Folder Voice-over Settings aus Dramaturgie erstellen",
            key=f"vo_fvo_settings_create_{project.id}",
        ):
            new_settings = build_default_folder_voiceover_settings(project)
            save_folder_voiceover_settings(project, new_settings)
            st.success("Settings erstellt.")
            st.rerun()
        return

    edited_rows = _render_settings_table(project, settings_doc)

    col_save, col_regen = st.columns(2)
    with col_save:
        if st.button("Settings speichern", key=f"vo_fvo_settings_save_{project.id}"):
            update_folder_voiceover_settings(project, edited_rows)
            st.success("Settings gespeichert.")
            st.rerun()
    with col_regen:
        if st.button(
            "Settings aus Dramaturgie neu erzeugen", key=f"vo_fvo_settings_regen_{project.id}"
        ):
            new_settings = build_default_folder_voiceover_settings(project)
            save_folder_voiceover_settings(project, new_settings)
            st.success("Settings neu aus Dramaturgie erzeugt.")
            st.rerun()

    author_provider, author_model, review_provider, review_model = _render_model_settings(project)

    st.subheader("Generierung")
    enabled_names = [setting.folder_name for setting in settings_doc.settings if setting.enabled]
    if not enabled_names:
        st.warning("Keine aktiven Ordner in den Settings.")
        return

    col_gen_one, col_gen_all = st.columns(2)
    with col_gen_one:
        selected_folder = st.selectbox(
            "Ordner auswählen", options=enabled_names, key=f"vo_fvo_select_{project.id}"
        )
        if st.button(
            "Voice-over für ausgewählten Ordner generieren",
            key=f"vo_fvo_generate_one_{project.id}",
        ):
            with st.spinner(f"Voice-over für „{selected_folder}“ wird erzeugt…"):
                result = generate_folder_voiceover(
                    project, selected_folder, provider=author_provider, model=author_model
                )
            if result.status == STATUS_PASS:
                st.success(f"Voice-over für „{selected_folder}“ erzeugt.")
            else:
                st.error(f"Fehlgeschlagen ({result.status}): {result.error}")
            st.rerun()
    with col_gen_all:
        st.caption("Läuft sequenziell — ein Ordner nach dem anderen.")
        if st.button(
            "Alle aktiven Folder Voice-overs generieren", key=f"vo_fvo_generate_all_{project.id}"
        ):
            progress_placeholder = st.empty()

            def _progress(folder_name: str, index: int, total: int) -> None:
                progress_placeholder.info(f"Ordner {index}/{total}: „{folder_name}“ läuft…")

            with st.spinner("Voice-overs werden erzeugt…"):
                results = generate_all_folder_voiceovers(
                    project,
                    provider=author_provider,
                    model=author_model,
                    progress_callback=_progress,
                )
            progress_placeholder.empty()
            pass_count = sum(1 for result in results if result.status == STATUS_PASS)
            st.success(f"{pass_count}/{len(results)} Ordner erfolgreich erzeugt.")
            for result in results:
                if result.status != STATUS_PASS:
                    st.error(f"Fehlgeschlagen: {result.error}")
            st.rerun()

    st.subheader("Drafts")
    draft_document = load_folder_voiceovers_draft(project)
    confirmed_document = load_folder_voiceovers_confirmed(project)
    confirmed_names = {item.folder_name for item in confirmed_document.items}
    reports_document = load_validation_reports(project)

    # Einmal pro Rendering: brief/style_profile/confirmed_plan/settings_doc
    # sind oben bereits geladen — hier gebündelt für is_draft_stale, damit sie
    # nicht erneut pro Ordner von der Platte gelesen werden. duration_cache
    # sorgt zusätzlich dafür, dass dieselbe Videodatei innerhalb dieses einen
    # Renderings nur einmal per ffprobe vermessen wird (statt einmal pro
    # Ordner, in dem sie vorkommt).
    stale_check_context = _StaleCheckContext(
        project_brief=brief,
        style_profile=style_profile,
        confirmed_plan=confirmed_plan,
        settings_doc=settings_doc,
    )

    for entry in active_entries:
        draft = next(
            (item for item in draft_document.items if item.folder_name == entry.folder_name), None
        )
        status_label = draft.status if draft is not None else "Kein Entwurf"
        with st.expander(f"{entry.order_index}. {entry.folder_name} — {status_label}"):
            if draft is None:
                st.info("Noch kein Voice-over-Entwurf für diesen Ordner.")
                continue
            _render_folder_draft(
                project,
                entry.folder_name,
                draft,
                author_provider=author_provider,
                author_model=author_model,
                review_provider=review_provider,
                review_model=review_model,
                reports_document=reports_document,
                is_confirmed=entry.folder_name in confirmed_names,
                stale_check_context=stale_check_context,
            )

    _render_bulk_draft_actions(
        project,
        active_entries,
        draft_document=draft_document,
        confirmed_names=confirmed_names,
        author_provider=author_provider,
        author_model=author_model,
        review_provider=review_provider,
        review_model=review_model,
    )


def _render_bulk_draft_actions(
    project: Project,
    active_entries: list,
    *,
    draft_document,
    confirmed_names: set[str],
    author_provider: str,
    author_model: str,
    review_provider: str,
    review_model: str,
) -> None:
    """Sammel-Aktionen für ALLE Ordner gleichzeitig, unterhalb der Drafts-
    Liste (Nutzerfeedback: 'Ich will unterhalb der Drafts alle gleichzeitig
    speichern, bestätigen, validieren etc. können'). Läuft — wie schon bei
    'Alle aktiven Folder Voice-overs generieren' oben — sequenziell und
    blockierend (kein Abbrechen-Button; das würde einen Hintergrund-Job
    erfordern, siehe Diagnose-Gespräch)."""
    has_any_draft = any(
        next((item for item in draft_document.items if item.folder_name == entry.folder_name), None)
        is not None
        for entry in active_entries
    )

    st.subheader("Alle Ordner gleichzeitig")
    st.caption(
        "Wirkt auf alle aktiven Ordner mit vorhandenem Entwurf (bzw. bestätigte Ordner bei "
        "„Bestätigung zurücknehmen“) — läuft sequenziell, ein Ordner nach dem anderen, ohne "
        "Abbrechen-Möglichkeit."
    )
    col_save_all, col_regen_all, col_validate_all, col_confirm_all, col_unconfirm_all = st.columns(5)

    with col_save_all:
        if st.button(
            "Alle Texte speichern",
            key=f"vo_fvo_save_all_{project.id}",
            disabled=not has_any_draft,
        ):
            checked_count = 0
            changed_count = 0
            for entry in active_entries:
                draft = next(
                    (item for item in draft_document.items if item.folder_name == entry.folder_name),
                    None,
                )
                if draft is None:
                    continue
                checked_count += 1
                text_key = f"vo_fvo_text_{entry.folder_name}_{project.id}"
                text_value = st.session_state.get(text_key, draft.voiceover_text_full)
                if text_value != draft.voiceover_text_full:
                    changed_count += 1
                # Unveränderten Text erneut zu speichern ist ein No-Op (siehe
                # update_folder_voiceover_text) — setzt Status NICHT zurück,
                # z. B. nicht von CONFIRMED auf NEEDS_VALIDATION.
                update_folder_voiceover_text(project, entry.folder_name, text_value)
            if changed_count:
                st.success(
                    f"{changed_count}/{checked_count} Ordner-Texte geändert und gespeichert "
                    "(Status: NEEDS_VALIDATION). Unveränderte Ordner bleiben unangetastet."
                )
            else:
                st.info(f"Keine Änderungen an {checked_count} geprüften Ordner-Texten gefunden.")
            st.rerun()

    with col_regen_all:
        if st.button("Alle neu generieren", key=f"vo_fvo_regen_all_{project.id}"):
            progress_placeholder = st.empty()

            def _progress_regen(folder_name: str, index: int, total: int) -> None:
                progress_placeholder.info(f"Ordner {index}/{total}: „{folder_name}“ läuft…")

            with st.spinner("Voice-overs werden neu erzeugt…"):
                results = generate_all_folder_voiceovers(
                    project,
                    provider=author_provider,
                    model=author_model,
                    progress_callback=_progress_regen,
                )
            progress_placeholder.empty()
            pass_count = sum(1 for result in results if result.status == STATUS_PASS)
            st.success(f"{pass_count}/{len(results)} Ordner erfolgreich neu erzeugt.")
            for result in results:
                if result.status != STATUS_PASS:
                    st.error(f"Fehlgeschlagen: {result.error}")
            st.rerun()

    with col_validate_all:
        if st.button(
            "Alle validieren",
            key=f"vo_fvo_validate_all_{project.id}",
            disabled=not has_any_draft,
        ):
            progress_placeholder = st.empty()

            def _progress_validate(folder_name: str, index: int, total: int) -> None:
                progress_placeholder.info(f"Ordner {index}/{total}: „{folder_name}“ wird validiert…")

            with st.spinner("Validierung läuft (Review-/Correction-Loop pro Ordner)…"):
                reports = validate_all_folder_voiceovers(
                    project,
                    provider=review_provider,
                    model=review_model,
                    progress_callback=_progress_validate,
                )
            progress_placeholder.empty()
            pass_count = sum(1 for report in reports if report.status == VOICEOVER_STATUS_PASS)
            st.success(f"{pass_count}/{len(reports)} Ordner validiert (PASS).")
            for report in reports:
                if report.status != VOICEOVER_STATUS_PASS:
                    st.warning(
                        f"„{report.folder_name}“: {report.status} nach "
                        f"{report.attempt_count} Versuch(en)."
                    )
            st.rerun()

    with col_confirm_all:
        if st.button(
            "Alle bestätigen",
            key=f"vo_fvo_confirm_all_{project.id}",
            disabled=not has_any_draft,
        ):
            with st.spinner("Ordner werden bestätigt…"):
                results = confirm_all_folder_voiceovers(project)
            st.success(f"{len(results)} Ordner bestätigt (ohne vorherige Validierungspflicht).")
            st.rerun()

    with col_unconfirm_all:
        if st.button(
            "Alle Bestätigungen zurücknehmen",
            key=f"vo_fvo_unconfirm_all_{project.id}",
            disabled=not confirmed_names,
        ):
            with st.spinner("Bestätigungen werden zurückgenommen…"):
                results = unconfirm_all_folder_voiceovers(project)
            st.info(f"{len(results)} Bestätigung(en) zurückgenommen.")
            st.rerun()
