"""Nachbar-Kontext für Enhanced-Skript-Prompts (Schritt ④).

- Kapitelliste (nur Überschriften) immer in Filmreihenfolge
- Erste/letzte Sätze der zwei unmittelbar vorherigen Kapitel (ab Kapitel 3)
- Kontrast/Gemeinsamkeit/Übergänge nur bei explizitem dramaturgischem Brief
"""

from __future__ import annotations

from otio_app.services.voiceover_generation.models import (
    DramaturgyFolderEntry,
    FolderVoiceoverSetting,
)
from otio_app.services.without_voiceover_enhanced.segment_alignment_service import (
    split_segment_into_sentences,
)

__all__ = [
    "first_and_last_sentence",
    "build_chapter_order_block",
    "build_recent_neighbor_excerpts_block",
    "build_editorial_neighbor_craft_block",
    "recent_prior_chapter_excerpts",
]


def first_and_last_sentence(narration: str) -> tuple[str | None, str | None]:
    """Erster und letzter Satz aus Kapitel-Narration (Satzsplit wie TTS-Alignment)."""
    sentences = split_segment_into_sentences(narration)
    if not sentences:
        return None, None
    if len(sentences) == 1:
        return sentences[0], sentences[0]
    return sentences[0], sentences[-1]


def build_chapter_order_block(
    chapter_names: list[str],
    *,
    current_folder_name: str,
) -> str:
    lines: list[str] = []
    for index, name in enumerate(chapter_names, start=1):
        marker = " ← THIS CHAPTER" if name == current_folder_name else ""
        lines.append(f"{index}. {name}{marker}")
    return "\n".join(lines)


def recent_prior_chapter_excerpts(
    *,
    prior_folder_names: list[str],
    narration_for_folder: dict[str, str],
) -> list[tuple[str, str, str]]:
    """(folder_name, first_sentence, last_sentence) für vorherige Kapitel mit Skript."""
    excerpts: list[tuple[str, str, str]] = []
    for folder_name in prior_folder_names:
        narration = (narration_for_folder.get(folder_name) or "").strip()
        if not narration:
            continue
        first, last = first_and_last_sentence(narration)
        if not first or not last:
            continue
        excerpts.append((folder_name, first, last))
    return excerpts


def build_recent_neighbor_excerpts_block(
    excerpts: list[tuple[str, str, str]],
) -> str:
    if not excerpts:
        return ""
    lines = [
        "RECENT NEIGHBOR NARRATION (first/last sentence only — context, do NOT copy):",
        "",
        "OPENING VARIETY (BINDING):",
        "- Do NOT open this chapter with a sentence that mirrors the opening pattern,",
        "  rhythm, or stock phrasing of the recent chapters below.",
        "- Vary structure and entry point — not every chapter should start with the same",
        "  landscape, time-of-day, or \"Hier beginnt…\" template.",
        "",
    ]
    for folder_name, first_sentence, last_sentence in excerpts:
        lines.append(f'Chapter "{folder_name}":')
        lines.append(f'  first sentence: "{first_sentence}"')
        lines.append(f'  last sentence: "{last_sentence}"')
        lines.append("")
    return "\n".join(lines).rstrip()


def _craft_flags_active(setting: FolderVoiceoverSetting | None) -> bool:
    if setting is None:
        return False
    return any(
        (
            setting.transition_from_previous,
            setting.transition_to_next,
            setting.callback_to_previous,
            setting.use_contrast_with_previous,
            setting.use_commonality_with_previous,
        )
    )


def _dramaturgy_neighbor_hints_active(entry: DramaturgyFolderEntry) -> bool:
    return any(
        (
            (entry.transition_from_previous_hint or "").strip(),
            (entry.transition_goal_to_next or "").strip(),
            (entry.contrast_or_commonality_hint or "").strip(),
        )
    )


def build_editorial_neighbor_craft_block(
    *,
    entry: DramaturgyFolderEntry,
    setting: FolderVoiceoverSetting | None,
    previous_folder_name: str | None,
    next_folder_name: str | None,
) -> str:
    """Kontrast/Gemeinsamkeit/Übergänge — nur wenn Brief oder Flags aktiv sind."""
    contrast_hint = (entry.contrast_or_commonality_hint or "").strip()
    from_hint = (entry.transition_from_previous_hint or "").strip()
    to_hint = (entry.transition_goal_to_next or "").strip()

    flags_active = _craft_flags_active(setting)
    hints_active = _dramaturgy_neighbor_hints_active(entry)

    if not flags_active and not hints_active:
        return (
            "EDITORIAL NEIGHBOR LINKS:\n"
            "- No explicit contrast, commonality, or transition brief for this chapter.\n"
            "- Write a self-contained section. You may glance at FILM CHAPTER ORDER for "
            "orientation, but do NOT force bridges, teasers, callbacks, contrast, or "
            "commonality unless they arise naturally from the place itself."
        )

    lines = [
        "EDITORIAL NEIGHBOR LINKS (apply ONLY where the brief below is meaningful — "
        "do not pad every chapter with forced bridges):",
    ]
    if from_hint:
        lines.append(f"- transition from previous hint: {from_hint}")
    if to_hint:
        lines.append(f"- transition goal toward next: {to_hint}")
    if contrast_hint:
        lines.append(f"- contrast or commonality hint: {contrast_hint}")

    if setting is not None:
        if setting.transition_from_previous and previous_folder_name:
            lines.append(
                f"- OPEN with a short bridge from the previous chapter "
                f'("{previous_folder_name}") near the START — only if editorially justified.'
            )
        if setting.transition_to_next and next_folder_name:
            lines.append(
                f'- END with a brief forward-looking bridge toward the VERY NEXT chapter '
                f'("{next_folder_name}") — teaser only, no spoilers; it follows immediately.'
            )
        if setting.callback_to_previous and previous_folder_name:
            lines.append(
                f"- Later in the text, a brief CALLBACK to "
                f'"{previous_folder_name}" is requested.'
            )
        if setting.use_contrast_with_previous and previous_folder_name:
            lines.append(
                f'- Weave in a meaningful CONTRAST with the previous chapter '
                f'("{previous_folder_name}") where it genuinely helps the story.'
            )
        if setting.use_commonality_with_previous and previous_folder_name:
            lines.append(
                f'- Weave in a meaningful COMMONALITY with the previous chapter '
                f'("{previous_folder_name}") where it genuinely helps the story.'
            )

    if hints_active and not flags_active:
        lines.append(
            "- Use the hints above only where they fit naturally — skip forced segues "
            "if the place does not support them."
        )

    return "\n".join(lines)
