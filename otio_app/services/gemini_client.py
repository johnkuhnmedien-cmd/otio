"""Gemini-Client — API-Schlüssel nur aus Umgebungsvariablen."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
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

ASSET_DESCRIPTION_PROMPT_VERSION = "asset_v3_editorial_r3"
_CAPTION_MAX_CHARS = 180
_ALLOWED_MOTION = frozenset(
    {"static", "pan", "tilt", "tracking", "drone", "handheld", "zoom", "unknown"}
)
_ALLOWED_MOTION_DIRECTION = frozenset(
    {
        "left_to_right",
        "right_to_left",
        "forward",
        "backward",
        "up",
        "down",
        "none",
        "unknown",
    }
)
_ALLOWED_FRAMING = frozenset({"close", "medium", "wide", "aerial", "pov"})
_ALLOWED_SHOT_SCALE = frozenset(
    {"detail", "close", "medium", "wide", "extreme_wide", "unknown"}
)
_ALLOWED_COLOR_TEMPERATURE = frozenset(
    {"warm", "neutral", "cool", "mixed", "unknown"}
)
_ALLOWED_DEFECT_TYPE = frozenset(
    {
        "watermark",
        "logo",
        "blur",
        "shake",
        "black_frame",
        "compression",
        "exposure",
        "obstruction",
        "other",
    }
)


class GeminiNotConfiguredError(RuntimeError):
    """GEMINI_API_KEY fehlt."""


#: Vorübergehende Serverzustände — ein erneuter Versuch ist sinnvoll.
_TRANSIENT_HTTP_CODES = frozenset({408, 429, 500, 502, 503, 504})
_TRANSIENT_STATUS_NAMES = frozenset(
    {
        "UNAVAILABLE",
        "RESOURCE_EXHAUSTED",
        "DEADLINE_EXCEEDED",
        "INTERNAL",
        "ABORTED",
    }
)


def is_transient_api_error(exc: BaseException) -> bool:
    """True bei Fehlern, die ein Wiederholen rechtfertigen.

    Ein 503 „Deadline expired" ist kein Befund über die Mediendatei. Ohne diese
    Unterscheidung landet er als dauerhafter Analysefehler im Cache, der Ordner
    gilt nicht mehr als vollständig — und sein Inventar wird entfernt.
    """
    code = getattr(exc, "code", None)
    if isinstance(code, int) and code in _TRANSIENT_HTTP_CODES:
        return True
    status = str(getattr(exc, "status", "") or "").strip().upper()
    if status in _TRANSIENT_STATUS_NAMES:
        return True
    if isinstance(exc, (TimeoutError, ConnectionError)):
        return True
    text = str(exc).upper()
    if any(name in text for name in _TRANSIENT_STATUS_NAMES):
        return True
    return any(f" {http_code} " in f" {text} " for http_code in ("429", "503", "504"))


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


def _get_client(*, timeout_ms: int | None = None):
    api_key = get_api_key("GEMINI_API_KEY")
    if not api_key:
        raise GeminiNotConfiguredError(
            "GEMINI_API_KEY ist nicht gesetzt. "
            "Bitte unter 🔑 API-Schlüssel oder in .env eintragen."
        )
    from google import genai

    if timeout_ms is None:
        return genai.Client(api_key=api_key)
    from google.genai import types

    return genai.Client(
        api_key=api_key,
        http_options=types.HttpOptions(timeout=int(timeout_ms)),
    )


def _extract_json(text: str) -> Any:
    """Parse JSON from an LLM reply.

    Gemini often puts literal newlines/tabs inside long string fields
    (YouTube descriptions). ``json.loads`` rejects those by default
    (``Invalid control character``); ``strict=False`` keeps the text.
    """
    cleaned = (text or "").strip()
    fence_match = re.search(r"```(?:json)?\s*(.*?)\s*```", cleaned, re.DOTALL)
    if fence_match:
        cleaned = fence_match.group(1).strip()
    try:
        return json.loads(cleaned, strict=False)
    except json.JSONDecodeError:
        match = re.search(r"(\{.*\}|\[.*\])", cleaned, re.DOTALL)
        if match is None:
            raise
        return json.loads(match.group(1), strict=False)


@dataclass(frozen=True)
class MediaFrameAnalysis:
    """Strukturierte Asset-Frame-Analyse (asset_v3_editorial_r3)."""

    description: str = ""
    caption: str = ""
    content_tags: tuple[str, ...] = ()
    motion: str = "unknown"
    framing: str = "medium"
    people: bool = False
    people_action: Optional[str] = None
    defects: Optional[str] = None
    motion_profile: Optional[Any] = None
    framing_profile: Optional[Any] = None
    look_profile: Optional[Any] = None
    quality_profile: Optional[Any] = None
    defect_items: tuple[Any, ...] = ()
    confidence: Optional[float] = None
    parse_ok: bool = False
    raw_response: str = ""

    @classmethod
    def successful(
        cls,
        description: str,
        *,
        caption: str | None = None,
        content_tags: list[str] | tuple[str, ...] | None = None,
        motion: str = "static",
        framing: str = "wide",
        people: bool = False,
        people_action: Optional[str] = None,
        defects: Optional[str] = None,
        confidence: float = 0.85,
        raw_response: str = "",
    ) -> "MediaFrameAnalysis":
        """Test-/Mock-Hilfsfactory für eine gültige v3-Analyse (parse_ok=True).

        Nicht für Produktions-API-Antworten verwenden — dort ausschließlich
        ``parse_media_frame_analysis`` / ``analyze_media_from_frames``.
        """
        from otio_app.analysis_models import (
            AssetDefect,
            AssetFramingProfile,
            AssetLookProfile,
            AssetMotionProfile,
            AssetQualityProfile,
        )

        caption_text = (caption if caption is not None else description)[:_CAPTION_MAX_CHARS]
        tags = tuple(
            dict.fromkeys(
                tag.strip()
                for tag in (content_tags or ("landscape", "daylight", "exterior"))
                if str(tag).strip()
            )
        )
        motion_profile = AssetMotionProfile(
            type=motion,
            intensity=None,
            direction="unknown",
            confidence=None,
        )
        framing_profile = AssetFramingProfile(type=framing, shot_scale=framing if framing in _ALLOWED_SHOT_SCALE else "unknown")
        look_profile = AssetLookProfile(
            brightness=None,
            contrast=None,
            saturation=None,
            color_temperature="unknown",
            dominant_colors=[],
        )
        quality_profile = AssetQualityProfile(
            technical_quality=80,
            composition_quality=80,
            visual_appeal=80,
            subject_clarity=80,
            hero_potential=70,
            defect_severity=0,
        )
        defect_items: tuple[Any, ...] = ()
        if defects:
            defect_items = (AssetDefect(type="other", severity=50, note=str(defects)),)
        return cls(
            description=description.strip(),
            caption=caption_text.strip(),
            content_tags=tags,
            motion=motion_profile.type,
            framing=framing_profile.type,
            people=people,
            people_action=people_action,
            defects=defects,
            motion_profile=motion_profile,
            framing_profile=framing_profile,
            look_profile=look_profile,
            quality_profile=quality_profile,
            defect_items=defect_items,
            confidence=confidence,
            parse_ok=True,
            raw_response=raw_response,
        )


def _chapter_location_hint(folder_name: str) -> str:
    """Kapitel-/Ordnername als einzeiliger Orts-Hinweis, ohne Prompt-Umbrüche."""
    return " ".join(str(folder_name or "").split())[:120]


def build_asset_frame_analysis_prompt(
    media_name: str, folder_name: str, language: str
) -> str:
    """Baut den Asset-Frame-Prompt (v3 editorial r3).

    ``folder_name`` ist der Kapitelort — ein Identifikationshinweis, kein Beweis.
    ``media_name`` bleibt absichtlich draußen: Dateinamen sind keine visuelle Evidenz.
    """
    del media_name
    chapter = _chapter_location_hint(folder_name)
    location_line = (
        f"Kapitelort (Hinweis, kein Beweis): {chapter}\n"
        if chapter
        else "Kapitelort: nicht angegeben.\n"
    )
    return (
        "Du analysierst die bereitgestellten Frames einer Mediendatei. "
        f"Sprache für Freitext: {language}.\n"
        f"{location_line}\n"
        "WICHTIG — Frames zuerst, Kapitelort als Identifikationshilfe:\n"
        "- Beschreibe nur, was in den Frames sichtbar ist.\n"
        "- Dateiname und Pfad sind keine visuelle Evidenz. Niemals Motiv, Person, "
        "Ereignis oder Inhalt aus dem Dateinamen ableiten.\n"
        "- Der Kapitelort ist Kontext: nutze ihn, um sichtbare Orte, Bauwerke und "
        "Wahrzeichen mit dem gebräuchlichen Eigennamen zu benennen, wenn die Frames "
        "dazu passen.\n"
        "- Mehrere ähnliche Motive am selben Ort unterscheiden (welche Festung, "
        "welcher Tempel, welches Schloss). Nicht jedes passende Motiv auf das "
        "berühmteste Wahrzeichen des Kapitels legen.\n"
        "- Wenn die Frames kein bestimmtes Wahrzeichen stützen: visuell genau "
        "beschreiben und den Kapitelort nur als Lage nennen — keinen berühmten "
        "Namen raten.\n"
        "- Nichts erfinden, was nicht sichtbar ist.\n\n"
        "Antworte NUR mit einem JSON-Objekt in exakt dieser Struktur.\n"
        "Die Platzhalter <string>, <int_0_100>, <float_0_1>, <enum_...> und null sind "
        "Formhinweise — keine Beispielwerte zum Kopieren und keine Score-Anker:\n\n"
        "{\n"
        '  "description": "<string>",\n'
        '  "caption": "<string>",\n'
        '  "content_tags": ["<string>"],\n'
        '  "motion": {\n'
        '    "type": "<enum_motion_type>",\n'
        '    "intensity": "<int_0_100_or_null>",\n'
        '    "direction": "<enum_motion_direction>",\n'
        '    "confidence": "<float_0_1_or_null>"\n'
        "  },\n"
        '  "framing": {\n'
        '    "type": "<enum_framing_type>",\n'
        '    "shot_scale": "<enum_shot_scale>"\n'
        "  },\n"
        '  "look": {\n'
        '    "brightness": "<int_0_100_or_null>",\n'
        '    "contrast": "<int_0_100_or_null>",\n'
        '    "saturation": "<int_0_100_or_null>",\n'
        '    "color_temperature": "<enum_color_temperature>",\n'
        '    "dominant_colors": ["<string>"]\n'
        "  },\n"
        '  "people": false,\n'
        '  "people_action": null,\n'
        '  "quality": {\n'
        '    "technical_quality": "<int_0_100>",\n'
        '    "composition_quality": "<int_0_100>",\n'
        '    "visual_appeal": "<int_0_100>",\n'
        '    "subject_clarity": "<int_0_100>",\n'
        '    "hero_potential": "<int_0_100>",\n'
        '    "defect_severity": "<int_0_100>"\n'
        "  },\n"
        '  "defects": [],\n'
        '  "confidence": "<float_0_1>"\n'
        "}\n\n"
        "Gemeinsame Score-Skala (0–100) für quality.*:\n"
        "- 0–19: praktisch unbrauchbar oder massiv beeinträchtigt\n"
        "- 20–39: deutlich problematisch\n"
        "- 40–59: durchschnittlich, eingeschränkt oder nur bedingt brauchbar\n"
        "- 60–74: solide bis gut\n"
        "- 75–89: sehr gut\n"
        "- 90–100: außergewöhnlich; selten vergeben und nur bei klarer visueller Evidenz\n"
        "Score-Regeln:\n"
        "- Nicht automatisch bei 80 beginnen und keine Beispielzahlen kopieren.\n"
        "- Jeden Score zuerst qualitativ einordnen, danach die Zahl wählen.\n"
        "- Werte >=90 nur für außergewöhnliches Material.\n"
        "- Keine künstlichen Unterschiede erfinden, wenn zwei Assets ähnlich wirken.\n"
        "- Auflösung, Dateigröße oder vermutete Bitrate sind KEINE sichtbare "
        "technische Qualität; nur die gelieferten Frames bewerten.\n\n"
        "quality-Felder getrennt:\n"
        "- technical_quality: sichtbare Schärfe, Belichtung, Artefakte, Stabilität, "
        "technische Sauberkeit — nicht Schönheit oder Motivwert.\n"
        "- composition_quality: Bildaufbau, Balance, Ebenen, Blickführung, "
        "Motivplatzierung, nutzbarer Raum.\n"
        "- visual_appeal: ästhetische Wirkung von Licht, Farbe, Atmosphäre und Motiv.\n"
        "- subject_clarity: wie eindeutig und gut lesbar das zentrale Motiv ist.\n"
        "- hero_potential: Eignung als visuell prägender Vollbild-Shot, Opener oder "
        "Höhepunkt — keine semantische Passung zu einem späteren Voice-over.\n"
        "- defect_severity: 0 wenn kein sichtbarer Defekt; sonst konsistent mit dem "
        "schwersten defects-Eintrag.\n\n"
        "Freitextfelder (unterschiedliche Aufgaben):\n"
        "- description: 2–3 sachliche Sätze zu räumlichem Aufbau, zentralen Motiven, "
        "Licht, Atmosphäre und sichtbarer Handlung. Eigennamen von Ort und Wahrzeichen "
        "nennen, wenn Kapitelort und Frames sie stützen. Keine Taglisten wiederholen, "
        "keine unsichtbaren Details erfinden.\n"
        "- caption: ein kurzer Retrieval-Satz, maximal 180 Zeichen; primäres Motiv mit "
        "spezifischer Identität (welches Bauwerk, welcher Ort) + Perspektive + "
        "wichtigste sichtbare Handlung; keine Werbesprache, keine Dateimetadaten.\n"
        "- content_tags: 3–8 kurze Suchbegriffe; keine vollständigen Sätze; "
        "Orts- und Wahrzeichennamen erlaubt, wenn Kapitelort und Frames sie stützen; "
        "keine redundanten Singular-/Plural- oder Synonymvarianten; keine "
        "Qualitätsurteile (schön, hochwertig, cinematisch).\n\n"
        "framing.type = Perspektive / dominanter Aufnahmetyp "
        "(close|medium|wide|aerial|pov):\n"
        "- aerial: eindeutig erhöhte Luft-/Drohnenperspektive\n"
        "- pov: sichtbare Ich-/Fahrzeug-/Körperperspektive "
        "(z. B. Kajakbug im Vordergrund)\n"
        "- close: bodennahe/normale Perspektive mit engem Motiv\n"
        "- medium: bodennahe/normale Perspektive mit mittlerer Motivdistanz\n"
        "- wide: bodennahe/normale Perspektive mit weiter Gesamtansicht\n"
        "Priorität für framing.type:\n"
        "1) eindeutig aerial → aerial\n"
        "2) sonst eindeutig subjektive POV → pov\n"
        "3) sonst close|medium|wide nach Bildausschnitt\n"
        "framing.shot_scale unabhängig bestimmen "
        "(detail|close|medium|wide|extreme_wide|unknown).\n"
        "Beispiele (nur Orientierung, nicht kopieren):\n"
        "- Drohnenansicht eines Dorfs: type=aerial, shot_scale=wide oder extreme_wide\n"
        "- Kajakbug aus Fahrersicht: type=pov, shot_scale=wide\n"
        "- bildfüllende Hausfassade: type=medium oder close, "
        "shot_scale=medium oder close\n"
        "- normale Landschaft: type=wide, shot_scale=wide oder extreme_wide\n\n"
        "motion beschreibt NUR Kamerabewegung, nicht Motivbewegung.\n"
        "- motion.type: static|pan|tilt|tracking|drone|handheld|zoom|unknown\n"
        "- motion.direction: left_to_right|right_to_left|forward|backward|up|down|"
        "none|unknown\n"
        "- Wasserfall, fahrendes Auto oder wehende Bäume sind keine Kamerabewegung.\n"
        "- drone nur, wenn sowohl Luftperspektive als auch eine Veränderung zwischen "
        "den Frames eine Drohnenbewegung nahelegt.\n"
        "- Statische Luftaufnahme: framing.type=aerial und motion.type=static oder "
        "unknown — nicht automatisch motion.type=drone.\n"
        "- Bei wenigen Standframes im Zweifel unknown; intensity/confidence dann null.\n\n"
        "look.color_temperature: warm|neutral|cool|mixed|unknown. "
        "Look-Zahlenwerte dürfen null sein, wenn unsicher.\n"
        "- people: true, wenn Personen erkennbar sind; people_action dann kurz, sonst null.\n"
        "- defects: Liste von Objekten "
        '{ "type": "watermark|logo|blur|shake|black_frame|compression|exposure|'
        'obstruction|other", "severity": 0-100, "note": "..." }. '
        "Leere Liste wenn keine Defekte.\n"
        "- Lensflare ist nicht automatisch ein Defekt; nur als exposure/obstruction "
        "werten, wenn Bildinformation wirklich störend verdeckt oder technisch "
        "unbrauchbar wird.\n\n"
        "confidence (0.0–1.0) für die Verlässlichkeit der gesamten Frame-Analyse:\n"
        "- 0.90–1.00: nahezu alle wesentlichen Aussagen eindeutig sichtbar; selten\n"
        "- 0.70–0.89: Motiv sicher, einzelne Eigenschaften oder zeitliche Aussagen unsicher\n"
        "- 0.50–0.69: mehrdeutige Frames oder wichtige Unsicherheiten\n"
        "- unter 0.50: Analyse nur eingeschränkt belastbar\n"
        "- 0.95 nicht als Standard verwenden.\n"
        "- Unsichere motion-Aussage muss die Gesamt-Confidence nicht zerstören, "
        "darf aber keine rundum nahezu sichere Analyse vortäuschen.\n"
        "- Confidence nicht künstlich variieren; sie soll die Beleglage ausdrücken.\n"
        "- Unbekannte Werte als null beziehungsweise unknown."
    )


def _parse_fail(raw: str) -> MediaFrameAnalysis:
    return MediaFrameAnalysis(parse_ok=False, raw_response=raw)


def _as_optional_score_0_100(value: Any) -> Optional[int]:
    if value is None:
        return None
    if isinstance(value, bool):
        raise ValueError("score bool")
    if isinstance(value, (int, float)):
        number = int(value)
    elif isinstance(value, str) and value.strip():
        number = int(float(value.strip()))
    else:
        raise ValueError("score type")
    if number < 0 or number > 100:
        raise ValueError("score range")
    return number


def _as_required_score_0_100(value: Any) -> int:
    number = _as_optional_score_0_100(value)
    if number is None:
        raise ValueError("score missing")
    return number


def _as_optional_confidence(value: Any) -> Optional[float]:
    if value is None:
        return None
    if isinstance(value, bool):
        raise ValueError("confidence bool")
    if isinstance(value, (int, float)):
        number = float(value)
    elif isinstance(value, str) and value.strip():
        number = float(value.strip())
    else:
        raise ValueError("confidence type")
    if number < 0.0 or number > 1.0:
        raise ValueError("confidence range")
    return number


def _normalize_caption(value: Any) -> str:
    text = str(value or "").strip()
    if len(text) <= _CAPTION_MAX_CHARS:
        return text
    return text[:_CAPTION_MAX_CHARS].rstrip()


def _normalize_tags(value: Any) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, list):
        raise ValueError("tags type")
    cleaned: list[str] = []
    seen: set[str] = set()
    for item in value:
        tag = str(item or "").strip()
        if not tag:
            continue
        key = tag.casefold()
        if key in seen:
            continue
        seen.add(key)
        cleaned.append(tag)
    return tuple(cleaned)


def _legacy_defects_summary(items: list[Any]) -> Optional[str]:
    if not items:
        return None
    parts: list[str] = []
    for item in items:
        label = item.type
        if item.note:
            parts.append(f"{label}: {item.note}")
        else:
            parts.append(f"{label} ({item.severity})")
    return "; ".join(parts)


def parse_media_frame_analysis(text: str) -> MediaFrameAnalysis:
    """Parst Gemini-v3-Antwort strikt; bei Fehler parse_ok=False + raw_response."""
    from otio_app.analysis_models import (
        AssetDefect,
        AssetFramingProfile,
        AssetLookProfile,
        AssetMotionProfile,
        AssetQualityProfile,
    )

    raw = (text or "").strip()
    if not raw:
        return _parse_fail("")

    try:
        try:
            payload = _extract_json(raw)
        except (json.JSONDecodeError, TypeError, ValueError):
            match = re.search(r"\{.*\}", raw, re.DOTALL)
            if not match:
                return _parse_fail(raw)
            payload = json.loads(match.group(0))

        if not isinstance(payload, dict):
            return _parse_fail(raw)

        description = str(payload.get("description") or "").strip()
        caption = _normalize_caption(payload.get("caption"))
        if not description or not caption:
            return _parse_fail(raw)

        motion_raw = payload.get("motion")
        framing_raw = payload.get("framing")
        quality_raw = payload.get("quality")
        if not isinstance(motion_raw, dict):
            return _parse_fail(raw)
        if not isinstance(framing_raw, dict):
            return _parse_fail(raw)
        if not isinstance(quality_raw, dict):
            return _parse_fail(raw)

        if "confidence" not in payload:
            return _parse_fail(raw)
        confidence = _as_optional_confidence(payload.get("confidence"))
        if confidence is None:
            return _parse_fail(raw)

        motion_type = str(motion_raw.get("type") or "").strip().lower()
        if motion_type not in _ALLOWED_MOTION:
            return _parse_fail(raw)
        motion_direction = str(motion_raw.get("direction") or "unknown").strip().lower()
        if motion_direction not in _ALLOWED_MOTION_DIRECTION:
            return _parse_fail(raw)
        motion_intensity = _as_optional_score_0_100(motion_raw.get("intensity"))
        motion_confidence = _as_optional_confidence(motion_raw.get("confidence"))

        framing_type = str(framing_raw.get("type") or "").strip().lower()
        if framing_type not in _ALLOWED_FRAMING:
            return _parse_fail(raw)
        shot_scale = str(framing_raw.get("shot_scale") or "unknown").strip().lower()
        if shot_scale not in _ALLOWED_SHOT_SCALE:
            return _parse_fail(raw)

        look_raw = payload.get("look")
        if look_raw is None:
            look_profile = AssetLookProfile()
        elif not isinstance(look_raw, dict):
            return _parse_fail(raw)
        else:
            color_temperature = str(
                look_raw.get("color_temperature") or "unknown"
            ).strip().lower()
            if color_temperature not in _ALLOWED_COLOR_TEMPERATURE:
                return _parse_fail(raw)
            dominant = look_raw.get("dominant_colors")
            if dominant is None:
                colors: list[str] = []
            elif not isinstance(dominant, list):
                return _parse_fail(raw)
            else:
                colors = [str(item).strip() for item in dominant if str(item).strip()]
            look_profile = AssetLookProfile(
                brightness=_as_optional_score_0_100(look_raw.get("brightness")),
                contrast=_as_optional_score_0_100(look_raw.get("contrast")),
                saturation=_as_optional_score_0_100(look_raw.get("saturation")),
                color_temperature=color_temperature,
                dominant_colors=colors,
            )

        quality_profile = AssetQualityProfile(
            technical_quality=_as_required_score_0_100(quality_raw.get("technical_quality")),
            composition_quality=_as_required_score_0_100(
                quality_raw.get("composition_quality")
            ),
            visual_appeal=_as_required_score_0_100(quality_raw.get("visual_appeal")),
            subject_clarity=_as_required_score_0_100(quality_raw.get("subject_clarity")),
            hero_potential=_as_required_score_0_100(quality_raw.get("hero_potential")),
            defect_severity=_as_required_score_0_100(quality_raw.get("defect_severity")),
        )

        people_raw = payload.get("people", False)
        if isinstance(people_raw, bool):
            people = people_raw
        elif isinstance(people_raw, str):
            people = people_raw.strip().lower() in {"true", "1", "yes", "ja"}
        else:
            people = bool(people_raw)

        people_action_raw = payload.get("people_action")
        if people:
            people_action = (
                str(people_action_raw).strip()
                if people_action_raw not in (None, "")
                else None
            )
        else:
            people_action = None

        defects_raw = payload.get("defects", [])
        if defects_raw is None:
            defects_raw = []
        if not isinstance(defects_raw, list):
            return _parse_fail(raw)
        defect_items: list[AssetDefect] = []
        for item in defects_raw:
            if not isinstance(item, dict):
                return _parse_fail(raw)
            defect_type = str(item.get("type") or "").strip().lower()
            if defect_type not in _ALLOWED_DEFECT_TYPE:
                return _parse_fail(raw)
            severity = _as_required_score_0_100(item.get("severity"))
            note = str(item.get("note") or "").strip()
            defect_items.append(
                AssetDefect(type=defect_type, severity=severity, note=note)
            )

        content_tags = _normalize_tags(payload.get("content_tags"))
        motion_profile = AssetMotionProfile(
            type=motion_type,
            intensity=motion_intensity,
            direction=motion_direction,
            confidence=motion_confidence,
        )
        framing_profile = AssetFramingProfile(type=framing_type, shot_scale=shot_scale)
        legacy_defects = _legacy_defects_summary(defect_items)

        return MediaFrameAnalysis(
            description=description,
            caption=caption,
            content_tags=content_tags,
            motion=motion_profile.type,
            framing=framing_profile.type,
            people=people,
            people_action=people_action,
            defects=legacy_defects,
            motion_profile=motion_profile,
            framing_profile=framing_profile,
            look_profile=look_profile,
            quality_profile=quality_profile,
            defect_items=tuple(defect_items),
            confidence=confidence,
            parse_ok=True,
            raw_response=raw,
        )
    except Exception:  # noqa: BLE001 — Parser darf die App nie abstürzen lassen
        return _parse_fail(raw)


def generate_text_from_image_frames(
    prompt: str,
    frame_paths: list[Path],
    *,
    model: Optional[str] = None,
) -> str:
    """Kurzer Vision-Aufruf über JPEG-Frames — gleiches Asset-Modell, nicht Cut."""
    if not frame_paths:
        return ""

    client = _get_client()
    from google.genai import types

    parts: list[types.Part] = [types.Part.from_text(text=prompt)]
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


def analyze_media_from_frames(
    media_name: str,
    folder_name: str,
    frame_paths: list[Path],
    language: str,
    *,
    model: Optional[str] = None,
) -> MediaFrameAnalysis:
    """Frame-Analyse → strukturierte v3-Felder inkl. Freitext-description.

    ``model`` sollte die bereits aufgelöste Modell-ID sein; ``resolve_gemini_model``
    ist für gültige IDs idempotent.
    """
    if not frame_paths:
        return MediaFrameAnalysis(
            description="",
            parse_ok=False,
            raw_response="",
        )

    client = _get_client()
    from google.genai import types

    resolved_model = resolve_gemini_model(model)
    parts: list[types.Part] = [
        types.Part.from_text(
            text=build_asset_frame_analysis_prompt(media_name, folder_name, language)
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
        model=resolved_model,
        contents=[types.Content(role="user", parts=parts)],
    )
    return parse_media_frame_analysis((response.text or "").strip())


def describe_media_from_frames(
    media_name: str,
    folder_name: str,
    frame_paths: list[Path],
    language: str,
    *,
    model: Optional[str] = None,
) -> str:
    """Kompatibel: liefert nur den Freitext ``description``."""
    return analyze_media_from_frames(
        media_name,
        folder_name,
        frame_paths,
        language,
        model=model,
    ).description


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


def describe_and_validate_supplement_asset(
    *,
    media_name: str,
    folder_name: str,
    frame_paths: list[Path],
    passage_text: str,
    visual_requirement: str,
    location_name: str = "",
    must_show: Optional[list[str]] = None,
    avoid_showing: Optional[list[str]] = None,
    language: str = "de",
    model: Optional[str] = None,
) -> dict[str, Any]:
    """Kombinierter Gemini-Aufruf (EIN Request statt zwei): beschreibt die
    übergebenen Frames UND beurteilt im selben Aufruf, ob das gezeigte
    Material zum Satz/zur visuellen Anforderung passt.

    Ergänzt describe_media_from_frames()/validate_supplement_asset_match()
    rein additiv — beide bestehenden Zwei-Schritt-Funktionen bleiben für
    ihre bisherigen Aufrufer (Produktions-Pipeline) unverändert. Gedacht für
    den Cut-Plan-Auto-Resolver (Phase 11.3), der pro geprüftem Kandidaten
    Latenz/Kosten sparen will UND die Bildinformation nicht über den Umweg
    einer separaten Text-Beschreibung an die Validierung weiterreichen muss
    (die Bilder bleiben im selben Kontextfenster wie die Beurteilung).

    Liefert IMMER ein dict mit description/status/score/reason — auch bei
    fehlenden Frames oder nicht auswertbarer Antwort (FAIL/NEEDS_USER_REVIEW
    statt Exception)."""
    if not frame_paths:
        return {"description": "", "status": "FAIL", "score": 0.0, "reason": "Keine Frames verfügbar."}

    client = _get_client()
    from google.genai import types

    must_show_line = ", ".join(must_show or []) or "keine besonderen Vorgaben"
    avoid_line = ", ".join(avoid_showing or []) or "keine"
    prompt_text = (
        f"Du analysierst die Mediendatei '{media_name}' aus dem Ordner "
        f"'{folder_name}' als Kandidat für einen Voice-over-Satz.\n"
        f"Ort/Ordner: {location_name or folder_name or 'unbekannt'}\n"
        f"Voice-over-Satz: {passage_text.strip()}\n"
        f"Visuelle Anforderung: {visual_requirement.strip() or passage_text.strip()}\n"
        f"Muss zeigen: {must_show_line}\n"
        f"Darf nicht zeigen: {avoid_line}\n\n"
        "Beschreibe zunächst kurz und sachlich (max. 4 Sätze, Sprache: "
        f"{language}), was auf den Bildern zu sehen ist (Ort, Motiv, Stimmung, "
        "Kameraperspektive). Beurteile danach, ob dieses Material inhaltlich "
        "zum Satz UND zur visuellen Anforderung passt.\n\n"
        "Antworte NUR als JSON in exakt diesem Format:\n"
        '{"description":"...","status":"PASS|WEAK_PASS|NEEDS_USER_REVIEW|FAIL",'
        '"score":0.0,"reason":"..."}\n'
        "PASS = passt eindeutig. WEAK_PASS = passt teilweise/generisch. "
        "NEEDS_USER_REVIEW = unklar, manuelle Prüfung nötig. FAIL = passt "
        "nicht oder zeigt verbotene Inhalte."
    )
    parts: list[types.Part] = [types.Part.from_text(text=prompt_text)]
    for frame_path in frame_paths:
        parts.append(types.Part.from_bytes(data=frame_path.read_bytes(), mime_type="image/jpeg"))

    response = client.models.generate_content(
        model=resolve_gemini_model(model),
        contents=[types.Content(role="user", parts=parts)],
    )
    text = response.text or "{}"
    try:
        payload = _extract_json(text)
    except json.JSONDecodeError:
        payload = {
            "description": "",
            "status": "NEEDS_USER_REVIEW",
            "score": 0.5,
            "reason": "Antwort nicht auswertbar.",
        }
    status = str(payload.get("status", "NEEDS_USER_REVIEW")).upper()
    if status not in SUPPLEMENT_VALIDATION_STATUSES:
        status = "NEEDS_USER_REVIEW"
    try:
        score = float(payload.get("score", 0.5))
    except (TypeError, ValueError):
        score = 0.5
    return {
        "description": str(payload.get("description", "")).strip(),
        "status": status,
        "score": max(0.0, min(1.0, score)),
        "reason": str(payload.get("reason", "")).strip(),
    }


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
    elif prompt_mode == "holistic_v1":
        prompt = build_plan_folder_holistic_v1_prompt(
            folder_name=folder_name,
            segment_lines=_format_segment_lines_holistic_v1(segments),
            asset_lines=_format_asset_lines_holistic_v1(assets),
            language=language,
            extra_instructions=extra_instructions,
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


def _format_segment_lines_holistic_v1(segments: list[dict[str, Any]]) -> str:
    lines: list[str] = []
    for segment in segments:
        beat_id = str(segment.get("beat_id", "")).strip()
        text = str(segment.get("text", "")).strip()
        start_sec = segment.get("start_sec", 0.0)
        end_sec = segment.get("end_sec", 0.0)
        if not text:
            continue
        lines.append(
            f'- beat_id="{beat_id}" start_sec={float(start_sec)} end_sec={float(end_sec)} text="{text}"'
        )
    return "\n".join(lines) or "- (keine)"


def _format_asset_lines_holistic_v1(assets: list[dict[str, str]]) -> str:
    lines = [
        f'- path="{item.get("path", "")}" description="{item.get("description", "")}"'
        for item in assets
        if item.get("path")
    ]
    return "\n".join(lines) or "- (keine)"


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


def build_plan_folder_holistic_v1_prompt(
    *,
    folder_name: str,
    segment_lines: str,
    asset_lines: str,
    language: str,
    extra_instructions: str = "",
) -> str:
    """Ursprünglicher Holistic-Prompt (v1): keine Shot-Timing- oder Asset-Nutzungsregeln an das LLM."""
    sections = [
        f"Du planst Video-Shots für den Ordner '{folder_name}'. Sprache: {language}.",
        "",
        "Voice-over-Segmente (in chronologischer Reihenfolge):",
        segment_lines,
        "",
        "Verfügbare lokale Assets:",
        asset_lines,
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
            "WICHTIG: Betrachte ALLE Segmente und ALLE Assets gesamtheitlich.",
            "Wähle für jeden Shot das inhaltlich passendste Asset aus der gesamten Liste.",
            "Vermeide unnötige Wiederholungen, aber inhaltliche Passung hat Priorität.",
            "Wenn die Passage mehrere Sehenswürdigkeiten/Motive nennt, erstelle mehrere Teile.",
            "Bewerte die visuelle Passung jedes Teils: sehr_gut, gut, mittel oder unpassend.",
            "Bei unpassend: asset_path auf null setzen.",
            "",
            "Antworte NUR als JSON:",
            (
                '{"beats":[{"beat_id":"beat_001","parts":[{"text":"...","motif":"...",'
                '"asset_path":"exakter path oder null",'
                '"match_quality":"sehr_gut|gut|mittel|unpassend"}]}]}'
            ),
            "beat_id muss exakt einem beat_id aus den Segmenten entsprechen.",
            "asset_path muss exakt einem path aus der Asset-Liste entsprechen oder null sein.",
        ]
    )
    return "\n".join(sections)


def build_plan_folder_model_comparison_prompt(
    *,
    folder_name: str,
    segment_lines: str,
    asset_lines: str,
    language: str,
    editor_hint: str = "",
) -> str:
    """Neutraler Vergleichs-Prompt ohne harte Timing- oder Asset-Nutzungsregeln."""
    sections = [
        (
            f"Du planst Video-Shots für den Ordner '{folder_name}' als kreativen, "
            f"semantischen Vorschlag. Sprache: {language}."
        ),
        "",
        "Voice-over-Segmente (in chronologischer Reihenfolge):",
        segment_lines,
        "",
        "Verfügbare lokale Assets:",
        asset_lines,
    ]
    hint = editor_hint.strip()
    if hint:
        sections.extend(
            [
                "",
                "Zusätzliche Anweisungen des Editors (Hinweis, keine harten technischen Regeln):",
                hint,
            ]
        )
    sections.extend(
        [
            "",
            "WICHTIG: Betrachte ALLE Segmente und ALLE Assets gesamtheitlich.",
            "Wähle für jeden Teil das inhaltlich passendste Asset.",
            "Wenn die Passage mehrere Motive nennt, darfst du mehrere Teile vorschlagen.",
            "Bewerte die visuelle Passung: sehr_gut, gut, mittel oder unpassend.",
            "Bei unpassend: asset_path auf null setzen.",
            "Du darfst optional desired_duration_sec, visual_intent, reason und confidence angeben.",
            "Es gibt KEINE harten Min-/Max-Shot-Regeln und KEINE Asset-Wiederverwendungslimits.",
            "",
            "Antworte NUR als JSON:",
            (
                '{"beats":[{"beat_id":"beat_001","parts":[{"text":"...","motif":"...",'
                '"asset_path":"exakter path oder null",'
                '"match_quality":"sehr_gut|gut|mittel|unpassend",'
                '"visual_intent":"optional",'
                '"reason":"optional",'
                '"confidence":"optional",'
                '"desired_duration_sec":6.0}]}]}'
            ),
            "beat_id muss exakt einem beat_id aus den Segmenten entsprechen.",
            "asset_path muss exakt einem path aus der Asset-Liste entsprechen oder null sein.",
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
