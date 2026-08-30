"""Gap-Status pro Cut-Plan-Lauf: Download/Accepted schließt Gaps in der UI."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path

from otio_app.models import Project
from otio_app.services.without_voiceover_enhanced.io_utils import load_model, write_json
from otio_app.services.without_voiceover_enhanced.models import (
    AcceptedSupplementsDocument,
    CoverageGap,
    CoverageGapsDocument,
    GapMergeReport,
    StockCandidate,
    SupplementFunnelReport,
    UnifiedCutPlanDocument,
)
from otio_app.services.without_voiceover_enhanced.paths import (
    UNIFIED_CUT_PLAN_FILENAME,
    accepted_supplements_path,
    chapters_cut_dir,
    coverage_gaps_path,
    gap_merge_report_path,
    stock_candidate_download_dir,
    supplement_funnel_report_path,
    unified_cut_plan_path,
)

__all__ = [
    "GapStatusSummary",
    "carry_over_user_confirmed_weak",
    "compute_cut_plan_run_id",
    "compute_cut_plan_run_id_from_path",
    "is_weak_upgrade_gap",
    "rebind_gap_fills_to_current_run",
    "sanitize_stale_user_confirmed_weak",
    "summarize_gap_status",
    "sync_missing_plan_gaps_into_coverage",
]


def carry_over_user_confirmed_weak(
    coverage: CoverageGapsDocument,
    previous: CoverageGapsDocument | None,
) -> CoverageGapsDocument:
    """Behält Weak-Bestätigungen bei Coverage-Rebuild (gleiche Gap-ID).

    Nur für echte Weak-Upgrade-Gaps (``priority=medium``). High/none-Gaps
    (typisch nach Cut-Settings-/Style-Wechsel) erben keine alte Bestätigung —
    sonst bleiben Placeholder-Slots in der UI fälschlich „erfüllt“.
    """
    if previous is None or not previous.gaps:
        return coverage
    confirmed = {
        str(gap.gap_id or "").strip()
        for gap in previous.gaps
        if str(gap.gap_id or "").strip()
        and bool(getattr(gap, "user_confirmed_weak", False))
        and is_weak_upgrade_gap(gap)
    }
    if not confirmed:
        return coverage
    updated: list[CoverageGap] = []
    changed = False
    for gap in coverage.gaps or []:
        gid = str(gap.gap_id or "").strip()
        if (
            gid in confirmed
            and is_weak_upgrade_gap(gap)
            and not bool(getattr(gap, "user_confirmed_weak", False))
        ):
            updated.append(gap.model_copy(update={"user_confirmed_weak": True}))
            changed = True
        else:
            updated.append(gap)
    if not changed:
        return coverage
    return coverage.model_copy(update={"gaps": updated})


def sanitize_stale_user_confirmed_weak(project: Project) -> int:
    """Löscht ``user_confirmed_weak`` auf Gaps, die keine Weak-Upgrades sind.

    Heilt Alt-Läufe (z. B. Rhythmus → Keyword Flow), in denen Bestätigungen
    per Gap-ID auf high/none-Gaps mitgeschleppt wurden.
    """
    coverage = load_model(coverage_gaps_path(project), CoverageGapsDocument)
    if coverage is None or not coverage.gaps:
        return 0
    updated: list[CoverageGap] = []
    cleared = 0
    for gap in coverage.gaps:
        if bool(getattr(gap, "user_confirmed_weak", False)) and not is_weak_upgrade_gap(
            gap
        ):
            updated.append(gap.model_copy(update={"user_confirmed_weak": False}))
            cleared += 1
        else:
            updated.append(gap)
    if not cleared:
        return 0
    from otio_app.services.without_voiceover_enhanced.coverage_gap_external_export import (
        persist_coverage_gaps,
    )

    persist_coverage_gaps(
        project,
        coverage.model_copy(update={"gaps": updated}),
    )
    return cleared


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
    """Stabiler Hash über den Unified Cut Plan (Inhalt, nicht mtime).

    ``target_duration_seconds`` wird ignoriert — Timing schreibt diese Felder
    nach; sonst invalidiert jeder Timing-Lauf Funnel/Accepted (Run-ID-Drift).
    """
    if plan is None:
        return ""
    if isinstance(plan, UnifiedCutPlanDocument):
        payload = plan.model_dump(mode="json")
    else:
        payload = dict(plan)
    slots = payload.get("slots")
    if isinstance(slots, list):
        cleaned_slots = []
        for slot in slots:
            if isinstance(slot, dict):
                slot = dict(slot)
                slot.pop("target_duration_seconds", None)
            cleaned_slots.append(slot)
        payload["slots"] = cleaned_slots
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


_PLAN_GAP_SYNC_DEPTH = 0


def _coverage_gap_from_plan_slot(slot: object) -> CoverageGap | None:
    """Coverage-Gap aus einem Kapitel-Plan-Slot (weak/none, keine Bridges)."""
    from otio_app.services.without_voiceover_enhanced.models import GAP_FIT_VALUES

    fit = str(getattr(slot, "asset_fit", "") or "").strip().lower()
    slot_id = str(getattr(slot, "slot_id", "") or "").strip()
    narrative = str(getattr(slot, "narrative_function", "") or "").strip().lower()
    is_bridge = slot_id.startswith("bridge_") or narrative == "chapter_transition"
    if fit not in GAP_FIT_VALUES or is_bridge or not slot_id:
        return None
    from otio_app.services.without_voiceover_enhanced.unified_cut_plan import (
        canonical_coverage_gap_id,
    )

    gap_id = canonical_coverage_gap_id(slot_id) or str(
        getattr(slot, "coverage_gap_id", "") or ""
    ).strip()
    needed = (
        str(getattr(slot, "needed_visual", "") or "").strip()
        or str(getattr(slot, "visual_intent", "") or "").strip()
        or slot_id
    )
    concepts = [
        str(item).strip()
        for item in (getattr(slot, "search_concepts", None) or [])
        if str(item).strip()
    ]
    if not concepts:
        concepts = [needed[:40] or slot_id]
    reason = str(getattr(slot, "asset_fit_reason", "") or "").strip() or (
        "Kein geeignetes lokales Asset"
        if fit == "none"
        else "Lokales Asset nur schwach geeignet — Upgrade-Gap"
    )
    return CoverageGap(
        gap_id=gap_id,
        related_shot_ids=[slot_id],
        needed_visual=needed,
        editorial_purpose=str(getattr(slot, "narrative_function", "") or "orientation"),
        preferred_media_type=str(
            getattr(slot, "preferred_media_type", "") or "video"
        ),
        search_concepts=concepts,
        search_queries=list(concepts),
        must_include=[
            str(item).strip()
            for item in (getattr(slot, "must_include", None) or [])
            if str(item).strip()
        ],
        must_avoid=[
            str(item).strip()
            for item in (getattr(slot, "must_avoid", None) or [])
            if str(item).strip()
        ],
        fact_check_required=bool(getattr(slot, "fact_check_required", False)),
        desired_motion=str(getattr(slot, "desired_motion", "") or ""),
        desired_framing=str(getattr(slot, "desired_framing", "") or ""),
        subject=needed,
        editorial_function=str(
            getattr(slot, "narrative_function", "") or "orientation"
        ),
        priority="high" if fit == "none" else "medium",
        reason=reason,
        target_duration_seconds=getattr(slot, "target_duration_seconds", None),
    )


def _plan_gaps_from_chapter_files(project: Project) -> list[CoverageGap]:
    """weak/none-Slots aus gespeicherten Kapitel-Plänen, ohne Dramaturgie."""
    root = chapters_cut_dir(project)
    if not root.is_dir():
        return []
    found: list[CoverageGap] = []
    seen: set[str] = set()
    for child in sorted(root.iterdir()):
        if not child.is_dir():
            continue
        plan = load_model(child / UNIFIED_CUT_PLAN_FILENAME, UnifiedCutPlanDocument)
        if plan is None or not plan.slots:
            continue
        for slot in plan.slots:
            gap = _coverage_gap_from_plan_slot(slot)
            if gap is None or gap.gap_id in seen:
                continue
            seen.add(gap.gap_id)
            found.append(gap)
    return found


def sync_missing_plan_gaps_into_coverage(project: Project) -> list[str]:
    """Trägt Kapitel-Plan-Gaps nach, die in coverage_gaps.json fehlen.

    Cut-Plan blockiert Timing über den Kapitel-Plan. Der Funnel liest nur
    coverage_gaps.json. Nach Demote/Rebuild ohne Merge-Refresh klaffen die
    Listen auseinander (Timing blockiert, Funnel „offen 0“).
    Die Cut-Plan-Run-ID bleibt unverändert, damit erfüllte Fills gültig bleiben.
    """
    global _PLAN_GAP_SYNC_DEPTH
    if _PLAN_GAP_SYNC_DEPTH:
        return []
    plan_gaps = _plan_gaps_from_chapter_files(project)
    if not plan_gaps:
        return []
    coverage = load_model(coverage_gaps_path(project), CoverageGapsDocument)
    existing = {
        str(gap.gap_id or "").strip()
        for gap in (coverage.gaps if coverage is not None else [])
        if str(gap.gap_id or "").strip()
    }
    missing = [gap for gap in plan_gaps if gap.gap_id not in existing]
    if not missing:
        return []
    _PLAN_GAP_SYNC_DEPTH += 1
    try:
        if coverage is None:
            script_version = ""
            root = chapters_cut_dir(project)
            if root.is_dir():
                for child in sorted(root.iterdir()):
                    plan = load_model(
                        child / UNIFIED_CUT_PLAN_FILENAME, UnifiedCutPlanDocument
                    )
                    if plan is not None and str(plan.script_version or "").strip():
                        script_version = str(plan.script_version)
                        break
            coverage = CoverageGapsDocument(
                script_version=script_version,
                cut_plan_run_id=compute_cut_plan_run_id_from_path(
                    unified_cut_plan_path(project)
                ),
                gaps=missing,
            )
        else:
            coverage = coverage.model_copy(
                update={"gaps": list(coverage.gaps) + missing}
            )
        from otio_app.services.without_voiceover_enhanced.coverage_gap_external_export import (
            persist_coverage_gaps,
        )

        persist_coverage_gaps(project, coverage)
    finally:
        _PLAN_GAP_SYNC_DEPTH -= 1
    return [gap.gap_id for gap in missing]


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


def _accepted_export_ready_gap_ids(
    project: Project,
    *,
    expected_run_id: str,
) -> set[str]:
    """Gaps mit export_ready Accepted-Supplement der aktuellen Run-ID.

    Deckt Funnel-Downloads und manuelle Zuordnung ab — auch wenn der aktuelle
    Funnel-Report die Gap nicht mehr in ``filled_gap_ids`` führt (z. B. Skip /
    neuer Lauf überschreibt den Report).
    """
    accepted = load_model(
        accepted_supplements_path(project), AcceptedSupplementsDocument
    )
    if accepted is None or not accepted.supplements:
        return set()
    ready: set[str] = set()
    for candidate in accepted.supplements:
        gid = str(getattr(candidate, "gap_id", "") or "").strip()
        if not gid:
            continue
        status = str(
            getattr(candidate, "media_validation_status", "") or ""
        ).strip()
        if status != "export_ready":
            continue
        cand_run = str(getattr(candidate, "cut_plan_run_id", "") or "").strip()
        if expected_run_id:
            if not cand_run or cand_run != expected_run_id:
                continue
        ready.add(gid)
    return ready


def _resolve_funnel_media_path(
    project: Project,
    *,
    gap_id: str,
    candidate_id: str,
    local_media_path: str = "",
) -> Path | None:
    """Lokale Datei für Funnel-/Manual-Fill finden (Accepted war evtl. gelöscht)."""
    raw = (local_media_path or "").strip()
    if raw:
        path = Path(raw).expanduser()
        if path.is_file():
            return path
    download_dir = stock_candidate_download_dir(
        project, gap_id=gap_id, candidate_id=candidate_id
    )
    if download_dir.is_dir():
        for child in sorted(download_dir.iterdir()):
            if child.is_file() and not child.name.startswith("."):
                return child
    # Clean-Kopie: oft ``{candidate_id}_*.mp4`` unter project clean/.
    root = Path(project.project_root).expanduser()
    clean_root = root / "clean"
    if clean_root.is_dir():
        matches = sorted(clean_root.rglob(f"{candidate_id}*"))
        for match in matches:
            if match.is_file():
                return match
    return None


def rebind_gap_fills_to_current_run(project: Project) -> dict[str, int]:
    """Übernimmt vorhandene Accepted-/Funnel-Fills auf die aktuelle Run-ID.

    Wenn ein neuer LLM-Cut die Run-ID wechselt, bleiben manuelle/Funnel-
    Downloads mit alter Run-ID sonst „offen“. Gaps mit gleicher Gap-ID und
    ``export_ready`` Accepted werden hier auf den aktuellen Lauf umgebogen —
    ohne erneutes Zuweisen/Downloaden.

    Wenn Accepted bereits geleert wurde (alte Migration), werden Fills aus dem
    Funnel-Report + lokalen Dateien wiederhergestellt.
    """
    coverage = load_model(coverage_gaps_path(project), CoverageGapsDocument)
    if coverage is None or not coverage.gaps:
        return {"accepted": 0, "funnel": 0, "restored": 0}

    run_id = str(getattr(coverage, "cut_plan_run_id", "") or "").strip()
    if not run_id:
        run_id = compute_cut_plan_run_id_from_path(unified_cut_plan_path(project))
    if not run_id:
        return {"accepted": 0, "funnel": 0, "restored": 0}

    current_gaps = {
        str(gap.gap_id or "").strip()
        for gap in coverage.gaps
        if str(gap.gap_id or "").strip()
    }
    gap_by_id = {
        str(gap.gap_id or "").strip(): gap
        for gap in coverage.gaps
        if str(gap.gap_id or "").strip()
    }

    accepted_n = 0
    restored_n = 0
    accepted_ready_ids: set[str] = set()
    accepted = load_model(
        accepted_supplements_path(project), AcceptedSupplementsDocument
    )
    supplements: list[StockCandidate] = list(accepted.supplements) if accepted else []
    script_version = (
        accepted.script_version
        if accepted is not None
        else str(coverage.script_version or "")
    )
    schema_version = (
        accepted.schema_version
        if accepted is not None
        else "enhanced-accepted-supplements-v1"
    )

    updated: list[StockCandidate] = []
    changed = False
    for candidate in supplements:
        gid = str(getattr(candidate, "gap_id", "") or "").strip()
        status = str(
            getattr(candidate, "media_validation_status", "") or ""
        ).strip()
        cand_run = str(getattr(candidate, "cut_plan_run_id", "") or "").strip()
        if (
            gid in current_gaps
            and status == "export_ready"
            and cand_run != run_id
        ):
            candidate = candidate.model_copy(update={"cut_plan_run_id": run_id})
            accepted_n += 1
            changed = True
        if (
            gid in current_gaps
            and str(getattr(candidate, "media_validation_status", "") or "").strip()
            == "export_ready"
            and str(getattr(candidate, "cut_plan_run_id", "") or "").strip()
            == run_id
        ):
            accepted_ready_ids.add(gid)
        updated.append(candidate)

    # Recovery: Funnel meldet filled, Accepted fehlt (alte Purge-Migration).
    funnel = load_model(
        supplement_funnel_report_path(project), SupplementFunnelReport
    )
    if funnel is not None:
        existing_ids = {
            str(c.candidate_id or "").strip() for c in updated if c.candidate_id
        }
        for gap_rep in funnel.gaps or []:
            gid = str(gap_rep.gap_id or "").strip()
            if not gid or gid not in current_gaps or gid in accepted_ready_ids:
                continue
            if not (gap_rep.filled or gap_rep.export_ready_candidate_id):
                continue
            candidate_id = str(gap_rep.export_ready_candidate_id or "").strip()
            if not candidate_id:
                ready_rec = next(
                    (
                        c
                        for c in (gap_rep.candidates or [])
                        if str(c.funnel_status or "") == "export_ready"
                    ),
                    None,
                )
                if ready_rec is not None:
                    candidate_id = str(ready_rec.candidate_id or "").strip()
            if not candidate_id or candidate_id in existing_ids:
                continue
            record = next(
                (
                    c
                    for c in (gap_rep.candidates or [])
                    if str(c.candidate_id or "").strip() == candidate_id
                ),
                None,
            )
            media = _resolve_funnel_media_path(
                project,
                gap_id=gid,
                candidate_id=candidate_id,
                local_media_path=str(
                    getattr(record, "local_media_path", "") or ""
                ),
            )
            if media is None:
                continue
            # Clean-Kopie bevorzugen, falls vorhanden.
            from otio_app.services.without_voiceover_enhanced.local_media_service import (
                find_clean_media_for_candidate,
            )

            clean = find_clean_media_for_candidate(
                project, candidate_id=candidate_id
            )
            if clean is not None and clean.is_file():
                media = clean
            gap = gap_by_id.get(gid)
            description = (
                (gap.needed_visual if gap else "")
                or (gap.subject if gap else "")
                or gid
            )
            provider = str(getattr(record, "provider", "") or "manual")
            media_type = "video"
            suffix = media.suffix.lower()
            if suffix in {".jpg", ".jpeg", ".png", ".webp", ".gif"}:
                media_type = "photo"
            restored = StockCandidate(
                candidate_id=candidate_id,
                provider=provider,
                provider_asset_id=candidate_id,
                title=str(description)[:120],
                media_type=media_type,
                creator=str(getattr(record, "creator", "") or provider),
                source_page=str(getattr(record, "source_page", "") or ""),
                license=str(getattr(record, "license_name", "") or ""),
                attribution=str(getattr(record, "attribution", "") or ""),
                selected=True,
                gap_id=gid,
                local_media_path=str(media),
                media_validation_status="export_ready",
                funnel_managed=True,
                cut_plan_run_id=run_id,
                assign_status="restored",
            )
            updated.append(restored)
            existing_ids.add(candidate_id)
            accepted_ready_ids.add(gid)
            restored_n += 1
            changed = True

    if changed:
        write_json(
            accepted_supplements_path(project),
            AcceptedSupplementsDocument(
                schema_version=schema_version,
                script_version=script_version,
                supplements=updated,
            ),
        )
        from otio_app.services.without_voiceover_enhanced.coverage_gap_external_export import (
            refresh_coverage_gaps_external_export,
        )

        refresh_coverage_gaps_external_export(project)

    funnel_n = 0
    # Funnel nur anpassen, wenn Accepted-Fills den neuen Lauf verankern —
    # sonst bliebe ein reiner Funnel-Report fälschlich „erfüllt“.
    if funnel is not None and accepted_ready_ids:
        funnel_run = str(getattr(funnel, "cut_plan_run_id", "") or "").strip()
        valid_filled = sorted(accepted_ready_ids)
        needs_funnel_write = funnel_run != run_id or set(
            funnel.filled_gap_ids or []
        ) != set(valid_filled)
        if needs_funnel_write:
            open_ordered = [
                str(g.gap_id).strip()
                for g in coverage.gaps
                if str(g.gap_id or "").strip()
                and str(g.gap_id).strip() not in accepted_ready_ids
            ]
            updated_gaps = []
            for gap_rep in funnel.gaps or []:
                gid = str(gap_rep.gap_id or "").strip()
                if gid in accepted_ready_ids:
                    updated_gaps.append(
                        gap_rep.model_copy(
                            update={
                                "filled": True,
                            }
                        )
                    )
                elif gid in current_gaps:
                    updated_gaps.append(
                        gap_rep.model_copy(
                            update={
                                "filled": False,
                                "export_ready_candidate_id": None,
                            }
                        )
                    )
                else:
                    updated_gaps.append(gap_rep)
            write_json(
                supplement_funnel_report_path(project),
                funnel.model_copy(
                    update={
                        "cut_plan_run_id": run_id,
                        "filled_gap_ids": valid_filled,
                        "open_gap_ids": open_ordered,
                        "gaps": updated_gaps,
                    }
                ),
            )
            funnel_n = len(valid_filled)

    return {"accepted": accepted_n, "funnel": funnel_n, "restored": restored_n}


def summarize_gap_status(project: Project) -> GapStatusSummary:
    """Aktueller Gap-Status relativ zum Unified-Cut-Plan-Lauf.

    Regeln:
    - Gap-Liste kommt aus coverage_gaps.json (aktueller Plan), ergänzt um
      weak/none-Slots aus den Kapitel-Plänen, falls die JSON sie nicht kennt.
    - Funnel export_ready / Download (gleiche Run-ID) schließt weak und none.
    - Accepted export_ready (gleiche Run-ID) schließt ebenfalls — auch ohne
      aktuellen Funnel-Eintrag.
    - Merge (merged | kept_local_weak) schließt weiterhin.
    - ``user_confirmed_weak`` schließt nur Weak-Upgrade-Gaps (priority=medium).
      Stale Flags auf high/none werden zurückgesetzt.
    - Stale Funnel/Merge (andere/fehlende Run-ID) zählen nicht — Accepted-
      Fills mit gleicher Gap-ID werden zuvor auf den aktuellen Lauf rebound.
    """
    added_from_plans = sync_missing_plan_gaps_into_coverage(project)
    coverage = load_model(coverage_gaps_path(project), CoverageGapsDocument)
    if coverage is None or not coverage.gaps:
        return GapStatusSummary(message="Keine Coverage Gaps.")

    run_id = str(getattr(coverage, "cut_plan_run_id", "") or "").strip()
    if not run_id:
        run_id = compute_cut_plan_run_id_from_path(unified_cut_plan_path(project))

    cleared_stale_weak = sanitize_stale_user_confirmed_weak(project)
    if cleared_stale_weak:
        coverage = load_model(coverage_gaps_path(project), CoverageGapsDocument)
        if coverage is None or not coverage.gaps:
            return GapStatusSummary(message="Keine Coverage Gaps.")

    rebound = rebind_gap_fills_to_current_run(project)

    funnel = load_model(supplement_funnel_report_path(project), SupplementFunnelReport)
    merge = load_model(gap_merge_report_path(project), GapMergeReport)
    funnel_ready, funnel_stale = _funnel_export_ready_ids(
        funnel, expected_run_id=run_id
    )
    merge_closed, merge_stale = _merge_closed_ids(merge, expected_run_id=run_id)
    accepted_ready = _accepted_export_ready_gap_ids(project, expected_run_id=run_id)

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
        weak_confirmed = bool(getattr(gap, "user_confirmed_weak", False)) and (
            is_weak_upgrade_gap(gap)
        )
        if (
            gid in merge_closed
            or gid in funnel_ready
            or gid in accepted_ready
            or weak_confirmed
        ):
            filled_ids.append(gid)
        else:
            open_ids.append(gid)

    notes: list[str] = []
    rebound_accepted = int(rebound.get("accepted") or 0)
    restored = int(rebound.get("restored") or 0)
    if rebound_accepted:
        notes.append(
            f"{rebound_accepted} Accepted-Fill(s) auf aktuellen Cut-Plan-Lauf übernommen"
        )
    if restored:
        notes.append(
            f"{restored} Fill(s) aus Funnel/Dateien wiederhergestellt"
        )
    if cleared_stale_weak:
        notes.append(
            f"{cleared_stale_weak} veraltete Weak-Bestätigung(en) zurückgesetzt"
        )
    if funnel_stale:
        notes.append("Funnel-Report gehört zu einem älteren Cut-Plan-Lauf")
    if merge_stale:
        notes.append("Gap-Merge-Report gehört zu einem älteren Cut-Plan-Lauf")
    if added_from_plans:
        notes.append(
            f"{len(added_from_plans)} Gap(s) aus Kapitel-Plänen nachgetragen "
            "(Cut-Plan und Funnel waren nicht synchron)"
        )
    return GapStatusSummary(
        total=len(open_ids) + len(filled_ids),
        open_gap_ids=open_ids,
        filled_gap_ids=filled_ids,
        cut_plan_run_id=run_id,
        funnel_stale=funnel_stale,
        merge_stale=merge_stale,
        message="; ".join(notes),
    )
