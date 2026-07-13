"""Gemini Image (Nano Banana) Client für Kapitel-Karten — immer 16:9."""

from __future__ import annotations

from pathlib import Path

from otio_app.defaults import (
    CHAPTER_MAP_ASPECT_RATIO,
    CHAPTER_MAP_ASPECT_RATIO_TOLERANCE,
    CHAPTER_MAP_IMAGE_SIZE_DEFAULT,
    CHAPTER_MAP_MODEL_DEFAULT,
    CHAPTER_MAP_TARGET_HEIGHT,
    CHAPTER_MAP_TARGET_WIDTH,
)
from otio_app.services.api_keys import get_api_key
from otio_app.services.gemini_client import GeminiNotConfiguredError


class ChapterMapImageError(RuntimeError):
    """Kapitel-Karten-Bildgenerierung fehlgeschlagen."""


def _require_gemini_image_client():
    api_key = get_api_key("GEMINI_API_KEY")
    if not api_key:
        raise GeminiNotConfiguredError(
            "GEMINI_API_KEY ist nicht gesetzt. "
            "Bitte unter 🔑 API-Schlüssel oder in .env eintragen "
            "(Nano Banana / Gemini Image nutzt denselben Key)."
        )
    from google import genai

    return genai.Client(api_key=api_key)


def assert_aspect_ratio_16_9(image_path: Path) -> tuple[int, int]:
    """Prüft, dass das Bild (nahezu) 16:9 ist — sonst Fehler statt strecken."""
    from PIL import Image

    with Image.open(image_path) as image:
        width, height = image.size
    if height <= 0:
        raise ChapterMapImageError(f"Ungültige Bildhöhe in `{image_path}`.")
    ratio = width / height
    expected = CHAPTER_MAP_TARGET_WIDTH / CHAPTER_MAP_TARGET_HEIGHT
    if abs(ratio - expected) > CHAPTER_MAP_ASPECT_RATIO_TOLERANCE:
        raise ChapterMapImageError(
            f"Kapitel-Karte ist nicht 16:9 (ist {width}x{height}, Ratio {ratio:.4f}). "
            "Bitte erneut generieren — Ränder/Letterboxing werden nicht akzeptiert."
        )
    return width, height


def generate_chapter_map_image(
    *,
    prompt: str,
    reference_image_paths: list[Path],
    output_path: Path,
    model: str | None = None,
    image_size: str | None = None,
) -> tuple[int, int]:
    """Generiert ein 16:9-Kartenbild aus Prompt + Referenzbild(ern).

    Nutzt gemini-*-image (Nano Banana / Pro). image_size "2K" liefert bei
    unterstützten Modellen schärfere Ausgabe. Das Ergebnis wird roh gespeichert;
    Geometrie (exakt 16:9 / 1920×1080) und Qualitäts-Upscale laufen separat.
    """
    resolved_model = (model or CHAPTER_MAP_MODEL_DEFAULT).strip() or CHAPTER_MAP_MODEL_DEFAULT
    resolved_size = (image_size or CHAPTER_MAP_IMAGE_SIZE_DEFAULT).strip() or CHAPTER_MAP_IMAGE_SIZE_DEFAULT
    client = _require_gemini_image_client()
    from google.genai import types
    from PIL import Image

    missing = [str(path) for path in reference_image_paths if not path.is_file()]
    if missing:
        raise ChapterMapImageError(
            "Referenzbild(er) fehlen: " + ", ".join(missing)
        )

    contents: list = [prompt]
    for path in reference_image_paths:
        contents.append(Image.open(path))

    image_config_kwargs: dict = {"aspect_ratio": CHAPTER_MAP_ASPECT_RATIO}
    # 2K/1K — Pro und neuere Flash-Image-Modelle; bei Ablehnung ohne size retry.
    if resolved_size:
        image_config_kwargs["image_size"] = resolved_size

    try:
        response = client.models.generate_content(
            model=resolved_model,
            contents=contents,
            config=types.GenerateContentConfig(
                response_modalities=["IMAGE"],
                image_config=types.ImageConfig(**image_config_kwargs),
            ),
        )
    except Exception as exc:  # noqa: BLE001
        message = str(exc).lower()
        if "image_size" in message or "imagesize" in message:
            response = client.models.generate_content(
                model=resolved_model,
                contents=contents,
                config=types.GenerateContentConfig(
                    response_modalities=["IMAGE"],
                    image_config=types.ImageConfig(aspect_ratio=CHAPTER_MAP_ASPECT_RATIO),
                ),
            )
        else:
            raise

    image_bytes: bytes | None = None
    mime_type = "image/png"
    parts = getattr(response, "parts", None) or []
    for part in parts:
        inline = getattr(part, "inline_data", None)
        if inline is None:
            continue
        data = getattr(inline, "data", None)
        if not data:
            continue
        image_bytes = data if isinstance(data, (bytes, bytearray)) else bytes(data)
        mime_type = getattr(inline, "mime_type", None) or mime_type
        break

    if not image_bytes:
        raise ChapterMapImageError(
            "Gemini Image hat kein Bild zurückgegeben. Bitte erneut versuchen."
        )

    output_path.parent.mkdir(parents=True, exist_ok=True)
    from io import BytesIO

    with Image.open(BytesIO(image_bytes)) as generated:
        rgb = generated.convert("RGB")
        rgb.save(output_path, format="PNG", optimize=True)
        return rgb.size
