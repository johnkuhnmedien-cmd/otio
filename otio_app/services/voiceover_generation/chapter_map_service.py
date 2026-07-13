"""Kapitel-Karten nach bestätigter Dramaturgie — Bulk und Einzelgenerierung."""

from __future__ import annotations

import shutil
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from otio_app.defaults import (
    CHAPTER_MAP_ASPECT_RATIO,
    CHAPTER_MAP_MODEL_DEFAULT,
    CHAPTER_MAP_STATUS_FAIL,
    CHAPTER_MAP_STATUS_MISSING,
    CHAPTER_MAP_STATUS_PASS,
    CHAPTER_MAP_STYLE_EXAMPLE_1_FILENAME,
    CHAPTER_MAP_STYLE_EXAMPLE_2_FILENAME,
)
from otio_app.models import Project
from otio_app.project_layout import (
    chapter_map_filename,
    get_chapter_maps_manifest_path,
    get_chapter_maps_settings_path,
    get_chapter_maps_style_refs_dir,
    get_folder_chapter_map_path,
    get_folder_chapter_maps_dir,
)
from otio_app.services.gemini_client import GeminiNotConfiguredError
from otio_app.services.voiceover_generation.chapter_map_image_client import (
    ChapterMapImageError,
    generate_chapter_map_image,
)
from otio_app.services.voiceover_generation.chapter_map_models import (
    ChapterMapEntry,
    ChapterMapManifest,
    ChapterMapSettings,
)
from otio_app.services.voiceover_generation.dramaturgy_service import load_confirmed_dramaturgy
from otio_app.services.voiceover_generation.models import DramaturgyFolderEntry, DramaturgyPlan
from otio_app.services.voiceover_generation.project_brief_service import load_project_brief

_LANGUAGE_DISPLAY_NAMES: dict[str, str] = {
    "DE": "German",
    "EN": "English",
    "FR": "French",
    "ES": "Spanish",
    "PT": "Portuguese",
    "IT": "Italian",
}


@dataclass
class ChapterMapGenerateResult:
    status: str
    entry: ChapterMapEntry | None = None
    error: str = ""
    manifest: ChapterMapManifest | None = None


@dataclass
class ChapterMapBulkResult:
    status: str
    generated: int = 0
    failed: int = 0
    skipped: int = 0
    errors: list[str] = field(default_factory=list)
    manifest: ChapterMapManifest | None = None


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def load_chapter_map_settings(project: Project) -> ChapterMapSettings:
    path = get_chapter_maps_settings_path(project.language_work_dir_path)
    if not path.is_file():
        return ChapterMapSettings()
    try:
        return ChapterMapSettings.model_validate_json(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return ChapterMapSettings()


def save_chapter_map_settings(project: Project, settings: ChapterMapSettings) -> ChapterMapSettings:
    path = get_chapter_maps_settings_path(project.language_work_dir_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(settings.model_dump_json(indent=2), encoding="utf-8")
    return settings


def load_chapter_map_manifest(project: Project) -> ChapterMapManifest:
    path = get_chapter_maps_manifest_path(project.language_work_dir_path)
    if not path.is_file():
        return ChapterMapManifest(project_id=project.id)
    try:
        return ChapterMapManifest.model_validate_json(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return ChapterMapManifest(project_id=project.id)


def save_chapter_map_manifest(project: Project, manifest: ChapterMapManifest) -> ChapterMapManifest:
    normalized = manifest.model_copy(
        update={"project_id": project.id, "updated_at": _utcnow()}
    )
    path = get_chapter_maps_manifest_path(project.language_work_dir_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(normalized.model_dump_json(indent=2), encoding="utf-8")
    return normalized


def resolve_style_example_paths(project: Project, settings: ChapterMapSettings) -> tuple[Path, Path]:
    """Liefert (example_1, example_2). Preferiert Settings-Pfade, sonst style_refs/."""
    style_dir = get_chapter_maps_style_refs_dir(project.language_work_dir_path)
    candidates_1 = [
        Path(settings.style_example_1_path) if settings.style_example_1_path.strip() else None,
        style_dir / CHAPTER_MAP_STYLE_EXAMPLE_1_FILENAME,
        style_dir / "EN_MAP_EXMAPLE_1.png",  # häufige Tippfehler-Variante aus Finder
    ]
    candidates_2 = [
        Path(settings.style_example_2_path) if settings.style_example_2_path.strip() else None,
        style_dir / CHAPTER_MAP_STYLE_EXAMPLE_2_FILENAME,
        style_dir / "EN_MAP_EXMAPLE_2.png",
    ]
    path_1 = next((path for path in candidates_1 if path is not None and path.is_file()), None)
    path_2 = next((path for path in candidates_2 if path is not None and path.is_file()), None)
    if path_1 is None or path_2 is None:
        raise ChapterMapImageError(
            "Style-Beispielbilder fehlen. Bitte EN_MAP_EXAMPLE_1.png und "
            "EN_MAP_EXAMPLE_2.png unter den Kapitel-Karten-Einstellungen hinterlegen "
            f"(oder nach `{style_dir}` kopieren)."
        )
    return path_1, path_2


def import_style_examples_from_folder(project: Project, source_folder: Path) -> ChapterMapSettings:
    """Kopiert Example-1/2 aus einem Ordner (z. B. Map_example) in style_refs/."""
    if not source_folder.is_dir():
        raise ChapterMapImageError(f"Ordner nicht gefunden: `{source_folder}`")

    def _find(names: tuple[str, ...]) -> Path | None:
        for name in names:
            candidate = source_folder / name
            if candidate.is_file():
                return candidate
        # case-insensitive fallback
        lowered = {path.name.lower(): path for path in source_folder.iterdir() if path.is_file()}
        for name in names:
            hit = lowered.get(name.lower())
            if hit is not None:
                return hit
        return None

    src_1 = _find((CHAPTER_MAP_STYLE_EXAMPLE_1_FILENAME, "EN_MAP_EXMAPLE_1.png"))
    src_2 = _find((CHAPTER_MAP_STYLE_EXAMPLE_2_FILENAME, "EN_MAP_EXMAPLE_2.png"))
    if src_1 is None or src_2 is None:
        raise ChapterMapImageError(
            f"In `{source_folder}` wurden EN_MAP_EXAMPLE_1/2 (oder EXMAPLE-Tippfehler) nicht gefunden."
        )

    dest_dir = get_chapter_maps_style_refs_dir(project.language_work_dir_path)
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest_1 = dest_dir / CHAPTER_MAP_STYLE_EXAMPLE_1_FILENAME
    dest_2 = dest_dir / CHAPTER_MAP_STYLE_EXAMPLE_2_FILENAME
    shutil.copy2(src_1, dest_1)
    shutil.copy2(src_2, dest_2)
    settings = load_chapter_map_settings(project).model_copy(
        update={
            "style_example_1_path": str(dest_1),
            "style_example_2_path": str(dest_2),
        }
    )
    return save_chapter_map_settings(project, settings)


def _enabled_ordered_entries(plan: DramaturgyPlan) -> list[DramaturgyFolderEntry]:
    return sorted(
        (entry for entry in plan.recommended_folder_order if entry.enabled),
        key=lambda entry: entry.order_index,
    )


def _project_language(project: Project, plan: DramaturgyPlan) -> str:
    brief = load_project_brief(project)
    language = (brief.language or plan.language or project.language or "EN").strip().upper()
    return language or "EN"


def _language_display_name(language: str) -> str:
    return _LANGUAGE_DISPLAY_NAMES.get(language.upper(), language)


def build_chapter_map_prompt(
    *,
    order_index: int,
    location_name: str,
    previous_location_name: str | None,
    language: str,
    is_first: bool,
) -> str:
    lang_name = _language_display_name(language)
    if is_first:
        return f"""Create a new 16:9 chapter map image in EXACTLY the same visual style as the
provided style reference image (example 1).

Hard requirements:
- Output aspect ratio MUST be 16:9 (widescreen). No letterboxing, no black bars, no borders.
- Keep colors, logo, fonts, font sizes, map styling, and layout proportions identical to the reference.
- Top-left large chapter number: "{order_index}"
- Bottom-left large location title: "{location_name}"
- Map callout label for the pin: "{location_name}" (uppercase styling as in the reference)
- Place ONE green location pin on the correct geographic position for "{location_name}".
- Write ALL visible location words in {lang_name} (project language: {language}). Keep numbers as digits.
- Do not invent extra locations, routes, or labels.
"""

    return f"""Edit the previous generated chapter map (second attached image) using the style of the
style reference (example 2 / first attached image).

Hard requirements:
- Output aspect ratio MUST be 16:9. No letterboxing, no black bars, no borders.
- Keep colors, logo, fonts, font sizes, map styling, and layout proportions identical.
- Change the large top-left number to "{order_index}".
- Change the large bottom-left title to "{location_name}".
- Keep the previous location "{previous_location_name}" as the start pin (red, as in example 2).
- Add the new destination "{location_name}" as the end pin (green).
- Draw a dotted connection line from "{previous_location_name}" to "{location_name}".
- Update map callout labels accordingly.
- Write ALL visible location words in {lang_name} (project language: {language}).
- Do not restyle the brand logo or background color.
"""


def _upsert_manifest_entry(manifest: ChapterMapManifest, entry: ChapterMapEntry) -> ChapterMapManifest:
    entries = [item for item in manifest.entries if item.order_index != entry.order_index]
    entries.append(entry)
    entries.sort(key=lambda item: item.order_index)
    return manifest.model_copy(update={"entries": entries})


def _invalidate_following_entries(
    manifest: ChapterMapManifest, *, from_order_index: int
) -> ChapterMapManifest:
    """Nach Einzel-Regenerierung ab Index N sind Folgebilder stilistisch veraltet."""
    updated: list[ChapterMapEntry] = []
    for entry in manifest.entries:
        if entry.order_index > from_order_index and entry.status == CHAPTER_MAP_STATUS_PASS:
            updated.append(
                entry.model_copy(
                    update={
                        "status": CHAPTER_MAP_STATUS_MISSING,
                        "error": (
                            f"Ungültig nach Neu-Generierung von Kapitel {from_order_index} "
                            "— bitte Bulk ab hier erneut ausführen."
                        ),
                    }
                )
            )
        else:
            updated.append(entry)
    return manifest.model_copy(update={"entries": updated})


def _relative_to_project(project: Project, path: Path) -> str:
    try:
        return str(path.resolve().relative_to(project.project_root_path.resolve()))
    except ValueError:
        return str(path)


def generate_single_chapter_map(
    project: Project,
    *,
    order_index: int,
    invalidate_following: bool = True,
) -> ChapterMapGenerateResult:
    """Generiert genau eine Kapitel-Karte (benötigt Vorgänger-Karte wenn order_index > 1)."""
    plan = load_confirmed_dramaturgy(project)
    if plan is None:
        return ChapterMapGenerateResult(
            status=CHAPTER_MAP_STATUS_FAIL,
            error="Keine bestätigte Dramaturgie. Bitte zuerst die Dramaturgie bestätigen.",
        )

    ordered = _enabled_ordered_entries(plan)
    target = next((entry for entry in ordered if entry.order_index == order_index), None)
    if target is None:
        return ChapterMapGenerateResult(
            status=CHAPTER_MAP_STATUS_FAIL,
            error=f"Kein aktives Kapitel mit order_index={order_index} in der bestätigten Dramaturgie.",
        )

    settings = load_chapter_map_settings(project)
    language = _project_language(project, plan)
    model = (settings.model or CHAPTER_MAP_MODEL_DEFAULT).strip()

    try:
        example_1, example_2 = resolve_style_example_paths(project, settings)
    except ChapterMapImageError as exc:
        return ChapterMapGenerateResult(status=CHAPTER_MAP_STATUS_FAIL, error=str(exc))

    previous_entry = next((entry for entry in ordered if entry.order_index == order_index - 1), None)
    previous_path: Path | None = None
    if order_index > 1:
        if previous_entry is None:
            return ChapterMapGenerateResult(
                status=CHAPTER_MAP_STATUS_FAIL,
                error=f"Vorgänger-Kapitel für Index {order_index} fehlt in der Dramaturgie.",
            )
        previous_path = get_folder_chapter_map_path(
            project.project_root_path,
            folder_name=previous_entry.folder_name,
            order_index=previous_entry.order_index,
        )
        if not previous_path.is_file():
            return ChapterMapGenerateResult(
                status=CHAPTER_MAP_STATUS_FAIL,
                error=(
                    f"Vorgänger-Karte fehlt: `{previous_path}`. "
                    "Bitte zuerst das vorherige Kapitel (oder Bulk) generieren."
                ),
            )

    is_first = order_index == 1
    prompt = build_chapter_map_prompt(
        order_index=order_index,
        location_name=target.folder_name,
        previous_location_name=previous_entry.folder_name if previous_entry else None,
        language=language,
        is_first=is_first,
    )
    if is_first:
        reference_paths = [example_1]
    else:
        assert previous_path is not None
        reference_paths = [example_2, previous_path]

    output_path = get_folder_chapter_map_path(
        project.project_root_path,
        folder_name=target.folder_name,
        order_index=order_index,
    )
    get_folder_chapter_maps_dir(project.project_root_path, target.folder_name).mkdir(
        parents=True, exist_ok=True
    )

    try:
        width, height = generate_chapter_map_image(
            prompt=prompt,
            reference_image_paths=reference_paths,
            output_path=output_path,
            model=model,
        )
    except (ChapterMapImageError, GeminiNotConfiguredError) as exc:
        entry = ChapterMapEntry(
            order_index=order_index,
            folder_name=target.folder_name,
            filename=chapter_map_filename(order_index=order_index, folder_name=target.folder_name),
            relative_path=_relative_to_project(project, output_path),
            absolute_path=str(output_path),
            previous_map_path=str(previous_path) if previous_path else "",
            language=language,
            model=model,
            status=CHAPTER_MAP_STATUS_FAIL,
            error=str(exc),
        )
        manifest = _upsert_manifest_entry(load_chapter_map_manifest(project), entry)
        manifest = save_chapter_map_manifest(
            project,
            manifest.model_copy(update={"language": language, "model": model}),
        )
        return ChapterMapGenerateResult(
            status=CHAPTER_MAP_STATUS_FAIL, entry=entry, error=str(exc), manifest=manifest
        )

    entry = ChapterMapEntry(
        order_index=order_index,
        folder_name=target.folder_name,
        filename=output_path.name,
        relative_path=_relative_to_project(project, output_path),
        absolute_path=str(output_path),
        previous_map_path=str(previous_path) if previous_path else "",
        language=language,
        model=model,
        status=CHAPTER_MAP_STATUS_PASS,
        width=width,
        height=height,
        generated_at=_utcnow(),
    )
    manifest = _upsert_manifest_entry(load_chapter_map_manifest(project), entry)
    if invalidate_following:
        manifest = _invalidate_following_entries(manifest, from_order_index=order_index)
    manifest = save_chapter_map_manifest(
        project,
        manifest.model_copy(
            update={
                "language": language,
                "model": model,
                "aspect_ratio": CHAPTER_MAP_ASPECT_RATIO,
            }
        ),
    )
    return ChapterMapGenerateResult(
        status=CHAPTER_MAP_STATUS_PASS, entry=entry, manifest=manifest
    )


def generate_all_chapter_maps(
    project: Project,
    *,
    start_order_index: int = 1,
    stop_on_error: bool = True,
) -> ChapterMapBulkResult:
    """Sequentieller Bulk: Kapitel ab start_order_index in Reihenfolge erzeugen."""
    plan = load_confirmed_dramaturgy(project)
    if plan is None:
        return ChapterMapBulkResult(
            status=CHAPTER_MAP_STATUS_FAIL,
            errors=["Keine bestätigte Dramaturgie. Bitte zuerst die Dramaturgie bestätigen."],
        )

    ordered = [
        entry
        for entry in _enabled_ordered_entries(plan)
        if entry.order_index >= start_order_index
    ]
    if not ordered:
        return ChapterMapBulkResult(
            status=CHAPTER_MAP_STATUS_FAIL,
            errors=[f"Keine aktiven Kapitel ab order_index>={start_order_index}."],
        )

    generated = 0
    failed = 0
    errors: list[str] = []
    manifest = load_chapter_map_manifest(project)

    for entry in ordered:
        result = generate_single_chapter_map(
            project,
            order_index=entry.order_index,
            invalidate_following=False,
        )
        if result.manifest is not None:
            manifest = result.manifest
        if result.status == CHAPTER_MAP_STATUS_PASS:
            generated += 1
        else:
            failed += 1
            message = f"Kapitel {entry.order_index} ({entry.folder_name}): {result.error}"
            errors.append(message)
            if stop_on_error:
                break

    status = CHAPTER_MAP_STATUS_PASS if failed == 0 else CHAPTER_MAP_STATUS_FAIL
    return ChapterMapBulkResult(
        status=status,
        generated=generated,
        failed=failed,
        errors=errors,
        manifest=manifest,
    )
