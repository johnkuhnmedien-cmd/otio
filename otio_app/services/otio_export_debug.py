"""Gezieltes Debugging für OTIO-Export-/Merge-Blocker.

Strukturiert die String-Meldungen aus ``merge_confirmed_edit_plans`` nach
Kategorie und Ordner, damit Cut-Plan-Exports nicht nur eine flache Fehlerliste
zeigen. Schreibt optional ein JSON-Artefakt unter ``_otio/exports/``.
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from otio_app.project_layout import get_exports_dir
from otio_app.services.otio_exporter import MergedEditPlanResult

__all__ = [
    "OtioExportMergeDebugReport",
    "categorize_merge_warning",
    "build_otio_export_merge_debug_report",
    "save_otio_export_merge_debug_report",
    "render_otio_export_merge_debug_lines",
]

_CATEGORY_MARKERS: tuple[tuple[str, tuple[str, ...]], ...] = (
    (
        "shot_max_duration",
        (
            "final_duration_sec",
            "länger als",
            "shot_max",
            "Maximaldauer",
            "Dauer >",
        ),
    ),
    (
        "section_outro",
        (
            "section_outro_sec",
            "generic_outro_visual",
            "Outro-Elemente",
        ),
    ),
    (
        "opening_title",
        (
            "opening_title",
            "Ordner-Titel-Regel",
        ),
    ),
    (
        "voiceover_duration_source",
        (
            "duration_source muss ffprobe",
            "duration_source",
        ),
    ),
    (
        "voiceover_audio_offset",
        (
            "audio_offset_sec",
            "timeline_start_sec",
            "timeline_end_sec",
            "audio_offset + duration",
        ),
    ),
    (
        "video_head_trim",
        (
            "source_in_sec muss",
            "video_head_trim",
        ),
    ),
    (
        "empty_timeline",
        (
            "kein timeline_items",
            "Keine Timeline-Items",
        ),
    ),
    (
        "inventory_stale",
        (
            "inventory_hash",
            "Inventory geändert",
        ),
    ),
    (
        "blocked_candidate",
        (
            "Schnittplan BLOCKED",
        ),
    ),
    (
        "media_path",
        (
            "nicht lesbar",
            "Medienpfad",
            "clean media",
        ),
    ),
)


@dataclass
class OtioExportMergeDebugIssue:
    folder: str
    category: str
    message: str
    raw: str


@dataclass
class OtioExportMergeDebugReport:
    created_at: str
    ready: bool
    validation_status: str
    included_folders: list[str] = field(default_factory=list)
    skipped_folders: list[str] = field(default_factory=list)
    cut_plan_relaxed_folders: list[str] = field(default_factory=list)
    issue_count: int = 0
    issues_by_category: dict[str, list[OtioExportMergeDebugIssue]] = field(default_factory=dict)
    issues_by_folder: dict[str, list[OtioExportMergeDebugIssue]] = field(default_factory=dict)
    analysis_notes: list[str] = field(default_factory=list)
    raw_warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        payload = asdict(self)
        return payload


def categorize_merge_warning(message: str) -> tuple[str, str, str]:
    """Zerlegt eine Merge-Warnung in (folder, category, body)."""
    text = message.strip()
    folder = "(global)"
    body = text
    working = text
    if working.lower().startswith("validierung:"):
        working = working.split(":", 1)[1].strip()
    match = re.match(r"^([^:]+):\s+(.*)$", working, re.DOTALL)
    if match:
        maybe_folder, rest = match.group(1).strip(), match.group(2).strip()
        lowered = maybe_folder.lower()
        if lowered in {"global"}:
            folder = "(global)"
            body = rest
        elif lowered not in {"regel-hinweis (export trotzdem möglich)"} and maybe_folder:
            folder = maybe_folder
            body = rest

    haystack = body.lower()
    for category, markers in _CATEGORY_MARKERS:
        if any(marker.lower() in haystack for marker in markers):
            return folder, category, body
    return folder, "other", body


def build_otio_export_merge_debug_report(merged: MergedEditPlanResult) -> OtioExportMergeDebugReport:
    """Baut einen strukturierten Debug-Report aus dem Merge-Ergebnis."""
    issues_by_category: dict[str, list[OtioExportMergeDebugIssue]] = {}
    issues_by_folder: dict[str, list[OtioExportMergeDebugIssue]] = {}
    issues: list[OtioExportMergeDebugIssue] = []

    for warning in merged.warnings:
        if warning.startswith("Regel-Hinweis (Export trotzdem möglich):"):
            continue
        folder, category, body = categorize_merge_warning(warning)
        issue = OtioExportMergeDebugIssue(
            folder=folder,
            category=category,
            message=body,
            raw=warning,
        )
        issues.append(issue)
        issues_by_category.setdefault(category, []).append(issue)
        issues_by_folder.setdefault(folder, []).append(issue)

    notes: list[str] = []
    relaxed = list(getattr(merged, "cut_plan_relaxed_folders", []) or [])
    if relaxed:
        notes.append(
            "Cut-Plan-Validierungsmodus aktiv für: "
            + ", ".join(relaxed)
            + " (relaxierte Settings + ohne Opening-Title-Zwang; "
            "duration_source=bridge_audio_plan wird nicht als ffprobe-Fehler gewertet)."
        )
    elif any(
        vo.duration_source == "bridge_audio_plan"
        for vo in getattr(merged, "voiceovers", []) or []
    ):
        notes.append(
            "Voiceover mit duration_source=bridge_audio_plan erkannt, aber kein "
            "Cut-Plan-Relaxed-Modus — Merge sollte diese Folder relaxiert prüfen."
        )

    production_like = {
        "shot_max_duration",
        "section_outro",
        "opening_title",
        "voiceover_duration_source",
        "voiceover_audio_offset",
        "video_head_trim",
    }
    if production_like.intersection(issues_by_category) and not relaxed:
        notes.append(
            "Typische With-Voice-over-Regelverletzungen (Shot-Max 8s, Outro 5s, "
            "audio_offset 1s, head_trim 0.5, ffprobe, Opening Title). Bei Cut-Plan-"
            "promoteten Plänen gehört der Merge auf den relaxierten Validator-Pfad."
        )
    if "opening_title" in issues_by_category and relaxed:
        notes.append(
            "Opening-Title-Fehler trotz Cut-Plan-Modus — unerwartet; Ordner-Titel-"
            "Regel sollte im Cut-Plan-Merge deaktiviert sein."
        )

    return OtioExportMergeDebugReport(
        created_at=datetime.now(timezone.utc).isoformat(),
        ready=bool(merged.ready),
        validation_status=str(merged.validation_status),
        included_folders=list(merged.included_folders),
        skipped_folders=list(merged.skipped_folders),
        cut_plan_relaxed_folders=relaxed,
        issue_count=len(issues),
        issues_by_category=issues_by_category,
        issues_by_folder=issues_by_folder,
        analysis_notes=notes,
        raw_warnings=list(merged.warnings),
    )


def save_otio_export_merge_debug_report(
    work_dir: Path, report: OtioExportMergeDebugReport
) -> Path:
    """Schreibt den Debug-Report nach ``_otio/exports/otio_export_merge_debug.json``."""
    exports = get_exports_dir(work_dir)
    exports.mkdir(parents=True, exist_ok=True)
    path = exports / "otio_export_merge_debug.json"
    path.write_text(json.dumps(report.to_dict(), indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return path


def render_otio_export_merge_debug_lines(report: OtioExportMergeDebugReport) -> list[str]:
    """Kurze Textzeilen für Streamlit-Expander / Logs."""
    lines: list[str] = [
        f"Status: {report.validation_status} · ready={report.ready} · Issues={report.issue_count}",
    ]
    if report.cut_plan_relaxed_folders:
        lines.append("Cut-Plan-Relaxed: " + ", ".join(report.cut_plan_relaxed_folders))
    for note in report.analysis_notes:
        lines.append(f"Analyse: {note}")
    for category, items in sorted(report.issues_by_category.items()):
        lines.append(f"[{category}] {len(items)}×")
        for issue in items[:8]:
            lines.append(f"  • {issue.folder}: {issue.message}")
        if len(items) > 8:
            lines.append(f"  … +{len(items) - 8} weitere")
    return lines
