"""Adapter: technische Medienprüfung über vorhandene ffprobe-Helfer."""

from __future__ import annotations

import json
import math
import re
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
    "yuv422p": 8,
    "yuv444p": 8,
    "yuv420p10le": 10,
    "yuv422p10le": 10,
    "yuv444p10le": 10,
    "yuv420p12le": 12,
}

_HDR_TRANSFERS = frozenset(
    {
        "smpte2084",
        "arib-std-b67",
        "smpte2086",
    }
)
_HDR_PRIMARIES = frozenset({"bt2020"})

_DISPLAYMATRIX_RE = re.compile(
    r"([+-]?\d+(?:\.\d+)?)\s+([+-]?\d+(?:\.\d+)?)\s+([+-]?\d+(?:\.\d+)?)",
    re.MULTILINE,
)


@dataclass(frozen=True)
class DataStreamInfo:
    codec_name: str | None = None
    codec_tag: str | None = None

    @property
    def is_tmcd(self) -> bool:
        tag = (self.codec_tag or "").strip().lower()
        name = (self.codec_name or "").strip().lower()
        return tag == "tmcd" or name in {"tmcd", "timedcode"}


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
    avg_frame_rate_numerator: int | None = None
    avg_frame_rate_denominator: int | None = None
    audio_stream_count: int | None = None
    embedded_timecode: str | None = None
    pixel_format: str | None = None
    bit_depth: int | None = None
    audio_channels: int | None = None
    sample_aspect_ratio: str | None = None
    rotation_degrees: float | None = None
    color_range: str | None = None
    color_space: str | None = None
    color_transfer: str | None = None
    color_primaries: str | None = None
    subtitle_stream_count: int = 0
    data_stream_count: int = 0
    tmcd_stream_count: int = 0
    attachment_stream_count: int = 0
    data_streams: tuple[DataStreamInfo, ...] = ()


def is_reliable_constant_frame_rate(probe: NormalizedMediaProbe) -> bool:
    """True nur wenn r_frame_rate und avg_frame_rate übereinstimmen."""
    if not (
        probe.frame_rate_numerator
        and probe.frame_rate_denominator
        and probe.avg_frame_rate_numerator
        and probe.avg_frame_rate_denominator
    ):
        return False
    try:
        r = Fraction(probe.frame_rate_numerator, probe.frame_rate_denominator)
        avg = Fraction(
            probe.avg_frame_rate_numerator, probe.avg_frame_rate_denominator
        )
    except (ZeroDivisionError, ValueError):
        return False
    return r == avg


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


def is_hdr_color_profile(
    *,
    color_transfer: str | None,
    color_primaries: str | None,
) -> bool:
    transfer = (color_transfer or "").strip().lower()
    primaries = (color_primaries or "").strip().lower()
    return transfer in _HDR_TRANSFERS or primaries in _HDR_PRIMARIES


def _run_ffprobe_json(path: Path, *, timeout_sec: int = 120) -> dict:
    """Ruft ffprobe als Argumentliste auf (keine Shell-Injection)."""
    command = [
        "ffprobe",
        "-v",
        "error",
        "-show_entries",
        "format=format_name,duration:format_tags=timecode:"
        "stream=index,codec_type,codec_name,width,height,r_frame_rate,"
        "avg_frame_rate,pix_fmt,bits_per_raw_sample,codec_tag_string,channels,"
        "sample_aspect_ratio,color_range,color_space,color_transfer,"
        "color_primaries:stream_tags=timecode,rotate:"
        "stream_side_data=rotation,side_data_type",
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


def _tag_timecode(tags: dict | None) -> str | None:
    if not tags:
        return None
    for key, value in tags.items():
        if str(key).strip().lower() == "timecode" and value not in (None, ""):
            return str(value)
    return None


def _extract_embedded_timecode(payload: dict) -> str | None:
    format_tags = (payload.get("format") or {}).get("tags") or {}
    format_tc = _tag_timecode(format_tags)
    if format_tc:
        return format_tc

    tmcd_tc: str | None = None
    other_tc: str | None = None
    for stream in payload.get("streams") or []:
        tags = stream.get("tags") or {}
        tag_value = _tag_timecode(tags)
        if not tag_value:
            continue
        is_tmcd = (stream.get("codec_type") or "").lower() == "data" or (
            stream.get("codec_tag_string") or ""
        ).lower() == "tmcd"
        if is_tmcd and tmcd_tc is None:
            tmcd_tc = tag_value
        elif other_tc is None:
            other_tc = tag_value
    return tmcd_tc or other_tc


def _normalize_rotation(value: object | None) -> float | None:
    if value is None or value == "":
        return None
    try:
        degrees = float(value)
    except (TypeError, ValueError):
        return None
    if math.isnan(degrees) or math.isinf(degrees):
        return None
    # Normalisiere nahe Null zu 0.
    if abs(degrees) < 0.01:
        return 0.0
    return degrees


def _extract_rotation(stream: dict) -> float | None:
    tags = stream.get("tags") or {}
    for key, value in tags.items():
        if str(key).strip().lower() == "rotate":
            rot = _normalize_rotation(value)
            if rot is not None:
                return rot
    side_data = stream.get("side_data_list") or []
    for entry in side_data:
        if not isinstance(entry, dict):
            continue
        if entry.get("rotation") is not None:
            rot = _normalize_rotation(entry.get("rotation"))
            if rot is not None:
                return rot
        # Manche Builds liefern Display Matrix als Text.
        matrix = entry.get("displaymatrix")
        if isinstance(matrix, str) and "rotation" in matrix.lower():
            match = re.search(r"rotation of\s+([+-]?\d+(?:\.\d+)?)", matrix, re.I)
            if match:
                return _normalize_rotation(match.group(1))
    return None


def _normalize_color_token(value: object | None) -> str | None:
    if value is None:
        return None
    text = str(value).strip().lower()
    if not text or text in {"unknown", "unspecified", "n/a"}:
        return None
    return text


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
    avg_frame_num: int | None = None
    avg_frame_den: int | None = None
    audio_stream_count = 0
    subtitle_stream_count = 0
    data_stream_count = 0
    tmcd_stream_count = 0
    attachment_stream_count = 0
    data_streams: list[DataStreamInfo] = []
    audio_channels: int | None = None
    pixel_format: str | None = None
    bits_per_raw_sample: object | None = None
    sample_aspect_ratio: str | None = None
    rotation_degrees: float | None = None
    color_range: str | None = None
    color_space: str | None = None
    color_transfer: str | None = None
    color_primaries: str | None = None
    if getattr(basic, "pixel_format", None):
        pixel_format = str(basic.pixel_format).strip().lower() or None

    for stream in payload.get("streams") or []:
        codec_type = (stream.get("codec_type") or "").lower()
        codec_name = (stream.get("codec_name") or "").lower() or None
        codec_tag = (stream.get("codec_tag_string") or "").strip() or None
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
            if avg_frame_num is None:
                avg_frac = parse_frame_rate_fraction(stream.get("avg_frame_rate"))
                if avg_frac is not None:
                    avg_frame_num, avg_frame_den = avg_frac
            if pixel_format is None and stream.get("pix_fmt"):
                pixel_format = str(stream["pix_fmt"]).strip().lower() or None
            if bits_per_raw_sample is None and stream.get("bits_per_raw_sample") not in (
                None,
                "",
                "N/A",
            ):
                bits_per_raw_sample = stream.get("bits_per_raw_sample")
            if sample_aspect_ratio is None and stream.get("sample_aspect_ratio"):
                sar = str(stream["sample_aspect_ratio"]).strip()
                if sar and sar not in {"0:1", "N/A"}:
                    sample_aspect_ratio = sar
            if rotation_degrees is None:
                rotation_degrees = _extract_rotation(stream)
            if color_range is None:
                color_range = _normalize_color_token(stream.get("color_range"))
            if color_space is None:
                color_space = _normalize_color_token(stream.get("color_space"))
            if color_transfer is None:
                color_transfer = _normalize_color_token(stream.get("color_transfer"))
            if color_primaries is None:
                color_primaries = _normalize_color_token(stream.get("color_primaries"))
        elif codec_type == "audio":
            audio_stream_count += 1
            if audio_codec is None and codec_name:
                audio_codec = codec_name
            if audio_channels is None and stream.get("channels") is not None:
                try:
                    audio_channels = int(stream["channels"])
                except (TypeError, ValueError):
                    audio_channels = None
        elif codec_type == "subtitle":
            subtitle_stream_count += 1
        elif codec_type == "attachment":
            attachment_stream_count += 1
        elif codec_type == "data":
            info = DataStreamInfo(codec_name=codec_name, codec_tag=codec_tag)
            data_streams.append(info)
            data_stream_count += 1
            if info.is_tmcd:
                tmcd_stream_count += 1

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
        avg_frame_num = None
        avg_frame_den = None
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
        avg_frame_rate_numerator=avg_frame_num if media_kind == MediaKind.VIDEO else None,
        avg_frame_rate_denominator=(
            avg_frame_den if media_kind == MediaKind.VIDEO else None
        ),
        audio_stream_count=audio_stream_count if media_kind != MediaKind.IMAGE else None,
        embedded_timecode=_extract_embedded_timecode(payload),
        pixel_format=pixel_format,
        bit_depth=bit_depth,
        audio_channels=audio_channels if media_kind != MediaKind.IMAGE else None,
        sample_aspect_ratio=sample_aspect_ratio if media_kind == MediaKind.VIDEO else None,
        rotation_degrees=rotation_degrees if media_kind == MediaKind.VIDEO else None,
        color_range=color_range if media_kind == MediaKind.VIDEO else None,
        color_space=color_space if media_kind == MediaKind.VIDEO else None,
        color_transfer=color_transfer if media_kind == MediaKind.VIDEO else None,
        color_primaries=color_primaries if media_kind == MediaKind.VIDEO else None,
        subtitle_stream_count=subtitle_stream_count,
        data_stream_count=data_stream_count,
        tmcd_stream_count=tmcd_stream_count,
        attachment_stream_count=attachment_stream_count,
        data_streams=tuple(data_streams),
    )
