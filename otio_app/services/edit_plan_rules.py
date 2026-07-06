"""Katalog und Persistenz für Schnittplan-Regeln."""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from pathlib import Path

from otio_app.analysis_models import EditPlanRule, EditPlanRulesDocument, EditPlanShot
from otio_app.models import Project

RULE_MAX_ASSET_USES = "max_asset_uses"
RULE_NO_CONSECUTIVE_SAME_ASSET = "no_consecutive_same_asset"
RULE_MIN_SHOTS_BETWEEN_SAME_ASSET = "min_shots_between_same_asset"
RULE_PREFER_LEAST_USED_ASSET = "prefer_least_used_asset"
RULE_CUSTOM = "custom"
RULE_CUSTOM_NOTE = "custom_note"  # Legacy — wird wie RULE_CUSTOM behandelt


@dataclass(frozen=True)
class EditPlanRuleTemplate:
    rule_type: str
    label: str
    description: str
    default_params: dict[str, int | float | str | bool]
    implemented: bool = True
    allow_multiple: bool = False


EDIT_PLAN_RULE_TEMPLATES: tuple[EditPlanRuleTemplate, ...] = (
    EditPlanRuleTemplate(
        rule_type=RULE_MAX_ASSET_USES,
        label="Max. Asset-Nutzung",
        description="Dasselbe Asset höchstens N-mal im gesamten Video.",
        default_params={"max_count": 2},
        implemented=True,
    ),
    EditPlanRuleTemplate(
        rule_type=RULE_NO_CONSECUTIVE_SAME_ASSET,
        label="Nicht zweimal hintereinander",
        description="Dasselbe Asset darf nicht in zwei aufeinanderfolgenden Shots vorkommen.",
        default_params={},
        implemented=True,
    ),
    EditPlanRuleTemplate(
        rule_type=RULE_MIN_SHOTS_BETWEEN_SAME_ASSET,
        label="Min. Abstand zwischen Wiederholungen",
        description="Dasselbe Asset erst wieder nach mindestens N anderen Shots.",
        default_params={"min_gap": 3},
        implemented=False,
    ),
    EditPlanRuleTemplate(
        rule_type=RULE_PREFER_LEAST_USED_ASSET,
        label="Selten genutzte Assets bevorzugen",
        description="Bevorzugt Assets, die im Schnittplan bisher seltener vorkamen.",
        default_params={},
        implemented=False,
    ),
)


def _template_map() -> dict[str, EditPlanRuleTemplate]:
    return {template.rule_type: template for template in EDIT_PLAN_RULE_TEMPLATES}


def rules_path(project: Project) -> Path:
    return project.work_dir_path / "edit_plan_rules.json"


def default_rules(project: Project) -> EditPlanRulesDocument:
    rules: list[EditPlanRule] = []
    for template in EDIT_PLAN_RULE_TEMPLATES:
        if not template.implemented:
            continue
        rules.append(create_rule_from_template(template.rule_type))
    return EditPlanRulesDocument(project_id=project.id, rules=rules)


def is_rule_implemented(rule_type: str) -> bool:
    template = _template_map().get(rule_type)
    return bool(template and template.implemented)


def available_rule_templates(existing: EditPlanRulesDocument) -> list[EditPlanRuleTemplate]:
    used_types = {rule.rule_type for rule in existing.rules if not is_custom_rule(rule)}
    available: list[EditPlanRuleTemplate] = []
    for template in EDIT_PLAN_RULE_TEMPLATES:
        if template.allow_multiple or template.rule_type not in used_types:
            available.append(template)
    return available


def is_custom_rule(rule: EditPlanRule) -> bool:
    return rule.rule_type in {RULE_CUSTOM, RULE_CUSTOM_NOTE}


def list_custom_rules(rules_doc: EditPlanRulesDocument, *, enabled_only: bool = False) -> list[EditPlanRule]:
    rules = [rule for rule in rules_doc.rules if is_custom_rule(rule)]
    if enabled_only:
        rules = [rule for rule in rules if rule.enabled]
    return rules


def create_custom_rule(title: str, text: str) -> EditPlanRule:
    clean_title = title.strip() or "Eigene Regel"
    clean_text = text.strip()
    return EditPlanRule(
        id=str(uuid.uuid4()),
        rule_type=RULE_CUSTOM,
        enabled=True,
        params={"title": clean_title, "text": clean_text},
        label=clean_title,
    )


def load_edit_plan_rules(project: Project) -> EditPlanRulesDocument:
    path = rules_path(project)
    if not path.is_file():
        return default_rules(project)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        document = EditPlanRulesDocument.model_validate(payload)
        if document.project_id != project.id:
            document = document.model_copy(update={"project_id": project.id})
        return document
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError):
        return default_rules(project)


def save_edit_plan_rules(project: Project, document: EditPlanRulesDocument) -> Path:
    path = rules_path(project)
    path.parent.mkdir(parents=True, exist_ok=True)
    normalized = document.model_copy(update={"project_id": project.id})
    path.write_text(normalized.model_dump_json(indent=2), encoding="utf-8")
    return path


def rule_label(rule: EditPlanRule) -> str:
    if is_custom_rule(rule):
        title = str(rule.params.get("title") or rule.label or "").strip()
        if title:
            return title
        legacy_note = str(rule.params.get("note", "")).strip()
        if legacy_note:
            return legacy_note.splitlines()[0][:80]
        return "Eigene Regel"
    if rule.label.strip():
        return rule.label.strip()
    template = _template_map().get(rule.rule_type)
    return template.label if template else rule.rule_type


def rule_description(rule: EditPlanRule) -> str:
    if is_custom_rule(rule):
        text = str(rule.params.get("text") or rule.params.get("note") or "").strip()
        return text or "Eigene Regel — manuell beim Schnitt beachten."
    template = _template_map().get(rule.rule_type)
    return template.description if template else ""


def create_rule_from_template(rule_type: str) -> EditPlanRule:
    template = _template_map()[rule_type]
    return EditPlanRule(
        id=str(uuid.uuid4()),
        rule_type=template.rule_type,
        enabled=True,
        params=dict(template.default_params),
        label=template.label,
    )


def _asset_key(asset_path: str | None) -> str | None:
    if not asset_path:
        return None
    return Path(asset_path).name


def _enabled_rules(rules_doc: EditPlanRulesDocument) -> list[EditPlanRule]:
    return [
        rule
        for rule in rules_doc.rules
        if rule.enabled and is_rule_implemented(rule.rule_type)
    ]


def _max_count(rules: list[EditPlanRule]) -> int | None:
    for rule in rules:
        if rule.rule_type == RULE_MAX_ASSET_USES:
            raw = rule.params.get("max_count", 2)
            try:
                return max(1, int(raw))
            except (TypeError, ValueError):
                return 2
    return None


def _no_consecutive(rules: list[EditPlanRule]) -> bool:
    return any(rule.rule_type == RULE_NO_CONSECUTIVE_SAME_ASSET for rule in rules)


def validate_shots_against_rules(
    shots: list[EditPlanShot],
    rules_doc: EditPlanRulesDocument,
) -> list[str]:
    """Liefert menschenlesbare Regelverletzungen."""
    rules = _enabled_rules(rules_doc)
    if not rules:
        return []

    violations: list[str] = []
    max_count = _max_count(rules)
    usage: dict[str, int] = {}
    previous_key: str | None = None

    for index, shot in enumerate(shots, start=1):
        key = _asset_key(shot.asset_path)
        if key is None:
            previous_key = None
            continue
        usage[key] = usage.get(key, 0) + 1
        if max_count is not None and usage[key] > max_count:
            violations.append(
                f"Shot {index}: `{key}` mehr als {max_count}× verwendet "
                f"({usage[key]}×)"
            )
        if _no_consecutive(rules) and previous_key is not None and key == previous_key:
            violations.append(
                f"Shot {index}: `{key}` direkt nach Shot {index - 1} wiederholt"
            )
        previous_key = key
    return violations


def apply_edit_plan_rules(
    shots: list[EditPlanShot],
    rules_doc: EditPlanRulesDocument,
    assets_by_folder: dict[str, list[str]],
) -> list[EditPlanShot]:
    """Weist Assets so zu, dass aktive Regeln eingehalten werden."""
    rules = _enabled_rules(rules_doc)
    if not rules:
        return shots

    max_count = _max_count(rules)
    consecutive = _no_consecutive(rules)
    usage: dict[str, int] = {}
    previous_key: str | None = None
    adjusted: list[EditPlanShot] = []

    for shot in shots:
        folder_assets = assets_by_folder.get(shot.folder, [])
        chosen = shot.asset_path if shot.asset_path in folder_assets else None
        chosen_key = _asset_key(chosen)

        def violates(candidate: str | None) -> bool:
            key = _asset_key(candidate)
            if key is None:
                return False
            if max_count is not None and usage.get(key, 0) >= max_count:
                return True
            if consecutive and previous_key is not None and key == previous_key:
                return True
            return False

        if chosen is not None and violates(chosen):
            chosen = None
            chosen_key = None

        if chosen is None and folder_assets:
            candidates = sorted(
                folder_assets,
                key=lambda path: (usage.get(_asset_key(path) or "", 0), path),
            )
            for candidate in candidates:
                if not violates(candidate):
                    chosen = candidate
                    chosen_key = _asset_key(candidate)
                    break

        if chosen_key is not None:
            usage[chosen_key] = usage.get(chosen_key, 0) + 1
            previous_key = chosen_key
        else:
            previous_key = None

        adjusted.append(
            shot.model_copy(
                update={
                    "asset_path": chosen,
                    "asset_source": "local" if chosen else "missing",
                }
            )
        )
    return adjusted
