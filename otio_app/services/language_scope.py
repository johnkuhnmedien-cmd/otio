"""Language-Scope unter `_otio/{LANG}/` — einmalige Migration + Pfad-Auflösung.

SHARED bleibt unter `_otio/` (clean, inventory, cache/inventory, frames, …).
LANGUAGE-Artefakte liegen unter `_otio/{DE|EN}/` (voiceover_generation, edit_plan,
exports, generated_titles, Settings-JSONs, …).

Ein DB-Projekt = eine Sprache (`Project.language`).
"""

from __future__ import annotations

import json
import shutil
from datetime import datetime, timezone
from pathlib import Path

from otio_app.models import Project
from otio_app.project_layout import get_language_work_dir, language_folder_name

LANGUAGE_SCOPE_MARKER_NAME = ".language_scope_v1.json"
LANGUAGE_SCOPE_VERSION = 1

# Ordner, die von flat `_otio/` nach `_otio/{LANG}/` wandern.
_LANGUAGE_DIRS: tuple[str, ...] = (
    "edit_plan",
    "exports",
    "generated_titles",
    "voiceover_generation",
    "model_comparison_runs",
    "supplement",
)

# Einzeldateien am `_otio/`-Root → Language-Scope.
_LANGUAGE_ROOT_FILES: tuple[str, ...] = (
    "edit_plan_rules.json",
    "edit_plan_timing_settings.json",
    "otio_export_settings.json",
    "validation_report.json",
    "edit_plan_validation_report.json",
    "gemini_retry_report.json",
    "edit_plan_candidate.failed.json",
)

# Nur dieser Cache-Zweig ist sprachspezifisch (nicht cache/inventory).
_LANGUAGE_CACHE_VOICE = ("cache", "voice")


def language_scope_marker_path(work_dir: Path) -> Path:
    return work_dir / LANGUAGE_SCOPE_MARKER_NAME


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _load_marker(work_dir: Path) -> dict:
    path = language_scope_marker_path(work_dir)
    if not path.is_file():
        return {"version": LANGUAGE_SCOPE_VERSION, "languages": []}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            return {"version": LANGUAGE_SCOPE_VERSION, "languages": []}
        languages = payload.get("languages") or []
        if not isinstance(languages, list):
            languages = []
        return {
            "version": int(payload.get("version") or LANGUAGE_SCOPE_VERSION),
            "languages": [str(item) for item in languages],
            "migrated_at": payload.get("migrated_at"),
            "source_layout": payload.get("source_layout"),
        }
    except (OSError, UnicodeError, json.JSONDecodeError, TypeError, ValueError):
        return {"version": LANGUAGE_SCOPE_VERSION, "languages": []}


def _write_marker(work_dir: Path, *, lang: str, source_layout: str) -> None:
    path = language_scope_marker_path(work_dir)
    existing = _load_marker(work_dir)
    languages = list(dict.fromkeys([*existing.get("languages", []), lang]))
    payload = {
        "version": LANGUAGE_SCOPE_VERSION,
        "languages": languages,
        "migrated_at": _utcnow_iso(),
        "source_layout": source_layout,
    }
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def _has_flat_language_artifacts(work_dir: Path) -> bool:
    for name in _LANGUAGE_DIRS:
        if (work_dir / name).exists():
            return True
    for name in _LANGUAGE_ROOT_FILES:
        if (work_dir / name).is_file():
            return True
    if (work_dir.joinpath(*_LANGUAGE_CACHE_VOICE)).exists():
        return True
    return False


def _safe_move(src: Path, dest: Path) -> None:
    if not src.exists():
        return
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists():
        # Ziel schon da — Quelle nicht überschreiben; flat-Rest bleibt liegen.
        return
    shutil.move(str(src), str(dest))


def _rewrite_absolute_paths_in_tree(root: Path, *, old_prefix: str, new_prefix: str) -> int:
    """Ersetzt absolute Pfad-Prefixe in JSON-Dateien unter root."""
    if not root.is_dir() or old_prefix == new_prefix:
        return 0
    changed = 0
    for path in root.rglob("*.json"):
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeError):
            continue
        if old_prefix not in text:
            continue
        updated = text.replace(old_prefix, new_prefix)
        if updated == text:
            continue
        try:
            path.write_text(updated, encoding="utf-8")
            changed += 1
        except OSError:
            continue
    return changed


def migrate_language_scope(project: Project) -> Path:
    """Einmalige Migration flat `_otio/` → `_otio/{LANG}/`. Idempotent.

    Gibt den Language-Work-Dir zurück.
    """
    work_dir = project.work_dir_path
    work_dir.mkdir(parents=True, exist_ok=True)
    lang = language_folder_name(project.language)
    lang_dir = get_language_work_dir(work_dir, project.language)
    marker = _load_marker(work_dir)

    if lang in marker.get("languages", []):
        lang_dir.mkdir(parents=True, exist_ok=True)
        return lang_dir

    flat_present = _has_flat_language_artifacts(work_dir)
    if not flat_present:
        lang_dir.mkdir(parents=True, exist_ok=True)
        _write_marker(work_dir, lang=lang, source_layout="fresh")
        return lang_dir

    lang_dir.mkdir(parents=True, exist_ok=True)

    try:
        old_prefix = str(work_dir.resolve())
        new_prefix = str(lang_dir.resolve())
    except OSError:
        old_prefix = str(work_dir)
        new_prefix = str(lang_dir)

    for name in _LANGUAGE_DIRS:
        _safe_move(work_dir / name, lang_dir / name)

    for name in _LANGUAGE_ROOT_FILES:
        _safe_move(work_dir / name, lang_dir / name)

    voice_src = work_dir.joinpath(*_LANGUAGE_CACHE_VOICE)
    voice_dest = lang_dir.joinpath(*_LANGUAGE_CACHE_VOICE)
    _safe_move(voice_src, voice_dest)

    _rewrite_absolute_paths_in_tree(lang_dir, old_prefix=old_prefix, new_prefix=new_prefix)

    _write_marker(work_dir, lang=lang, source_layout="flat")
    return lang_dir


def ensure_language_scope(project: Project) -> Path:
    """Stellt sicher, dass der Language-Scope existiert (migriert bei Bedarf)."""
    return migrate_language_scope(project)


__all__ = [
    "LANGUAGE_SCOPE_MARKER_NAME",
    "ensure_language_scope",
    "language_scope_marker_path",
    "migrate_language_scope",
]
