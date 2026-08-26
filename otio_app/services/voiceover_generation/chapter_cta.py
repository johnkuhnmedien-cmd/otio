"""Filmweite Kapitelende-CTAs: Like + Dranbleiben.

Die Dramaturgie plant (welches Kapitel, Stay-Text im Referenzstil).
Das Kapitel-Skript spricht die Zeilen am Ende — zusätzlich zum Kapitelkörper.
"""

from __future__ import annotations

from otio_app.services.voiceover_generation.models import DramaturgyFolderEntry

__all__ = [
    "CTA_STAY_STYLE_REFERENCES_DE",
    "LIKE_CTA_TEMPLATES",
    "apply_chapter_ctas",
    "build_chapter_end_cta_prompt_block",
    "build_dramaturgy_cta_task_block",
    "film_has_planned_ctas",
    "later_enabled_folder_names",
    "like_cta_spoken_line",
    "normalize_chapter_ctas_after_llm",
    "sanitize_chapter_ctas",
]


# User-approved German style references (concreteness + short stay-ask).
# Other languages must imitate the shape, not paste the German wording.
CTA_STAY_STYLE_REFERENCES_DE: tuple[str, ...] = (
    "Später kommen wir zu einem kleinen Ort, der so abgeschieden liegt, dass ihn "
    "viele Reisende einfach auslassen. Dranbleiben lohnt sich hier wirklich!",
    "Noch später wartet ein Dorf, das mitten zwischen gewaltigen Bergen liegt und "
    "fast wie von der Außenwelt abgeschnitten wirkt. Das sollte man gesehen haben!",
    "Gegen Ende geht es zu einem Ort, bei dem schon die Anfahrt fast spektakulärer "
    "ist als das Ziel selbst. Nicht verpassen!",
    "Später sehen wir einen Ort, an dem die Häuser dicht an einem steilen Hang "
    "gebaut wurden. Allein die Lage ist ziemlich verrückt!",
)

LIKE_CTA_TEMPLATES: dict[str, str] = {
    "de": (
        'Bevor es mit "{next_chapter}" weitergeht, würde ich mich über ein Like freuen.'
    ),
    "en": 'Before we continue with "{next_chapter}", I\'d appreciate a like.',
    "fr": (
        'Avant de continuer avec « {next_chapter} », un like me ferait plaisir.'
    ),
    "es": 'Antes de seguir con "{next_chapter}", me alegraría un like.',
    "pt": 'Antes de continuarmos com "{next_chapter}", eu agradeceria um like.',
    "it": 'Prima di continuare con "{next_chapter}", un like mi farebbe piacere.',
    "ja": "「{next_chapter}」に進む前に、いいねを押してもらえると嬉しいです。",
    "ko": '"{next_chapter}"(으)로 이어지기 전에 좋아요를 눌러 주시면 좋겠습니다.',
}


def _language_key(language: str) -> str:
    return (language or "de").strip().lower() or "de"


def like_cta_spoken_line(language: str, next_chapter: str) -> str:
    """Feste Like-Bedeutung mit eingesetztem nächsten Kapitelnamen."""
    name = (next_chapter or "").strip()
    key = _language_key(language)
    template = LIKE_CTA_TEMPLATES.get(key) or LIKE_CTA_TEMPLATES["de"]
    return template.format(next_chapter=name)


def later_enabled_folder_names(
    entries: list[DramaturgyFolderEntry],
    *,
    current_folder_name: str,
) -> list[str]:
    """Aktivierte Kapitel nach dem aktuellen, in Filmreihenfolge."""
    ordered = sorted(
        (entry for entry in entries if entry.enabled),
        key=lambda entry: entry.order_index,
    )
    names = [entry.folder_name for entry in ordered]
    if current_folder_name not in names:
        return []
    index = names.index(current_folder_name)
    return names[index + 1 :]


def _enabled_in_order(
    entries: list[DramaturgyFolderEntry],
) -> list[DramaturgyFolderEntry]:
    return sorted(
        (entry for entry in entries if entry.enabled),
        key=lambda entry: entry.order_index,
    )


def _wipe_cta(entry: DramaturgyFolderEntry) -> DramaturgyFolderEntry:
    return entry.model_copy(
        update={
            "cta_like": False,
            "cta_stay": False,
            "cta_stay_text": "",
            "cta_stay_target_folders": [],
        }
    )


def _pick_cta_folder(
    names: list[str],
    *,
    prefer_index_ratio: float,
    avoid: set[str],
) -> str | None:
    if not names:
        return None
    last_index = len(names) - 1
    preferred_index = min(
        last_index, max(0, int(round(last_index * prefer_index_ratio)))
    )
    preferred = names[preferred_index]
    if preferred not in avoid:
        return preferred
    for name in names:
        if name not in avoid:
            return name
    return preferred


def _filter_stay_targets(
    targets: list[str],
    *,
    later_names: list[str],
) -> list[str]:
    allowed = set(later_names)
    filtered = [name for name in targets if name in allowed]
    return filtered or later_names[:2]


def apply_chapter_ctas(
    entries: list[DramaturgyFolderEntry],
    *,
    assign_if_missing: bool,
) -> list[DramaturgyFolderEntry]:
    """Höchstens ein Like und ein Stay; nie auf dem letzten aktiven Kapitel."""
    enabled = _enabled_in_order(entries)
    if len(enabled) <= 1:
        return [_wipe_cta(entry) for entry in entries]

    last_name = enabled[-1].folder_name
    eligible_names = [
        entry.folder_name for entry in enabled if entry.folder_name != last_name
    ]
    eligible_set = set(eligible_names)

    like_ordered = [
        entry.folder_name
        for entry in enabled
        if entry.cta_like and entry.folder_name in eligible_set
    ]
    stay_ordered = [
        entry
        for entry in enabled
        if entry.cta_stay and entry.folder_name in eligible_set
    ]

    like_name = like_ordered[0] if like_ordered else None
    stay_entry = next(
        (entry for entry in stay_ordered if (entry.cta_stay_text or "").strip()),
        stay_ordered[0] if stay_ordered else None,
    )
    stay_name = stay_entry.folder_name if stay_entry is not None else None

    stay_text = ""
    stay_targets_raw: list[str] = []
    if stay_entry is not None:
        stay_text = (stay_entry.cta_stay_text or "").strip()
        stay_targets_raw = list(stay_entry.cta_stay_target_folders)
    if not stay_text:
        for entry in enabled:
            text = (entry.cta_stay_text or "").strip()
            if text and entry.folder_name in eligible_set:
                stay_text = text
                stay_targets_raw = list(entry.cta_stay_target_folders)
                break

    if assign_if_missing:
        if like_name is None:
            like_name = _pick_cta_folder(
                eligible_names,
                prefer_index_ratio=0.66,
                avoid={stay_name} if stay_name else set(),
            )
        if stay_name is None:
            stay_name = _pick_cta_folder(
                eligible_names,
                prefer_index_ratio=0.33,
                avoid={like_name} if like_name else set(),
            )

    stay_later: list[str] = []
    if stay_name:
        stay_later = later_enabled_folder_names(entries, current_folder_name=stay_name)
    stay_targets = (
        _filter_stay_targets(stay_targets_raw, later_names=stay_later)
        if stay_name
        else []
    )

    updated: list[DramaturgyFolderEntry] = []
    for entry in entries:
        if not entry.enabled or entry.folder_name == last_name:
            updated.append(_wipe_cta(entry))
            continue
        is_like = bool(like_name) and entry.folder_name == like_name
        is_stay = bool(stay_name) and entry.folder_name == stay_name
        if is_stay:
            text = stay_text
            targets = stay_targets
        elif assign_if_missing:
            text = ""
            targets = []
        else:
            text = (entry.cta_stay_text or "").strip()
            targets = []
        updated.append(
            entry.model_copy(
                update={
                    "cta_like": is_like,
                    "cta_stay": is_stay,
                    "cta_stay_text": text,
                    "cta_stay_target_folders": targets,
                }
            )
        )
    return updated


def normalize_chapter_ctas_after_llm(
    entries: list[DramaturgyFolderEntry],
) -> list[DramaturgyFolderEntry]:
    """Nach LLM-Parse: genau ein Like + ein Stay auf zulässigen Kapiteln."""
    return apply_chapter_ctas(entries, assign_if_missing=True)


def sanitize_chapter_ctas(
    entries: list[DramaturgyFolderEntry],
) -> list[DramaturgyFolderEntry]:
    """Beim Bestätigen: letzte Kapitel säubern, nicht neu zuweisen."""
    return apply_chapter_ctas(entries, assign_if_missing=False)


def build_dramaturgy_cta_task_block() -> str:
    examples = "\n".join(f'- "{line}"' for line in CTA_STAY_STYLE_REFERENCES_DE)
    return f"""### Chapter-end CTAs (film-wide — spoken later by the chapter script)
Plan two closing viewer-asks. These are NOT neighbor transitions and NOT craft flags.

1) LIKE — exactly one chapter, never the last:
   Set cta_like=true. Do NOT write the spoken like line here. The chapter script
   will localize a fixed template that names the IMMEDIATE next chapter heading.
   Prefer a mid-to-late chapter that still has a next chapter.

2) STAY / "dran bleiben" — exactly one chapter, never the last:
   Prefer a DIFFERENT chapter than LIKE when more than one non-last chapter exists.
   Set cta_stay=true. Write cta_stay_text in the TARGET language (native-speaker).
   Put the later folder_name values being teased in cta_stay_target_folders
   (internal only). The spoken line must NOT name those folders or other place names.
   Tease one concrete setting/image from later chapters' chapter_themes (or a
   concrete image implied by a later folder — without saying the name).

Style for cta_stay_text (German samples are STYLE only — if the target language
is not German, write in the target language; imitate concreteness, not wording):
{examples}

DO:
- a time marker (later / still later / toward the end)
- one concrete place-image (remote village, houses on a steep slope, spectacular approach, …)
- a short stay-ask at the end
DON'T:
- abstract mood (emptiness, time, farewell, "something special awaits")
- spoil the later chapter's story
- name the next or later chapter heading
- put either CTA on the last chapter
- output more than one true cta_like or cta_stay

Both CTAs on the same non-last chapter is allowed only when few chapters remain.
cta_like / cta_stay are the only new per-chapter flags in this JSON — do not add
transition/callback/contrast checkboxes."""


def _silent_later_lines(
    entries: list[DramaturgyFolderEntry],
    *,
    current_folder_name: str,
) -> str:
    later_names = later_enabled_folder_names(
        entries, current_folder_name=current_folder_name
    )
    if not later_names:
        return "(no later chapters)"
    by_name = {entry.folder_name: entry for entry in entries}
    lines: list[str] = []
    for name in later_names:
        entry = by_name.get(name)
        reason = (entry.reason or "").strip() if entry is not None else ""
        if reason:
            lines.append(f'- {name} — {reason}')
        else:
            lines.append(f"- {name}")
    return "\n".join(lines)


def _stay_style_block() -> str:
    examples = "\n".join(f'- "{line}"' for line in CTA_STAY_STYLE_REFERENCES_DE)
    return (
        "STAY STYLE REFERENCES (German — imitate concreteness; write in LANGUAGE):\n"
        f"{examples}\n"
        "DO: time marker + concrete setting/image + short stay-ask.\n"
        "DON'T: abstract mood, place names, chapter headings, spoilers."
    )


def film_has_planned_ctas(entries: list[DramaturgyFolderEntry]) -> bool:
    return any(
        entry.enabled and (entry.cta_like or entry.cta_stay) for entry in entries
    )


def build_chapter_end_cta_prompt_block(
    *,
    entry: DramaturgyFolderEntry,
    entries: list[DramaturgyFolderEntry] | None = None,
    next_folder_name: str | None,
    language: str,
) -> str:
    """Spoken closing-CTA instructions for the chapter script / classic VO.

    Empty string when the film has no planned CTAs (legacy plans keep
    optional rhetoric stay-tuned). When CTAs are planned, every chapter
    gets either a speak-block or a forbid-block.
    """
    all_entries = list(entries or [entry])
    if not film_has_planned_ctas(all_entries):
        return ""

    parts = [
        "CHAPTER-END CTAs (BINDING — spoken after the independent chapter body)",
        "",
        "These beats OUTRANK the independent-chapter rule for the CLOSING only.",
        "They are extra words: do not shorten the chapter body to make room.",
        "They are NOT journey/transition formulas and do NOT set "
        "chapter_link_usage.to_next unless a separate neighbor-bridge permission "
        "is already ALLOWED.",
        "semantic_function for these beats: cta_stay and/or cta_like.",
        "",
    ]

    speak_stay = bool(entry.cta_stay)
    speak_like = bool(entry.cta_like) and bool((next_folder_name or "").strip())

    if not speak_stay and not speak_like:
        parts.extend(
            [
                "THIS CHAPTER: no CTA assigned.",
                "Do NOT add a like-ask.",
                "Do NOT add a stay-tuned / dranbleiben tease — that slot is reserved "
                "for the planned CTA chapter.",
                "Do NOT claim rhetoric slot stay_tuned_payoff.",
            ]
        )
        return "\n".join(parts)

    parts.append("THIS CHAPTER: speak the assigned closing CTA(s) in this order:")
    if speak_stay and speak_like:
        parts.append("1) stay tease  2) like-ask (last spoken beat).")
    elif speak_stay:
        parts.append("1) stay tease as the last spoken beat(s).")
    else:
        parts.append("1) like-ask as the last spoken beat.")
    parts.append("")

    if speak_stay:
        stay_text = (entry.cta_stay_text or "").strip()
        silent_later = _silent_later_lines(
            all_entries, current_folder_name=entry.folder_name
        )
        parts.append("STAY / DRANBLEIBEN (SPOKEN):")
        if stay_text:
            parts.append(
                "Speak this tease. You may polish grammar; keep the concrete image "
                "and the stay-ask. Do not make it vaguer. Do not name folder names:"
            )
            parts.append(f'"{stay_text}"')
        else:
            parts.append(
                "Write ONE stay-tease in LANGUAGE in the reference style, using a "
                "concrete image from later chapters. Do not name them."
            )
            parts.append(_stay_style_block())
        parts.append("Later chapters (SILENT — do not speak these names):")
        parts.append(silent_later)
        if entry.cta_stay_target_folders:
            parts.append(
                "Internal tease targets (do not speak): "
                + ", ".join(entry.cta_stay_target_folders)
            )
        parts.append(
            "If you claim rhetoric slot stay_tuned_payoff, evidence_quote must be "
            "this stay tease — do not invent a second stay-tuned line."
        )
        parts.append("")

    if speak_like:
        line = like_cta_spoken_line(language, next_folder_name or "")
        key = _language_key(language)
        parts.append("LIKE (SPOKEN — last beat if both CTAs):")
        parts.append(
            "Speak this meaning. Keep the next chapter name exactly as written. "
            "You may only adjust small grammar particles. Do not change the ask. "
            "Do not add travel/journey language."
        )
        if key in LIKE_CTA_TEMPLATES:
            parts.append(f'"{line}"')
        else:
            parts.append("Localize into LANGUAGE; keep the chapter name exact:")
            parts.append(f'DE: "{like_cta_spoken_line("de", next_folder_name or "")}"')
            parts.append(f'EN: "{like_cta_spoken_line("en", next_folder_name or "")}"')
        parts.append(
            "This like-ask is NOT a neighbor transition and does NOT count as "
            "chapter_link_usage.to_next."
        )
        parts.append("")

    if speak_stay and not speak_like:
        parts.append("Do NOT add a like-ask in this chapter.")
    if speak_like and not speak_stay:
        parts.append("Do NOT add a stay-tuned / dranbleiben tease besides the like-ask.")

    return "\n".join(parts).rstrip() + "\n"
