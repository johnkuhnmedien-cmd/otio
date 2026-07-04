"""UI: Schnittplan-Regeln verwalten."""

from __future__ import annotations

import streamlit as st

from otio_app.analysis_models import EditPlanRule, EditPlanRulesDocument
from otio_app.models import Project
from otio_app.services.edit_plan_rules import (
    RULE_CUSTOM_NOTE,
    RULE_MAX_ASSET_USES,
    available_rule_templates,
    create_rule_from_template,
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


def get_edit_plan_rules_for_project(project: Project) -> EditPlanRulesDocument:
    """Aktuelle Regeln (Session-Entwurf oder gespeicherte Datei)."""
    key = _rules_state_key(project.id)
    if key in st.session_state:
        return EditPlanRulesDocument.model_validate(st.session_state[key])
    return load_edit_plan_rules(project)


def _template_option_label(rule_type: str) -> str:
    from otio_app.services.edit_plan_rules import _template_map

    template = _template_map().get(rule_type)
    if template is None:
        return rule_type
    suffix = "" if template.implemented else " · demnächst"
    return f"{template.label}{suffix}"


def render_edit_plan_rules_manager(project: Project) -> EditPlanRulesDocument:
    """Regeln anzeigen, bearbeiten und dauerhaft speichern."""
    st.markdown("**Schnittregeln (Assets)**")
    st.caption(
        "Regeln gelten beim Erzeugen des Schnittplans. "
        f"Gespeichert in `{project.work_dir_path / 'edit_plan_rules.json'}`"
    )

    document = _get_rules_document(project)

    if not document.rules:
        st.info("Noch keine Regeln — füge unten eine Regel hinzu.")

    remove_ids: list[str] = []
    updated_rules: list[EditPlanRule] = []

    for rule in document.rules:
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
            if rule.rule_type == RULE_MAX_ASSET_USES:
                max_count = st.number_input(
                    "Max. Nutzungen pro Asset",
                    min_value=1,
                    max_value=20,
                    value=int(params.get("max_count", 2)),
                    step=1,
                    key=f"rule_max_{project.id}_{rule.id}",
                )
                params["max_count"] = int(max_count)
            elif rule.rule_type == RULE_CUSTOM_NOTE:
                params["note"] = st.text_area(
                    "Deine Regel / Notiz",
                    value=str(params.get("note", "")),
                    key=f"rule_note_{project.id}_{rule.id}",
                    height=80,
                )

            if rule.id not in remove_ids:
                updated_rules.append(
                    rule.model_copy(update={"enabled": enabled, "params": params})
                )

    document = document.model_copy(update={"rules": updated_rules})

    st.markdown("**Regel hinzufügen**")
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
            st.caption("Keine weiteren Regeltypen verfügbar.")
    with add_col2:
        if st.button("➕ Hinzufügen", key=f"rule_add_{project.id}", disabled=not templates):
            document = document.model_copy(
                update={"rules": [*document.rules, create_rule_from_template(selected_type)]}
            )
            _set_rules_document(document)
            st.rerun()

    save_col1, save_col2 = st.columns(2)
    with save_col1:
        if st.button("💾 Regeln dauerhaft speichern", key=f"rules_save_{project.id}", type="primary"):
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
