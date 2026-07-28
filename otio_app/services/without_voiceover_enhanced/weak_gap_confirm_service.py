"""Weak-Upgrade-Gaps bewusst mit lokalem Asset schließen.

Ohne besseres Supplement bleiben medium-priority Gaps offen und blockieren
Python Timing (Chicken-Egg: ``kept_local_weak`` entsteht erst im Merge).
Redaktion kann das lokale Weak-Asset bestätigen → Gap gilt als geschlossen.
"""

from __future__ import annotations

from dataclasses import dataclass

from otio_app.models import Project
from otio_app.services.without_voiceover_enhanced.gap_status_service import (
    is_weak_upgrade_gap,
    summarize_gap_status,
)
from otio_app.services.without_voiceover_enhanced.io_utils import load_model, write_json
from otio_app.services.without_voiceover_enhanced.models import (
    CoverageGap,
    CoverageGapsDocument,
    RoughCutPlanDocument,
    UnifiedCutPlanDocument,
)
from otio_app.services.without_voiceover_enhanced.paths import (
    coverage_gaps_path,
    rough_cut_plan_path,
    unified_cut_plan_path,
)
from otio_app.services.without_voiceover_enhanced.script_lock_service import (
    require_locked_script,
)


class WeakGapConfirmError(RuntimeError):
    pass


@dataclass(frozen=True)
class WeakGapConfirmResult:
    gap_id: str
    local_asset_id: str
    slot_id: str = ""


def _slot_local_asset_for_gap(
    project: Project, gap: CoverageGap
) -> tuple[str, str]:
    """(local_asset_id, slot_id) für eine Weak-Gap — sonst ValueError."""
    related = {str(x).strip() for x in (gap.related_shot_ids or []) if str(x).strip()}
    gap_id = (gap.gap_id or "").strip()

    plans: list[UnifiedCutPlanDocument] = []
    global_plan = load_model(unified_cut_plan_path(project), UnifiedCutPlanDocument)
    if global_plan is not None:
        plans.append(global_plan)
    try:
        from otio_app.services.without_voiceover_enhanced.chapter_cut_service import (
            list_body_chapter_names,
            load_chapter_unified_plan,
        )
        from otio_app.services.without_voiceover_enhanced.intro_cut_service import (
            intro_unified_cut_plan_path,
        )

        intro = load_model(
            intro_unified_cut_plan_path(project), UnifiedCutPlanDocument
        )
        if intro is not None:
            plans.append(intro)
        for folder in list_body_chapter_names(project):
            chapter_plan = load_chapter_unified_plan(project, folder)
            if chapter_plan is not None:
                plans.append(chapter_plan)
    except Exception:  # noqa: BLE001 — Rough-Fallback reicht
        pass

    for plan in plans:
        for slot in plan.slots or []:
            slot_gap = str(getattr(slot, "coverage_gap_id", "") or "").strip()
            slot_id = str(getattr(slot, "slot_id", "") or "").strip()
            if gap_id and slot_gap == gap_id:
                asset_id = str(getattr(slot, "local_asset_id", "") or "").strip()
                if asset_id:
                    return asset_id, slot_id
            if slot_id and slot_id in related:
                asset_id = str(getattr(slot, "local_asset_id", "") or "").strip()
                if asset_id:
                    return asset_id, slot_id

    rough = load_model(rough_cut_plan_path(project), RoughCutPlanDocument)
    if rough is not None:
        for shot in rough.shots or []:
            shot_gap = str(getattr(shot, "coverage_gap_id", "") or "").strip()
            shot_id = str(getattr(shot, "shot_id", "") or "").strip()
            if gap_id and shot_gap == gap_id:
                asset_id = str(
                    getattr(shot, "local_asset_id", None)
                    or getattr(shot, "asset_id", None)
                    or ""
                ).strip()
                if asset_id:
                    return asset_id, shot_id
            if shot_id and shot_id in related:
                asset_id = str(
                    getattr(shot, "local_asset_id", None)
                    or getattr(shot, "asset_id", None)
                    or ""
                ).strip()
                if asset_id:
                    return asset_id, shot_id

    raise WeakGapConfirmError(
        f"{gap_id}: kein lokales Asset am zugehörigen Slot — "
        "Weak-Bestätigung nur bei asset_fit=weak mit local_asset_id."
    )


def list_confirmable_weak_gaps(project: Project) -> list[CoverageGap]:
    """Offene Weak-Upgrade-Gaps mit lokalem Asset, noch nicht bestätigt."""
    coverage = load_model(coverage_gaps_path(project), CoverageGapsDocument)
    if coverage is None or not coverage.gaps:
        return []
    open_ids = set(summarize_gap_status(project).open_gap_ids)
    out: list[CoverageGap] = []
    for gap in coverage.gaps:
        gid = (gap.gap_id or "").strip()
        if not gid or gid not in open_ids:
            continue
        if not is_weak_upgrade_gap(gap):
            continue
        if bool(getattr(gap, "user_confirmed_weak", False)):
            continue
        try:
            _slot_local_asset_for_gap(project, gap)
        except WeakGapConfirmError:
            continue
        out.append(gap)
    return out


def confirm_weak_local_asset_for_gap(
    project: Project, gap_id: str
) -> WeakGapConfirmResult:
    """Bestätigt lokales Weak-Asset → Gap geschlossen für Timing/Status."""
    require_locked_script(project)
    gap_id = (gap_id or "").strip()
    if not gap_id:
        raise WeakGapConfirmError("Gap-ID fehlt.")

    coverage = load_model(coverage_gaps_path(project), CoverageGapsDocument)
    if coverage is None or not coverage.gaps:
        raise WeakGapConfirmError("Keine Coverage Gaps vorhanden.")

    gap = next((g for g in coverage.gaps if (g.gap_id or "").strip() == gap_id), None)
    if gap is None:
        raise WeakGapConfirmError(f"Unbekannte Gap-ID: {gap_id}")
    if not is_weak_upgrade_gap(gap):
        raise WeakGapConfirmError(
            f"{gap_id}: nur Weak-Upgrade-Gaps (priority=medium) bestätigbar."
        )

    asset_id, slot_id = _slot_local_asset_for_gap(project, gap)
    if bool(getattr(gap, "user_confirmed_weak", False)):
        return WeakGapConfirmResult(
            gap_id=gap_id, local_asset_id=asset_id, slot_id=slot_id
        )

    updated = [
        (
            g.model_copy(update={"user_confirmed_weak": True})
            if (g.gap_id or "").strip() == gap_id
            else g
        )
        for g in coverage.gaps
    ]
    write_json(
        coverage_gaps_path(project),
        coverage.model_copy(update={"gaps": updated}),
    )
    return WeakGapConfirmResult(
        gap_id=gap_id, local_asset_id=asset_id, slot_id=slot_id
    )


def confirm_all_open_weak_local_assets(
    project: Project,
) -> list[WeakGapConfirmResult]:
    """Bestätigt alle aktuell bestätigbaren Weak-Gaps."""
    results: list[WeakGapConfirmResult] = []
    for gap in list_confirmable_weak_gaps(project):
        results.append(confirm_weak_local_asset_for_gap(project, gap.gap_id))
    return results
