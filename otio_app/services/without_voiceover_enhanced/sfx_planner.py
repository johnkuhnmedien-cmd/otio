"""SFX Planner: build LLM input, call plan LLM, validate structured plan."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

from otio_app.models import Project
from otio_app.services.gemini_client import _extract_json
from otio_app.services.plan_llm_client import (
    PlanLlmNotConfiguredError,
    generate_plan_text_with_metadata,
)
from otio_app.services.voiceover_generation.model_settings_service import (
    load_model_settings,
    resolve_llm_model_id,
)
from otio_app.services.without_voiceover_enhanced.cut_plan_options import (
    DEFAULT_MAX_SFX_PER_CHAPTER,
    load_cut_plan_options,
)
from otio_app.services.without_voiceover_enhanced.cut_plan_service import (
    _local_assets_payload,
)
from otio_app.services.without_voiceover_enhanced.elevenlabs_sfx_client import (
    SFX_PROMPT_MAX_CHARS,
)
from otio_app.services.without_voiceover_enhanced.keyword_flow_free_input import (
    load_cleaned_sentence_rows_for_segments,
)
from otio_app.services.without_voiceover_enhanced.models import (
    ResolvedShot,
    ResolvedTimelineDocument,
    UnifiedCutPlanDocument,
)
from otio_app.services.without_voiceover_enhanced.sfx_prompt import (
    SFX_PLAN_SCHEMA_VERSION,
    build_sfx_planner_prompt,
)
from otio_app.services.without_voiceover_enhanced.sentence_timing_prompt import (
    clean_words_for_keyword_flow_prompt,
)

__all__ = [
    "SfxPlannerError",
    "SfxPlanValidationError",
    "ALLOWED_SFX_TYPES",
    "ALLOWED_EVIDENCE",
    "ALLOWED_ANCHORS",
    "ALLOWED_DURATION_CLASSES",
    "DURATION_CLASS_SECONDS",
    "build_used_shots_for_planner",
    "build_word_flow_for_planner",
    "build_planner_input_bundle",
    "parse_and_validate_sfx_plan",
    "resolve_sfx_planner_model_id",
    "run_sfx_planner",
]

ALLOWED_SFX_TYPES = {
    "natural_ambience",
    "location_ambience",
    "diegetic_foley",
    "editorial_transition",
}
ALLOWED_EVIDENCE = {
    "visible",
    "environmental_plausible",
    "editorial_non_diegetic",
}
ALLOWED_ANCHORS = {
    "shot_start",
    "shot_center",
    "shot_end",
    "span_shot",
    "narration_word",
}
ALLOWED_DURATION_CLASSES = {"short", "medium", "long"}
DURATION_CLASS_SECONDS = {
    "short": 2.0,
    "medium": 5.0,
    "long": 8.0,
}


class SfxPlannerError(RuntimeError):
    """Planner failed before/during LLM call."""


class SfxPlanValidationError(RuntimeError):
    """Structured plan invalid — no ElevenLabs generation for this plan."""


@dataclass
class SfxPlannerResult:
    plan: dict[str, Any]
    raw_text: str = ""
    planner_model: str = ""
    used_shots: list[dict[str, Any]] = field(default_factory=list)
    word_flow: list[dict[str, Any]] = field(default_factory=list)
    unused_assets_excluded: bool = True


def resolve_sfx_planner_model_id(project: Project) -> str:
    options = load_cut_plan_options(project)
    configured = str(options.sfx_planner_model or "").strip()
    if configured:
        # CutPlanOptions may store combined id ("openai:gpt-5.6-sol") or bare.
        if ":" in configured or configured.startswith("gemini"):
            return configured
    settings = load_model_settings(project)
    role = settings.enhanced_sfx_planner
    return resolve_llm_model_id(role.provider, role.model)


def _slot_meta_by_id(plan: UnifiedCutPlanDocument | None) -> dict[str, Any]:
    out: dict[str, Any] = {}
    if plan is None:
        return out
    for slot in list(plan.slots or []):
        out[str(slot.slot_id)] = {
            "visual_intent": str(slot.visual_intent or ""),
            "needed_visual": str(getattr(slot, "needed_visual", "") or ""),
            "asset_fit": str(slot.asset_fit or ""),
            "asset_fit_reason": str(slot.asset_fit_reason or ""),
            "local_asset_id": str(slot.local_asset_id or "") or None,
        }
    return out


def _asset_description_index(
    project: Project, *, folder_name: str, used_asset_ids: set[str]
) -> dict[str, dict[str, Any]]:
    """Join descriptions only for assets actually used on the timeline."""
    if not used_asset_ids:
        return {}
    assets = _local_assets_payload(project, folder_name=folder_name or None)
    index: dict[str, dict[str, Any]] = {}
    for row in assets:
        asset_id = str(row.get("local_asset_id") or row.get("asset_id") or "").strip()
        if not asset_id or asset_id not in used_asset_ids:
            continue
        index[asset_id] = {
            "description": str(row.get("description") or ""),
            "shot_or_camera": str(
                row.get("shot_type")
                or row.get("camera")
                or row.get("camera_movement")
                or ""
            ),
            "kind": str(row.get("kind") or row.get("media_type") or ""),
        }
    return index


def build_used_shots_for_planner(
    project: Project,
    *,
    resolved: ResolvedTimelineDocument,
    plan: UnifiedCutPlanDocument | None,
    folder_name: str,
) -> list[dict[str, Any]]:
    """Only shots present on the resolved timeline (not full asset inventory)."""
    slot_meta = _slot_meta_by_id(plan)
    used_asset_ids = {
        str(s.asset_id).strip()
        for s in list(resolved.shots or [])
        if str(s.asset_id or "").strip()
    }
    asset_index = _asset_description_index(
        project, folder_name=folder_name, used_asset_ids=used_asset_ids
    )
    shots: list[dict[str, Any]] = []
    for shot in list(resolved.shots or []):
        assert isinstance(shot, ResolvedShot)
        meta = slot_meta.get(str(shot.shot_id), {})
        asset_id = str(shot.asset_id or "").strip()
        asset_info = asset_index.get(asset_id, {})
        start = float(shot.timeline_start_seconds)
        end = float(shot.timeline_end_seconds)
        visual_desc = (
            str(asset_info.get("description") or "").strip()
            or str(meta.get("needed_visual") or "").strip()
            or str(meta.get("visual_intent") or "").strip()
            or str(shot.editorial_function or "").strip()
        )
        shots.append(
            {
                "shot_id": str(shot.shot_id),
                "timeline_start": round(start, 3),
                "timeline_end": round(end, 3),
                "duration": round(max(0.0, end - start), 3),
                "asset_id": asset_id,
                "source_start": round(float(shot.source_start_seconds), 3),
                "source_end": round(float(shot.source_end_seconds), 3),
                "visual_description": visual_desc,
                "shot_or_camera_description": str(
                    asset_info.get("shot_or_camera") or ""
                ),
                "visual_intent": str(
                    meta.get("visual_intent") or shot.editorial_function or ""
                ),
                "asset_fit": str(meta.get("asset_fit") or shot.asset_fit or ""),
                "asset_fit_reason": str(
                    meta.get("asset_fit_reason") or shot.asset_fit_reason or ""
                ),
            }
        )
    return shots


def _resolve_audio_segment_for_sentence(
    *,
    sentence_id: str,
    audio_by_seg: dict[str, Any],
) -> Any | None:
    """Return the uniquely mapped resolved audio segment, or None if ambiguous."""
    if not audio_by_seg:
        return None
    if len(audio_by_seg) == 1:
        return next(iter(audio_by_seg.values()))
    matches = []
    for seg_id, audio in audio_by_seg.items():
        if sentence_id.startswith(seg_id) or seg_id in sentence_id:
            matches.append(audio)
    if len(matches) == 1:
        return matches[0]
    return None


def build_word_flow_for_planner(
    project: Project,
    *,
    resolved: ResolvedTimelineDocument,
) -> list[dict[str, Any]]:
    """Real ElevenLabs word timestamps with absolute timeline onsets + word_ref.

    Only words that map unambiguously onto a resolved audio segment (and thus
    the scope timeline) are included. Relative/offset-only fallbacks are never
    emitted as absolute ``onset`` — those words are omitted so
    ``narration_word`` cannot use them.
    """
    segment_ids = [a.segment_id for a in list(resolved.audio_segments or []) if a.segment_id]
    if not segment_ids and resolved.chapters:
        for env in resolved.chapters:
            segment_ids.extend(list(env.segment_ids or []))
    segment_ids = [s for s in dict.fromkeys(segment_ids) if s]
    rows = load_cleaned_sentence_rows_for_segments(project, segment_ids=segment_ids)

    audio_by_seg = {
        str(a.segment_id): a for a in list(resolved.audio_segments or []) if a.segment_id
    }
    flow: list[dict[str, Any]] = []
    for row in rows:
        sentence_id = str(row.get("sentence_id") or "").strip()
        segment = _resolve_audio_segment_for_sentence(
            sentence_id=sentence_id, audio_by_seg=audio_by_seg
        )
        if segment is None:
            # Not absolutely mappable → omit (narration_word unavailable for these).
            continue
        cleaned = clean_words_for_keyword_flow_prompt(
            list(row.get("words") or []), sentence_id=sentence_id
        )
        for word in cleaned:
            if word.get("start_seconds") is None:
                continue
            word_ref = str(word.get("word_ref") or "").strip()
            if not word_ref and sentence_id:
                word_ref = f"{sentence_id}#{int(word.get('original_word_index', 0))}"
            if not word_ref:
                continue
            word_start = float(word.get("start_seconds") or 0.0)
            onset = float(segment.timeline_start_seconds) + (
                word_start - float(segment.source_start_seconds or 0.0)
            )
            flow.append(
                {
                    "word_ref": word_ref,
                    "text": str(word.get("text") or ""),
                    "sentence_id": sentence_id,
                    "segment_id": str(segment.segment_id or ""),
                    "onset": round(max(0.0, onset), 3),
                    "absolute_mapped": True,
                }
            )
    return flow


def build_planner_input_bundle(
    project: Project,
    *,
    resolved: ResolvedTimelineDocument,
    plan: UnifiedCutPlanDocument | None,
    folder_name: str,
    locked_script_text: str,
    scope: str,
    narration_start: float,
    narration_end: float,
    scope_total_duration: float,
    max_sfx: int,
) -> dict[str, Any]:
    used_shots = build_used_shots_for_planner(
        project, resolved=resolved, plan=plan, folder_name=folder_name
    )
    word_flow = build_word_flow_for_planner(project, resolved=resolved)
    prompt = build_sfx_planner_prompt(
        max_sfx_per_chapter=max_sfx,
        scope=scope,
        chapter_id=folder_name,
        locked_script_text=locked_script_text,
        narration_start=narration_start,
        narration_end=narration_end,
        scope_total_duration=scope_total_duration,
        resolved_shots=used_shots,
        word_flow=word_flow,
    )
    return {
        "prompt": prompt,
        "used_shots": used_shots,
        "word_flow": word_flow,
        "used_asset_ids": sorted(
            {str(s.get("asset_id") or "") for s in used_shots if s.get("asset_id")}
        ),
    }


def parse_and_validate_sfx_plan(
    raw: Any,
    *,
    max_sfx: int,
    known_shot_ids: set[str],
    known_word_refs: set[str],
    scope: str,
) -> dict[str, Any]:
    """Validate planner JSON. Over-max plans are rejected (no silent truncation)."""
    payload = _extract_json(raw) if isinstance(raw, str) else raw
    if not isinstance(payload, dict):
        raise SfxPlanValidationError("SFX-Plan ist kein JSON-Objekt.")
    schema = str(payload.get("schema_version") or "").strip()
    if schema and schema != SFX_PLAN_SCHEMA_VERSION:
        raise SfxPlanValidationError(
            f"Unbekannte schema_version {schema!r} (erwartet {SFX_PLAN_SCHEMA_VERSION})."
        )
    items = payload.get("sfx")
    if items is None:
        items = []
    if not isinstance(items, list):
        raise SfxPlanValidationError("Feld 'sfx' muss eine Liste sein.")
    if len(items) > int(max_sfx):
        # No established safe repair for over-max item selection — reject.
        raise SfxPlanValidationError(
            f"SFX-Plan enthält {len(items)} Effekte, Maximum ist {int(max_sfx)}. "
            "Plan abgelehnt (keine willkürliche Kürzung, keine ElevenLabs-Calls)."
        )

    validated: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for index, item in enumerate(items):
        if not isinstance(item, dict):
            raise SfxPlanValidationError(f"sfx[{index}] ist kein Objekt.")
        sfx_id = str(item.get("sfx_id") or f"sfx_{index+1:03d}").strip()
        if sfx_id in seen_ids:
            raise SfxPlanValidationError(f"Doppelte sfx_id: {sfx_id}")
        seen_ids.add(sfx_id)
        sfx_type = str(item.get("sfx_type") or "").strip()
        if sfx_type not in ALLOWED_SFX_TYPES:
            raise SfxPlanValidationError(f"{sfx_id}: ungültiger sfx_type {sfx_type!r}.")
        evidence = str(item.get("evidence_basis") or "").strip()
        if evidence not in ALLOWED_EVIDENCE:
            raise SfxPlanValidationError(
                f"{sfx_id}: ungültige evidence_basis {evidence!r}."
            )
        editorial = str(item.get("editorial_value") or "").strip().lower()
        if editorial != "high":
            # Explicitly not generated later; keep out of validated generatable set.
            continue
        prompt = str(item.get("prompt") or "").strip()
        if not prompt:
            raise SfxPlanValidationError(f"{sfx_id}: leerer prompt.")
        if len(prompt) > SFX_PROMPT_MAX_CHARS:
            raise SfxPlanValidationError(
                f"{sfx_id}: prompt länger als {SFX_PROMPT_MAX_CHARS} Zeichen."
            )
        shot_id = str(item.get("shot_id") or "").strip()
        if not shot_id or shot_id not in known_shot_ids:
            raise SfxPlanValidationError(f"{sfx_id}: ungültige shot_id {shot_id!r}.")
        anchor = str(item.get("anchor_type") or "").strip()
        if anchor not in ALLOWED_ANCHORS:
            raise SfxPlanValidationError(f"{sfx_id}: ungültiger anchor_type {anchor!r}.")
        word_ref_raw = item.get("word_ref")
        word_ref = None if word_ref_raw in (None, "", "null") else str(word_ref_raw).strip()
        if anchor == "narration_word":
            if not word_ref or word_ref not in known_word_refs:
                raise SfxPlanValidationError(
                    f"{sfx_id}: ungültige word_ref {word_ref!r}."
                )
        else:
            word_ref = None
        duration_class = str(item.get("duration_class") or "medium").strip().lower()
        if duration_class not in ALLOWED_DURATION_CLASSES:
            raise SfxPlanValidationError(
                f"{sfx_id}: ungültige duration_class {duration_class!r}."
            )
        validated.append(
            {
                "sfx_id": sfx_id,
                "sfx_type": sfx_type,
                "prompt": prompt,
                "evidence_basis": evidence,
                "editorial_value": "high",
                "shot_id": shot_id,
                "anchor_type": anchor,
                "word_ref": word_ref,
                "duration_class": duration_class,
                "reason": str(item.get("reason") or "").strip(),
            }
        )

    return {
        "schema_version": SFX_PLAN_SCHEMA_VERSION,
        "scope": "intro" if str(scope).lower() == "intro" else "chapter",
        "sfx": validated,
    }


def run_sfx_planner(
    project: Project,
    *,
    resolved: ResolvedTimelineDocument,
    plan: UnifiedCutPlanDocument | None,
    folder_name: str,
    locked_script_text: str,
    scope: str,
    narration_start: float,
    narration_end: float,
    scope_total_duration: float,
    max_sfx: int | None = None,
    model_id: str | None = None,
    llm_callable: Callable[..., Any] | None = None,
) -> SfxPlannerResult:
    options = load_cut_plan_options(project)
    max_n = (
        int(max_sfx)
        if max_sfx is not None
        else int(options.max_sfx_per_chapter or DEFAULT_MAX_SFX_PER_CHAPTER)
    )
    max_n = max(0, min(5, max_n))
    planner_model = model_id or resolve_sfx_planner_model_id(project)
    bundle = build_planner_input_bundle(
        project,
        resolved=resolved,
        plan=plan,
        folder_name=folder_name,
        locked_script_text=locked_script_text,
        scope=scope,
        narration_start=narration_start,
        narration_end=narration_end,
        scope_total_duration=scope_total_duration,
        max_sfx=max_n,
    )
    call = llm_callable or generate_plan_text_with_metadata
    try:
        response = call(prompt=bundle["prompt"], model=planner_model)
    except PlanLlmNotConfiguredError as exc:
        raise SfxPlannerError(f"SFX Planner Modell/API fehlt: {exc}") from exc
    except Exception as exc:  # noqa: BLE001
        raise SfxPlannerError(f"SFX Planner LLM-Fehler: {exc}") from exc

    raw_text = getattr(response, "text", None) or getattr(response, "raw_text", None)
    if raw_text is None and isinstance(response, str):
        raw_text = response
    if raw_text is None and isinstance(response, dict):
        raw_text = response.get("text") or json_dumps_safe(response)
    raw_text = str(raw_text or "")

    known_shots = {str(s.get("shot_id") or "") for s in bundle["used_shots"]}
    known_words = {str(w.get("word_ref") or "") for w in bundle["word_flow"] if w.get("word_ref")}
    try:
        plan_payload = parse_and_validate_sfx_plan(
            raw_text,
            max_sfx=max_n,
            known_shot_ids=known_shots,
            known_word_refs=known_words,
            scope=scope,
        )
    except SfxPlanValidationError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise SfxPlanValidationError(f"SFX-Plan Parsing fehlgeschlagen: {exc}") from exc

    return SfxPlannerResult(
        plan=plan_payload,
        raw_text=raw_text,
        planner_model=planner_model,
        used_shots=bundle["used_shots"],
        word_flow=bundle["word_flow"],
        unused_assets_excluded=True,
    )


def json_dumps_safe(value: Any) -> str:
    import json

    try:
        return json.dumps(value, ensure_ascii=False)
    except Exception:  # noqa: BLE001
        return str(value)
