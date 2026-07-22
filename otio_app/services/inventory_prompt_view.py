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


def _asset_rank(asset: AssetMediaAnalysis) -> tuple[int, int, int]:
    """Höher = bevorzugter Vertreter bei Duplikaten."""
    path = str(asset.path or "")
    name = Path(path).name.lower()
    has_resolution = 1 if _RESOLUTION_SUFFIX_RE.search(Path(path).stem) else 0
    is_variant = 1 if _VARIANT_SUFFIX_RE.search(Path(path).stem) else 0
    desc_len = len((asset.description or "").strip())
    # Bevorzuge Einträge ohne Auflösungs-/Clean-Suffix und längere Beschreibung.
    return (desc_len, 0 if has_resolution else 1, 0 if is_variant else 1)


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

    ordered = sorted(
        chosen.values(),
        key=lambda asset: first_index.get(
            _dedupe_key(Path(str(asset.path)).name), 10_000
        ),
    )

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
) -> list[dict[str, Any]]:
    """LLM-Payload für Enhanced Cut Plan: schlank, stabile IDs, EN-Keys.

    Behält ``local_asset_id`` / ``description`` / ``duration_seconds`` für
    bestehende Prompt-Verträge; ersetzt den vollen ``path`` durch ``file``.
    """
    slim = build_slim_folder_inventory(
        folder_inventory, probe_duration=probe_duration
    )
    # Map id → dauer/beschreibung already in slim; rebuild EN keys.
    out: list[dict[str, Any]] = []
    for item in slim["assets"]:
        media = str(item.get("type") or "")
        media_type = "image" if media == "photo" else media
        row: dict[str, Any] = {
            "local_asset_id": item["id"],
            "asset_id": item["id"],
            "folder": folder_name,
            "file": item["file"],
            "duration_seconds": item.get("dauer_s"),
            "media_type": media_type,
            "description": item.get("beschreibung") or "",
        }
        for key in ("motion", "framing", "people", "people_action", "defects"):
            if key in item:
                row[key] = item[key]
        out.append(row)
    return out
