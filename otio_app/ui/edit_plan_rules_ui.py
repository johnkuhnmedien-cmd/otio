"""UI: Schnittplan-Regeln verwalten."""

from __future__ import annotations

import streamlit as st

from otio_app.analysis_models import EditPlanRule, EditPlanRulesDocument
from otio_app.models import Project
from otio_app.services.edit_plan_rules import (
    RULE_AUTO_ZOOM_FILL,
    RULE_MAX_ASSET_USES,
    RULE_TRIM_LEADING,
    available_rule_templates,
    create_custom_rule,
    create_rule_from_template,
    is_custom_rule,
    is_rule_implemented,
    list_custom_rules,
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


def _render_rule_card(
    project: Project,
    rule: EditPlanRule,
    remove_ids: list[str],
) -> EditPlanRule | None:
    with st.container(border=True):
        cols = st.columns([4, 1])
        title = rule_label(rule)
        if is_custom_rule(rule):
            title += " · *eigene Regel*"
        elif not is_rule_implemented(rule.rule_type):
            title += " *(demnächst)*"
        with cols[0]:
            enabled = st.checkbox(
                title,
                value=rule.enabled,
                key=f"rule_enabled_{project.id}_{rule.id}",
            )
            if not is_custom_rule(rule):
                st.caption(rule_description(rule))
            if not is_rule_implemented(rule.rule_type) and not is_custom_rule(rule):
                st.caption("Wird gespeichert, aber noch nicht automatisch im Schnittplan angewendet.")
        with cols[1]:
            if st.button("🗑️", key=f"rule_remove_{project.id}_{rule.id}", help="Regel entfernen"):
                remove_ids.append(rule.id)

        params = dict(rule.params)
        label = rule.label

        if rule.rule_type == RULE_MAX_ASSET_USES:
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
        elif is_custom_rule(rule):
            label = st.text_input(
                "Titel",
                value=str(params.get("title") or rule.label or "Eigene Regel"),
                key=f"rule_custom_title_{project.id}_{rule.id}",
            )
            params["title"] = label.strip() or "Eigene Regel"
            legacy_text = str(params.get("text") or params.get("note") or "")
            params["text"] = st.text_area(
                "Regeltext",
                value=legacy_text,
                key=f"rule_custom_text_{project.id}_{rule.id}",
                height=100,
                placeholder="z. B. Keine Drohnenaufnahmen direkt hintereinander …",
            )
            params.pop("note", None)

        if rule.id in remove_ids:
            return None
        return rule.model_copy(update={"enabled": enabled, "params": params, "label": label})


def render_edit_plan_rules_manager(project: Project) -> EditPlanRulesDocument:
    """Regeln anzeigen, bearbeiten und dauerhaft speichern."""
    st.markdown("**Schnittregeln**")
    st.caption(
        "Automatische Regeln wirken beim Erzeugen des Schnittplans (Asset-Auswahl) "
        "oder beim OTIO-Export (Anfang abschneiden, Zoom). "
        "Eigene Regeln speicherst du als Checkliste für den manuellen Schnitt. "
        f"Datei: `{project.work_dir_path / 'edit_plan_rules.json'}`"
    )

    document = _get_rules_document(project)
    remove_ids: list[str] = []
    updated_rules: list[EditPlanRule] = []

    system_rules = [rule for rule in document.rules if not is_custom_rule(rule)]
    custom_rules = list_custom_rules(document)

    st.markdown("**Automatische Regeln**")
    if not system_rules:
        st.caption("Keine System-Regeln — unten eine vordefinierte Regel hinzufügen.")
    for rule in system_rules:
        updated = _render_rule_card(project, rule, remove_ids)
        if updated is not None:
            updated_rules.append(updated)

    st.divider()
    st.markdown("**Deine eigenen Regeln**")
    st.caption("Frei formuliert — beliebig viele, dauerhaft speicherbar, jederzeit löschbar.")

    if not custom_rules:
        st.info("Noch keine eigenen Regeln.")

    for rule in custom_rules:
        updated = _render_rule_card(project, rule, remove_ids)
        if updated is not None:
            updated_rules.append(updated)

    with st.container(border=True):
        new_title = st.text_input(
            "Titel der neuen Regel",
            placeholder="z. B. Keine Wiederholung von Intro-Shots",
            key=f"custom_rule_title_new_{project.id}",
        )
        new_text = st.text_area(
            "Regeltext",
            placeholder="Beschreibe die Regel in eigenen Worten …",
            key=f"custom_rule_text_new_{project.id}",
            height=100,
        )
        if st.button("➕ Eigene Regel hinzufügen", key=f"custom_rule_add_{project.id}", type="primary"):
            if not new_text.strip() and not new_title.strip():
                st.warning("Bitte mindestens Titel oder Regeltext eingeben.")
            else:
                updated_rules.append(create_custom_rule(new_title, new_text))
                document = document.model_copy(update={"rules": updated_rules})
                _set_rules_document(document)
                st.rerun()

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
            document = document.model_copy(update={"rules": updated_rules})
            _set_rules_document(document)
            st.rerun()

    document = document.model_copy(update={"rules": updated_rules})

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
