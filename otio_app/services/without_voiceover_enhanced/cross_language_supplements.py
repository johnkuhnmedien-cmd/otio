"""Accepted Supplements aus Geschwister-Sprachen für Unified LLM Cut / Katalog.

Shared Inventory/Clean bleiben die Medienquelle; die Acceptance-Listen liegen
pro Sprache. Dieses Modul macht ``export_ready``-Einträge anderer Sprachen für
den aktuellen LLM-Cut und den Asset-Katalog sichtbar.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Iterable

from otio_app.defaults import VOICEOVER_GENERATION_SUBDIR
from otio_app.models import Project
from otio_app.project_layout import language_folder_name, safe_folder_slug
from otio_app.services.without_voiceover_enhanced.io_utils import load_model
from otio_app.services.without_voiceover_enhanced.local_media_service import (
    STATUS_EXPORT_READY,
    is_http_url,
    refresh_supplement_validation,
)
from otio_app.services.without_voiceover_enhanced.models import (
    AcceptedSupplementsDocument,
    StockCandidate,
)
from otio_app.services.without_voiceover_enhanced.paths import (
    ACCEPTED_SUPPLEMENTS_FILENAME,
    STOCK_SUBDIR,
    assert_enhanced_work_root,
)

# Geteilte Artefakt-Ordner unter ``_otio_enhanced/`` — keine Sprach-Scopes.
_SHARED_WORK_DIR_NAMES = frozenset(
    {
        "inventory",
        "clean",
        "clean_media",
        "config",
        "placeholders",
        "frames",
        "exports",
        "llm_runs",
    }
)


def iter_sibling_language_dirs(project: Project) -> list[tuple[str, Path]]:
    """``[(LANG, language_dir), …]`` außer der aktuellen Projektsprache."""
    work = assert_enhanced_work_root(project)
    current = language_folder_name(project.language)
    if not work.is_dir():
        return []
    out: list[tuple[str, Path]] = []
    for child in sorted(work.iterdir(), key=lambda p: p.name.lower()):
        if not child.is_dir():
            continue
        name = child.name.strip()
        if not name or name == current:
            continue
        if name.lower() in _SHARED_WORK_DIR_NAMES:
            continue
        accepted = (
            child
            / VOICEOVER_GENERATION_SUBDIR
            / STOCK_SUBDIR
            / ACCEPTED_SUPPLEMENTS_FILENAME
        )
        if accepted.is_file():
            out.append((name, child))
    return out


def gap_folder_hint(gap_id: str) -> str:
    """``Yellowstone_gap_001`` → ``Yellowstone``; sonst leer."""
    text = (gap_id or "").strip()
    if not text:
        return ""
    lower = text.lower()
    marker = "_gap_"
    idx = lower.find(marker)
    if idx <= 0:
        return ""
    return text[:idx].strip()


def folder_matches_supplement(
    folder_name: str,
    *,
    gap_id: str,
    media_path: str,
) -> bool:
    """Ob ein Supplement zum Kapitel-/Ordner-Kontext gehört."""
    target = safe_folder_slug(folder_name or "").strip().lower()
    if not target:
        return True
    hint = safe_folder_slug(gap_folder_hint(gap_id)).strip().lower()
    if hint and (hint == target or hint.startswith(target) or target.startswith(hint)):
        return True
    path_key = (media_path or "").replace("\\", "/").lower()
    if target and target in path_key:
        return True
    # Leerzeichen-Variante im Pfad
    spaced = (folder_name or "").strip().lower().replace("_", " ")
    if spaced and spaced in path_key.replace("_", " "):
        return True
    return False


def load_sibling_export_ready_supplements(
    project: Project,
) -> list[tuple[str, StockCandidate]]:
    """``export_ready`` Accepted aus anderen Sprachen (Run-ID egal)."""
    results: list[tuple[str, StockCandidate]] = []
    for lang, lang_dir in iter_sibling_language_dirs(project):
        path = (
            lang_dir
            / VOICEOVER_GENERATION_SUBDIR
            / STOCK_SUBDIR
            / ACCEPTED_SUPPLEMENTS_FILENAME
        )
        doc = load_model(path, AcceptedSupplementsDocument)
        if doc is None:
            continue
        for candidate in doc.supplements:
            refreshed = refresh_supplement_validation(candidate)
            if refreshed.media_validation_status != STATUS_EXPORT_READY:
                continue
            local = str(refreshed.local_media_path or "").strip()
            if not local or is_http_url(local):
                continue
            if not Path(local).expanduser().is_file():
                continue
            results.append((lang, refreshed))
    return results


def sibling_supplement_rows_for_cut_plan(
    project: Project,
    *,
    folder_name: str | None = None,
    existing_asset_ids: Iterable[str] | None = None,
    existing_files: Iterable[str] | None = None,
) -> list[dict[str, Any]]:
    """Prompt-Rows (wie slim Local Assets) aus Sibling-Accepted-Supplements."""
    seen_ids = {
        str(x).strip() for x in (existing_asset_ids or []) if str(x).strip()
    }
    seen_files = {
        str(x).strip().lower() for x in (existing_files or []) if str(x).strip()
    }
    rows: list[dict[str, Any]] = []
    for lang, candidate in load_sibling_export_ready_supplements(project):
        local = str(candidate.local_media_path or "").strip()
        asset_id = str(candidate.candidate_id or "").strip()
        if not asset_id or not local:
            continue
        if folder_name and not folder_matches_supplement(
            folder_name, gap_id=str(candidate.gap_id or ""), media_path=local
        ):
            continue
        file_name = Path(local).name
        file_key = file_name.lower()
        if asset_id in seen_ids or file_key in seen_files:
            continue
        seen_ids.add(asset_id)
        seen_files.add(file_key)
        media = str(candidate.media_type or "video").strip().lower() or "video"
        if media == "photo":
            media = "image"
        folder_hint = gap_folder_hint(str(candidate.gap_id or ""))
        folder_label = (folder_name or folder_hint or "").replace("_", " ").strip()
        title = (candidate.title or candidate.attribution or file_name).strip()
        description = f"[accepted {lang}] {title}".strip()
        rows.append(
            {
                "local_asset_id": asset_id,
                "asset_id": asset_id,
                "folder": folder_label,
                "file": file_name,
                "duration_seconds": candidate.duration_seconds,
                "media_type": media,
                "description": description,
                "source": "cross_language_accepted_supplement",
                "source_language": lang,
                "local_media_path": local,
            }
        )
    return rows
