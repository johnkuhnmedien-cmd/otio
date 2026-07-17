"""Discovery-V2 Remux: Containerwechsel per Stream Copy (kein Re-Encode)."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from otio_app.discovery_v2.adapters.ffmpeg_runner import (
    FFmpegRunnerError,
    run_ffmpeg,
)
from otio_app.discovery_v2.adapters.media_probe import (
    MediaProbeAdapterError,
    NormalizedMediaProbe,
    probe_source_media,
)
from otio_app.discovery_v2.adapters.source_hash import compute_sha256_hex
from otio_app.discovery_v2.domain.inventory import MediaKind
from otio_app.discovery_v2.domain.media_intake import IntakeAction
from otio_app.discovery_v2.paths import assert_path_is_under_discovery_v2


_FRIENDLY_VIDEO_CODECS = frozenset(
    {"h264", "avc", "avc1", "libx264", "mpeg4", "mp4v"}
)
_FRIENDLY_CONTAINERS = frozenset({".mp4", ".mov", ".m4v"})
_ALLOWED_PIXEL_FORMATS = frozenset({"yuv420p", "yuvj420p"})
_ALLOWED_AUDIO_COPY_CODECS = frozenset({"aac", "mp3"})
REMUX_TIMEOUT_SEC = 600


class MediaRemuxError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True)
class RemuxAudioDecision:
    map_audio: bool
    audio_codec: str | None
    policy_result: str


@dataclass(frozen=True)
class RemuxGateResult:
    ok: bool
    error_code: str | None
    error_message: str | None
    audio: RemuxAudioDecision | None = None


@dataclass(frozen=True)
class RemuxPublishResult:
    source_sha256: str
    output_sha256: str
    working_path: Path
    argv: list[str]
    source_probe: NormalizedMediaProbe
    output_probe: NormalizedMediaProbe
    audio_policy: str
    timecode_policy: str


def _normalize_codec(value: str | None) -> str:
    return (value or "").strip().lower()


def _normalize_extension(extension: str | None, relative_path: str = "") -> str:
    raw = (extension or "").strip().lower()
    if raw and not raw.startswith("."):
        raw = f".{raw}"
    if not raw and relative_path:
        raw = Path(relative_path).suffix.lower()
    return raw


def _container_token(extension: str, container_format: str | None) -> str:
    if extension in _FRIENDLY_CONTAINERS:
        return extension
    fmt = (container_format or "").strip().lower()
    if not fmt:
        return extension
    parts = {p.strip() for p in fmt.split(",") if p.strip()}
    for candidate in (".mp4", ".mov", ".m4v"):
        token = candidate.lstrip(".")
        if token in parts or candidate in parts:
            return candidate
    return extension


def evaluate_remux_audio_policy(probe: NormalizedMediaProbe) -> RemuxAudioDecision:
    count = probe.audio_stream_count
    if count is None:
        count = 1 if probe.audio_codec else 0
    codec = _normalize_codec(probe.audio_codec)
    if count == 0 or not codec:
        return RemuxAudioDecision(
            map_audio=False,
            audio_codec=None,
            policy_result="no_audio",
        )
    if count > 1:
        raise MediaRemuxError(
            "remux_multiple_audio_streams_unsupported",
            f"Mehrere Audiostreams ({count}) — Remux nicht erlaubt.",
        )
    if codec not in _ALLOWED_AUDIO_COPY_CODECS:
        raise MediaRemuxError(
            "remux_audio_codec_unsupported",
            f"Audiocodec nicht für Stream-Copy freigegeben: {codec}",
        )
    return RemuxAudioDecision(
        map_audio=True,
        audio_codec=codec,
        policy_result=f"copy_{codec}",
    )


def evaluate_remux_gate(
    *,
    planned_action: IntakeAction | str,
    media_kind: str | None,
    video_codec: str | None,
    pixel_format: str | None,
    bit_depth: int | None,
    extension: str | None,
    container_format: str | None,
    source_relative_path: str = "",
    validation_status: str | None = "probe_succeeded",
    probe: NormalizedMediaProbe | None = None,
) -> RemuxGateResult:
    """Prüft, ob Remux ausschließlich ein Containerproblem lösen darf."""
    action = (
        planned_action.value
        if isinstance(planned_action, IntakeAction)
        else str(planned_action)
    )
    if action != IntakeAction.REMUX.value:
        return RemuxGateResult(
            False,
            "stale_plan",
            f"Geplante Aktion ist nicht remux: {action}",
        )
    if (validation_status or "").strip().lower() != "probe_succeeded":
        return RemuxGateResult(
            False,
            "unsupported_codec",
            f"Validation-Status nicht probe_succeeded: {validation_status}",
        )
    if (media_kind or "").strip().lower() != MediaKind.VIDEO.value:
        return RemuxGateResult(
            False,
            "unsupported_codec",
            f"Remux nur für Video, erhalten: {media_kind}",
        )

    codec = _normalize_codec(video_codec)
    if codec not in _FRIENDLY_VIDEO_CODECS:
        return RemuxGateResult(
            False,
            "unsupported_codec",
            f"Video-Codec nicht für Remux freigegeben: {codec or 'unbekannt'}",
        )

    pix = (pixel_format or "").strip().lower() or None
    if pix is None:
        return RemuxGateResult(
            False,
            "unsupported_pixel_format",
            "Pixel-Format unbekannt — Remux nicht zulässig.",
        )
    if pix not in _ALLOWED_PIXEL_FORMATS:
        return RemuxGateResult(
            False,
            "unsupported_pixel_format",
            f"Pixel-Format nicht für Remux freigegeben: {pix}",
        )

    if bit_depth is None:
        return RemuxGateResult(
            False,
            "unsupported_bit_depth",
            "Bit-Tiefe unbekannt — Remux nicht zulässig.",
        )
    if bit_depth != 8:
        return RemuxGateResult(
            False,
            "unsupported_bit_depth",
            f"Bit-Tiefe nicht für Remux freigegeben: {bit_depth}",
        )

    ext = _normalize_extension(extension, source_relative_path)
    container = _container_token(ext, container_format)
    if container in _FRIENDLY_CONTAINERS:
        return RemuxGateResult(
            False,
            "unsupported_container",
            (
                f"Container ist bereits Copy-geeignet ({container}); "
                "Remux löst nur Containerprobleme."
            ),
        )

    audio: RemuxAudioDecision | None = None
    if probe is not None:
        try:
            audio = evaluate_remux_audio_policy(probe)
        except MediaRemuxError as exc:
            return RemuxGateResult(False, exc.code, exc.message)

    return RemuxGateResult(True, None, None, audio=audio)


def build_remux_argv(
    *,
    source_path: Path,
    temp_path: Path,
    map_audio: bool,
    embedded_timecode: str | None = None,
) -> list[str]:
    argv = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "warning",
        "-nostdin",
        "-y",
        "-i",
        str(source_path),
        "-map",
        "0:v:0",
        "-c:v",
        "copy",
    ]
    if map_audio:
        argv.extend(["-map", "0:a:0", "-c:a", "copy"])
    argv.extend(
        [
            "-map_metadata",
            "0",
        ]
    )
    # Timecode deterministisch: vorhanden → explizit setzen; fehlend → leeren
    # (verhindert „erfundenen“ Format-Timecode nach Containerwechsel).
    if embedded_timecode:
        argv.extend(["-metadata", f"timecode={embedded_timecode}"])
    else:
        argv.extend(["-metadata", "timecode="])
    argv.extend(
        [
            "-movflags",
            "+faststart",
            str(temp_path),
        ]
    )
    return argv


def assert_remux_argv_is_stream_copy(argv: list[str]) -> None:
    joined = " ".join(argv)
    if "libx264" in joined or "-c:v libx264" in joined:
        raise MediaRemuxError("ffmpeg_failed", "Re-Encoding ist für Remux verboten.")
    if "-vf" in argv or "-filter" in argv or "-filter_complex" in argv:
        raise MediaRemuxError("ffmpeg_failed", "Filter sind für Remux verboten.")
    if "-c:v" not in argv or "copy" not in argv:
        raise MediaRemuxError("ffmpeg_failed", "Remux erfordert -c:v copy.")
    if "-c:a" in argv:
        # Muss copy sein, kein aac encode
        try:
            idx = argv.index("-c:a")
            if argv[idx + 1] != "copy":
                raise MediaRemuxError(
                    "ffmpeg_failed",
                    "Audio-Re-Encoding ist für Remux verboten.",
                )
        except (ValueError, IndexError) as exc:
            raise MediaRemuxError(
                "ffmpeg_failed",
                "Ungültige Audio-Stream-Copy-Argumente.",
            ) from exc


def _duration_tolerance_seconds(probe: NormalizedMediaProbe) -> float:
    num = probe.frame_rate_numerator
    den = probe.frame_rate_denominator
    if num and den and den > 0 and num > 0:
        frame = float(den) / float(num)
        return max(0.10, 2.0 * frame)
    return 0.10


def _mp4_compatible(container: str | None) -> bool:
    text = (container or "").strip().lower()
    if not text:
        return False
    parts = {p.strip() for p in text.replace(".", ",").split(",") if p.strip()}
    return bool(parts & {"mp4", "mov", "isom", "mp42", "mp41", "m4a", "3gp"})


def validate_remux_output_policy(
    *,
    source: NormalizedMediaProbe,
    output: NormalizedMediaProbe,
    expected_audio: RemuxAudioDecision,
) -> tuple[str, str]:
    """Prüft Output-Policy. Liefert (audio_policy, timecode_policy)."""
    if not _mp4_compatible(output.container_format):
        raise MediaRemuxError(
            "output_policy_mismatch",
            f"Zielcontainer nicht MP4-kompatibel: {output.container_format}",
        )

    src_codec = _normalize_codec(source.video_codec)
    out_codec = _normalize_codec(output.video_codec)
    if not out_codec or out_codec != src_codec:
        raise MediaRemuxError(
            "output_policy_mismatch",
            f"Video-Codec verändert: {src_codec} → {out_codec}",
        )

    if (source.pixel_format or "").lower() != (output.pixel_format or "").lower():
        raise MediaRemuxError(
            "output_policy_mismatch",
            (
                f"Pixel-Format verändert: {source.pixel_format} → "
                f"{output.pixel_format}"
            ),
        )
    if source.bit_depth != output.bit_depth:
        raise MediaRemuxError(
            "output_policy_mismatch",
            f"Bit-Tiefe verändert: {source.bit_depth} → {output.bit_depth}",
        )
    if source.width != output.width or source.height != output.height:
        raise MediaRemuxError(
            "output_policy_mismatch",
            (
                f"Dimensionen verändert: {source.width}x{source.height} → "
                f"{output.width}x{output.height}"
            ),
        )

    if (
        source.frame_rate_numerator
        and source.frame_rate_denominator
        and output.frame_rate_numerator
        and output.frame_rate_denominator
        and (
            source.frame_rate_numerator != output.frame_rate_numerator
            or source.frame_rate_denominator != output.frame_rate_denominator
        )
    ):
        raise MediaRemuxError(
            "output_policy_mismatch",
            "Framerate hat sich durch Remux verändert.",
        )

    src_dur = source.duration_seconds
    out_dur = output.duration_seconds
    if src_dur is not None and out_dur is not None:
        tol = _duration_tolerance_seconds(source)
        if abs(out_dur - src_dur) > tol:
            raise MediaRemuxError(
                "output_policy_mismatch",
                (
                    f"Dauer außerhalb Toleranz: source={src_dur}, "
                    f"output={out_dur}, tol={tol}"
                ),
            )

    out_audio_count = output.audio_stream_count
    if out_audio_count is None:
        out_audio_count = 1 if output.audio_codec else 0

    if expected_audio.map_audio:
        if out_audio_count != 1:
            raise MediaRemuxError(
                "output_policy_mismatch",
                (
                    "Erwarteter einzelner Audiostream fehlt oder "
                    f"Anzahl falsch: {out_audio_count}"
                ),
            )
        if _normalize_codec(output.audio_codec) != expected_audio.audio_codec:
            raise MediaRemuxError(
                "output_policy_mismatch",
                (
                    f"Audio-Codec verändert: {expected_audio.audio_codec} → "
                    f"{output.audio_codec}"
                ),
            )
        audio_policy = expected_audio.policy_result
    else:
        if out_audio_count and output.audio_codec:
            raise MediaRemuxError(
                "output_policy_mismatch",
                "Unerwarteter Audiostream in Remux-Ausgabe.",
            )
        audio_policy = "no_audio"

    src_tc = source.embedded_timecode
    out_tc = output.embedded_timecode
    if src_tc:
        if out_tc != src_tc:
            raise MediaRemuxError(
                "timecode_preservation_failed",
                (
                    f"Timecode nicht erhalten: source={src_tc!r}, "
                    f"output={out_tc!r}"
                ),
            )
        timecode_policy = "preserved"
    else:
        if out_tc:
            raise MediaRemuxError(
                "timecode_preservation_failed",
                f"Timecode wurde erfunden: {out_tc!r}",
            )
        timecode_policy = "absent"

    return audio_policy, timecode_policy


def publish_remux_mp4(
    *,
    project_root: Path,
    source_path: Path,
    temp_path: Path,
    working_path: Path,
    expected_source_sha256: str,
    source_probe: NormalizedMediaProbe | None = None,
) -> RemuxPublishResult:
    """Remuxt nach Temp, validiert Policy, veröffentlicht atomar."""
    assert_path_is_under_discovery_v2(temp_path, project_root)
    assert_path_is_under_discovery_v2(working_path, project_root)

    if working_path.exists():
        raise MediaRemuxError(
            "working_media_conflict",
            f"Finale Working-Media-Datei existiert bereits: {working_path.name}",
        )
    if not source_path.is_file() or source_path.is_symlink():
        raise MediaRemuxError(
            "source_missing",
            f"Quelldatei nicht als reguläre Datei lesbar: {source_path}",
        )

    try:
        source_sha = compute_sha256_hex(source_path)
    except OSError as exc:
        raise MediaRemuxError("source_hash_mismatch", str(exc)) from exc
    if source_sha != expected_source_sha256.lower():
        raise MediaRemuxError(
            "source_hash_mismatch",
            "Quell-Hash weicht vom Validation-Hash ab.",
        )

    try:
        probe_in = source_probe or probe_source_media(
            source_path, media_kind=MediaKind.VIDEO
        )
    except MediaProbeAdapterError as exc:
        raise MediaRemuxError(exc.code, exc.message) from exc

    audio = evaluate_remux_audio_policy(probe_in)
    argv = build_remux_argv(
        source_path=source_path,
        temp_path=temp_path,
        map_audio=audio.map_audio,
        embedded_timecode=probe_in.embedded_timecode,
    )
    assert_remux_argv_is_stream_copy(argv)

    temp_path.parent.mkdir(parents=True, exist_ok=True)
    working_path.parent.mkdir(parents=True, exist_ok=True)
    if temp_path.exists():
        try:
            temp_path.unlink()
        except OSError as exc:
            raise MediaRemuxError(
                "ffmpeg_failed",
                f"Temp-Datei konnte nicht entfernt werden: {exc}",
            ) from exc

    try:
        result = run_ffmpeg(argv, timeout_sec=REMUX_TIMEOUT_SEC)
    except FFmpegRunnerError as exc:
        raise MediaRemuxError(exc.code, exc.message) from exc

    if result.returncode != 0:
        message = (result.stderr or result.stdout or "").strip()
        _cleanup(temp_path)
        raise MediaRemuxError(
            "ffmpeg_failed",
            message or f"FFmpeg exit {result.returncode}",
        )

    if not temp_path.is_file() or temp_path.stat().st_size < 1:
        _cleanup(temp_path)
        raise MediaRemuxError("invalid_output", "Remux lieferte keine Temp-Datei.")

    try:
        output_sha = compute_sha256_hex(temp_path)
    except OSError as exc:
        _cleanup(temp_path)
        raise MediaRemuxError("invalid_output", str(exc)) from exc

    try:
        probe_out = probe_source_media(temp_path, media_kind=MediaKind.VIDEO)
    except MediaProbeAdapterError as exc:
        _cleanup(temp_path)
        raise MediaRemuxError("output_probe_failed", exc.message) from exc
    except Exception as exc:  # noqa: BLE001
        _cleanup(temp_path)
        raise MediaRemuxError("output_probe_failed", str(exc)) from exc

    try:
        audio_policy, timecode_policy = validate_remux_output_policy(
            source=probe_in,
            output=probe_out,
            expected_audio=audio,
        )
    except MediaRemuxError:
        _cleanup(temp_path)
        raise

    if working_path.exists():
        _cleanup(temp_path)
        raise MediaRemuxError(
            "working_media_conflict",
            f"Finale Working-Media-Datei existiert bereits: {working_path.name}",
        )

    try:
        os.replace(str(temp_path), str(working_path))
    except OSError as exc:
        _cleanup(temp_path)
        raise MediaRemuxError(
            "ffmpeg_failed",
            f"Atomare Veröffentlichung fehlgeschlagen: {exc}",
        ) from exc

    return RemuxPublishResult(
        source_sha256=source_sha,
        output_sha256=output_sha,
        working_path=working_path,
        argv=result.argv,
        source_probe=probe_in,
        output_probe=probe_out,
        audio_policy=audio_policy,
        timecode_policy=timecode_policy,
    )


def _cleanup(path: Path) -> None:
    try:
        if path.exists():
            path.unlink()
    except OSError:
        pass
