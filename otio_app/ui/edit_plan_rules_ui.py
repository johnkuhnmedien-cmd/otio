"""UI: Schnittplan-Regeln verwalten."""

from __future__ import annotations

import streamlit as st

from otio_app.analysis_models import EditPlanRule, EditPlanRulesDocument
from otio_app.models import Project
from otio_app.services.edit_plan_rules import (
    EDIT_PLAN_RULE_TEMPLATES,
    RULE_MAX_ASSET_USES,
    create_rule_from_template,
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


def render_edit_plan_rules_manager(project: Project) -> EditPlanRulesDocument:
    """Regeln anzeigen, bearbeiten und dauerhaft speichern."""
    st.markdown("**Schnittregeln (Assets)**")
    st.caption(
        "Regeln gelten beim Erzeugen des Schnittplans. "
        f"Gespeichert in `{project.work_dir_path / 'edit_plan_rules.json'}`"
    )

    document = _get_rules_document(project)

    if not document.rules:
        st.info("Noch keine Regeln — füge unten eine Standardregel hinzu.")

    remove_ids: list[str] = []
    updated_rules: list[EditPlanRule] = []

    for rule in document.rules:
        with st.container(border=True):
            cols = st.columns([4, 1])
            with cols[0]:
                enabled = st.checkbox(
                    rule_label(rule),
                    value=rule.enabled,
                    key=f"rule_enabled_{project.id}_{rule.id}",
                )
                st.caption(rule_description(rule))
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

            if rule.id not in remove_ids:
                updated_rules.append(
                    rule.model_copy(update={"enabled": enabled, "params": params})
                )

    document = document.model_copy(update={"rules": updated_rules})

    st.markdown("**Regel hinzufügen**")
    available_templates = [
        template
        for template in EDIT_PLAN_RULE_TEMPLATES
        if template.rule_type not in {rule.rule_type for rule in document.rules}
    ]
    add_col1, add_col2 = st.columns([3, 1])
    with add_col1:
        if available_templates:
            template_labels = {template.rule_type: template.label for template in available_templates}
            selected_type = st.selectbox(
                "Regeltyp",
                options=list(template_labels.keys()),
                format_func=lambda value: template_labels[value],
                key=f"rule_add_type_{project.id}",
                label_visibility="collapsed",
            )
        else:
            selected_type = None
            st.caption("Alle vordefinierten Regeltypen sind bereits aktiv.")
    with add_col2:
        if st.button("➕ Hinzufügen", key=f"rule_add_{project.id}", disabled=not available_templates):
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
