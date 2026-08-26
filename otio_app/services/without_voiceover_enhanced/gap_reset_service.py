"""Offene Coverage Gaps und ihren Funnel-Zustand vor einem neuen LLM-Cut räumen.

Ein neuer LLM-Cut schreibt ``coverage_gaps.json`` komplett neu — die Gap-Liste
des Vorlaufs verschwindet also von selbst. Zwei Dinge überleben aber bewusst:

- ``rebind_gap_fills_to_current_run`` biegt vorhandene ``export_ready``-Fills auf
  die neue Run-ID um, sobald die Gap-ID wieder vorkommt.
- ``search_results.json`` und ``supplement_funnel_report.json`` werden nicht
  aufgeräumt.

Weil Gap-IDs deterministisch aus den Slot-IDs entstehen (``gap_{slot_id}``),
kommen sie über Läufe hinweg wieder vor. Ein neuer Gap an Slot 3 kann so den
Kandidaten des alten Slot 3 erben, obwohl es redaktionell um etwas anderes geht.
Vor einem bewusst frischen Cut — etwa nachdem neues Material ins Inventar kam —
ist das unerwünscht.

Dieses Modul räumt deshalb gezielt auf. Zwei Regeln:

1. **Dateien bleiben liegen.** Gelöscht wird nur Zustand, nie beschafftes
   Material. Downloads und Clean-Fassungen bleiben, ebenso das Inventar.
2. **Bezahlte Assets bleiben im Ledger.** ``export_ready``-Einträge in
   ``accepted_supplements.json`` überleben; optional wird nur ihre Gap-Bindung
   gelöst, damit sie einen neuen Gap nicht automatisch schließen.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable

from otio_app.models import Project
from otio_app.services.without_voiceover_enhanced.io_utils import load_model, write_json
from otio_app.services.without_voiceover_enhanced.local_media_service import (
    STATUS_EXPORT_READY,
)
from otio_app.services.without_voiceover_enhanced.models import (
    AcceptedSupplementsDocument,
    CoverageGapsDocument,
    StockCandidate,
    StockSearchResultsDocument,
    SupplementFunnelReport,
)
from otio_app.services.without_voiceover_enhanced.paths import (
    accepted_supplements_path,
    coverage_gaps_path,
    stock_search_results_path,
    supplement_funnel_report_path,
)

__all__ = [
    "GapResetPreview",
    "GapResetReport",
    "preview_open_gap_reset",
    "reset_open_coverage_gaps",
]


@dataclass
class GapResetPreview:
    open_gap_ids: list[str] = field(default_factory=list)
    filled_gap_ids: list[str] = field(default_factory=list)
    search_candidates: int = 0
    funnel_gap_reports: int = 0
    #: Accepted-Einträge offener Gaps ohne fertiges Medium — reiner Zustand.
    accepted_pending: int = 0
    #: Accepted-Einträge mit fertigem Medium — bleiben in jedem Fall erhalten.
    accepted_export_ready: int = 0

    @property
    def open_count(self) -> int:
        return len(self.open_gap_ids)

    @property
    def has_work(self) -> bool:
        return bool(
            self.open_gap_ids
            or self.search_candidates
            or self.funnel_gap_reports
            or self.accepted_pending
        )


@dataclass
class GapResetReport:
    removed_gap_ids: list[str] = field(default_factory=list)
    removed_search_candidates: int = 0
    removed_funnel_gap_reports: int = 0
    removed_accepted_pending: int = 0
    unbound_accepted_export_ready: int = 0
    kept_gap_ids: list[str] = field(default_factory=list)

    @property
    def removed_count(self) -> int:
        return len(self.removed_gap_ids)


def _open_and_filled(project: Project) -> tuple[set[str], set[str]]:
    from otio_app.services.without_voiceover_enhanced.gap_status_service import (
        summarize_gap_status,
    )

    summary = summarize_gap_status(project)
    return set(summary.open_gap_ids), set(summary.filled_gap_ids)


def preview_open_gap_reset(project: Project) -> GapResetPreview:
    """Was ein Reset entfernen würde — ohne etwas zu ändern."""
    coverage = load_model(coverage_gaps_path(project), CoverageGapsDocument)
    if coverage is None or not coverage.gaps:
        return GapResetPreview()

    open_ids, filled_ids = _open_and_filled(project)
    preview = GapResetPreview(
        open_gap_ids=sorted(open_ids),
        filled_gap_ids=sorted(filled_ids),
    )

    search = load_model(stock_search_results_path(project), StockSearchResultsDocument)
    if search is not None:
        preview.search_candidates = sum(
            1
            for candidate in search.candidates or []
            if str(candidate.gap_id or "").strip() in open_ids
        )

    funnel = load_model(supplement_funnel_report_path(project), SupplementFunnelReport)
    if funnel is not None:
        preview.funnel_gap_reports = sum(
            1
            for gap_report in funnel.gaps or []
            if str(gap_report.gap_id or "").strip() in open_ids
        )

    accepted = load_model(
        accepted_supplements_path(project), AcceptedSupplementsDocument
    )
    for candidate in getattr(accepted, "supplements", None) or []:
        ready = (
            str(candidate.media_validation_status or "").strip() == STATUS_EXPORT_READY
        )
        if ready:
            preview.accepted_export_ready += 1
        elif str(candidate.gap_id or "").strip() in open_ids:
            preview.accepted_pending += 1
    return preview


def reset_open_coverage_gaps(
    project: Project,
    *,
    unbind_filled: bool = False,
    gap_ids: Iterable[str] | None = None,
) -> GapResetReport:
    """Entfernt offene Gaps und ihren Such-/Funnel-Zustand.

    Args:
        unbind_filled: Löst zusätzlich die Gap-Bindung fertiger Accepted-Einträge
            (``gap_id`` und ``cut_plan_run_id`` werden geleert). Asset, Pfad und
            Lizenz bleiben erhalten — das Material ist über das geteilte Inventar
            weiterhin zuweisbar. Sinnvoll, wenn der neue Cut jede Zuordnung neu
            verdienen soll.
        gap_ids: Beschränkt den Reset auf diese Gap-IDs. Für den Kapitel-Reset
            beim neuen LLM-Cut: nur die Gaps des betroffenen Kapitels, damit
            fertige Kapitel unberührt bleiben.
    """
    from otio_app.services.without_voiceover_enhanced.coverage_gap_external_export import (
        persist_coverage_gaps,
    )

    report = GapResetReport()
    coverage = load_model(coverage_gaps_path(project), CoverageGapsDocument)
    if coverage is None or not coverage.gaps:
        return report

    open_ids, _filled_ids = _open_and_filled(project)
    scope: set[str] = set()
    if gap_ids is not None:
        scope = {str(gid).strip() for gid in gap_ids if str(gid).strip()}
        open_ids = open_ids & scope
    if not open_ids and not unbind_filled:
        report.kept_gap_ids = [
            str(gap.gap_id or "") for gap in coverage.gaps if gap.gap_id
        ]
        return report

    kept_gaps = []
    for gap in coverage.gaps:
        gap_id = str(gap.gap_id or "").strip()
        if gap_id and gap_id in open_ids:
            report.removed_gap_ids.append(gap_id)
            continue
        kept_gaps.append(gap)
        if gap_id:
            report.kept_gap_ids.append(gap_id)
    persist_coverage_gaps(project, coverage.model_copy(update={"gaps": kept_gaps}))

    search = load_model(stock_search_results_path(project), StockSearchResultsDocument)
    if search is not None:
        kept_candidates = [
            candidate
            for candidate in search.candidates or []
            if str(candidate.gap_id or "").strip() not in open_ids
        ]
        report.removed_search_candidates = len(search.candidates or []) - len(
            kept_candidates
        )
        if report.removed_search_candidates:
            write_json(
                stock_search_results_path(project),
                search.model_copy(update={"candidates": kept_candidates}),
            )

    funnel = load_model(supplement_funnel_report_path(project), SupplementFunnelReport)
    if funnel is not None:
        kept_reports = [
            gap_report
            for gap_report in funnel.gaps or []
            if str(gap_report.gap_id or "").strip() not in open_ids
        ]
        report.removed_funnel_gap_reports = len(funnel.gaps or []) - len(kept_reports)
        if report.removed_funnel_gap_reports:
            write_json(
                supplement_funnel_report_path(project),
                funnel.model_copy(
                    update={
                        "gaps": kept_reports,
                        "requested_gap_ids": [
                            gid
                            for gid in funnel.requested_gap_ids or []
                            if gid not in open_ids
                        ],
                        "skipped_gap_ids": [
                            gid
                            for gid in funnel.skipped_gap_ids or []
                            if gid not in open_ids
                        ],
                        "open_gap_ids": [
                            gid
                            for gid in funnel.open_gap_ids or []
                            if gid not in open_ids
                        ],
                    }
                ),
            )

    accepted = load_model(
        accepted_supplements_path(project), AcceptedSupplementsDocument
    )
    if accepted is not None:
        kept_supplements: list[StockCandidate] = []
        unbound_gap_ids: list[str] = []
        for candidate in accepted.supplements or []:
            ready = (
                str(candidate.media_validation_status or "").strip()
                == STATUS_EXPORT_READY
            )
            gap_id = str(candidate.gap_id or "").strip()
            if not ready and gap_id in open_ids:
                # Kein fertiges Medium dahinter — reiner Zustand des Vorlaufs.
                report.removed_accepted_pending += 1
                continue
            in_scope = gap_ids is None or gap_id in scope
            if ready and unbind_filled and in_scope and (gap_id or candidate.cut_plan_run_id):
                if gap_id:
                    unbound_gap_ids.append(gap_id)
                candidate = candidate.model_copy(
                    update={"gap_id": "", "cut_plan_run_id": ""}
                )
                report.unbound_accepted_export_ready += 1
            kept_supplements.append(candidate)
        if report.removed_accepted_pending or report.unbound_accepted_export_ready:
            write_json(
                accepted_supplements_path(project),
                accepted.model_copy(update={"supplements": kept_supplements}),
            )
        if unbound_gap_ids:
            _unfill_funnel_gaps(project, unbound_gap_ids)

    from otio_app.services.without_voiceover_enhanced.coverage_gap_external_export import (
        refresh_coverage_gaps_external_export,
    )

    refresh_coverage_gaps_external_export(project)
    return report


def _unfill_funnel_gaps(project: Project, gap_ids: list[str]) -> None:
    """Funnel-Report nach Unbind wieder auf offen stellen."""
    unbound = {str(gid).strip() for gid in gap_ids if str(gid).strip()}
    if not unbound:
        return
    funnel = load_model(supplement_funnel_report_path(project), SupplementFunnelReport)
    if funnel is None:
        return
    updated_gaps = []
    for gap_report in funnel.gaps or []:
        gid = str(gap_report.gap_id or "").strip()
        if gid in unbound:
            updated_gaps.append(
                gap_report.model_copy(
                    update={"filled": False, "export_ready_candidate_id": None}
                )
            )
        else:
            updated_gaps.append(gap_report)
    filled_ids = [
        gid for gid in (funnel.filled_gap_ids or []) if str(gid).strip() not in unbound
    ]
    open_ids = [
        gid
        for gid in list(funnel.open_gap_ids or []) + sorted(unbound)
        if str(gid).strip() and str(gid).strip() not in filled_ids
    ]
    seen: set[str] = set()
    open_unique: list[str] = []
    for gid in open_ids:
        if gid in seen:
            continue
        seen.add(gid)
        open_unique.append(gid)
    write_json(
        supplement_funnel_report_path(project),
        funnel.model_copy(
            update={
                "gaps": updated_gaps,
                "filled_gap_ids": filled_ids,
                "open_gap_ids": open_unique,
            }
        ),
    )
