"""Keyword-Flow: deterministischer 9s-Map-Opener vor Kapitel-VO."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from otio_app.models import Project
from otio_app.project_layout import get_folder_inventory_path, safe_folder_slug
from otio_app.services.without_voiceover_enhanced.cut_plan_options import (
    KEYWORD_FLOW_MAP_OPENER_SEC,
)
from otio_app.services.inventory_prompt_view import (
    load_slim_folder_inventory_file,
    slim_inventory_path_for,
)
from otio_app.services.without_voiceover_enhanced.intro_script_bridge import (
    is_intro_folder_name,
)


@dataclass
class MapOpenerDecision:
    chapter_id: str
    status: str  # used | missing | ambiguous | too_short | invalid | skipped_intro
    warning: str = ""
    asset_id: str | None = None
    media_path: str | None = None
    source_duration_seconds: float = 0.0
    opener_seconds: float = KEYWORD_FLOW_MAP_OPENER_SEC


def _maps_folder_candidates(project: Project) -> list[str]:
    names = list(project.asset_subdir_names or []) + list(
        project.selected_asset_subdirs or []
    )
    out: list[str] = []
    for name in names:
        text = str(name or "").strip()
        if not text:
            continue
        if text.lower() == "maps" or safe_folder_slug(text).lower() == "maps":
            if text not in out:
                out.append(text)
    # Physischer Ordner unter Projektroot / work_dir
    for root in (project.project_root_path, project.work_dir_path):
        candidate = root / "Maps"
        if candidate.is_dir() and "Maps" not in out:
            out.append("Maps")
    return out


def _list_map_media_for_chapter(
    project: Project,
    chapter_id: str,
) -> list[dict[str, Any]]:
    """Findet Map-Medien per kanonischem Kapitel-Slug im Maps-Inventar/Ordner."""
    chapter_slug = safe_folder_slug(chapter_id)
    chapter_slug_l = chapter_slug.lower()
    matches: list[dict[str, Any]] = []
    map_folders = _maps_folder_candidates(project)
    for folder in map_folders:
        from otio_app.project_layout import get_folder_inventory_path as _g

        slim_path = slim_inventory_path_for(_g(project.work_dir_path, folder))
        doc = load_slim_folder_inventory_file(slim_path)
        assets = list((doc or {}).get("assets") or [])
        if not assets:
            # Fallback: Dateinamen im Maps-Ordner
            maps_dir = project.project_root_path / folder
            if maps_dir.is_dir():
                for path in sorted(maps_dir.iterdir()):
                    if not path.is_file():
                        continue
                    stem = safe_folder_slug(path.stem).lower()
                    if stem == chapter_slug_l or stem.startswith(chapter_slug_l + "_"):
                        matches.append(
                            {
                                "asset_id": f"map::{folder}::{path.name}",
                                "path": str(path),
                                "duration_seconds": 0.0,
                            }
                        )
            continue
        for asset in assets:
            if not isinstance(asset, dict):
                continue
            asset_id = str(asset.get("asset_id") or asset.get("id") or "").strip()
            filename = str(
                asset.get("filename") or asset.get("file_name") or Path(
                    str(asset.get("path") or "")
                ).name
            ).strip()
            tokens = " ".join(
                [
                    asset_id,
                    filename,
                    str(asset.get("description") or ""),
                    safe_folder_slug(filename),
                ]
            ).lower()
            slug_token = chapter_slug_l
            if slug_token in tokens.replace(" ", "_") or slug_token in tokens:
                matches.append(
                    {
                        "asset_id": asset_id or filename,
                        "path": str(asset.get("path") or ""),
                        "duration_seconds": float(asset.get("duration_seconds") or 0.0),
                        "width": asset.get("width"),
                        "height": asset.get("height"),
                        "export_ready": asset.get("export_ready", True),
                    }
                )
    # Deduplizieren nach path/asset_id
    seen: set[str] = set()
    unique: list[dict[str, Any]] = []
    for item in matches:
        key = str(item.get("path") or item.get("asset_id") or "")
        if not key or key in seen:
            continue
        seen.add(key)
        unique.append(item)
    return unique


def _probe_map_media(path: str) -> dict[str, Any]:
    """Leichte Validierung; nutzt ffprobe wenn verfügbar."""
    media = Path(path)
    if not media.is_file():
        return {"ok": False, "reason": "Datei fehlt"}
    if str(media).lower().startswith(("http://", "https://")):
        return {"ok": False, "reason": "HTTP-URL unzulässig"}
    try:
        import json
        import subprocess

        proc = subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-select_streams",
                "v:0",
                "-show_entries",
                "stream=width,height,duration:format=duration",
                "-of",
                "json",
                str(media),
            ],
            capture_output=True,
            text=True,
            check=False,
            timeout=30,
        )
        if proc.returncode != 0:
            return {"ok": False, "reason": "ffprobe fehlgeschlagen"}
        payload = json.loads(proc.stdout or "{}")
        streams = payload.get("streams") or []
        if not streams:
            return {"ok": False, "reason": "kein Videostream"}
        stream = streams[0]
        width = float(stream.get("width") or 0)
        height = float(stream.get("height") or 0)
        duration = float(
            stream.get("duration")
            or (payload.get("format") or {}).get("duration")
            or 0
        )
        if width <= 0 or height <= 0 or duration <= 0:
            return {"ok": False, "reason": "ungültige Geometrie/Dauer"}
        return {
            "ok": True,
            "width": width,
            "height": height,
            "duration_seconds": duration,
        }
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "reason": f"Probe-Fehler: {exc}"}


def decide_map_opener(
    project: Project,
    chapter_id: str,
    *,
    opener_seconds: float = KEYWORD_FLOW_MAP_OPENER_SEC,
) -> MapOpenerDecision:
    """Deterministische Map-Entscheidung inkl. Warnungs-Fallbacks."""
    if is_intro_folder_name(chapter_id):
        return MapOpenerDecision(
            chapter_id=chapter_id,
            status="skipped_intro",
            warning="Intro erhält keinen Kapitel-Map-Opener.",
        )
    matches = _list_map_media_for_chapter(project, chapter_id)
    if not matches:
        return MapOpenerDecision(
            chapter_id=chapter_id,
            status="missing",
            warning=(
                f"Kapitel {chapter_id}: keine Map gefunden — "
                "normaler Kapitelstart / Vorlauf."
            ),
        )
    if len(matches) > 1:
        return MapOpenerDecision(
            chapter_id=chapter_id,
            status="ambiguous",
            warning=(
                f"Kapitel {chapter_id}: mehrere Maps — keine automatische Auswahl, "
                "normaler Kapitelstart."
            ),
        )
    item = matches[0]
    path = str(item.get("path") or "").strip()
    probe = _probe_map_media(path) if path else {"ok": False, "reason": "Pfad fehlt"}
    if not probe.get("ok"):
        return MapOpenerDecision(
            chapter_id=chapter_id,
            status="invalid",
            warning=(
                f"Kapitel {chapter_id}: Map ungültig "
                f"({probe.get('reason')}) — normaler Kapitelstart."
            ),
            asset_id=str(item.get("asset_id") or "") or None,
            media_path=path or None,
        )
    duration = float(probe.get("duration_seconds") or item.get("duration_seconds") or 0.0)
    if duration + 1e-9 < float(opener_seconds):
        return MapOpenerDecision(
            chapter_id=chapter_id,
            status="too_short",
            warning=(
                f"Kapitel {chapter_id}: Map zu kurz ({duration:.2f}s < "
                f"{opener_seconds:.1f}s) — normaler Kapitelstart."
            ),
            asset_id=str(item.get("asset_id") or "") or None,
            media_path=path or None,
            source_duration_seconds=duration,
        )
    return MapOpenerDecision(
        chapter_id=chapter_id,
        status="used",
        asset_id=str(item.get("asset_id") or "") or None,
        media_path=path,
        source_duration_seconds=duration,
        opener_seconds=float(opener_seconds),
    )
