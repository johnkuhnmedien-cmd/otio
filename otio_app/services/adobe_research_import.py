"""Research-Excel → Adobe-Stock lizenzieren/herunterladen (vor Projektanlage).

Erwartetes Excel-Layout (Research Template):
- Zeile 1: Kapitel-Überschriften alle 3 Spalten (1, 4, 7, …)
- Zeile 2: Count | Asset ID | Link (wiederholt)
- ab Zeile 3: laufende Nummer | Adobe Content-ID | Adobe-URL
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import BinaryIO, Callable, Iterable

from otio_app.defaults import (
    ADOBE_STOCK_LICENSE_TYPE_STANDARD,
    ADOBE_STOCK_LICENSE_TYPE_VIDEO_4K,
    ADOBE_STOCK_LICENSE_TYPE_VIDEO_HD,
    ADOBE_STOCK_MIN_DOWNLOAD_BYTES,
    ADOBE_STOCK_VIDEO_4K_MAX_BYTES,
)
from otio_app.services.adobe_stock_oauth import get_adobe_access_token
from otio_app.services.api_keys import get_api_key
from otio_app.services.supplement_sources.adobe_stock import (
    AdobeAssetTooLargeError,
    AdobeContentUnavailableError,
    AdobeStockAdapter,
    is_full_adobe_download_url,
)

__all__ = [
    "AdobeResearchAsset",
    "AdobeResearchAssetStatus",
    "AdobeResearchChapter",
    "AdobeResearchChapterStatus",
    "AdobeResearchImportBoard",
    "AdobeResearchImportPlan",
    "AdobeResearchImportProgress",
    "AdobeResearchImportResult",
    "build_research_import_board",
    "cleanup_media_folder_json",
    "download_research_import",
    "format_asset_stem",
    "parse_research_excel",
    "persist_research_import_board",
    "sanitize_folder_name",
]

_INVALID_FS_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_WHITESPACE = re.compile(r"\s+")
_MANIFEST_NAME = "adobe_research_import_manifest.json"
_BOARD_NAME = "adobe_research_import_board.json"
# Bulk-Lizenzierung: Pause zwischen Assets / Lizenzversuchen gegen Rate-Limits.
_ASSET_PAUSE_SECONDS = 0.8
_LICENSE_RETRY_PAUSE_SECONDS = 0.45

STATUS_DOWNLOADED = "downloaded"
STATUS_OPEN = "open"
STATUS_ERROR = "error"
STATUS_DOWNLOADING = "downloading"
STATUS_CANCELLED = "cancelled"
STATUS_SKIPPED = "skipped"
STATUS_UNAVAILABLE = "unavailable"


@dataclass(frozen=True)
class AdobeResearchAsset:
    asset_id: str
    link: str = ""
    media_hint: str = ""  # "video" | "image" | ""
    row_number: int = 0
    column_block: int = 0


@dataclass(frozen=True)
class AdobeResearchChapter:
    title: str
    folder_name: str
    assets: tuple[AdobeResearchAsset, ...] = ()
    source_column: int = 1

    @property
    def asset_count(self) -> int:
        return len(self.assets)


@dataclass(frozen=True)
class AdobeResearchImportPlan:
    sheet_name: str
    chapters: tuple[AdobeResearchChapter, ...] = ()

    @property
    def chapter_count(self) -> int:
        return len(self.chapters)

    @property
    def asset_count(self) -> int:
        return sum(ch.asset_count for ch in self.chapters)


@dataclass
class AdobeResearchImportItemResult:
    chapter_title: str
    folder_name: str
    asset_id: str
    status: str  # downloaded | skipped | error | cancelled
    local_path: str = ""
    message: str = ""
    license: str = ""


@dataclass
class AdobeResearchImportResult:
    target_root: str
    items: list[AdobeResearchImportItemResult] = field(default_factory=list)
    manifest_path: str = ""
    cancelled: bool = False

    @property
    def downloaded(self) -> int:
        return sum(1 for item in self.items if item.status == STATUS_DOWNLOADED)

    @property
    def skipped(self) -> int:
        return sum(1 for item in self.items if item.status == STATUS_SKIPPED)

    @property
    def errors(self) -> int:
        return sum(1 for item in self.items if item.status == STATUS_ERROR)

    @property
    def unavailable(self) -> int:
        return sum(1 for item in self.items if item.status == STATUS_UNAVAILABLE)

    @property
    def cancelled_count(self) -> int:
        return sum(1 for item in self.items if item.status == STATUS_CANCELLED)


@dataclass(frozen=True)
class AdobeResearchAssetStatus:
    chapter_title: str
    folder_name: str
    asset_id: str
    link: str = ""
    status: str = STATUS_OPEN
    local_path: str = ""
    license: str = ""
    message: str = ""


@dataclass(frozen=True)
class AdobeResearchChapterStatus:
    title: str
    folder_name: str
    assets: tuple[AdobeResearchAssetStatus, ...] = ()

    @property
    def total(self) -> int:
        return len(self.assets)

    @property
    def downloaded(self) -> int:
        return sum(1 for a in self.assets if a.status == STATUS_DOWNLOADED)

    @property
    def open_count(self) -> int:
        return sum(1 for a in self.assets if a.status in {STATUS_OPEN, STATUS_CANCELLED})

    @property
    def error_count(self) -> int:
        return sum(1 for a in self.assets if a.status == STATUS_ERROR)

    @property
    def downloading_count(self) -> int:
        return sum(1 for a in self.assets if a.status == STATUS_DOWNLOADING)


@dataclass(frozen=True)
class AdobeResearchImportBoard:
    sheet_name: str
    target_root: str
    chapters: tuple[AdobeResearchChapterStatus, ...] = ()

    @property
    def total(self) -> int:
        return sum(ch.total for ch in self.chapters)

    @property
    def downloaded(self) -> int:
        return sum(ch.downloaded for ch in self.chapters)

    @property
    def open_count(self) -> int:
        return sum(ch.open_count for ch in self.chapters)

    @property
    def error_count(self) -> int:
        return sum(ch.error_count for ch in self.chapters)


@dataclass(frozen=True)
class AdobeResearchImportProgress:
    done: int
    total: int
    folder_name: str
    asset_id: str
    chapter_title: str = ""
    status: str = STATUS_DOWNLOADING
    message: str = ""
    fraction: float = 0.0


def sanitize_folder_name(title: str) -> str:
    """Dateisystemtauglicher Ordnername aus Excel-Überschrift."""
    text = (title or "").strip()
    text = text.replace("’", "'").replace("‘", "'").replace("–", "-").replace("—", "-")
    text = _INVALID_FS_CHARS.sub("-", text)
    text = _WHITESPACE.sub(" ", text).strip(" .")
    return text or "Untitled"


def format_asset_stem(folder_name: str, index: int) -> str:
    """z. B. Dublin_Asset_01"""
    return f"{folder_name}_Asset_{index:02d}"


def _as_asset_id(value: object) -> str | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return str(value) if value > 0 else None
    if isinstance(value, float):
        if value.is_integer() and value > 0:
            return str(int(value))
        return None
    text = str(value).strip()
    if not text:
        return None
    if text.isdigit():
        return text
    # Excel manchmal "1.23456789E9"
    try:
        as_float = float(text.replace(",", ""))
    except ValueError:
        return None
    if as_float.is_integer() and as_float > 0:
        return str(int(as_float))
    return None


def _media_hint_from_link(link: str) -> str:
    lower = (link or "").lower()
    if "/images/" in lower or "/image/" in lower:
        return "image"
    if "/video/" in lower:
        return "video"
    return ""


def parse_research_excel(
    source: str | Path | bytes | BinaryIO,
    *,
    sheet_name: str | None = None,
) -> AdobeResearchImportPlan:
    """Parst Research-Template-Excel zu Kapitelblöcken."""
    try:
        from openpyxl import load_workbook
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError(
            "Paket 'openpyxl' fehlt — bitte `pip install openpyxl` ausführen."
        ) from exc

    if isinstance(source, bytes):
        from io import BytesIO

        source = BytesIO(source)
    wb = load_workbook(source, data_only=True, read_only=True)
    try:
        name = sheet_name or wb.sheetnames[0]
        if name not in wb.sheetnames:
            raise ValueError(f"Sheet '{name}' nicht gefunden. Vorhanden: {wb.sheetnames}")
        ws = wb[name]
        rows = list(ws.iter_rows(values_only=True))
    finally:
        wb.close()

    if len(rows) < 3:
        raise ValueError("Excel zu kurz — erwartet Zeile 1 Titel, Zeile 2 Header, ab Zeile 3 IDs.")

    header_row = rows[0]
    max_col = len(header_row)
    chapters: list[AdobeResearchChapter] = []

    col = 0  # 0-based
    while col < max_col:
        title_raw = header_row[col] if col < len(header_row) else None
        title = str(title_raw).strip() if title_raw is not None else ""
        if not title:
            col += 3
            continue

        assets: list[AdobeResearchAsset] = []
        seen_ids: set[str] = set()
        for row_index, row in enumerate(rows[2:], start=3):
            # Pad short rows
            id_col = col + 1
            link_col = col + 2
            asset_val = row[id_col] if id_col < len(row) else None
            link_val = row[link_col] if link_col < len(row) else None
            asset_id = _as_asset_id(asset_val)
            link = str(link_val).strip() if link_val is not None else ""
            if asset_id is None:
                # Manchmal steht Text in der ID-Spalte (Notiz) — überspringen.
                continue
            if asset_id in seen_ids:
                continue
            seen_ids.add(asset_id)
            assets.append(
                AdobeResearchAsset(
                    asset_id=asset_id,
                    link=link,
                    media_hint=_media_hint_from_link(link),
                    row_number=row_index,
                    column_block=col + 1,
                )
            )

        chapters.append(
            AdobeResearchChapter(
                title=title,
                folder_name=sanitize_folder_name(title),
                assets=tuple(assets),
                source_column=col + 1,
            )
        )
        col += 3

    if not chapters:
        raise ValueError(
            "Keine Kapitel gefunden. Erwartet: Überschrift in Zeile 1 alle 3 Spalten, "
            "Asset-IDs in der 2. Spalte jedes Blocks."
        )

    return AdobeResearchImportPlan(sheet_name=name, chapters=tuple(chapters))


def _manifest_records(manifest_path: Path) -> dict[str, dict]:
    if not manifest_path.is_file():
        return {}
    try:
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    found: dict[str, dict] = {}
    for item in payload.get("items") or []:
        aid = str(item.get("asset_id") or "").strip()
        if not aid:
            continue
        status = str(item.get("status") or STATUS_OPEN)
        if status == STATUS_SKIPPED:
            status = STATUS_DOWNLOADED
        found[aid] = {
            "local_path": str(item.get("local_path") or ""),
            "license": str(item.get("license") or ""),
            "message": str(item.get("message") or ""),
            "status": status,
        }
    return found


def _legacy_sidecar_records(target_root: Path) -> dict[str, dict]:
    """Einmalige Migration: alte *.adobe.json neben Medien lesen."""
    found: dict[str, dict] = {}
    if not target_root.is_dir():
        return found
    for path in target_root.rglob("*.adobe.json"):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        aid = str(payload.get("asset_id") or "").strip()
        if not aid:
            continue
        found[aid] = {
            "local_path": str(payload.get("local_path") or ""),
            "license": str(payload.get("license") or ""),
            "message": "",
            "status": STATUS_DOWNLOADED,
        }
    return found


def _downloaded_ids_from_records(records: dict[str, dict]) -> set[str]:
    ids: set[str] = set()
    for aid, record in records.items():
        status = str(record.get("status") or "")
        if status not in {STATUS_DOWNLOADED, STATUS_SKIPPED}:
            continue
        local = str(record.get("local_path") or "")
        if local and not Path(local).is_file():
            # Datei fehlt → als noch offen behandeln
            continue
        ids.add(aid)
    return ids


def cleanup_media_folder_json(target_root: str | Path) -> dict[str, int]:
    """Löscht Board/Manifest/*.adobe.json aus dem Medien-Zielordner (nicht aus data/)."""
    root = Path(target_root).expanduser().resolve()
    removed = {"sidecar": 0, "board": 0, "manifest": 0}
    if not root.is_dir():
        return removed
    board = root / _BOARD_NAME
    if board.is_file():
        board.unlink()
        removed["board"] = 1
    manifest = root / _MANIFEST_NAME
    if manifest.is_file():
        manifest.unlink()
        removed["manifest"] = 1
    for path in root.rglob("*.adobe.json"):
        try:
            path.unlink()
            removed["sidecar"] += 1
        except OSError:
            continue
    return removed


def build_research_import_board(
    plan: AdobeResearchImportPlan,
    target_root: str | Path | None,
    *,
    state_dir: str | Path | None = None,
    live_statuses: dict[str, dict] | None = None,
) -> AdobeResearchImportBoard:
    """Excel-Spiegel: pro Asset Downloaded / Open / Error (+ Live-Overrides).

    Fortschritt wird aus `state_dir` gelesen (Download-Projekt unter data/),
    nicht aus JSON neben den Mediendateien. Legacy-Sidecars im Zielordner
    werden nur noch als Fallback gelesen.
    """
    root = Path(target_root).expanduser().resolve() if target_root else Path()
    state = Path(state_dir).expanduser().resolve() if state_dir else None
    state_records = _manifest_records(state / _MANIFEST_NAME) if state else {}
    # Legacy: alte Dateien im Medienordner (Migration)
    legacy_root = _manifest_records(root / _MANIFEST_NAME) if target_root else {}
    legacy_sidecars = _legacy_sidecar_records(root) if target_root else {}
    live = live_statuses or {}

    chapters: list[AdobeResearchChapterStatus] = []
    for chapter in plan.chapters:
        assets: list[AdobeResearchAssetStatus] = []
        for asset in chapter.assets:
            record = (
                live.get(asset.asset_id)
                or state_records.get(asset.asset_id)
                or legacy_sidecars.get(asset.asset_id)
                or legacy_root.get(asset.asset_id)
            )
            if record:
                status = str(record.get("status") or STATUS_DOWNLOADED)
                if status == STATUS_SKIPPED:
                    status = STATUS_DOWNLOADED
                local_path = str(record.get("local_path") or "")
                if (
                    status == STATUS_DOWNLOADED
                    and local_path
                    and not Path(local_path).is_file()
                ):
                    status = STATUS_OPEN
                assets.append(
                    AdobeResearchAssetStatus(
                        chapter_title=chapter.title,
                        folder_name=chapter.folder_name,
                        asset_id=asset.asset_id,
                        link=asset.link,
                        status=status,
                        local_path=local_path if status == STATUS_DOWNLOADED else "",
                        license=str(record.get("license") or ""),
                        message=str(record.get("message") or ""),
                    )
                )
            else:
                assets.append(
                    AdobeResearchAssetStatus(
                        chapter_title=chapter.title,
                        folder_name=chapter.folder_name,
                        asset_id=asset.asset_id,
                        link=asset.link,
                        status=STATUS_OPEN,
                    )
                )
        chapters.append(
            AdobeResearchChapterStatus(
                title=chapter.title,
                folder_name=chapter.folder_name,
                assets=tuple(assets),
            )
        )
    return AdobeResearchImportBoard(
        sheet_name=plan.sheet_name,
        target_root=str(root) if target_root else "",
        chapters=tuple(chapters),
    )


def persist_research_import_board(
    board: AdobeResearchImportBoard,
    *,
    state_dir: str | Path | None = None,
) -> Path | None:
    """Schreibt Board-JSON nur in state_dir (nie in den Medienordner)."""
    if state_dir is None:
        return None
    root = Path(state_dir).expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema_version": "adobe-research-import-board-v1",
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "sheet_name": board.sheet_name,
        "target_root": board.target_root,
        "downloaded": board.downloaded,
        "open": board.open_count,
        "errors": board.error_count,
        "total": board.total,
        "chapters": [
            {
                "title": ch.title,
                "folder_name": ch.folder_name,
                "downloaded": ch.downloaded,
                "open": ch.open_count,
                "errors": ch.error_count,
                "total": ch.total,
                "assets": [asdict(a) for a in ch.assets],
            }
            for ch in board.chapters
        ],
    }
    path = root / _BOARD_NAME
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def _next_asset_index(folder: Path) -> int:
    """Nächster freier Asset_NN Index in einem Kapitelordner."""
    pattern = re.compile(r"_Asset_(\d+)\.[^.]+$", re.IGNORECASE)
    highest = 0
    if folder.is_dir():
        for path in folder.iterdir():
            if not path.is_file():
                continue
            match = pattern.search(path.name)
            if match:
                highest = max(highest, int(match.group(1)))
    return highest + 1


def _media_type_from_file_meta(meta: dict, *, hint: str = "") -> str:
    if hint in {"video", "image"}:
        return hint
    media_type_id = meta.get("media_type_id")
    try:
        media_type_id_i = int(media_type_id) if media_type_id is not None else 0
    except (TypeError, ValueError):
        media_type_id_i = 0
    from otio_app.defaults import ADOBE_STOCK_MEDIA_TYPE_ID_PHOTO, ADOBE_STOCK_MEDIA_TYPE_ID_VIDEO

    if media_type_id_i == ADOBE_STOCK_MEDIA_TYPE_ID_VIDEO:
        return "video"
    if media_type_id_i == ADOBE_STOCK_MEDIA_TYPE_ID_PHOTO:
        return "image"
    content_type = str(meta.get("content_type") or "").lower()
    if content_type.startswith("video/"):
        return "video"
    if content_type.startswith("image/"):
        return "image"
    return "video"


def _extension_for_purchase(purchase: dict, media_type: str) -> str:
    content_type = str(purchase.get("content_type") or "").lower()
    if "video" in content_type:
        return ".mp4"
    if "png" in content_type:
        return ".png"
    if "jpeg" in content_type or "jpg" in content_type:
        return ".jpg"
    return ".jpg" if media_type == "image" else ".mp4"


def _format_license_with_size(license_type: str, local_path: Path) -> str:
    """Lizenzlabel inkl. Dateigröße für Live-Log / Board."""
    try:
        mb = local_path.stat().st_size / (1024 * 1024)
    except OSError:
        return license_type
    return f"{license_type} · {mb:.0f} MB"


def _download_purchase_to_path(
    adapter: AdobeStockAdapter,
    purchase: dict,
    destination: Path,
    *,
    api_key: str,
    access_token: str,
    media_type: str,
    size: int | None,
    max_bytes: int | None,
) -> Path:
    url = str(purchase.get("url") or "")
    if not is_full_adobe_download_url(url):
        raise RuntimeError(f"Keine Voll-Download-URL (Comp/Wasserzeichen): {url[:160]}")
    local_path = destination.with_suffix(_extension_for_purchase(purchase, media_type))
    adapter._stream_download_to_file(
        url,
        local_path,
        api_key=api_key,
        access_token=access_token,
        size=size,
        max_bytes=max_bytes,
    )
    if not local_path.is_file() or local_path.stat().st_size < ADOBE_STOCK_MIN_DOWNLOAD_BYTES:
        local_path.unlink(missing_ok=True)
        raise RuntimeError("Download zu klein / ungültig.")
    # Sicherheitsnetz: auch wenn Content-Length fehlte / Stream-Abbruch
    # nicht greift, darf 4K die 600-MB-Grenze nicht behalten.
    if max_bytes is not None and local_path.stat().st_size > max_bytes:
        local_path.unlink(missing_ok=True)
        raise AdobeAssetTooLargeError(
            f"Download {local_path.name} überschreitet "
            f"{max_bytes / (1024 * 1024):.0f} MB-Grenze."
        )
    return local_path


def _license_and_download_to_path(
    adapter: AdobeStockAdapter,
    *,
    content_id: str,
    media_type: str,
    destination: Path,
    media_hint: str = "",
) -> tuple[Path, str]:
    """Lizenziert/lädt eine Content-ID — Foto + Video, inkl. bereits lizenziert.

    Reihenfolge:
    1) Files-API → echter Medientyp (Foto/Video), 404 → unavailable
    2) Content/Info: wenn bereits purchased + Voll-URL → Download
    3) Content/License (ohne Diagnose-Spam)
    4) LicenseHistory-Fallback für schon lizenzierte Assets
    """
    api_key = get_api_key("ADOBE_STOCK_API_KEY")
    access_token = get_adobe_access_token()
    if not api_key:
        raise PermissionError("ADOBE_STOCK_API_KEY fehlt.")
    if not access_token:
        raise PermissionError(
            "Kein Adobe Access-Token — bitte OAuth-Login nutzen oder "
            "ADOBE_STOCK_ACCESS_TOKEN setzen."
        )

    destination.parent.mkdir(parents=True, exist_ok=True)

    try:
        meta = adapter.lookup_file_metadata(content_id, api_key)
        resolved_type = _media_type_from_file_meta(meta, hint=media_hint or media_type)
    except AdobeContentUnavailableError:
        raise
    except Exception:
        resolved_type = media_hint if media_hint in {"video", "image"} else media_type
        meta = {}

    if resolved_type == "image":
        pending: list[tuple[str, int | None, int | None]] = [
            (ADOBE_STOCK_LICENSE_TYPE_STANDARD, None, None)
        ]
    else:
        pending = [
            (
                ADOBE_STOCK_LICENSE_TYPE_VIDEO_4K,
                2160,
                ADOBE_STOCK_VIDEO_4K_MAX_BYTES,
            ),
            (ADOBE_STOCK_LICENSE_TYPE_VIDEO_HD, 1080, None),
        ]

    attempt_errors: list[str] = []
    tried: set[str] = set()
    first = True
    while pending:
        license_type, size, max_bytes = pending.pop(0)
        if license_type in tried:
            continue
        tried.add(license_type)
        if not first:
            time.sleep(_LICENSE_RETRY_PAUSE_SECONDS)
        first = False

        # Bereits lizenziert? Content/Info zuerst (kein neuer Kauf nötig).
        info = adapter.content_info_purchase(
            content_id, license_type, api_key, access_token
        )
        info_state = str(info.get("state") or "")
        info_url = str(info.get("url") or "")
        if info_state in {"purchased", "just_purchased"} and is_full_adobe_download_url(info_url):
            try:
                path = _download_purchase_to_path(
                    adapter,
                    info,
                    destination,
                    api_key=api_key,
                    access_token=access_token,
                    media_type=resolved_type,
                    size=size,
                    max_bytes=max_bytes,
                )
                used = str(info.get("license") or license_type)
                if max_bytes is not None and any(">600MB" in e for e in attempt_errors):
                    used = f"{used} (nach 4K>600MB)"
                return path, _format_license_with_size(used, path)
            except AdobeAssetTooLargeError:
                attempt_errors.append(f"{license_type}: >600MB → Fallback HD")
                continue
            except Exception as exc:  # noqa: BLE001
                attempt_errors.append(f"{license_type}: Info-Download {exc}")

        try:
            purchase = adapter._license_asset(
                content_id,
                license_type,
                api_key,
                access_token,
                diagnose=False,
            )
        except AdobeContentUnavailableError:
            raise
        except Exception as exc:  # noqa: BLE001
            msg = str(exc)
            # Falscher Medientyp → andere Lizenzfamilie nachziehen
            if "does not match type of content" in msg:
                if resolved_type == "video" and ADOBE_STOCK_LICENSE_TYPE_STANDARD not in tried:
                    pending.append((ADOBE_STOCK_LICENSE_TYPE_STANDARD, None, None))
                if resolved_type == "image":
                    if ADOBE_STOCK_LICENSE_TYPE_VIDEO_4K not in tried:
                        pending.append(
                            (
                                ADOBE_STOCK_LICENSE_TYPE_VIDEO_4K,
                                2160,
                                ADOBE_STOCK_VIDEO_4K_MAX_BYTES,
                            )
                        )
                    if ADOBE_STOCK_LICENSE_TYPE_VIDEO_HD not in tried:
                        pending.append((ADOBE_STOCK_LICENSE_TYPE_VIDEO_HD, 1080, None))
            attempt_errors.append(f"{license_type}: {exc}")
            continue

        try:
            path = _download_purchase_to_path(
                adapter,
                purchase,
                destination,
                api_key=api_key,
                access_token=access_token,
                media_type=resolved_type,
                size=size,
                max_bytes=max_bytes,
            )
            used = license_type
            if license_type == ADOBE_STOCK_LICENSE_TYPE_VIDEO_HD and any(
                ">600MB" in e for e in attempt_errors
            ):
                used = "Video_HD (4K>600MB)"
            return path, _format_license_with_size(used, path)
        except AdobeAssetTooLargeError:
            attempt_errors.append(f"{license_type}: >600MB → Fallback HD")
            continue
        except Exception as exc:  # noqa: BLE001
            attempt_errors.append(f"{license_type}: Download {exc}")
            continue

    # Bereits früher lizenziert, aber Content/License liefert nur Comp/cancelled?
    history = adapter.find_license_history_download(content_id, api_key, access_token)
    if history:
        try:
            hist_type = (
                "image"
                if "image" in str(history.get("content_type") or "").lower()
                else resolved_type
            )
            hist_license = str(history.get("license") or "")
            if hist_license == ADOBE_STOCK_LICENSE_TYPE_VIDEO_HD:
                size, max_bytes = 1080, None
            elif hist_type == "video":
                # Auch ohne klares Video_4K-Label: History-Videos >600MB → HD.
                size, max_bytes = 2160, ADOBE_STOCK_VIDEO_4K_MAX_BYTES
            else:
                size, max_bytes = None, None
            path = _download_purchase_to_path(
                adapter,
                history,
                destination,
                api_key=api_key,
                access_token=access_token,
                media_type=hist_type,
                size=size,
                max_bytes=max_bytes,
            )
            return path, _format_license_with_size(hist_license or "history", path)
        except AdobeAssetTooLargeError:
            attempt_errors.append("LicenseHistory: 4K>600MB → versuche Video_HD")
            try:
                purchase = adapter._license_asset(
                    content_id,
                    ADOBE_STOCK_LICENSE_TYPE_VIDEO_HD,
                    api_key,
                    access_token,
                    diagnose=False,
                )
                path = _download_purchase_to_path(
                    adapter,
                    purchase,
                    destination,
                    api_key=api_key,
                    access_token=access_token,
                    media_type=resolved_type,
                    size=1080,
                    max_bytes=None,
                )
                return path, _format_license_with_size("Video_HD (4K>600MB)", path)
            except Exception as exc2:  # noqa: BLE001
                attempt_errors.append(f"Video_HD after history: {exc2}")
        except Exception as exc:  # noqa: BLE001
            attempt_errors.append(f"LicenseHistory: {exc}")

    detail = " | ".join(attempt_errors) if attempt_errors else "unbekannter Fehler"
    hint = ""
    if any("cancelled" in e for e in attempt_errors) or any("Comp" in e for e in attempt_errors):
        hint = (
            " Hinweis: API meldet oft nur cct_pro_unlimited_images — "
            "Videos brauchen Video-Entitlement; OAuth ggf. mit dem Unlimited-Konto erneut."
        )
    raise RuntimeError(
        f"Adobe-Download fehlgeschlagen für Content-ID {content_id} "
        f"(media_type={resolved_type}, meta_content_type={meta.get('content_type') or '—'}). "
        f"Versuche: {detail}.{hint}"
    )


def download_research_import(
    plan: AdobeResearchImportPlan,
    target_root: str | Path,
    *,
    state_dir: str | Path | None = None,
    chapter_titles: Iterable[str] | None = None,
    skip_existing_ids: bool = True,
    progress_callback: Callable[[AdobeResearchImportProgress], None] | None = None,
    should_stop: Callable[[], bool] | None = None,
    live_status_callback: Callable[[dict[str, dict]], None] | None = None,
) -> AdobeResearchImportResult:
    """Lizenzieren + Download in Zielordner/{Kapitel}/{Kapitel}_Asset_NN.ext.

    Fortschritt/Manifest landen in `state_dir` (Download-Projekt unter data/),
    nicht als JSON neben den Medien. `should_stop` bricht kooperativ zwischen
    Assets ab.
    """
    root = Path(target_root).expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)
    state_path = Path(state_dir).expanduser().resolve() if state_dir else None
    if state_path is not None:
        state_path.mkdir(parents=True, exist_ok=True)

    selected = None
    if chapter_titles is not None:
        selected = {str(t).strip() for t in chapter_titles if str(t).strip()}

    adapter = AdobeStockAdapter()
    readiness = adapter.readiness()
    if not readiness.acquire_enabled:
        raise PermissionError(
            readiness.message
            or "Adobe Stock ist nicht für Lizenzierung/Download konfiguriert."
        )

    result = AdobeResearchImportResult(target_root=str(root))
    chapters = [
        ch
        for ch in plan.chapters
        if selected is None or ch.title in selected or ch.folder_name in selected
    ]
    total = sum(ch.asset_count for ch in chapters)
    done = 0
    live_statuses: dict[str, dict] = {}
    stopped = False

    prior_records: dict[str, dict] = {}
    if state_path is not None:
        prior_records.update(_manifest_records(state_path / _MANIFEST_NAME))
    prior_records.update(_legacy_sidecar_records(root))
    prior_records.update(_manifest_records(root / _MANIFEST_NAME))
    already_global = _downloaded_ids_from_records(prior_records) if skip_existing_ids else set()

    def _emit(
        *,
        folder_name: str,
        asset_id: str,
        chapter_title: str,
        status: str,
        message: str = "",
    ) -> None:
        if progress_callback is None:
            return
        fraction = (done / total) if total else 1.0
        progress_callback(
            AdobeResearchImportProgress(
                done=done,
                total=total,
                folder_name=folder_name,
                asset_id=asset_id,
                chapter_title=chapter_title,
                status=status,
                message=message,
                fraction=min(1.0, max(0.0, fraction)),
            )
        )

    def _publish_live() -> None:
        if live_status_callback is not None:
            live_status_callback(dict(live_statuses))
        board = build_research_import_board(
            plan,
            root,
            state_dir=state_path,
            live_statuses=live_statuses,
        )
        persist_research_import_board(board, state_dir=state_path)

    for chapter in chapters:
        if stopped:
            break
        folder = root / chapter.folder_name
        folder.mkdir(parents=True, exist_ok=True)
        already = set(already_global)
        next_index = _next_asset_index(folder)

        for asset in chapter.assets:
            if should_stop is not None and should_stop():
                stopped = True
                result.cancelled = True
                live_statuses[asset.asset_id] = {
                    "status": STATUS_CANCELLED,
                    "message": "Import gestoppt — noch offen",
                    "local_path": "",
                    "license": "",
                }
                result.items.append(
                    AdobeResearchImportItemResult(
                        chapter_title=chapter.title,
                        folder_name=chapter.folder_name,
                        asset_id=asset.asset_id,
                        status=STATUS_CANCELLED,
                        message="Import gestoppt — noch offen",
                    )
                )
                _publish_live()
                _emit(
                    folder_name=chapter.folder_name,
                    asset_id=asset.asset_id,
                    chapter_title=chapter.title,
                    status=STATUS_CANCELLED,
                    message="Import gestoppt",
                )
                break

            done += 1
            if asset.asset_id in already:
                live_statuses[asset.asset_id] = {
                    "status": STATUS_DOWNLOADED,
                    "message": "bereits vorhanden",
                    "local_path": "",
                    "license": "",
                }
                result.items.append(
                    AdobeResearchImportItemResult(
                        chapter_title=chapter.title,
                        folder_name=chapter.folder_name,
                        asset_id=asset.asset_id,
                        status=STATUS_SKIPPED,
                        message="bereits vorhanden",
                    )
                )
                _publish_live()
                _emit(
                    folder_name=chapter.folder_name,
                    asset_id=asset.asset_id,
                    chapter_title=chapter.title,
                    status=STATUS_SKIPPED,
                    message="bereits vorhanden",
                )
                continue

            # Pause vor echten License/Download-Calls (Rate-Limits).
            time.sleep(_ASSET_PAUSE_SECONDS)
            live_statuses[asset.asset_id] = {
                "status": STATUS_DOWNLOADING,
                "message": "läuft…",
                "local_path": "",
                "license": "",
            }
            _publish_live()
            _emit(
                folder_name=chapter.folder_name,
                asset_id=asset.asset_id,
                chapter_title=chapter.title,
                status=STATUS_DOWNLOADING,
                message="Lizenzieren & Download…",
            )

            stem = format_asset_stem(chapter.folder_name, next_index)
            dest = folder / stem  # Suffix setzt Download
            try:
                local_path, used_license = _license_and_download_to_path(
                    adapter,
                    content_id=asset.asset_id,
                    media_type=asset.media_hint or "video",
                    destination=dest,
                    media_hint=asset.media_hint,
                )
                live_statuses[asset.asset_id] = {
                    "status": STATUS_DOWNLOADED,
                    "message": "",
                    "local_path": str(local_path),
                    "license": used_license,
                }
                result.items.append(
                    AdobeResearchImportItemResult(
                        chapter_title=chapter.title,
                        folder_name=chapter.folder_name,
                        asset_id=asset.asset_id,
                        status=STATUS_DOWNLOADED,
                        local_path=str(local_path),
                        license=used_license,
                    )
                )
                already.add(asset.asset_id)
                already_global.add(asset.asset_id)
                next_index += 1
                _publish_live()
                _emit(
                    folder_name=chapter.folder_name,
                    asset_id=asset.asset_id,
                    chapter_title=chapter.title,
                    status=STATUS_DOWNLOADED,
                    message=used_license,
                )
            except AdobeContentUnavailableError as exc:
                live_statuses[asset.asset_id] = {
                    "status": STATUS_UNAVAILABLE,
                    "message": str(exc),
                    "local_path": "",
                    "license": "",
                }
                result.items.append(
                    AdobeResearchImportItemResult(
                        chapter_title=chapter.title,
                        folder_name=chapter.folder_name,
                        asset_id=asset.asset_id,
                        status=STATUS_UNAVAILABLE,
                        message=str(exc),
                    )
                )
                _publish_live()
                _emit(
                    folder_name=chapter.folder_name,
                    asset_id=asset.asset_id,
                    chapter_title=chapter.title,
                    status=STATUS_UNAVAILABLE,
                    message=str(exc),
                )
            except Exception as exc:  # noqa: BLE001
                live_statuses[asset.asset_id] = {
                    "status": STATUS_ERROR,
                    "message": str(exc),
                    "local_path": "",
                    "license": "",
                }
                result.items.append(
                    AdobeResearchImportItemResult(
                        chapter_title=chapter.title,
                        folder_name=chapter.folder_name,
                        asset_id=asset.asset_id,
                        status=STATUS_ERROR,
                        message=str(exc),
                    )
                )
                _publish_live()
                _emit(
                    folder_name=chapter.folder_name,
                    asset_id=asset.asset_id,
                    chapter_title=chapter.title,
                    status=STATUS_ERROR,
                    message=str(exc),
                )

    board = build_research_import_board(
        plan,
        root,
        state_dir=state_path,
        live_statuses=live_statuses,
    )
    persist_research_import_board(board, state_dir=state_path)

    # Manifest mergen: vorherige Downloads behalten + aktuelle Run-Items
    merged_by_id: dict[str, dict] = dict(prior_records)
    for item in result.items:
        merged_by_id[item.asset_id] = asdict(item)
    manifest_items = list(merged_by_id.values())
    manifest = {
        "schema_version": "adobe-research-import-v1",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "target_root": str(root),
        "sheet_name": plan.sheet_name,
        "downloaded": sum(
            1
            for item in manifest_items
            if str(item.get("status")) in {STATUS_DOWNLOADED, STATUS_SKIPPED}
        ),
        "skipped": result.skipped,
        "errors": result.errors,
        "cancelled": result.cancelled,
        "items": manifest_items,
    }
    if state_path is not None:
        manifest_path = state_path / _MANIFEST_NAME
        manifest_path.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        result.manifest_path = str(manifest_path)
    else:
        result.manifest_path = ""
    return result
