"""Harte max_asset_usage-Regel — zählt Nutzung über asset_id."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass

from otio_app.analysis_models import EditPlanShot, MaxAssetUsageViolation, TimelineItem
from otio_app.services.edit_plan_rules import _enabled_rules, _max_count, _min_gap
from otio_app.services.edit_plan_rules import EditPlanRulesDocument
from otio_app.services.generic_outro_selector import asset_id_for_path

USAGE_COUNTING_TYPES = frozenset(
    {
        "video_shot",
        "image_shot",
        "generic_narration_visual",
        "generic_outro_visual",
        "image_with_background",
    }
)


def visual_usage_timeline_items(items: list[TimelineItem]) -> list[TimelineItem]:
    """Visual shots mit asset_id in Timeline-Reihenfolge."""
    visual = [item for item in items if item.type in USAGE_COUNTING_TYPES]
    return sorted(visual, key=lambda item: (item.timeline_in_sec, item.timeline_item_id))


def asset_id_from_shot(shot: EditPlanShot) -> str | None:
    if shot.asset_id:
        return shot.asset_id
    if shot.asset_path:
        return asset_id_for_path(shot.asset_path)
    return None


def asset_id_from_timeline_item(item: TimelineItem) -> str | None:
    if item.asset_id:
        return item.asset_id
    if item.resolved_media_path:
        return asset_id_for_path(item.resolved_media_path)
    return None


def usage_count_by_asset_id_from_shots(shots: list[EditPlanShot]) -> dict[str, int]:
    counts: Counter[str] = Counter()
    for shot in shots:
        asset_id = asset_id_from_shot(shot)
        if asset_id:
            counts[asset_id] += 1
    return dict(counts)


def usage_count_by_asset_id_from_timeline(items: list[TimelineItem]) -> dict[str, int]:
    counts: Counter[str] = Counter()
    for item in items:
        if item.type not in USAGE_COUNTING_TYPES:
            continue
        asset_id = asset_id_from_timeline_item(item)
        if asset_id:
            counts[asset_id] += 1
    return dict(counts)


@dataclass(frozen=True)
class AssetUsageRules:
    max_asset_usage: int | None
    min_asset_reuse_distance_shots: int
    asset_reuse_policy: str = "hard_block"

    def to_dict(self) -> dict[str, int | str | None]:
        return {
            "max_asset_usage": self.max_asset_usage,
            "min_asset_reuse_distance_shots": self.min_asset_reuse_distance_shots,
            "asset_reuse_policy": self.asset_reuse_policy,
        }


def get_asset_usage_rules(rules_doc: EditPlanRulesDocument) -> AssetUsageRules:
    """Lädt globale Asset-Nutzungsregeln aus dem Regel-Dokument."""
    enabled = _enabled_rules(rules_doc)
    return AssetUsageRules(
        max_asset_usage=_max_count(enabled),
        min_asset_reuse_distance_shots=_min_gap(enabled),
    )


def max_asset_usage_limit(rules_doc: EditPlanRulesDocument) -> int | None:
    return get_asset_usage_rules(rules_doc).max_asset_usage


def filter_assets_by_usage(
    assets: list[dict[str, str]],
    *,
    usage: dict[str, int],
    max_count: int | None,
) -> list[dict[str, str]]:
    if max_count is None:
        return list(assets)
    allowed: list[dict[str, str]] = []
    for asset in assets:
        path = asset.get("path", "")
        if not path:
            continue
        asset_id = asset.get("asset_id") or asset_id_for_path(path)
        if usage.get(asset_id, 0) >= max_count:
            continue
        allowed.append(asset)
    return allowed


def validate_max_asset_usage_blockers(
    *,
    shots: list[EditPlanShot] | None = None,
    timeline_items: list[TimelineItem] | None = None,
    rules_doc: EditPlanRulesDocument,
) -> list[MaxAssetUsageViolation]:
    max_allowed = max_asset_usage_limit(rules_doc)
    if max_allowed is None:
        return []

    if timeline_items is not None:
        counts = usage_count_by_asset_id_from_timeline(timeline_items)
    elif shots is not None:
        counts = usage_count_by_asset_id_from_shots(shots)
    else:
        return []

    violations: list[MaxAssetUsageViolation] = []
    for asset_id, count in sorted(counts.items()):
        if count > max_allowed:
            violations.append(
                MaxAssetUsageViolation(
                    asset_id=asset_id,
                    usage_count=count,
                    max_allowed=max_allowed,
                )
            )
    return violations


def append_max_usage_to_validation_report(
    work_dir_path,
    violations: list[MaxAssetUsageViolation],
) -> None:
    import json
    from pathlib import Path

    from otio_app.services.title_style import validation_report_path

    path = validation_report_path(Path(work_dir_path))
    payload: dict = {}
    if path.is_file():
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            payload = {}
    existing = list(payload.get("max_asset_usage_violations", []))
    for violation in violations:
        entry = violation.model_dump()
        if entry not in existing:
            existing.append(entry)
    payload["max_asset_usage_violations"] = existing
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
