"""Abgeleitete, schlanke Inventory-Sicht für LLM / externe Nutzung.

Die kanonische ``inventory/{folder}.json`` (``AssetFolderAnalysis``) bleibt
unverändert die Quelle der Wahrheit für Sync, Supplements, OTIO, Hashes.
Dieses Modul erzeugt daraus eine kompakte, versionierte Slim-v2-Projektion.
Slim-Erzeugung ist rein lokal/deterministisch und löst niemals Gemini aus.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Literal

from otio_app.analysis_models import AssetFolderAnalysis, AssetMediaAnalysis
from otio_app.services.generic_outro_selector import asset_id_for_path
from otio_app.services.media_utils import (
    NO_ANALYZABLE_MEDIA_DESCRIPTION,
    is_image_media,
    probe_duration_seconds,
)

__all__ = [
    "SLIM_INVENTORY_SCHEMA_VERSION",
    "build_slim_folder_inventory",
    "load_slim_folder_inventory_file",
    "slim_assets_for_cut_plan_prompt",
    "slim_assets_from_slim_document",
    "slim_inventory_path_for",
    "write_slim_folder_inventory",
]

SLIM_INVENTORY_SCHEMA_VERSION = "asset-slim-v2"

_CAPTION_MAX_CHARS = 180
_TAG_LIMIT = 6
_COLOR_LIMIT = 3
_DEFECT_NOTE_MAX_CHARS = 120

# z. B. _3840x2160, _1920x1080
_RESOLUTION_SUFFIX_RE = re.compile(r"_\d{3,5}x\d{3,5}$", re.IGNORECASE)
_VARIANT_SUFFIX_RE = re.compile(r"_(clean|raw|proxy)$", re.IGNORECASE)

SlimKind = Literal["v1", "v2"]


def slim_inventory_path_for(canonical_inventory_path: Path) -> Path:
    """``Antelope_Canyon.json`` → ``Antelope_Canyon.slim.json``."""
    return canonical_inventory_path.with_name(
        f"{canonical_inventory_path.stem}.slim.json"
    )


def _slim_document_kind(payload: dict[str, Any]) -> SlimKind | None:
    """Erkennt Slim v1/v2; unbekannte explizite Version → None."""
    raw_version = payload.get("schema_version")
    if raw_version is not None and str(raw_version).strip():
        version = str(raw_version).strip()
        if version == SLIM_INVENTORY_SCHEMA_VERSION:
            return "v2"
        return None
    # Legacy v1: kein schema_version, typischerweise kapitel + assets[].
    return "v1"


def load_slim_folder_inventory_file(path: Path) -> dict[str, Any] | None:
    """Lädt vorhandene ``{folder}.slim.json`` (v1 oder v2) oder None."""
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    assets = payload.get("assets")
    if not isinstance(assets, list):
        return None
    if _slim_document_kind(payload) is None:
        return None
    return payload


def _dedupe_limit(values: Any, *, limit: int) -> list[str]:
    if not isinstance(values, (list, tuple)):
        return []
    seen: set[str] = set()
    out: list[str] = []
    for raw in values:
        text = str(raw or "").strip()
        if not text:
            continue
        key = text.casefold()
        if key in seen:
            continue
        seen.add(key)
        out.append(text)
        if len(out) >= limit:
            break
    return out


def _clamp_text(value: Any, *, max_chars: int) -> str:
    text = str(value or "").strip()
    if len(text) <= max_chars:
        return text
    return text[:max_chars]


def _caption_for_slim(asset: AssetMediaAnalysis) -> str:
    caption = str(getattr(asset, "caption", "") or "").strip()
    if caption:
        return _clamp_text(caption, max_chars=_CAPTION_MAX_CHARS)
    description = str(getattr(asset, "description", "") or "").strip()
    if not description or description == NO_ANALYZABLE_MEDIA_DESCRIPTION:
        return ""
    return _clamp_text(description, max_chars=_CAPTION_MAX_CHARS)


def _asset_has_slim_text(asset: AssetMediaAnalysis) -> bool:
    return bool(_caption_for_slim(asset))


def _asset_slim_usable(asset: AssetMediaAnalysis) -> bool:
    """Slim-tauglich: Text vorhanden, kein Parse-/Analysefehler."""
    if getattr(asset, "analysis_parse_ok", None) is False:
        return False
    error = getattr(asset, "error", None)
    if error is not None and str(error).strip():
        return False
    return _asset_has_slim_text(asset)


def _round_seconds(value: Any) -> float | None:
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number <= 0:
        return None
    return round(number, 3)


def _motion_for_slim(asset: AssetMediaAnalysis) -> dict[str, Any] | None:
    profile = getattr(asset, "motion_profile", None)
    if profile is not None:
        motion_type = str(getattr(profile, "type", "") or "").strip()
        direction = str(getattr(profile, "direction", "") or "").strip()
        intensity = getattr(profile, "intensity", None)
        payload: dict[str, Any] = {}
        if motion_type and motion_type != "unknown":
            payload["type"] = motion_type
        if intensity is not None:
            try:
                payload["intensity"] = int(intensity)
            except (TypeError, ValueError):
                pass
        if direction and direction != "unknown":
            payload["direction"] = direction
        return payload or None

    flat = str(getattr(asset, "motion", "") or "").strip()
    if flat and flat != "unknown":
        return {"type": flat}
    return None


def _framing_for_slim(asset: AssetMediaAnalysis) -> dict[str, Any] | None:
    profile = getattr(asset, "framing_profile", None)
    if profile is not None:
        framing_type = str(getattr(profile, "type", "") or "").strip()
        scale = str(getattr(profile, "shot_scale", "") or "").strip()
        payload: dict[str, Any] = {}
        if framing_type:
            payload["type"] = framing_type
        if scale and scale != "unknown":
            payload["scale"] = scale
        return payload or None

    flat = str(getattr(asset, "framing", "") or "").strip()
    if flat:
        return {"type": flat}
    return None


def _quality_for_slim(asset: AssetMediaAnalysis) -> dict[str, Any] | None:
    profile = getattr(asset, "quality_profile", None)
    if profile is None:
        return None
    mapping = (
        ("technical", "technical_quality"),
        ("composition", "composition_quality"),
        ("appeal", "visual_appeal"),
        ("clarity", "subject_clarity"),
        ("hero", "hero_potential"),
        ("defect", "defect_severity"),
    )
    payload: dict[str, Any] = {}
    for out_key, attr in mapping:
        value = getattr(profile, attr, None)
        if value is None:
            continue
        try:
            payload[out_key] = int(value)
        except (TypeError, ValueError):
            continue
    return payload or None


def _look_for_slim(asset: AssetMediaAnalysis) -> dict[str, Any] | None:
    profile = getattr(asset, "look_profile", None)
    if profile is None:
        return None
    payload: dict[str, Any] = {}
    for out_key, attr in (
        ("brightness", "brightness"),
        ("contrast", "contrast"),
        ("saturation", "saturation"),
    ):
        value = getattr(profile, attr, None)
        if value is None:
            continue
        try:
            payload[out_key] = int(value)
        except (TypeError, ValueError):
            continue
    temperature = str(getattr(profile, "color_temperature", "") or "").strip()
    if temperature and temperature != "unknown":
        payload["temperature"] = temperature
    colors = _dedupe_limit(
        getattr(profile, "dominant_colors", None) or [],
        limit=_COLOR_LIMIT,
    )
    if colors:
        payload["colors"] = colors
    return payload or None


def _defects_for_slim(asset: AssetMediaAnalysis) -> list[dict[str, Any]] | None:
    items = list(getattr(asset, "defect_items", None) or [])
    out: list[dict[str, Any]] = []
    for item in items:
        defect_type = str(getattr(item, "type", "") or "").strip() or "other"
        try:
            severity = int(getattr(item, "severity", 0) or 0)
        except (TypeError, ValueError):
            severity = 0
        note = _clamp_text(
            getattr(item, "note", ""),
            max_chars=_DEFECT_NOTE_MAX_CHARS,
        )
        entry: dict[str, Any] = {"type": defect_type, "severity": severity}
        if note:
            entry["note"] = note
        out.append(entry)
    if out:
        return out

    legacy = str(getattr(asset, "defects", "") or "").strip()
    if not legacy:
        return None
    return [
        {
            "type": "other",
            "severity": 0,
            "note": _clamp_text(legacy, max_chars=_DEFECT_NOTE_MAX_CHARS),
        }
    ]


def _defects_prompt_summary(defects: Any) -> str | None:
    if defects is None:
        return None
    if isinstance(defects, str):
        text = defects.strip()
        return text or None
    if not isinstance(defects, list):
        return None
    parts: list[str] = []
    for item in defects:
        if not isinstance(item, dict):
            continue
        defect_type = str(item.get("type") or "").strip() or "other"
        note = str(item.get("note") or "").strip()
        parts.append(f"{defect_type}: {note}" if note else defect_type)
    summary = "; ".join(parts).strip()
    return summary or None


def slim_assets_from_slim_document(
    slim: dict[str, Any],
    *,
    folder_name: str,
) -> list[dict[str, Any]]:
    """Slim-Disk-Dokument → Cut-Plan-Prompt-Rows (EN-Keys).

    Videos stehen vor Fotos, damit der Cut-LLM Motion-Kandidaten zuerst sieht.
    Phase 2A: Quality/Look/Tags/Scale noch nicht im Prompt-Row.
    """
    kind = _slim_document_kind(slim)
    if kind is None:
        return []

    out: list[dict[str, Any]] = []
    for item in slim.get("assets") or []:
        if not isinstance(item, dict):
            continue
        asset_id = str(item.get("id") or "").strip()
        file_name = str(item.get("file") or "").strip()
        if not asset_id or not file_name:
            continue
        media = str(item.get("type") or "").strip().lower()
        media_type = "image" if media == "photo" else (media or "video")

        if kind == "v2":
            duration = item.get("duration_s")
            description = str(item.get("caption") or "").strip()
            motion_raw = item.get("motion")
            framing_raw = item.get("framing")
            motion = (
                str(motion_raw.get("type") or "").strip()
                if isinstance(motion_raw, dict)
                else ""
            )
            framing = (
                str(framing_raw.get("type") or "").strip()
                if isinstance(framing_raw, dict)
                else ""
            )
            defects = _defects_prompt_summary(item.get("defects"))
        else:
            duration = item.get("dauer_s")
            description = str(item.get("beschreibung") or "").strip()
            motion = str(item.get("motion") or "").strip()
            framing = str(item.get("framing") or "").strip()
            defects = _defects_prompt_summary(item.get("defects"))

        row: dict[str, Any] = {
            "local_asset_id": asset_id,
            "asset_id": asset_id,
            "folder": folder_name,
            "file": file_name,
            "duration_seconds": duration,
            "media_type": media_type,
            "description": description,
        }
        if "usable_in_s" in item:
            row["usable_in_s"] = item["usable_in_s"]
        if motion:
            row["motion"] = motion
        if framing:
            row["framing"] = framing
        if "people" in item:
            row["people"] = bool(item.get("people"))
        people_action = item.get("people_action")
        if people_action:
            row["people_action"] = str(people_action)
        if defects:
            row["defects"] = defects
        out.append(row)

    out.sort(
        key=lambda row: (
            0 if str(row.get("media_type") or "").lower() == "video" else 1,
            str(row.get("local_asset_id") or ""),
        )
    )
    return out


def _dedupe_key(filename: str) -> str:
    stem = Path(filename).stem.strip().lower()
    stem = _RESOLUTION_SUFFIX_RE.sub("", stem)
    stem = _VARIANT_SUFFIX_RE.sub("", stem)
    return stem


def _media_type_label(path: str, media_type: str | None) -> str:
    raw = (media_type or "").strip().lower()
    if raw in {"video", "image", "photo", "audio"}:
        return "photo" if raw == "image" else raw
    try:
        if is_image_media(Path(path)):
            return "photo"
    except Exception:  # noqa: BLE001
        pass
    suffix = Path(path).suffix.lower()
    if suffix in {".jpg", ".jpeg", ".png", ".webp", ".tif", ".tiff", ".heic"}:
        return "photo"
    if suffix in {".mp3", ".wav", ".aac", ".m4a", ".flac"}:
        return "audio"
    return "video"


def _dauer_s_for_asset(
    asset: AssetMediaAnalysis,
    *,
    type_label: str,
    probe_duration: bool,
) -> float | None:
    if type_label != "video":
        return None
    stored = getattr(asset, "duration_seconds", None)
    rounded = _round_seconds(stored)
    if rounded is not None:
        return rounded
    if not probe_duration:
        return None
    path = str(asset.path or "")
    try:
        duration = probe_duration_seconds(Path(path))
    except Exception:  # noqa: BLE001
        return None
    return _round_seconds(duration)


def _asset_rank(asset: AssetMediaAnalysis) -> tuple[int, int, int, int]:
    """Höher = bevorzugter Vertreter bei Duplikaten.

    Video schlägt Foto/Still bei gleichem Stem — Cut Plan soll Motion bevorzugen.
    Danach: längerer Caption-/Description-Text, ohne Auflösungs-/Clean-Suffix.
    """
    path = str(asset.path or "")
    type_label = _media_type_label(path, getattr(asset, "media_type", None))
    is_video = 1 if type_label == "video" else 0
    has_resolution = 1 if _RESOLUTION_SUFFIX_RE.search(Path(path).stem) else 0
    is_variant = 1 if _VARIANT_SUFFIX_RE.search(Path(path).stem) else 0
    text_len = max(
        len(str(getattr(asset, "caption", "") or "").strip()),
        len(str(getattr(asset, "description", "") or "").strip()),
    )
    return (
        is_video,
        text_len,
        0 if has_resolution else 1,
        0 if is_variant else 1,
    )


def _build_slim_asset_entry(
    asset: AssetMediaAnalysis,
    *,
    probe_duration: bool,
) -> dict[str, Any] | None:
    path = str(getattr(asset, "path", "") or "").strip()
    if not path or not _asset_slim_usable(asset):
        return None

    caption = _caption_for_slim(asset)
    if not caption:
        return None

    asset_id = (asset.asset_id or "").strip() or asset_id_for_path(path)
    type_label = _media_type_label(path, getattr(asset, "media_type", None))
    duration_s = _dauer_s_for_asset(
        asset, type_label=type_label, probe_duration=probe_duration
    )

    entry: dict[str, Any] = {
        "id": asset_id,
        "file": Path(path).name,
        "type": type_label,
        "duration_s": duration_s,
        "caption": caption,
    }

    tags = _dedupe_limit(getattr(asset, "content_tags", None) or [], limit=_TAG_LIMIT)
    if tags:
        entry["tags"] = tags

    motion = _motion_for_slim(asset)
    if motion is not None:
        entry["motion"] = motion
    framing = _framing_for_slim(asset)
    if framing is not None:
        entry["framing"] = framing
    quality = _quality_for_slim(asset)
    if quality is not None:
        entry["quality"] = quality
    look = _look_for_slim(asset)
    if look is not None:
        entry["look"] = look

    people = getattr(asset, "people", None)
    if people is not None:
        entry["people"] = bool(people)
    people_action = getattr(asset, "people_action", None)
    if people_action:
        entry["people_action"] = str(people_action).strip()

    defects = _defects_for_slim(asset)
    if defects:
        entry["defects"] = defects

    usable_in = getattr(asset, "usable_in_s", None)
    if type_label == "video" and usable_in is not None:
        try:
            # 0.0 ist ein gültiger Lead-In (anders als duration_s).
            entry["usable_in_s"] = round(float(usable_in), 3)
        except (TypeError, ValueError):
            pass

    return entry


def build_slim_folder_inventory(
    folder_inventory: AssetFolderAnalysis,
    *,
    probe_duration: bool = True,
    hinweis: str | None = None,
) -> dict[str, Any]:
    """Baut die Slim-v2-Projektion (schema_version / chapter / assets[]).

    ``hinweis`` bleibt aus Kompatibilitätsgründen akzeptiert, wird in v2
    nicht mehr geschrieben.
    """
    del hinweis  # v2: kein Hinweistext im LLM-orientierten Dokument

    chosen: dict[str, AssetMediaAnalysis] = {}
    first_index: dict[str, int] = {}
    for index, asset in enumerate(folder_inventory.assets or []):
        path = str(getattr(asset, "path", "") or "").strip()
        if not path or not _asset_slim_usable(asset):
            continue
        key = _dedupe_key(Path(path).name)
        previous = chosen.get(key)
        if previous is None or _asset_rank(asset) > _asset_rank(previous):
            chosen[key] = asset
        first_index.setdefault(key, index)

    def _order_key(asset: AssetMediaAnalysis) -> tuple[int, int]:
        path = str(asset.path or "")
        type_label = _media_type_label(path, getattr(asset, "media_type", None))
        type_rank = 0 if type_label == "video" else 1
        return (
            type_rank,
            first_index.get(_dedupe_key(Path(path).name), 10_000),
        )

    ordered = sorted(chosen.values(), key=_order_key)
    assets_out: list[dict[str, Any]] = []
    for asset in ordered:
        entry = _build_slim_asset_entry(asset, probe_duration=probe_duration)
        if entry is not None:
            assets_out.append(entry)

    return {
        "schema_version": SLIM_INVENTORY_SCHEMA_VERSION,
        "chapter": folder_inventory.folder,
        "assets": assets_out,
    }


def write_slim_folder_inventory(
    canonical_inventory_path: Path,
    folder_inventory: AssetFolderAnalysis,
    *,
    probe_duration: bool = True,
) -> Path:
    """Schreibt ``{folder}.slim.json`` (Slim v2) neben die kanonische Inventory-Datei."""
    slim = build_slim_folder_inventory(
        folder_inventory, probe_duration=probe_duration
    )
    out = slim_inventory_path_for(canonical_inventory_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        json.dumps(slim, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return out


def slim_assets_for_cut_plan_prompt(
    folder_inventory: AssetFolderAnalysis,
    *,
    folder_name: str,
    probe_duration: bool = True,
    existing_slim_path: Path | None = None,
) -> list[dict[str, Any]]:
    """LLM-Payload für Enhanced Cut Plan: schlank, stabile IDs, EN-Keys.

    Bevorzugt vorhandene ``{folder}.slim.json`` (keine Neubau-/ffprobe-Runde).
    Fallback: Slim-Projektion aus kanonischem Inventar.
    """
    if existing_slim_path is not None:
        loaded = load_slim_folder_inventory_file(existing_slim_path)
        if loaded is not None:
            rows = slim_assets_from_slim_document(loaded, folder_name=folder_name)
            if rows:
                return rows

    slim = build_slim_folder_inventory(
        folder_inventory, probe_duration=probe_duration
    )
    return slim_assets_from_slim_document(slim, folder_name=folder_name)
