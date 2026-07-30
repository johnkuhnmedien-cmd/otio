"""Filmweites Inventar der Satzanfänge für Enhanced-Skript-Prompts (Schritt ④).

Nach jedem Kapitel wird der erste Satz gespeichert. Dieselbe Eröffnung
(Phrase oder schematischer Stem) darf höchstens zweimal vorkommen —
danach ist sie für folgende Kapitel gesperrt.
"""

from __future__ import annotations

import re
from collections import Counter

from pydantic import BaseModel, Field

from otio_app.models import Project
from otio_app.services.without_voiceover_enhanced.io_utils import load_model, write_json
from otio_app.services.without_voiceover_enhanced.paths import (
    script_opening_inventory_path,
)
from otio_app.services.without_voiceover_enhanced.segment_alignment_service import (
    split_segment_into_sentences,
)

MAX_OPENING_USES = 2

# Schematische Stems: nur das Muster zählt, nicht der Ortsname danach.
_STEM_FIRST_WORDS: dict[str, str] = {
    "after": "stem:after_previous",
    "nach": "stem:after_previous",
    "where": "stem:where_contrast",
    "wo": "stem:where_contrast",
    "across": "stem:wide_establish",
    "weit": "stem:wide_establish",
    "here": "stem:here_begins",
    "hier": "stem:here_begins",
}

__all__ = [
    "MAX_OPENING_USES",
    "OpeningInventoryDocument",
    "OpeningInventoryEntry",
    "build_opening_inventory_prompt_block",
    "clear_opening_inventory",
    "extract_opening_keys",
    "first_sentence_of_narration",
    "load_opening_inventory",
    "merge_opening_for_folder",
    "opening_key_counts",
    "remove_opening_for_folder",
    "save_opening_inventory",
    "validate_opening_against_inventory",
]


class OpeningInventoryEntry(BaseModel):
    folder_name: str
    first_sentence: str = ""
    keys: list[str] = Field(default_factory=list)


class OpeningInventoryDocument(BaseModel):
    schema_version: str = "enhanced-opening-inventory-v1"
    entries: list[OpeningInventoryEntry] = Field(default_factory=list)


_WS_RE = re.compile(r"\s+")
_NON_WORD_RE = re.compile(r"[^\w\s'-]", re.UNICODE)


def first_sentence_of_narration(narration: str) -> str:
    sentences = split_segment_into_sentences(narration or "")
    return sentences[0].strip() if sentences else ""


def extract_opening_keys(first_sentence: str) -> list[str]:
    """Zähl-Keys für einen Satzanfang: Phrase (4 Wörter) + optionaler Stem."""
    cleaned = _NON_WORD_RE.sub(" ", (first_sentence or "").lower())
    words = [w for w in _WS_RE.split(cleaned.strip()) if w]
    if not words:
        return []
    keys: list[str] = [f"phrase:{' '.join(words[:4])}"]
    stem = _STEM_FIRST_WORDS.get(words[0])
    if stem:
        keys.append(stem)
    # Deduplicate preserving order
    seen: set[str] = set()
    ordered: list[str] = []
    for key in keys:
        if key not in seen:
            seen.add(key)
            ordered.append(key)
    return ordered


def load_opening_inventory(project: Project) -> OpeningInventoryDocument:
    loaded = load_model(script_opening_inventory_path(project), OpeningInventoryDocument)
    return loaded or OpeningInventoryDocument()


def save_opening_inventory(project: Project, document: OpeningInventoryDocument) -> None:
    write_json(script_opening_inventory_path(project), document)


def clear_opening_inventory(project: Project) -> None:
    save_opening_inventory(project, OpeningInventoryDocument())


def remove_opening_for_folder(
    document: OpeningInventoryDocument, folder_name: str
) -> OpeningInventoryDocument:
    return OpeningInventoryDocument(
        schema_version=document.schema_version,
        entries=[e for e in document.entries if e.folder_name != folder_name],
    )


def merge_opening_for_folder(
    document: OpeningInventoryDocument,
    *,
    folder_name: str,
    narration_full: str,
) -> OpeningInventoryDocument:
    first = first_sentence_of_narration(narration_full)
    keys = extract_opening_keys(first)
    cleaned = remove_opening_for_folder(document, folder_name)
    if not first:
        return cleaned
    return OpeningInventoryDocument(
        schema_version=cleaned.schema_version,
        entries=list(cleaned.entries)
        + [
            OpeningInventoryEntry(
                folder_name=folder_name,
                first_sentence=first,
                keys=keys,
            )
        ],
    )


def opening_key_counts(
    document: OpeningInventoryDocument,
    *,
    exclude_folder: str | None = None,
) -> Counter[str]:
    counts: Counter[str] = Counter()
    for entry in document.entries:
        if exclude_folder and entry.folder_name == exclude_folder:
            continue
        for key in entry.keys:
            counts[key] += 1
    return counts


def validate_opening_against_inventory(
    *,
    narration_full: str,
    inventory: OpeningInventoryDocument,
    folder_name: str,
) -> list[str]:
    """Fehler wenn ein Key bereits MAX_OPENING_USES-mal in anderen Kapiteln steht."""
    first = first_sentence_of_narration(narration_full)
    keys = extract_opening_keys(first)
    if not keys:
        return []
    counts = opening_key_counts(inventory, exclude_folder=folder_name)
    errors: list[str] = []
    for key in keys:
        if counts.get(key, 0) >= MAX_OPENING_USES:
            errors.append(
                f"Satzanfang „{key}“ bereits {counts[key]}× verwendet "
                f"(Maximum {MAX_OPENING_USES}). Erster Satz: „{first[:120]}“"
            )
    return errors


def _human_key_label(key: str) -> str:
    if key.startswith("phrase:"):
        return f'phrase "{key.removeprefix("phrase:")}"'
    labels = {
        "stem:after_previous": 'stem "After/Nach [previous place]…"',
        "stem:where_contrast": 'stem "Where/Wo [previous]…"',
        "stem:wide_establish": 'stem "Across/Weit…" landscape open',
        "stem:here_begins": 'stem "Here/Hier…"',
    }
    return labels.get(key, key)


def build_opening_inventory_prompt_block(
    inventory: OpeningInventoryDocument,
    *,
    exclude_folder: str | None = None,
) -> str:
    counts = opening_key_counts(inventory, exclude_folder=exclude_folder)
    lines = [
        "SENTENCE OPENING INVENTORY (film-wide — same opening max "
        f"{MAX_OPENING_USES}×):",
        "",
        "RULES:",
        f"- Do NOT reuse an opening phrase or schematic stem that already appears "
        f"{MAX_OPENING_USES}× below (FORBIDDEN).",
        "- Especially avoid repeating \"After [previous place]…\" / "
        "\"Nach [vorheriger Ort]…\" once that stem is exhausted.",
        "- Vary HOW the chapter starts (fact, detail, atmosphere, mid-action) — "
        "not only the place name inside the same template.",
        "",
    ]
    visible = [
        e for e in inventory.entries if not exclude_folder or e.folder_name != exclude_folder
    ]
    if visible:
        lines.append("USED FIRST SENTENCES:")
        for entry in visible:
            lines.append(f'- "{entry.folder_name}": "{entry.first_sentence}"')
        lines.append("")
    else:
        lines.append("USED FIRST SENTENCES: (none yet)")
        lines.append("")

    forbidden = sorted(key for key, count in counts.items() if count >= MAX_OPENING_USES)
    if forbidden:
        lines.append("FORBIDDEN (already used 2× — do not start this way again):")
        for key in forbidden:
            lines.append(f"- {_human_key_label(key)} ({counts[key]}×)")
        lines.append("")
    else:
        lines.append("FORBIDDEN: (none yet)")
        lines.append("")

    approaching = sorted(
        (key, count)
        for key, count in counts.items()
        if 0 < count < MAX_OPENING_USES
    )
    if approaching:
        lines.append("NEAR LIMIT (one more use allowed, then forbidden):")
        for key, count in approaching:
            lines.append(f"- {_human_key_label(key)} ({count}/{MAX_OPENING_USES})")
    return "\n".join(lines).rstrip()
