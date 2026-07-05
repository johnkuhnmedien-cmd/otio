"""Projektordner-Struktur und Asset-Erkennung."""

from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from otio_app.defaults import (
    DEFAULT_WORK_SUBDIR,
    EDIT_PLAN_FILENAME,
    EDIT_PLAN_SUBDIR,
    INVENTORY_FILENAME,
    INVENTORY_SUBDIR,
    VOICE_ANALYSIS_FILENAME,
    VOICE_FOLDER_MAPPING_FILENAME,
)

LANGUAGE_FOLDER_NAMES: dict[str, str] = {
    "de": "DE",
    "en": "EN",
}

VOICE_OVER_NAME_HINTS: tuple[str, ...] = (
    "voice over",
    "voiceover",
    "voice-over",
    "voice_over",
)


def language_folder_name(language: str) -> str:
    """Ordnername für Voice-over-Sprachen (z. B. de -> DE)."""
    normalized = language.strip().lower()
    return LANGUAGE_FOLDER_NAMES.get(normalized, language.strip().upper())


def default_work_dir(project_root: Path) -> Path:
    """Standard-Arbeitsordner innerhalb des Projektroots."""
    return project_root / DEFAULT_WORK_SUBDIR


def get_voice_over_dir(
    project_root: Path,
    voice_over_subdir: str,
    language: str,
) -> Path:
    """Pfad zum sprachspezifischen Voice-over-Unterordner."""
    return project_root / voice_over_subdir / language_folder_name(language)


def safe_folder_slug(value: str) -> str:
    """Dateiname-sicherer Slug für Ordner- und Medien-Cache."""
    return value.replace(" ", "_").replace("/", "_")


def get_inventory_dir(work_dir: Path) -> Path:
    """Verzeichnis für pro-Ordner-Inventar-JSONs unter dem Arbeitsordner."""
    return work_dir / INVENTORY_SUBDIR


def get_folder_inventory_path(work_dir: Path, folder_name: str) -> Path:
    """Pfad zur Inventar-JSON eines Asset-Ordners (z. B. _otio/inventory/Florida_Keys.json)."""
    return get_inventory_dir(work_dir) / f"{safe_folder_slug(folder_name)}.json"


def get_inventory_path(project_root: Path) -> Path:
    """Legacy-Pfad zur zentralen inventory.json im Projektroot (Migration)."""
    return project_root / INVENTORY_FILENAME


def get_voice_analysis_path(project_root: Path) -> Path:
    return project_root / VOICE_ANALYSIS_FILENAME


def get_voice_folder_mapping_path(project_root: Path) -> Path:
    return project_root / VOICE_FOLDER_MAPPING_FILENAME


def get_edit_plan_path(project_root: Path) -> Path:
    """Legacy-Pfad zur zentralen edit_plan.json im Projektroot (Migration)."""
    return project_root / EDIT_PLAN_FILENAME


def get_edit_plan_dir(work_dir: Path) -> Path:
    """Verzeichnis für pro-Ort-Schnittpläne unter dem Arbeitsordner."""
    return work_dir / EDIT_PLAN_SUBDIR


def get_folder_edit_plan_path(work_dir: Path, folder_name: str) -> Path:
    """Pfad zur Schnittplan-JSON eines Asset-Ordners (z. B. _otio/edit_plan/Florida_Keys.json)."""
    return get_edit_plan_dir(work_dir) / f"{safe_folder_slug(folder_name)}.json"


def get_exports_dir(work_dir: Path) -> Path:
    """Verzeichnis für OTIO-Exporte unter dem Arbeitsordner."""
    from otio_app.defaults import EXPORTS_SUBDIR

    return work_dir / EXPORTS_SUBDIR


def get_clean_media_output_dir(work_dir: Path) -> Path:
    """Transcodierte Medien: _otio/clean/<Ordner>/…"""
    from otio_app.defaults import CLEAN_MEDIA_OUTPUT_SUBDIR

    return work_dir / CLEAN_MEDIA_OUTPUT_SUBDIR


def get_clean_media_manifest_dir(work_dir: Path) -> Path:
    """Manifeste original → clean: _otio/clean_media/<Ordner>.json"""
    from otio_app.defaults import CLEAN_MEDIA_MANIFEST_SUBDIR

    return work_dir / CLEAN_MEDIA_MANIFEST_SUBDIR


def get_folder_clean_manifest_path(work_dir: Path, folder_name: str) -> Path:
    return get_clean_media_manifest_dir(work_dir) / f"{safe_folder_slug(folder_name)}.json"


def get_folder_clean_output_dir(work_dir: Path, folder_name: str) -> Path:
    return get_clean_media_output_dir(work_dir) / safe_folder_slug(folder_name)


def clean_output_path_for_media(
    work_dir: Path,
    folder_name: str,
    original_path: Path,
) -> Path:
    """Zielpfad für eine transcodierte MP4-Datei."""
    stem = safe_folder_slug(original_path.stem) or "media"
    return get_folder_clean_output_dir(work_dir, folder_name) / f"{stem}.mp4"


def aspect_filled_output_path_for_media(
    work_dir: Path,
    folder_name: str,
    original_path: Path,
    *,
    width: int,
    height: int,
) -> Path:
    """Eigener Dateiname für Zoom/Crop — Resolve verwechselt ihn nicht mit dem Original."""
    stem = safe_folder_slug(original_path.stem) or "media"
    return get_folder_clean_output_dir(work_dir, folder_name) / f"{stem}_{width}x{height}.mp4"


def get_otio_export_path(work_dir: Path, project_name: str) -> Path:
    """Standard-Pfad für den OTIO-Export eines Projekts."""
    safe_name = safe_folder_slug(project_name) or "timeline"
    return get_exports_dir(work_dir) / f"{safe_name}.otio"


def safe_path_is_dir(path: Path) -> bool:
    """Prüft ein Verzeichnis ohne Hänger bei nicht verfügbaren Cloud-Dateien."""
    try:
        return path.is_dir()
    except OSError:
        return False


def is_probably_icloud_path(path: Path) -> bool:
    """Erkennt typische iCloud-/CloudDocs-Pfade auf dem Mac."""
    resolved = str(path.expanduser().resolve())
    markers = (
        "Mobile Documents",
        "com~apple~CloudDocs",
        "iCloud Drive",
    )
    return any(marker in resolved for marker in markers)


def _names_match(left: str, right: str) -> bool:
    return left.strip().casefold() == right.strip().casefold()


def detect_voice_over_folder(subdirectory_names: list[str]) -> str | None:
    """Sucht einen Voice-over-Ordner anhand üblicher Namen."""
    for name in subdirectory_names:
        folded = name.strip().casefold()
        if folded in VOICE_OVER_NAME_HINTS:
            return name
        if "voice" in folded and "over" in folded:
            return name
    return None


def resolve_voice_over_folder_name(
    subdirectory_names: list[str],
    preferred_name: str,
) -> str | None:
    """Findet den Voice-over-Ordner (case-insensitive) oder erkennt ihn automatisch."""
    for name in subdirectory_names:
        if _names_match(name, preferred_name):
            return name
    return detect_voice_over_folder(subdirectory_names)


@dataclass(frozen=True)
class PathDiagnostic:
    input_path: str
    resolved_path: str
    exists: bool
    is_directory: bool
    total_entries: int
    subdirectory_names: list[str]
    file_names: list[str]
    unreadable_entries: list[str]
    read_error: str | None
    icloud_path: bool
    used_icloud_fallback: bool

    @property
    def has_entries(self) -> bool:
        return self.total_entries > 0


def _list_names_subprocess(path: Path) -> tuple[list[str], list[str], str | None]:
    """Fallback für macOS/iCloud: Ordner mit /bin/ls -1p lesen (ohne shell=True)."""
    try:
        result = subprocess.run(
            ["/bin/ls", "-1p", str(path)],
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError as exc:
        return [], [], str(exc)

    if result.returncode != 0:
        message = (result.stderr or result.stdout or "").strip()
        return [], [], message or f"ls exit {result.returncode}"

    subdirs: list[str] = []
    all_names: list[str] = []
    for line in result.stdout.splitlines():
        entry = line.strip()
        if not entry:
            continue
        if entry.endswith("/"):
            name = entry.rstrip("/")
            if not name.startswith("."):
                subdirs.append(name)
                all_names.append(name)
        elif not entry.startswith("."):
            all_names.append(entry)
    return subdirs, all_names, None


def diagnose_project_root(project_root: Path) -> PathDiagnostic:
    """Liefert eine ausführliche Diagnose für den Projektordner."""
    resolved = project_root.expanduser().resolve()
    exists = False
    is_directory = False
    try:
        exists = resolved.exists()
        is_directory = resolved.is_dir()
    except OSError as exc:
        return PathDiagnostic(
            input_path=str(project_root),
            resolved_path=str(resolved),
            exists=False,
            is_directory=False,
            total_entries=0,
            subdirectory_names=[],
            file_names=[],
            unreadable_entries=[],
            read_error=str(exc),
            icloud_path=is_probably_icloud_path(resolved),
            used_icloud_fallback=False,
        )

    raw_names: list[str] = []
    read_error: str | None = None
    for lister in (_list_names_iterdir, _list_names_os_listdir, _list_names_glob):
        raw_names, read_error = lister(resolved)
        if raw_names or read_error:
            break

    subdirectory_names: list[str] = []
    file_names: list[str] = []
    unreadable_entries: list[str] = []
    used_icloud_fallback = False

    for name in raw_names:
        if name.startswith("."):
            continue
        child = resolved / name
        try:
            if child.is_dir():
                subdirectory_names.append(name)
            elif child.is_file():
                file_names.append(name)
            else:
                unreadable_entries.append(name)
        except OSError:
            unreadable_entries.append(name)

    if not subdirectory_names and raw_names and is_probably_icloud_path(resolved):
        subdirectory_names = sorted(
            name for name in raw_names if not name.startswith(".")
        )
        used_icloud_fallback = bool(subdirectory_names)

    if not subdirectory_names and exists and is_directory:
        ls_subdirs, ls_all, ls_error = _list_names_subprocess(resolved)
        if ls_subdirs:
            subdirectory_names = sorted(ls_subdirs, key=str.lower)
            if not raw_names:
                raw_names = ls_all
            used_icloud_fallback = True
        elif ls_error and not read_error:
            read_error = ls_error

    return PathDiagnostic(
        input_path=str(project_root),
        resolved_path=str(resolved),
        exists=exists,
        is_directory=is_directory,
        total_entries=len(raw_names),
        subdirectory_names=sorted(subdirectory_names, key=str.lower),
        file_names=sorted(file_names, key=str.lower),
        unreadable_entries=sorted(unreadable_entries, key=str.lower),
        read_error=read_error,
        icloud_path=is_probably_icloud_path(resolved),
        used_icloud_fallback=used_icloud_fallback,
    )


def _list_names_iterdir(path: Path) -> tuple[list[str], str | None]:
    try:
        return [entry.name for entry in path.iterdir()], None
    except OSError as exc:
        return [], str(exc)


def _list_names_os_listdir(path: Path) -> tuple[list[str], str | None]:
    try:
        return list(os.listdir(path)), None
    except OSError as exc:
        return [], str(exc)


def _list_names_glob(path: Path) -> tuple[list[str], str | None]:
    try:
        return [entry.name for entry in path.glob("*")], None
    except OSError as exc:
        return [], str(exc)


def list_project_subdirectories(
    project_root: Path,
) -> tuple[list[str], str | None, PathDiagnostic, str | None]:
    """Liest alle Unterordner im Projektroot."""
    diagnostic = diagnose_project_root(project_root)

    if not diagnostic.exists:
        return (
            [],
            f"Projektordner existiert nicht: {diagnostic.resolved_path}",
            diagnostic,
            None,
        )
    if not diagnostic.is_directory:
        return (
            [],
            f"Pfad ist kein Verzeichnis: {diagnostic.resolved_path}",
            diagnostic,
            None,
        )
    if diagnostic.read_error:
        return (
            [],
            (
                f"Ordner konnte nicht gelesen werden ({diagnostic.resolved_path}): "
                f"{diagnostic.read_error}"
            ),
            diagnostic,
            None,
        )

    names = list(diagnostic.subdirectory_names)
    warning: str | None = None
    if diagnostic.used_icloud_fallback:
        warning = (
            "iCloud-Ordner erkannt: Unterordner wurden über Dateinamen erkannt. "
            "Bitte im Finder lokal laden, falls Inhalte fehlen."
        )

    if not names:
        hint = (
            f"Keine Unterordner in `{diagnostic.resolved_path}` gefunden "
            f"({diagnostic.total_entries} Einträge insgesamt)."
        )
        if diagnostic.icloud_path:
            hint += (
                " Dies ist ein iCloud-Pfad — öffne den Ordner im Finder und lade "
                "die Inhalte lokal herunter (Wolke-Symbol verschwindet)."
            )
        elif diagnostic.file_names:
            hint += (
                f" Gefundene Dateien im Root: {', '.join(diagnostic.file_names[:5])}."
            )
        return [], hint, diagnostic, warning

    return names, None, diagnostic, warning


@dataclass(frozen=True)
class ProjectStructureScan:
    project_root: Path
    work_dir: Path
    voice_over_subdir: str
    language: str
    all_subdirectory_names: list[str] = field(default_factory=list)
    asset_subdir_names: list[str] = field(default_factory=list)
    system_folder_names: list[str] = field(default_factory=list)
    voice_over_folder_name: str | None = None
    voice_over_dir: Path | None = None
    voice_over_language_dir: Path | None = None
    voice_over_language_exists: bool = False
    error: str | None = None
    warning: str | None = None
    diagnostic: PathDiagnostic | None = None

    @property
    def ok(self) -> bool:
        return self.error is None


def classify_subdirectories(
    subdirectory_names: list[str],
    voice_over_subdir: str,
    work_dir: Path,
    project_root: Path,
    language: str,
    *,
    warning: str | None = None,
    diagnostic: PathDiagnostic | None = None,
) -> ProjectStructureScan:
    """Ordnet Unterordner in Assets, Voice-over und System ein."""
    voice_over_folder_name = resolve_voice_over_folder_name(
        subdirectory_names,
        voice_over_subdir,
    )

    reserved_names: set[str] = {DEFAULT_WORK_SUBDIR.casefold()}
    if work_dir.parent == project_root:
        reserved_names.add(work_dir.name.casefold())

    asset_names: list[str] = []
    system_names: list[str] = []

    for name in subdirectory_names:
        folded = name.casefold()
        if voice_over_folder_name and _names_match(name, voice_over_folder_name):
            continue
        if folded in reserved_names:
            system_names.append(name)
            continue
        asset_names.append(name)

    voice_over_dir = (
        project_root / voice_over_folder_name if voice_over_folder_name else None
    )
    voice_over_language_dir = (
        get_voice_over_dir(project_root, voice_over_folder_name, language)
        if voice_over_folder_name
        else None
    )
    voice_over_language_exists = (
        safe_path_is_dir(voice_over_language_dir)
        if voice_over_language_dir is not None
        else False
    )

    return ProjectStructureScan(
        project_root=project_root,
        work_dir=work_dir,
        voice_over_subdir=voice_over_folder_name or voice_over_subdir.strip(),
        language=language,
        all_subdirectory_names=list(subdirectory_names),
        asset_subdir_names=sorted(asset_names, key=str.lower),
        system_folder_names=sorted(system_names, key=str.lower),
        voice_over_folder_name=voice_over_folder_name,
        voice_over_dir=voice_over_dir,
        voice_over_language_dir=voice_over_language_dir,
        voice_over_language_exists=voice_over_language_exists,
        warning=warning,
        diagnostic=diagnostic,
    )


def scan_project_structure(
    project_root: Path,
    work_dir: Path,
    voice_over_subdir: str,
    language: str,
) -> ProjectStructureScan:
    """Scannt den Projektordner und klassifiziert alle Unterordner."""
    subdirectory_names, error, diagnostic, warning = list_project_subdirectories(
        project_root
    )
    if error:
        return ProjectStructureScan(
            project_root=project_root.expanduser().resolve(),
            work_dir=work_dir,
            voice_over_subdir=voice_over_subdir.strip(),
            language=language,
            error=error,
            diagnostic=diagnostic,
        )
    return classify_subdirectories(
        subdirectory_names,
        voice_over_subdir,
        work_dir,
        project_root.expanduser().resolve(),
        language,
        warning=warning,
        diagnostic=diagnostic,
    )


def discover_asset_subdir_names(
    project_root: Path,
    work_dir: Path,
    voice_over_subdir: str,
    language: str = "de",
) -> list[str]:
    """Listet Asset-Unterordner-Namen."""
    scan = scan_project_structure(project_root, work_dir, voice_over_subdir, language)
    return scan.asset_subdir_names


def discover_asset_subdirs(
    project_root: Path,
    work_dir: Path,
    voice_over_subdir: str,
    language: str = "de",
) -> list[Path]:
    """Listet Asset-Unterordner im Projektroot."""
    return [
        project_root / name
        for name in discover_asset_subdir_names(
            project_root,
            work_dir,
            voice_over_subdir,
            language,
        )
    ]
