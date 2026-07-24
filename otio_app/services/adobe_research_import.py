"""Research-Excel → Adobe-Stock lizenzieren/herunterladen (vor Projektanlage).

Erwartetes Excel-Layout (Research Template):
- Zeile 1: Kapitel-Überschriften alle 3 Spalten (1, 4, 7, …)
- Zeile 2: Count | Asset ID | Link (wiederholt)
- ab Zeile 3: laufende Nummer | Adobe Content-ID | Adobe-URL
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import BinaryIO, Iterable

from otio_app.defaults import (
    ADOBE_STOCK_LICENSE_TYPE_STANDARD,
    ADOBE_STOCK_LICENSE_TYPE_VIDEO_4K,
    ADOBE_STOCK_LICENSE_TYPE_VIDEO_HD,
    ADOBE_STOCK_MIN_DOWNLOAD_BYTES,
    ADOBE_STOCK_VIDEO_4K_MAX_BYTES,
)
from otio_app.services.api_keys import get_api_key
from otio_app.services.supplement_sources.adobe_stock import (
    AdobeAssetTooLargeError,
    AdobeStockAdapter,
)

__all__ = [
    "AdobeResearchAsset",
    "AdobeResearchChapter",
    "AdobeResearchImportPlan",
    "AdobeResearchImportResult",
    "download_research_import",
    "format_asset_stem",
    "parse_research_excel",
    "sanitize_folder_name",
]

_INVALID_FS_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_WHITESPACE = re.compile(r"\s+")
_MANIFEST_NAME = "adobe_research_import_manifest.json"


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
    status: str  # downloaded | skipped | error
    local_path: str = ""
    message: str = ""
    license: str = ""


@dataclass
class AdobeResearchImportResult:
    target_root: str
    items: list[AdobeResearchImportItemResult] = field(default_factory=list)
    manifest_path: str = ""

    @property
    def downloaded(self) -> int:
        return sum(1 for item in self.items if item.status == "downloaded")

    @property
    def skipped(self) -> int:
        return sum(1 for item in self.items if item.status == "skipped")

    @property
    def errors(self) -> int:
        return sum(1 for item in self.items if item.status == "error")


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


def _existing_asset_ids_in_folder(folder: Path) -> set[str]:
    """Liest bereits importierte Adobe-IDs aus Manifest oder Dateinamen-Sidecar."""
    found: set[str] = set()
    manifest = folder / _MANIFEST_NAME
    if manifest.is_file():
        try:
            payload = json.loads(manifest.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            payload = {}
        for item in payload.get("items") or []:
            aid = str(item.get("asset_id") or "").strip()
            status = str(item.get("status") or "")
            if aid and status == "downloaded":
                found.add(aid)
    # Sidecar JSON neben Dateien
    for path in folder.glob("*.adobe.json"):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        aid = str(payload.get("asset_id") or "").strip()
        if aid:
            found.add(aid)
    return found


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


def _infer_media_type(adapter: AdobeStockAdapter, asset: AdobeResearchAsset) -> str:
    if asset.media_hint in {"video", "image"}:
        return asset.media_hint
    # Fallback: Content/Info mit Video_HD — wenn möglich → video, sonst image.
    api_key = get_api_key("ADOBE_STOCK_API_KEY") or ""
    access_token = get_api_key("ADOBE_STOCK_ACCESS_TOKEN") or ""
    if not api_key or not access_token:
        return "video"
    from otio_app.defaults import ADOBE_STOCK_CONTENT_INFO_ENDPOINT

    payload = adapter._request_licensing_json_safe(
        ADOBE_STOCK_CONTENT_INFO_ENDPOINT,
        {
            "content_id": asset.asset_id,
            "license": ADOBE_STOCK_LICENSE_TYPE_VIDEO_HD,
            "locale": "en_US",
        },
        api_key,
        access_token,
    )
    if payload.get("_error"):
        return "image"
    return "video"


def _license_and_download_to_path(
    adapter: AdobeStockAdapter,
    *,
    content_id: str,
    media_type: str,
    destination: Path,
) -> tuple[Path, str]:
    """Lizenziert eine Content-ID und schreibt die Datei nach destination."""
    api_key = get_api_key("ADOBE_STOCK_API_KEY")
    access_token = get_api_key("ADOBE_STOCK_ACCESS_TOKEN")
    if not api_key:
        raise PermissionError("ADOBE_STOCK_API_KEY fehlt.")
    if not access_token:
        raise PermissionError("ADOBE_STOCK_ACCESS_TOKEN fehlt.")

    destination.parent.mkdir(parents=True, exist_ok=True)

    if media_type == "image":
        licenses = [(ADOBE_STOCK_LICENSE_TYPE_STANDARD, None, None)]
    else:
        licenses = [
            (
                ADOBE_STOCK_LICENSE_TYPE_VIDEO_4K,
                2160,
                ADOBE_STOCK_VIDEO_4K_MAX_BYTES,
            ),
            (ADOBE_STOCK_LICENSE_TYPE_VIDEO_HD, 1080, None),
        ]

    last_error: Exception | None = None
    for index, (license_type, size, max_bytes) in enumerate(licenses):
        try:
            purchase = adapter._license_asset(
                content_id, license_type, api_key, access_token
            )
        except Exception as exc:  # noqa: BLE001
            last_error = exc
            # Foto-Fallback, falls Video-Lizenz fehlschlägt
            if media_type == "video" and index == len(licenses) - 1:
                try:
                    purchase = adapter._license_asset(
                        content_id,
                        ADOBE_STOCK_LICENSE_TYPE_STANDARD,
                        api_key,
                        access_token,
                    )
                    license_type = ADOBE_STOCK_LICENSE_TYPE_STANDARD
                    size = None
                    max_bytes = None
                except Exception as exc2:  # noqa: BLE001
                    last_error = exc2
                    continue
            else:
                continue

        content_type = str(purchase.get("content_type") or "").lower()
        if "video" in content_type:
            ext = ".mp4"
        elif "png" in content_type:
            ext = ".png"
        elif "jpeg" in content_type or "jpg" in content_type:
            ext = ".jpg"
        elif media_type == "image":
            ext = ".jpg"
        else:
            ext = ".mp4"

        local_path = destination.with_suffix(ext)
        try:
            adapter._stream_download_to_file(
                str(purchase.get("url") or ""),
                local_path,
                api_key=api_key,
                access_token=access_token,
                size=size,
                max_bytes=max_bytes,
            )
        except AdobeAssetTooLargeError as exc:
            last_error = exc
            local_path.unlink(missing_ok=True)
            continue
        except Exception as exc:  # noqa: BLE001
            last_error = exc
            local_path.unlink(missing_ok=True)
            continue

        if not local_path.is_file() or local_path.stat().st_size < ADOBE_STOCK_MIN_DOWNLOAD_BYTES:
            local_path.unlink(missing_ok=True)
            last_error = RuntimeError("Download zu klein / ungültig.")
            continue
        return local_path, license_type

    raise RuntimeError(
        f"Adobe-Download fehlgeschlagen für Content-ID {content_id}: {last_error}"
    )


def download_research_import(
    plan: AdobeResearchImportPlan,
    target_root: str | Path,
    *,
    chapter_titles: Iterable[str] | None = None,
    skip_existing_ids: bool = True,
    progress_callback=None,
) -> AdobeResearchImportResult:
    """Lizenzieren + Download in Zielordner/{Kapitel}/{Kapitel}_Asset_NN.ext."""
    root = Path(target_root).expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)

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

    for chapter in chapters:
        folder = root / chapter.folder_name
        folder.mkdir(parents=True, exist_ok=True)
        already = _existing_asset_ids_in_folder(folder) if skip_existing_ids else set()
        next_index = _next_asset_index(folder)

        for asset in chapter.assets:
            done += 1
            if progress_callback is not None:
                progress_callback(
                    done,
                    total,
                    chapter.folder_name,
                    asset.asset_id,
                )
            if asset.asset_id in already:
                result.items.append(
                    AdobeResearchImportItemResult(
                        chapter_title=chapter.title,
                        folder_name=chapter.folder_name,
                        asset_id=asset.asset_id,
                        status="skipped",
                        message="bereits vorhanden",
                    )
                )
                continue

            stem = format_asset_stem(chapter.folder_name, next_index)
            dest = folder / stem  # Suffix setzt Download
            try:
                media_type = _infer_media_type(adapter, asset)
                local_path, used_license = _license_and_download_to_path(
                    adapter,
                    content_id=asset.asset_id,
                    media_type=media_type,
                    destination=dest,
                )
                sidecar = {
                    "asset_id": asset.asset_id,
                    "link": asset.link,
                    "license": used_license,
                    "chapter_title": chapter.title,
                    "folder_name": chapter.folder_name,
                    "local_path": str(local_path),
                    "downloaded_at": datetime.now(timezone.utc).isoformat(),
                }
                sidecar_path = local_path.with_suffix(local_path.suffix + ".adobe.json")
                # Prefer sibling: Dublin_Asset_01.mp4.adobe.json → cleaner: .adobe.json next to stem
                sidecar_path = folder / f"{local_path.stem}.adobe.json"
                sidecar_path.write_text(
                    json.dumps(sidecar, ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )
                result.items.append(
                    AdobeResearchImportItemResult(
                        chapter_title=chapter.title,
                        folder_name=chapter.folder_name,
                        asset_id=asset.asset_id,
                        status="downloaded",
                        local_path=str(local_path),
                        license=used_license,
                    )
                )
                already.add(asset.asset_id)
                next_index += 1
            except Exception as exc:  # noqa: BLE001
                result.items.append(
                    AdobeResearchImportItemResult(
                        chapter_title=chapter.title,
                        folder_name=chapter.folder_name,
                        asset_id=asset.asset_id,
                        status="error",
                        message=str(exc),
                    )
                )

    manifest = {
        "schema_version": "adobe-research-import-v1",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "target_root": str(root),
        "sheet_name": plan.sheet_name,
        "downloaded": result.downloaded,
        "skipped": result.skipped,
        "errors": result.errors,
        "items": [asdict(item) for item in result.items],
    }
    manifest_path = root / _MANIFEST_NAME
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    result.manifest_path = str(manifest_path)
    return result
