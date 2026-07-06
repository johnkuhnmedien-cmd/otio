"""UI: Schnittplan-Regeln verwalten."""

from __future__ import annotations

import streamlit as st

from otio_app.analysis_models import EditPlanRule, EditPlanRulesDocument
from otio_app.models import Project
from otio_app.services.edit_plan_rules import (
    RULE_AUTO_ZOOM_FILL,
    RULE_FOLDER_TITLE,
    RULE_MAX_ASSET_USES,
    RULE_TRIM_LEADING,
    available_rule_templates,
    create_rule_from_template,
    is_custom_rule,
    is_rule_implemented,
    load_edit_plan_rules,
    rule_description,
    rule_label,
    save_edit_plan_rules,
)


def _rules_state_key(project_id: str) -> str:
    return f"edit_plan_rules_{project_id}"


def _get_rules_document(project: Project) -> EditPlanRulesDocument:
    key = _rules_state_key(project.id)
    if key not in st.session_state:
        st.session_state[key] = load_edit_plan_rules(project).model_dump(mode="json")
    return EditPlanRulesDocument.model_validate(st.session_state[key])


def _set_rules_document(document: EditPlanRulesDocument) -> None:
    st.session_state[_rules_state_key(document.project_id)] = document.model_dump(mode="json")


def merge_rule_widgets_from_session(
    project: Project,
    document: EditPlanRulesDocument,
    *,
    session: dict | None = None,
) -> EditPlanRulesDocument:
    """Widget-Werte aus session_state in Regeln übernehmen (auch ohne aktiven Regeln-Tab)."""
    state = session if session is not None else st.session_state
    updated_rules: list[EditPlanRule] = []

    for rule in document.rules:
        enabled_key = f"rule_enabled_{project.id}_{rule.id}"
        enabled = bool(state.get(enabled_key, rule.enabled))
        params = dict(rule.params)

        if rule.rule_type == RULE_MAX_ASSET_USES:
            max_key = f"rule_max_{project.id}_{rule.id}"
            if max_key in state:
                params["max_count"] = int(state[max_key])
            min_gap_key = f"rule_min_gap_{project.id}_{rule.id}"
            if min_gap_key in state:
                params["min_gap"] = int(state[min_gap_key])
        elif rule.rule_type == RULE_TRIM_LEADING:
            trim_key = f"rule_trim_{project.id}_{rule.id}"
            if trim_key in state:
                params["trim_sec"] = float(state[trim_key])
        elif rule.rule_type == RULE_FOLDER_TITLE:
            font_key = f"rule_folder_title_font_{project.id}_{rule.id}"
            size_key = f"rule_folder_title_font_size_{project.id}_{rule.id}"
            duration_key = f"rule_folder_title_duration_{project.id}_{rule.id}"
            if font_key in state:
                params["font_name"] = str(state[font_key])
            if size_key in state:
                params["font_size"] = float(state[size_key])
            if duration_key in state:
                params["duration_sec"] = float(state[duration_key])

        updated_rules.append(rule.model_copy(update={"enabled": enabled, "params": params}))

    gemini_key = f"gemini_prompt_{project.id}"
    gemini_prompt = str(state.get(gemini_key, document.gemini_prompt))
    return document.model_copy(update={"rules": updated_rules, "gemini_prompt": gemini_prompt})


def get_edit_plan_rules_for_project(project: Project) -> EditPlanRulesDocument:
    """Aktuelle Regeln inkl. Widget-Werte (Session oder Datei)."""
    key = _rules_state_key(project.id)
    if key in st.session_state:
        document = EditPlanRulesDocument.model_validate(st.session_state[key])
    else:
        document = load_edit_plan_rules(project)
    return merge_rule_widgets_from_session(project, document)


def _template_option_label(rule_type: str) -> str:
    from otio_app.services.edit_plan_rules import _template_map

    template = _template_map().get(rule_type)
    if template is None:
        return rule_type
    suffix = "" if template.implemented else " · demnächst"
    return f"{template.label}{suffix}"


def _render_rule_card(
    project: Project,
    rule: EditPlanRule,
    remove_ids: list[str],
) -> EditPlanRule | None:
    with st.container(border=True):
        cols = st.columns([4, 1])
        title = rule_label(rule)
        if not is_rule_implemented(rule.rule_type):
            title += " *(demnächst)*"
        with cols[0]:
            enabled = st.checkbox(
                title,
                value=rule.enabled,
                key=f"rule_enabled_{project.id}_{rule.id}",
            )
            st.caption(rule_description(rule))
            if not is_rule_implemented(rule.rule_type):
                st.caption("Wird gespeichert, aber noch nicht automatisch im Schnittplan angewendet.")
        with cols[1]:
            if st.button("🗑️", key=f"rule_remove_{project.id}_{rule.id}", help="Regel entfernen"):
                remove_ids.append(rule.id)

        params = dict(rule.params)
        label = rule.label

        if rule.rule_type == RULE_MAX_ASSET_USES:
            max_cols = st.columns(2)
            with max_cols[0]:
                params["max_count"] = int(
                    st.number_input(
                        "Max. Nutzungen pro Asset",
                        min_value=1,
                        max_value=20,
                        value=int(params.get("max_count", 2)),
                        step=1,
                        key=f"rule_max_{project.id}_{rule.id}",
                    )
                )
            with max_cols[1]:
                params["min_gap"] = int(
                    st.number_input(
                        "Min. Abstand (Shots) bis Wiederverwendung",
                        min_value=0,
                        max_value=50,
                        value=int(params.get("min_gap", 0)),
                        step=1,
                        key=f"rule_min_gap_{project.id}_{rule.id}",
                        help=(
                            "0 = deaktiviert. Sonst muss ein Asset erst nach so vielen "
                            "ANDEREN Shots erneut verwendet werden — vermeidet zu "
                            "schnelle Wiederholungen desselben Assets."
                        ),
                    )
                )
        elif rule.rule_type == RULE_TRIM_LEADING:
            params["trim_sec"] = float(
                st.number_input(
                    "Sekunden am Anfang überspringen",
                    min_value=0.0,
                    max_value=5.0,
                    value=float(params.get("trim_sec", 0.5)),
                    step=0.1,
                    key=f"rule_trim_{project.id}_{rule.id}",
                    help="Gilt beim OTIO-Export — schneidet z. B. schwarze Erstframes weg.",
                )
            )
        elif rule.rule_type == RULE_AUTO_ZOOM_FILL:
            st.caption(
                "Aktiv: Zoom-Faktor wird pro Asset aus Auflösung vs. Projekt berechnet "
                f"({project.width}×{project.height})."
            )
        from otio_app.services.edit_plan_rules import RULE_FOLDER_TITLE
        from otio_app.services.font_utils import FOLDER_TITLE_FONT_OPTIONS

        if rule.rule_type == RULE_FOLDER_TITLE:
            font_options = list(FOLDER_TITLE_FONT_OPTIONS)
            current_font = str(params.get("font_name", "Helvetica Neue"))
            if current_font not in font_options:
                font_options = [current_font, *font_options]
            params["font_name"] = st.selectbox(
                "Schriftart",
                options=font_options,
                index=font_options.index(current_font),
                key=f"rule_folder_title_font_{project.id}_{rule.id}",
                help="Phosphate muss auf dem System installiert sein (Mac: ~/Library/Fonts).",
            )
            params["duration_sec"] = float(
                st.number_input(
                    "Dauer (Sekunden)",
                    min_value=0.5,
                    max_value=15.0,
                    value=float(params.get("duration_sec", 5.0)),
                    step=0.5,
                    key=f"rule_folder_title_duration_{project.id}_{rule.id}",
                )
            )
            params["font_size"] = float(
                st.number_input(
                    "Schriftgröße (px)",
                    min_value=0.0,
                    max_value=200.0,
                    value=float(params.get("font_size", 0.0)),
                    step=2.0,
                    key=f"rule_folder_title_font_size_{project.id}_{rule.id}",
                    help="0 = automatisch (Lower Third, abhängig von der Projektauflösung).",
                )
            )
            st.caption(
                "Ordnername als Lower Third unten links (Clean-and-Simple-Stil) — "
                "Unterstriche (_) werden zu Leerzeichen. "
                "Wird vor dem OTIO-Export als transparentes Overlay auf V2 gerendert. "
                "**Wirkt nach Speichern** und **Schnittplan vorschlagen** "
                "(oder beim **Bestätigen & speichern** für Titel-Einstellungen)."
            )

        if rule.id in remove_ids:
            return None
        return rule.model_copy(update={"enabled": enabled, "params": params, "label": label})


def render_edit_plan_rules_manager(project: Project) -> EditPlanRulesDocument:
    """Regeln anzeigen, bearbeiten und dauerhaft speichern."""
    st.markdown("**Schnittregeln**")
    st.caption(
        "System-Regeln wirken nach **Speichern** — Titel/Zoom beim **Schnittplan vorschlagen**, "
        "Asset-Regeln ebenfalls beim Vorschlag. "
        "Unter **Gemini-Zusatzhinweise** kannst du freie Anweisungen formulieren — "
        "die werden beim Schnittplan-Vorschlag an Gemini geschickt. "
        f"Datei: `{project.work_dir_path / 'edit_plan_rules.json'}`"
    )

    document = _get_rules_document(project)
    remove_ids: list[str] = []
    updated_rules: list[EditPlanRule] = []

    system_rules = [rule for rule in document.rules if not is_custom_rule(rule)]

    st.markdown("**Automatische System-Regeln**")
    if not system_rules:
        st.caption("Keine System-Regeln — unten eine vordefinierte Regel hinzufügen.")
    for rule in system_rules:
        updated = _render_rule_card(project, rule, remove_ids)
        if updated is not None:
            updated_rules.append(updated)

    st.divider()
    st.markdown("**Gemini-Zusatzhinweise**")
    st.caption(
        "Freitext für Gemini beim **Schnittplan vorschlagen** — z. B. "
        "«Jedes Asset soll bis zum Beginn des nächsten Satzes laufen» oder "
        "«Keine Drohnenaufnahmen hintereinander». "
        "Nach dem Speichern Schnittplan **neu generieren**."
    )
    gemini_prompt = st.text_area(
        "Zusatzhinweise für Gemini",
        value=document.gemini_prompt,
        height=160,
        key=f"gemini_prompt_{project.id}",
        placeholder=(
            "Beispiel:\n"
            "- Assets laufen bis zum Beginn des nächsten Satzes\n"
            "- Bevorzuge Weitwinkel bei Landschaften"
        ),
        label_visibility="collapsed",
    )

    st.divider()
    st.markdown("**Vordefinierte Regel hinzufügen**")
    templates = available_rule_templates(document)
    add_col1, add_col2 = st.columns([3, 1])
    with add_col1:
        if templates:
            selected_type = st.selectbox(
                "Regeltyp",
                options=[template.rule_type for template in templates],
                format_func=_template_option_label,
                key=f"rule_add_type_{project.id}",
                label_visibility="collapsed",
            )
        else:
            selected_type = None
            st.caption("Alle vordefinierten System-Regeln sind bereits in der Liste.")
    with add_col2:
        if st.button("➕ System-Regel", key=f"rule_add_{project.id}", disabled=not templates):
            updated_rules.append(create_rule_from_template(selected_type))
            document = document.model_copy(
                update={"rules": updated_rules, "gemini_prompt": gemini_prompt}
            )
            _set_rules_document(document)
            st.rerun()

    document = document.model_copy(update={"rules": updated_rules, "gemini_prompt": gemini_prompt})

    save_col1, save_col2 = st.columns(2)
    with save_col1:
        if st.button("💾 Alle Regeln speichern", key=f"rules_save_{project.id}", type="primary"):
            save_edit_plan_rules(project, document)
            _set_rules_document(document)
            st.success("Regeln gespeichert.")
            st.rerun()
    with save_col2:
        if st.button("↩️ Standardregeln laden", key=f"rules_reset_{project.id}"):
            from otio_app.services.edit_plan_rules import default_rules

            document = default_rules(project)
            save_edit_plan_rules(project, document)
            _set_rules_document(document)
            st.rerun()

    _set_rules_document(document)
    return document
