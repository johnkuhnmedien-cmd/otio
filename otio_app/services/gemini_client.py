"""Gemini-Client — API-Schlüssel nur aus Umgebungsvariablen."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Optional

from otio_app.config import get_gemini_model_from_env
from otio_app.services.api_keys import get_api_key
from otio_app.defaults import GEMINI_MODEL_CHOICES, GEMINI_MODEL_LABELS


class GeminiNotConfiguredError(RuntimeError):
    """GEMINI_API_KEY fehlt."""


def resolve_gemini_model(model: Optional[str] = None) -> str:
    """Ermittelt das zu nutzende Modell (UI-Auswahl > .env > Standard)."""
    if model and model.strip() in GEMINI_MODEL_CHOICES:
        return model.strip()
    return get_gemini_model_from_env()


def get_default_gemini_model() -> str:
    """Standardmodell für die UI (aus .env oder App-Default)."""
    return get_gemini_model_from_env()


def format_gemini_model_label(model_id: str) -> str:
    """Anzeigename für ein Modell in der UI."""
    return GEMINI_MODEL_LABELS.get(model_id, model_id)


def _get_client():
    api_key = get_api_key("GEMINI_API_KEY")
    if not api_key:
        raise GeminiNotConfiguredError(
            "GEMINI_API_KEY ist nicht gesetzt. "
            "Bitte unter Systemstatus → API-Schlüssel oder in .env eintragen."
        )
    from google import genai

    return genai.Client(api_key=api_key)


def _extract_json(text: str) -> Any:
    cleaned = text.strip()
    fence_match = re.search(r"```(?:json)?\s*(.*?)\s*```", cleaned, re.DOTALL)
    if fence_match:
        cleaned = fence_match.group(1)
    return json.loads(cleaned)


def describe_media_from_frames(
    media_name: str,
    folder_name: str,
    frame_paths: list[Path],
    language: str,
    *,
    model: Optional[str] = None,
) -> str:
    """Sendet Frames eines einzelnen Assets an Gemini und liefert eine Beschreibung."""
    if not frame_paths:
        return "Keine Frames verfügbar."

    client = _get_client()
    from google.genai import types

    parts: list[types.Part] = [
        types.Part.from_text(
            text=(
                f"Du analysierst die Mediendatei '{media_name}' aus dem Ordner "
                f"'{folder_name}'. "
                f"Sprache der Antwort: {language}. "
                "Beschreibe kurz und sachlich, was zu sehen ist (Ort, Motiv, Stimmung, "
                "Kameraperspektive). Maximal 6 Sätze."
            )
        )
    ]
    for frame_path in frame_paths:
        parts.append(
            types.Part.from_bytes(
                data=frame_path.read_bytes(),
                mime_type="image/jpeg",
            )
        )

    response = client.models.generate_content(
        model=resolve_gemini_model(model),
        contents=[types.Content(role="user", parts=parts)],
    )
    return (response.text or "").strip()


SUPPLEMENT_VALIDATION_STATUSES = ("PASS", "WEAK_PASS", "NEEDS_USER_REVIEW", "FAIL")


def validate_supplement_asset_match(
    *,
    passage_text: str,
    visual_requirement: str,
    description: str,
    location_name: str = "",
    must_show: Optional[list[str]] = None,
    avoid_showing: Optional[list[str]] = None,
    language: str = "de",
    model: Optional[str] = None,
) -> dict[str, Any]:
    """Prüft mit Gemini, ob ein supplementiertes Asset wirklich zum Voice-over-Satz passt.

    Vergleicht die (Gemini-)Beschreibung des heruntergeladenen Assets gegen den
    ursprünglichen Bedarf (passage_text/visual_requirement/must_show/avoid_showing)
    und liefert PASS/WEAK_PASS/NEEDS_USER_REVIEW/FAIL statt das Asset ungeprüft
    zu übernehmen.
    """
    client = _get_client()
    from google.genai import types

    must_show_line = ", ".join(must_show or []) or "keine besonderen Vorgaben"
    avoid_line = ", ".join(avoid_showing or []) or "keine"
    prompt = (
        "Du prüfst, ob ein gefundenes Video-/Bild-Asset zu einem Voice-over-Satz passt.\n"
        f"Ort/Ordner: {location_name or 'unbekannt'}\n"
        f"Voice-over-Satz: {passage_text.strip()}\n"
        f"Visuelle Anforderung: {visual_requirement.strip() or passage_text.strip()}\n"
        f"Muss zeigen: {must_show_line}\n"
        f"Darf nicht zeigen: {avoid_line}\n"
        f"Beschreibung des gefundenen Assets: {description.strip()}\n\n"
        "Bewerte, ob dieses Asset inhaltlich zum Satz passt. Antworte NUR als JSON:\n"
        '{"status":"PASS|WEAK_PASS|NEEDS_USER_REVIEW|FAIL","score":0.0,"reason":"..."}\n'
        "PASS = passt eindeutig. WEAK_PASS = passt teilweise/generisch. "
        "NEEDS_USER_REVIEW = unklar, manuelle Prüfung nötig. FAIL = passt nicht "
        "oder zeigt verbotene Inhalte."
    )
    response = client.models.generate_content(
        model=resolve_gemini_model(model),
        contents=[types.Content(role="user", parts=[types.Part.from_text(text=prompt)])],
    )
    text = response.text or "{}"
    try:
        payload = _extract_json(text)
    except json.JSONDecodeError:
        payload = {"status": "NEEDS_USER_REVIEW", "score": 0.5, "reason": "Antwort nicht auswertbar."}
    status = str(payload.get("status", "NEEDS_USER_REVIEW")).upper()
    if status not in SUPPLEMENT_VALIDATION_STATUSES:
        status = "NEEDS_USER_REVIEW"
    try:
        score = float(payload.get("score", 0.5))
    except (TypeError, ValueError):
        score = 0.5
    reason = str(payload.get("reason", "")).strip()
    return {"status": status, "score": max(0.0, min(1.0, score)), "reason": reason}


def describe_folder_from_frames(
    folder_name: str,
    frame_paths: list[Path],
    language: str,
    *,
    model: Optional[str] = None,
) -> str:
    """Sendet Frame-Bilder an Gemini und liefert eine Ordnerbeschreibung."""
    if not frame_paths:
        return "Keine Frames verfügbar."

    client = _get_client()
    from google.genai import types

    parts: list[types.Part] = [
        types.Part.from_text(
            text=(
                f"Du analysierst Bildmaterial für den Ordner '{folder_name}'. "
                f"Sprache der Antwort: {language}. "
                "Beschreibe kurz und sachlich, was zu sehen ist (Ort, Motiv, Stimmung, "
                "Kameraperspektive). Maximal 6 Sätze."
            )
        )
    ]
    for frame_path in frame_paths:
        parts.append(
            types.Part.from_bytes(
                data=frame_path.read_bytes(),
                mime_type="image/jpeg",
            )
        )

    response = client.models.generate_content(
        model=resolve_gemini_model(model),
        contents=[types.Content(role="user", parts=parts)],
    )
    return (response.text or "").strip()


def analyze_voice_over_file(
    audio_path: Path,
    language: str,
    *,
    model: Optional[str] = None,
) -> dict[str, Any]:
    """Analysiert Voice-over-Audio mit Timestamps pro Passage."""
    client = _get_client()
    from google.genai import types

    mime_map = {
        ".wav": "audio/wav",
        ".mp3": "audio/mpeg",
        ".m4a": "audio/mp4",
        ".aac": "audio/aac",
        ".flac": "audio/flac",
        ".ogg": "audio/ogg",
    }
    mime_type = mime_map.get(audio_path.suffix.lower(), "audio/mpeg")
    prompt = (
        "Transkribiere diese Voice-over-Datei. "
        f"Sprache: {language}. "
        "Antworte NUR als JSON-Objekt mit diesem Schema:\n"
        '{"segments":[{"start_sec":0.0,"end_sec":1.2,"text":"..."}]}\n'
        "start_sec/end_sec in Sekunden (float). Jede Passage ein Segment."
    )
    parts = [
        types.Part.from_text(text=prompt),
        types.Part.from_bytes(data=audio_path.read_bytes(), mime_type=mime_type),
    ]
    response = client.models.generate_content(
        model=resolve_gemini_model(model),
        contents=[types.Content(role="user", parts=parts)],
    )
    text = response.text or "{}"
    try:
        payload = _extract_json(text)
    except json.JSONDecodeError:
        payload = {"segments": [{"start_sec": 0.0, "end_sec": 0.0, "text": text.strip()}]}
    return payload


def plan_passage_assets(
    passage_text: str,
    folder_name: str,
    assets: list[dict[str, str]],
    language: str,
    *,
    model: Optional[str] = None,
    extra_instructions: str = "",
) -> list[dict[str, Any]]:
    """Zerlegt eine Passage in Motive und ordnet lokale Assets zu (nur Text an Gemini)."""
    if not passage_text.strip():
        return []

    client = _get_client()
    from google.genai import types

    asset_lines = "\n".join(
        f'- path="{item["path"]}" description="{item.get("description", "")}"'
        for item in assets
    )
    prompt = build_plan_passage_prompt(
        passage_text=passage_text,
        folder_name=folder_name,
        asset_lines=asset_lines,
        language=language,
        extra_instructions=extra_instructions,
    )
    response = client.models.generate_content(
        model=resolve_gemini_model(model),
        contents=[types.Content(role="user", parts=[types.Part.from_text(text=prompt)])],
    )
    text = response.text or "{}"
    try:
        payload = _extract_json(text)
    except json.JSONDecodeError:
        payload = {
            "parts": [
                {
                    "text": passage_text.strip(),
                    "motif": passage_text.strip()[:80],
                    "asset_path": assets[0]["path"] if assets else None,
                    "confidence": "low",
                }
            ]
        }
    parts = payload.get("parts", [])
    if not isinstance(parts, list):
        return []
    return [part for part in parts if isinstance(part, dict)]


def build_plan_passage_prompt(
    *,
    passage_text: str,
    folder_name: str,
    asset_lines: str,
    language: str,
    extra_instructions: str = "",
) -> str:
    """Prompt für Motiv-Zerlegung und Asset-Zuordnung."""
    sections = [
        f"Du planst Video-Shots für den Ordner '{folder_name}'. Sprache: {language}.",
        f"Passage: {passage_text.strip()}",
        "Verfügbare lokale Assets:",
        asset_lines or "- (keine)",
    ]
    instructions = extra_instructions.strip()
    if instructions:
        sections.extend(
            [
                "",
                "Zusätzliche Anweisungen des Editors (unbedingt beachten):",
                instructions,
            ]
        )
    sections.extend(
        [
            "",
            "Wenn die Passage mehrere Sehenswürdigkeiten/Motive nennt, erstelle mehrere Teile.",
            "Antworte NUR als JSON:",
            '{"parts":[{"text":"...","motif":"...","asset_path":"exakter path oder null","confidence":"high|low"}]}',
            "asset_path muss exakt einem path aus der Liste entsprechen oder null sein.",
        ]
    )
    return "\n".join(sections)


def is_gemini_configured() -> bool:
    return bool(get_api_key("GEMINI_API_KEY"))
