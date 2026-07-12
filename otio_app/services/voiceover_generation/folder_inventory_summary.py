"""Kompakte Ordner-Zusammenfassung aus dem Inventory — reine Python-Aggregation,
kein LLM-Call. Grundlage für den Dramaturgie-Prompt (Phase 3).
"""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

from otio_app.analysis_models import AssetFolderAnalysis, AssetMediaAnalysis
from otio_app.defaults import (
    VOICEOVER_GEN_DEFAULT_WORD_TOLERANCE_PERCENT,
    VOICEOVER_GEN_MAX_FOLDER_WORDS,
    VOICEOVER_GEN_MIN_FOLDER_WORDS,
)
from otio_app.models import Project
from otio_app.project_layout import get_folder_inventory_summaries_path
from otio_app.services.inventory_loader import load_folder_inventory
from otio_app.services.media_utils import is_image_media, is_video_media, probe_duration_seconds
from otio_app.services.voiceover_generation.models import (
    FolderInventorySummariesDocument,
    FolderInventorySummary,
)

__all__ = [
    "MIN_ASSET_COUNT_WARNING",
    "build_folder_inventory_summary",
    "build_all_folder_inventory_summaries",
    "build_and_save_folder_inventory_summaries",
    "load_folder_inventory_summaries",
]

MIN_ASSET_COUNT_WARNING = 3

RISK_NO_ASSETS = "NO_ASSETS"
RISK_VERY_FEW_ASSETS = "VERY_FEW_ASSETS"
RISK_MISSING_DESCRIPTIONS = "MISSING_DESCRIPTIONS"
RISK_IMAGES_ONLY = "IMAGES_ONLY"
RISK_LOW_VISUAL_DIVERSITY = "LOW_VISUAL_DIVERSITY"

_ESTABLISHING_HINTS = (
    "weit", "panorama", "luftaufnahme", "wide", "establishing", "aerial", "drone",
    "übersicht", "landschaft", "landscape",
)
_DETAIL_HINTS = ("detail", "nahaufnahme", "close-up", "closeup", "makro", "macro")
_PEOPLE_HINTS = (
    "mensch", "person", "leute", "wanderer", "tourist", "people", "hiker",
    "family", "familie", "kinder", "children", "menschen",
)
_MOTION_HINTS = (
    "bewegung", "fließend", "fliessend", "wind", "welle", "flug", "laufen",
    "motion", "flowing", "wave", "flying", "running", "walking", "strömung",
)
_STOPWORDS = {
    "und", "der", "die", "das", "ein", "eine", "einer", "mit", "im", "am", "auf",
    "von", "sich", "sind", "wird", "durch", "über", "unter", "the", "and", "a",
    "an", "of", "in", "on", "with", "is", "are", "this", "that", "from",
}


def _is_video(asset: AssetMediaAnalysis) -> bool:
    if asset.media_type == "video":
        return True
    if asset.media_type == "image":
        return False
    return bool(asset.path) and is_video_media(Path(asset.path))


def _is_image(asset: AssetMediaAnalysis) -> bool:
    if asset.media_type == "image":
        return True
    if asset.media_type == "video":
        return False
    return bool(asset.path) and is_image_media(Path(asset.path))


def _matches_any(text: str, hints: tuple[str, ...]) -> bool:
    lowered = text.lower()
    return any(hint in lowered for hint in hints)


def _dominant_visual_themes(descriptions: list[str], *, limit: int = 5) -> list[str]:
    """Einfache Keyword-Häufung — keine Semantik, nur eine brauchbare
    Kompaktdarstellung für den Dramaturgie-Prompt."""
    counter: Counter[str] = Counter()
    for description in descriptions:
        for raw_word in description.lower().replace(",", " ").replace(".", " ").split():
            word = raw_word.strip("-–—:;()[]\"'")
            if len(word) < 4 or word in _STOPWORDS:
                continue
            counter[word] += 1
    return [word for word, _count in counter.most_common(limit)]


def _estimate_word_count(
    *,
    asset_count: int,
    diversity_score: float,
    total_video_duration_sec: float,
) -> tuple[int, int, int]:
    """Heuristik (Phase 3 §2): wenige Assets/geringe Vielfalt -> 50-80, mittel
    -> 80-120, viele Assets/hohe Vielfalt -> 120-180 Wörter."""
    if asset_count <= 3 or diversity_score < 0.35:
        base = 65
    elif asset_count <= 8 or diversity_score < 0.65:
        base = 100
    else:
        base = 150

    if total_video_duration_sec >= 60:
        base += 15
    elif total_video_duration_sec < 10 and asset_count > 0:
        base -= 10

    base = max(VOICEOVER_GEN_MIN_FOLDER_WORDS, min(VOICEOVER_GEN_MAX_FOLDER_WORDS, base))
    tolerance = base * VOICEOVER_GEN_DEFAULT_WORD_TOLERANCE_PERCENT / 100
    min_words = max(VOICEOVER_GEN_MIN_FOLDER_WORDS, round(base - tolerance))
    max_words = min(VOICEOVER_GEN_MAX_FOLDER_WORDS, round(base + tolerance))
    return base, min_words, max_words


def build_folder_inventory_summary(
    project: Project,
    folder_name: str,
    *,
    inventory: AssetFolderAnalysis | None = None,
) -> FolderInventorySummary:
    """Baut die Zusammenfassung für einen Ordner. `inventory` kann für Tests
    direkt übergeben werden, um Dateisystemzugriffe zu vermeiden."""
    analysis = inventory if inventory is not None else load_folder_inventory(project, folder_name)
    assets = analysis.assets

    video_assets = [asset for asset in assets if _is_video(asset)]
    image_assets = [asset for asset in assets if _is_image(asset)]
    asset_count = len(assets)

    durations: list[float] = []
    for asset in video_assets:
        if not asset.path:
            continue
        duration = probe_duration_seconds(Path(asset.path))
        if duration and duration > 0:
            durations.append(duration)
    total_video_duration_sec = round(sum(durations), 2)
    average_video_duration_sec = (
        round(total_video_duration_sec / len(durations), 2) if durations else 0.0
    )

    descriptions = [
        asset.description.strip() for asset in assets if asset.description and asset.description.strip()
    ]
    unique_descriptions = set(descriptions)
    diversity_score = round(len(unique_descriptions) / asset_count, 3) if asset_count > 0 else 0.0

    has_people = any(_matches_any(text, _PEOPLE_HINTS) for text in descriptions)
    has_motion = any(_matches_any(text, _MOTION_HINTS) for text in descriptions)
    has_wide_shots = any(_matches_any(text, _ESTABLISHING_HINTS) for text in descriptions)
    has_detail_shots = any(_matches_any(text, _DETAIL_HINTS) for text in descriptions)

    visual_strength_score = round(
        min(
            1.0,
            0.35
            + 0.1 * min(asset_count, 5)
            + (0.15 if total_video_duration_sec > 20 else 0.0)
            + (0.1 if has_wide_shots else 0.0),
        ),
        3,
    )

    risks: list[str] = []
    if asset_count == 0:
        risks.append(RISK_NO_ASSETS)
    elif asset_count < MIN_ASSET_COUNT_WARNING:
        risks.append(RISK_VERY_FEW_ASSETS)
    if asset_count > 0 and not descriptions:
        risks.append(RISK_MISSING_DESCRIPTIONS)
    if asset_count > 0 and not video_assets:
        risks.append(RISK_IMAGES_ONLY)
    if asset_count > 0 and descriptions and diversity_score < 0.3:
        risks.append(RISK_LOW_VISUAL_DIVERSITY)

    word_count, min_words, max_words = _estimate_word_count(
        asset_count=asset_count,
        diversity_score=diversity_score,
        total_video_duration_sec=total_video_duration_sec,
    )
    if RISK_IMAGES_ONLY in risks or RISK_VERY_FEW_ASSETS in risks or RISK_NO_ASSETS in risks:
        word_count = min(word_count, 80)
        min_words = min(min_words, word_count)
        max_words = min(max(max_words, word_count), max(min_words, word_count) + 20)

    return FolderInventorySummary(
        folder_name=folder_name,
        asset_count=asset_count,
        video_count=len(video_assets),
        image_count=len(image_assets),
        total_video_duration_sec=total_video_duration_sec,
        average_video_duration_sec=average_video_duration_sec,
        has_people=has_people,
        has_motion=has_motion,
        has_wide_shots=has_wide_shots,
        has_detail_shots=has_detail_shots,
        has_establishing_shots=has_wide_shots,
        dominant_visual_themes=_dominant_visual_themes(descriptions),
        notable_asset_descriptions=descriptions[:5],
        visual_strength_score=visual_strength_score,
        asset_diversity_score=diversity_score,
        estimated_voiceover_word_count=word_count,
        estimated_min_words=min_words,
        estimated_max_words=max_words,
        risks=risks,
    )


def build_all_folder_inventory_summaries(project: Project) -> list[FolderInventorySummary]:
    return [
        build_folder_inventory_summary(project, folder_name)
        for folder_name in project.selected_asset_subdirs
    ]


def build_and_save_folder_inventory_summaries(project: Project) -> list[FolderInventorySummary]:
    """Erzeugt Summaries für alle ausgewählten Ordner und schreibt sie zusätzlich
    als Debug-Artefakt (Phase 3 §10)."""
    summaries = build_all_folder_inventory_summaries(project)
    document = FolderInventorySummariesDocument(project_id=project.id, folder_summaries=summaries)
    path = get_folder_inventory_summaries_path(project.language_work_dir_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(document.model_dump_json(indent=2), encoding="utf-8")
    return summaries


def load_folder_inventory_summaries(project: Project) -> FolderInventorySummariesDocument | None:
    path = get_folder_inventory_summaries_path(project.language_work_dir_path)
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return FolderInventorySummariesDocument.model_validate(payload)
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError):
        return None
