"""Phase 11.1: LLM-gestützte Pexels-Suchqueries für den Cut-Plan-Supplement-
Workflow.

Erzeugt aus einem `CutPlanSupplementRequest` (Text, Visual Intent, Reason,
Ordnername) per LLM genau bis zu drei kurze, stichwortartige, ENGLISCHE
Suchqueries, jede mit dem Ort vorangestellt (Nutzeranforderung Juli 2026).
Läuft ausschließlich bei explizitem Aufruf (Klick auf „Supplement-Kandidaten
suchen“ im Cut-Plan-Tab) — niemals automatisch beim Draft-Bau oder bei der
Validierung.

Schreibt ausschließlich in den bestehenden LLM-Run-Ordner dieser Pipeline
(_otio/voiceover_generation/llm_runs/{run_id}/) — dieselbe Traceability wie
alle anderen LLM-Aufrufe dieser Pipeline (Prompt/Raw Response/Parsed
Response/Manifest). Schreibt niemals unter _otio/supplement/ oder in
reguläre Folder-Inventories.

Bei JEDEM Fehler (fehlender API-Key, Netzwerkfehler, ungültiges JSON, leere
Antwort) wird ein FAIL/PARSE_FAILED-Ergebnis mit leerer queries-Liste
zurückgegeben statt eine Exception zu werfen — der Aufrufer (siehe
cut_plan_supplement_bridge.search_candidates_for_cut_plan_request) fällt in
diesem Fall auf die bestehende deterministische Query-Logik zurück
(build_pexels_query_variants ohne llm_generated_queries).

Phase 9: ist auf dem Request bereits ein supplement_search_hint gesetzt
(vom Autor-LLM beim Skriptschreiben vorbereitet, siehe SentenceItem.
visual_asset_plan.supplement_search_hint), wird er dem Prompt als
bevorzugter Ausgangspunkt mitgegeben — unabhängig davon greift
cut_plan_supplement_bridge.search_candidates_for_cut_plan_request den
Hinweis ZUSÄTZLICH direkt als eigene Suchquery auf, auch wenn dieser
LLM-Aufruf hier fehlschlägt oder gar nicht ausgeführt wird."""

from __future__ import annotations

from dataclasses import dataclass, field

from otio_app.models import Project
from otio_app.services.gemini_client import _extract_json
from otio_app.services.supplement_search import ensure_location_in_query
from otio_app.services.voiceover_generation.cut_plan_supplement_models import CutPlanSupplementRequest
from otio_app.services.voiceover_generation.llm_trace_service import (
    STAGE_CUT_PLAN_SUPPLEMENT_QUERY,
    STATUS_FAIL,
    STATUS_PARSE_FAILED,
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
from otio_app.services.plan_llm_client import generate_plan_text_with_metadata

__all__ = [
    "CutPlanSupplementQueryResult",
    "MAX_LLM_SUPPLEMENT_QUERIES",
    "build_cut_plan_supplement_query_prompt",
    "generate_cut_plan_supplement_queries",
]

MAX_LLM_SUPPLEMENT_QUERIES = 3


@dataclass
class CutPlanSupplementQueryResult:
    status: str  # PASS | FAIL | PARSE_FAILED
    queries: list[str] = field(default_factory=list)
    run_id: str = ""
    provider: str = ""
    model: str = ""
    error: str = ""


def build_cut_plan_supplement_query_prompt(
    *,
    folder_name: str,
    text: str,
    visual_intent: str,
    reason: str,
    supplement_search_hint: str = "",
) -> str:
    location = (folder_name or "").strip() or "(unbekannter Ort)"
    hint = supplement_search_hint.strip()
    # Phase 9: ein bereits beim Skriptschreiben vorbereiteter Suchvorschlag
    # (SentenceItem.visual_asset_plan.supplement_search_hint) entstand mit
    # vollem redaktionellem Kontext des Satzes — er wird deshalb als
    # bevorzugter Ausgangspunkt genannt, nicht einfach verworfen.
    hint_block = (
        f"Bereits beim Skriptschreiben vorbereiteter Suchvorschlag: \"{hint}\"\n"
        "Nutze diesen Vorschlag als AUSGANGSPUNKT für deine erste Suchquery "
        "(du darfst ihn leicht anpassen/vervollständigen, z. B. den Ort "
        "ergänzen, aber verwerfe ihn nicht ohne guten Grund) und erstelle 2 "
        "weitere, unterschiedliche Varianten dazu.\n\n"
        if hint
        else ""
    )
    return (
        "Du erstellst Suchqueries für eine Stock-Footage-/Stock-Foto-Suche bei "
        "Pexels, auf ENGLISCH.\n\n"
        f"Ort/Ordner: {location}\n"
        f"Voice-over-Satz (Deutsch): {text.strip() or '(kein Text)'}\n"
        f"Visuelle Anforderung: {visual_intent.strip() or '(keine Angabe)'}\n"
        f"Grund für fehlendes Material: {reason.strip() or '(keine Angabe)'}\n\n"
        f"{hint_block}"
        "Erstelle genau 3 unterschiedliche, kurze, stichwortartige Suchqueries "
        "auf ENGLISCH für Pexels. Jede Suchquery MUSS mit dem Ort "
        f'("{location}") beginnen, gefolgt von 2-4 visuellen Schlagwörtern '
        "(keine ganzen Sätze, keine deutschen Wörter).\n\n"
        "Beispiel für den Satz \"Noch vor kurzem stand ich am fallenden Wasser "
        "der Havasu Falls, spürte seine Kühle auf der Haut.\":\n"
        '{"queries": ["Havasu Falls waterfall woman", '
        '"Havasu Falls blue water waterfall", "Havasu Falls Arizona waterfall"]}\n\n'
        "Antworte NUR als JSON in exakt diesem Format:\n"
        '{"queries": ["...", "...", "..."]}'
    )


def _parse_queries_response(raw_text: str, *, folder_name: str) -> list[str]:
    payload = _extract_json(raw_text)
    if not isinstance(payload, dict):
        raise ValueError("Antwort ist kein JSON-Objekt.")
    raw_queries = payload.get("queries")
    if not isinstance(raw_queries, list):
        raise ValueError("Feld 'queries' fehlt oder ist keine Liste.")

    queries: list[str] = []
    for raw_query in raw_queries:
        query = str(raw_query or "").strip()
        if not query:
            continue
        query = ensure_location_in_query(query, folder_name)
        queries.append(query)
        if len(queries) >= MAX_LLM_SUPPLEMENT_QUERIES:
            break
    if not queries:
        raise ValueError("Keine gültigen Suchqueries in der Antwort gefunden.")
    return queries


def generate_cut_plan_supplement_queries(
    project: Project,
    request: CutPlanSupplementRequest,
    *,
    provider: str,
    model: str,
) -> CutPlanSupplementQueryResult:
    """Ein LLM-Aufruf pro Klick auf „Supplement-Kandidaten suchen“ — niemals
    automatisch, niemals gecacht über mehrere Klicks hinweg (der Nutzer soll
    bei Bedarf jederzeit neue Queries für denselben Request bekommen können,
    z. B. nach einer Textänderung im Cut Plan)."""
    prompt = build_cut_plan_supplement_query_prompt(
        folder_name=request.folder_name,
        text=request.text,
        visual_intent=request.visual_intent,
        reason=request.reason,
        supplement_search_hint=request.supplement_search_hint,
    )
    prompt_hash = content_hash(prompt)
    model_id = resolve_llm_model_id(provider, model)
    run_id, run_dir = create_llm_run_dir(project, STAGE_CUT_PLAN_SUPPLEMENT_QUERY)
    write_llm_prompt(run_dir, prompt)

    try:
        llm_response = generate_plan_text_with_metadata(
            prompt=prompt,
            model=model_id,
            project=project,
            stage=STAGE_CUT_PLAN_SUPPLEMENT_QUERY,
        )
    except Exception as exc:  # noqa: BLE001 — jeder LLM-/SDK-/Netzwerkfehler soll als
        # kontrollierter FAIL-Status zurückkommen, damit der Aufrufer auf die
        # deterministische Query-Logik zurückfallen kann, statt die Suche
        # (und damit die ganze Seite) abzubrechen.
        write_llm_raw_response(run_dir, raw_text=f"ERROR: {exc}", provider=provider, model=model)
        write_llm_parsed_response(run_dir, {"parse_error": str(exc)})
        write_llm_manifest(
            run_dir,
            LlmRunManifest(
                run_id=run_id,
                stage=STAGE_CUT_PLAN_SUPPLEMENT_QUERY,
                provider=provider,
                model=model,
                prompt_hash=prompt_hash,
                status=STATUS_FAIL,
            ),
        )
        return CutPlanSupplementQueryResult(
            status=STATUS_FAIL, run_id=run_id, provider=provider, model=model, error=str(exc)
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
        queries = _parse_queries_response(llm_response.raw_text, folder_name=request.folder_name)
    except (ValueError, TypeError) as exc:
        write_llm_parsed_response(run_dir, {"parse_error": str(exc)})
        write_llm_manifest(
            run_dir,
            LlmRunManifest(
                run_id=run_id,
                stage=STAGE_CUT_PLAN_SUPPLEMENT_QUERY,
                provider=llm_response.provider,
                model=llm_response.model,
                prompt_hash=prompt_hash,
                status=STATUS_PARSE_FAILED,
                latency_ms=llm_response.latency_ms,
                token_usage=llm_response.token_usage,
            ),
        )
        return CutPlanSupplementQueryResult(
            status=STATUS_PARSE_FAILED,
            run_id=run_id,
            provider=llm_response.provider,
            model=llm_response.model,
            error=str(exc),
        )

    write_llm_parsed_response(run_dir, {"queries": queries})
    write_llm_manifest(
        run_dir,
        LlmRunManifest(
            run_id=run_id,
            stage=STAGE_CUT_PLAN_SUPPLEMENT_QUERY,
            provider=llm_response.provider,
            model=llm_response.model,
            prompt_hash=prompt_hash,
            status=STATUS_PASS,
            latency_ms=llm_response.latency_ms,
            token_usage=llm_response.token_usage,
        ),
    )
    return CutPlanSupplementQueryResult(
        status=STATUS_PASS,
        queries=queries,
        run_id=run_id,
        provider=llm_response.provider,
        model=llm_response.model,
    )
