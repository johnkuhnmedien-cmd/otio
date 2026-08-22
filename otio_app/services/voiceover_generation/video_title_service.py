"""LLM-Videotitel aus Land/Region + inspirierenden Referenz-Titeln."""

from __future__ import annotations

from dataclasses import dataclass

from otio_app.models import Project
from otio_app.services.gemini_client import _extract_json
from otio_app.services.plan_llm_client import (
    generate_plan_text_with_metadata,
    reraise_if_llm_cancelled,
)
from otio_app.services.voiceover_generation.llm_trace_service import (
    STAGE_PROJECT_BRIEF_TITLE,
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
from otio_app.services.voiceover_generation.project_brief_defaults_service import (
    normalize_title_references,
)
from otio_app.services.voiceover_generation.prompts import build_video_title_prompt

__all__ = ["VideoTitleGenerateResult", "generate_video_title"]


@dataclass(frozen=True)
class VideoTitleGenerateResult:
    status: str
    title: str = ""
    error: str = ""
    llm_run_id: str = ""
    provider: str = ""
    model: str = ""


def _parse_title(raw_text: str) -> str:
    payload = _extract_json(raw_text)
    if isinstance(payload, dict):
        title = str(payload.get("title") or "").strip()
        if title:
            return title
    raise ValueError("LLM-Antwort enthält keinen Titel.")


def generate_video_title(
    project: Project,
    *,
    language: str,
    video_place: str,
    title_references: list[str],
    tone_tags: list[str] | None = None,
    provider: str,
    model: str,
) -> VideoTitleGenerateResult:
    place = (video_place or "").strip()
    refs = normalize_title_references(title_references)
    if not place:
        return VideoTitleGenerateResult(
            status=STATUS_FAIL,
            error="Kein Land/Region am Projekt — zuerst unter Gespeicherte Projekte eintragen.",
            provider=provider,
            model=model,
        )
    if not refs:
        return VideoTitleGenerateResult(
            status=STATUS_FAIL,
            error="Mindestens eine Titel-Referenz eingeben.",
            provider=provider,
            model=model,
        )

    prompt = build_video_title_prompt(
        language=language,
        video_place=place,
        title_references=refs,
        tone_tags=tone_tags,
    )
    run_id, run_dir = create_llm_run_dir(project, STAGE_PROJECT_BRIEF_TITLE)
    prompt_hash = content_hash(prompt)
    write_llm_prompt(run_dir, prompt)
    model_id = resolve_llm_model_id(provider, model)

    try:
        llm_response = generate_plan_text_with_metadata(
            prompt=prompt,
            model=model_id,
            project=project,
            stage=STAGE_PROJECT_BRIEF_TITLE,
        )
    except Exception as exc:  # noqa: BLE001 — UI zeigt FAIL statt Crash
        reraise_if_llm_cancelled(exc)
        write_llm_raw_response(run_dir, raw_text=f"ERROR: {exc}", provider=provider, model=model)
        write_llm_parsed_response(run_dir, {"parse_error": str(exc)})
        write_llm_manifest(
            run_dir,
            LlmRunManifest(
                run_id=run_id,
                stage=STAGE_PROJECT_BRIEF_TITLE,
                provider=provider,
                model=model,
                prompt_hash=prompt_hash,
                status=STATUS_FAIL,
            ),
        )
        return VideoTitleGenerateResult(
            status=STATUS_FAIL,
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
        title = _parse_title(llm_response.raw_text)
    except Exception as exc:  # noqa: BLE001
        write_llm_parsed_response(run_dir, {"parse_error": str(exc)})
        write_llm_manifest(
            run_dir,
            LlmRunManifest(
                run_id=run_id,
                stage=STAGE_PROJECT_BRIEF_TITLE,
                provider=llm_response.provider,
                model=llm_response.model,
                prompt_hash=prompt_hash,
                status=STATUS_FAIL,
            ),
        )
        return VideoTitleGenerateResult(
            status=STATUS_FAIL,
            error=f"JSON-Parse fehlgeschlagen: {exc}",
            llm_run_id=run_id,
            provider=llm_response.provider,
            model=llm_response.model,
        )

    write_llm_parsed_response(run_dir, {"title": title})
    write_llm_manifest(
        run_dir,
        LlmRunManifest(
            run_id=run_id,
            stage=STAGE_PROJECT_BRIEF_TITLE,
            provider=llm_response.provider,
            model=llm_response.model,
            prompt_hash=prompt_hash,
            status=STATUS_PASS,
        ),
    )
    return VideoTitleGenerateResult(
        status=STATUS_PASS,
        title=title,
        llm_run_id=run_id,
        provider=llm_response.provider,
        model=llm_response.model,
    )
