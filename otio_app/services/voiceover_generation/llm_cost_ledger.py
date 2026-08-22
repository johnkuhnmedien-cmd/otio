"""Append-only LLM-Kostenledger pro Sprache (echte Tokens × interne Preisliste).

Zwei Dateien unter ``voiceover_generation/``:

- ``llm_costs.jsonl`` — eine Zeile pro Call
- ``llm_costs_summary.json`` — Summen für Karten/UI (kein Scan von llm_runs/)

Kein API-Key, keine Prompts. FAIL ohne Token-Usage wird nicht geschrieben.
"""

from __future__ import annotations

import json
import threading
from contextlib import contextmanager
from contextvars import ContextVar, Token
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from otio_app.models import Project
from otio_app.project_layout import get_voiceover_generation_dir
from otio_app.services.voiceover_generation.llm_pricing import actual_call_cost_usd
from otio_app.services.voiceover_generation.llm_trace_service import (
    STAGE_ASSET_ALLOCATION_CORRECTION,
    STAGE_CUT_PLAN_SUPPLEMENT_QUERY,
    STAGE_DRAMATURGY,
    STAGE_FOLDER_VOICEOVER,
    STAGE_INTRO_HOOK,
    STAGE_PROJECT_BRIEF_TITLE,
    STAGE_STYLE_PROFILE,
    STAGE_VOICEOVER_CORRECTION,
    STAGE_VOICEOVER_REVIEW,
    STAGE_YOUTUBE_PUBLISH,
    STAGE_YOUTUBE_QUIZ,
)
from otio_app.services.voiceover_generation.project_brief_defaults_service import (
    normalize_brief_language,
)

LLM_COSTS_FILENAME = "llm_costs.jsonl"
LLM_COSTS_SUMMARY_FILENAME = "llm_costs_summary.json"
LLM_COSTS_SCHEMA_VERSION = "1"
# Anzeige-Kurs; intern bleibt USD (Preistabelle).
LLM_COST_EUR_PER_USD = 0.92

STAGE_ENHANCED_SCRIPT = "enhanced_script"
STAGE_INTRO_CUT = "intro_cut"
STAGE_LLM_CUT = "llm_cut"
STAGE_LLM_CUT_REPAIR = "llm_cut_repair"
STAGE_SFX_PLANNER = "sfx_planner"
STAGE_MAPS_GEOCODE = "maps_geocode"
STAGE_FUNNEL_GEMINI = "funnel_gemini"
STAGE_ROUGH_CUT = "rough_cut"
STAGE_FINAL_CUT = "final_cut"

STAGE_LABELS_DE: dict[str, str] = {
    STAGE_PROJECT_BRIEF_TITLE: "Project Brief",
    STAGE_STYLE_PROFILE: "Style Profile",
    STAGE_DRAMATURGY: "Dramaturgie",
    STAGE_FOLDER_VOICEOVER: "Folder Voice-over",
    STAGE_VOICEOVER_REVIEW: "Voice-over Review",
    STAGE_VOICEOVER_CORRECTION: "Voice-over Korrektur",
    STAGE_ASSET_ALLOCATION_CORRECTION: "Asset-Zuordnung",
    STAGE_INTRO_HOOK: "Intro",
    STAGE_ENHANCED_SCRIPT: "Skripte",
    STAGE_INTRO_CUT: "Intro Cut",
    STAGE_LLM_CUT: "LLM Cut",
    STAGE_LLM_CUT_REPAIR: "LLM Cut Repair",
    STAGE_ROUGH_CUT: "Rough Cut",
    STAGE_FINAL_CUT: "Final Cut",
    STAGE_SFX_PLANNER: "SFX Planner",
    STAGE_MAPS_GEOCODE: "Karten / Geocode",
    STAGE_FUNNEL_GEMINI: "Funnel (Gemini)",
    STAGE_CUT_PLAN_SUPPLEMENT_QUERY: "Supplement-Query",
    STAGE_YOUTUBE_PUBLISH: "YouTube",
    STAGE_YOUTUBE_QUIZ: "YouTube Quiz",
}

COST_STAGE_ORDER: tuple[str, ...] = (
    STAGE_PROJECT_BRIEF_TITLE,
    STAGE_STYLE_PROFILE,
    STAGE_DRAMATURGY,
    STAGE_ENHANCED_SCRIPT,
    STAGE_FOLDER_VOICEOVER,
    STAGE_VOICEOVER_REVIEW,
    STAGE_VOICEOVER_CORRECTION,
    STAGE_INTRO_HOOK,
    STAGE_INTRO_CUT,
    STAGE_LLM_CUT,
    STAGE_LLM_CUT_REPAIR,
    STAGE_ROUGH_CUT,
    STAGE_FINAL_CUT,
    STAGE_SFX_PLANNER,
    STAGE_MAPS_GEOCODE,
    STAGE_FUNNEL_GEMINI,
    STAGE_CUT_PLAN_SUPPLEMENT_QUERY,
    STAGE_ASSET_ALLOCATION_CORRECTION,
    STAGE_YOUTUBE_PUBLISH,
    STAGE_YOUTUBE_QUIZ,
)

_LEDGER_LOCK = threading.Lock()


@dataclass(frozen=True)
class LlmCostScope:
    project: Project
    stage: str
    folder_name: str = ""


_COST_SCOPE: ContextVar[LlmCostScope | None] = ContextVar(
    "otio_llm_cost_scope", default=None
)


def current_llm_cost_scope() -> LlmCostScope | None:
    return _COST_SCOPE.get()


@contextmanager
def llm_cost_scope(
    project: Project | None,
    *,
    stage: str,
    folder_name: str = "",
) -> Iterator[LlmCostScope | None]:
    """Markiert nachfolgende Plan-LLM- bzw. Funnel-Calls für das Ledger."""
    if project is None or not str(stage or "").strip():
        yield None
        return
    scope = LlmCostScope(
        project=project,
        stage=str(stage).strip(),
        folder_name=str(folder_name or "").strip(),
    )
    token: Token = _COST_SCOPE.set(scope)
    try:
        yield scope
    finally:
        _COST_SCOPE.reset(token)


def llm_costs_dir(project: Project) -> Path:
    return get_voiceover_generation_dir(project.language_work_dir_path)


def llm_costs_jsonl_path(project: Project) -> Path:
    return llm_costs_dir(project) / LLM_COSTS_FILENAME


def llm_costs_summary_path(project: Project) -> Path:
    return llm_costs_dir(project) / LLM_COSTS_SUMMARY_FILENAME


def usd_to_eur(amount_usd: float) -> float:
    return float(amount_usd) * LLM_COST_EUR_PER_USD


def format_eur(amount_usd: float) -> str:
    """Deutsche Anzeige aus internem USD."""
    return f"{usd_to_eur(amount_usd):.2f} €".replace(".", ",")


def stage_label_de(stage: str) -> str:
    key = str(stage or "").strip()
    return STAGE_LABELS_DE.get(key, key or "Sonstiges")


def tokens_from_usage(usage: dict[str, Any] | None) -> tuple[int, int]:
    payload = usage if isinstance(usage, dict) else {}
    inp = payload.get("input_tokens")
    if inp is None:
        inp = payload.get("input")
    if inp is None:
        inp = payload.get("prompt_tokens")
    out = payload.get("output_tokens")
    if out is None:
        out = payload.get("output")
    if out is None:
        out = payload.get("completion_tokens")
    try:
        input_tokens = max(0, int(inp or 0))
    except (TypeError, ValueError):
        input_tokens = 0
    try:
        output_tokens = max(0, int(out or 0))
    except (TypeError, ValueError):
        output_tokens = 0
    return input_tokens, output_tokens


def empty_stage_totals() -> dict[str, Any]:
    return {
        "call_count": 0,
        "input_tokens": 0,
        "output_tokens": 0,
        "cost_usd": 0.0,
    }


def empty_summary() -> dict[str, Any]:
    return {
        "schema_version": LLM_COSTS_SCHEMA_VERSION,
        "currency_stored": "USD",
        "updated_at": "",
        "call_count": 0,
        "input_tokens": 0,
        "output_tokens": 0,
        "cost_usd": 0.0,
        "by_stage": {},
    }


def load_llm_costs_summary(project: Project) -> dict[str, Any]:
    path = llm_costs_summary_path(project)
    if not path.is_file():
        return empty_summary()
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return empty_summary()
    if not isinstance(payload, dict):
        return empty_summary()
    summary = empty_summary()
    summary.update({key: payload[key] for key in summary if key in payload})
    by_stage = payload.get("by_stage")
    summary["by_stage"] = by_stage if isinstance(by_stage, dict) else {}
    return summary


def _empty_event_totals() -> dict[str, int | float]:
    return empty_stage_totals()


def _add_event_to_bucket(bucket: dict[str, Any], event: dict[str, Any]) -> None:
    bucket["call_count"] = int(bucket.get("call_count") or 0) + 1
    bucket["input_tokens"] = int(bucket.get("input_tokens") or 0) + int(
        event.get("input_tokens") or 0
    )
    bucket["output_tokens"] = int(bucket.get("output_tokens") or 0) + int(
        event.get("output_tokens") or 0
    )
    bucket["cost_usd"] = float(bucket.get("cost_usd") or 0.0) + float(
        event.get("cost_usd") or 0.0
    )


def _write_summary(path: Path, summary: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(summary, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def append_llm_cost_event(
    project: Project,
    *,
    stage: str,
    provider: str,
    model: str,
    status: str,
    input_tokens: int,
    output_tokens: int,
    folder_name: str = "",
    run_id: str = "",
    error: str = "",
) -> dict[str, Any] | None:
    """Hängt eine Ledger-Zeile an und aktualisiert die Summary.

    FAIL/Fehler ohne Tokens werden übersprungen (nicht abgerechnet).
    Erfolgreiche Calls mit 0 Tokens zählen (Call-Anzahl).
    """
    stage_key = str(stage or "").strip()
    if not stage_key:
        return None
    status_key = str(status or "ok").strip().lower() or "ok"
    inp = max(0, int(input_tokens))
    out = max(0, int(output_tokens))
    if status_key not in {"ok", "pass"} and inp <= 0 and out <= 0:
        return None

    quote = actual_call_cost_usd(
        provider=provider,
        model=model,
        input_tokens=inp,
        output_tokens=out,
    )
    now = datetime.now(timezone.utc).isoformat()
    event = {
        "ts": now,
        "stage": stage_key,
        "provider": str(provider or ""),
        "model": str(model or ""),
        "status": status_key,
        "input_tokens": quote.input_tokens,
        "output_tokens": quote.output_tokens,
        "cost_usd": round(quote.total_usd, 6),
        "price_unknown": bool(quote.price_unknown),
        "folder_name": str(folder_name or "").strip(),
        "run_id": str(run_id or "").strip(),
        "error": str(error or "").strip()[:300],
    }

    jsonl_path = llm_costs_jsonl_path(project)
    summary_path = llm_costs_summary_path(project)
    with _LEDGER_LOCK:
        jsonl_path.parent.mkdir(parents=True, exist_ok=True)
        with jsonl_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(event, ensure_ascii=False) + "\n")
        summary = load_llm_costs_summary(project)
        _add_event_to_bucket(summary, event)
        by_stage = dict(summary.get("by_stage") or {})
        bucket = dict(by_stage.get(stage_key) or _empty_event_totals())
        _add_event_to_bucket(bucket, event)
        by_stage[stage_key] = bucket
        summary["by_stage"] = by_stage
        summary["updated_at"] = now
        summary["schema_version"] = LLM_COSTS_SCHEMA_VERSION
        summary["currency_stored"] = "USD"
        _write_summary(summary_path, summary)
    return event


def record_plan_llm_cost(
    *,
    project: Project | None = None,
    stage: str = "",
    folder_name: str = "",
    provider: str = "",
    model: str = "",
    token_usage: dict[str, Any] | None = None,
    status: str = "ok",
    run_id: str = "",
    error: str = "",
) -> dict[str, Any] | None:
    """Nimmt explizite Args oder den aktuellen ``llm_cost_scope``."""
    scope = current_llm_cost_scope()
    target = project if project is not None else (scope.project if scope else None)
    stage_key = str(stage or "").strip() or (scope.stage if scope else "")
    folder = str(folder_name or "").strip() or (scope.folder_name if scope else "")
    if target is None or not stage_key:
        return None
    inp, out = tokens_from_usage(token_usage)
    return append_llm_cost_event(
        target,
        stage=stage_key,
        provider=provider,
        model=model,
        status=status,
        input_tokens=inp,
        output_tokens=out,
        folder_name=folder,
        run_id=run_id,
        error=error,
    )


def _gemini_usage_tokens(response: Any) -> tuple[int, int]:
    usage_meta = getattr(response, "usage_metadata", None)
    if usage_meta is None and isinstance(response, dict):
        usage_meta = response.get("usage_metadata")
    if isinstance(usage_meta, dict):
        inp = usage_meta.get("prompt_token_count", usage_meta.get("input_tokens"))
        out = usage_meta.get("candidates_token_count", usage_meta.get("output_tokens"))
    elif usage_meta is None:
        inp = None
        out = None
    else:
        inp = getattr(usage_meta, "prompt_token_count", None)
        if inp is None:
            inp = getattr(usage_meta, "input_tokens", None)
        out = getattr(usage_meta, "candidates_token_count", None)
        if out is None:
            out = getattr(usage_meta, "output_tokens", None)
    return tokens_from_usage({"input_tokens": inp, "output_tokens": out})


def record_gemini_response_cost(
    response: Any,
    *,
    model: str,
    status: str = "ok",
    error: str = "",
) -> dict[str, Any] | None:
    """Funnel/Gemini-Calls, die nicht über den Plan-LLM-Router laufen.

    Ohne ``llm_cost_scope`` wird nichts geschrieben.
    """
    scope = current_llm_cost_scope()
    if scope is None:
        return None
    inp, out = _gemini_usage_tokens(response)
    return record_plan_llm_cost(
        provider="gemini",
        model=model,
        token_usage={"input_tokens": inp, "output_tokens": out},
        status=status,
        error=error,
    )


def record_gemini_response_cost_safe(
    response: Any,
    *,
    model: str,
    status: str = "ok",
    error: str = "",
) -> dict[str, Any] | None:
    """Wie ``record_gemini_response_cost``, fängt Ledger-Fehler ab."""
    try:
        return record_gemini_response_cost(
            response,
            model=model,
            status=status,
            error=error,
        )
    except Exception:
        return None


def format_family_cost_line(projects: list[Project]) -> str:
    """Eine Zeile für Gespeicherte-Projekte-Karten, z. B. ``JP 4,20 € · Σ 4,20 €``."""
    parts: list[str] = []
    total_usd = 0.0
    any_file = False
    ordered = sorted(
        projects,
        key=lambda item: normalize_brief_language(getattr(item, "language", "") or ""),
    )
    for project in ordered:
        path = llm_costs_summary_path(project)
        if not path.is_file():
            continue
        summary = load_llm_costs_summary(project)
        cost = float(summary.get("cost_usd") or 0.0)
        calls = int(summary.get("call_count") or 0)
        if calls <= 0 and cost <= 0:
            continue
        any_file = True
        lang = normalize_brief_language(getattr(project, "language", "") or "") or "?"
        parts.append(f"{lang} {format_eur(cost)}")
        total_usd += cost
    if not any_file:
        return ""
    if len(parts) == 1:
        return parts[0]
    return f"{' · '.join(parts)} · Σ {format_eur(total_usd)}"


def iter_recent_cost_events(project: Project, *, limit: int = 40) -> list[dict[str, Any]]:
    path = llm_costs_jsonl_path(project)
    if not path.is_file():
        return []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError):
        return []
    events: list[dict[str, Any]] = []
    for raw in lines[-max(1, int(limit)) :]:
        raw = raw.strip()
        if not raw:
            continue
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            events.append(payload)
    events.reverse()
    return events
