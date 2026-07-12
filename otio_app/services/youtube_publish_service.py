"""YouTube Publish: Kontext aus Merge + Skript, LLM-Generierung, Persistenz."""

from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path

from otio_app.defaults import (
    YOUTUBE_DESCRIPTION_MAX_CHARS,
    YOUTUBE_HASHTAGS_MAX_CHARS,
    YOUTUBE_QUIZ_INTERVAL_SEC,
    YOUTUBE_QUIZ_OPTION_COUNT,
)
from otio_app.models import Project
from otio_app.project_layout import get_youtube_metadata_path
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
from otio_app.services.voiceover_generation.prompts import build_youtube_publish_prompt
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
    "format_youtube_timestamp",
    "generate_youtube_publish_metadata",
    "load_youtube_metadata",
    "quiz_count_for_duration",
    "save_youtube_metadata",
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
    parts: list[str] = []
    for token in (raw or "").replace("\n", ",").split(","):
        tag = token.strip()
        if not tag:
            continue
        if not tag.startswith("#"):
            tag = f"#{tag.lstrip('#')}"
        if tag not in parts:
            parts.append(tag)
    return _clamp_text(", ".join(parts), YOUTUBE_HASHTAGS_MAX_CHARS)


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


def save_youtube_metadata(
    project: Project,
    document: YouTubeMetadataDocument,
) -> YouTubeMetadataDocument:
    path = get_youtube_metadata_path(project.language_work_dir_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    normalized = document.model_copy(update={"project_id": project.id})
    path.write_text(normalized.model_dump_json(indent=2), encoding="utf-8")
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


def _prepare_prompt(project: Project, merged: MergedEditPlanResult) -> _BuildInputs:
    context = build_youtube_publish_context(project, merged)
    prompt = build_youtube_publish_prompt(
        language=context.language,
        title=context.title,
        total_duration_sec=context.total_duration_sec,
        quiz_count=context.quiz_count,
        chapters_block=_chapters_prompt_block(context.chapters),
        intro_text=context.intro_text,
        folder_scripts_block=_folder_scripts_prompt_block(context.folder_scripts),
        description_max_chars=YOUTUBE_DESCRIPTION_MAX_CHARS,
        hashtags_max_chars=YOUTUBE_HASHTAGS_MAX_CHARS,
        option_count=YOUTUBE_QUIZ_OPTION_COUNT,
    )
    return _BuildInputs(context=context, prompt=prompt)


def generate_youtube_publish_metadata(
    project: Project,
    merged: MergedEditPlanResult,
    *,
    provider: str,
    model: str,
) -> YouTubePublishResult:
    prepared = _prepare_prompt(project, merged)
    context = prepared.context
    prompt = prepared.prompt
    run_id, run_dir = create_llm_run_dir(project, STAGE_YOUTUBE_PUBLISH)
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
                stage=STAGE_YOUTUBE_PUBLISH,
                provider=provider,
                model=model,
                prompt_hash=prompt_hash,
                status=STATUS_FAIL,
            ),
        )
        return YouTubePublishResult(
            status=STATUS_FAIL,
            document=None,
            error=str(exc),
            llm_run_id=run_id,
            provider=provider,
            model=model,
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
                stage=STAGE_YOUTUBE_PUBLISH,
                provider=llm_response.provider,
                model=llm_response.model,
                prompt_hash=prompt_hash,
                status=STATUS_FAIL,
            ),
        )
        return YouTubePublishResult(
            status=STATUS_FAIL,
            document=None,
            error=f"JSON-Parse fehlgeschlagen: {exc}",
            llm_run_id=run_id,
            provider=llm_response.provider,
            model=llm_response.model,
        )

    title = str(payload.get("title") or context.title).strip() or context.title
    description_body = _clamp_text(
        str(payload.get("description_body") or ""),
        YOUTUBE_DESCRIPTION_MAX_CHARS,
    )
    description = _append_chapters_to_description(description_body, context.chapters)
    hashtags = _normalize_hashtags(str(payload.get("hashtags") or ""))
    quizzes = _parse_quizzes(
        payload,
        quiz_count=context.quiz_count,
        total_duration_sec=context.total_duration_sec,
    )

    document = YouTubeMetadataDocument(
        project_id=project.id,
        language=context.language,
        title=title,
        description=description,
        description_body=description_body,
        hashtags=hashtags,
        chapters=context.chapters,
        quizzes=quizzes,
        total_duration_sec=context.total_duration_sec,
        quiz_count_target=context.quiz_count,
        folder_names=context.folder_names,
        provider=llm_response.provider,
        model=llm_response.model,
        llm_run_id=run_id,
        status=STATUS_PASS,
    )
    saved = save_youtube_metadata(project, document)
    write_llm_parsed_response(run_dir, payload)
    write_llm_manifest(
        run_dir,
        LlmRunManifest(
            run_id=run_id,
            stage=STAGE_YOUTUBE_PUBLISH,
            provider=llm_response.provider,
            model=llm_response.model,
            prompt_hash=prompt_hash,
            status=STATUS_PASS,
            latency_ms=llm_response.latency_ms,
            token_usage=dict(llm_response.token_usage or {}),
        ),
    )
    return YouTubePublishResult(
        status=STATUS_PASS,
        document=saved,
        error="",
        llm_run_id=run_id,
        provider=llm_response.provider,
        model=llm_response.model,
    )


def youtube_metadata_path(project: Project) -> Path:
    return get_youtube_metadata_path(project.language_work_dir_path)
