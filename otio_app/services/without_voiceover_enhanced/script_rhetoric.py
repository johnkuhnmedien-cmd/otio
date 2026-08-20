"""Filmweites Rhetoric-Slot-Ledger für Enhanced-Skript-Prompts (Schritt ④).

Jeder Slot darf filmweit höchstens einmal beansprucht werden. Das LLM meldet
Claims in ``rhetoric_usage``; Python speichert und validiert sie.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from pydantic import BaseModel, Field

from otio_app.models import Project
from otio_app.services.without_voiceover_enhanced.io_utils import load_model, write_json
from otio_app.services.without_voiceover_enhanced.paths import script_rhetoric_ledger_path

MAX_RHETORIC_CLAIMS_PER_CHAPTER = 2

__all__ = [
    "MAX_RHETORIC_CLAIMS_PER_CHAPTER",
    "RHETORIC_SLOTS",
    "RhetoricClaim",
    "RhetoricLedgerDocument",
    "RhetoricSlotDef",
    "RhetoricUsageItem",
    "build_rhetoric_ledger_prompt_block",
    "clear_rhetoric_ledger",
    "load_rhetoric_ledger",
    "merge_rhetoric_claims_for_folder",
    "parse_rhetoric_usage",
    "remove_rhetoric_claims_for_folder",
    "rhetoric_slot_ids",
    "save_rhetoric_ledger",
    "validate_rhetoric_usage_against_ledger",
]


@dataclass(frozen=True)
class RhetoricSlotDef:
    slot_id: str
    label: str
    description: str
    example_phrases: tuple[str, ...]


RHETORIC_SLOTS: tuple[RhetoricSlotDef, ...] = (
    RhetoricSlotDef(
        slot_id="stay_tuned_payoff",
        label="Stay-tuned / dranbleiben",
        description=(
            "Tell the viewer it is worth staying because something unique "
            "still awaits — once per film."
        ),
        example_phrases=(
            "Es lohnt sich, dranzubleiben …",
            "Stay with us — later …",
        ),
    ),
    RhetoricSlotDef(
        slot_id="named_future_highlight",
        label="Named future highlight",
        description=(
            "Name a LATER chapter (not only the next one) as an upcoming "
            "highlight — once per film. Must match a heading from FILM CHAPTER MAP."
        ),
        example_phrases=(
            "… in Antelope Canyon …",
            "… later at Horseshoe Bend …",
        ),
    ),
    RhetoricSlotDef(
        slot_id="callback_early_chapter",
        label="Callback to early chapter",
        description=(
            "Briefly recall an EARLIER chapter that is not only the immediate "
            "previous neighbor — once per film."
        ),
        example_phrases=(
            "Wie schon in Sedona …",
            "As earlier in …",
        ),
    ),
    RhetoricSlotDef(
        slot_id="film_arc_echo",
        label="Film-arc echo",
        description=(
            "Echo the film's core promise / narrative arc in spoken narration — "
            "once per film."
        ),
        example_phrases=(
            "Genau dafür sind wir unterwegs …",
            "This is the journey we promised …",
        ),
    ),
    RhetoricSlotDef(
        slot_id="superlative_unique_once",
        label="Unique / once-in-a-film superlative",
        description=(
            "Claim uniqueness in the film sense (\"einmalig\", \"nirgends sonst\", "
            "\"only here\") — once per film. Do not invent unsupported superlatives."
        ),
        example_phrases=(
            "… so nur hier …",
            "… nirgends sonst …",
        ),
    ),
    RhetoricSlotDef(
        slot_id="distant_contrast",
        label="Distant contrast",
        description=(
            "Contrast THIS place with a non-adjacent chapter (2+ chapters away) — "
            "once per film."
        ),
        example_phrases=(
            "Ganz anders als in …",
            "Unlike … earlier …",
        ),
    ),
    RhetoricSlotDef(
        slot_id="distant_commonality",
        label="Distant commonality",
        description=(
            "Link THIS place to a non-adjacent chapter via a shared theme — "
            "once per film."
        ),
        example_phrases=(
            "Wie dort auch …",
            "The same thread returns in …",
        ),
    ),
    RhetoricSlotDef(
        slot_id="opener_rhetorical_question",
        label="Opener: rhetorical question",
        description="Open the chapter with a rhetorical question — once per film.",
        example_phrases=(
            "Was, wenn …?",
            "Who would guess …?",
        ),
    ),
    RhetoricSlotDef(
        slot_id="opener_time_of_day",
        label="Opener: time of day",
        description=(
            "Open with a time-of-day framing (morning, dusk, night) as the "
            "chapter's first move — once per film."
        ),
        example_phrases=(
            "Am Morgen, wenn …",
            "In der Dämmerung …",
        ),
    ),
    RhetoricSlotDef(
        slot_id="opener_wide_landscape",
        label="Opener: wide landscape",
        description=(
            "Open with a classic wide-landscape establishing line — once per film."
        ),
        example_phrases=(
            "Weit breitet sich …",
            "Across the open land …",
        ),
    ),
)


def rhetoric_slot_ids() -> frozenset[str]:
    return frozenset(slot.slot_id for slot in RHETORIC_SLOTS)


class RhetoricClaim(BaseModel):
    slot_id: str
    folder_name: str
    evidence_quote: str = ""
    related_chapter_ref: str = ""


class RhetoricLedgerDocument(BaseModel):
    schema_version: str = "enhanced-rhetoric-ledger-v1"
    claims: list[RhetoricClaim] = Field(default_factory=list)


class RhetoricUsageItem(BaseModel):
    """Parsed LLM claim for one chapter (used=true only)."""

    slot_id: str
    evidence_quote: str = ""
    related_chapter_ref: str = ""


def load_rhetoric_ledger(project: Project) -> RhetoricLedgerDocument:
    loaded = load_model(script_rhetoric_ledger_path(project), RhetoricLedgerDocument)
    return loaded or RhetoricLedgerDocument()


def save_rhetoric_ledger(project: Project, ledger: RhetoricLedgerDocument) -> None:
    write_json(script_rhetoric_ledger_path(project), ledger)


def clear_rhetoric_ledger(project: Project) -> None:
    save_rhetoric_ledger(project, RhetoricLedgerDocument())


def remove_rhetoric_claims_for_folder(
    ledger: RhetoricLedgerDocument, folder_name: str
) -> RhetoricLedgerDocument:
    return RhetoricLedgerDocument(
        schema_version=ledger.schema_version,
        claims=[c for c in ledger.claims if c.folder_name != folder_name],
    )


def merge_rhetoric_claims_for_folder(
    ledger: RhetoricLedgerDocument,
    *,
    folder_name: str,
    usage: list[RhetoricUsageItem],
) -> RhetoricLedgerDocument:
    cleaned = remove_rhetoric_claims_for_folder(ledger, folder_name)
    new_claims = [
        RhetoricClaim(
            slot_id=item.slot_id,
            folder_name=folder_name,
            evidence_quote=item.evidence_quote,
            related_chapter_ref=item.related_chapter_ref,
        )
        for item in usage
    ]
    return RhetoricLedgerDocument(
        schema_version=cleaned.schema_version,
        claims=list(cleaned.claims) + new_claims,
    )


def parse_rhetoric_usage(raw: str | dict | list | None) -> list[RhetoricUsageItem]:
    """Extrahiert used=true Claims aus LLM-JSON (rhetoric_usage)."""
    if raw is None:
        return []
    if isinstance(raw, dict):
        items = raw.get("rhetoric_usage")
        if items is None and "slot_id" in raw:
            items = [raw]
        else:
            items = items or []
    elif isinstance(raw, list):
        items = raw
    else:
        return []

    known = rhetoric_slot_ids()
    parsed: list[RhetoricUsageItem] = []
    seen: set[str] = set()
    for item in items:
        if not isinstance(item, dict):
            continue
        slot_id = str(item.get("slot_id") or "").strip()
        if not slot_id or slot_id not in known:
            continue
        used = item.get("used", True)
        if used is False or str(used).lower() in {"false", "0", "no"}:
            continue
        if slot_id in seen:
            continue
        seen.add(slot_id)
        parsed.append(
            RhetoricUsageItem(
                slot_id=slot_id,
                evidence_quote=str(item.get("evidence_quote") or "").strip(),
                related_chapter_ref=str(item.get("related_chapter_ref") or "").strip(),
            )
        )
    return parsed


_WS_RE = re.compile(r"\s+")


def _normalize_text(text: str) -> str:
    return _WS_RE.sub(" ", (text or "").strip().lower())


def _quote_in_narration(quote: str, narration: str) -> bool:
    q = _normalize_text(quote)
    n = _normalize_text(narration)
    if not q or not n:
        return False
    if q in n:
        return True
    # allow short paraphrase: require first 24 chars of quote if long enough
    if len(q) >= 24 and q[:24] in n:
        return True
    return False


def validate_rhetoric_usage_against_ledger(
    *,
    usage: list[RhetoricUsageItem],
    ledger: RhetoricLedgerDocument,
    folder_name: str,
    narration_full: str,
) -> list[str]:
    """Gibt Fehlerliste zurück (leer = OK). Ledger ohne Claims dieses Ordners."""
    errors: list[str] = []
    if len(usage) > MAX_RHETORIC_CLAIMS_PER_CHAPTER:
        errors.append(
            f"Zu viele Rhetoric-Claims ({len(usage)}>{MAX_RHETORIC_CLAIMS_PER_CHAPTER})."
        )
    known = rhetoric_slot_ids()
    occupied = {
        claim.slot_id: claim
        for claim in ledger.claims
        if claim.folder_name != folder_name
    }
    seen: set[str] = set()
    for item in usage:
        if item.slot_id not in known:
            errors.append(f"Unbekannter slot_id: {item.slot_id}")
            continue
        if item.slot_id in seen:
            errors.append(f"Doppelter slot_id in Antwort: {item.slot_id}")
            continue
        seen.add(item.slot_id)
        prior = occupied.get(item.slot_id)
        if prior is not None:
            errors.append(
                f"Slot „{item.slot_id}“ bereits in „{prior.folder_name}“ belegt "
                f"(„{prior.evidence_quote[:80]}“)."
            )
        if not item.evidence_quote:
            errors.append(f"Slot „{item.slot_id}“ ohne evidence_quote.")
        elif not _quote_in_narration(item.evidence_quote, narration_full):
            errors.append(
                f"evidence_quote für „{item.slot_id}“ kommt nicht in narration_full vor."
            )
    return errors


def build_rhetoric_ledger_prompt_block(ledger: RhetoricLedgerDocument) -> str:
    used_ids = {claim.slot_id for claim in ledger.claims}
    lines = [
        "RHETORIC SLOT LEDGER (film-wide — each slot AT MOST ONCE in the whole film):",
        "",
        "CLAIM RULES:",
        f"- Prefer ZERO claims. At most {MAX_RHETORIC_CLAIMS_PER_CHAPTER} used:true "
        "slots in THIS chapter.",
        "- If you use a slot, set used=true and evidence_quote to a phrase that "
        "appears VERBATIM (or near-verbatim) in narration_full.",
        "- related_chapter_ref: set when naming another chapter from FILM CHAPTER MAP; "
        "else empty string.",
        "- Do NOT reuse ALREADY USED slots or close paraphrases of their device.",
        "- Do NOT invent future highlights — only if supported by THIS CHAPTER "
        "DRAMATURGY / verified facts.",
        "",
    ]
    if ledger.claims:
        lines.append("ALREADY USED — do NOT reuse:")
        for claim in ledger.claims:
            quote = (claim.evidence_quote or "(no quote)").strip()
            if len(quote) > 80:
                quote = quote[:77].rstrip() + "..."
            ref = f" → {claim.related_chapter_ref}" if claim.related_chapter_ref else ""
            lines.append(
                f'- {claim.slot_id} @ "{claim.folder_name}": "{quote}"{ref}'
            )
        lines.append("")
    else:
        lines.append("ALREADY USED: (none yet)")
        lines.append("")

    lines.append("AVAILABLE (ids only — use ONLY if editorially justified):")
    available = [slot for slot in RHETORIC_SLOTS if slot.slot_id not in used_ids]
    if not available:
        lines.append("- (none — all slots already claimed)")
    else:
        for slot in available:
            lines.append(f"- {slot.slot_id}: {slot.description}")
    return "\n".join(lines)
