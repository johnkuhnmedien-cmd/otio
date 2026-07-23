"""Gap-Status pro Cut-Plan-Lauf (Fix 4): weak offen bis Merge; kein Stale-Zähler."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path

from otio_app.models import Project
from otio_app.services.without_voiceover_enhanced.io_utils import load_model
from otio_app.services.without_voiceover_enhanced.models import (
    CoverageGap,
    CoverageGapsDocument,
    GapMergeReport,
    SupplementFunnelReport,
    UnifiedCutPlanDocument,
)
from otio_app.services.without_voiceover_enhanced.paths import (
    coverage_gaps_path,
    gap_merge_report_path,
    supplement_funnel_report_path,
    unified_cut_plan_path,
)

__all__ = [
    "GapStatusSummary",
    "compute_cut_plan_run_id",
    "compute_cut_plan_run_id_from_path",
    "is_weak_upgrade_gap",
    "summarize_gap_status",
]


@dataclass
class GapStatusSummary:
    total: int = 0
    open_gap_ids: list[str] = field(default_factory=list)
    filled_gap_ids: list[str] = field(default_factory=list)
    cut_plan_run_id: str = ""
    funnel_stale: bool = False
    merge_stale: bool = False
    message: str = ""

    @property
    def open_count(self) -> int:
        return len(self.open_gap_ids)

    @property
    def filled_count(self) -> int:
        return len(self.filled_gap_ids)


def compute_cut_plan_run_id(plan: UnifiedCutPlanDocument | dict | None) -> str:
    """Stabiler Hash über den Unified Cut Plan (Inhalt, nicht mtime)."""
    if plan is None:
        return ""
    if isinstance(plan, UnifiedCutPlanDocument):
        payload = plan.model_dump(mode="json")
    else:
        payload = plan
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]


def compute_cut_plan_run_id_from_path(path: Path) -> str:
    if not path.is_file():
        return ""
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return ""
    if not isinstance(payload, dict):
        return ""
    return compute_cut_plan_run_id(payload)


def is_weak_upgrade_gap(gap: CoverageGap) -> bool:
    """weak-Upgrade: priority=medium (none = high)."""
    return str(gap.priority or "").strip().lower() == "medium"


def _funnel_export_ready_ids(
    funnel: SupplementFunnelReport | None,
    *,
    expected_run_id: str,
) -> tuple[set[str], bool]:
    """filled/export_ready IDs nur wenn Run-ID passt; sonst stale."""
    if funnel is None:
        return set(), False
    funnel_run = str(getattr(funnel, "cut_plan_run_id", "") or "").strip()
    if expected_run_id and funnel_run and funnel_run != expected_run_id:
        return set(), True
    if expected_run_id and not funnel_run:
        # Alter Report ohne Run-ID: als stale behandeln, wenn Coverage eine hat.
        return set(), True
    ready: set[str] = set()
    for gap_rep in funnel.gaps:
        gid = (gap_rep.gap_id or "").strip()
        if not gid:
            continue
        if gap_rep.filled or gap_rep.export_ready_candidate_id:
            ready.add(gid)
            continue
        if any(
            str(c.funnel_status or "") == "export_ready" for c in gap_rep.candidates
        ):
            ready.add(gid)
    for gid in funnel.filled_gap_ids or []:
        if str(gid).strip():
            ready.add(str(gid).strip())
    return ready, False


def _merge_closed_ids(
    merge: GapMergeReport | None,
    *,
    expected_run_id: str,
) -> tuple[set[str], bool]:
    """merged / kept_local_weak schließen Gaps; nur bei passender Run-ID."""
    if merge is None:
        return set(), False
    merge_run = str(getattr(merge, "cut_plan_run_id", "") or "").strip()
    if expected_run_id and merge_run and merge_run != expected_run_id:
        return set(), True
    if expected_run_id and not merge_run:
        return set(), True
    closed: set[str] = set()
    for slot in merge.slots or []:
        status = str(slot.status or "").strip().lower()
        gid = (slot.coverage_gap_id or "").strip()
        if not gid:
            continue
        if status in {"merged", "kept_local_weak"}:
            closed.add(gid)
    return closed, False


def summarize_gap_status(project: Project) -> GapStatusSummary:
    """Aktueller Gap-Status relativ zum Unified-Cut-Plan-Lauf.

    Regeln (Fix 4):
    - Gap-Liste kommt aus coverage_gaps.json (aktueller Plan).
    - weak-Upgrade bleibt offen bis Merge (merged | kept_local_weak).
    - none kann durch Funnel export_ready (gleiche Run-ID) als erfüllt gelten.
    - Stale Funnel/Merge (andere/fehlende Run-ID) zählen nicht.
    """
    coverage = load_model(coverage_gaps_path(project), CoverageGapsDocument)
    if coverage is None or not coverage.gaps:
        return GapStatusSummary(message="Keine Coverage Gaps.")

    run_id = str(getattr(coverage, "cut_plan_run_id", "") or "").strip()
    if not run_id:
        run_id = compute_cut_plan_run_id_from_path(unified_cut_plan_path(project))

    funnel = load_model(supplement_funnel_report_path(project), SupplementFunnelReport)
    merge = load_model(gap_merge_report_path(project), GapMergeReport)
    funnel_ready, funnel_stale = _funnel_export_ready_ids(
        funnel, expected_run_id=run_id
    )
    merge_closed, merge_stale = _merge_closed_ids(merge, expected_run_id=run_id)

    # Zusätzlich: Coverage neuer als Funnel → Funnel-Zähler stale.
    cov_path = coverage_gaps_path(project)
    fun_path = supplement_funnel_report_path(project)
    if (
        not funnel_stale
        and cov_path.is_file()
        and fun_path.is_file()
        and cov_path.stat().st_mtime_ns > fun_path.stat().st_mtime_ns
        and run_id
        and str(getattr(funnel, "cut_plan_run_id", "") or "") != run_id
    ):
        funnel_stale = True
        funnel_ready = set()

    open_ids: list[str] = []
    filled_ids: list[str] = []
    for gap in coverage.gaps:
        gid = (gap.gap_id or "").strip()
        if not gid:
            continue
        if gid in merge_closed:
            filled_ids.append(gid)
            continue
        if is_weak_upgrade_gap(gap):
            # Lokales Asset / alter Funnel zählt nicht.
            open_ids.append(gid)
            continue
        # none / high: Funnel export_ready mit passender Run-ID reicht für UI „erfüllt“.
        if gid in funnel_ready:
            filled_ids.append(gid)
        else:
            open_ids.append(gid)

    notes: list[str] = []
    if funnel_stale:
        notes.append("Funnel-Report gehört zu einem älteren Cut-Plan-Lauf")
    if merge_stale:
        notes.append("Gap-Merge-Report gehört zu einem älteren Cut-Plan-Lauf")
    return GapStatusSummary(
        total=len(open_ids) + len(filled_ids),
        open_gap_ids=open_ids,
        filled_gap_ids=filled_ids,
        cut_plan_run_id=run_id,
        funnel_stale=funnel_stale,
        merge_stale=merge_stale,
        message="; ".join(notes),
    )
