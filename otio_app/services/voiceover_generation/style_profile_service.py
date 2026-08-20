"""Style-Profile-Erzeugung via LLM (Projekt ohne Voice-Over).

Der Ablauf ist bewusst diagnosefreundlich: Rohantwort und geparstes Ergebnis
werden IMMER als eigener LLM-Run gespeichert (siehe llm_trace_service.py). Ein
bestehendes, gespeichertes Style Profile wird NUR bei Erfolg überschrieben —
bei API-Fehlern oder ungültigem JSON bleibt ein vorhandenes Profil unverändert.
"""

from __future__ import annotations

import json
from dataclasses import dataclass

from otio_app.models import Project
from otio_app.project_layout import get_voiceover_style_profile_path
from otio_app.services.gemini_client import _extract_json
from otio_app.services.plan_llm_client import (
    generate_plan_text_with_metadata,
    reraise_if_llm_cancelled,
)
from otio_app.services.voiceover_generation.llm_trace_service import (
    STAGE_STYLE_PROFILE,
    STATUS_FAIL,
    STATUS_PARSE_FAILED,
    STATUS_PASS,
    content_hash,
    content_hash_of_model,
    create_llm_run_dir,
    write_llm_manifest,
    write_llm_parsed_response,
    write_llm_prompt,
    write_llm_raw_response,
)
from otio_app.services.voiceover_generation.model_settings_service import resolve_llm_model_id
from otio_app.services.voiceover_generation.models import (
    LlmRunManifest,
    ProjectBrief,
    VoiceoverStyleProfile,
    VoiceoverStyleReferences,
    as_str_list,
)
from otio_app.services.voiceover_generation.prompts import build_style_profile_prompt

__all__ = [
    "StyleProfileBuildResult",
    "load_style_profile",
    "save_style_profile",
    "parse_style_profile_response",
    "build_style_profile",
]


def load_style_profile(project: Project) -> VoiceoverStyleProfile | None:
    path = get_voiceover_style_profile_path(project.language_work_dir_path)
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return VoiceoverStyleProfile.model_validate(payload)
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError):
        return None


def save_style_profile(project: Project, profile: VoiceoverStyleProfile) -> VoiceoverStyleProfile:
    normalized = profile.model_copy(update={"project_id": project.id})
    path = get_voiceover_style_profile_path(project.language_work_dir_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(normalized.model_dump_json(indent=2), encoding="utf-8")
    return normalized


def parse_style_profile_response(raw_text: str) -> dict:
    """Parst die LLM-Antwort zu einem dict. Wirft ValueError bei ungültigem JSON
    oder falls die Antwort kein JSON-Objekt ist (z. B. eine JSON-Liste)."""
    payload = _extract_json(raw_text)
    if not isinstance(payload, dict):
        raise ValueError("Style-Profile-Antwort ist kein JSON-Objekt.")
    return payload


@dataclass
class StyleProfileBuildResult:
    status: str  # PASS | FAIL | PARSE_FAILED
    profile: VoiceoverStyleProfile | None
    error: str | None
    llm_run_id: str
    provider: str
    model: str


def build_style_profile(
    project: Project,
    *,
    project_brief: ProjectBrief,
    style_references: VoiceoverStyleReferences,
    provider: str,
    model: str,
) -> StyleProfileBuildResult:
    run_id, run_dir = create_llm_run_dir(project, STAGE_STYLE_PROFILE)
    prompt = build_style_profile_prompt(project_brief, style_references)
    prompt_hash = content_hash(prompt)
    write_llm_prompt(run_dir, prompt)

    model_id = resolve_llm_model_id(provider, model)

    try:
        llm_response = generate_plan_text_with_metadata(prompt=prompt, model=model_id)
    except Exception as exc:  # noqa: BLE001 — jeder LLM-/SDK-/Netzwerkfehler soll als
        # kontrollierter FAIL-Status zurückkommen statt die Streamlit-Seite crashen zu
        # lassen (nicht nur der eng gefasste PlanLlmNotConfiguredError-Fall).
        reraise_if_llm_cancelled(exc)
        write_llm_raw_response(run_dir, raw_text=f"ERROR: {exc}", provider=provider, model=model)
        write_llm_parsed_response(run_dir, {"parse_error": str(exc)})
        write_llm_manifest(
            run_dir,
            LlmRunManifest(
                run_id=run_id,
                stage=STAGE_STYLE_PROFILE,
                provider=provider,
                model=model,
                prompt_hash=prompt_hash,
                status=STATUS_FAIL,
            ),
        )
        return StyleProfileBuildResult(
            status=STATUS_FAIL,
            profile=None,
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
        payload = parse_style_profile_response(llm_response.raw_text)
    except (ValueError, TypeError) as exc:
        write_llm_parsed_response(run_dir, {"parse_error": str(exc)})
        write_llm_manifest(
            run_dir,
            LlmRunManifest(
                run_id=run_id,
                stage=STAGE_STYLE_PROFILE,
                provider=llm_response.provider,
                model=llm_response.model,
                prompt_hash=prompt_hash,
                status=STATUS_PARSE_FAILED,
                latency_ms=llm_response.latency_ms,
                token_usage=llm_response.token_usage,
            ),
        )
        return StyleProfileBuildResult(
            status=STATUS_PARSE_FAILED,
            profile=None,
            error=str(exc),
            llm_run_id=run_id,
            provider=llm_response.provider,
            model=llm_response.model,
        )

    profile = VoiceoverStyleProfile(
        project_id=project.id,
        language=str(payload.get("language") or project_brief.language),
        overall_tone=str(payload.get("overall_tone", "")),
        narration_style=str(payload.get("narration_style", "")),
        sentence_length=str(payload.get("sentence_length", "")),
        pacing=str(payload.get("pacing", "")),
        imagery_style=str(payload.get("imagery_style", "")),
        intro_hook_style=str(payload.get("intro_hook_style", "")),
        segment_style=str(payload.get("segment_style", "")),
        do=as_str_list(payload.get("do")),
        dont=as_str_list(payload.get("dont")),
        forbidden_phrases=as_str_list(payload.get("forbidden_phrases")),
        avoid_copying_reference_text=bool(payload.get("avoid_copying_reference_text", True)),
        style_summary_for_prompts=str(payload.get("style_summary_for_prompts", "")),
        source_reference_hash=content_hash_of_model(style_references),
        project_brief_hash=content_hash_of_model(project_brief),
        llm_run_id=run_id,
    )
    saved = save_style_profile(project, profile)
    write_llm_parsed_response(run_dir, saved.model_dump(mode="json"))
    write_llm_manifest(
        run_dir,
        LlmRunManifest(
            run_id=run_id,
            stage=STAGE_STYLE_PROFILE,
            provider=llm_response.provider,
            model=llm_response.model,
            prompt_hash=prompt_hash,
            status=STATUS_PASS,
            latency_ms=llm_response.latency_ms,
            token_usage=llm_response.token_usage,
        ),
    )

    return StyleProfileBuildResult(
        status=STATUS_PASS,
        profile=saved,
        error=None,
        llm_run_id=run_id,
        provider=llm_response.provider,
        model=llm_response.model,
    )
