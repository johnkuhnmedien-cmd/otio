"""Auswahl von Outro-Assets aus demselben Ordner."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from otio_app.project_layout import safe_folder_slug
from otio_app.services.media_utils import probe_duration_seconds

_PREFERRED_KEYWORDS = (
    "establishing",
    "landschaft",
    "landscape",
    "overview",
    "luftaufnahme",
    "aerial",
    "drone",
    "überblick",
    "detail",
    "atmosph",
    "b-roll",
    "b roll",
    "ruhig",
    "wide",
    "panorama",
    "vintage",
    "hintergrund",
)

_DISFAVORED_KEYWORDS = (
    "logo",
    "text",
    "action",
    "spezifisch",
    "wiederhol",
)


@dataclass(frozen=True)
class GenericAssetCandidate:
    path: str
    asset_id: str
    description: str
    score: float
    selection_reason: str
    warnings: list[str]


def asset_id_for_path(path: str) -> str:
    stem = Path(path).stem
    slug = re.sub(r"[^a-zA-Z0-9]+", "_", stem).strip("_").lower()
    return f"asset_{slug or 'unknown'}"


def _score_asset(
    path: str,
    description: str,
    *,
    used_paths: set[str],
    last_path: str | None,
    min_duration_sec: float,
) -> GenericAssetCandidate:
    warnings: list[str] = []
    text = f"{Path(path).name} {description}".lower()
    score = 0.5

    for keyword in _PREFERRED_KEYWORDS:
        if keyword in text:
            score += 0.12

    for keyword in _DISFAVORED_KEYWORDS:
        if keyword in text:
            score -= 0.2
            warnings.append(f"Keyword «{keyword}» in Beschreibung")

    if path in used_paths:
        score -= 0.35
        warnings.append("Bereits im Schnittplan verwendet")

    if last_path and path == last_path:
        score -= 0.25
        warnings.append("Gleiches Asset wie letzter Shot")

    duration = probe_duration_seconds(Path(path))
    if duration is not None and duration < min_duration_sec:
        score -= 0.4
        warnings.append(f"Clip nur {duration:.1f}s — kürzer als Minimum")

    reason = "Neutraler Shot aus derselben Sektion als visuelles Outro."
    if any(k in text for k in ("landschaft", "landscape", "establishing", "overview")):
        reason = "Ruhiger Landschafts- oder Establishing-Shot aus derselben Sektion."
    elif "detail" in text:
        reason = "Detailaufnahme aus derselben Sektion als visuelles Outro."

    return GenericAssetCandidate(
        path=path,
        asset_id=asset_id_for_path(path),
        description=description,
        score=round(max(0.0, min(1.0, score)), 3),
        selection_reason=reason,
        warnings=warnings,
    )


def select_generic_outro_assets(
    folder_assets: list[dict[str, str]],
    *,
    used_paths: set[str],
    last_asset_path: str | None,
    count: int,
    min_duration_sec: float = 3.0,
    excluded_asset_ids: set[str] | None = None,
    usage_by_asset_id: dict[str, int] | None = None,
    max_asset_usage: int | None = None,
) -> list[GenericAssetCandidate]:
    """Wählt bis zu ``count`` unterschiedliche Outro-Kandidaten aus dem Ordner."""
    excluded = excluded_asset_ids or set()
    usage = usage_by_asset_id or {}
    candidates = [
        _score_asset(
            asset["path"],
            asset.get("description", ""),
            used_paths=used_paths,
            last_path=last_asset_path,
            min_duration_sec=min_duration_sec,
        )
        for asset in folder_assets
        if asset.get("path")
    ]
    candidates.sort(key=lambda item: (-item.score, item.path))
    chosen: list[GenericAssetCandidate] = []
    chosen_paths: set[str] = set()
    for candidate in candidates:
        if candidate.asset_id in excluded:
            continue
        if max_asset_usage is not None and usage.get(candidate.asset_id, 0) >= max_asset_usage:
            continue
        if candidate.score < 0.2:
            continue
        if candidate.path in chosen_paths:
            continue
        chosen.append(candidate)
        chosen_paths.add(candidate.path)
        if max_asset_usage is not None:
            usage[candidate.asset_id] = usage.get(candidate.asset_id, 0) + 1
        if len(chosen) >= count:
            break
    return chosen


def section_id_for_folder(folder_name: str) -> str:
    return f"section_{safe_folder_slug(folder_name)}"
