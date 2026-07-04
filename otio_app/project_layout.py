"""Projektordner-Struktur und Asset-Erkennung."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from otio_app.defaults import DEFAULT_WORK_SUBDIR, INVENTORY_FILENAME, VOICE_ANALYSIS_FILENAME

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


def get_inventory_path(project_root: Path) -> Path:
    return project_root / INVENTORY_FILENAME


def get_voice_analysis_path(project_root: Path) -> Path:
    return project_root / VOICE_ANALYSIS_FILENAME


def safe_path_is_dir(path: Path) -> bool:
    """Prüft ein Verzeichnis ohne Hänger bei nicht verfügbaren Cloud-Dateien."""
    try:
        return path.is_dir()
    except OSError:
        return False


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


def list_project_subdirectories(project_root: Path) -> tuple[list[str], str | None]:
    """Liest alle Unterordner im Projektroot."""
    if not safe_path_is_dir(project_root):
        return [], "Projektordner nicht gefunden oder nicht lesbar."

    names: list[str] = []
    try:
        entries = sorted(project_root.iterdir(), key=lambda path: path.name.lower())
    except OSError as exc:
        return [], f"Ordner konnte nicht gelesen werden: {exc}"

    for entry in entries:
        try:
            if entry.is_dir() and not entry.name.startswith("."):
                names.append(entry.name)
        except OSError:
            continue

    if not names:
        return [], (
            "Keine Unterordner gefunden. Prüfe den Pfad, entferne Anführungszeichen "
            "und lade iCloud-Ordner im Finder ggf. erst lokal herunter."
        )
    return names, None


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

    @property
    def ok(self) -> bool:
        return self.error is None


def classify_subdirectories(
    subdirectory_names: list[str],
    voice_over_subdir: str,
    work_dir: Path,
    project_root: Path,
    language: str,
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
    )


def scan_project_structure(
    project_root: Path,
    work_dir: Path,
    voice_over_subdir: str,
    language: str,
) -> ProjectStructureScan:
    """Scannt den Projektordner und klassifiziert alle Unterordner."""
    subdirectory_names, error = list_project_subdirectories(project_root)
    if error:
        return ProjectStructureScan(
            project_root=project_root,
            work_dir=work_dir,
            voice_over_subdir=voice_over_subdir.strip(),
            language=language,
            error=error,
        )
    return classify_subdirectories(
        subdirectory_names,
        voice_over_subdir,
        work_dir,
        project_root,
        language,
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
