"""Upscale für Kapitel-Karten — getrennt von der Gemini-Bildgenerierung.

Unterstützt:
- openrouter: OpenRouter Image-API (Image-to-Image @ 2K/4K)
- lanczos: lokales PIL-Resize auf 1920×1080
- replicate_esrgan: Real-ESRGAN über Replicate HTTP API
- none: Rohbild belassen

Hinweis: OpenRouter bietet kein reines ESRGAN/Topaz. Upscale läuft als
Image-to-Image mit höherer Auflösung und Preserve-Prompt.
"""

from __future__ import annotations

import base64
import mimetypes
import time
from io import BytesIO
from pathlib import Path

import requests
from PIL import Image

from otio_app.defaults import (
    CHAPTER_MAP_ASPECT_RATIO,
    CHAPTER_MAP_ASPECT_RATIO_TOLERANCE,
    CHAPTER_MAP_OPENROUTER_API_BASE_URL,
    CHAPTER_MAP_OPENROUTER_UPSCALE_MODEL_DEFAULT,
    CHAPTER_MAP_OPENROUTER_UPSCALE_RESOLUTION_DEFAULT,
    CHAPTER_MAP_REPLICATE_API_BASE_URL,
    CHAPTER_MAP_REPLICATE_ESRGAN_MODEL,
    CHAPTER_MAP_TARGET_HEIGHT,
    CHAPTER_MAP_TARGET_WIDTH,
    CHAPTER_MAP_UPSCALER_LANCZOS,
    CHAPTER_MAP_UPSCALER_NONE,
    CHAPTER_MAP_UPSCALER_OPENROUTER,
    CHAPTER_MAP_UPSCALER_REPLICATE_ESRGAN,
)
from otio_app.services.api_keys import get_api_key

_REQUEST_TIMEOUT_SEC = 180
_POLL_INTERVAL_SEC = 1.5
_MAX_POLL_ATTEMPTS = 120

_OPENROUTER_UPSCALE_PROMPT = (
    "Upscale this exact chapter map image to higher resolution. "
    "Preserve the layout, pins, numbers, labels, colors, geography, and style "
    "exactly as shown. Do not redraw, invent, move, or remove any elements. "
    "Faithful resolution enhancement only. Keep 16:9 aspect ratio."
)


class ChapterMapUpscaleError(RuntimeError):
    """Upscale fehlgeschlagen (enthält niemals den API-Key)."""


def is_openrouter_configured() -> bool:
    return bool(get_api_key("OPENROUTER_API_KEY"))


def is_replicate_configured() -> bool:
    return bool(get_api_key("REPLICATE_API_TOKEN"))


def _assert_near_16_9(width: int, height: int) -> None:
    if height <= 0:
        raise ChapterMapUpscaleError("Ungültige Bildhöhe nach Upscale.")
    ratio = width / height
    expected = CHAPTER_MAP_TARGET_WIDTH / CHAPTER_MAP_TARGET_HEIGHT
    if abs(ratio - expected) > CHAPTER_MAP_ASPECT_RATIO_TOLERANCE:
        raise ChapterMapUpscaleError(
            f"Upscale-Ergebnis ist nicht 16:9 ({width}x{height}, Ratio {ratio:.4f})."
        )


def _fit_to_target(image: Image.Image) -> Image.Image:
    """Auf Timeline-Zielgröße bringen (Breite 1920), Aspect Ratio beibehalten."""
    if image.width == CHAPTER_MAP_TARGET_WIDTH and image.height == CHAPTER_MAP_TARGET_HEIGHT:
        return image
    scale = CHAPTER_MAP_TARGET_WIDTH / float(image.width)
    new_size = (
        CHAPTER_MAP_TARGET_WIDTH,
        max(1, int(round(image.height * scale))),
    )
    return image.resize(new_size, Image.Resampling.LANCZOS)


def upscale_lanczos(image_path: Path) -> tuple[int, int]:
    """Lokales Lanczos auf Zielbreite, wenn kleiner als 1920px."""
    with Image.open(image_path) as image:
        rgb = image.convert("RGB")
        if rgb.width >= CHAPTER_MAP_TARGET_WIDTH:
            width, height = rgb.size
            _assert_near_16_9(width, height)
            return width, height
        scaled = _fit_to_target(rgb)
        scaled.save(image_path, format="PNG", optimize=True)
        width, height = scaled.size
    _assert_near_16_9(width, height)
    return width, height


def _image_data_uri(image_path: Path, *, prefer_jpeg: bool = False) -> str:
    if prefer_jpeg:
        with Image.open(image_path) as image:
            rgb = image.convert("RGB")
            buffer = BytesIO()
            rgb.save(buffer, format="JPEG", quality=92, optimize=True)
            encoded = base64.b64encode(buffer.getvalue()).decode("ascii")
        return f"data:image/jpeg;base64,{encoded}"
    mime, _ = mimetypes.guess_type(str(image_path))
    if not mime or not mime.startswith("image/"):
        mime = "image/png"
    encoded = base64.b64encode(image_path.read_bytes()).decode("ascii")
    return f"data:{mime};base64,{encoded}"


def _decode_openrouter_image(payload: dict) -> bytes:
    data = payload.get("data")
    if not isinstance(data, list) or not data:
        raise ChapterMapUpscaleError(
            "OpenRouter lieferte kein Bild. "
            f"Antwort: {str(payload)[:300]!r}"
        )
    first = data[0] if isinstance(data[0], dict) else {}
    b64 = first.get("b64_json") or first.get("b64")
    if isinstance(b64, str) and b64.strip():
        try:
            return base64.b64decode(b64)
        except Exception as exc:  # noqa: BLE001
            raise ChapterMapUpscaleError(
                f"OpenRouter-Bild konnte nicht dekodiert werden: {exc}"
            ) from exc
    url = first.get("url")
    if isinstance(url, str) and url.startswith("http"):
        return _download_bytes(url)
    raise ChapterMapUpscaleError(
        "OpenRouter-Antwort ohne b64_json/url. "
        f"keys={list(first.keys())!r}"
    )


def upscale_openrouter(
    image_path: Path,
    *,
    model: str | None = None,
    resolution: str | None = None,
) -> tuple[int, int]:
    """Image-to-Image Upscale über OpenRouter `/api/v1/images`."""
    api_key = get_api_key("OPENROUTER_API_KEY")
    if not api_key:
        raise ChapterMapUpscaleError(
            "OPENROUTER_API_KEY ist nicht gesetzt. "
            "Bitte unter 🔑 API-Schlüssel eintragen oder Upscaler auf Lanczos stellen."
        )

    resolved_model = (
        (model or CHAPTER_MAP_OPENROUTER_UPSCALE_MODEL_DEFAULT).strip()
        or CHAPTER_MAP_OPENROUTER_UPSCALE_MODEL_DEFAULT
    )
    resolved_resolution = (
        (resolution or CHAPTER_MAP_OPENROUTER_UPSCALE_RESOLUTION_DEFAULT).strip()
        or CHAPTER_MAP_OPENROUTER_UPSCALE_RESOLUTION_DEFAULT
    )

    # Sourceful hat 4.5MB Limit — JPEG-Data-URI ist robuster für große Karten.
    prefer_jpeg = resolved_model.startswith("sourceful/")
    body: dict = {
        "model": resolved_model,
        "prompt": _OPENROUTER_UPSCALE_PROMPT,
        "n": 1,
        "output_format": "png",
        "input_references": [
            {
                "type": "image_url",
                "image_url": {
                    "url": _image_data_uri(image_path, prefer_jpeg=prefer_jpeg),
                },
            }
        ],
    }
    # Nicht alle Modelle akzeptieren resolution/aspect_ratio — bei Ablehnung retry.
    body_with_geometry = {
        **body,
        "resolution": resolved_resolution,
        "aspect_ratio": CHAPTER_MAP_ASPECT_RATIO,
    }

    url = f"{CHAPTER_MAP_OPENROUTER_API_BASE_URL}/images"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "HTTP-Referer": "https://github.com/johnkuhnmedien-cmd/otio",
        "X-Title": "OTIO Chapter Map Upscale",
    }

    try:
        response = requests.post(
            url,
            headers=headers,
            json=body_with_geometry,
            timeout=_REQUEST_TIMEOUT_SEC,
        )
    except requests.RequestException as exc:
        raise ChapterMapUpscaleError(f"OpenRouter-Request fehlgeschlagen: {exc}") from exc

    if response.status_code >= 400:
        message = (response.text or "").lower()
        # Fallback ohne resolution/aspect_ratio falls Endpoint sie nicht kennt.
        if any(
            token in message
            for token in ("resolution", "aspect_ratio", "unsupported", "invalid")
        ):
            try:
                response = requests.post(
                    url,
                    headers=headers,
                    json=body,
                    timeout=_REQUEST_TIMEOUT_SEC,
                )
            except requests.RequestException as exc:
                raise ChapterMapUpscaleError(
                    f"OpenRouter-Retry fehlgeschlagen: {exc}"
                ) from exc
        if response.status_code >= 400:
            detail = (response.text or "")[:500]
            raise ChapterMapUpscaleError(
                f"OpenRouter antwortete mit Status {response.status_code}: {detail}"
            )

    try:
        payload = response.json()
    except ValueError as exc:
        raise ChapterMapUpscaleError(
            f"OpenRouter-Antwort ist kein JSON: {(response.text or '')[:300]}"
        ) from exc

    if isinstance(payload, dict) and payload.get("error"):
        err = payload["error"]
        detail = err.get("message") if isinstance(err, dict) else err
        raise ChapterMapUpscaleError(f"OpenRouter-Fehler: {detail}")

    image_bytes = _decode_openrouter_image(payload)
    with Image.open(BytesIO(image_bytes)) as upscaled:
        rgb = upscaled.convert("RGB")
        fitted = _fit_to_target(rgb)
        fitted.save(image_path, format="PNG", optimize=True)
        width, height = fitted.size

    _assert_near_16_9(width, height)
    return width, height


def _choose_esrgan_scale(width: int) -> int:
    """Scale so dass die Ausgabe mind. ~1920px Breite erreicht (max 4)."""
    if width <= 0:
        return 2
    if width * 2 >= CHAPTER_MAP_TARGET_WIDTH:
        return 2
    if width * 3 >= CHAPTER_MAP_TARGET_WIDTH:
        return 3
    return 4


def _download_bytes(url: str) -> bytes:
    try:
        response = requests.get(url, timeout=_REQUEST_TIMEOUT_SEC)
    except requests.RequestException as exc:
        raise ChapterMapUpscaleError(f"Bild-Download fehlgeschlagen: {exc}") from exc
    if response.status_code >= 400:
        raise ChapterMapUpscaleError(
            f"Bild-Download Status {response.status_code}: {(response.text or '')[:300]}"
        )
    return response.content


def _extract_output_url(payload: dict) -> str:
    output = payload.get("output")
    if isinstance(output, str) and output.startswith("http"):
        return output
    if isinstance(output, list) and output:
        first = output[0]
        if isinstance(first, str) and first.startswith("http"):
            return first
        if isinstance(first, dict):
            url = first.get("url") or first.get("href")
            if isinstance(url, str) and url.startswith("http"):
                return url
    if isinstance(output, dict):
        url = output.get("url") or output.get("href")
        if isinstance(url, str) and url.startswith("http"):
            return url
    raise ChapterMapUpscaleError(
        "Replicate Real-ESRGAN lieferte keine Bild-URL. "
        f"Status={payload.get('status')!r}, output={str(output)[:200]!r}"
    )


def _poll_prediction(api_key: str, prediction_url: str) -> dict:
    headers = {"Authorization": f"Bearer {api_key}", "Accept": "application/json"}
    for _ in range(_MAX_POLL_ATTEMPTS):
        try:
            response = requests.get(
                prediction_url, headers=headers, timeout=_REQUEST_TIMEOUT_SEC
            )
        except requests.RequestException as exc:
            raise ChapterMapUpscaleError(f"Replicate-Poll fehlgeschlagen: {exc}") from exc
        if response.status_code >= 400:
            raise ChapterMapUpscaleError(
                f"Replicate-Poll Status {response.status_code}: {(response.text or '')[:400]}"
            )
        payload = response.json()
        status = (payload.get("status") or "").lower()
        if status == "succeeded":
            return payload
        if status in {"failed", "canceled", "cancelled"}:
            detail = payload.get("error") or payload.get("status")
            raise ChapterMapUpscaleError(f"Replicate Real-ESRGAN fehlgeschlagen: {detail}")
        time.sleep(_POLL_INTERVAL_SEC)
    raise ChapterMapUpscaleError("Replicate Real-ESRGAN Timeout — bitte erneut versuchen.")


def upscale_replicate_esrgan(image_path: Path) -> tuple[int, int]:
    """Real-ESRGAN über Replicate; danach auf 1920px Breite fitten."""
    api_key = get_api_key("REPLICATE_API_TOKEN")
    if not api_key:
        raise ChapterMapUpscaleError(
            "REPLICATE_API_TOKEN ist nicht gesetzt. "
            "Bitte unter 🔑 API-Schlüssel eintragen oder Upscaler auf Lanczos/OpenRouter stellen."
        )

    with Image.open(image_path) as probe:
        source_width = probe.width
    scale = _choose_esrgan_scale(source_width)
    owner_model = CHAPTER_MAP_REPLICATE_ESRGAN_MODEL
    create_url = f"{CHAPTER_MAP_REPLICATE_API_BASE_URL}/models/{owner_model}/predictions"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "Prefer": "wait",
    }
    body = {
        "input": {
            "image": _image_data_uri(image_path),
            "scale": scale,
            "face_enhance": False,
        }
    }

    try:
        response = requests.post(
            create_url,
            headers=headers,
            json=body,
            timeout=_REQUEST_TIMEOUT_SEC,
        )
    except requests.RequestException as exc:
        raise ChapterMapUpscaleError(f"Replicate-Request fehlgeschlagen: {exc}") from exc

    if response.status_code >= 400:
        detail = (response.text or "")[:500]
        raise ChapterMapUpscaleError(
            f"Replicate antwortete mit Status {response.status_code}: {detail}"
        )

    payload = response.json()
    status = (payload.get("status") or "").lower()
    if status != "succeeded":
        get_url = payload.get("urls", {}).get("get") if isinstance(payload.get("urls"), dict) else None
        if not get_url:
            raise ChapterMapUpscaleError(
                f"Replicate Real-ESRGAN unvollständig (status={status!r})."
            )
        payload = _poll_prediction(api_key, get_url)

    output_url = _extract_output_url(payload)
    image_bytes = _download_bytes(output_url)

    with Image.open(BytesIO(image_bytes)) as upscaled:
        rgb = upscaled.convert("RGB")
        fitted = _fit_to_target(rgb)
        fitted.save(image_path, format="PNG", optimize=True)
        width, height = fitted.size

    _assert_near_16_9(width, height)
    return width, height


def upscale_chapter_map_image(
    image_path: Path,
    *,
    upscaler: str,
    openrouter_model: str | None = None,
    openrouter_resolution: str | None = None,
) -> tuple[int, int]:
    """Wendet den gewählten Upscaler auf die gespeicherte Karte an."""
    mode = (upscaler or CHAPTER_MAP_UPSCALER_NONE).strip().lower()
    if not image_path.is_file():
        raise ChapterMapUpscaleError(f"Bild zum Upscalen fehlt: `{image_path}`")

    if mode in {"", CHAPTER_MAP_UPSCALER_NONE}:
        with Image.open(image_path) as image:
            width, height = image.size
        _assert_near_16_9(width, height)
        return width, height

    if mode == CHAPTER_MAP_UPSCALER_LANCZOS:
        return upscale_lanczos(image_path)

    if mode == CHAPTER_MAP_UPSCALER_OPENROUTER:
        return upscale_openrouter(
            image_path,
            model=openrouter_model,
            resolution=openrouter_resolution,
        )

    if mode == CHAPTER_MAP_UPSCALER_REPLICATE_ESRGAN:
        return upscale_replicate_esrgan(image_path)

    raise ChapterMapUpscaleError(
        f"Unbekannter Upscaler `{upscaler}`. "
        f"Erlaubt: {CHAPTER_MAP_UPSCALER_OPENROUTER}, "
        f"{CHAPTER_MAP_UPSCALER_LANCZOS}, {CHAPTER_MAP_UPSCALER_REPLICATE_ESRGAN}, "
        f"{CHAPTER_MAP_UPSCALER_NONE}."
    )
