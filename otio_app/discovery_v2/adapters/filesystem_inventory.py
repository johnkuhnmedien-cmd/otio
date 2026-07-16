"""Rekursiver read-only Scanner für Discovery-V2-Bestandsaufnahmen."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from otio_app.discovery_v2.domain.exclusions import (
    exclusion_reason_for_dir,
    exclusion_reason_for_file,
    is_excluded_dir_name,
    is_excluded_file_name,
)
from otio_app.discovery_v2.domain.inventory import (
    ROOT_SOURCE_GROUP,
    ROOT_SOURCE_GROUP_LABEL,
    ExcludedEntry,
    InventoryFileEntry,
    MediaKind,
    ScanStatus,
    SourceGroupSummary,
)
from otio_app.discovery_v2.media_types import classify_media_kind


class InventoryScanError(ValueError):
    """Kontrollierter Scanner-Fehler (z. B. fehlender Projektroot)."""


@dataclass
class FilesystemScanResult:
    files: list[InventoryFileEntry] = field(default_factory=list)
    excluded: list[ExcludedEntry] = field(default_factory=list)
    source_groups: list[SourceGroupSummary] = field(default_factory=list)
    video_count: int = 0
    image_count: int = 0
    audio_count: int = 0
    other_count: int = 0


def source_group_for_relative(relative_path: str) -> tuple[str, str]:
    """Liefert (source_group, label) für einen relativen Pfad."""
    parts = Path(relative_path).parts
    if len(parts) <= 1:
        return ROOT_SOURCE_GROUP, ROOT_SOURCE_GROUP_LABEL
    top = parts[0]
    return top, top


def _sort_key_file(entry: InventoryFileEntry) -> tuple[str, str, str]:
    return (
        entry.source_group_label.casefold(),
        entry.relative_path.casefold(),
        entry.relative_path,
    )


def _sort_key_group(group: SourceGroupSummary) -> tuple[str, str]:
    # Unsortiert zuletzt in der UI ist optional — spez sagt Quellgruppe zuerst.
    # Stabil: Label casefold, dann interner Key.
    if group.source_group == ROOT_SOURCE_GROUP:
        return ("\uffff", group.source_group)
    return (group.label.casefold(), group.source_group)


def _is_symlink_escaping_root(path: Path, project_root: Path) -> bool:
    """True, wenn ein Symlink (Datei/Dir) außerhalb des Projektroots zeigt."""
    if not path.is_symlink():
        return False
    try:
        resolved = path.resolve(strict=False)
        resolved.relative_to(project_root)
    except (OSError, ValueError):
        return True
    return False


def scan_project_filesystem(project_root: Path | str) -> FilesystemScanResult:
    """Scannt den Projektroot rekursiv ohne Symlink-Follow und ohne Dateiinhalte."""
    root = Path(project_root).expanduser()
    if not root.exists():
        raise InventoryScanError(f"Projektroot existiert nicht: {root}")
    if not root.is_dir():
        raise InventoryScanError(f"Projektroot ist kein Verzeichnis: {root}")
    try:
        root = root.resolve()
    except OSError as exc:
        raise InventoryScanError(f"Projektroot nicht auflösbar: {root}") from exc
    if not os.access(root, os.R_OK | os.X_OK):
        raise InventoryScanError(f"Projektroot nicht lesbar: {root}")

    files: list[InventoryFileEntry] = []
    excluded: list[ExcludedEntry] = []

    def walk(current: Path) -> None:
        try:
            with os.scandir(current) as it:
                entries = sorted(it, key=lambda e: e.name.casefold())
        except PermissionError:
            try:
                rel = str(current.relative_to(root))
            except ValueError:
                rel = str(current)
            excluded.append(
                ExcludedEntry(relative_path=rel or ".", reason="keine Leserechte")
            )
            return
        except FileNotFoundError:
            return

        for entry in entries:
            name = entry.name
            try:
                path = Path(entry.path)
            except OSError:
                excluded.append(
                    ExcludedEntry(
                        relative_path=name,
                        reason="Eintrag nicht lesbar",
                    )
                )
                continue

            try:
                rel = path.relative_to(root)
            except ValueError:
                excluded.append(
                    ExcludedEntry(
                        relative_path=str(path),
                        reason="außerhalb des Projektroots",
                    )
                )
                continue
            rel_str = rel.as_posix()

            try:
                is_dir = entry.is_dir(follow_symlinks=False)
                is_file = entry.is_file(follow_symlinks=False)
                is_symlink = entry.is_symlink()
            except OSError:
                excluded.append(
                    ExcludedEntry(
                        relative_path=rel_str,
                        reason="Metadaten nicht lesbar",
                    )
                )
                continue

            if is_dir:
                if is_excluded_dir_name(name):
                    excluded.append(
                        ExcludedEntry(
                            relative_path=rel_str,
                            reason=exclusion_reason_for_dir(name),
                        )
                    )
                    continue
                if is_symlink:
                    # Symlink-Verzeichnissen nicht rekursiv folgen.
                    excluded.append(
                        ExcludedEntry(
                            relative_path=rel_str,
                            reason="Symlink-Verzeichnis (kein rekursives Folgen)",
                        )
                    )
                    continue
                walk(path)
                continue

            if is_symlink:
                if _is_symlink_escaping_root(path, root):
                    excluded.append(
                        ExcludedEntry(
                            relative_path=rel_str,
                            reason="Symlink führt außerhalb des Projektroots",
                        )
                    )
                    continue
                # Symlink-Datei innerhalb des Roots: Metadaten vom Ziel nur via lstat?
                # Spez: keine Datei öffnen; außerhalb ablehnen. Innerhalb als Datei
                # aufnehmen, wenn Ziel eine Datei ist — ohne Follow in Dirs.
                try:
                    if path.resolve(strict=False).is_dir():
                        excluded.append(
                            ExcludedEntry(
                                relative_path=rel_str,
                                reason="Symlink auf Verzeichnis (kein Folgen)",
                            )
                        )
                        continue
                except OSError:
                    excluded.append(
                        ExcludedEntry(
                            relative_path=rel_str,
                            reason="Symlink nicht auflösbar",
                        )
                    )
                    continue

            if not is_file and not is_symlink:
                excluded.append(
                    ExcludedEntry(
                        relative_path=rel_str,
                        reason="kein regulärer Dateieintrag",
                    )
                )
                continue

            if is_excluded_file_name(name):
                excluded.append(
                    ExcludedEntry(
                        relative_path=rel_str,
                        reason=exclusion_reason_for_file(name),
                    )
                )
                continue

            try:
                stat = path.lstat() if is_symlink else path.stat()
                size = int(stat.st_size)
                mtime = datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc)
            except FileNotFoundError:
                excluded.append(
                    ExcludedEntry(
                        relative_path=rel_str,
                        reason="Datei verschwunden während des Scans",
                    )
                )
                continue
            except OSError:
                files.append(
                    InventoryFileEntry(
                        relative_path=rel_str,
                        filename=name,
                        extension=path.suffix.lower(),
                        source_group=source_group_for_relative(rel_str)[0],
                        source_group_label=source_group_for_relative(rel_str)[1],
                        media_kind=classify_media_kind(path.suffix),
                        size_bytes=0,
                        mtime_iso="",
                        scan_status=ScanStatus.ERROR,
                    )
                )
                continue

            group_id, group_label = source_group_for_relative(rel_str)
            files.append(
                InventoryFileEntry(
                    relative_path=rel_str,
                    filename=name,
                    extension=path.suffix.lower(),
                    source_group=group_id,
                    source_group_label=group_label,
                    media_kind=classify_media_kind(path.suffix),
                    size_bytes=size,
                    mtime_iso=mtime.isoformat(),
                    scan_status=ScanStatus.FOUND,
                )
            )

    walk(root)

    files.sort(key=_sort_key_file)
    excluded.sort(key=lambda e: (e.relative_path.casefold(), e.relative_path))

    counts: dict[str, SourceGroupSummary] = {}
    video_count = image_count = audio_count = other_count = 0
    for item in files:
        if item.scan_status != ScanStatus.FOUND:
            continue
        summary = counts.get(item.source_group)
        if summary is None:
            summary = SourceGroupSummary(
                source_group=item.source_group,
                label=item.source_group_label,
            )
            counts[item.source_group] = summary
        summary.file_count += 1
        if item.media_kind == MediaKind.VIDEO:
            summary.video_count += 1
            video_count += 1
        elif item.media_kind == MediaKind.IMAGE:
            summary.image_count += 1
            image_count += 1
        elif item.media_kind == MediaKind.AUDIO:
            summary.audio_count += 1
            audio_count += 1
        else:
            summary.other_count += 1
            other_count += 1

    groups = sorted(counts.values(), key=_sort_key_group)
    return FilesystemScanResult(
        files=files,
        excluded=excluded,
        source_groups=groups,
        video_count=video_count,
        image_count=image_count,
        audio_count=audio_count,
        other_count=other_count,
    )
