"""YouTube Publish: Kontext aus Merge + Skript, LLM-Generierung, Persistenz."""

from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path

from otio_app.defaults import (
    YOUTUBE_DESCRIPTION_BODY_MAX_CHARS,
    YOUTUBE_DESCRIPTION_MAX_CHARS,
    YOUTUBE_HASHTAGS_MAX_CHARS,
    YOUTUBE_QUIZ_INTERVAL_SEC,
    YOUTUBE_QUIZ_OPTION_COUNT,
)
from otio_app.models import Project
from otio_app.project_layout import (
    get_project_youtube_metadata_path,
    get_project_youtube_metadata_text_path,
    get_youtube_metadata_path,
)
from otio_app.services.gemini_client import _extract_json
from otio_app.services.otio_exporter import (
    MergedEditPlanResult,
    TimelineSection,
    _compute_timeline_sections,
)
from otio_app.services.otio_media_transform import format_folder_display_name
from otio_app.services.plan_llm_client import generate_plan_text_with_metadata
from otio_app.services.voiceover_generation.final_plan_service import (
    load_confirmed_voiceover_project_plan,
)
from otio_app.services.voiceover_generation.llm_trace_service import (
    STAGE_YOUTUBE_PUBLISH,
    STAGE_YOUTUBE_QUIZ,
    STATUS_FAIL,
    STATUS_PASS,
    content_hash,
    create_llm_run_dir,
    write_llm_manifest,
    write_llm_parsed_response,
    write_llm_prompt,
    write_llm_raw_response,
)
from otio_app.services.voiceover_generation.model_settings_service import resolve_llm_model_id
from otio_app.services.voiceover_generation.models import LlmRunManifest
from otio_app.services.voiceover_generation.prompts import (
    build_youtube_publish_prompt,
    build_youtube_quiz_prompt,
)
from otio_app.services.youtube_publish_models import (
    YouTubeChapter,
    YouTubeMetadataDocument,
    YouTubePublishContext,
    YouTubePublishResult,
    YouTubeQuizItem,
    YouTubeQuizOption,
)

__all__ = [
    "build_youtube_publish_context",
    "build_youtube_publish_context_from_resolved",
    "format_youtube_timestamp",
    "generate_youtube_publish_metadata",
    "generate_youtube_publish_metadata_from_context",
    "generate_youtube_quizzes",
    "generate_youtube_quizzes_from_context",
    "load_youtube_metadata",
    "quiz_count_for_duration",
    "save_youtube_metadata",
    "youtube_metadata_path",
    "youtube_project_metadata_path",
]


def format_youtube_timestamp(seconds: float) -> str:
    total = max(0, int(math.floor(float(seconds) + 0.5)))
    hours, rem = divmod(total, 3600)
    minutes, secs = divmod(rem, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes:02d}:{secs:02d}"


def quiz_count_for_duration(total_duration_sec: float) -> int:
    """Ein Quiz pro angefangene 10 Minuten, mindestens 1."""
    if total_duration_sec <= 0:
        return 1
    return max(1, int(math.ceil(float(total_duration_sec) / YOUTUBE_QUIZ_INTERVAL_SEC)))


def _clamp_text(text: str, max_chars: int) -> str:
    cleaned = (text or "").strip()
    if len(cleaned) <= max_chars:
        return cleaned
    return cleaned[: max(0, max_chars - 1)].rstrip() + "…"


def _chapters_from_sections(sections: list[TimelineSection]) -> list[YouTubeChapter]:
    chapters: list[YouTubeChapter] = []
    for section in sections:
        display = format_folder_display_name(section.folder)
        chapters.append(
            YouTubeChapter(
                folder_name=section.folder,
                display_title=display,
                video_start_sec=float(section.video_start_sec),
                video_duration_sec=float(section.video_duration_sec),
                timestamp=format_youtube_timestamp(section.video_start_sec),
            )
        )
    return chapters


def _scripts_for_chapters(
    project: Project,
    chapters: list[YouTubeChapter],
) -> tuple[str, str, str, list[dict[str, str]]]:
    plan = load_confirmed_voiceover_project_plan(project)
    intro_text = ""
    by_folder: dict[str, str] = {}
    title = project.name
    language = project.language or "DE"
    if plan is not None:
        title = (plan.project_title or "").strip() or title
        language = plan.language or language
        intro_text = (plan.intro.hook_text or "").strip()
        for folder_item in plan.folders:
            by_folder[folder_item.folder_name] = (folder_item.voiceover_text_full or "").strip()

    folder_scripts: list[dict[str, str]] = []
    for chapter in chapters:
        if chapter.folder_name.casefold() == "intro":
            text = intro_text
        else:
            text = by_folder.get(chapter.folder_name, "")
        folder_scripts.append(
            {
                "folder_name": chapter.folder_name,
                "display_title": chapter.display_title,
                "timestamp": chapter.timestamp,
                "voiceover_text": text,
            }
        )
    return title, language, intro_text, folder_scripts


def build_youtube_publish_context(
    project: Project,
    merged: MergedEditPlanResult,
) -> YouTubePublishContext:
    sections = _compute_timeline_sections(
        merged.timeline_items,
        merged.settings,
        merged.voiceovers,
    )
    chapters = _chapters_from_sections(sections)
    total_duration = sum(chapter.video_duration_sec for chapter in chapters)
    title, language, intro_text, folder_scripts = _scripts_for_chapters(project, chapters)
    return YouTubePublishContext(
        title=title,
        language=language,
        total_duration_sec=total_duration,
        quiz_count=quiz_count_for_duration(total_duration),
        chapters=chapters,
        intro_text=intro_text,
        folder_scripts=folder_scripts,
        folder_names=[chapter.folder_name for chapter in chapters],
    )


def _enhanced_title_and_language(project: Project) -> tuple[str, str]:
    from otio_app.services.voiceover_generation.dramaturgy_service import (
        load_confirmed_dramaturgy,
    )
    from otio_app.services.voiceover_generation.project_brief_service import (
        load_project_brief,
    )

    title = (project.name or "").strip() or "Video"
    language = project.language or "DE"
    brief = load_project_brief(project)
    if brief is not None:
        title = (brief.video_title or "").strip() or title
        language = brief.language or language
    plan = load_confirmed_dramaturgy(project)
    if plan is not None and (plan.project_title or "").strip():
        title = plan.project_title.strip()
    return title, language


def _enhanced_folder_scripts(
    project: Project,
    chapters: list[YouTubeChapter],
) -> tuple[str, list[dict[str, str]]]:
    from otio_app.services.without_voiceover_enhanced.intro_script_bridge import (
        confirmed_intro_text,
    )
    from otio_app.services.without_voiceover_enhanced.script_lock_service import (
        load_locked_script,
    )

    intro_text = (confirmed_intro_text(project) or "").strip()
    by_folder: dict[str, list[str]] = {}
    locked = load_locked_script(project)
    if locked is not None:
        for segment in locked.segments:
            folder = (segment.folder_name or "").strip()
            text = (segment.text or "").strip()
            if not text:
                continue
            by_folder.setdefault(folder, []).append(text)
            if folder.casefold() == "intro" and not intro_text:
                intro_text = text

    folder_scripts: list[dict[str, str]] = []
    for chapter in chapters:
        key = chapter.folder_name
        if key.casefold() == "intro":
            text = intro_text or "\n\n".join(by_folder.get(key, []))
        else:
            text = "\n\n".join(by_folder.get(key, []))
            if not text:
                # Fallback: case-insensitive folder match
                for folder_key, parts in by_folder.items():
                    if folder_key.casefold() == key.casefold():
                        text = "\n\n".join(parts)
                        break
        folder_scripts.append(
            {
                "folder_name": chapter.folder_name,
                "display_title": chapter.display_title,
                "timestamp": chapter.timestamp,
                "voiceover_text": text,
            }
        )
    return intro_text, folder_scripts


def build_youtube_publish_context_from_resolved(
    project: Project,
    resolved: object,
) -> YouTubePublishContext:
    """Kontext aus Enhanced Resolved Timeline + Locked Script (kein Edit-Plan-Merge)."""
    chapters: list[YouTubeChapter] = []
    for chapter in list(getattr(resolved, "chapters", None) or []):
        folder = str(getattr(chapter, "folder_name", "") or "").strip()
        if not folder:
            continue
        start = float(getattr(chapter, "chapter_video_start", 0.0) or 0.0)
        end = float(getattr(chapter, "chapter_video_end", start) or start)
        duration = max(0.0, end - start)
        display = format_folder_display_name(folder)
        chapters.append(
            YouTubeChapter(
                folder_name=folder,
                display_title=display,
                video_start_sec=start,
                video_duration_sec=duration,
                timestamp=format_youtube_timestamp(start),
            )
        )
    total_duration = float(getattr(resolved, "total_duration_seconds", 0.0) or 0.0)
    if total_duration <= 0:
        total_duration = sum(chapter.video_duration_sec for chapter in chapters)
    title, language = _enhanced_title_and_language(project)
    intro_text, folder_scripts = _enhanced_folder_scripts(project, chapters)
    return YouTubePublishContext(
        title=title,
        language=language,
        total_duration_sec=total_duration,
        quiz_count=quiz_count_for_duration(total_duration),
        chapters=chapters,
        intro_text=intro_text,
        folder_scripts=folder_scripts,
        folder_names=[chapter.folder_name for chapter in chapters],
    )


def _chapters_prompt_block(chapters: list[YouTubeChapter]) -> str:
    if not chapters:
        return "(no chapters)"
    lines = [
        f"- {chapter.display_title} — {chapter.timestamp} "
        f"({chapter.video_duration_sec:.1f}s, folder=`{chapter.folder_name}`)"
        for chapter in chapters
    ]
    return "\n".join(lines)


def _folder_scripts_prompt_block(folder_scripts: list[dict[str, str]]) -> str:
    if not folder_scripts:
        return "(no folder scripts)"
    blocks: list[str] = []
    for entry in folder_scripts:
        text = entry.get("voiceover_text") or "(no confirmed voice-over text)"
        blocks.append(
            f"[{entry.get('timestamp', '00:00')}] {entry.get('display_title', entry.get('folder_name', ''))}\n{text}"
        )
    return "\n\n".join(blocks)


def _append_chapters_to_description(body: str, chapters: list[YouTubeChapter]) -> str:
    body = (body or "").rstrip()
    chapter_lines = [f"{chapter.display_title} - {chapter.timestamp}" for chapter in chapters]
    if not chapter_lines:
        return body
    block = "\n".join(chapter_lines)
    if body:
        combined = f"{body}\n\n{block}"
    else:
        combined = block
    return _clamp_text(combined, YOUTUBE_DESCRIPTION_MAX_CHARS)


def _normalize_hashtags(raw: str) -> str:
    """Komma-getrennt ohne führendes `#` — z. B. `USA, Reisedokumentation, Natur`."""
    import re

    parts: list[str] = []
    # Kommas und Whitespace als Trenner (LLM liefert oft `#Tag #Tag2`).
    for token in re.split(r"[\s,]+", (raw or "").strip()):
        tag = token.strip().lstrip("#").strip()
        if not tag:
            continue
        if tag not in parts:
            parts.append(tag)
    return _clamp_text(", ".join(parts), YOUTUBE_HASHTAGS_MAX_CHARS)


def _parse_wonders_title(payload: dict) -> tuple[str, str]:
    """Zweizeiliger On-Screen-Titel: Formel + Land/Region."""
    if not isinstance(payload, dict):
        return "", ""
    formula = str(payload.get("wonders_title_formula") or "").strip()
    place = str(payload.get("wonders_title_place") or "").strip()
    if formula and place:
        formula = formula.splitlines()[0].strip()
        place = place.splitlines()[0].strip()
        return formula, place
    combined = str(
        payload.get("on_screen_title") or payload.get("wonders_title") or ""
    ).strip()
    if not combined:
        return formula, place
    lines = [line.strip() for line in combined.splitlines() if line.strip()]
    if len(lines) >= 2:
        return lines[0], lines[1]
    if len(lines) == 1 and not formula:
        return "", lines[0]
    return formula, place or (lines[0] if lines else "")


def _parse_quizzes(
    payload: dict,
    *,
    quiz_count: int,
    total_duration_sec: float,
) -> list[YouTubeQuizItem]:
    raw_quizzes = payload.get("quizzes") if isinstance(payload, dict) else None
    if not isinstance(raw_quizzes, list):
        raw_quizzes = []
    quizzes: list[YouTubeQuizItem] = []
    for index, raw in enumerate(raw_quizzes[:quiz_count], start=1):
        if not isinstance(raw, dict):
            continue
        options_raw = raw.get("options") if isinstance(raw.get("options"), list) else []
        options: list[YouTubeQuizOption] = []
        for opt_index, opt in enumerate(options_raw[:YOUTUBE_QUIZ_OPTION_COUNT]):
            if not isinstance(opt, dict):
                continue
            label = str(opt.get("label") or chr(ord("A") + opt_index)).strip() or chr(ord("A") + opt_index)
            options.append(
                YouTubeQuizOption(
                    label=label,
                    text=str(opt.get("text") or "").strip(),
                    is_correct=bool(opt.get("is_correct")),
                )
            )
        while len(options) < YOUTUBE_QUIZ_OPTION_COUNT:
            label = chr(ord("A") + len(options))
            options.append(YouTubeQuizOption(label=label, text="", is_correct=False))

        correct_label = str(raw.get("correct_option_label") or "").strip()
        if not any(opt.is_correct for opt in options):
            for opt in options:
                if opt.label == correct_label:
                    opt.is_correct = True
                    break
        if not any(opt.is_correct for opt in options) and options:
            options[0].is_correct = True
            correct_label = options[0].label
        else:
            correct_label = next((opt.label for opt in options if opt.is_correct), correct_label)

        try:
            insert_at = float(raw.get("insert_at_sec") or 0.0)
        except (TypeError, ValueError):
            insert_at = 0.0
        insert_at = max(0.0, min(insert_at, max(0.0, total_duration_sec)))

        quizzes.append(
            YouTubeQuizItem(
                order_index=int(raw.get("order_index") or index),
                question=str(raw.get("question") or "").strip(),
                options=options,
                correct_option_label=correct_label,
                insert_at_sec=insert_at,
                insert_timestamp=format_youtube_timestamp(insert_at),
                reason=str(raw.get("reason") or "").strip(),
            )
        )
    return quizzes


def _format_youtube_metadata_text(document: YouTubeMetadataDocument) -> str:
    """Lesbare Kopie für den Sprachordner im Projekt (YouTube Studio)."""
    sections: list[str] = []
    title = (document.title or "").strip()
    if title:
        sections.append(f"Titel\n{title}")
    wonders = document.formatted_wonders_title()
    if wonders:
        sections.append(f"Videotitel\n{wonders}")
    description = (document.description or "").strip()
    if description:
        sections.append(f"Beschreibung\n{description}")
    hashtags = _normalize_hashtags(document.hashtags)
    if hashtags:
        sections.append(f"Hashtags\n{hashtags}")
    if document.chapters:
        chapter_lines = "\n".join(
            f"{chapter.display_title} - {chapter.timestamp}"
            for chapter in document.chapters
        )
        sections.append(f"Kapitel\n{chapter_lines}")
    return "\n\n".join(sections).rstrip() + ("\n" if sections else "")


def _export_youtube_metadata_to_project_folder(
    project: Project,
    document: YouTubeMetadataDocument,
    json_payload: str,
) -> None:
    json_path = get_project_youtube_metadata_path(
        project.project_root_path,
        project.voice_over_subdir,
        project.language,
    )
    text_path = get_project_youtube_metadata_text_path(
        project.project_root_path,
        project.voice_over_subdir,
        project.language,
    )
    json_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(json_payload, encoding="utf-8")
    text_path.write_text(_format_youtube_metadata_text(document), encoding="utf-8")


def save_youtube_metadata(
    project: Project,
    document: YouTubeMetadataDocument,
) -> YouTubeMetadataDocument:
    path = get_youtube_metadata_path(project.language_work_dir_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    normalized = document.model_copy(update={"project_id": project.id})
    payload = normalized.model_dump_json(indent=2)
    path.write_text(payload, encoding="utf-8")
    _export_youtube_metadata_to_project_folder(project, normalized, payload)
    return normalized


def load_youtube_metadata(project: Project) -> YouTubeMetadataDocument | None:
    path = get_youtube_metadata_path(project.language_work_dir_path)
    if not path.is_file():
        return None
    try:
        return YouTubeMetadataDocument.model_validate_json(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError):
        return None


@dataclass(frozen=True)
class _BuildInputs:
    context: YouTubePublishContext
    prompt: str


def _prompt_from_context(context: YouTubePublishContext) -> str:
    """Metadaten-Prompt: nur Kapitelüberschriften, keine Folder-Skripte."""
    return build_youtube_publish_prompt(
        language=context.language,
        title=context.title,
        total_duration_sec=context.total_duration_sec,
        chapters_block=_chapters_prompt_block(context.chapters),
        description_max_chars=YOUTUBE_DESCRIPTION_BODY_MAX_CHARS,
        hashtags_max_chars=YOUTUBE_HASHTAGS_MAX_CHARS,
    )


def _quiz_prompt_from_context(context: YouTubePublishContext) -> str:
    """Quiz-Prompt: nur Kapitelüberschriften, keine Folder-Skripte."""
    return build_youtube_quiz_prompt(
        language=context.language,
        title=context.title,
        total_duration_sec=context.total_duration_sec,
        quiz_count=context.quiz_count,
        chapters_block=_chapters_prompt_block(context.chapters),
        option_count=YOUTUBE_QUIZ_OPTION_COUNT,
    )


def _prepare_prompt(project: Project, merged: MergedEditPlanResult) -> _BuildInputs:
    context = build_youtube_publish_context(project, merged)
    return _BuildInputs(context=context, prompt=_prompt_from_context(context))


def _fail_result(
    *,
    run_id: str,
    provider: str,
    model: str,
    error: str,
) -> YouTubePublishResult:
    return YouTubePublishResult(
        status=STATUS_FAIL,
        document=None,
        error=error,
        llm_run_id=run_id,
        provider=provider,
        model=model,
    )


@dataclass(frozen=True)
class _LlmCallOk:
    run_id: str
    run_dir: Path
    prompt_hash: str
    payload: dict
    provider: str
    model: str
    latency_ms: int
    token_usage: dict


def _call_youtube_llm(
    project: Project,
    *,
    stage: str,
    prompt: str,
    provider: str,
    model: str,
) -> tuple[_LlmCallOk | None, YouTubePublishResult | None]:
    """Gemeinsamer LLM-Call + Trace. Bei Fehler: (None, fail_result)."""
    run_id, run_dir = create_llm_run_dir(project, stage)
    prompt_hash = content_hash(prompt)
    write_llm_prompt(run_dir, prompt)
    model_id = resolve_llm_model_id(provider, model)

    try:
        llm_response = generate_plan_text_with_metadata(prompt=prompt, model=model_id)
    except Exception as exc:  # noqa: BLE001 — UI soll Fehler als FAIL sehen, nicht crashen
        write_llm_raw_response(run_dir, raw_text=f"ERROR: {exc}", provider=provider, model=model)
        write_llm_parsed_response(run_dir, {"parse_error": str(exc)})
        write_llm_manifest(
            run_dir,
            LlmRunManifest(
                run_id=run_id,
                stage=stage,
                provider=provider,
                model=model,
                prompt_hash=prompt_hash,
                status=STATUS_FAIL,
            ),
        )
        return None, _fail_result(
            run_id=run_id, provider=provider, model=model, error=str(exc)
        )

    write_llm_raw_response(
        run_dir,
        raw_text=llm_response.raw_text,
        provider=llm_response.provider,
        model=llm_response.model,
        latency_ms=llm_response.latency_ms,
        token_usage=llm_response.token_usage,
    )

    try:
        payload = _extract_json(llm_response.raw_text)
        if not isinstance(payload, dict):
            raise ValueError("LLM-Antwort ist kein JSON-Objekt.")
    except Exception as exc:  # noqa: BLE001
        write_llm_parsed_response(run_dir, {"parse_error": str(exc)})
        write_llm_manifest(
            run_dir,
            LlmRunManifest(
                run_id=run_id,
                stage=stage,
                provider=llm_response.provider,
                model=llm_response.model,
                prompt_hash=prompt_hash,
                status=STATUS_FAIL,
            ),
        )
        return None, _fail_result(
            run_id=run_id,
            provider=llm_response.provider,
            model=llm_response.model,
            error=f"JSON-Parse fehlgeschlagen: {exc}",
        )

    return (
        _LlmCallOk(
            run_id=run_id,
            run_dir=run_dir,
            prompt_hash=prompt_hash,
            payload=payload,
            provider=llm_response.provider,
            model=llm_response.model,
            latency_ms=int(llm_response.latency_ms or 0),
            token_usage=dict(llm_response.token_usage or {}),
        ),
        None,
    )


def generate_youtube_publish_metadata_from_context(
    project: Project,
    context: YouTubePublishContext,
    *,
    provider: str,
    model: str,
) -> YouTubePublishResult:
    """Generiert nur Titel/Beschreibung/Hashtags. Bestehende Quizzes bleiben erhalten."""
    if not context.chapters:
        return YouTubePublishResult(
            status=STATUS_FAIL,
            document=None,
            error="Keine Kapitel im Kontext – zuerst Timeline/Final Cut fertigstellen.",
            provider=provider,
            model=model,
        )

    prompt = _prompt_from_context(context)
    ok, fail = _call_youtube_llm(
        project,
        stage=STAGE_YOUTUBE_PUBLISH,
        prompt=prompt,
        provider=provider,
        model=model,
    )
    if fail is not None or ok is None:
        return fail or YouTubePublishResult(
            status=STATUS_FAIL,
            document=None,
            error="LLM-Call fehlgeschlagen.",
            provider=provider,
            model=model,
        )

    payload = ok.payload
    title = str(payload.get("title") or context.title).strip() or context.title
    wonders_formula, wonders_place = _parse_wonders_title(payload)
    description_body = _clamp_text(
        str(payload.get("description_body") or ""),
        YOUTUBE_DESCRIPTION_BODY_MAX_CHARS,
    )
    description = _append_chapters_to_description(description_body, context.chapters)
    hashtags = _normalize_hashtags(str(payload.get("hashtags") or ""))

    # Metadata regenerate must not wipe separately generated quizzes.
    existing = load_youtube_metadata(project)
    quizzes = list(existing.quizzes) if existing is not None else []

    document = YouTubeMetadataDocument(
        project_id=project.id,
        language=context.language,
        title=title,
        wonders_title_formula=wonders_formula,
        wonders_title_place=wonders_place,
        description=description,
        description_body=description_body,
        hashtags=hashtags,
        chapters=context.chapters,
        quizzes=quizzes,
        total_duration_sec=context.total_duration_sec,
        quiz_count_target=context.quiz_count,
        folder_names=context.folder_names,
        provider=ok.provider,
        model=ok.model,
        llm_run_id=ok.run_id,
        status=STATUS_PASS,
    )
    saved = save_youtube_metadata(project, document)
    write_llm_parsed_response(ok.run_dir, payload)
    write_llm_manifest(
        ok.run_dir,
        LlmRunManifest(
            run_id=ok.run_id,
            stage=STAGE_YOUTUBE_PUBLISH,
            provider=ok.provider,
            model=ok.model,
            prompt_hash=ok.prompt_hash,
            status=STATUS_PASS,
            latency_ms=ok.latency_ms,
            token_usage=ok.token_usage,
        ),
    )
    return YouTubePublishResult(
        status=STATUS_PASS,
        document=saved,
        error="",
        llm_run_id=ok.run_id,
        provider=ok.provider,
        model=ok.model,
    )


def generate_youtube_quizzes_from_context(
    project: Project,
    context: YouTubePublishContext,
    *,
    provider: str,
    model: str,
) -> YouTubePublishResult:
    """Generiert nur Quizzes. Bestehende YouTube-Metadaten bleiben erhalten."""
    if not context.chapters:
        return YouTubePublishResult(
            status=STATUS_FAIL,
            document=None,
            error="Keine Kapitel im Kontext – zuerst Timeline/Final Cut fertigstellen.",
            provider=provider,
            model=model,
        )

    prompt = _quiz_prompt_from_context(context)
    ok, fail = _call_youtube_llm(
        project,
        stage=STAGE_YOUTUBE_QUIZ,
        prompt=prompt,
        provider=provider,
        model=model,
    )
    if fail is not None or ok is None:
        return fail or YouTubePublishResult(
            status=STATUS_FAIL,
            document=None,
            error="LLM-Call fehlgeschlagen.",
            provider=provider,
            model=model,
        )

    payload = ok.payload
    if not isinstance(payload.get("quizzes"), list):
        write_llm_parsed_response(ok.run_dir, {"parse_error": "missing quizzes array"})
        write_llm_manifest(
            ok.run_dir,
            LlmRunManifest(
                run_id=ok.run_id,
                stage=STAGE_YOUTUBE_QUIZ,
                provider=ok.provider,
                model=ok.model,
                prompt_hash=ok.prompt_hash,
                status=STATUS_FAIL,
            ),
        )
        return _fail_result(
            run_id=ok.run_id,
            provider=ok.provider,
            model=ok.model,
            error="LLM-Antwort enthält kein quizzes-Array.",
        )

    quizzes = _parse_quizzes(
        payload,
        quiz_count=context.quiz_count,
        total_duration_sec=context.total_duration_sec,
    )

    existing = load_youtube_metadata(project)
    if existing is not None:
        document = existing.model_copy(
            update={
                "quizzes": quizzes,
                "chapters": context.chapters,
                "total_duration_sec": context.total_duration_sec,
                "quiz_count_target": context.quiz_count,
                "folder_names": context.folder_names,
                "language": context.language,
                "provider": ok.provider,
                "model": ok.model,
                "llm_run_id": ok.run_id,
                "status": STATUS_PASS,
                "error": "",
            }
        )
        if not (document.title or "").strip():
            document = document.model_copy(update={"title": context.title})
    else:
        document = YouTubeMetadataDocument(
            project_id=project.id,
            language=context.language,
            title=context.title,
            description=_append_chapters_to_description("", context.chapters),
            description_body="",
            hashtags="",
            chapters=context.chapters,
            quizzes=quizzes,
            total_duration_sec=context.total_duration_sec,
            quiz_count_target=context.quiz_count,
            folder_names=context.folder_names,
            provider=ok.provider,
            model=ok.model,
            llm_run_id=ok.run_id,
            status=STATUS_PASS,
        )

    saved = save_youtube_metadata(project, document)
    write_llm_parsed_response(ok.run_dir, payload)
    write_llm_manifest(
        ok.run_dir,
        LlmRunManifest(
            run_id=ok.run_id,
            stage=STAGE_YOUTUBE_QUIZ,
            provider=ok.provider,
            model=ok.model,
            prompt_hash=ok.prompt_hash,
            status=STATUS_PASS,
            latency_ms=ok.latency_ms,
            token_usage=ok.token_usage,
        ),
    )
    return YouTubePublishResult(
        status=STATUS_PASS,
        document=saved,
        error="",
        llm_run_id=ok.run_id,
        provider=ok.provider,
        model=ok.model,
    )


def generate_youtube_publish_metadata(
    project: Project,
    merged: MergedEditPlanResult,
    *,
    provider: str,
    model: str,
) -> YouTubePublishResult:
    context = build_youtube_publish_context(project, merged)
    return generate_youtube_publish_metadata_from_context(
        project,
        context,
        provider=provider,
        model=model,
    )


def generate_youtube_quizzes(
    project: Project,
    merged: MergedEditPlanResult,
    *,
    provider: str,
    model: str,
) -> YouTubePublishResult:
    context = build_youtube_publish_context(project, merged)
    return generate_youtube_quizzes_from_context(
        project,
        context,
        provider=provider,
        model=model,
    )


def youtube_metadata_path(project: Project) -> Path:
    return get_youtube_metadata_path(project.language_work_dir_path)


def youtube_project_metadata_path(project: Project) -> Path:
    """Sprachordner im Projektroot, z. B. ``Voice over/PT/youtube_metadata.json``."""
    return get_project_youtube_metadata_path(
        project.project_root_path,
        project.voice_over_subdir,
        project.language,
    )
