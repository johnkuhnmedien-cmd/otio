"""Generischer Ordner-Fallback für Enhanced Coverage Gaps.

Wenn Stock-Suche / Supplement-Funnel für einen Gap kein ``export_ready``
liefert, wählt dieses Modul ein bereits vorhandenes, neutrales Asset aus
dem Kapitel-Inventar und schreibt es als Accepted Supplement — ohne
Download, ohne Gemini. Wie Cut Plan und Gap-Merge gelten
``max_asset_usage`` und ``min_asset_reuse_distance_shots``. Spiegel der
Classic-Logik in ``cut_plan_generic_fallback_service``, angepasst an
Enhanced (Accepted + Funnel-Report statt CutPlanDocument).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

from otio_app.models import Project
from otio_app.services.generic_outro_selector import (
    GenericAssetCandidate,
    select_generic_outro_assets,
)
from otio_app.services.inventory_loader import load_folder_inventory
from otio_app.services.media_utils import is_image_media, probe_duration_seconds
from otio_app.services.without_voiceover_enhanced.cut_plan_options import (
    load_cut_plan_options,
)
from otio_app.services.without_voiceover_enhanced.io_utils import load_model, write_json
from otio_app.services.without_voiceover_enhanced.local_media_service import (
    STATUS_EXPORT_READY,
    list_export_ready_supplements,
    validate_local_media_path,
)
from otio_app.services.without_voiceover_enhanced.models import (
    AcceptedSupplementsDocument,
    CoverageGap,
    CoverageGapsDocument,
    StockCandidate,
    SupplementFunnelGapReport,
    SupplementFunnelReport,
    UnifiedCutPlanDocument,
)
from otio_app.services.without_voiceover_enhanced.paths import (
    accepted_supplements_path,
    chapter_unified_cut_plan_path,
    coverage_gaps_path,
    supplement_funnel_report_path,
    unified_cut_plan_path,
)
from otio_app.services.without_voiceover_enhanced.script_lock_service import (
    require_locked_script,
)
from otio_app.services.without_voiceover_enhanced.supplement_resolve_service import (
    _folder_for_gap,
)

__all__ = [
    "GENERIC_FALLBACK_CANDIDATE_POOL_SIZE",
    "GenericGapFallbackError",
    "GenericGapFallbackResult",
    "GenericGapFallbackBatchResult",
    "AssetUsageLedger",
    "build_asset_usage_ledger",
    "select_generic_fallback_for_gap",
    "apply_generic_fallback_to_gap",
    "apply_generic_fallback_to_open_gaps",
    "try_generic_fallback_after_stock_fail",
]

GENERIC_FALLBACK_CANDIDATE_POOL_SIZE = 5
_DURATION_EPSILON = 0.05
_DEFAULT_NEEDED_DURATION_SEC = 5.0
ASSIGN_STATUS_GENERIC_FALLBACK = "generic_fallback"
PROVIDER_GENERIC_FALLBACK = "generic_fallback"


class GenericGapFallbackError(RuntimeError):
    pass


@dataclass
class AssetUsageLedger:
    """Laufende Asset-Nutzung (Plan + Accepted + bereits in diesem Lauf vergeben)."""

    usage_by_asset_id: dict[str, int] = field(default_factory=dict)
    used_paths: set[str] = field(default_factory=set)
    # Cut-Plan-Reihenfolge: Asset-ID oder "" bei offenem Gap. Für Reuse-Abstand.
    editorial_asset_ids: list[str] = field(default_factory=list)
    gap_index_by_id: dict[str, int] = field(default_factory=dict)
    slot_index_by_id: dict[str, int] = field(default_factory=dict)
    intro_asset_ids: set[str] = field(default_factory=set)

    def note_use(self, asset_id: str, path: str, gap_id: str = "") -> None:
        aid = (asset_id or "").strip()
        if aid:
            self.usage_by_asset_id[aid] = self.usage_by_asset_id.get(aid, 0) + 1
        resolved = str(Path(path).resolve()) if path else ""
        if resolved:
            self.used_paths.add(resolved)
            self.used_paths.add(path)
        gid = (gap_id or "").strip()
        if aid and gid and gid in self.gap_index_by_id:
            index = self.gap_index_by_id[gid]
            if 0 <= index < len(self.editorial_asset_ids):
                self.editorial_asset_ids[index] = aid


@dataclass
class GenericGapFallbackResult:
    gap_id: str
    status: str  # filled | skipped | failed
    candidate: StockCandidate | None = None
    asset_id: str = ""
    message: str = ""


@dataclass
class GenericGapFallbackBatchResult:
    results: list[GenericGapFallbackResult] = field(default_factory=list)

    @property
    def filled_count(self) -> int:
        return sum(1 for r in self.results if r.status == "filled")

    @property
    def failed_count(self) -> int:
        return sum(1 for r in self.results if r.status == "failed")


def _load_cut_plans(
    project: Project,
) -> tuple[UnifiedCutPlanDocument | None, list[UnifiedCutPlanDocument]]:
    merged = load_model(unified_cut_plan_path(project), UnifiedCutPlanDocument)
    chapters: list[UnifiedCutPlanDocument] = []
    for folder in project.selected_asset_subdirs or []:
        chapter = load_model(
            chapter_unified_cut_plan_path(project, folder),
            UnifiedCutPlanDocument,
        )
        if chapter is not None:
            chapters.append(chapter)
    return merged, chapters


def _plans_for_editorial_sequence(
    merged: UnifiedCutPlanDocument | None,
    chapters: list[UnifiedCutPlanDocument],
) -> list[UnifiedCutPlanDocument]:
    """Filmweite Reihenfolge: gemergter Plan, sonst Kapitelpläne hintereinander."""
    if merged is not None and merged.slots:
        return [merged]
    return [plan for plan in chapters if plan.slots]


def _all_cut_plans(
    merged: UnifiedCutPlanDocument | None,
    chapters: list[UnifiedCutPlanDocument],
) -> list[UnifiedCutPlanDocument]:
    plans: list[UnifiedCutPlanDocument] = []
    if merged is not None:
        plans.append(merged)
    plans.extend(chapters)
    return plans


def _intro_asset_ids(plans: list[UnifiedCutPlanDocument]) -> set[str]:
    ids: set[str] = set()
    for plan in plans:
        for attr in ("intro_opener_asset_id", "intro_closing_asset_id"):
            aid = str(getattr(plan, attr, None) or "").strip()
            if aid:
                ids.add(aid)
    return ids


def _seed_editorial_sequence(
    project: Project,
    ledger: AssetUsageLedger,
    *,
    merged: UnifiedCutPlanDocument | None,
    chapters: list[UnifiedCutPlanDocument],
) -> None:
    plans = _all_cut_plans(merged, chapters)
    ledger.intro_asset_ids = _intro_asset_ids(plans)
    for plan in _plans_for_editorial_sequence(merged, chapters):
        for slot in plan.slots or []:
            slot_id = str(slot.slot_id or "").strip()
            gap_id = str(slot.coverage_gap_id or "").strip()
            fit = str(slot.asset_fit or "none").strip().lower()
            aid = str(slot.local_asset_id or "").strip()
            if fit == "none":
                aid = ""
            index = len(ledger.editorial_asset_ids)
            ledger.editorial_asset_ids.append(aid)
            if slot_id:
                ledger.slot_index_by_id[slot_id] = index
            if gap_id:
                ledger.gap_index_by_id[gap_id] = index
    coverage = load_model(coverage_gaps_path(project), CoverageGapsDocument)
    if coverage is None:
        return
    for gap in coverage.gaps or []:
        gid = str(gap.gap_id or "").strip()
        if not gid or gid in ledger.gap_index_by_id:
            continue
        for related in gap.related_shot_ids or []:
            slot_id = str(related or "").strip()
            if slot_id in ledger.slot_index_by_id:
                ledger.gap_index_by_id[gid] = ledger.slot_index_by_id[slot_id]
                break


def _strip_generic_asset_id(asset_id: str) -> str:
    aid = str(asset_id or "").strip()
    if aid.startswith("generic_"):
        return aid[len("generic_") :]
    return aid


def _gap_editorial_index(gap: CoverageGap, ledger: AssetUsageLedger) -> int | None:
    gid = str(gap.gap_id or "").strip()
    if gid and gid in ledger.gap_index_by_id:
        return ledger.gap_index_by_id[gid]
    for related in gap.related_shot_ids or []:
        slot_id = str(related or "").strip()
        if slot_id in ledger.slot_index_by_id:
            return ledger.slot_index_by_id[slot_id]
    return None


def _generic_fallback_reuse_violation(
    asset_id: str,
    *,
    editorial_asset_ids: list[str],
    gap_index: int,
    min_asset_reuse_distance_shots: int,
    intro_asset_ids: set[str],
    reuse_key_index: Mapping[str, str] | None,
) -> str | None:
    """Hard filter: Nachbar und min_asset_reuse_distance_shots wie Cut-Plan/Gap-Merge."""
    from otio_app.services.without_voiceover_enhanced.enhanced_supplement_dedupe import (
        reuse_identity_key,
    )

    aid = str(asset_id or "").strip()
    if not aid or gap_index < 0 or gap_index >= len(editorial_asset_ids):
        return None
    key = reuse_identity_key(aid, index=reuse_key_index)
    if not key:
        return None
    min_gap = max(1, int(min_asset_reuse_distance_shots or 0))
    intro = {str(item).strip() for item in intro_asset_ids if str(item).strip()}
    last_before: int | None = None
    next_after: int | None = None
    for index, other_id in enumerate(editorial_asset_ids):
        if index == gap_index:
            continue
        other = str(other_id or "").strip()
        if not other or other in intro:
            continue
        if reuse_identity_key(other, index=reuse_key_index) != key:
            continue
        if index < gap_index:
            last_before = index
        elif next_after is None:
            next_after = index

    if last_before is not None:
        gap_shots = gap_index - int(last_before) - 1
        if gap_shots < min_gap:
            if gap_shots == 0:
                return f"Benachbartes Asset {aid} bereits im Cut Plan."
            return (
                f"Asset {aid} erneut nach {gap_shots} Shots "
                f"(min Abstand {min_gap})."
            )
    if next_after is not None:
        gap_shots = int(next_after) - gap_index - 1
        if gap_shots < min_gap:
            if gap_shots == 0:
                return f"Benachbartes Asset {aid} bereits im Cut Plan."
            return (
                f"Asset {aid} vor Wiederverwendung nur {gap_shots} Shots "
                f"Abstand (min {min_gap})."
            )
    return None


def _preceding_asset_path(
    *,
    editorial_asset_ids: list[str],
    gap_index: int,
    path_by_asset_id: dict[str, str],
) -> str | None:
    for index in range(gap_index - 1, -1, -1):
        aid = str(editorial_asset_ids[index] or "").strip()
        if not aid:
            continue
        path = path_by_asset_id.get(aid)
        if path:
            return path
    return None


def build_asset_usage_ledger(project: Project) -> AssetUsageLedger:
    """Zählt bereits verplante / Accepted Assets für max_asset_usage und Reuse-Abstand."""
    ledger = AssetUsageLedger()
    merged, chapters = _load_cut_plans(project)
    plans = _all_cut_plans(merged, chapters)
    _seed_editorial_sequence(project, ledger, merged=merged, chapters=chapters)
    for plan in plans:
        for slot in plan.slots or []:
            aid = str(getattr(slot, "local_asset_id", None) or "").strip()
            if not aid:
                continue
            ledger.usage_by_asset_id[aid] = ledger.usage_by_asset_id.get(aid, 0) + 1
        closer = str(getattr(plan, "closing_fallback_asset_id", None) or "").strip()
        if closer:
            ledger.usage_by_asset_id[closer] = (
                ledger.usage_by_asset_id.get(closer, 0) + 1
            )
        for _chapter, asset_id in (plan.closing_fallback_by_chapter or {}).items():
            aid = str(asset_id or "").strip()
            if aid:
                ledger.usage_by_asset_id[aid] = (
                    ledger.usage_by_asset_id.get(aid, 0) + 1
                )
    for supplement in list_export_ready_supplements(project):
        path = str(supplement.local_media_path or "").strip()
        aid = _strip_generic_asset_id(
            str(supplement.provider_asset_id or supplement.candidate_id or "")
        )
        if path:
            ledger.used_paths.add(path)
            try:
                ledger.used_paths.add(str(Path(path).resolve()))
            except OSError:
                pass
        if aid:
            # Accepted zählt als Nutzung (auch wenn Plan-Slot noch Placeholder).
            ledger.usage_by_asset_id[aid] = ledger.usage_by_asset_id.get(aid, 0) + 1
            gap_id = str(supplement.gap_id or "").strip()
            if gap_id and gap_id in ledger.gap_index_by_id:
                index = ledger.gap_index_by_id[gap_id]
                if 0 <= index < len(ledger.editorial_asset_ids):
                    ledger.editorial_asset_ids[index] = aid
    return ledger


def _resolve_media_path(project: Project, raw_path: str) -> Path | None:
    text = (raw_path or "").strip()
    if not text:
        return None
    path = Path(text)
    if path.is_file():
        return path
    candidate = project.project_root_path / text
    if candidate.is_file():
        return candidate
    return None


def _needed_duration_seconds(gap: CoverageGap) -> float:
    raw = gap.target_duration_seconds
    if raw is None:
        return _DEFAULT_NEEDED_DURATION_SEC
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return _DEFAULT_NEEDED_DURATION_SEC
    if value <= 0:
        return _DEFAULT_NEEDED_DURATION_SEC
    return value


def select_generic_fallback_for_gap(
    project: Project,
    gap: CoverageGap,
    *,
    ledger: AssetUsageLedger | None = None,
    needed_duration_sec: float | None = None,
) -> tuple[GenericAssetCandidate | None, str, Path | None]:
    """Wählt neutrales Ordner-Asset; liefert (candidate, inventory_asset_id, path)."""
    locked = require_locked_script(project)
    folder = _folder_for_gap(project, gap, locked)
    if not folder:
        return None, "", None
    inventory = load_folder_inventory(project, folder)
    if inventory is None or not inventory.assets:
        return None, "", None

    options = load_cut_plan_options(project)
    max_usage = int(options.max_asset_usage or 2)
    min_reuse_distance = int(options.min_asset_reuse_distance_shots or 0)
    head_trim = float(options.video_head_trim_sec or 0.0)
    needed = (
        float(needed_duration_sec)
        if needed_duration_sec is not None
        else _needed_duration_seconds(gap)
    )
    active_ledger = ledger or build_asset_usage_ledger(project)
    gap_index = _gap_editorial_index(gap, active_ledger)

    folder_assets: list[dict[str, str]] = []
    path_to_asset_id: dict[str, str] = {}
    for asset in inventory.assets:
        media = _resolve_media_path(project, str(asset.path or ""))
        if media is None:
            continue
        abs_path = str(media.resolve())
        aid = str(asset.asset_id or "").strip() or media.stem
        folder_assets.append(
            {"path": abs_path, "description": str(asset.description or "")}
        )
        path_to_asset_id[abs_path] = aid

    if not folder_assets:
        return None, "", None

    path_by_asset_id = {aid: path for path, aid in path_to_asset_id.items()}
    last_asset_path = None
    if gap_index is not None:
        last_asset_path = _preceding_asset_path(
            editorial_asset_ids=active_ledger.editorial_asset_ids,
            gap_index=gap_index,
            path_by_asset_id=path_by_asset_id,
        )

    from otio_app.services.without_voiceover_enhanced.enhanced_supplement_dedupe import (
        build_asset_reuse_key_index,
    )

    reuse_key_index = build_asset_reuse_key_index(project)
    ranked = select_generic_outro_assets(
        folder_assets,
        used_paths=set(active_ledger.used_paths),
        last_asset_path=last_asset_path,
        count=max(GENERIC_FALLBACK_CANDIDATE_POOL_SIZE, len(folder_assets)),
        min_duration_sec=needed,
        usage_by_asset_id={
            # Score-Funktion nutzt path-basierte IDs — Usage prüfen wir hart unten.
        },
        max_asset_usage=None,
    )

    for candidate in ranked:
        abs_path = str(Path(candidate.path).resolve())
        inventory_id = path_to_asset_id.get(abs_path, candidate.asset_id)
        if active_ledger.usage_by_asset_id.get(inventory_id, 0) >= max_usage:
            continue
        if gap_index is not None and _generic_fallback_reuse_violation(
            inventory_id,
            editorial_asset_ids=active_ledger.editorial_asset_ids,
            gap_index=gap_index,
            min_asset_reuse_distance_shots=min_reuse_distance,
            intro_asset_ids=active_ledger.intro_asset_ids,
            reuse_key_index=reuse_key_index,
        ):
            continue
        local_path = Path(candidate.path)
        if not local_path.is_file():
            continue
        if is_image_media(local_path):
            remapped = GenericAssetCandidate(
                path=candidate.path,
                asset_id=inventory_id,
                description=candidate.description,
                score=candidate.score,
                selection_reason=candidate.selection_reason,
                warnings=list(candidate.warnings),
            )
            return remapped, inventory_id, local_path
        duration = probe_duration_seconds(local_path)
        usable = max(0.0, (duration or 0.0) - head_trim)
        if needed <= usable + _DURATION_EPSILON:
            remapped = GenericAssetCandidate(
                path=candidate.path,
                asset_id=inventory_id,
                description=candidate.description,
                score=candidate.score,
                selection_reason=candidate.selection_reason,
                warnings=list(candidate.warnings),
            )
            return remapped, inventory_id, local_path
    return None, "", None


def _current_cut_plan_run_id(project: Project) -> str:
    from otio_app.services.without_voiceover_enhanced.gap_status_service import (
        compute_cut_plan_run_id_from_path,
    )

    coverage = load_model(coverage_gaps_path(project), CoverageGapsDocument)
    run_id = str(getattr(coverage, "cut_plan_run_id", "") or "").strip()
    if run_id:
        return run_id
    return compute_cut_plan_run_id_from_path(unified_cut_plan_path(project))


def _upsert_accepted(project: Project, candidate: StockCandidate) -> None:
    locked = require_locked_script(project)
    run_id = _current_cut_plan_run_id(project)
    if run_id:
        candidate.cut_plan_run_id = run_id
    existing = load_model(accepted_supplements_path(project), AcceptedSupplementsDocument)
    supplements = list(existing.supplements) if existing else []
    gap_id = (candidate.gap_id or "").strip()
    supplements = [
        item
        for item in supplements
        if item.candidate_id != candidate.candidate_id
        and (not gap_id or (item.gap_id or "") != gap_id)
    ]
    supplements.append(candidate)
    write_json(
        accepted_supplements_path(project),
        AcceptedSupplementsDocument(
            script_version=locked.script_version,
            supplements=supplements,
        ),
    )
    from otio_app.services.without_voiceover_enhanced.coverage_gap_external_export import (
        refresh_coverage_gaps_external_export,
    )

    refresh_coverage_gaps_external_export(project)


def _mark_gap_filled_in_funnel_report(
    project: Project,
    *,
    gap_id: str,
    candidate_id: str,
    asset_id: str,
    report: SupplementFunnelReport | None = None,
    gap_report: SupplementFunnelGapReport | None = None,
) -> None:
    message = (
        f"export_ready: {candidate_id} (generischer Ordner-Fallback ← {asset_id})"
    )
    if report is not None and gap_report is not None:
        gap_report.filled = True
        gap_report.export_ready_candidate_id = candidate_id
        gap_report.message = message
        for index, existing in enumerate(report.gaps):
            if existing.gap_id == gap_id:
                report.gaps[index] = gap_report
                break
        else:
            report.gaps.append(gap_report)
        if gap_id not in report.filled_gap_ids:
            report.filled_gap_ids.append(gap_id)
        report.open_gap_ids = [gid for gid in report.open_gap_ids if gid != gap_id]
        write_json(supplement_funnel_report_path(project), report)
        return

    existing_report = load_model(
        supplement_funnel_report_path(project), SupplementFunnelReport
    )
    if existing_report is None:
        return
    found = False
    for index, gap_rep in enumerate(existing_report.gaps):
        if gap_rep.gap_id != gap_id:
            continue
        gap_rep.filled = True
        gap_rep.export_ready_candidate_id = candidate_id
        gap_rep.message = message
        existing_report.gaps[index] = gap_rep
        found = True
        break
    if not found:
        existing_report.gaps.append(
            SupplementFunnelGapReport(
                gap_id=gap_id,
                filled=True,
                export_ready_candidate_id=candidate_id,
                message=message,
            )
        )
    if gap_id not in existing_report.filled_gap_ids:
        existing_report.filled_gap_ids.append(gap_id)
    existing_report.open_gap_ids = [
        gid for gid in existing_report.open_gap_ids if gid != gap_id
    ]
    write_json(supplement_funnel_report_path(project), existing_report)


def apply_generic_fallback_to_gap(
    project: Project,
    gap_id: str,
    *,
    ledger: AssetUsageLedger | None = None,
    report: SupplementFunnelReport | None = None,
    gap_report: SupplementFunnelGapReport | None = None,
) -> GenericGapFallbackResult:
    """Weist einem offenen Gap ein generisches Ordner-Asset zu (Accepted)."""
    gap_id = (gap_id or "").strip()
    if not gap_id:
        raise GenericGapFallbackError("Gap-ID fehlt.")

    coverage = load_model(coverage_gaps_path(project), CoverageGapsDocument)
    if coverage is None:
        raise GenericGapFallbackError("Keine Coverage Gaps vorhanden.")
    gap = next((item for item in coverage.gaps if item.gap_id == gap_id), None)
    if gap is None:
        raise GenericGapFallbackError(f"Unbekannte Gap-ID: {gap_id}")

    active_ledger = ledger or build_asset_usage_ledger(project)
    chosen, inventory_id, media_path = select_generic_fallback_for_gap(
        project, gap, ledger=active_ledger
    )
    if chosen is None or media_path is None or not inventory_id:
        return GenericGapFallbackResult(
            gap_id=gap_id,
            status="failed",
            message="Kein geeignetes generisches Ordner-Asset verfügbar.",
        )

    media_type = "photo" if is_image_media(media_path) else "video"
    status, error = validate_local_media_path(str(media_path), media_type)
    if status != STATUS_EXPORT_READY:
        return GenericGapFallbackResult(
            gap_id=gap_id,
            status="failed",
            message=error or f"Ordner-Asset technisch ungültig ({status}).",
        )

    duration_seconds: float | None = None
    if media_type == "video":
        try:
            probed = probe_duration_seconds(media_path)
            if probed is not None and float(probed) > 0:
                duration_seconds = float(probed)
        except Exception:  # noqa: BLE001
            duration_seconds = None

    description = (
        (gap.needed_visual or "").strip()
        or (gap.subject or "").strip()
        or chosen.description
        or inventory_id
    )
    candidate_id = f"generic_{inventory_id}"
    candidate = StockCandidate(
        candidate_id=candidate_id,
        provider=PROVIDER_GENERIC_FALLBACK,
        provider_asset_id=inventory_id,
        title=(description[:120] or inventory_id),
        media_type=media_type,
        creator="inventory",
        source_page=str(media_path),
        download_url="",
        preview_url="",
        license="project_inventory",
        attribution="Generischer Ordner-Fallback",
        selected=True,
        gap_id=gap_id,
        local_media_path=str(media_path),
        duration_seconds=duration_seconds,
        media_validation_status=STATUS_EXPORT_READY,
        media_validation_error=None,
        funnel_managed=True,
        license_metadata_status="complete",
        cut_plan_run_id=_current_cut_plan_run_id(project),
        assign_status=ASSIGN_STATUS_GENERIC_FALLBACK,
    )
    _upsert_accepted(project, candidate)
    _mark_gap_filled_in_funnel_report(
        project,
        gap_id=gap_id,
        candidate_id=candidate_id,
        asset_id=inventory_id,
        report=report,
        gap_report=gap_report,
    )
    active_ledger.note_use(inventory_id, str(media_path), gap_id=gap_id)
    return GenericGapFallbackResult(
        gap_id=gap_id,
        status="filled",
        candidate=candidate,
        asset_id=inventory_id,
        message=(
            f"Generischer Fallback: `{inventory_id}` → Gap `{gap_id}` "
            f"({chosen.selection_reason})"
        ),
    )


def apply_generic_fallback_to_open_gaps(
    project: Project,
    *,
    gap_ids: list[str] | None = None,
) -> GenericGapFallbackBatchResult:
    """Batch: generischer Fallback für offene Gaps (UI-Button / Nachzug)."""
    from otio_app.services.without_voiceover_enhanced.gap_status_service import (
        summarize_gap_status,
    )

    open_ids = list(summarize_gap_status(project).open_gap_ids)
    if gap_ids is not None:
        wanted = {str(g).strip() for g in gap_ids if str(g).strip()}
        open_ids = [gid for gid in open_ids if gid in wanted]
    if not open_ids:
        return GenericGapFallbackBatchResult(results=[])

    ledger = build_asset_usage_ledger(project)
    results: list[GenericGapFallbackResult] = []
    for gap_id in open_ids:
        try:
            results.append(
                apply_generic_fallback_to_gap(
                    project, gap_id, ledger=ledger
                )
            )
        except GenericGapFallbackError as exc:
            results.append(
                GenericGapFallbackResult(
                    gap_id=gap_id, status="failed", message=str(exc)
                )
            )
    return GenericGapFallbackBatchResult(results=results)


def try_generic_fallback_after_stock_fail(
    project: Project,
    *,
    gap: CoverageGap,
    report: SupplementFunnelReport,
    gap_report: SupplementFunnelGapReport,
    ledger: AssetUsageLedger,
) -> bool:
    """Funnel-Hook: nach Stock-Fail generischen Fallback versuchen.

    Bei Erfolg: Gap als filled markieren, nicht in ``open_gap_ids``.
    Bei Misserfolg: False — Caller hängt Gap an ``open_gap_ids``.
    """
    result = apply_generic_fallback_to_gap(
        project,
        gap.gap_id,
        ledger=ledger,
        report=report,
        gap_report=gap_report,
    )
    if result.status != "filled":
        if result.message:
            stock_msg = (gap_report.message or "").strip()
            gap_report.message = (
                f"{stock_msg} · Generic-Fallback: {result.message}"
                if stock_msg
                else f"Generic-Fallback: {result.message}"
            )
        return False
    report.generic_fallback_count = int(report.generic_fallback_count or 0) + 1
    return True
