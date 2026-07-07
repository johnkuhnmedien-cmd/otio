"""Gemini-Client — API-Schlüssel nur aus Umgebungsvariablen."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Optional

from otio_app.config import get_gemini_model_from_env
from otio_app.services.api_keys import get_api_key
from otio_app.services.plan_llm_client import generate_plan_text
from otio_app.defaults import (
    GEMINI_MODEL_CHOICES,
    GEMINI_MODEL_LABELS,
    MATCH_QUALITY_CHOICES,
    MATCH_QUALITY_GUT,
    MATCH_QUALITY_MITTEL,
    MATCH_QUALITY_SEHR_GUT,
    MATCH_QUALITY_UNPASSEND,
)


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


def normalize_match_quality(value: str | None) -> str:
    """Normalisiert Gemini-Antworten auf die vier festen Passungsstufen."""
    if not value:
        return ""
    normalized = value.strip().lower().replace(" ", "_").replace("-", "_")
    aliases = {
        "sehrgut": MATCH_QUALITY_SEHR_GUT,
        "very_good": MATCH_QUALITY_SEHR_GUT,
        "excellent": MATCH_QUALITY_SEHR_GUT,
        "good": MATCH_QUALITY_GUT,
        "medium": MATCH_QUALITY_MITTEL,
        "moderate": MATCH_QUALITY_MITTEL,
        "ok": MATCH_QUALITY_MITTEL,
        "poor": MATCH_QUALITY_UNPASSEND,
        "bad": MATCH_QUALITY_UNPASSEND,
        "unsuitable": MATCH_QUALITY_UNPASSEND,
        "unpassend": MATCH_QUALITY_UNPASSEND,
        "nicht_passend": MATCH_QUALITY_UNPASSEND,
    }
    if normalized in MATCH_QUALITY_CHOICES:
        return normalized
    return aliases.get(normalized, "")


def _format_segment_lines_basic(segments: list[dict[str, Any]]) -> str:
    rows: list[list[Any]] = []
    for segment in segments:
        beat_id = str(segment.get("beat_id", "")).strip()
        text = str(segment.get("text", "")).strip()
        start_sec = segment.get("start_sec", 0.0)
        end_sec = segment.get("end_sec", 0.0)
        if not text:
            continue
        duration = max(0.0, float(end_sec) - float(start_sec))
        rows.append(
            [
                beat_id,
                f"{float(start_sec):.3f}",
                f"{float(end_sec):.3f}",
                f"{duration:.3f}",
                text,
            ]
        )
    return _markdown_table(
        ["beat_id", "start_sec", "end_sec", "duration_sec", "text"],
        rows,
    )


def plan_folder_assets(
    *,
    folder_name: str,
    segments: list[dict[str, Any]],
    assets: list[dict[str, str]],
    language: str,
    model: Optional[str] = None,
    extra_instructions: str = "",
    section_outro_sec: float = 0.0,
    shot_min_sec: float = 3.0,
    shot_max_sec: float = 8.0,
    audio_offset_sec: float = 1.0,
    correction_instructions: str = "",
    max_asset_usage: int | None = None,
    min_asset_reuse_distance_shots: int = 0,
    prompt_mode: str = "full",
) -> list[dict[str, Any]]:
    """Plant alle Voice-over-Segmente und optional das Ordner-Ausklingen in einem Call."""
    if not segments and section_outro_sec <= 0.05:
        return []

    asset_lines = _format_asset_lines(assets)
    if prompt_mode == "free":
        prompt = build_plan_folder_free_prompt(
            folder_name=folder_name,
            segment_lines=_format_segment_lines_basic(segments),
            asset_lines=asset_lines,
            language=language,
            rule_text=extra_instructions,
            correction_instructions=correction_instructions,
        )
    else:
        prompt = build_plan_folder_prompt(
            folder_name=folder_name,
            segment_lines=_format_segment_lines(
                segments,
                shot_min_sec=shot_min_sec,
                shot_max_sec=shot_max_sec,
            ),
            asset_lines=asset_lines,
            language=language,
            extra_instructions=extra_instructions,
            section_outro_sec=section_outro_sec,
            shot_min_sec=shot_min_sec,
            shot_max_sec=shot_max_sec,
            audio_offset_sec=audio_offset_sec,
            correction_instructions=correction_instructions,
            max_asset_usage=max_asset_usage,
            min_asset_reuse_distance_shots=min_asset_reuse_distance_shots,
        )
    text = generate_plan_text(prompt=prompt, model=model)
    try:
        payload = _extract_json(text)
    except json.JSONDecodeError:
        payload = {"beats": []}
    beats = payload.get("beats", [])
    if not isinstance(beats, list):
        return []
    return [beat for beat in beats if isinstance(beat, dict)]


def _markdown_table_cell(value: Any) -> str:
    text = str(value).replace("\n", " ").replace("\r", " ").strip()
    return text.replace("|", "\\|")


def _markdown_table(headers: list[str], rows: list[list[Any]]) -> str:
    if not rows:
        return "_Keine Einträge._"
    header_line = "| " + " | ".join(headers) + " |"
    separator = "| " + " | ".join("---" for _ in headers) + " |"
    body_lines = [
        "| " + " | ".join(_markdown_table_cell(cell) for cell in row) + " |" for row in rows
    ]
    return "\n".join([header_line, separator, *body_lines])


def _format_segment_lines(
    segments: list[dict[str, Any]],
    *,
    shot_min_sec: float = 3.0,
    shot_max_sec: float = 8.0,
) -> str:
    from otio_app.services.shot_timing import allowed_parts_for_segment

    rows: list[list[Any]] = []
    for segment in segments:
        beat_id = str(segment.get("beat_id", "")).strip()
        text = str(segment.get("text", "")).strip()
        start_sec = segment.get("start_sec", 0.0)
        end_sec = segment.get("end_sec", 0.0)
        if not text:
            continue
        duration = max(0.0, float(end_sec) - float(start_sec))
        bounds = allowed_parts_for_segment(duration, min_sec=shot_min_sec, max_sec=shot_max_sec)
        rows.append(
            [
                beat_id,
                f"{float(start_sec):.3f}",
                f"{float(end_sec):.3f}",
                f"{duration:.3f}",
                bounds.min_parts,
                bounds.max_parts,
                "ja" if bounds.short_segment_allowed else "nein",
                text,
            ]
        )
    return _markdown_table(
        [
            "beat_id",
            "start_sec",
            "end_sec",
            "duration_sec",
            "parts_min",
            "parts_max",
            "short_ok",
            "text",
        ],
        rows,
    )


def _format_asset_lines(assets: list[dict[str, str]]) -> str:
    rows = [
        [
            item.get("path", ""),
            item.get("asset_id") or "",
            item.get("description", ""),
        ]
        for item in assets
        if item.get("path")
    ]
    return _markdown_table(["path", "asset_id", "description"], rows)


def _asset_usage_rules_markdown(
    *,
    max_asset_usage: int | None,
    min_asset_reuse_distance_shots: int,
) -> str:
    if max_asset_usage is None and min_asset_reuse_distance_shots <= 0:
        return ""

    rows: list[list[Any]] = []
    if max_asset_usage is not None:
        rows.append(["max_asset_usage", max_asset_usage, "Jede asset_id max. N× im gesamten Plan"])
        if max_asset_usage == 1:
            rows.append(["reuse_policy", "unique", "Keine Wiederverwendung derselben asset_id"])
    if min_asset_reuse_distance_shots > 0:
        rows.append(
            [
                "min_reuse_distance_shots",
                min_asset_reuse_distance_shots,
                "Gleiche asset_id erst nach N anderen Shots",
            ]
        )
    rows.append(["asset_reuse_policy", "hard_block", "Regeln sind hart — nicht verletzen"])

    table = _markdown_table(["regel", "wert", "bedeutung"], rows)
    notes = [
        "- Bei zu wenigen passenden Assets: `asset_path: null` (Supplement) statt Regel brechen.",
        "- Asset-Nutzung gilt global über alle Shots inkl. Outro/Supplement.",
    ]
    if max_asset_usage == 1 and min_asset_reuse_distance_shots > 0:
        notes.append("- Bei max_asset_usage = 1 ist Abstandsregel irrelevant (keine Wiederverwendung).")
    return table + "\n\n" + "\n".join(notes)


def _shot_timing_rules_markdown(*, shot_min_sec: float, shot_max_sec: float, audio_offset_sec: float) -> str:
    rows = [
        ["shot_min_sec", f"{shot_min_sec:.1f}", "Mindestdauer je part/Shot"],
        ["shot_max_sec", f"{shot_max_sec:.1f}", "Höchstdauer je part/Shot"],
        ["audio_offset_sec", f"{audio_offset_sec:.1f}", "Voice-over-Start auf der Timeline"],
    ]
    table = _markdown_table(["parameter", "wert", "bedeutung"], rows)
    notes = [
        "- Pro Segment: zwischen `parts_min` und `parts_max` Teile zurückgeben.",
        "- Jeder part wird ein visueller Shot mit Dauer innerhalb shot_min/max.",
        "- Wenn `short_ok = ja`: genau 1 part, auch wenn kürzer als shot_min.",
        "- Voice-over-Zeiten pro Segment sind fix — Text sinnvoll auf Teile verteilen.",
        "- Wenn Timing nicht erfüllbar: mehr Teile oder `asset_path: null` (Supplement).",
    ]
    return table + "\n\n" + "\n".join(notes)


def build_plan_folder_prompt(
    *,
    folder_name: str,
    segment_lines: str,
    asset_lines: str,
    language: str,
    extra_instructions: str = "",
    section_outro_sec: float = 0.0,
    shot_min_sec: float = 3.0,
    shot_max_sec: float = 8.0,
    audio_offset_sec: float = 1.0,
    correction_instructions: str = "",
    max_asset_usage: int | None = None,
    min_asset_reuse_distance_shots: int = 0,
) -> str:
    """Prompt für gesamtheitliche Motiv-Planung und Asset-Zuordnung (Markdown Option A)."""
    from otio_app.defaults import OUTRO_BEAT_ID

    sections = [
        "## Aufgabe",
        (
            f"Plane Video-Shots für Ordner **{folder_name}** (Sprache: {language}). "
            "Betrachte alle Segmente, Assets und Regeln **gesamtheitlich**."
        ),
        "",
        "## Eingabe: Voice-over-Segmente",
        segment_lines or "_Keine Segmente._",
        "",
        "## Eingabe: Verfügbare Assets",
        asset_lines or "_Keine Assets._",
    ]

    asset_rules = _asset_usage_rules_markdown(
        max_asset_usage=max_asset_usage,
        min_asset_reuse_distance_shots=min_asset_reuse_distance_shots,
    )
    if asset_rules:
        sections.extend(["", "## Harte Regeln: Asset-Nutzung", asset_rules])

    sections.extend(
        [
            "",
            "## Harte Regeln: Shot-Timing",
            _shot_timing_rules_markdown(
                shot_min_sec=shot_min_sec,
                shot_max_sec=shot_max_sec,
                audio_offset_sec=audio_offset_sec,
            ),
        ]
    )

    correction = correction_instructions.strip()
    if correction:
        sections.extend(["", correction])

    if section_outro_sec > 0.05:
        outro_rows = [
            [OUTRO_BEAT_ID, f"{section_outro_sec:.1f}", "Ausklingen", "Establishing/Luftaufnahme"],
        ]
        sections.extend(
            [
                "",
                "## Zusatz: Ordner-Ausklingen",
                _markdown_table(["beat_id", "duration_sec", "motif", "asset_typ"], outro_rows),
                (
                    f"- Wenn Dauer > {shot_max_sec:.1f}s: mehrere parts (je max. {shot_max_sec:.1f}s)."
                ),
                "- Passung bewerten: sehr_gut, gut, mittel, unpassend.",
            ]
        )

    instructions = extra_instructions.strip()
    if instructions:
        sections.extend(["", "## Zusätzliche Editor-Anweisungen", instructions])

    sections.extend(
        [
            "",
            "## Planungs-Hinweise",
            "- Wähle pro part das inhaltlich passendste Asset aus der gesamten Liste.",
            "- Mehrere Motive im Segment → mehrere parts.",
            "- `match_quality`: sehr_gut | gut | mittel | unpassend.",
            "- Narration + unpassend: `asset_path` = null (Platzhalter wird lokal ergänzt).",
            "- Outro + unpassend: trotzdem bestes ruhiges Asset, match_quality = unpassend.",
            "",
            "## Ausgabeformat",
            "Antworte **nur** als JSON (kein Markdown, kein Fließtext):",
            "```json",
            (
                '{"beats":[{"beat_id":"beat_001","parts":[{"text":"...","motif":"...",'
                '"asset_path":"exakter path oder null",'
                '"match_quality":"sehr_gut|gut|mittel|unpassend"}]}]}'
            ),
            "```",
            "- `beat_id` muss exakt einem Segment entsprechen"
            + (f' oder `{OUTRO_BEAT_ID}` für das Ausklingen.' if section_outro_sec > 0.05 else "."),
            "- `asset_path` muss exakt einem `path` aus der Asset-Tabelle entsprechen oder null sein.",
        ]
    )
    return "\n".join(sections)


def build_plan_folder_free_prompt(
    *,
    folder_name: str,
    segment_lines: str,
    asset_lines: str,
    language: str,
    rule_text: str,
    correction_instructions: str = "",
) -> str:
    """Freier Schnittplan: nur Segmente, Assets und der Gemini-Freitext aus den Regeln."""
    rule_clause = rule_text.strip() or "(no additional rule)"
    sections = [
        (
            f'Create a timeline for "{folder_name}" with the following scenes/voice over '
            f'segments and follow this rule "{rule_clause}".'
        ),
        "",
        f"Language: {language}.",
        "",
        "## Voice-over segments",
        segment_lines or "_No segments._",
        "",
        "## Available assets",
        asset_lines or "_No assets._",
    ]
    correction = correction_instructions.strip()
    if correction:
        sections.extend(["", correction])
    sections.extend(
        [
            "",
            "## Planning notes",
            "- Choose the best matching asset per part from the full asset list.",
            "- Multiple motifs in one segment → multiple parts.",
            "- `match_quality`: sehr_gut | gut | mittel | unpassend.",
            "- If no suitable asset: `asset_path` = null.",
            "",
            "## Output format",
            "Respond **only** as JSON (no markdown, no prose):",
            "```json",
            (
                '{"beats":[{"beat_id":"beat_001","parts":[{"text":"...","motif":"...",'
                '"asset_path":"exact path or null",'
                '"match_quality":"sehr_gut|gut|mittel|unpassend"}]}]}'
            ),
            "```",
            "- `beat_id` must match a segment beat_id exactly.",
            "- `asset_path` must match a `path` from the asset table exactly or be null.",
        ]
    )
    return "\n".join(sections)


def summarize_beats_plan_for_retry(beats_plan: dict[str, list[dict[str, Any]]]) -> str:
    """Kompakte Zusammenfassung des abgelehnten Plans für einen Korrektur-Lauf."""
    from otio_app.defaults import OUTRO_BEAT_ID

    lines: list[str] = []
    for beat_id in sorted(beats_plan, key=lambda value: (value != OUTRO_BEAT_ID, value)):
        parts = beats_plan.get(beat_id, [])
        if beat_id == OUTRO_BEAT_ID:
            lines.append(f"- {beat_id}: Ausklingen, {len(parts)} part(s)")
            continue
        qualities = [str(part.get("match_quality") or "?") for part in parts]
        lines.append(f"- {beat_id}: {len(parts)} part(s), Passung: {', '.join(qualities)}")
    return "\n".join(lines) if lines else "- (kein vorheriger Plan)"


def compact_beats_plan_json_for_retry(beats_plan: dict[str, list[dict[str, Any]]]) -> str:
    """Struktur des abgelehnten Plans — ohne Asset-Pfade, Text gekürzt."""
    from otio_app.defaults import OUTRO_BEAT_ID

    beats: list[dict[str, Any]] = []
    for beat_id in sorted(beats_plan, key=lambda value: (value != OUTRO_BEAT_ID, value)):
        parts = beats_plan.get(beat_id, [])
        beats.append(
            {
                "beat_id": beat_id,
                "parts": [
                    {
                        "text": str(part.get("text", ""))[:80],
                        "motif": str(part.get("motif", ""))[:60],
                        "match_quality": part.get("match_quality"),
                    }
                    for part in parts[:12]
                ],
            }
        )
    return json.dumps({"beats": beats}, ensure_ascii=False, indent=2)


def _structured_correction_hints(
    structured_errors: list[Any],
) -> list[str]:
    from otio_app.services.edit_plan_validator import PlanValidationError

    hints: list[str] = []
    asset_violations: list[PlanValidationError] = []
    for raw in structured_errors:
        error = raw if isinstance(raw, PlanValidationError) else PlanValidationError.from_dict(raw)
        if error.type == "ASSET_USAGE_LIMIT_EXCEEDED":
            asset_violations.append(error)
        elif error.type == "ASSET_REUSE_DISTANCE_TOO_SHORT":
            hints.append(
                "The previous plan violates asset reuse distance rules.\n"
                f"- asset_id `{error.asset_id}` reused too soon "
                f"(distance {error.actual_distance_shots} shots, required "
                f"{error.required_distance_shots}).\n"
                f"- Previous item: {error.previous_item_id}; current item: {error.current_item_id}.\n"
                "Required fix: choose a different asset_id or insert supplement_request."
            )
        elif error.type == "SHOT_TOO_SHORT":
            hints.append(
                f"SHOT_TOO_SHORT: {error.timeline_item_id} is {error.duration_sec:.1f}s "
                f"(min {error.min_sec:.1f}s) in segment {error.segment_id or error.beat_id or '?'}. "
                "Split into more parts or merge text so each shot is at least shot_min_sec."
            )
        elif error.type == "SHOT_TOO_LONG":
            hints.append(
                f"SHOT_TOO_LONG: {error.timeline_item_id} is {error.duration_sec:.1f}s "
                f"(max {error.max_sec:.1f}s) in segment {error.segment_id or error.beat_id or '?'}. "
                "Return more parts for this segment (see allowed_parts_min/max)."
            )
        elif error.type == "INSUFFICIENT_PARTS":
            hints.append(
                f"INSUFFICIENT_PARTS: segment {error.segment_id or error.beat_id} returned "
                f"{error.actual_parts} part(s), but allowed_parts_min is {error.allowed_parts_min} "
                f"and allowed_parts_max is {error.allowed_parts_max}. "
                "Return more parts so no single shot exceeds shot_max_sec."
            )

    if asset_violations:
        lines = [
            "The previous plan violates asset usage rules.",
            "",
            "Violations:",
        ]
        for error in asset_violations:
            item_ids = ", ".join(error.timeline_item_ids or [])
            lines.append(
                f'- asset_id "{error.asset_id}" used {error.usage_count} times '
                f"(max_asset_usage is {error.max_allowed})."
            )
            if item_ids:
                lines.append(f"  Timeline items: {item_ids}")
        lines.extend(
            [
                "",
                "Required fix:",
                "- Keep at most max_asset_usage usages per asset_id across the entire plan.",
                "- Replace duplicate occurrences with different suitable assets.",
                "- If no suitable asset exists, create/keep a supplement_request for that beat.",
                "- Do not reuse the same asset_id again.",
            ]
        )
        hints.insert(0, "\n".join(lines))

    return hints


def build_plan_folder_correction_instructions(
    *,
    errors: list[str],
    previous_beats: dict[str, list[dict[str, Any]]],
    attempt: int,
    max_attempts: int,
    file_duration_sec: float | None,
    shot_min_sec: float,
    shot_max_sec: float,
    structured_errors: list[Any] | None = None,
) -> str:
    """Korrektur-Block für einen erneuten Gemini-Lauf nach Plan-Validierung."""
    from otio_app.services.edit_plan_validator import (
        PlanValidationError,
        plan_validation_error_to_message,
    )

    message_lines = list(errors)
    if structured_errors:
        for raw in structured_errors:
            if isinstance(raw, PlanValidationError):
                message_lines.append(plan_validation_error_to_message(raw))
            elif isinstance(raw, dict):
                message_lines.append(plan_validation_error_to_message(PlanValidationError.from_dict(raw)))
            else:
                message_lines.append(str(raw))

    error_lines = "\n".join(f"- {error}" for error in message_lines) or "- (unbekannt)"
    duration_hint = (
        f"{file_duration_sec:.2f}s"
        if file_duration_sec is not None and file_duration_sec > 0
        else "unbekannt"
    )
    structured_hints = _structured_correction_hints(structured_errors or [])
    sections = [
        f"## Korrektur — Versuch {attempt} von {max_attempts}",
        "",
        "Die lokale Plan-Validierung hat den vorherigen Plan abgelehnt.",
        "Erstelle einen **neuen vollständigen Plan** (komplettes JSON mit allen beats) — kein Patch.",
        "",
        "## Validierungsfehler",
        error_lines,
        "",
        "## Kontext",
        _markdown_table(
            ["parameter", "wert"],
            [
                ["voiceover_duration_ffprobe", duration_hint],
                ["shot_min_sec", f"{shot_min_sec:.1f}"],
                ["shot_max_sec", f"{shot_max_sec:.1f}"],
            ],
        ),
        "",
        "## Abgelehnter Plan (Zusammenfassung)",
        summarize_beats_plan_for_retry(previous_beats),
        "",
        "## Abgelehnter Plan (Struktur, gekürzt)",
        f"```json\n{compact_beats_plan_json_for_retry(previous_beats)}\n```",
        "",
        "## Korrektur-Hinweise",
        f"- Ziel pro part/shot: {shot_min_sec:.1f}s–{shot_max_sec:.1f}s (siehe parts_min/max).",
        "- Weniger, längere parts nur wenn parts_max es erlaubt.",
        "- Mehr parts bei SHOT_TOO_LONG oder INSUFFICIENT_PARTS.",
        "- Bei ASSET_USAGE_LIMIT_EXCEEDED: anderes asset_id oder asset_path null — nie duplizieren.",
        "- Inhaltliche Passung möglichst beibehalten.",
        "- Nicht dieselbe fehlerhafte Aufteilung wiederholen.",
    ]
    if structured_hints:
        sections.extend(["", "## Konkrete Korrekturen", *structured_hints])
    return "\n".join(sections)


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
