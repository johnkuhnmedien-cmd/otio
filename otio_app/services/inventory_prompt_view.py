"""Abgeleitete, schlanke Inventory-Sicht für LLM / externe Nutzung.

Die kanonische ``inventory/{folder}.json`` (``AssetFolderAnalysis``) bleibt
unverändert die Quelle der Wahrheit für Sync, Supplements, OTIO, Hashes.
Dieses Modul erzeugt daraus eine kompakte Projektion — analog zum manuellen
``*_inventory_slim.json``-Beispiel.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from otio_app.analysis_models import AssetFolderAnalysis, AssetMediaAnalysis
from otio_app.services.generic_outro_selector import asset_id_for_path
from otio_app.services.media_utils import is_image_media, probe_duration_seconds

__all__ = [
    "build_slim_folder_inventory",
    "load_slim_folder_inventory_file",
    "slim_assets_for_cut_plan_prompt",
    "slim_assets_from_slim_document",
    "slim_inventory_path_for",
    "write_slim_folder_inventory",
]

_HINT_DEFAULT = (
    "Nur Assets MIT Beschreibung; Roh/Clean- und Auflösungs-Duplikate "
    "zusammengeführt. 'dauer_s' kommt aus inventory.duration_seconds "
    "(ffprobe); Bilder: null."
)

# z. B. _3840x2160, _1920x1080
_RESOLUTION_SUFFIX_RE = re.compile(r"_\d{3,5}x\d{3,5}$", re.IGNORECASE)
_VARIANT_SUFFIX_RE = re.compile(r"_(clean|raw|proxy)$", re.IGNORECASE)


def slim_inventory_path_for(canonical_inventory_path: Path) -> Path:
    """``Antelope_Canyon.json`` → ``Antelope_Canyon.slim.json``."""
    return canonical_inventory_path.with_name(
        f"{canonical_inventory_path.stem}.slim.json"
    )


def load_slim_folder_inventory_file(path: Path) -> dict[str, Any] | None:
    """Lädt vorhandene ``{folder}.slim.json`` oder None."""
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
    return payload


def slim_assets_from_slim_document(
    slim: dict[str, Any],
    *,
    folder_name: str,
) -> list[dict[str, Any]]:
    """Slim-Disk-Dokument → Cut-Plan-Prompt-Rows (EN-Keys).

    Videos stehen vor Fotos, damit der Cut-LLM Motion-Kandidaten zuerst sieht.
    """
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
        row: dict[str, Any] = {
            "local_asset_id": asset_id,
            "asset_id": asset_id,
            "folder": folder_name,
            "file": file_name,
            "duration_seconds": item.get("dauer_s"),
            "media_type": media_type,
            "description": item.get("beschreibung") or "",
        }
        if "usable_in_s" in item:
            row["usable_in_s"] = item["usable_in_s"]
        for key in ("motion", "framing", "people", "people_action", "defects"):
            if key in item:
                row[key] = item[key]
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
    if stored is not None and float(stored) > 0:
        return round(float(stored), 3)
    if not probe_duration:
        return None
    path = str(asset.path or "")
    try:
        duration = probe_duration_seconds(Path(path))
    except Exception:  # noqa: BLE001
        return None
    if duration is None or duration <= 0:
        return None
    return round(float(duration), 3)


def _asset_rank(asset: AssetMediaAnalysis) -> tuple[int, int, int, int]:
    """Höher = bevorzugter Vertreter bei Duplikaten.

    Video schlägt Foto/Still bei gleichem Stem — Cut Plan soll Motion bevorzugen.
    Danach: längere Beschreibung, ohne Auflösungs-/Clean-Suffix.
    """
    path = str(asset.path or "")
    type_label = _media_type_label(path, getattr(asset, "media_type", None))
    is_video = 1 if type_label == "video" else 0
    has_resolution = 1 if _RESOLUTION_SUFFIX_RE.search(Path(path).stem) else 0
    is_variant = 1 if _VARIANT_SUFFIX_RE.search(Path(path).stem) else 0
    desc_len = len((asset.description or "").strip())
    return (
        is_video,
        desc_len,
        0 if has_resolution else 1,
        0 if is_variant else 1,
    )


def build_slim_folder_inventory(
    folder_inventory: AssetFolderAnalysis,
    *,
    probe_duration: bool = True,
    hinweis: str | None = None,
) -> dict[str, Any]:
    """Baut die Slim-Projektion (kapitel / assets[]).

    - nur Assets mit nicht-leerer Beschreibung
    - Duplikate (gleiche Basisdatei ± Auflösung/Clean/Raw) werden gemerged
    - ``id`` bleibt die stabile ``asset_id`` (kein A01-Renumbering)
    """
    chosen: dict[str, AssetMediaAnalysis] = {}
    first_index: dict[str, int] = {}
    for index, asset in enumerate(folder_inventory.assets or []):
        path = str(getattr(asset, "path", "") or "").strip()
        description = str(getattr(asset, "description", "") or "").strip()
        if not path or not description:
            continue
        key = _dedupe_key(Path(path).name)
        previous = chosen.get(key)
        if previous is None or _asset_rank(asset) > _asset_rank(previous):
            chosen[key] = asset
        first_index.setdefault(key, index)

    def _order_key(asset: AssetMediaAnalysis) -> tuple[int, int]:
        path = str(asset.path or "")
        type_label = _media_type_label(path, getattr(asset, "media_type", None))
        # Videos first in slim / prompt — LLM sees motion inventory before stills.
        type_rank = 0 if type_label == "video" else 1
        return (
            type_rank,
            first_index.get(_dedupe_key(Path(path).name), 10_000),
        )

    ordered = sorted(chosen.values(), key=_order_key)

    assets_out: list[dict[str, Any]] = []
    for asset in ordered:
        path = str(asset.path)
        asset_id = (asset.asset_id or "").strip() or asset_id_for_path(path)
        type_label = _media_type_label(path, getattr(asset, "media_type", None))
        dauer = _dauer_s_for_asset(
            asset, type_label=type_label, probe_duration=probe_duration
        )
        entry: dict[str, Any] = {
            "id": asset_id,
            "file": Path(path).name,
            "type": type_label,
            "dauer_s": dauer,
            "beschreibung": str(asset.description or "").strip(),
        }
        usable_in = getattr(asset, "usable_in_s", None)
        if type_label == "video" and usable_in is not None:
            entry["usable_in_s"] = round(float(usable_in), 3)
        motion = str(getattr(asset, "motion", "") or "").strip()
        framing = str(getattr(asset, "framing", "") or "").strip()
        if motion:
            entry["motion"] = motion
        if framing:
            entry["framing"] = framing
        people = getattr(asset, "people", None)
        if people is not None:
            entry["people"] = bool(people)
        people_action = getattr(asset, "people_action", None)
        if people_action:
            entry["people_action"] = str(people_action)
        defects = getattr(asset, "defects", None)
        if defects:
            entry["defects"] = str(defects)
        assets_out.append(entry)

    return {
        "kapitel": folder_inventory.folder,
        "hinweis": hinweis if hinweis is not None else _HINT_DEFAULT,
        "assets": assets_out,
    }


def write_slim_folder_inventory(
    canonical_inventory_path: Path,
    folder_inventory: AssetFolderAnalysis,
    *,
    probe_duration: bool = True,
) -> Path:
    """Schreibt ``{folder}.slim.json`` neben die kanonische Inventory-Datei."""
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
