"""Nachbar-Kontext für Enhanced-Skript-Prompts (Schritt ④).

- Kapitelliste (Überschriften + Rolle + Reason) immer in Filmreihenfolge
- Erste/letzte Sätze der zwei unmittelbar vorherigen Kapitel (ab Kapitel 3)
- Gesprochene Übergänge nur bei expliziten FolderVoiceoverSetting-Flags
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
    "build_film_wide_editorial_links_block",
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
    chapters: list[DramaturgyFolderEntry] | list[str],
    *,
    current_folder_name: str,
) -> str:
    """Nummerierte Kapitelliste; bei Entries inkl. Rolle + Reason-Zeile."""
    map_lines: list[str] = []
    note_lines: list[str] = []
    for index, item in enumerate(chapters, start=1):
        if isinstance(item, str):
            name = item
            role = ""
            reason = ""
        else:
            name = item.folder_name
            role = (item.dramaturgy_role or "").strip()
            reason = (item.reason or "").strip()
        marker = " ← THIS CHAPTER" if name == current_folder_name else ""
        role_part = f"  [{role}]" if role else ""
        map_lines.append(f"{index:2d}. {name}{role_part}{marker}")
        if reason:
            note_lines.append(f"{index:2d}. {name} — {reason}")

    blocks = [
        "FILM CHAPTER MAP — SILENT EDITORIAL METADATA (NOT spoken narration):",
        "",
        "Use this map only for silent film orientation: order, emphasis, contrast.",
        "Do NOT recite, summarize, or verbalize the map, roles, reasons, or chapter order.",
        "Do NOT invent spoken bridges from this map unless SPOKEN CHAPTER LINK "
        "PERMISSIONS explicitly allow a direction.",
        "",
        *map_lines,
    ]
    if note_lines:
        blocks.extend(
            [
                "",
                "CHAPTER EDITORIAL NOTES — SILENT METADATA ONLY "
                "(do NOT treat as spoken copy; do NOT invent beyond these):",
                "",
                *note_lines,
            ]
        )
    return "\n".join(blocks)


def build_film_wide_editorial_links_block(
    *,
    allow_callback: bool = False,
    allow_forward_glance: bool = False,
) -> str:
    if not allow_callback and not allow_forward_glance:
        return (
            "FILM-WIDE EDITORIAL LINKS:\n"
            "No film-wide spoken link is permitted for this chapter.\n"
            "Use the chapter map only for silent editorial orientation."
        )
    lines = [
        "FILM-WIDE EDITORIAL LINKS (only where explicitly permitted):",
    ]
    if allow_forward_glance:
        lines.append(
            "- FORWARD GLANCE: You MAY briefly hint at a LATER chapter when "
            "editorially justified — claim stay_tuned_payoff / named_future_highlight."
        )
    else:
        lines.append("- FORWARD GLANCE: FORBIDDEN for this chapter.")
    if allow_callback:
        lines.append(
            "- CALLBACK: You MAY briefly recall an EARLIER non-adjacent chapter — "
            "claim callback_early_chapter / distant_contrast / distant_commonality."
        )
    else:
        lines.append("- CALLBACK: FORBIDDEN for this chapter.")
    lines.append("- Prefer at most ONE such film-wide link in this chapter.")
    lines.append("- Never spoil concrete reveals from later payoff chapters.")
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
        "RECENT NEIGHBOR NARRATION (first/last sentence only — opening variety context, do NOT copy):",
        "",
        "OPENING VARIETY (BINDING):",
        "- Do NOT open this chapter with a sentence that mirrors the opening pattern,",
        "  rhythm, or stock phrasing of the recent chapters below.",
        "- Vary structure and entry point — prefer a direct place/fact opening.",
        "- Neighbor excerpts are NOT permission to mention those chapters.",
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


def build_editorial_neighbor_craft_block(
    *,
    entry: DramaturgyFolderEntry,
    setting: FolderVoiceoverSetting | None,
    previous_folder_name: str | None,
    next_folder_name: str | None,
) -> str:
    """Gesprochene Nachbarlinks — nur bei aktiven FolderVoiceoverSetting-Flags."""
    contrast_hint = (entry.contrast_or_commonality_hint or "").strip()
    from_hint = (entry.transition_from_previous_hint or "").strip()
    to_hint = (entry.transition_goal_to_next or "").strip()

    flags_active = _craft_flags_active(setting)
    if not flags_active:
        return (
            "EDITORIAL NEIGHBOR LINKS:\n"
            "No spoken neighboring-chapter link is permitted.\n"
            "Do not mention departure, arrival, route, journey continuation, "
            "the previous chapter, or the next chapter."
        )

    lines = [
        "EDITORIAL NEIGHBOR LINKS (apply ONLY where the matching setting is ALLOWED — "
        "never invent road/departure/arrival formulas):",
    ]

    assert setting is not None
    if setting.transition_from_previous:
        if from_hint:
            lines.append(f"- transition from previous hint: {from_hint}")
        if previous_folder_name:
            lines.append(
                f"- OPEN with exactly one short thematic/geographic/historical bridge "
                f'from "{previous_folder_name}" — not a pure travel formula.'
            )
        else:
            lines.append(
                "- OPEN with exactly one short thematic bridge from the previous "
                "chapter — not a pure travel formula."
            )
    if setting.transition_to_next:
        if to_hint:
            lines.append(f"- transition goal toward next: {to_hint}")
        if next_folder_name:
            lines.append(
                f'- END with exactly one short forward bridge toward "{next_folder_name}" '
                f"— teaser only, no spoilers, no journey lecture."
            )
        else:
            lines.append(
                "- END with exactly one short forward bridge toward the next chapter "
                "— teaser only, no spoilers."
            )
    if setting.callback_to_previous:
        if previous_folder_name:
            lines.append(
                f'- Later in the text, a brief CALLBACK to "{previous_folder_name}" '
                f"is requested (not a departure scene)."
            )
        else:
            lines.append(
                "- Later in the text, a brief CALLBACK to the previous chapter "
                "is requested (not a departure scene)."
            )
    if setting.use_contrast_with_previous:
        if contrast_hint and (
            setting.use_contrast_with_previous or setting.use_commonality_with_previous
        ):
            lines.append(f"- contrast or commonality hint: {contrast_hint}")
        if previous_folder_name:
            lines.append(
                f'- Weave in a meaningful CONTRAST with "{previous_folder_name}" '
                f"in fact selection/framing. Do NOT open with a travel bridge unless "
                f"transition_from_previous is also ALLOWED."
            )
        else:
            lines.append(
                "- Weave in a meaningful CONTRAST with the previous chapter in fact "
                "selection/framing — no travel bridge unless transition_from_previous "
                "is ALLOWED."
            )
    if setting.use_commonality_with_previous:
        if contrast_hint and not setting.use_contrast_with_previous:
            lines.append(f"- contrast or commonality hint: {contrast_hint}")
        if previous_folder_name:
            lines.append(
                f'- Weave in a meaningful COMMONALITY with "{previous_folder_name}" '
                f"in fact selection/framing. Do NOT open with a travel bridge unless "
                f"transition_from_previous is also ALLOWED."
            )
        else:
            lines.append(
                "- Weave in a meaningful COMMONALITY with the previous chapter in fact "
                "selection/framing — no travel bridge unless transition_from_previous "
                "is ALLOWED."
            )

    return "\n".join(lines)
