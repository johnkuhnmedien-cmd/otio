"""Persistenz für Style-Referenzen (Beispielskripte) — Projekt ohne Voice-Over.

Nur Klartext (.txt/.md) wird unterstützt — keine PDF/DOCX-Verarbeitung.
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path

from otio_app.models import Project
from otio_app.project_layout import (
    get_style_references_uploads_dir,
    get_voiceover_style_references_path,
)
from otio_app.services.voiceover_generation.models import (
    STYLE_MODE_CHOICES,
    STYLE_MODE_PROFILE,
    STYLE_MODE_RAW_TEXT,
    VoiceoverStyleProfile,
    VoiceoverStyleReferences,
)
from otio_app.services.voiceover_generation.style_reference_defaults_service import (
    apply_language_defaults_to_refs,
    load_language_style_defaults,
)

ALLOWED_UPLOAD_EXTENSIONS = (".txt", ".md")
MAX_UPLOAD_CHARS = 20_000
MAX_RAW_REFERENCE_CHARS = 40_000


def default_style_references(project: Project) -> VoiceoverStyleReferences:
    """Ausgangswerte: Sprachstandard falls vorhanden, sonst leere Referenzen."""
    refs = VoiceoverStyleReferences(project_id=project.id)
    defaults = load_language_style_defaults(project.language)
    if defaults is None:
        return refs
    return apply_language_defaults_to_refs(refs, defaults)


def apply_language_style_defaults_to_project(project: Project) -> VoiceoverStyleReferences:
    """Schreibt den Sprachstandard in das Projekt (Referenzen und ggf. Profile)."""
    defaults = load_language_style_defaults(project.language)
    refs = VoiceoverStyleReferences(project_id=project.id)
    if defaults is not None:
        refs = apply_language_defaults_to_refs(refs, defaults)
    saved = save_style_references(project, refs)
    if (
        defaults is not None
        and defaults.style_profile is not None
        and not is_raw_style_mode(saved)
    ):
        from otio_app.services.voiceover_generation.style_profile_service import (
            save_style_profile,
        )

        save_style_profile(project, defaults.style_profile)
    return saved


def normalize_style_mode(style_mode: str | None) -> str:
    mode = (style_mode or STYLE_MODE_PROFILE).strip().lower()
    if mode in STYLE_MODE_CHOICES:
        return mode
    return STYLE_MODE_PROFILE


def is_raw_style_mode(refs: VoiceoverStyleReferences) -> bool:
    return normalize_style_mode(refs.style_mode) == STYLE_MODE_RAW_TEXT


def format_raw_chapter_reference_for_prompts(raw_text: str) -> str:
    """Binding prose-architecture block for Enhanced chapter scripts."""
    from otio_app.services.without_voiceover_enhanced.raw_chapter_style_structure import (
        analyze_raw_chapter_style_structure,
        format_raw_chapter_structure_signals,
        prepare_raw_chapter_reference,
    )

    prepared = prepare_raw_chapter_reference(raw_text)
    if not prepared.cleaned_text.strip():
        return (
            "(kein Raw-Style-Text hinterlegt — neutraler dokumentarischer Standardstil)"
        )
    structure = analyze_raw_chapter_style_structure(prepared)
    signals = format_raw_chapter_structure_signals(structure)
    return (
        "RAW CHAPTER PROSE REFERENCE — BINDING PROSE ARCHITECTURE\n\n"
        "Use the reference below as the primary prose-architecture model for this chapter.\n\n"
        "Silently analyze and reproduce its:\n"
        "- sentence-length distribution\n"
        "- paragraph and beat rhythm\n"
        "- factual density\n"
        "- directness of openings\n"
        "- ratio of factual explanation to atmosphere\n"
        "- use of concrete landmarks, names, dates and visible details\n"
        "- narrator distance and formality\n"
        "- amount of metaphor and personification\n"
        "- way of moving between subjects inside one location\n\n"
        "Preserve the reference's LEVEL OF DIRECTNESS.\n\n"
        "Do not merely borrow its general mood.\n\n"
        "Do not copy:\n"
        "- wording\n"
        "- sentences\n"
        "- facts\n"
        "- dates\n"
        "- place names\n"
        "- unique metaphors\n\n"
        "When this reference conflicts with generic style advice, follow this reference "
        "for prose form. Factuality, schema and explicit editor instructions still remain "
        "binding.\n\n"
        f"{signals}\n\n"
        "REFERENCE TEXT:\n"
        f"{prepared.cleaned_text}"
    )


def format_raw_style_reference_for_prompts(
    raw_text: str,
    *,
    label: str = "RAW STYLE REFERENCE",
    as_structural_template: bool = False,
    for_chapter: bool = False,
) -> str:
    text = (raw_text or "").strip()
    if not text:
        return (
            "(kein Raw-Style-Text hinterlegt — neutraler dokumentarischer Standardstil)"
        )
    if for_chapter:
        return format_raw_chapter_reference_for_prompts(text)
    if as_structural_template:
        return (
            f"{label} — STRUCTURAL TEMPLATE for the Intro.\n"
            "Mirror this Intro's STRUCTURE only: beat order, vignette rhythm, "
            "pause/pacing markers if present, escalation to naming the place, "
            "open questions, and host/promise close.\n"
            "Write NEW content for THIS project (different places, facts, angles). "
            "Do NOT copy wording, place names, or sentences verbatim:\n"
            f"{text}"
        )
    return (
        f"{label} — use only as style inspiration; "
        "do not copy wording or sentences verbatim:\n"
        f"{text}"
    )


def format_style_profile_summary_for_prompts(
    style_profile: VoiceoverStyleProfile | None,
) -> str:
    if style_profile is None:
        return "(kein Style Profile vorhanden — neutraler dokumentarischer Standardstil)"
    return (
        f"- overall_tone: {style_profile.overall_tone or '-'}\n"
        f"- narration_style: {style_profile.narration_style or '-'}\n"
        f"- sentence_length: {style_profile.sentence_length or '-'}\n"
        f"- pacing: {style_profile.pacing or '-'}\n"
        f"- imagery_style: {style_profile.imagery_style or '-'}\n"
        f"- segment_style: {style_profile.segment_style or '-'}\n"
        f"- style_summary_for_prompts: {style_profile.style_summary_for_prompts or '-'}"
    )


def style_context_text_for_prompts(
    project: Project,
    *,
    detailed: bool = False,
    for_intro: bool = False,
    for_chapter: bool = False,
) -> str:
    """Textblock für LLM-Prompts: Raw-Referenz oder Style Profile.

    for_intro=True nutzt im Raw-Modus bevorzugt ``raw_intro_reference_text``
    (Fallback: allgemeiner ``raw_reference_text``).
    for_chapter=True formatiert Raw-Text als verbindliche Kapitel-Prosaarchitektur.
    """
    refs = load_style_references(project)
    if is_raw_style_mode(refs):
        if for_intro:
            intro_text = (refs.raw_intro_reference_text or "").strip()
            text = intro_text or (refs.raw_reference_text or "")
            return format_raw_style_reference_for_prompts(
                text,
                label="RAW INTRO STRUCTURAL REFERENCE",
                as_structural_template=True,
            )
        return format_raw_style_reference_for_prompts(
            refs.raw_reference_text,
            for_chapter=for_chapter,
        )

    from otio_app.services.voiceover_generation.style_profile_service import (
        load_style_profile,
    )

    profile = load_style_profile(project)
    if detailed:
        if profile is None:
            return "(kein Style Profile)"
        return profile.model_dump_json(indent=2)
    return format_style_profile_summary_for_prompts(profile)


def compute_style_context_hash(project: Project) -> str:
    """Stabiler Hash über aktiven Style-Modus und relevante Referenzinhalte."""
    refs = load_style_references(project)
    mode = normalize_style_mode(refs.style_mode)
    if mode == STYLE_MODE_RAW_TEXT:
        from otio_app.services.without_voiceover_enhanced.raw_chapter_style_structure import (
            prepare_raw_chapter_reference,
        )

        prepared = prepare_raw_chapter_reference(refs.raw_reference_text or "")
        payload = {
            "mode": mode,
            "raw": prepared.cleaned_text,
            "raw_intro": (refs.raw_intro_reference_text or "").strip(),
        }
    else:
        from otio_app.services.voiceover_generation.style_profile_service import (
            load_style_profile,
        )

        profile = load_style_profile(project)
        payload = {
            "mode": mode,
            "profile": profile.model_dump(mode="json") if profile is not None else None,
        }
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]


def load_style_references(project: Project) -> VoiceoverStyleReferences:
    path = get_voiceover_style_references_path(project.language_work_dir_path)
    if not path.is_file():
        return default_style_references(project)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        refs = VoiceoverStyleReferences.model_validate(payload)
        return refs.model_copy(update={"style_mode": normalize_style_mode(refs.style_mode)})
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError):
        return default_style_references(project)


def is_allowed_upload_filename(filename: str) -> bool:
    """Nur .txt/.md — bewusst keine PDF/DOCX-Verarbeitung in Phase 2."""
    return filename.lower().endswith(ALLOWED_UPLOAD_EXTENSIONS)


def truncate_upload_text(text: str, *, max_chars: int = MAX_UPLOAD_CHARS) -> tuple[str, bool]:
    """Begrenzt sehr große Uploads. Gibt (text, wurde_gekuerzt) zurück."""
    if len(text) <= max_chars:
        return text, False
    return text[:max_chars], True


def _safe_upload_filename(index: int, filename: str) -> str:
    stem = re.sub(r"[^A-Za-z0-9_.-]+", "_", Path(filename).name) or f"upload_{index}"
    return f"{index:02d}_{stem}"


def save_style_references(
    project: Project,
    refs: VoiceoverStyleReferences,
) -> VoiceoverStyleReferences:
    """Speichert die konsolidierte JSON und zusätzlich jeden Upload als reine
    Textdatei unter style_references/uploads/ (Audit-Spur, keine Binärdaten)."""
    previous_hash = ""
    try:
        previous_hash = compute_style_context_hash(project)
    except Exception:  # noqa: BLE001 — vor erstem Save / fehlende Artefakte
        previous_hash = ""

    raw_text = refs.raw_reference_text or ""
    if len(raw_text) > MAX_RAW_REFERENCE_CHARS:
        raw_text = raw_text[:MAX_RAW_REFERENCE_CHARS]
    raw_intro = refs.raw_intro_reference_text or ""
    if len(raw_intro) > MAX_RAW_REFERENCE_CHARS:
        raw_intro = raw_intro[:MAX_RAW_REFERENCE_CHARS]
    normalized = refs.model_copy(
        update={
            "project_id": project.id,
            "generated_at": datetime.now(timezone.utc),
            "style_mode": normalize_style_mode(refs.style_mode),
            "raw_reference_text": raw_text,
            "raw_intro_reference_text": raw_intro,
        }
    )
    path = get_voiceover_style_references_path(project.language_work_dir_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(normalized.model_dump_json(indent=2), encoding="utf-8")

    if normalized.uploaded_file_names:
        uploads_dir = get_style_references_uploads_dir(project.language_work_dir_path)
        uploads_dir.mkdir(parents=True, exist_ok=True)
        for index, (name, text) in enumerate(
            zip(normalized.uploaded_file_names, normalized.uploaded_file_texts), start=1
        ):
            safe_name = _safe_upload_filename(index, name)
            (uploads_dir / safe_name).write_text(text, encoding="utf-8")

    try:
        new_hash = compute_style_context_hash(project)
    except Exception:  # noqa: BLE001
        new_hash = previous_hash
    if previous_hash and new_hash and previous_hash != new_hash:
        # Enhanced Script Lock invalidieren — Draft bleibt zur Ansicht erhalten.
        try:
            from otio_app.services.without_voiceover_enhanced.paths import (
                script_locked_path,
            )

            locked = script_locked_path(project)
            if locked.is_file():
                locked.unlink()
        except Exception:  # noqa: BLE001 — Classic/non-enhanced projects
            pass

    return normalized
