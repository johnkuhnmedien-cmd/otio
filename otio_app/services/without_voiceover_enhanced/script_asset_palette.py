"""Slim-Inventory → Motiv-Cluster für asset-grounded Kapitel-Skripte.

Viele Dateien zeigen dasselbe Motiv. Der Skript-Prompt bekommt eine Palette
(Cluster), keine Shotliste und keine Asset-IDs.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from otio_app.models import Project
from otio_app.project_layout import get_folder_inventory_path
from otio_app.services.inventory_prompt_view import (
    load_slim_folder_inventory_file,
    slim_inventory_path_for,
)

_CAMERA_FILLER_RE = re.compile(
    r"\b("
    r"weite\s+luftaufnahme|luftaufnahme|weitwinkelansicht|weitwinkel|"
    r"drohnenaufnahme|drohne|aerial(?:\s+view|\s+shot)?|drone(?:\s+shot)?"
    r")\b",
    re.IGNORECASE,
)
_LEADING_CAMERA_RE = re.compile(
    r"^(?:weite\s+)?(?:luftaufnahme|weitwinkelansicht|aerial(?:\s+view)?)\s+",
    re.IGNORECASE,
)
_TOKEN_RE = re.compile(r"[^\w\säöüÄÖÜß-]+", re.UNICODE)

_STOPWORDS = frozenset(
    {
        "der",
        "die",
        "das",
        "des",
        "dem",
        "den",
        "ein",
        "eine",
        "einer",
        "eines",
        "einem",
        "einen",
        "und",
        "mit",
        "von",
        "vom",
        "am",
        "im",
        "an",
        "auf",
        "vor",
        "über",
        "unter",
        "hinter",
        "zwischen",
        "the",
        "and",
        "with",
        "from",
        "for",
        "into",
        "onto",
        "see",
        "lake",
        "video",
        "photo",
        "foto",
    }
)

_GENERIC_TAG_KEYS = frozenset(
    {
        "luftaufnahme",
        "aerial",
        "see",
        "lake",
        "slowenien",
        "slovenia",
        "video",
        "photo",
        "foto",
        "berge",
        "alpen",
        "alps",
        "mountains",
    }
)

_JACCARD_THRESHOLD = 0.5

# Mixed overview shots keep several labels and must not absorb pure motifs.
_MOTIF_RULES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("pletna", ("pletna",)),
    ("boathouse", ("bootshaus", "boathouse", "holzbootshaus")),
    ("castle", ("burg bled", "burg von bled", "castle", "festung")),
    (
        "island_church",
        (
            "marienkirche",
            "inselkirche",
            "kircheninsel",
            "marieninsel",
            "blejski",
        ),
    ),
    (
        "parish_church",
        (
            "martinskirche",
            "st.-martins",
            "st. martin",
            "st-martins",
            "st martin's",
        ),
    ),
)
_CASTLE_WORD_RE = re.compile(r"(?<!\w)(burg|castle|festung)(?!\w)", re.IGNORECASE)


@dataclass(frozen=True)
class MotifCluster:
    count: int
    representative_caption: str
    video_count: int
    photo_count: int
    people_note: str


def slim_path_for_folder(project: Project, folder_name: str):
    canonical = get_folder_inventory_path(project.work_dir_path, folder_name)
    return slim_inventory_path_for(canonical)


def load_chapter_slim_document(project: Project, folder_name: str) -> dict[str, Any] | None:
    return load_slim_folder_inventory_file(slim_path_for_folder(project, folder_name))


def load_chapter_slim_assets(project: Project, folder_name: str) -> list[dict[str, Any]]:
    document = load_chapter_slim_document(project, folder_name)
    if document is None:
        return []
    assets = document.get("assets")
    if not isinstance(assets, list):
        return []
    return [item for item in assets if isinstance(item, dict)]


def folder_has_visual_palette(project: Project, folder_name: str) -> bool:
    return any(
        str(item.get("caption") or item.get("beschreibung") or "").strip()
        for item in load_chapter_slim_assets(project, folder_name)
    )


def _asset_caption(asset: dict[str, Any]) -> str:
    caption = str(asset.get("caption") or "").strip()
    if caption:
        return caption
    return str(asset.get("beschreibung") or "").strip()


def _asset_tags(asset: dict[str, Any]) -> list[str]:
    raw = asset.get("tags")
    if not isinstance(raw, list):
        return []
    out: list[str] = []
    seen: set[str] = set()
    for item in raw:
        text = str(item or "").strip()
        if not text:
            continue
        key = text.casefold()
        if key in seen:
            continue
        seen.add(key)
        out.append(text)
    return out


def _asset_type(asset: dict[str, Any]) -> str:
    media = str(asset.get("type") or "").strip().lower()
    if media in {"photo", "image", "still"}:
        return "photo"
    return "video" if media else "video"


def _caption_tokens(caption: str) -> frozenset[str]:
    cleaned = _CAMERA_FILLER_RE.sub(" ", caption)
    cleaned = _TOKEN_RE.sub(" ", cleaned)
    tokens: list[str] = []
    for raw in cleaned.casefold().split():
        if len(raw) < 3 or raw in _STOPWORDS:
            continue
        tokens.append(raw)
    return frozenset(tokens)


def _jaccard(left: frozenset[str], right: frozenset[str]) -> float:
    if not left or not right:
        return 0.0
    union = len(left | right)
    if union <= 0:
        return 0.0
    return len(left & right) / union


def _distinctive_tags(tags: list[str], *, chapter_name: str) -> frozenset[str]:
    generic = set(_GENERIC_TAG_KEYS)
    chapter = (chapter_name or "").strip()
    if chapter:
        generic.add(chapter.casefold())
        generic.add(chapter.replace(" ", "_").casefold())
        generic.add(chapter.replace(" ", "").casefold())
    out: set[str] = set()
    for tag in tags:
        key = tag.casefold().strip()
        if not key or key in generic:
            continue
        out.add(key)
    return frozenset(out)


def _haystack_for(asset: dict[str, Any]) -> str:
    caption = _asset_caption(asset)
    tags = " ".join(_asset_tags(asset))
    return f"{caption} {tags}".casefold()


def _has_term(haystack: str, term: str) -> bool:
    needle = term.casefold().strip()
    if not needle:
        return False
    if any(mark in needle for mark in (" ", "-", ".")):
        return needle in haystack
    return re.search(rf"(?<!\w){re.escape(needle)}(?!\w)", haystack) is not None


def _motif_labels(asset: dict[str, Any]) -> frozenset[str]:
    haystack = _haystack_for(asset)
    labels: list[str] = []
    for label, terms in _MOTIF_RULES:
        if any(_has_term(haystack, term) for term in terms):
            labels.append(label)
    if "castle" not in labels and _CASTLE_WORD_RE.search(haystack):
        labels.append("castle")
    return frozenset(labels)


def _cluster_key(labels: frozenset[str]) -> frozenset[str] | None:
    """Pletna/Bootshaus gewinnen; gemischte Burg+Kirche bleiben ein eigener Überblick."""
    if "pletna" in labels:
        return frozenset({"pletna"})
    if "boathouse" in labels:
        return frozenset({"boathouse"})
    if labels:
        return labels
    return None


def _same_unlabeled_motif(
    *,
    tokens_a: frozenset[str],
    tokens_b: frozenset[str],
    tags_a: frozenset[str],
    tags_b: frozenset[str],
) -> bool:
    if _jaccard(tokens_a, tokens_b) >= _JACCARD_THRESHOLD:
        return True
    return len(tags_a & tags_b) >= 2


def _clean_caption_for_prompt(caption: str) -> str:
    text = " ".join(caption.strip().split())
    text = _LEADING_CAMERA_RE.sub("", text).strip()
    if not text:
        return caption.strip()
    return text[0].upper() + text[1:] if text else text


def cluster_slim_assets(
    assets: list[dict[str, Any]],
    *,
    chapter_name: str = "",
) -> list[MotifCluster]:
    items: list[dict[str, Any]] = []
    for asset in assets:
        if _asset_caption(asset):
            items.append(asset)
    if not items:
        return []

    tokens = [_caption_tokens(_asset_caption(asset)) for asset in items]
    tags = [
        _distinctive_tags(_asset_tags(asset), chapter_name=chapter_name)
        for asset in items
    ]
    labels = [_cluster_key(_motif_labels(asset)) for asset in items]
    assigned = [False] * len(items)
    groups: list[list[int]] = []
    for index, _asset in enumerate(items):
        if assigned[index]:
            continue
        group = [index]
        assigned[index] = True
        seed_key = labels[index]
        for other in range(index + 1, len(items)):
            if assigned[other]:
                continue
            other_key = labels[other]
            if seed_key is not None or other_key is not None:
                if seed_key is not None and seed_key == other_key:
                    group.append(other)
                    assigned[other] = True
                continue
            if all(
                _same_unlabeled_motif(
                    tokens_a=tokens[member],
                    tokens_b=tokens[other],
                    tags_a=tags[member],
                    tags_b=tags[other],
                )
                for member in group
            ):
                group.append(other)
                assigned[other] = True
        groups.append(group)

    clusters: list[MotifCluster] = []
    for group in groups:
        captions_no_people = [
            _asset_caption(items[i])
            for i in group
            if not str(items[i].get("people_action") or "").strip()
        ]
        captions = captions_no_people or [_asset_caption(items[i]) for i in group]
        representative = max(captions, key=len)
        video_count = sum(1 for i in group if _asset_type(items[i]) == "video")
        photo_count = len(group) - video_count
        people_notes: list[str] = []
        seen_notes: set[str] = set()
        for i in group:
            note = str(items[i].get("people_action") or "").strip()
            if not note:
                continue
            key = note.casefold()
            if key in seen_notes:
                continue
            seen_notes.add(key)
            people_notes.append(note)
        clusters.append(
            MotifCluster(
                count=len(group),
                representative_caption=_clean_caption_for_prompt(representative),
                video_count=video_count,
                photo_count=photo_count,
                people_note="; ".join(people_notes),
            )
        )
    clusters.sort(key=lambda item: (-item.count, item.representative_caption.casefold()))
    return clusters


def build_chapter_visual_palette_prompt_block(
    clusters: list[MotifCluster],
    *,
    folder_name: str,
) -> str:
    if not clusters:
        return ""
    lines = [
        "CHAPTER VISUAL PALETTE (this folder only — palette, not a shot list)",
        "",
        f"Folder: {folder_name}",
        "Near-duplicate files are already merged into motif clusters.",
        "Do not re-list clusters in narration. Do not speak asset IDs or filenames.",
        "",
        "Motif clusters:",
    ]
    for cluster in clusters:
        kinds: list[str] = []
        if cluster.video_count:
            kinds.append(f"{cluster.video_count} video")
        if cluster.photo_count:
            kinds.append(f"{cluster.photo_count} photo")
        kind_text = ", ".join(kinds) if kinds else "files"
        file_word = "file" if cluster.count == 1 else "files"
        line = (
            f"- ({cluster.count} {file_word}, {kind_text}) "
            f"{cluster.representative_caption}"
        )
        if cluster.people_note:
            line += f" [incidental people: {cluster.people_note}]"
        lines.append(line)
    return "\n".join(lines)


def build_chapter_visual_palette_text(project: Project, folder_name: str) -> str:
    assets = load_chapter_slim_assets(project, folder_name)
    clusters = cluster_slim_assets(assets, chapter_name=folder_name)
    return build_chapter_visual_palette_prompt_block(
        clusters, folder_name=folder_name
    )
