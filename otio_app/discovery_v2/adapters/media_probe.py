"""Adapter: technische Medienprüfung über vorhandene ffprobe-Helfer."""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path

from otio_app.discovery_v2.domain.inventory import MediaKind
from otio_app.services.clean_media import probe_media


class MediaProbeAdapterError(Exception):
    """Kontrollierter Probe-Fehler (nicht vorhanden, Timeout, ungültige Ausgabe)."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


# Eng begrenzte, deterministische Ableitung — unbekannte Formate bleiben null.
_KNOWN_PIXEL_FORMAT_BIT_DEPTH: dict[str, int] = {
    "yuv420p": 8,
    "yuvj420p": 8,
    "yuv420p10le": 10,
    "yuv422p10le": 10,
}


@dataclass(frozen=True)
class NormalizedMediaProbe:
    media_kind: str
    container_format: str | None = None
    video_codec: str | None = None
    audio_codec: str | None = None
    width: int | None = None
    height: int | None = None
    duration_seconds: float | None = None
    frame_rate_numerator: int | None = None
    frame_rate_denominator: int | None = None
    audio_stream_count: int | None = None
    embedded_timecode: str | None = None
    pixel_format: str | None = None
    bit_depth: int | None = None


def derive_bit_depth(
    pixel_format: str | None,
    *,
    bits_per_raw_sample: object | None = None,
) -> int | None:
    """Liefert Bit-Tiefe nur bei zuverlässiger Bestimmung, sonst ``None``."""
    if bits_per_raw_sample is not None and str(bits_per_raw_sample).strip() != "":
        try:
            value = int(str(bits_per_raw_sample).strip())
        except (TypeError, ValueError):
            value = None
        else:
            if value > 0:
                return value
    if not pixel_format:
        return None
    return _KNOWN_PIXEL_FORMAT_BIT_DEPTH.get(str(pixel_format).strip().lower())


def parse_frame_rate_fraction(value: str | None) -> tuple[int, int] | None:
    """Parst ``r_frame_rate`` als rationale Bildrate (Zähler/Nenner)."""
    if value is None:
        return None
    text = str(value).strip()
    if not text or text in {"0/0", "N/A", "nan"}:
        return None
    try:
        if "/" in text:
            num_s, den_s = text.split("/", 1)
            numerator = int(num_s)
            denominator = int(den_s)
            if denominator == 0 or numerator < 0 or denominator < 0:
                return None
            if numerator == 0:
                return None
            return numerator, denominator
        frac = Fraction(text).limit_denominator(100_000)
        if frac.denominator == 0 or frac.numerator <= 0:
            return None
        return int(frac.numerator), int(frac.denominator)
    except (ValueError, ZeroDivisionError, TypeError):
        return None


def _run_ffprobe_json(path: Path, *, timeout_sec: int = 120) -> dict:
    """Ruft ffprobe als Argumentliste auf (keine Shell-Injection)."""
    command = [
        "ffprobe",
        "-v",
        "error",
        "-show_entries",
        "format=format_name,duration:format_tags=timecode:"
        "stream=index,codec_type,codec_name,width,height,r_frame_rate,"
        "pix_fmt,bits_per_raw_sample,codec_tag_string:stream_tags=timecode",
        "-of",
        "json",
        str(path),
    ]
    try:
        result = subprocess.run(
            command,
            capture_output=True,
            check=False,
            timeout=timeout_sec,
        )
    except FileNotFoundError as exc:
        raise MediaProbeAdapterError(
            "ffprobe_missing",
            "ffprobe ist nicht vorhanden.",
        ) from exc
    except subprocess.TimeoutExpired as exc:
        raise MediaProbeAdapterError(
            "ffprobe_timeout",
            f"ffprobe-Timeout nach {timeout_sec}s.",
        ) from exc

    if result.returncode != 0:
        message = (result.stderr or result.stdout or b"").decode(
            "utf-8", errors="replace"
        ).strip()
        raise MediaProbeAdapterError(
            "ffprobe_failed",
            message or f"ffprobe exit {result.returncode}",
        )

    try:
        stdout = (result.stdout or b"").decode("utf-8", errors="replace")
        payload = json.loads(stdout or "{}")
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise MediaProbeAdapterError(
            "ffprobe_invalid_output",
            "Ungültige ffprobe-Ausgabe.",
        ) from exc
    if not isinstance(payload, dict):
        raise MediaProbeAdapterError(
            "ffprobe_invalid_output",
            "Ungültige ffprobe-Ausgabe.",
        )
    return payload


def _extract_embedded_timecode(payload: dict) -> str | None:
    format_tags = (payload.get("format") or {}).get("tags") or {}
    if format_tags.get("timecode"):
        return str(format_tags["timecode"])

    tmcd_tc: str | None = None
    other_tc: str | None = None
    for stream in payload.get("streams") or []:
        tags = stream.get("tags") or {}
        tag_value = tags.get("timecode")
        if not tag_value:
            continue
        is_tmcd = (stream.get("codec_type") or "").lower() == "data" or (
            stream.get("codec_tag_string") or ""
        ).lower() == "tmcd"
        if is_tmcd and tmcd_tc is None:
            tmcd_tc = str(tag_value)
        elif other_tc is None:
            other_tc = str(tag_value)
    return tmcd_tc or other_tc


def probe_source_media(
    path: Path,
    *,
    media_kind: MediaKind,
) -> NormalizedMediaProbe:
    """Liest technische Medieninformationen und normalisiert sie für die Registry."""
    if media_kind == MediaKind.OTHER:
        raise MediaProbeAdapterError(
            "unsupported_media_kind",
            f"Medientyp wird nicht technisch geprüft: {media_kind.value}",
        )

    # Vorhandener Wrapper für Basisdaten (Codec/Dimensionen/Dauer).
    basic = probe_media(path)
    payload = _run_ffprobe_json(path)

    format_info = payload.get("format") or {}
    container = format_info.get("format_name") or basic.container
    if isinstance(container, str) and "," in container:
        # ffprobe liefert oft "mov,mp4,m4a,..." — ersten Token behalten.
        container = container.split(",", 1)[0].strip() or basic.container

    duration = basic.duration_sec
    raw_duration = format_info.get("duration")
    if raw_duration is not None:
        try:
            duration = float(raw_duration)
        except (TypeError, ValueError):
            pass

    video_codec = basic.video_codec
    audio_codec = basic.audio_codec
    width = basic.width
    height = basic.height
    frame_num: int | None = None
    frame_den: int | None = None
    audio_stream_count = 0
    pixel_format: str | None = None
    bits_per_raw_sample: object | None = None
    if getattr(basic, "pixel_format", None):
        pixel_format = str(basic.pixel_format).strip().lower() or None

    for stream in payload.get("streams") or []:
        codec_type = (stream.get("codec_type") or "").lower()
        codec_name = (stream.get("codec_name") or "").lower() or None
        if codec_type == "video":
            if video_codec is None and codec_name:
                video_codec = codec_name
            if width is None and stream.get("width") is not None:
                try:
                    width = int(stream["width"])
                except (TypeError, ValueError):
                    pass
            if height is None and stream.get("height") is not None:
                try:
                    height = int(stream["height"])
                except (TypeError, ValueError):
                    pass
            if frame_num is None:
                frac = parse_frame_rate_fraction(stream.get("r_frame_rate"))
                if frac is not None:
                    frame_num, frame_den = frac
            if pixel_format is None and stream.get("pix_fmt"):
                pixel_format = str(stream["pix_fmt"]).strip().lower() or None
            if bits_per_raw_sample is None and stream.get("bits_per_raw_sample") not in (
                None,
                "",
                "N/A",
            ):
                bits_per_raw_sample = stream.get("bits_per_raw_sample")
        elif codec_type == "audio":
            audio_stream_count += 1
            if audio_codec is None and codec_name:
                audio_codec = codec_name

    bit_depth = (
        derive_bit_depth(pixel_format, bits_per_raw_sample=bits_per_raw_sample)
        if media_kind == MediaKind.VIDEO
        else None
    )
    if media_kind != MediaKind.VIDEO:
        pixel_format = None

    # Bild: ffprobe liefert oft video-stream mit codec mjpeg/png/… und r_frame_rate 0/0
    if media_kind == MediaKind.IMAGE:
        frame_num = None
        frame_den = None
        if width is None or height is None:
            raise MediaProbeAdapterError(
                "unreadable_media",
                "Bild technisch nicht lesbar (Breite/Höhe fehlen).",
            )

    if media_kind == MediaKind.VIDEO and video_codec is None and width is None:
        raise MediaProbeAdapterError(
            "unreadable_media",
            "Video technisch nicht lesbar.",
        )

    if media_kind == MediaKind.AUDIO and audio_codec is None and audio_stream_count == 0:
        raise MediaProbeAdapterError(
            "unreadable_media",
            "Audio technisch nicht lesbar.",
        )

    return NormalizedMediaProbe(
        media_kind=media_kind.value,
        container_format=str(container) if container else None,
        video_codec=video_codec,
        audio_codec=audio_codec,
        width=width,
        height=height,
        duration_seconds=duration,
        frame_rate_numerator=frame_num,
        frame_rate_denominator=frame_den,
        audio_stream_count=audio_stream_count if media_kind != MediaKind.IMAGE else None,
        embedded_timecode=_extract_embedded_timecode(payload),
        pixel_format=pixel_format,
        bit_depth=bit_depth,
    )
