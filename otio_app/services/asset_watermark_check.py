"""Prüfschritt: kommerzielle Stock-Wasserzeichen in Asset-Frames.

Läuft vor der teuren v3-Beschreibung. Getroffene Assets gelten nicht als
analysiert und landen in einer Review-Datei zur manuellen Prüfung.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Optional

from otio_app.analysis_models import AssetMediaAnalysis
from otio_app.models import Project
from otio_app.services.gemini_client import (
    GeminiNotConfiguredError,
    generate_text_from_image_frames,
    is_transient_api_error,
)

WATERMARK_CHECK_VERSION = "stock-wm-v1"
WATERMARK_BLOCK_ERROR = "Wasserzeichen: manuell prüfen"

WATERMARK_REVIEW_JSON_NAME = "watermark_review.json"
WATERMARK_REVIEW_TXT_NAME = "watermark_review.txt"

_ALLOWED_PROVIDERS = frozenset(
    {
        "adobe_stock",
        "shutterstock",
        "getty",
        "istock",
        "dreamstime",
        "alamy",
        "pond5",
        "depositphotos",
        "123rf",
        "other_stock",
    }
)

_PROVIDER_ALIASES = {
    "adobe": "adobe_stock",
    "adobe stock": "adobe_stock",
    "adobe_stock": "adobe_stock",
    "adobestock": "adobe_stock",
    "shutterstock": "shutterstock",
    "getty": "getty",
    "getty images": "getty",
    "gettyimages": "getty",
    "istock": "istock",
    "istockphoto": "istock",
    "i stock": "istock",
    "dreamstime": "dreamstime",
    "alamy": "alamy",
    "pond5": "pond5",
    "pond 5": "pond5",
    "depositphotos": "depositphotos",
    "deposit photos": "depositphotos",
    "123rf": "123rf",
    "123 rf": "123rf",
    "other": "other_stock",
    "other_stock": "other_stock",
    "stock": "other_stock",
}

_PROVIDER_MARKERS: tuple[tuple[str, str], ...] = (
    ("adobe stock", "adobe_stock"),
    ("adobestock", "adobe_stock"),
    ("shutterstock", "shutterstock"),
    ("getty images", "getty"),
    ("gettyimages", "getty"),
    ("istockphoto", "istock"),
    ("istock", "istock"),
    ("dreamstime", "dreamstime"),
    ("alamy", "alamy"),
    ("pond5", "pond5"),
    ("depositphotos", "depositphotos"),
    ("123rf", "123rf"),
)

_PROVIDER_LABELS = {
    "adobe_stock": "Adobe Stock",
    "shutterstock": "Shutterstock",
    "getty": "Getty Images",
    "istock": "iStock",
    "dreamstime": "Dreamstime",
    "alamy": "Alamy",
    "pond5": "Pond5",
    "depositphotos": "Depositphotos",
    "123rf": "123RF",
    "other_stock": "Stock (sonstige)",
}

STOCK_WATERMARK_PROMPT = (
    "Du prüfst Standbilder einer Videodatei auf kommerzielle Stock-Vorschau-Wasserzeichen.\n"
    "\n"
    "watermark=true NUR bei einem deutlichen kommerziellen Preview-Overlay, typischerweise:\n"
    "- groß, oft zentriert, halbtransparent über dem Bild\n"
    "- Anbieter-Logo plus Schriftzug\n"
    "Bekannte Fälle: Adobe Stock (weißes Adobe-A im Quadrat und Text \"Adobe Stock\"), "
    "Shutterstock, Getty Images, iStock, Dreamstime, Alamy, Pond5, Depositphotos, 123RF.\n"
    "\n"
    "watermark=false bei:\n"
    "- kleinem Sender-/Kanal-/Produktions-Logo in einer Ecke\n"
    "- Untertiteln, Bauchbinden, Timecode, Copyright-Zeile am Rand\n"
    "- Text oder Logos, die zum gefilmten Motiv gehören (Schilder, Gebäude, Kleidung)\n"
    "- unleserlichen Andeutungen oder Unsicherheit\n"
    "Im Zweifel: watermark=false.\n"
    "\n"
    "Antworte NUR als JSON-Objekt:\n"
    '{"watermark": false, "provider": "", "note": ""}\n'
    "provider nur wenn watermark=true, eines von: "
    "adobe_stock|shutterstock|getty|istock|dreamstime|alamy|pond5|depositphotos|123rf|other_stock.\n"
    "note: ein kurzer deutscher Satz, was sichtbar ist.\n"
)


@dataclass(frozen=True)
class StockWatermarkCheckResult:
    blocked: bool
    provider: str = ""
    note: str = ""
    failed_open: bool = False
    raw_response: str = ""


@dataclass(frozen=True)
class WatermarkReviewItem:
    folder: str
    filename: str
    path: str
    provider: str = ""
    note: str = ""
    detected_at: str = ""


def watermark_check_is_current(entry: AssetMediaAnalysis | None) -> bool:
    """True, wenn die Stock-Wasserzeichen-Prüfung zur aktuellen Version passt und nicht sperrt."""
    if entry is None:
        return False
    if entry.watermark_blocked is True:
        return False
    return (entry.watermark_check_version or "").strip() == WATERMARK_CHECK_VERSION


def provider_label(provider: str) -> str:
    key = (provider or "").strip().lower()
    return _PROVIDER_LABELS.get(key, provider.strip() or "unbekannt")


def watermark_review_json_path(project: Project) -> Path:
    return project.work_dir_path / WATERMARK_REVIEW_JSON_NAME


def watermark_review_txt_path(project: Project) -> Path:
    return project.work_dir_path / WATERMARK_REVIEW_TXT_NAME


def format_watermark_review_banner(count: int, txt_path: Path) -> str:
    return (
        f"**{count} Asset(s) mit Stock-Wasserzeichen** — Analyse nicht übernommen. "
        f"Bitte manuell prüfen in `{txt_path}`."
    )


def _as_bool(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)) and value in (0, 1):
        return bool(value)
    if isinstance(value, str):
        text = value.strip().lower()
        if text in {"true", "yes", "ja", "1"}:
            return True
        if text in {"false", "no", "nein", "0", ""}:
            return False
    return None


def _normalize_provider(value: str) -> str:
    text = " ".join((value or "").strip().lower().replace("-", " ").replace("_", " ").split())
    if not text:
        return ""
    alias = _PROVIDER_ALIASES.get(text) or _PROVIDER_ALIASES.get(text.replace(" ", "_"))
    if alias:
        return alias
    compact = text.replace(" ", "")
    alias = _PROVIDER_ALIASES.get(compact)
    if alias:
        return alias
    return ""


def _provider_from_text(text: str) -> str:
    blob = (text or "").lower()
    if not blob:
        return ""
    for marker, provider in _PROVIDER_MARKERS:
        if marker in blob:
            return provider
    return ""


def parse_stock_watermark_response(raw: str) -> StockWatermarkCheckResult:
    """Parst die Vision-Antwort. Unlesbares JSON öffnet fail-open (nicht blockieren)."""
    text = (raw or "").strip()
    if not text:
        return StockWatermarkCheckResult(blocked=False, failed_open=True, raw_response=raw)

    from otio_app.services.gemini_client import _extract_json

    try:
        payload = _extract_json(text)
    except (json.JSONDecodeError, TypeError, ValueError):
        return StockWatermarkCheckResult(
            blocked=False,
            failed_open=True,
            note="Wasserzeichen-Antwort nicht lesbar",
            raw_response=raw,
        )
    if not isinstance(payload, dict):
        return StockWatermarkCheckResult(
            blocked=False,
            failed_open=True,
            note="Wasserzeichen-Antwort ist kein Objekt",
            raw_response=raw,
        )

    flagged = _as_bool(payload.get("watermark"))
    if flagged is None:
        return StockWatermarkCheckResult(
            blocked=False,
            failed_open=True,
            note="Feld watermark fehlt oder ist ungültig",
            raw_response=raw,
        )

    note = str(payload.get("note") or "").strip()
    provider = _normalize_provider(str(payload.get("provider") or ""))
    if not provider:
        provider = _provider_from_text(note) or _provider_from_text(text)
    if flagged and provider and provider not in _ALLOWED_PROVIDERS:
        provider = "other_stock"
    if flagged and not provider:
        provider = "other_stock"

    return StockWatermarkCheckResult(
        blocked=bool(flagged),
        provider=provider if flagged else "",
        note=note,
        failed_open=False,
        raw_response=raw,
    )


def stock_watermark_from_v3_defects(
    defect_items: Iterable[Any] | None,
    defects_text: str | None = "",
) -> StockWatermarkCheckResult | None:
    """Zweite Sicherung: v3-Defekte mit klarem Stock-Wasserzeichen."""
    blobs: list[str] = []
    watermark_typed = False
    for item in defect_items or []:
        if item is None:
            continue
        if isinstance(item, dict):
            dtype = str(item.get("type") or "")
            note = str(item.get("note") or "")
        else:
            dtype = str(getattr(item, "type", "") or "")
            note = str(getattr(item, "note", "") or "")
        combined = f"{dtype} {note}".strip()
        if not combined:
            continue
        if dtype.strip().lower() == "watermark" or "watermark" in note.lower() or "wasserzeichen" in note.lower():
            watermark_typed = True
        blobs.append(combined)
    if (defects_text or "").strip():
        blob = defects_text.strip()
        blobs.append(blob)
        lower = blob.lower()
        if "watermark" in lower or "wasserzeichen" in lower:
            watermark_typed = True

    text = " ".join(blobs)
    provider = _provider_from_text(text)
    if not provider:
        return None
    if not watermark_typed and provider != "adobe_stock":
        # Ohne expliziten Wasserzeichen-Defekt nur Adobe-Stock-Schriftzug werten.
        if "adobe stock" not in text.lower() and "adobestock" not in text.lower():
            return None
    note = text.strip()[:240]
    return StockWatermarkCheckResult(
        blocked=True,
        provider=provider,
        note=note or f"{provider_label(provider)}-Wasserzeichen in der v3-Analyse",
    )


def check_frames_for_stock_watermark(
    frame_paths: list[Path],
    *,
    media_name: str,
    folder_name: str,
    model: Optional[str] = None,
) -> StockWatermarkCheckResult:
    """Vision-Check. Transiente/unlesbare Fehler: fail-open (nicht blockieren)."""
    del media_name, folder_name
    if not frame_paths:
        return StockWatermarkCheckResult(
            blocked=False,
            failed_open=True,
            note="Keine Frames für die Wasserzeichen-Prüfung",
        )
    try:
        raw = generate_text_from_image_frames(
            STOCK_WATERMARK_PROMPT,
            frame_paths,
            model=model,
        )
    except GeminiNotConfiguredError:
        raise
    except Exception as exc:  # noqa: BLE001
        note = str(exc).strip() or exc.__class__.__name__
        if is_transient_api_error(exc):
            note = f"vorübergehender API-Fehler: {note}"
        return StockWatermarkCheckResult(
            blocked=False,
            failed_open=True,
            note=note,
        )
    return parse_stock_watermark_response(raw)


def _review_key(path: str | Path) -> str:
    candidate = Path(path)
    try:
        if candidate.exists():
            return str(candidate.resolve())
    except OSError:
        pass
    return str(candidate)


def _same_review_item(item: WatermarkReviewItem, media_path: Path, folder: str) -> bool:
    if _review_key(item.path) == _review_key(media_path):
        return True
    return item.folder == folder and item.filename == media_path.name


def _item_from_dict(raw: dict[str, Any]) -> WatermarkReviewItem | None:
    path = str(raw.get("path") or "").strip()
    filename = str(raw.get("filename") or "").strip() or (Path(path).name if path else "")
    folder = str(raw.get("folder") or "").strip()
    if not path and not filename:
        return None
    return WatermarkReviewItem(
        folder=folder,
        filename=filename,
        path=path or filename,
        provider=str(raw.get("provider") or "").strip(),
        note=str(raw.get("note") or "").strip(),
        detected_at=str(raw.get("detected_at") or "").strip(),
    )


def load_watermark_review_items(project: Project) -> list[WatermarkReviewItem]:
    path = watermark_review_json_path(project)
    if not path.is_file():
        return []
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        return []
    if not isinstance(payload, dict):
        return []
    items: list[WatermarkReviewItem] = []
    for raw in payload.get("items") or []:
        if not isinstance(raw, dict):
            continue
        item = _item_from_dict(raw)
        if item is not None:
            items.append(item)
    return items


def _write_watermark_review(project: Project, items: list[WatermarkReviewItem]) -> None:
    work_dir = project.work_dir_path
    work_dir.mkdir(parents=True, exist_ok=True)
    now = datetime.now(timezone.utc)
    stamp = now.replace(microsecond=0).isoformat().replace("+00:00", "Z")
    payload = {
        "version": 1,
        "updated_at": stamp,
        "items": [
            {
                "folder": item.folder,
                "filename": item.filename,
                "path": item.path,
                "provider": item.provider,
                "note": item.note,
                "detected_at": item.detected_at or stamp,
            }
            for item in items
        ],
    }
    json_path = watermark_review_json_path(project)
    json_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    lines = [
        "Stock-Wasserzeichen — manuelle Prüfung",
        "======================================",
        f"Aktualisiert: {stamp}",
        f"Anzahl: {len(items)}",
        "",
    ]
    if not items:
        lines.append("Keine Einträge.")
    else:
        for index, item in enumerate(items, start=1):
            lines.append(f"{index}. {item.folder} / {item.filename}")
            lines.append(f"   Anbieter: {provider_label(item.provider)}")
            lines.append(f"   Pfad: {item.path}")
            if item.note:
                lines.append(f"   Hinweis: {item.note}")
            lines.append("")
    watermark_review_txt_path(project).write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")


def upsert_watermark_review_item(
    project: Project,
    *,
    folder: str,
    media_path: Path,
    provider: str = "",
    note: str = "",
) -> None:
    items = load_watermark_review_items(project)
    now = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    updated = WatermarkReviewItem(
        folder=folder,
        filename=media_path.name,
        path=str(media_path),
        provider=provider,
        note=note,
        detected_at=now,
    )
    kept: list[WatermarkReviewItem] = [
        item for item in items if not _same_review_item(item, media_path, folder)
    ]
    kept.append(updated)
    kept.sort(key=lambda item: (item.folder.lower(), item.filename.lower(), item.path))
    _write_watermark_review(project, kept)


def remove_watermark_review_item(
    project: Project,
    media_path: Path,
    *,
    folder: str = "",
) -> None:
    items = load_watermark_review_items(project)
    remaining = [
        item for item in items if not _same_review_item(item, media_path, folder)
    ]
    if len(remaining) == len(items) and not watermark_review_json_path(project).is_file():
        return
    _write_watermark_review(project, remaining)


def _watermark_review_source_present(project: Project, item: WatermarkReviewItem) -> bool:
    """True, wenn die gemeldete Datei noch im Ordner, als iCloud-Platzhalter oder Clean-Kopie liegt."""
    from otio_app.project_layout import clean_output_path_for_media
    from otio_app.services.clean_media import clean_file_is_present
    from otio_app.services.media_inventory_cache import media_has_icloud_placeholder
    from otio_app.services.media_utils import list_media_files

    filename = (item.filename or Path(item.path).name).strip()
    if not filename:
        return False
    folder = (item.folder or "").strip()
    media_path = (
        project.project_root_path / folder / filename
        if folder
        else Path(item.path)
    )
    try:
        if media_path.is_file():
            return True
    except OSError:
        pass
    parent = media_path.parent
    try:
        listed = {path.name.casefold() for path in list_media_files(parent)}
    except OSError:
        listed = set()
    if filename.casefold() in listed:
        return True
    if media_has_icloud_placeholder(media_path):
        return True
    if not folder:
        return False
    return clean_file_is_present(
        clean_output_path_for_media(project.work_dir_path, folder, media_path)
    )


def prune_stale_watermark_review(project: Project) -> int:
    """Entfernt Review-Zeilen, deren Originaldatei ersetzt oder gelöscht wurde.

    ``Asset12.mp4`` und ``Asset00012.mov`` gelten als verschiedene Dateien —
    ein Neudownload räumt den alten Wasserzeichen-Eintrag.
    """
    items = load_watermark_review_items(project)
    if not items:
        return 0
    kept = [item for item in items if _watermark_review_source_present(project, item)]
    dropped = len(items) - len(kept)
    if dropped:
        _write_watermark_review(project, kept)
    return dropped
