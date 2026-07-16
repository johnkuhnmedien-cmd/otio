"""Application-Service: Medienauswahl aus Snapshot ableiten und bestätigen."""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

from otio_app.discovery_v2.application.inventory_service import (
    InventoryServiceError,
    require_discovery_project,
)
from otio_app.discovery_v2.domain.inventory import (
    InventoryFileEntry,
    InventorySnapshot,
    MediaKind,
    ScanStatus,
)
from otio_app.discovery_v2.domain.selection import (
    EXCLUSION_REASON_USER,
    InventorySelection,
    SelectionDraft,
    SelectionStatus,
)
from otio_app.discovery_v2.persistence.inventory_artifact_store import (
    InventoryArtifactError,
)
from otio_app.discovery_v2.persistence.selection_artifact_store import (
    load_latest_selection,
    save_selection,
)
from otio_app.models import Project


SELECTABLE_KINDS = frozenset({MediaKind.VIDEO, MediaKind.IMAGE, MediaKind.AUDIO})


def is_selectable_media(entry: InventoryFileEntry) -> bool:
    return (
        entry.scan_status == ScanStatus.FOUND
        and entry.media_kind in SELECTABLE_KINDS
    )


def found_files(snapshot: InventorySnapshot) -> list[InventoryFileEntry]:
    return [f for f in snapshot.files if f.scan_status == ScanStatus.FOUND]


def selectable_files(snapshot: InventorySnapshot) -> list[InventoryFileEntry]:
    return [f for f in found_files(snapshot) if is_selectable_media(f)]


def other_files(snapshot: InventorySnapshot) -> list[InventoryFileEntry]:
    return [
        f
        for f in found_files(snapshot)
        if f.media_kind == MediaKind.OTHER
    ]


def build_default_draft(snapshot: InventorySnapshot) -> SelectionDraft:
    """Standard: alle Gruppen aktiv; video/image/audio gewählt; other nicht."""
    groups: list[str] = []
    for group in snapshot.source_groups:
        has_media = any(
            f.source_group == group.source_group and is_selectable_media(f)
            for f in snapshot.files
        )
        if has_media or any(
            f.source_group == group.source_group and f.scan_status == ScanStatus.FOUND
            for f in snapshot.files
        ):
            groups.append(group.source_group)
    # Auch Gruppen ohne Selectables behalten (Anzeige), aber ohne Pfade.
    # Alle Quellgruppen mit gefundenen Dateien übernehmen.
    if not groups:
        groups = [g.source_group for g in snapshot.source_groups]
    return SelectionDraft(
        scan_id=snapshot.scan_id,
        selected_source_groups=list(groups),
        excluded_relative_paths=[],
    )


def resolve_selected_paths(
    snapshot: InventorySnapshot,
    draft: SelectionDraft,
) -> list[str]:
    """Berechnet ausgewählte unterstützte Medienpfade aus Draft + Snapshot."""
    if draft.scan_id != snapshot.scan_id:
        raise InventoryServiceError(
            "Die Auswahl gehört nicht zum aktuellen Bestands-Snapshot."
        )
    active_groups = set(draft.selected_source_groups)
    excluded = set(draft.excluded_relative_paths)
    selected: list[str] = []
    for entry in selectable_files(snapshot):
        if entry.source_group not in active_groups:
            continue
        if entry.relative_path in excluded:
            continue
        selected.append(entry.relative_path)
    return selected


def resolve_excluded_paths(
    snapshot: InventorySnapshot,
    draft: SelectionDraft,
) -> tuple[list[str], dict[str, str]]:
    """Unterstützte Medien, die nicht ausgewählt sind (Gruppe aus / einzeln)."""
    active_groups = set(draft.selected_source_groups)
    user_excluded = set(draft.excluded_relative_paths)
    excluded: list[str] = []
    reasons: dict[str, str] = {}
    for entry in selectable_files(snapshot):
        if entry.source_group not in active_groups:
            excluded.append(entry.relative_path)
            reasons[entry.relative_path] = EXCLUSION_REASON_USER
        elif entry.relative_path in user_excluded:
            excluded.append(entry.relative_path)
            reasons[entry.relative_path] = EXCLUSION_REASON_USER
    return excluded, reasons


def summarize_selection(
    snapshot: InventorySnapshot,
    draft: SelectionDraft,
) -> dict[str, int | list[str]]:
    selected_paths = resolve_selected_paths(snapshot, draft)
    by_path = {f.relative_path: f for f in selectable_files(snapshot)}
    video = image = audio = 0
    for path in selected_paths:
        kind = by_path[path].media_kind
        if kind == MediaKind.VIDEO:
            video += 1
        elif kind == MediaKind.IMAGE:
            image += 1
        elif kind == MediaKind.AUDIO:
            audio += 1
    excluded, _reasons = resolve_excluded_paths(snapshot, draft)
    return {
        "selected_source_groups": list(draft.selected_source_groups),
        "selected_relative_paths": selected_paths,
        "excluded_relative_paths": excluded,
        "selected_video_count": video,
        "selected_image_count": image,
        "selected_audio_count": audio,
        "selected_media_count": video + image + audio,
        "other_file_count": len(other_files(snapshot)),
    }


def effective_selection_status(
    selection: InventorySelection,
    current_scan_id: str | None,
) -> SelectionStatus:
    """Confirmed → stale, wenn ein neuer Snapshot aktiv ist."""
    if (
        selection.status == SelectionStatus.CONFIRMED
        and current_scan_id is not None
        and selection.scan_id != current_scan_id
    ):
        return SelectionStatus.STALE
    return selection.status


def confirm_selection(
    project: Project,
    snapshot: InventorySnapshot,
    draft: SelectionDraft,
    *,
    acknowledged: bool,
) -> InventorySelection:
    """Bestätigt die Auswahl und speichert ein versioniertes Artefakt."""
    project = require_discovery_project(project)
    if snapshot is None:
        raise InventoryServiceError("Kein Bestands-Snapshot vorhanden.")
    if draft.scan_id != snapshot.scan_id:
        raise InventoryServiceError(
            "Die Auswahl gehört nicht zum aktuellen Bestands-Snapshot."
        )
    if not acknowledged:
        raise InventoryServiceError(
            "Bitte bestätigen Sie, dass Sie Bestandsaufnahme und Auswahl geprüft haben."
        )

    summary = summarize_selection(snapshot, draft)
    if int(summary["selected_media_count"]) <= 0:
        raise InventoryServiceError(
            "Es müssen mindestens eine Mediendatei ausgewählt sein."
        )

    # Alle ausgewählten Pfade müssen im Snapshot existieren.
    snapshot_paths = {f.relative_path for f in selectable_files(snapshot)}
    for path in summary["selected_relative_paths"]:
        if path not in snapshot_paths:
            raise InventoryServiceError(
                f"Ausgewählter Pfad fehlt im Snapshot: {path}"
            )

    excluded, reasons = resolve_excluded_paths(snapshot, draft)
    now = datetime.now(timezone.utc)
    selection = InventorySelection(
        selection_id=str(uuid4()),
        project_id=project.id,
        scan_id=snapshot.scan_id,
        source_snapshot_relative_path=f"inventory/snapshots/{snapshot.scan_id}.json",
        created_at=now,
        confirmed_at=now,
        status=SelectionStatus.CONFIRMED,
        selected_source_groups=list(summary["selected_source_groups"]),
        selected_relative_paths=list(summary["selected_relative_paths"]),
        excluded_relative_paths=excluded,
        exclusion_reasons=reasons,
        selected_video_count=int(summary["selected_video_count"]),
        selected_image_count=int(summary["selected_image_count"]),
        selected_audio_count=int(summary["selected_audio_count"]),
        selected_media_count=int(summary["selected_media_count"]),
        other_file_count=int(summary["other_file_count"]),
    )
    # Kein Kapitelmodell — selected_source_groups sind nur Quellgruppen-IDs.
    try:
        save_selection(project.project_root_path, selection)
    except InventoryArtifactError as exc:
        raise InventoryServiceError(str(exc)) from exc
    return selection


def get_latest_confirmed_selection(
    project: Project,
    current_scan_id: str | None = None,
) -> tuple[InventorySelection | None, SelectionStatus | None, str | None]:
    """Lädt letzte Bestätigung; Status kann zur Laufzeit `stale` sein."""
    project = require_discovery_project(project)
    try:
        selection, warning = load_latest_selection(project.project_root_path)
    except InventoryArtifactError as exc:
        return None, None, str(exc)
    if selection is None:
        return None, None, warning
    status = effective_selection_status(selection, current_scan_id)
    return selection, status, warning


def set_group_selected(draft: SelectionDraft, source_group: str, selected: bool) -> SelectionDraft:
    groups = list(draft.selected_source_groups)
    if selected and source_group not in groups:
        groups.append(source_group)
    if not selected and source_group in groups:
        groups = [g for g in groups if g != source_group]
    # Einzelausschlüsse der deaktivierten Gruppe können bleiben; resolve ignoriert sie.
    return draft.model_copy(update={"selected_source_groups": groups})


def set_file_excluded(
    draft: SelectionDraft,
    relative_path: str,
    excluded: bool,
) -> SelectionDraft:
    paths = list(draft.excluded_relative_paths)
    if excluded and relative_path not in paths:
        paths.append(relative_path)
    if not excluded and relative_path in paths:
        paths = [p for p in paths if p != relative_path]
    return draft.model_copy(update={"excluded_relative_paths": paths})
