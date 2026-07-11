"""Katalog und Persistenz für Schnittplan-Regeln."""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from pathlib import Path

from otio_app.analysis_models import EditPlanRule, EditPlanRulesDocument, EditPlanShot
from otio_app.defaults import MATCH_QUALITY_UNPASSEND
from otio_app.models import Project
from otio_app.services.generic_outro_selector import asset_id_for_path

RULE_MAX_ASSET_USES = "max_asset_uses"
RULE_NO_CONSECUTIVE_SAME_ASSET = "no_consecutive_same_asset"
RULE_MIN_SHOTS_BETWEEN_SAME_ASSET = "min_shots_between_same_asset"
RULE_PREFER_LEAST_USED_ASSET = "prefer_least_used_asset"
RULE_TRIM_LEADING = "trim_leading"
RULE_FOLDER_TITLE = "folder_title_overlay"
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
    default_enabled: bool = True


EDIT_PLAN_RULE_TEMPLATES: tuple[EditPlanRuleTemplate, ...] = (
    EditPlanRuleTemplate(
        rule_type=RULE_MAX_ASSET_USES,
        label="Max. Asset-Nutzung",
        description=(
            "Dasselbe Asset höchstens N-mal im gesamten Video. Optional: "
            "Mindestabstand (in anderen Shots), bevor dasselbe Asset erneut "
            "verwendet werden darf — vermeidet zu schnelle Wiederholungen."
        ),
        default_params={"max_count": 2, "min_gap": 0},
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
    EditPlanRuleTemplate(
        rule_type=RULE_TRIM_LEADING,
        label="Anfang abschneiden",
        description=(
            "Beim OTIO-Export die ersten Sekunden jedes Assets überspringen "
            "(z. B. schwarzer Erstframe)."
        ),
        default_params={"trim_sec": 0.5},
        implemented=True,
    ),
    EditPlanRuleTemplate(
        rule_type=RULE_FOLDER_TITLE,
        label="Ordner-Titel einblenden",
        description=(
            "Opening Title zu Beginn jeder Sektion (Lower Third, 5 s, V2-Overlay). "
            "Wird als eigenes Timeline-Element im Schnittplan geplant und "
            "vor dem OTIO-Export als transparentes ProRes 4444 gerendert."
        ),
        default_params={"font_name": "Helvetica Neue", "duration_sec": 5.0, "font_size": 0.0},
        implemented=True,
        default_enabled=False,
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
        return normalize_rules_document(document)
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError):
        return default_rules(project)


def normalize_rules_document(document: EditPlanRulesDocument) -> EditPlanRulesDocument:
    """Legacy eigene Regeln → gemini_prompt; Custom-Regeln aus der Liste entfernen."""
    custom_rules = list_custom_rules(document)
    if not custom_rules:
        return document

    lines: list[str] = []
    existing = document.gemini_prompt.strip()
    if existing:
        lines.append(existing)

    for rule in custom_rules:
        if not rule.enabled:
            continue
        title = str(rule.params.get("title") or rule.label or "").strip()
        text = str(rule.params.get("text") or rule.params.get("note") or "").strip()
        if title and text:
            lines.append(f"{title}: {text}")
        elif text:
            lines.append(text)
        elif title:
            lines.append(title)

    system_rules = [rule for rule in document.rules if not is_custom_rule(rule)]
    return document.model_copy(
        update={
            "rules": system_rules,
            "gemini_prompt": "\n".join(lines).strip(),
        }
    )


def gemini_prompt_text(rules_doc: EditPlanRulesDocument) -> str:
    return rules_doc.gemini_prompt.strip()


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
        enabled=template.default_enabled,
        params=dict(template.default_params),
        label=template.label,
    )


def _asset_key(asset_path: str | None) -> str | None:
    if not asset_path:
        return None
    return asset_id_for_path(asset_path)


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


def _min_gap(rules: list[EditPlanRule]) -> int:
    """Mindestabstand (in anderen Shots) bis zur erneuten Nutzung desselben Assets.

    Lebt als Zusatzparameter bei RULE_MAX_ASSET_USES statt als eigene Regel,
    da beide dieselbe Wiederholungs-Problematik adressieren (0 = deaktiviert)."""
    for rule in rules:
        if rule.rule_type == RULE_MAX_ASSET_USES:
            raw = rule.params.get("min_gap", 0)
            try:
                return max(0, int(raw))
            except (TypeError, ValueError):
                return 0
    return 0


def _no_consecutive(rules: list[EditPlanRule]) -> bool:
    return any(rule.rule_type == RULE_NO_CONSECUTIVE_SAME_ASSET for rule in rules)


@dataclass(frozen=True)
class ExportRuleOptions:
    """Regeln, die beim OTIO-Export wirken (nicht bei Asset-Auswahl)."""

    trim_leading_sec: float = 0.0
    folder_title_enabled: bool = False
    folder_title_font: str = "Helvetica Neue"
    folder_title_duration_sec: float = 5.0
    folder_title_font_size: float | None = None


def export_rule_options(rules_doc: EditPlanRulesDocument) -> ExportRuleOptions:
    trim_sec = 0.0
    folder_title = False
    folder_font = "Helvetica Neue"
    folder_duration = 5.0
    folder_font_size: float | None = None
    for rule in _enabled_rules(rules_doc):
        if rule.rule_type == RULE_TRIM_LEADING:
            raw = rule.params.get("trim_sec", 0.5)
            try:
                trim_sec = max(0.0, float(raw))
            except (TypeError, ValueError):
                trim_sec = 0.5
        elif rule.rule_type == RULE_FOLDER_TITLE:
            folder_title = True
            raw_font = rule.params.get("font_name", "Helvetica Neue")
            folder_font = str(raw_font).strip() if raw_font else "Helvetica Neue"
            raw_duration = rule.params.get("duration_sec", 5.0)
            try:
                folder_duration = max(0.1, min(30.0, float(raw_duration)))
            except (TypeError, ValueError):
                folder_duration = 5.0
            raw_font_size = rule.params.get("font_size", 0.0)
            try:
                parsed_size = float(raw_font_size)
                folder_font_size = None if parsed_size <= 0 else max(12.0, min(200.0, parsed_size))
            except (TypeError, ValueError):
                folder_font_size = None
    return ExportRuleOptions(
        trim_leading_sec=trim_sec,
        folder_title_enabled=folder_title,
        folder_title_font=folder_font,
        folder_title_duration_sec=folder_duration,
        folder_title_font_size=folder_font_size,
    )


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
    min_gap = _min_gap(rules)
    usage: dict[str, int] = {}
    last_used_at: dict[str, int] = {}
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
        if min_gap > 0 and key in last_used_at and (index - last_used_at[key]) <= min_gap:
            violations.append(
                f"Shot {index}: `{key}` erneut nach nur "
                f"{index - last_used_at[key] - 1} anderen Shot(s) "
                f"(Mindestabstand {min_gap})"
            )
        last_used_at[key] = index
        previous_key = key
    return violations


def _normalize_folder_assets(entries: list) -> dict[str, dict[str, str]]:
    """Akzeptiert sowohl reine Pfadlisten (Alt-API) als auch volle
    Asset-Payloads (Pfad + Metadaten wie asset_origin/rights_status/...).

    Wird für die Metadaten-Auffrischung beim Neu-Zuweisen eines Assets
    benötigt (siehe apply_edit_plan_rules)."""
    by_path: dict[str, dict[str, str]] = {}
    for entry in entries:
        if isinstance(entry, str):
            by_path.setdefault(entry, {"path": entry})
        elif isinstance(entry, dict) and entry.get("path"):
            by_path[entry["path"]] = entry
    return by_path


def apply_edit_plan_rules(
    shots: list[EditPlanShot],
    rules_doc: EditPlanRulesDocument,
    assets_by_folder: dict[str, list],
) -> list[EditPlanShot]:
    """Weist Assets so zu, dass aktive Regeln eingehalten werden."""
    rules = _enabled_rules(rules_doc)
    if not rules:
        return shots

    max_count = _max_count(rules)
    min_gap = _min_gap(rules)
    consecutive = _no_consecutive(rules)
    usage: dict[str, int] = {}
    last_used_at: dict[str, int] = {}
    previous_key: str | None = None
    adjusted: list[EditPlanShot] = []

    normalized_by_folder = {
        folder: _normalize_folder_assets(entries) for folder, entries in assets_by_folder.items()
    }

    for position, shot in enumerate(shots):
        if shot.match_quality == MATCH_QUALITY_UNPASSEND:
            if shot.asset_path:
                chosen_key = _asset_key(shot.asset_path)
                if chosen_key is not None:
                    usage[chosen_key] = usage.get(chosen_key, 0) + 1
                    last_used_at[chosen_key] = position
                    previous_key = chosen_key
                else:
                    previous_key = None
            else:
                previous_key = None
            adjusted.append(shot)
            continue

        folder_asset_map = normalized_by_folder.get(shot.folder, {})
        chosen = shot.asset_path if shot.asset_path in folder_asset_map else None
        chosen_key = _asset_key(chosen)

        def violates(candidate: str | None) -> bool:
            key = _asset_key(candidate)
            if key is None:
                return False
            if max_count is not None and usage.get(key, 0) >= max_count:
                return True
            if consecutive and previous_key is not None and key == previous_key:
                return True
            if (
                min_gap > 0
                and key in last_used_at
                and (position - last_used_at[key]) <= min_gap
            ):
                return True
            return False

        if chosen is not None and violates(chosen):
            chosen = None
            chosen_key = None

        if chosen is None and folder_asset_map:
            candidates = sorted(
                folder_asset_map.keys(),
                key=lambda path: (usage.get(_asset_key(path) or "", 0), path),
            )
            for candidate in candidates:
                if not violates(candidate):
                    chosen = candidate
                    chosen_key = _asset_key(candidate)
                    break

        if chosen_key is not None:
            usage[chosen_key] = usage.get(chosen_key, 0) + 1
            last_used_at[chosen_key] = position
            previous_key = chosen_key
        else:
            previous_key = None

        update: dict[str, object] = {
            "asset_path": chosen,
            "asset_source": "local" if chosen else "missing",
        }
        chosen_meta = folder_asset_map.get(chosen or "", {})
        if chosen_meta.get("asset_id"):
            update["asset_id"] = chosen_meta["asset_id"]
        else:
            update["asset_id"] = asset_id_for_path(chosen) if chosen else ""

        # Wenn die Regelanwendung ein ANDERES Asset zugewiesen hat als
        # ursprünglich geplant (chosen != shot.asset_path), müssen auch die
        # vom Asset abhängigen Metadaten-Felder aufgefrischt werden — sonst
        # blieben rights_status/asset_origin/provider/... vom vorher
        # zugewiesenen (jetzt nicht mehr gültigen) Asset stehen. Das führte
        # z. B. dazu, dass ein per Regel neu zugewiesenes Supplement-Asset
        # fälschlich weiterhin als "local_original" (oder mit leerem
        # asset_origin) im Schnittplan auftauchte.
        if chosen != shot.asset_path:
            update.update(
                {
                    "asset_origin": chosen_meta.get("asset_origin", ""),
                    "rights_status": chosen_meta.get("rights_status", ""),
                    "source_url": chosen_meta.get("source_url", ""),
                    "provider": chosen_meta.get("provider", ""),
                    "media_type": chosen_meta.get("media_type", ""),
                    "supplement_request_id": chosen_meta.get("supplement_request_id", ""),
                }
            )

        adjusted.append(shot.model_copy(update=update))
    return adjusted
