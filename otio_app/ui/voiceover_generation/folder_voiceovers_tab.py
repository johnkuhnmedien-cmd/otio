"""Voice-over pro Ordner: Settings, Generierung, Review-/Correction-Loop, Bestätigung (Phase 4)."""

from __future__ import annotations

from dataclasses import dataclass, field

from otio_app.defaults import (
    DRAMATURGY_ROLES,
    ENERGY_CHOICES,
    FACTUALITY_MODE_CHOICES,
    FOLDER_ASSET_READINESS_HIGH_ISSUE_REGEN_THRESHOLD,
    MAX_ASSET_ALLOCATION_CORRECTION_ATTEMPTS,
    SEGMENT_ASSET_PLANNING_MODE_CHOICES,
    SEGMENT_ASSET_PLANNING_MODE_LABELS,
    VOICEOVER_GEN_DEFAULT_FOLDER_MAX_WORDS,
    VOICEOVER_GEN_DEFAULT_FOLDER_MIN_WORDS,
    VOICEOVER_GEN_DEFAULT_FOLDER_TARGET_WORDS,
    VOICEOVER_STATUS_PASS,
)
from otio_app.models import Project
from otio_app.services.inventory_loader import folder_has_usable_inventory_data
from otio_app.services.voiceover_generation.dramaturgy_service import load_confirmed_dramaturgy
from otio_app.services.voiceover_generation.folder_asset_allocation_correction_service import (
    ASSET_ALLOCATION_CORRECTION_STATUS_NEEDS_USER_REVIEW,
    ASSET_ALLOCATION_CORRECTION_STATUS_PASS,
    run_all_asset_allocation_corrections,
    run_asset_allocation_correction,
)
from otio_app.services.voiceover_generation.folder_asset_readiness import (
    READINESS_STATUS_PASS as ASSET_READINESS_STATUS_PASS,
    FolderAssetReadinessReport,
    build_all_folder_asset_readiness_reports,
    build_folder_asset_readiness_report,
)
from otio_app.services.voiceover_generation.folder_voiceover_settings_service import (
    apply_standard_word_target_to_enabled_settings,
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
    regenerate_all_folder_voiceovers_with_standard_word_target,
    regenerate_folder_voiceover_with_standard_word_target,
    regenerate_high_issue_folders_with_strict_inventory,
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
    render_new_feature_button,
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
            "segment_asset_planning_mode": setting.segment_asset_planning_mode,
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
            "segment_asset_planning_mode": st.column_config.SelectboxColumn(
                "Shot-Planung",
                options=list(SEGMENT_ASSET_PLANNING_MODE_CHOICES),
                format_func=lambda mode: SEGMENT_ASSET_PLANNING_MODE_LABELS.get(mode, mode),
                help=(
                    "Steuert, wie das Autor-LLM mit der Aufteilung eines Satzes in mehrere "
                    "Shots umgeht: 'Wie bisher' plant nur ein Asset pro Satz; 'Aktiv pro "
                    "Segment aufteilen' plant bewusst mehrere Shots mit unterschiedlichen "
                    "Assets; 'LLM entscheidet' wägt pro Satz ab (abwechslungsreich, aber "
                    "nicht unruhig)."
                ),
            ),
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


def _render_closing_visual_plan_section(draft: FolderVoiceoverDraft) -> None:
    """Nutzervorgabe (Juli 2026, "kein closing asset nach dem letzten
    Satz, der die Pause ausfüllt"): zeigt den geplanten Abschluss-Shot
    (siehe ClosingVisualPlan) an — rein informativ, keine eigene
    Bearbeitung hier (Text/Assets werden über 'Erneut generieren'/den
    späteren Correction-Loop angepasst, nicht manuell in diesem Panel)."""
    st.markdown("**Closing Shot** (visueller Abschluss nach dem letzten Satz, kein eigener Text)")
    plan = draft.closing_visual_plan
    if not plan.primary_asset_id and not plan.needs_supplement_asset:
        st.warning(
            "Kein Closing Shot geplant — die Pause nach dem letzten Satz bleibt visuell "
            "ungedeckt, falls das letzte Satz-Asset nicht lang genug gehalten werden kann. "
            "Bitte erneut generieren."
        )
        return
    col1, col2 = st.columns(2)
    with col1:
        st.write(f"**Visual Intent:** {plan.visual_intent or '—'}")
        st.write(f"**Primary Asset:** {plan.primary_asset_id or '—'}")
        st.write(f"**Backup Assets:** {', '.join(plan.backup_asset_ids) or '—'}")
        st.write(f"**Second Backup Assets:** {', '.join(plan.second_backup_asset_ids) or '—'}")
    with col2:
        if plan.needs_supplement_asset:
            st.warning(f"Supplement empfohlen: {plan.supplement_reason or '(kein Grund angegeben)'}")
            if plan.supplement_search_hint:
                st.caption(f"Suchvorschlag: `{plan.supplement_search_hint}`")
        else:
            st.success("Lokales Asset zugeordnet — kein Supplement nötig.")
        if plan.asset_strategy_reason:
            st.caption(plan.asset_strategy_reason)


def _asset_readiness_session_key(project: Project, folder_name: str) -> str:
    return f"vo_fvo_asset_readiness_{folder_name}_{project.id}"


def _asset_readiness_all_session_key(project: Project) -> str:
    return f"vo_fvo_asset_readiness_all_{project.id}"


def _folder_voiceover_text_widget_key(project: Project, folder_name: str) -> str:
    return f"vo_fvo_text_{folder_name}_{project.id}"


def _folder_voiceover_text_sync_key(project: Project, folder_name: str) -> str:
    return f"vo_fvo_text_sync_{folder_name}_{project.id}"


def _folder_draft_open_key(project: Project, folder_name: str) -> str:
    """Session-Flag: schwere Draft-UI (Textfeld, Tabelle, Buttons) nur laden,
    wenn der Nutzer den Ordner explizit geöffnet hat — sonst würde Streamlit
    bei JEDEM Seiten-Rerun alle Expander-Bodies inkl. is_draft_stale/
    Inventory/ffprobe ausführen (auch zugeklappt)."""
    return f"vo_fvo_draft_open_{folder_name}_{project.id}"


def folder_voiceover_text_draft_token(draft: FolderVoiceoverDraft) -> str:
    """Identifiziert den persistierten Text-Stand eines Drafts.

    Wird genutzt, um das Streamlit-Textfeld nach Regenerieren / Review-
    Correction / Asset-Allokations-Correction auf den neuen Draft zu
    synchronisieren — ohne ungespeicherte Tipparbeit zu verwerfen, solange
    sich der Draft auf der Platte nicht geändert hat."""
    correction_part = "|".join(draft.correction_run_ids)
    updated_at = draft.updated_at.isoformat() if draft.updated_at is not None else ""
    return f"{draft.author_run_id}|{correction_part}|{updated_at}|{draft.word_count}|{len(draft.voiceover_text_full)}"


def _sync_folder_voiceover_text_widget(
    project: Project, folder_name: str, draft: FolderVoiceoverDraft
) -> str:
    """Stellt sicher, dass ``st.session_state[text_key]`` zum aktuellen Draft
    passt, BEVOR das ``st.text_area`` gerendert wird.

    Bug (Juli 2026): das Textfeld wurde nur beim ersten Besuch befüllt
    (``if key not in session_state``). Nach „Erneut generieren“ / Bulk-
    Asset-Allokation blieb alter Session-Text stehen, während Wortanzahl,
    Satz-Tabelle und Closing Shot den neuen Draft zeigten."""
    text_key = _folder_voiceover_text_widget_key(project, folder_name)
    sync_key = _folder_voiceover_text_sync_key(project, folder_name)
    token = folder_voiceover_text_draft_token(draft)
    if st.session_state.get(sync_key) != token:
        st.session_state[text_key] = draft.voiceover_text_full
        st.session_state[sync_key] = token
    return text_key


def _render_bulk_asset_readiness_summary(reports: list[FolderAssetReadinessReport]) -> None:
    """Kompakte Übersicht nach „Alle Asset-Readiness prüfen“."""
    pass_count = sum(1 for report in reports if report.status == ASSET_READINESS_STATUS_PASS)
    needs_review_count = len(reports) - pass_count
    if needs_review_count == 0:
        st.success(f"Asset-Readiness alle Ordner: PASS ({pass_count}/{len(reports)}).")
    else:
        st.warning(
            f"Asset-Readiness alle Ordner: {needs_review_count}/{len(reports)} mit "
            "NEEDS_REVIEW — Details unten und in den einzelnen Ordner-Expandern."
        )
    rows = [
        {
            "folder": report.folder_name,
            "status": report.status,
            "issues": len(report.issues),
            "closing_shot_fehlt": report.closing_shot_missing_count,
            "reuse_konflikt": report.closing_shot_reuse_conflict_count,
            "über_limit": report.asset_over_folder_limit_count,
            "abstand_kurz": report.asset_reuse_distance_violation_count,
            "knappe_assets": report.scarce_asset_conflict_count,
        }
        for report in reports
    ]
    st.dataframe(rows, use_container_width=True, hide_index=True)


def _render_asset_readiness_report(report: FolderAssetReadinessReport) -> None:
    col1, col2, col3, col4, col5 = st.columns(5)
    with col1:
        st.metric("Sätze", report.sentence_count)
    with col2:
        st.metric("Mit Primary", report.with_primary_count)
    with col3:
        st.metric("Mit Backup", report.with_backup_count)
    with col4:
        st.metric("Direkte Wiederholungen", report.direct_repeat_count)
    with col5:
        st.metric("Supplement empfohlen", report.supplement_recommended_count)

    # Nutzervorgabe (Juli 2026): Closing-Shot- und folder-weite Asset-
    # Allokations-Zähler — siehe ClosingVisualPlan/FolderAssetReadinessReport.
    col6, col7, col8, col9 = st.columns(4)
    with col6:
        st.metric("Closing Shot fehlt", report.closing_shot_missing_count)
    with col7:
        st.metric("Closing wiederholt Satz", report.closing_shot_reuse_conflict_count)
    with col8:
        st.metric("Asset über Nutzungslimit", report.asset_over_folder_limit_count)
    with col9:
        st.metric("Abstand zu kurz", report.asset_reuse_distance_violation_count)

    if report.status == ASSET_READINESS_STATUS_PASS:
        st.success("Asset-Readiness: PASS — keine Auffälligkeiten gefunden.")
    else:
        st.warning(
            f"Asset-Readiness: NEEDS_REVIEW — {len(report.issues)} Auffälligkeit(en) gefunden "
            f"(davon {report.invalid_asset_id_count} ungültige Asset-ID(s), "
            f"{report.long_sentence_low_alternative_count} lange(r) Satz/Sätze mit zu wenig "
            f"Alternativen, {report.scarce_asset_conflict_count} knappe(s) Asset(s) an "
            "flexiblere(n) Satz/Sätze vergeben)."
        )

    if report.issues:
        rows = [
            {
                "sentence_id": issue.sentence_id,
                "issue_type": issue.issue_type,
                "message": issue.message,
            }
            for issue in report.issues
        ]
        st.dataframe(rows, use_container_width=True, hide_index=True)


def _render_asset_readiness_section(
    project: Project,
    folder_name: str,
    draft: FolderVoiceoverDraft,
    *,
    author_provider: str,
    author_model: str,
) -> None:
    """Phase 2 (Asset-bewusste Cut-Plan-Vorbereitung): rein lesende
    Diagnose, NUR bei explizitem Klick berechnet — läuft nie automatisch
    beim Seiten-Rendering, ruft kein LLM auf und schreibt nichts. Ergebnis
    bleibt bis zum nächsten Klick/Reload nur im Session-State erhalten
    (keine Persistenz auf Platte), analog zu anderen 'letztes Ergebnis'-
    Anzeigen dieser Pipeline (z. B. Dramaturgie-/Style-Profile-Läufe).

    Nutzervorgabe (Juli 2026): direkt darunter ein optionaler Correction-
    Button (siehe folder_asset_allocation_correction_service.py) — läuft
    NUR bei explizitem Klick und NUR, solange die zwischengespeicherte
    Diagnose NEEDS_REVIEW zeigt."""
    session_key = _asset_readiness_session_key(project, folder_name)
    if render_new_feature_button(
        "🟢 Asset-Readiness prüfen",
        key=f"vo_fvo_asset_readiness_btn_{folder_name}_{project.id}",
        help="NEU: prüft rein lesend (kein LLM-Aufruf, keine Änderung), ob jeder Satz ein "
        "gültiges lokales Primary-/Backup-Asset hat, ob dasselbe Asset direkt wiederholt "
        "wird und ob lange Sätze genug Asset-Alternativen für einen späteren Split haben.",
    ):
        st.session_state[session_key] = build_folder_asset_readiness_report(project, draft)

    cached_report = st.session_state.get(session_key)
    if isinstance(cached_report, FolderAssetReadinessReport) and cached_report.folder_name == folder_name:
        _render_asset_readiness_report(cached_report)

        if cached_report.status != ASSET_READINESS_STATUS_PASS and render_new_feature_button(
            "🤖 Asset-Allokation per LLM reparieren",
            key=f"vo_fvo_asset_allocation_correction_btn_{folder_name}_{project.id}",
            help="NEU: repariert GEZIELT die Asset-Zuordnung (inkl. Closing Shot) anhand der "
            "obigen Auffälligkeiten — lässt den redaktionellen Text möglichst unverändert. "
            f"Läuft bis zu {MAX_ASSET_ALLOCATION_CORRECTION_ATTEMPTS} Versuche.",
        ):
            with st.spinner("Asset-Allokation wird per LLM repariert…"):
                try:
                    result = run_asset_allocation_correction(
                        project, folder_name, provider=author_provider, model=author_model
                    )
                except ValueError as exc:
                    st.error(str(exc))
                else:
                    st.session_state[session_key] = build_folder_asset_readiness_report(
                        project, result.draft
                    )
                    if result.status == ASSET_ALLOCATION_CORRECTION_STATUS_PASS:
                        st.success(f"Asset-Allokation reparieren: PASS nach {result.attempt_count} Versuch(en).")
                    elif result.status == ASSET_ALLOCATION_CORRECTION_STATUS_NEEDS_USER_REVIEW:
                        st.warning(
                            f"Nach {result.attempt_count} Versuch(en) bleiben "
                            f"{len(result.remaining_issues)} Auffälligkeit(en) — bitte manuell prüfen."
                        )
                    else:
                        st.error(f"Fehlgeschlagen: {result.error}")
                    st.rerun()


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

    text_key = _sync_folder_voiceover_text_widget(project, folder_name, draft)
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
                "second_backup_asset_ids": ", ".join(item.second_backup_asset_ids),
                "asset_confidence": item.asset_confidence,
                "needs_supplement_asset": item.needs_supplement_asset,
                "supplement_reason": item.supplement_reason,
                "reuse_risk": item.visual_asset_plan.reuse_risk,
                "supplement_search_hint": item.visual_asset_plan.supplement_search_hint,
            }
            for item in draft.sentence_items
        ]
        st.dataframe(rows, use_container_width=True, hide_index=True)
    else:
        st.caption("Keine sentence_items vorhanden.")

    _render_closing_visual_plan_section(draft)

    _render_asset_readiness_section(
        project, folder_name, draft, author_provider=author_provider, author_model=author_model
    )

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

    if render_new_feature_button(
        "🟢 Asset-bewusst neu generieren (135 Wörter)",
        key=f"vo_fvo_regen_asset_aware_{folder_name}_{project.id}",
        help="NEU: hebt NUR diesen Ordner zuerst auf die neue Standard-Zielwortanzahl "
        "(135, min 120, max 150) und generiert danach in einem Schritt neu — nutzt "
        "automatisch den bereits asset-bewussten Prompt (kein neuer Prompt, nur ein "
        "Komfort-Klick für bereits vor dieser Änderung generierte Ordner).",
    ):
        with st.spinner("Zielwortanzahl wird angehoben und neu erzeugt…"):
            try:
                result = regenerate_folder_voiceover_with_standard_word_target(
                    project, folder_name, provider=author_provider, model=author_model
                )
            except ValueError as exc:
                st.error(str(exc))
            else:
                if result.status == STATUS_PASS:
                    st.success("Zielwortanzahl angehoben und neu erzeugt.")
                else:
                    st.error(f"Fehlgeschlagen ({result.status}): {result.error}")
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

    col_save, col_regen, col_word_target = st.columns(3)
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
    with col_word_target:
        if render_new_feature_button(
            "🟢 Zielwortanzahl 135 auf alle aktiven Folder anwenden",
            key=f"vo_fvo_apply_word_target_{project.id}",
            help=f"NEU: setzt target_words/min_words/max_words für alle aktivierten Ordner explizit auf "
            f"{VOICEOVER_GEN_DEFAULT_FOLDER_TARGET_WORDS} (min {VOICEOVER_GEN_DEFAULT_FOLDER_MIN_WORDS}, "
            f"max {VOICEOVER_GEN_DEFAULT_FOLDER_MAX_WORDS}) — kürzer und flexibler für den späteren Cut "
            "Plan. Ändert NUR die Settings, nicht sofort die bereits erzeugten Texte. Deaktivierte Ordner "
            "und alle anderen Settings-Felder bleiben unverändert.",
        ):
            apply_standard_word_target_to_enabled_settings(project)
            st.success(
                f"Zielwortanzahl {VOICEOVER_GEN_DEFAULT_FOLDER_TARGET_WORDS} "
                f"(min {VOICEOVER_GEN_DEFAULT_FOLDER_MIN_WORDS}, max {VOICEOVER_GEN_DEFAULT_FOLDER_MAX_WORDS}) "
                "auf alle aktiven Ordner angewendet. Texte wurden nicht verändert."
            )
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
    st.caption(
        "Ordner-Details werden erst nach „Öffnen“ geladen — sonst baut Streamlit bei jedem "
        "Klick alle Drafts neu (auch zugeklappte Expander). „Schließen“ entlädt die schwere UI wieder."
    )
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

            open_key = _folder_draft_open_key(project, entry.folder_name)
            if not st.session_state.get(open_key):
                meta_cols = st.columns([1, 1, 3])
                with meta_cols[0]:
                    if render_new_feature_button(
                        "🟢 Öffnen",
                        key=f"vo_fvo_open_draft_{entry.folder_name}_{project.id}",
                        help="NEU: lädt Textfeld, Satz-Tabelle und Aktionen erst jetzt — "
                        "hält die Seite bei vielen Ordnern schnell.",
                    ):
                        st.session_state[open_key] = True
                        st.rerun()
                with meta_cols[1]:
                    st.metric("Wörter", draft.word_count)
                with meta_cols[2]:
                    st.caption(
                        f"{len(draft.sentence_items)} Sätze · Status `{draft.status}` · "
                        "Details noch nicht geladen."
                    )
                continue

            close_cols = st.columns([1, 4])
            with close_cols[0]:
                if st.button(
                    "Schließen",
                    key=f"vo_fvo_close_draft_{entry.folder_name}_{project.id}",
                    help="Entlädt die Draft-Details wieder, damit die Seite leicht bleibt.",
                ):
                    st.session_state[open_key] = False
                    st.rerun()

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
                text_key = _folder_voiceover_text_widget_key(project, entry.folder_name)
                sync_key = _folder_voiceover_text_sync_key(project, entry.folder_name)
                # Verhindert, dass ein veralteter Session-Text (vor Sync des
                # Textfelds) einen frisch regenerierten/korrigierten Draft
                # überschreibt — siehe _sync_folder_voiceover_text_widget.
                if st.session_state.get(sync_key) != folder_voiceover_text_draft_token(draft):
                    text_value = draft.voiceover_text_full
                else:
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

    if render_new_feature_button(
        "🟢 Alle asset-bewusst neu generieren (135 Wörter)",
        key=f"vo_fvo_regen_all_asset_aware_{project.id}",
        help="NEU: hebt ALLE aktivierten Ordner zuerst auf die neue Standard-"
        "Zielwortanzahl (135, min 120, max 150) und generiert danach sequenziell "
        "neu — Komfort-Aktion für bereits vor dieser Änderung generierte Projekte.",
    ):
        progress_placeholder = st.empty()

        def _progress_regen_asset_aware(folder_name: str, index: int, total: int) -> None:
            progress_placeholder.info(f"Ordner {index}/{total}: „{folder_name}“ läuft…")

        with st.spinner("Zielwortanzahl wird für alle aktiven Ordner angehoben und neu erzeugt…"):
            try:
                results = regenerate_all_folder_voiceovers_with_standard_word_target(
                    project,
                    provider=author_provider,
                    model=author_model,
                    progress_callback=_progress_regen_asset_aware,
                )
            except ValueError as exc:
                progress_placeholder.empty()
                st.error(str(exc))
            else:
                progress_placeholder.empty()
                pass_count = sum(1 for result in results if result.status == STATUS_PASS)
                st.success(
                    f"Zielwortanzahl angehoben — {pass_count}/{len(results)} Ordner erfolgreich "
                    "neu erzeugt."
                )
                for result in results:
                    if result.status != STATUS_PASS:
                        st.error(f"Fehlgeschlagen: {result.error}")
                st.rerun()

    col_readiness_all, col_allocation_all = st.columns(2)
    with col_readiness_all:
        if render_new_feature_button(
            "🟢 Alle Asset-Readiness prüfen",
            key=f"vo_fvo_asset_readiness_all_btn_{project.id}",
            help="NEU: prüft rein lesend (kein LLM, keine Änderung) ALLE aktiven Ordner mit "
            "Entwurf — speichert die Diagnose auch in den einzelnen Ordner-Expandern.",
            disabled=not has_any_draft,
        ):
            progress_placeholder = st.empty()

            def _progress_readiness(folder_name: str, index: int, total: int) -> None:
                progress_placeholder.info(
                    f"Ordner {index}/{total}: „{folder_name}“ Asset-Readiness…"
                )

            with st.spinner("Asset-Readiness für alle Ordner…"):
                try:
                    reports = build_all_folder_asset_readiness_reports(
                        project, progress_callback=_progress_readiness
                    )
                except ValueError as exc:
                    progress_placeholder.empty()
                    st.error(str(exc))
                else:
                    progress_placeholder.empty()
                    for report in reports:
                        st.session_state[_asset_readiness_session_key(project, report.folder_name)] = (
                            report
                        )
                    st.session_state[_asset_readiness_all_session_key(project)] = reports
                    st.rerun()

    with col_allocation_all:
        if render_new_feature_button(
            "🤖 Alle Asset-Allokation per LLM reparieren",
            key=f"vo_fvo_asset_allocation_all_btn_{project.id}",
            help="NEU: läuft sequenziell über alle aktiven Ordner mit Entwurf — Ordner ohne "
            "Auffälligkeiten werden ohne LLM übersprungen (PASS). "
            f"Pro Ordner bis zu {MAX_ASSET_ALLOCATION_CORRECTION_ATTEMPTS} Versuche.",
            disabled=not has_any_draft,
        ):
            progress_placeholder = st.empty()

            def _progress_allocation(folder_name: str, index: int, total: int) -> None:
                progress_placeholder.info(
                    f"Ordner {index}/{total}: „{folder_name}“ Asset-Allokation…"
                )

            with st.spinner("Asset-Allokation für alle Ordner wird repariert…"):
                try:
                    results = run_all_asset_allocation_corrections(
                        project,
                        provider=author_provider,
                        model=author_model,
                        progress_callback=_progress_allocation,
                    )
                except ValueError as exc:
                    progress_placeholder.empty()
                    st.error(str(exc))
                else:
                    progress_placeholder.empty()
                    refreshed_reports: list[FolderAssetReadinessReport] = []
                    for result in results:
                        report = build_folder_asset_readiness_report(project, result.draft)
                        refreshed_reports.append(report)
                        st.session_state[
                            _asset_readiness_session_key(project, result.draft.folder_name)
                        ] = report
                    st.session_state[_asset_readiness_all_session_key(project)] = refreshed_reports

                    pass_count = sum(
                        1 for result in results if result.status == ASSET_ALLOCATION_CORRECTION_STATUS_PASS
                    )
                    review_count = sum(
                        1
                        for result in results
                        if result.status == ASSET_ALLOCATION_CORRECTION_STATUS_NEEDS_USER_REVIEW
                    )
                    fail_count = len(results) - pass_count - review_count
                    st.success(
                        f"Asset-Allokation alle Ordner: {pass_count} PASS, "
                        f"{review_count} NEEDS_USER_REVIEW, {fail_count} FAILED "
                        f"(von {len(results)})."
                    )
                    for result in results:
                        if result.status == ASSET_ALLOCATION_CORRECTION_STATUS_NEEDS_USER_REVIEW:
                            st.warning(
                                f"„{result.draft.folder_name}“: nach {result.attempt_count} "
                                f"Versuch(en) bleiben {len(result.remaining_issues)} "
                                "Auffälligkeit(en)."
                            )
                        elif result.status != ASSET_ALLOCATION_CORRECTION_STATUS_PASS:
                            st.error(f"„{result.draft.folder_name}“: {result.error}")
                    st.rerun()

    bulk_reports = st.session_state.get(_asset_readiness_all_session_key(project))
    if isinstance(bulk_reports, list) and bulk_reports:
        if all(isinstance(report, FolderAssetReadinessReport) for report in bulk_reports):
            _render_bulk_asset_readiness_summary(bulk_reports)
            high_issue_folders = [
                report.folder_name
                for report in bulk_reports
                if len(report.issues) >= FOLDER_ASSET_READINESS_HIGH_ISSUE_REGEN_THRESHOLD
            ]
            if high_issue_folders and render_new_feature_button(
                f"🟢 ≥{FOLDER_ASSET_READINESS_HIGH_ISSUE_REGEN_THRESHOLD} Issues → "
                "strict inventory + neu generieren + Allokation + Readiness",
                key=f"vo_fvo_strict_inventory_high_issue_regen_{project.id}",
                help=(
                    "NEU: erkennt Ordner mit mindestens "
                    f"{FOLDER_ASSET_READINESS_HIGH_ISSUE_REGEN_THRESHOLD} Asset-Readiness-Issues, "
                    "setzt NUR dort Faktentreue auf strict_inventory_only, speichert Settings, "
                    "generiert diese Ordner neu und führt danach erneut Asset-Allokation sowie "
                    "frische Asset-Readiness-Diagnose aus. Andere Ordner bleiben unverändert."
                ),
            ):
                progress_placeholder = st.empty()

                def _progress_high_issue(folder_name: str, index: int, total: int) -> None:
                    progress_placeholder.info(
                        f"Ordner {index}/{total}: „{folder_name}“ "
                        "(strict inventory → generieren → Allokation → Readiness)…"
                    )

                with st.spinner(
                    f"{len(high_issue_folders)} Ordner: "
                    "Settings → Generieren → Allokation → Readiness…"
                ):
                    try:
                        (
                            touched_folders,
                            gen_results,
                            alloc_results,
                            refreshed_partial,
                        ) = regenerate_high_issue_folders_with_strict_inventory(
                            project,
                            provider=author_provider,
                            model=author_model,
                            reports=bulk_reports,
                            progress_callback=_progress_high_issue,
                        )
                    except ValueError as exc:
                        progress_placeholder.empty()
                        st.error(str(exc))
                    else:
                        progress_placeholder.empty()
                        # Bulk-Übersicht aktualisieren: betroffene Ordner ersetzen,
                        # übrige Reports behalten.
                        refreshed_by_folder = {
                            report.folder_name: report for report in refreshed_partial
                        }
                        merged_reports = [
                            refreshed_by_folder.get(report.folder_name, report)
                            for report in bulk_reports
                        ]
                        for report in refreshed_partial:
                            st.session_state[
                                _asset_readiness_session_key(project, report.folder_name)
                            ] = report
                        st.session_state[_asset_readiness_all_session_key(project)] = merged_reports

                        gen_pass = sum(1 for result in gen_results if result.status == STATUS_PASS)
                        alloc_pass = sum(
                            1
                            for result in alloc_results
                            if result.status == ASSET_ALLOCATION_CORRECTION_STATUS_PASS
                        )
                        issue_counts = ", ".join(
                            f"„{report.folder_name}“={len(report.issues)}"
                            for report in refreshed_partial
                        )
                        st.success(
                            f"Strict inventory + Regen + Allokation + Readiness: "
                            f"{len(touched_folders)} Ordner — "
                            f"Generierung {gen_pass}/{len(gen_results)} PASS, "
                            f"Allokation {alloc_pass}/{len(alloc_results)} PASS. "
                            f"Faktentreue gesetzt auf: {', '.join(touched_folders)}. "
                            f"Issues danach: {issue_counts or '—'}"
                        )
                        for result in gen_results:
                            if result.status != STATUS_PASS:
                                st.error(f"Generierung fehlgeschlagen: {result.error}")
                        for result in alloc_results:
                            if result.status == ASSET_ALLOCATION_CORRECTION_STATUS_NEEDS_USER_REVIEW:
                                st.warning(
                                    f"„{result.draft.folder_name}“: nach Allokation bleiben "
                                    f"{len(result.remaining_issues)} Auffälligkeit(en)."
                                )
                            elif result.status != ASSET_ALLOCATION_CORRECTION_STATUS_PASS:
                                st.error(
                                    f"Allokation „{result.draft.folder_name}“: {result.error}"
                                )
                        st.rerun()
