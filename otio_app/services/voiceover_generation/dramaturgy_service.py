"""Dramaturgieplanung über alle Ordner (Projekt ohne Voice-Over, Phase 3).

Erst nach bestätigter Dramaturgie darf in Phase 4 Voice-over pro Ordner
geschrieben werden. Dieses Modul schreibt niemals EditPlanDocuments.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone

from otio_app.defaults import (
    DRAMATURGY_PLANNING_MODE_VARIETY,
    DRAMATURGY_ROLES,
    DRAMATURGY_STATUS_CONFIRMED,
    DRAMATURGY_STATUS_DRAFT,
    VOICEOVER_GEN_DEFAULT_FOLDER_MAX_WORDS,
    VOICEOVER_GEN_DEFAULT_FOLDER_MIN_WORDS,
    VOICEOVER_GEN_DEFAULT_FOLDER_TARGET_WORDS,
)
from otio_app.models import Project
from otio_app.project_layout import (
    get_dramaturgy_plan_confirmed_path,
    get_dramaturgy_plan_draft_path,
)
from otio_app.services.gemini_client import _extract_json
from otio_app.services.plan_llm_client import generate_plan_text_with_metadata
from otio_app.services.voiceover_generation.folder_inventory_summary import (
    build_and_save_folder_inventory_summaries,
)
from otio_app.services.voiceover_generation.llm_trace_service import (
    STAGE_DRAMATURGY,
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
    DramaturgyFolderEntry,
    DramaturgyPlan,
    LlmRunManifest,
    as_str_list,
)
from otio_app.services.voiceover_generation.project_brief_service import load_project_brief
from otio_app.services.voiceover_generation.prompts import build_dramaturgy_prompt
from otio_app.services.voiceover_generation.style_profile_service import load_style_profile

__all__ = [
    "DramaturgyBuildResult",
    "load_dramaturgy_draft",
    "save_dramaturgy_draft",
    "load_confirmed_dramaturgy",
    "save_confirmed_dramaturgy",
    "confirm_dramaturgy_plan",
    "update_dramaturgy_order",
    "disable_dramaturgy_craft_flags",
    "build_dramaturgy_plan",
]


_CRAFT_FLAG_CLEAR_UPDATES = {
    "use_transition_from_previous": False,
    "use_transition_to_next": False,
    "use_callback_to_previous": False,
    "use_contrast_with_previous": False,
    "use_commonality_with_previous": False,
    "transition_goal_to_next": "",
    "transition_from_previous_hint": "",
    "contrast_or_commonality_hint": "",
}


def _plan_with_craft_flags_disabled(plan: DramaturgyPlan) -> DramaturgyPlan:
    cleared_entries = [
        entry.model_copy(update=dict(_CRAFT_FLAG_CLEAR_UPDATES))
        for entry in plan.recommended_folder_order
    ]
    return plan.model_copy(
        update={
            "recommended_folder_order": cleared_entries,
            "craft_flags_disabled": True,
            "global_transition_strategy": "",
        }
    )


def disable_dramaturgy_craft_flags(project: Project) -> DramaturgyPlan | None:
    """Deaktiviert alle Übergang-/Craft-Kästchen im Draft (und Confirm/Settings).

    Folder-Voice-over-Prompts bekommen diese Parameter danach nicht mehr
    mitgeliefert (Flags bleiben false, Hints leer).
    """
    draft = load_dramaturgy_draft(project)
    if draft is None:
        return None

    cleared_draft = save_dramaturgy_draft(project, _plan_with_craft_flags_disabled(draft))

    confirmed = load_confirmed_dramaturgy(project)
    if confirmed is not None:
        save_confirmed_dramaturgy(project, _plan_with_craft_flags_disabled(confirmed))

    from otio_app.services.voiceover_generation.folder_voiceover_settings_service import (
        load_folder_voiceover_settings,
        save_folder_voiceover_settings,
    )

    settings_doc = load_folder_voiceover_settings(project)
    if settings_doc is not None:
        cleared_settings = [
            setting.model_copy(
                update={
                    "transition_from_previous": False,
                    "transition_to_next": False,
                    "callback_to_previous": False,
                    "use_contrast_with_previous": False,
                    "use_commonality_with_previous": False,
                }
            )
            for setting in settings_doc.settings
        ]
        save_folder_voiceover_settings(
            project,
            settings_doc.model_copy(update={"settings": cleared_settings}),
        )

    return cleared_draft


def load_dramaturgy_draft(project: Project) -> DramaturgyPlan | None:
    path = get_dramaturgy_plan_draft_path(project.language_work_dir_path)
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return DramaturgyPlan.model_validate(payload)
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError):
        return None


def save_dramaturgy_draft(project: Project, plan: DramaturgyPlan) -> DramaturgyPlan:
    normalized = plan.model_copy(
        update={"project_id": project.id, "status": DRAMATURGY_STATUS_DRAFT}
    )
    path = get_dramaturgy_plan_draft_path(project.language_work_dir_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(normalized.model_dump_json(indent=2), encoding="utf-8")
    return normalized


def load_confirmed_dramaturgy(project: Project) -> DramaturgyPlan | None:
    path = get_dramaturgy_plan_confirmed_path(project.language_work_dir_path)
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return DramaturgyPlan.model_validate(payload)
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError):
        return None


def save_confirmed_dramaturgy(project: Project, plan: DramaturgyPlan) -> DramaturgyPlan:
    normalized = plan.model_copy(
        update={
            "project_id": project.id,
            "status": DRAMATURGY_STATUS_CONFIRMED,
            "confirmed_at": plan.confirmed_at or datetime.now(timezone.utc),
        }
    )
    path = get_dramaturgy_plan_confirmed_path(project.language_work_dir_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(normalized.model_dump_json(indent=2), encoding="utf-8")
    return normalized


def _normalize_role(value: str) -> str:
    normalized = (value or "").strip().lower()
    return normalized if normalized in DRAMATURGY_ROLES else "setup"


def confirm_dramaturgy_plan(project: Project, edited_plan: DramaturgyPlan) -> DramaturgyPlan:
    """Bestätigt einen (ggf. manuell bearbeiteten) Dramaturgie-Plan.

    order_index wird für ALLE Einträge auf 1..N normalisiert — aktivierte
    Ordner zuerst (in ihrer relativen Reihenfolge), danach deaktivierte
    Ordner. Nur `enabled` entscheidet, ob ein Ordner in Phase 4 aktiv für die
    Voice-over-Erzeugung berücksichtigt wird — deaktivierte Ordner bleiben im
    Plan sichtbar (Audit), zählen aber nicht als aktiv."""
    enabled_entries = sorted(
        (entry for entry in edited_plan.recommended_folder_order if entry.enabled),
        key=lambda entry: entry.order_index,
    )
    disabled_entries = sorted(
        (entry for entry in edited_plan.recommended_folder_order if not entry.enabled),
        key=lambda entry: entry.order_index,
    )
    normalized_entries = [
        entry.model_copy(update={"order_index": index})
        for index, entry in enumerate(enabled_entries + disabled_entries, start=1)
    ]
    confirmed = edited_plan.model_copy(
        update={
            "recommended_folder_order": normalized_entries,
            "confirmed_at": datetime.now(timezone.utc),
            "status": DRAMATURGY_STATUS_CONFIRMED,
        }
    )
    return save_confirmed_dramaturgy(project, confirmed)


def update_dramaturgy_order(project: Project, edited_rows: list[dict]) -> DramaturgyPlan:
    """Übernimmt manuelle Tabellen-Bearbeitungen (z. B. aus st.data_editor) in
    den bestehenden Draft und speichert ihn erneut. Status bleibt DRAFT."""
    draft = load_dramaturgy_draft(project)
    if draft is None:
        raise ValueError("Kein Dramaturgie-Draft vorhanden, der aktualisiert werden könnte.")

    entries_by_folder = {entry.folder_name: entry for entry in draft.recommended_folder_order}
    updated_entries: list[DramaturgyFolderEntry] = []
    for row in edited_rows:
        folder_name = row.get("folder_name")
        existing = entries_by_folder.get(folder_name)
        if existing is None:
            continue
        updates: dict = {}
        if "order_index" in row:
            updates["order_index"] = int(row["order_index"])
        if "enabled" in row:
            updates["enabled"] = bool(row["enabled"])
        if "dramaturgy_role" in row:
            updates["dramaturgy_role"] = _normalize_role(str(row["dramaturgy_role"]))
        if "reason" in row:
            updates["reason"] = str(row["reason"])
        if "recommended_word_count" in row:
            updates["recommended_word_count"] = int(row["recommended_word_count"])
        if "recommended_min_words" in row:
            updates["recommended_min_words"] = int(row["recommended_min_words"])
        if "recommended_max_words" in row:
            updates["recommended_max_words"] = int(row["recommended_max_words"])
        if "transition_goal_to_next" in row:
            updates["transition_goal_to_next"] = str(row["transition_goal_to_next"])
        if "use_transition_from_previous" in row:
            updates["use_transition_from_previous"] = bool(row["use_transition_from_previous"])
        if "use_transition_to_next" in row:
            updates["use_transition_to_next"] = bool(row["use_transition_to_next"])
        if "use_callback_to_previous" in row:
            updates["use_callback_to_previous"] = bool(row["use_callback_to_previous"])
        if "use_contrast_with_previous" in row:
            updates["use_contrast_with_previous"] = bool(row["use_contrast_with_previous"])
        if "use_commonality_with_previous" in row:
            updates["use_commonality_with_previous"] = bool(row["use_commonality_with_previous"])
        updated_entries.append(existing.model_copy(update=updates))

    updated_plan = draft.model_copy(update={"recommended_folder_order": updated_entries})
    return save_dramaturgy_draft(project, updated_plan)


@dataclass
class DramaturgyBuildResult:
    status: str  # PASS | FAIL | PARSE_FAILED
    plan: DramaturgyPlan | None
    error: str | None
    llm_run_id: str
    provider: str
    model: str


def _parse_dramaturgy_response(raw_text: str) -> dict:
    payload = _extract_json(raw_text)
    if not isinstance(payload, dict):
        raise ValueError("Dramaturgie-Antwort ist kein JSON-Objekt.")
    return payload


def _float_field(entry: dict, key: str) -> float:
    try:
        return float(entry.get(key, 0.0))
    except (TypeError, ValueError):
        return 0.0


def _int_field(entry: dict, key: str, fallback: int) -> int:
    try:
        return int(entry.get(key, fallback))
    except (TypeError, ValueError):
        return fallback


def _bool_field(entry: dict, key: str, default: bool = False) -> bool:
    value = entry.get(key, default)
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"true", "1", "yes", "y", "ja"}:
            return True
        if normalized in {"false", "0", "no", "n", "nein", ""}:
            return False
    return default


_CRAFT_FLAG_KEYS = (
    "use_transition_from_previous",
    "use_transition_to_next",
    "use_callback_to_previous",
    "use_contrast_with_previous",
    "use_commonality_with_previous",
)


def _folder_entry_from_payload(entry: dict, *, default_order: int) -> DramaturgyFolderEntry | None:
    folder_name = str(entry.get("folder_name", "")).strip()
    if not folder_name:
        return None

    transition_goal_to_next = str(entry.get("transition_goal_to_next", ""))
    transition_from_previous_hint = str(entry.get("transition_from_previous_hint", ""))
    contrast_or_commonality_hint = str(entry.get("contrast_or_commonality_hint", ""))

    # Neue LLM-Antworten liefern explizite Booleans. Ältere Payloads ohne diese
    # Keys fallen auf die bisherige Hint-Heuristik zurück.
    if any(key in entry for key in _CRAFT_FLAG_KEYS):
        use_transition_from_previous = _bool_field(entry, "use_transition_from_previous")
        use_transition_to_next = _bool_field(entry, "use_transition_to_next")
        use_callback_to_previous = _bool_field(entry, "use_callback_to_previous")
        use_contrast_with_previous = _bool_field(entry, "use_contrast_with_previous")
        use_commonality_with_previous = _bool_field(entry, "use_commonality_with_previous")
    else:
        use_transition_from_previous = bool(transition_from_previous_hint.strip())
        use_transition_to_next = bool(transition_goal_to_next.strip())
        use_callback_to_previous = False
        use_contrast_with_previous = bool(contrast_or_commonality_hint.strip())
        use_commonality_with_previous = False

    return DramaturgyFolderEntry(
        folder_name=folder_name,
        order_index=_int_field(entry, "order_index", default_order),
        enabled=bool(entry.get("enabled", True)),
        dramaturgy_role=_normalize_role(str(entry.get("dramaturgy_role", ""))),
        reason=str(entry.get("reason", "")),
        visual_strength_score=_float_field(entry, "visual_strength_score"),
        asset_diversity_score=_float_field(entry, "asset_diversity_score"),
        hook_potential_score=_float_field(entry, "hook_potential_score"),
        recommended_word_count=_int_field(
            entry, "recommended_word_count", VOICEOVER_GEN_DEFAULT_FOLDER_TARGET_WORDS
        ),
        recommended_min_words=_int_field(
            entry, "recommended_min_words", VOICEOVER_GEN_DEFAULT_FOLDER_MIN_WORDS
        ),
        recommended_max_words=_int_field(
            entry, "recommended_max_words", VOICEOVER_GEN_DEFAULT_FOLDER_MAX_WORDS
        ),
        transition_goal_to_next=transition_goal_to_next,
        transition_from_previous_hint=transition_from_previous_hint,
        contrast_or_commonality_hint=contrast_or_commonality_hint,
        use_transition_from_previous=use_transition_from_previous,
        use_transition_to_next=use_transition_to_next,
        use_callback_to_previous=use_callback_to_previous,
        use_contrast_with_previous=use_contrast_with_previous,
        use_commonality_with_previous=use_commonality_with_previous,
        risks=as_str_list(entry.get("risks")),
    )


def build_dramaturgy_plan(
    project: Project,
    *,
    provider: str,
    model: str,
    planning_mode: str | None = None,
    max_output_tokens: int | None = None,
    disable_thinking: bool = False,
) -> DramaturgyBuildResult:
    """Plant die Dramaturgie über alle ausgewählten Ordner/Kapitel via LLM.

    Überschreibt einen bestehenden Draft NUR bei Erfolg — bei API-Fehlern oder
    ungültigem JSON bleibt ein vorhandener Draft unverändert (siehe §6/§9).

    planning_mode steuert die Prompt-Strategie:
    - geography: Reihenfolge primär nach Geographie / Reiseverlauf
    - variety: Reihenfolge primär nach Abwechslung / Kontrast

    max_output_tokens/disable_thinking erlauben es, für sehr umfangreiche
    Projekte (viele Ordner) das Output-Token-Limit gezielt zu erhöhen bzw. das
    interne "Thinking" des Modells abzuschalten, falls die Antwort sonst bei
    max_tokens abgeschnitten wird (siehe plan_llm_client.PlanLlmTruncatedResponseError)."""
    resolved_mode = (planning_mode or DRAMATURGY_PLANNING_MODE_VARIETY).strip().lower()

    project_brief = load_project_brief(project)
    style_profile = load_style_profile(project)
    folder_summaries = build_and_save_folder_inventory_summaries(project)

    from otio_app.services.voiceover_generation.style_reference_service import (
        style_context_text_for_prompts,
    )

    run_id, run_dir = create_llm_run_dir(project, STAGE_DRAMATURGY)
    prompt = build_dramaturgy_prompt(
        project_brief=project_brief,
        style_profile=style_profile,
        folder_summaries=folder_summaries,
        planning_mode=resolved_mode,
        style_context_text=style_context_text_for_prompts(project),
    )
    prompt_hash = content_hash(prompt)
    write_llm_prompt(run_dir, prompt)

    model_id = resolve_llm_model_id(provider, model)

    try:
        llm_response = generate_plan_text_with_metadata(
            prompt=prompt,
            model=model_id,
            max_output_tokens=max_output_tokens,
            disable_thinking=disable_thinking,
        )
    except Exception as exc:  # noqa: BLE001 — jeder LLM-/SDK-/Netzwerkfehler soll als
        # kontrollierter FAIL-Status zurückkommen statt die Streamlit-Seite crashen zu
        # lassen (nicht nur der eng gefasste PlanLlmNotConfiguredError-Fall).
        write_llm_raw_response(run_dir, raw_text=f"ERROR: {exc}", provider=provider, model=model)
        write_llm_parsed_response(run_dir, {"parse_error": str(exc)})
        write_llm_manifest(
            run_dir,
            LlmRunManifest(
                run_id=run_id,
                stage=STAGE_DRAMATURGY,
                provider=provider,
                model=model,
                prompt_hash=prompt_hash,
                status=STATUS_FAIL,
            ),
        )
        return DramaturgyBuildResult(
            status=STATUS_FAIL,
            plan=None,
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
        payload = _parse_dramaturgy_response(llm_response.raw_text)
    except (ValueError, TypeError) as exc:
        write_llm_parsed_response(run_dir, {"parse_error": str(exc)})
        write_llm_manifest(
            run_dir,
            LlmRunManifest(
                run_id=run_id,
                stage=STAGE_DRAMATURGY,
                provider=llm_response.provider,
                model=llm_response.model,
                prompt_hash=prompt_hash,
                status=STATUS_PARSE_FAILED,
                latency_ms=llm_response.latency_ms,
                token_usage=llm_response.token_usage,
            ),
        )
        return DramaturgyBuildResult(
            status=STATUS_PARSE_FAILED,
            plan=None,
            error=str(exc),
            llm_run_id=run_id,
            provider=llm_response.provider,
            model=llm_response.model,
        )

    raw_entries = payload.get("recommended_folder_order", [])
    entries: list[DramaturgyFolderEntry] = []
    if isinstance(raw_entries, list):
        for index, raw_entry in enumerate(raw_entries, start=1):
            if not isinstance(raw_entry, dict):
                continue
            parsed_entry = _folder_entry_from_payload(raw_entry, default_order=index)
            if parsed_entry is not None:
                entries.append(parsed_entry)

    # Nur Ordner behalten, die tatsächlich im Projekt existieren — schützt vor
    # LLM-Halluzinationen (erfundene Ordnernamen).
    valid_folder_names = {summary.folder_name for summary in folder_summaries}
    entries = [entry for entry in entries if entry.folder_name in valid_folder_names]

    plan = DramaturgyPlan(
        project_id=project.id,
        language=str(payload.get("language") or project_brief.language),
        project_title=str(payload.get("project_title") or project_brief.video_title),
        core_promise=str(payload.get("core_promise", "")),
        narrative_arc=str(payload.get("narrative_arc", "")),
        recommended_folder_order=entries,
        global_transition_strategy=str(payload.get("global_transition_strategy", "")),
        inventory_summary_hash=content_hash(
            json.dumps(
                [summary.model_dump(mode="json") for summary in folder_summaries],
                sort_keys=True,
            )
        ),
        project_brief_hash=content_hash_of_model(project_brief),
        style_profile_hash=content_hash_of_model(style_profile),
        llm_run_id=run_id,
        status=DRAMATURGY_STATUS_DRAFT,
        risks=as_str_list(payload.get("risks")),
    )
    saved = save_dramaturgy_draft(project, plan)
    write_llm_parsed_response(run_dir, saved.model_dump(mode="json"))
    write_llm_manifest(
        run_dir,
        LlmRunManifest(
            run_id=run_id,
            stage=STAGE_DRAMATURGY,
            provider=llm_response.provider,
            model=llm_response.model,
            prompt_hash=prompt_hash,
            status=STATUS_PASS,
            latency_ms=llm_response.latency_ms,
            token_usage=llm_response.token_usage,
        ),
    )

    return DramaturgyBuildResult(
        status=STATUS_PASS,
        plan=saved,
        error=None,
        llm_run_id=run_id,
        provider=llm_response.provider,
        model=llm_response.model,
    )
