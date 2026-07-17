"""Discovery-V2 Video-Transcode: video-h264-v1 (libx264 / yuv420p / 8-Bit)."""

from __future__ import annotations

import os
import shutil
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path

from otio_app.discovery_v2.adapters.ffmpeg_runner import (
    FFmpegRunnerError,
    ffmpeg_encoder_available,
    run_ffmpeg,
)
from otio_app.discovery_v2.adapters.media_probe import (
    MediaProbeAdapterError,
    NormalizedMediaProbe,
    is_hdr_color_profile,
    is_reliable_constant_frame_rate,
    probe_source_media,
)
from otio_app.discovery_v2.adapters.source_hash import compute_sha256_hex
from otio_app.discovery_v2.domain.inventory import MediaKind
from otio_app.discovery_v2.domain.media_intake import VIDEO_H264_PROFILE_VERSION
from otio_app.discovery_v2.paths import assert_path_is_under_discovery_v2

VIDEO_TRANSCODE_TIMEOUT_SEC = 3600
VIDEO_H264_PROFILE_NAME = VIDEO_H264_PROFILE_VERSION
_MIN_FREE_BYTES_FLOOR = 512 * 1024 * 1024


class VideoTranscodeError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True)
class VideoAudioDecision:
    map_audio: bool
    channels: int | None
    policy_result: str


@dataclass(frozen=True)
class VideoTranscodePublishResult:
    source_sha256: str
    output_sha256: str
    working_path: Path
    argv: list[str]
    source_probe: NormalizedMediaProbe
    output_probe: NormalizedMediaProbe
    audio_policy: str
    timecode_policy: str
    rotation_policy: str
    color_policy: str


def evaluate_video_audio_policy(probe: NormalizedMediaProbe) -> VideoAudioDecision:
    count = probe.audio_stream_count
    if count is None:
        count = 1 if probe.audio_codec else 0
    if count == 0:
        return VideoAudioDecision(False, None, "no_audio")
    if count > 1:
        raise VideoTranscodeError(
            "video_multiple_audio_streams_unsupported",
            f"Mehrere Audiostreams ({count}) — Video-Transcode nicht erlaubt.",
        )
    channels = probe.audio_channels
    if channels is None:
        raise VideoTranscodeError(
            "video_audio_channels_unknown",
            "Kanalanzahl des Audiostreams unbekannt.",
        )
    if channels not in {1, 2}:
        raise VideoTranscodeError(
            "video_audio_channels_unsupported",
            f"Kanalanzahl nicht für Alpha-Profil unterstützt: {channels}",
        )
    return VideoAudioDecision(
        True,
        channels,
        f"aac_{'mono' if channels == 1 else 'stereo'}",
    )


def evaluate_video_color_policy(probe: NormalizedMediaProbe) -> str:
    if is_hdr_color_profile(
        color_transfer=probe.color_transfer,
        color_primaries=probe.color_primaries,
    ):
        raise VideoTranscodeError(
            "video_color_profile_unsupported",
            (
                "HDR-/BT.2020-Farbprofil kann mit video-h264-v1 nicht sicher "
                "ohne Tone-Mapping erhalten werden."
            ),
        )
    known = [
        name
        for name, value in (
            ("color_range", probe.color_range),
            ("color_space", probe.color_space),
            ("color_transfer", probe.color_transfer),
            ("color_primaries", probe.color_primaries),
        )
        if value
    ]
    if not known:
        return "unspecified"
    return "passthrough:" + ",".join(known)


def evaluate_source_rotation(probe: NormalizedMediaProbe) -> float | None:
    rot = probe.rotation_degrees
    if rot is None:
        return None
    if abs(rot) < 0.01:
        return 0.0
    return rot


def required_free_bytes(source_size: int) -> int:
    return max(int(source_size) * 2, _MIN_FREE_BYTES_FLOOR)


def assert_sufficient_disk_space(temp_path: Path, source_size: int) -> None:
    target = temp_path.parent if temp_path.parent.exists() else temp_path.anchor or "/"
    try:
        usage = shutil.disk_usage(str(Path(target)))
    except OSError as exc:
        raise VideoTranscodeError(
            "insufficient_disk_space",
            f"Freien Speicherplatz nicht prüfbar: {exc}",
        ) from exc
    need = required_free_bytes(source_size)
    if usage.free < need:
        raise VideoTranscodeError(
            "insufficient_disk_space",
            f"Zu wenig freier Speicher: need={need}, free={usage.free}",
        )


def build_video_h264_argv(
    *,
    source_path: Path,
    temp_path: Path,
    source: NormalizedMediaProbe,
    audio: VideoAudioDecision,
) -> list[str]:
    argv = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "warning",
        "-nostdin",
        "-y",
        "-noautorotate",
        "-i",
        str(source_path),
        "-map",
        "0:v:0",
        "-c:v",
        "libx264",
        "-preset",
        "medium",
        "-crf",
        "18",
        "-profile:v",
        "high",
        "-pix_fmt",
        "yuv420p",
    ]
    # Farbmetadaten nur bei bekannten Source-Werten explizit setzen.
    if source.color_range:
        argv.extend(["-color_range", source.color_range])
    if source.color_space:
        argv.extend(["-colorspace", source.color_space])
    if source.color_transfer:
        argv.extend(["-color_trc", source.color_transfer])
    if source.color_primaries:
        argv.extend(["-color_primaries", source.color_primaries])

    if audio.map_audio:
        argv.extend(
            [
                "-map",
                "0:a:0",
                "-c:a",
                "aac",
                "-b:a",
                "192k",
                "-ar",
                "48000",
            ]
        )
        # Mono/Stereo beibehalten — kein erzwungenes Remix auf Stereo.
        if audio.channels == 1:
            argv.extend(["-ac", "1"])
        elif audio.channels == 2:
            argv.extend(["-ac", "2"])
    else:
        argv.append("-an")

    # Keine blinde Metadaten-Kopie (vermeidet fremde Streams/Tags).
    argv.extend(["-map_metadata", "-1"])
    if source.embedded_timecode:
        # MP4 erzeugt dabei typischerweise einen tmcd-Datenstream — erlaubt
        # und in der Output-Policy geprüft.
        argv.extend(["-metadata", f"timecode={source.embedded_timecode}"])
    else:
        # Keinen Timecode erfinden und keinen tmcd-Track anlegen.
        argv.extend(["-write_tmcd", "0"])

    rot = evaluate_source_rotation(source)
    if rot is not None and abs(rot) >= 0.01:
        # Explizit erhalten (keine Pixelrotation).
        argv.extend(
            [
                "-metadata:s:v:0",
                f"rotate={int(rot) if float(rot).is_integer() else rot}",
            ]
        )

    argv.extend(["-movflags", "+faststart", str(temp_path)])
    return argv


def assert_video_h264_argv(argv: list[str]) -> None:
    joined = " ".join(argv)
    forbidden = ("-vf", "-filter", "-filter_complex", "scale=", "crop=", "pad=", "zoom")
    for token in forbidden:
        if token in argv or token in joined:
            raise VideoTranscodeError(
                "ffmpeg_failed",
                f"Verbotene Filter-/Scale-Option in argv: {token}",
            )
    if "-r" in argv:
        raise VideoTranscodeError("ffmpeg_failed", "Framerate-Zwang (-r) verboten.")
    if "-s" in argv:
        raise VideoTranscodeError("ffmpeg_failed", "Größenzwang (-s) verboten.")
    if "-level" in argv or "-level:v" in argv:
        raise VideoTranscodeError("ffmpeg_failed", "Hartes H.264-Level verboten.")
    if "-c:v" not in argv or argv[argv.index("-c:v") + 1] != "libx264":
        raise VideoTranscodeError("ffmpeg_failed", "Encoder muss libx264 sein.")
    if "-crf" not in argv or argv[argv.index("-crf") + 1] != "18":
        raise VideoTranscodeError("ffmpeg_failed", "CRF muss 18 sein.")
    if "-preset" not in argv or argv[argv.index("-preset") + 1] != "medium":
        raise VideoTranscodeError("ffmpeg_failed", "Preset muss medium sein.")
    if "-profile:v" not in argv or argv[argv.index("-profile:v") + 1] != "high":
        raise VideoTranscodeError("ffmpeg_failed", "Profil muss high sein.")
    if "-pix_fmt" not in argv or argv[argv.index("-pix_fmt") + 1] != "yuv420p":
        raise VideoTranscodeError("ffmpeg_failed", "pix_fmt muss yuv420p sein.")
    if "-noautorotate" not in argv:
        raise VideoTranscodeError("ffmpeg_failed", "-noautorotate fehlt.")


def _duration_tolerance_seconds(probe: NormalizedMediaProbe) -> float:
    num = probe.frame_rate_numerator
    den = probe.frame_rate_denominator
    if num and den and den > 0 and num > 0:
        frame = float(den) / float(num)
        return max(0.15, 3.0 * frame)
    return 0.15


def _mp4_compatible(container: str | None) -> bool:
    text = (container or "").strip().lower()
    if not text:
        return False
    parts = {p.strip() for p in text.replace(".", ",").split(",") if p.strip()}
    return bool(parts & {"mp4", "mov", "isom", "mp42", "mp41", "m4a", "3gp"})


def _h264_codec(codec: str | None) -> bool:
    return (codec or "").strip().lower() in {"h264", "avc", "avc1", "libx264"}


def _rotation_equal(a: float | None, b: float | None) -> bool:
    if a is None and b is None:
        return True
    if a is None:
        return b is not None and abs(b) < 0.01
    if b is None:
        return abs(a) < 0.01
    return abs(a - b) < 0.5


def validate_video_h264_output_policy(
    *,
    source: NormalizedMediaProbe,
    output: NormalizedMediaProbe,
    expected_audio: VideoAudioDecision,
) -> tuple[str, str, str, str]:
    if not _mp4_compatible(output.container_format):
        raise VideoTranscodeError(
            "output_policy_mismatch",
            f"Zielcontainer nicht MP4-kompatibel: {output.container_format}",
        )
    if not _h264_codec(output.video_codec):
        raise VideoTranscodeError(
            "output_policy_mismatch",
            f"Video-Codec nicht H.264: {output.video_codec}",
        )
    if (output.pixel_format or "").lower() not in {"yuv420p", "yuvj420p"}:
        raise VideoTranscodeError(
            "output_policy_mismatch",
            f"Pixel-Format nicht yuv420p: {output.pixel_format}",
        )
    if output.bit_depth != 8:
        raise VideoTranscodeError(
            "output_policy_mismatch",
            f"Bit-Tiefe nicht 8: {output.bit_depth}",
        )
    if source.width != output.width or source.height != output.height:
        raise VideoTranscodeError(
            "output_policy_mismatch",
            (
                f"Dimensionen verändert: {source.width}x{source.height} → "
                f"{output.width}x{output.height}"
            ),
        )
    # Hochformat / Landscape: relative Orientierung der Pixelmaße erhalten.
    if source.width and source.height and output.width and output.height:
        src_portrait = source.height > source.width
        out_portrait = output.height > output.width
        if src_portrait != out_portrait:
            raise VideoTranscodeError(
                "output_policy_mismatch",
                "Seitenverhältnis/Hochformat verändert.",
            )

    # Exakte Framerate-Gleichheit nur bei zuverlässig erkannter CFR-Quelle.
    # VFR / uneinheitliche avg vs r → nur Dauer/Plausibilität prüfen.
    if is_reliable_constant_frame_rate(source) and (
        output.frame_rate_numerator and output.frame_rate_denominator
    ):
        try:
            src_fps = Fraction(
                source.frame_rate_numerator, source.frame_rate_denominator
            )
            out_fps = Fraction(
                output.frame_rate_numerator, output.frame_rate_denominator
            )
        except (ZeroDivisionError, ValueError):
            src_fps = out_fps = None  # type: ignore[assignment]
        if src_fps is not None and out_fps is not None and src_fps != out_fps:
            raise VideoTranscodeError(
                "output_policy_mismatch",
                (
                    "Framerate weicht von der CFR-Source ab: "
                    f"{source.frame_rate_numerator}/{source.frame_rate_denominator} → "
                    f"{output.frame_rate_numerator}/{output.frame_rate_denominator}"
                ),
            )
    elif (
        source.frame_rate_numerator
        and source.frame_rate_denominator
        and output.frame_rate_numerator
        and output.frame_rate_denominator
        and source.avg_frame_rate_numerator is None
        and source.avg_frame_rate_denominator is None
    ):
        # Unit-/Legacy-Probes ohne avg: bisheriges CFR-Verhalten beibehalten.
        try:
            src_fps = Fraction(
                source.frame_rate_numerator, source.frame_rate_denominator
            )
            out_fps = Fraction(
                output.frame_rate_numerator, output.frame_rate_denominator
            )
        except (ZeroDivisionError, ValueError):
            src_fps = out_fps = None  # type: ignore[assignment]
        if src_fps is not None and out_fps is not None and src_fps != out_fps:
            raise VideoTranscodeError(
                "output_policy_mismatch",
                (
                    "Framerate weicht von der Source ab: "
                    f"{source.frame_rate_numerator}/{source.frame_rate_denominator} → "
                    f"{output.frame_rate_numerator}/{output.frame_rate_denominator}"
                ),
            )

    if source.duration_seconds is not None and output.duration_seconds is not None:
        tol = _duration_tolerance_seconds(source)
        if abs(output.duration_seconds - source.duration_seconds) > tol:
            raise VideoTranscodeError(
                "output_policy_mismatch",
                (
                    f"Dauer außerhalb Toleranz: source={source.duration_seconds}, "
                    f"output={output.duration_seconds}, tol={tol}"
                ),
            )

    out_audio_count = output.audio_stream_count
    if out_audio_count is None:
        out_audio_count = 1 if output.audio_codec else 0
    if expected_audio.map_audio:
        if out_audio_count != 1:
            raise VideoTranscodeError(
                "output_policy_mismatch",
                f"Erwarteter einzelner Audiostream fehlt (count={out_audio_count}).",
            )
        if (output.audio_codec or "").lower() != "aac":
            raise VideoTranscodeError(
                "output_policy_mismatch",
                f"Audio-Codec nicht AAC: {output.audio_codec}",
            )
        if (
            expected_audio.channels is not None
            and output.audio_channels is not None
            and output.audio_channels != expected_audio.channels
        ):
            raise VideoTranscodeError(
                "output_policy_mismatch",
                (
                    f"Kanalanzahl verändert: {expected_audio.channels} → "
                    f"{output.audio_channels}"
                ),
            )
        audio_policy = expected_audio.policy_result
    else:
        if out_audio_count:
            raise VideoTranscodeError(
                "output_policy_mismatch",
                "Unerwarteter Audiostream in Transcode-Ausgabe.",
            )
        audio_policy = "no_audio"

    if output.subtitle_stream_count:
        raise VideoTranscodeError(
            "unexpected_subtitle_stream",
            f"Unerwartete Untertitelstreams: {output.subtitle_stream_count}",
        )
    if output.attachment_stream_count:
        raise VideoTranscodeError(
            "unexpected_attachment_stream",
            f"Unerwartete Attachments: {output.attachment_stream_count}",
        )

    src_tc = source.embedded_timecode
    out_tc = output.embedded_timecode
    tmcd_count = output.tmcd_stream_count
    non_tmcd_data = max(0, int(output.data_stream_count or 0) - int(tmcd_count or 0))
    # Falls Probe nur data_stream_count ohne Klassifikation liefert: konservativ.
    if output.data_streams:
        tmcd_count = sum(1 for d in output.data_streams if d.is_tmcd)
        non_tmcd_data = sum(1 for d in output.data_streams if not d.is_tmcd)

    if src_tc:
        if not out_tc or out_tc != src_tc:
            raise VideoTranscodeError(
                "timecode_preservation_failed",
                f"Timecode nicht erhalten: source={src_tc!r}, output={out_tc!r}",
            )
        if tmcd_count != 1:
            raise VideoTranscodeError(
                "timecode_preservation_failed",
                (
                    "Quelle mit Timecode erfordert genau einen erkennbaren "
                    f"tmcd-Stream (gefunden={tmcd_count})."
                ),
            )
        if non_tmcd_data > 0 or (output.data_stream_count or 0) > 1:
            raise VideoTranscodeError(
                "unexpected_data_stream",
                "Zusätzliche oder nicht-tmcd-Datenstreams in der Ausgabe.",
            )
        timecode_policy = "preserved"
    else:
        if out_tc:
            raise VideoTranscodeError(
                "timecode_preservation_failed",
                f"Timecode wurde erfunden: {out_tc!r}",
            )
        if tmcd_count > 0:
            raise VideoTranscodeError(
                "unexpected_timecode_stream",
                "tmcd-Datenstream ohne Source-Timecode.",
            )
        if (output.data_stream_count or 0) > 0 or non_tmcd_data > 0:
            raise VideoTranscodeError(
                "unexpected_data_stream",
                "Unerwartete Datenstreams ohne Source-Timecode.",
            )
        timecode_policy = "absent"

    src_rot = evaluate_source_rotation(source)
    out_rot = evaluate_source_rotation(output)
    if src_rot is None or abs(src_rot) < 0.01:
        if out_rot is not None and abs(out_rot) >= 0.01:
            raise VideoTranscodeError(
                "rotation_preservation_failed",
                f"Rotation wurde erfunden: {out_rot}",
            )
        rotation_policy = "none"
    else:
        if not _rotation_equal(src_rot, out_rot):
            raise VideoTranscodeError(
                "rotation_preservation_failed",
                f"Rotation nicht erhalten: source={src_rot}, output={out_rot}",
            )
        rotation_policy = f"preserved:{src_rot}"

    # Farbe: bekannte Source-Werte müssen gleich bleiben, wenn Output sie meldet.
    for label, src_val, out_val in (
        ("color_range", source.color_range, output.color_range),
        ("color_space", source.color_space, output.color_space),
        ("color_transfer", source.color_transfer, output.color_transfer),
        ("color_primaries", source.color_primaries, output.color_primaries),
    ):
        if src_val and out_val and src_val != out_val:
            raise VideoTranscodeError(
                "output_policy_mismatch",
                f"{label} verändert: {src_val} → {out_val}",
            )
    color_policy = evaluate_video_color_policy(source)

    return audio_policy, timecode_policy, rotation_policy, color_policy


def publish_video_h264_v1(
    *,
    project_root: Path,
    source_path: Path,
    temp_path: Path,
    working_path: Path,
    expected_source_sha256: str,
    source_probe: NormalizedMediaProbe | None = None,
) -> VideoTranscodePublishResult:
    assert_path_is_under_discovery_v2(temp_path, project_root)
    assert_path_is_under_discovery_v2(working_path, project_root)

    if working_path.exists():
        raise VideoTranscodeError(
            "working_media_conflict",
            f"Finale Working-Media-Datei existiert bereits: {working_path.name}",
        )
    if not source_path.is_file() or source_path.is_symlink():
        raise VideoTranscodeError(
            "source_missing",
            f"Quelldatei nicht als reguläre Datei lesbar: {source_path}",
        )
    if not ffmpeg_encoder_available("libx264"):
        raise VideoTranscodeError(
            "ffmpeg_encoder_unavailable",
            "libx264 Encoder ist nicht verfügbar.",
        )

    try:
        source_sha = compute_sha256_hex(source_path)
    except OSError as exc:
        raise VideoTranscodeError("source_hash_mismatch", str(exc)) from exc
    if source_sha != expected_source_sha256.lower():
        raise VideoTranscodeError(
            "source_hash_mismatch",
            "Quell-Hash weicht vom Validation-Hash ab.",
        )

    try:
        probe_in = source_probe or probe_source_media(
            source_path, media_kind=MediaKind.VIDEO
        )
    except MediaProbeAdapterError as exc:
        raise VideoTranscodeError(exc.code, exc.message) from exc

    if (probe_in.media_kind or "").lower() != MediaKind.VIDEO.value:
        raise VideoTranscodeError(
            "unsupported_media_kind",
            f"Medientyp ist kein Video: {probe_in.media_kind}",
        )

    audio = evaluate_video_audio_policy(probe_in)
    color_policy_pre = evaluate_video_color_policy(probe_in)
    _ = color_policy_pre

    try:
        source_size = source_path.stat().st_size
    except OSError as exc:
        raise VideoTranscodeError("source_missing", str(exc)) from exc
    assert_sufficient_disk_space(temp_path, source_size)

    argv = build_video_h264_argv(
        source_path=source_path,
        temp_path=temp_path,
        source=probe_in,
        audio=audio,
    )
    assert_video_h264_argv(argv)

    temp_path.parent.mkdir(parents=True, exist_ok=True)
    working_path.parent.mkdir(parents=True, exist_ok=True)
    if temp_path.exists():
        try:
            temp_path.unlink()
        except OSError as exc:
            raise VideoTranscodeError(
                "ffmpeg_failed",
                f"Temp-Datei konnte nicht entfernt werden: {exc}",
            ) from exc

    try:
        result = run_ffmpeg(argv, timeout_sec=VIDEO_TRANSCODE_TIMEOUT_SEC)
    except FFmpegRunnerError as exc:
        _cleanup(temp_path)
        raise VideoTranscodeError(exc.code, exc.message) from exc

    if result.returncode != 0:
        message = (result.stderr or result.stdout or "").strip()
        _cleanup(temp_path)
        raise VideoTranscodeError(
            "ffmpeg_failed",
            message or f"FFmpeg exit {result.returncode}",
        )

    if not temp_path.is_file() or temp_path.stat().st_size < 1:
        _cleanup(temp_path)
        raise VideoTranscodeError("invalid_output", "Transcode lieferte keine Temp-Datei.")

    try:
        output_sha = compute_sha256_hex(temp_path)
    except OSError as exc:
        _cleanup(temp_path)
        raise VideoTranscodeError("invalid_output", str(exc)) from exc

    try:
        probe_out = probe_source_media(temp_path, media_kind=MediaKind.VIDEO)
    except MediaProbeAdapterError as exc:
        _cleanup(temp_path)
        raise VideoTranscodeError("output_probe_failed", exc.message) from exc
    except Exception as exc:  # noqa: BLE001
        _cleanup(temp_path)
        raise VideoTranscodeError("output_probe_failed", str(exc)) from exc

    try:
        audio_policy, timecode_policy, rotation_policy, color_policy = (
            validate_video_h264_output_policy(
                source=probe_in,
                output=probe_out,
                expected_audio=audio,
            )
        )
    except VideoTranscodeError:
        _cleanup(temp_path)
        raise

    if working_path.exists():
        _cleanup(temp_path)
        raise VideoTranscodeError(
            "working_media_conflict",
            f"Finale Working-Media-Datei existiert bereits: {working_path.name}",
        )

    try:
        os.replace(str(temp_path), str(working_path))
    except OSError as exc:
        _cleanup(temp_path)
        raise VideoTranscodeError(
            "ffmpeg_failed",
            f"Atomare Veröffentlichung fehlgeschlagen: {exc}",
        ) from exc

    return VideoTranscodePublishResult(
        source_sha256=source_sha,
        output_sha256=output_sha,
        working_path=working_path,
        argv=result.argv,
        source_probe=probe_in,
        output_probe=probe_out,
        audio_policy=audio_policy,
        timecode_policy=timecode_policy,
        rotation_policy=rotation_policy,
        color_policy=color_policy,
    )


def _cleanup(path: Path) -> None:
    try:
        if path.exists():
            path.unlink()
    except OSError:
        pass
