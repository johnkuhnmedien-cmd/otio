"""Clean Media — lokale ffprobe/ffmpeg-Prüfung und Transcode für Resolve."""

from __future__ import annotations

import json
import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional

from otio_app.analysis_models import CleanMediaEntry, CleanMediaManifest, MediaProbeInfo
from otio_app.defaults import SUPPLEMENTAL_FOLDER_NAME
from otio_app.models import Project
from otio_app.project_layout import (
    clean_output_path_for_media,
    export_processed_output_path_for_media,
    get_clean_media_output_dir,
    get_folder_clean_manifest_path,
    get_folder_clean_output_dir,
    get_folder_supplemental_dir,
    safe_folder_slug,
)
from otio_app.services.media_utils import (
    is_image_media,
    is_video_media,
    list_media_files,
    probe_duration_seconds,
    probe_leading_black_seconds,
)


def _auto_zoom_enabled(project: Project) -> bool:
    from otio_app.services.clean_media_settings import load_clean_media_settings

    return load_clean_media_settings(project).auto_zoom_fill

CLEAN_STATUS_OK = "ok"
CLEAN_STATUS_CLEAN = "clean"
CLEAN_STATUS_FAILED = "failed"
CLEAN_STATUS_PENDING = "pending"
CLEAN_STATUS_NEEDS_TRANSCODE = "needs_transcode"

_ASSET_NUMBER_RE = re.compile(r"asset[_\s-]*(\d+)", re.IGNORECASE)

_RESOLVE_FRIENDLY_VIDEO_CODECS = frozenset(
    {"h264", "avc", "avc1", "libx264", "mpeg4", "mp4v"}
)
_RESOLVE_FRIENDLY_CONTAINERS = frozenset({".mp4", ".mov", ".m4v"})
_PROBLEMATIC_VIDEO_CODECS = frozenset(
    {
        "hevc",
        "h265",
        "prores",
        "dnxhd",
        "dnxhr",
        "vp9",
        "av1",
        "mjpeg",
        "rawvideo",
        "v210",
        "r210",
    }
)

def media_asset_number(path: Path) -> int | None:
    """Extrahiert Asset-Nummer aus Dateinamen (z. B. …_Asset03… → 3)."""
    match = _ASSET_NUMBER_RE.search(path.name)
    if not match:
        return None
    try:
        return int(match.group(1))
    except ValueError:
        return None


def media_stem_key(path: Path) -> str:
    return safe_folder_slug(path.stem).casefold()


def path_is_readable_file(path: Path) -> bool:
    try:
        if not path.is_file():
            return False
        with path.open("rb") as handle:
            handle.read(1)
        return True
    except OSError:
        return False


def clean_file_is_present(path: Path | None) -> bool:
    """Schnell: Datei vorhanden und nicht leer — ohne ffmpeg."""
    if path is None:
        return False
    try:
        return path_is_readable_file(path) and path.stat().st_size > 0
    except OSError:
        return False


def _probe_has_audio(path: Path) -> bool:
    probe = probe_media(path)
    return bool(probe.audio_codec)


def validate_clean_output(path: Path) -> tuple[bool, str | None]:
    """Prüft, ob eine Clean-Datei für Resolve/DaVinci wirklich nutzbar ist."""
    if not path_is_readable_file(path):
        return False, "Clean-Datei fehlt oder ist nicht lesbar"
    if path.stat().st_size < 1024:
        return False, "Clean-Datei ist leer"

    probe = probe_media(path)
    if not probe.video_codec and not is_image_media(path):
        return False, "Clean-Datei enthält keinen Video-Stream"
    if probe.video_codec and _codec_needs_transcode(probe, path):
        return False, f"Clean-Datei nicht Resolve-freundlich ({probe.video_codec})"

    decode_ok, decode_error = test_decode(path, timeout_sec=180)
    if not decode_ok:
        return False, decode_error or "Decode-Test der Clean-Datei fehlgeschlagen"

    duration = probe.duration_sec
    if not is_image_media(path) and (duration is None or duration <= 0):
        return False, "Clean-Datei ohne gültige Dauer"
    return True, None


def list_clean_files_in_folder(project: Project, folder_name: str) -> list[Path]:
    clean_dir = get_folder_clean_output_dir(project.work_dir_path, folder_name)
    if not clean_dir.is_dir():
        return []
    files: list[Path] = []
    for pattern in ("*.mp4", "*.mov", "*.m4v"):
        try:
            files.extend(clean_dir.rglob(pattern))
        except OSError:
            continue
    return sorted(
        {path.resolve() for path in files if path_is_readable_file(path)},
        key=lambda p: str(p).casefold(),
    )


def _list_clean_files_in_dir(directory: Path) -> list[Path]:
    if not directory.is_dir():
        return []
    files: list[Path] = []
    for pattern in ("*.mp4", "*.mov", "*.m4v"):
        try:
            files.extend(directory.glob(pattern))
        except OSError:
            continue
    return sorted(
        {path.resolve() for path in files if path_is_readable_file(path)},
        key=lambda p: p.name.casefold(),
    )


def find_clean_file_for_media(
    project: Project,
    folder_name: str,
    media_path: Path,
) -> Path | None:
    """Sucht Clean-Datei per erwartetem Pfad, Asset-Nummer oder Stem-Slug."""
    from otio_app.services.clean_media_settings import load_clean_media_settings

    auto_zoom_fill = load_clean_media_settings(project).auto_zoom_fill
    if auto_zoom_fill and not is_image_media(media_path):
        filled = export_processed_output_path_for_media(
            project.work_dir_path,
            folder_name,
            media_path,
            width=project.width,
            height=project.height,
        )
        if path_is_readable_file(filled):
            filled_probe = probe_media(filled)
            if (
                not filled_probe.width
                or not filled_probe.height
                or _probe_matches_target_resolution(
                    filled_probe,
                    project.width,
                    project.height,
                )
            ):
                return filled

    expected = clean_output_path_for_media(
        project.work_dir_path,
        folder_name,
        media_path,
    )
    if path_is_readable_file(expected):
        if not auto_zoom_fill or is_image_media(media_path):
            return expected
        expected_probe = probe_media(expected)
        if (
            not expected_probe.width
            or not expected_probe.height
            or _probe_matches_target_resolution(
                expected_probe,
                project.width,
                project.height,
            )
        ):
            return expected

    asset_number = media_asset_number(media_path)
    stem_key = media_stem_key(media_path)
    # Nur im erwarteten Clean-Unterordner suchen — verhindert Kollisionen
    # zwischen Top-Level und `_supplemental/_provider/`.
    for candidate in _list_clean_files_in_dir(expected.parent):
        if asset_number is not None and media_asset_number(candidate) == asset_number:
            if auto_zoom_fill and not is_image_media(media_path):
                candidate_probe = probe_media(candidate)
                if (
                    candidate_probe.width
                    and candidate_probe.height
                    and not _probe_matches_target_resolution(
                        candidate_probe,
                        project.width,
                        project.height,
                    )
                ):
                    continue
                return candidate
            else:
                return candidate
        if media_stem_key(candidate) == stem_key:
            if auto_zoom_fill and not is_image_media(media_path):
                candidate_probe = probe_media(candidate)
                if (
                    candidate_probe.width
                    and candidate_probe.height
                    and not _probe_matches_target_resolution(
                        candidate_probe,
                        project.width,
                        project.height,
                    )
                ):
                    continue
                return candidate
            else:
                return candidate
    return None


def _run_command(command: list[str], *, timeout_sec: int = 600) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        capture_output=True,
        text=True,
        check=False,
        timeout=timeout_sec,
    )


def probe_media(path: Path) -> MediaProbeInfo:
    """Liest Codec- und Container-Metadaten per ffprobe."""
    info = MediaProbeInfo(
        duration_sec=probe_duration_seconds(path),
        container=path.suffix.lower().lstrip("."),
    )
    try:
        result = _run_command(
            [
                "ffprobe",
                "-v",
                "error",
                "-show_entries",
                "stream=codec_name,codec_type,pix_fmt,width,height",
                "-of",
                "json",
                str(path),
            ],
            timeout_sec=120,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return info

    if result.returncode != 0:
        return info

    try:
        payload = json.loads(result.stdout or "{}")
    except json.JSONDecodeError:
        return info

    for stream in payload.get("streams", []):
        codec_type = (stream.get("codec_type") or "").lower()
        codec_name = (stream.get("codec_name") or "").lower()
        if codec_type == "video" and not info.video_codec:
            info.video_codec = codec_name
            info.pixel_format = stream.get("pix_fmt")
            width = stream.get("width")
            height = stream.get("height")
            if width is not None:
                info.width = int(width)
            if height is not None:
                info.height = int(height)
        elif codec_type == "audio" and not info.audio_codec:
            info.audio_codec = codec_name
    return info


def test_decode(path: Path, *, timeout_sec: int = 300) -> tuple[bool, str | None]:
    """Prüft, ob ffmpeg die Datei vollständig dekodieren kann."""
    try:
        result = _run_command(
            [
                "ffmpeg",
                "-v",
                "error",
                "-nostdin",
                "-i",
                str(path),
                "-f",
                "null",
                "-",
            ],
            timeout_sec=timeout_sec,
        )
    except FileNotFoundError:
        return False, "ffmpeg nicht gefunden"
    except subprocess.TimeoutExpired:
        return False, "Decode-Test Timeout"

    if result.returncode != 0:
        message = (result.stderr or result.stdout or "").strip()
        return False, message or f"Decode fehlgeschlagen (Exit {result.returncode})"
    return True, None


def _codec_needs_transcode(probe: MediaProbeInfo, path: Path) -> bool:
    if is_image_media(path):
        return path.suffix.lower() in {".heic", ".tif", ".tiff"}

    if not is_video_media(path):
        return False

    ext = path.suffix.lower()
    video_codec = (probe.video_codec or "").lower()

    if video_codec in _PROBLEMATIC_VIDEO_CODECS:
        return True
    if ext not in _RESOLVE_FRIENDLY_CONTAINERS:
        return True
    if video_codec and video_codec not in _RESOLVE_FRIENDLY_VIDEO_CODECS:
        return True

    pixel = (probe.pixel_format or "").lower()
    if pixel and ("10" in pixel or "444" in pixel or "rgb" in pixel):
        return True
    return False


def needs_transcode(path: Path, probe: MediaProbeInfo, decode_ok: bool) -> bool:
    if not decode_ok:
        return True
    return _codec_needs_transcode(probe, path)


def transcode_to_clean(
    original: Path,
    output_path: Path,
    *,
    video_filter: str | None = None,
    expected_width: int | None = None,
    expected_height: int | None = None,
) -> None:
    """Transkodiert zu H.264/AAC MP4 (Resolve-freundlich, High Profile)."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    h264_level = "5.1" if (expected_width or 0) >= 3840 or (expected_height or 0) >= 2160 else "4.2"
    video_flags = [
        "-c:v",
        "libx264",
        "-profile:v",
        "high",
        "-level",
        h264_level,
        "-preset",
        "medium",
        "-crf",
        "18",
        "-pix_fmt",
        "yuv420p",
        "-movflags",
        "+faststart",
    ]
    if is_image_media(original):
        command = [
            "ffmpeg",
            "-y",
            "-nostdin",
            "-hide_banner",
            "-loglevel",
            "warning",
            "-loop",
            "1",
            "-i",
            str(original),
            *video_flags,
            "-t",
            "5",
            str(output_path),
        ]
    else:
        probe = probe_media(original)
        if not probe.video_codec:
            raise RuntimeError("Kein Video-Stream in Quelldatei")
        has_audio = bool(probe.audio_codec)
        command = [
            "ffmpeg",
            "-y",
            "-nostdin",
            "-hide_banner",
            "-loglevel",
            "warning",
            "-i",
            str(original),
            "-map",
            "0:v:0",
            "-map_metadata",
            "-1",
            "-reset_timestamps",
            "1",
        ]
        if has_audio:
            command.extend(["-map", "0:a:0", "-c:a", "aac", "-b:a", "192k"])
        else:
            command.append("-an")
        if video_filter:
            command.extend(["-vf", video_filter])
        command.extend([
            "-fflags",
            "+genpts",
            "-avoid_negative_ts",
            "make_zero",
            # Expliziter Start-TC: sonst setzt Resolve manchmal 00:00:00:01
            # und der erste OTIO-Frame (bei 00:00:00:00) wird schwarz.
            "-timecode",
            "00:00:00:00",
            *video_flags,
            str(output_path),
        ])

    try:
        result = _run_command(command, timeout_sec=3600)
    except FileNotFoundError as exc:
        raise RuntimeError("ffmpeg nicht gefunden") from exc
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError("Transcode-Timeout") from exc

    if result.returncode != 0:
        message = (result.stderr or result.stdout or "").strip()
        raise RuntimeError(message or f"Transcode fehlgeschlagen (Exit {result.returncode})")

    if not path_is_readable_file(output_path) or output_path.stat().st_size < 1024:
        raise RuntimeError("Transcode lieferte keine gültige Ausgabedatei")

    # Manche Clean-Encodes starten mit ~1–2 Schwarzframes — wegtrimmen, wenn kurz.
    if not is_image_media(original):
        _trim_tiny_leading_black(output_path)

    valid, validation_error = validate_clean_output(output_path)
    if not valid:
        try:
            output_path.unlink(missing_ok=True)
        except OSError:
            pass
        raise RuntimeError(validation_error or "Clean-Datei nach Transcode ungültig")

    if expected_width and expected_height:
        out_probe = probe_media(output_path)
        if out_probe.width != expected_width or out_probe.height != expected_height:
            try:
                output_path.unlink(missing_ok=True)
            except OSError:
                pass
            actual = (
                f"{out_probe.width}×{out_probe.height}"
                if out_probe.width and out_probe.height
                else "unbekannt"
            )
            raise RuntimeError(
                f"Transcode lieferte {actual}, erwartet {expected_width}×{expected_height}"
            )


def _probe_video_fps(path: Path) -> float | None:
    try:
        result = _run_command(
            [
                "ffprobe",
                "-v",
                "error",
                "-select_streams",
                "v:0",
                "-show_entries",
                "stream=r_frame_rate",
                "-of",
                "default=noprint_wrappers=1:nokey=1",
                str(path),
            ],
            timeout_sec=30,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return None
    if result.returncode != 0:
        return None
    raw = (result.stdout or "").strip()
    if not raw or raw in {"0/0", "N/A"}:
        return None
    try:
        if "/" in raw:
            num_s, den_s = raw.split("/", 1)
            num, den = float(num_s), float(den_s)
            if den <= 0:
                return None
            return num / den
        return float(raw)
    except (TypeError, ValueError, ZeroDivisionError):
        return None


_PBLACK_RE = re.compile(r"pblack:\s*(\d+)", re.IGNORECASE)


def _first_frame_is_black(path: Path, *, min_pblack: int = 90) -> bool:
    """True wenn Frame 0 praktisch schwarz ist (x264-Priming / Clean-Lead-In)."""
    try:
        result = _run_command(
            [
                "ffmpeg",
                "-hide_banner",
                "-nostdin",
                "-i",
                str(path),
                "-vf",
                "select=eq(n\\,0),blackframe=amount=90:threshold=24",
                "-frames:v",
                "1",
                "-f",
                "null",
                "-",
            ],
            timeout_sec=60,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return False
    text = f"{result.stderr or ''}\n{result.stdout or ''}"
    match = _PBLACK_RE.search(text)
    if match is None:
        return False
    try:
        return int(match.group(1)) >= int(min_pblack)
    except ValueError:
        return False


def _trim_tiny_leading_black(path: Path, *, max_trim_seconds: float = 0.15) -> None:
    """Schneidet sehr kurzes führendes Schwarz nach dem Clean-Encode ab.

    libx264 erzeugt oft 1 Schwarzframe am Anfang. ``blackdetect`` mit d=0.04
    verpasst das bei 24fps leicht; daher zusätzlich First-Frame-Check und
    Re-Encode-Trim (nicht ``-c copy``), damit der Schnitt framegenau ist.
    """
    fps = _probe_video_fps(path) or 24.0
    one_frame = 1.0 / max(1.0, fps)
    leading = probe_leading_black_seconds(
        path,
        min_black_duration=min(0.02, one_frame * 0.5),
        pixel_threshold=0.12,
    )
    if leading is None:
        leading = 0.0
    if leading <= 0 and _first_frame_is_black(path):
        leading = one_frame
    if leading <= 0 or leading > max_trim_seconds:
        return
    # Mindestens ein volles Frame trimmen.
    leading = max(leading, one_frame)
    tmp_path = path.with_suffix(path.suffix + ".trimtmp")
    command = [
        "ffmpeg",
        "-y",
        "-nostdin",
        "-hide_banner",
        "-loglevel",
        "warning",
        "-ss",
        f"{leading:.4f}",
        "-i",
        str(path),
        "-map",
        "0:v:0",
        "-map",
        "0:a:0?",
        "-c:v",
        "libx264",
        "-preset",
        "veryfast",
        "-crf",
        "18",
        "-pix_fmt",
        "yuv420p",
        "-c:a",
        "aac",
        "-b:a",
        "192k",
        "-timecode",
        "00:00:00:00",
        "-movflags",
        "+faststart",
        "-avoid_negative_ts",
        "make_zero",
        str(tmp_path),
    ]
    try:
        result = _run_command(command, timeout_sec=600)
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        tmp_path.unlink(missing_ok=True)
        return
    if result.returncode != 0 or not path_is_readable_file(tmp_path):
        tmp_path.unlink(missing_ok=True)
        return
    try:
        tmp_path.replace(path)
    except OSError:
        tmp_path.unlink(missing_ok=True)


def validate_media_file(path: Path) -> CleanMediaEntry:
    """Prüft eine Datei (Probe + Decode) ohne Transcode."""
    entry = CleanMediaEntry(original_path=str(path.resolve()))
    if not path_is_readable_file(path):
        entry.status = CLEAN_STATUS_FAILED
        entry.error = "Datei nicht gefunden oder nicht lesbar (iCloud?)"
        entry.decode_ok = False
        return entry

    probe = probe_media(path)
    entry.probe = probe
    decode_ok, decode_error = test_decode(path)
    entry.decode_ok = decode_ok
    entry.needs_transcode = needs_transcode(path, probe, decode_ok)

    if not decode_ok:
        entry.status = CLEAN_STATUS_NEEDS_TRANSCODE
        entry.error = decode_error
    elif entry.needs_transcode:
        entry.status = CLEAN_STATUS_NEEDS_TRANSCODE
    else:
        entry.status = CLEAN_STATUS_OK
    return entry


def _probe_matches_target_resolution(
    probe: MediaProbeInfo,
    target_width: int,
    target_height: int,
) -> bool:
    return probe.width == target_width and probe.height == target_height


def _zoom_transcode_required(
    project: Project,
    media_path: Path,
    probe: MediaProbeInfo,
) -> bool:
    """True wenn Auto-Zoom aktiv ist und die Pixel-Auflösung nicht exakt passt."""
    if not _auto_zoom_enabled(project) or is_image_media(media_path):
        return False
    if not probe.width or not probe.height:
        return True
    return not _probe_matches_target_resolution(
        probe,
        project.width,
        project.height,
    )


def process_media_file(
    project: Project,
    folder_name: str,
    media_path: Path,
    *,
    force_transcode: bool = False,
) -> CleanMediaEntry:
    """Validiert und transkodiert bei Bedarf; Original bleibt unverändert."""
    if not path_is_readable_file(media_path):
        entry = CleanMediaEntry(
            original_path=str(media_path),
            status=CLEAN_STATUS_FAILED,
            error="Original nicht lesbar (iCloud?)",
            decode_ok=False,
        )
        return entry

    entry = validate_media_file(media_path)
    source_probe = entry.probe or probe_media(media_path)
    from otio_app.services.edit_plan_rules import export_rule_options, load_edit_plan_rules

    zoom_transcode = _zoom_transcode_required(project, media_path, source_probe)
    export_transcode = zoom_transcode
    auto_zoom_fill = _auto_zoom_enabled(project)

    if export_transcode:
        output_path = export_processed_output_path_for_media(
            project.work_dir_path,
            folder_name,
            media_path,
            width=project.width,
            height=project.height,
        )
    else:
        output_path = clean_output_path_for_media(
            project.work_dir_path,
            folder_name,
            media_path,
        )

    if export_transcode and path_is_readable_file(output_path) and not force_transcode:
        valid, validation_error = validate_clean_output(output_path)
        if valid:
            processed_probe = probe_media(output_path)
            resolution_ok = True
            if zoom_transcode and not _probe_matches_target_resolution(
                processed_probe,
                project.width,
                project.height,
            ):
                resolution_ok = False
            if resolution_ok:
                entry.clean_path = str(output_path.resolve())
                entry.status = CLEAN_STATUS_CLEAN
                entry.probe = processed_probe
                entry.error = None
                entry.transcoded_at = entry.transcoded_at or datetime.now(timezone.utc)
                return entry
        if validation_error:
            entry.error = validation_error

    existing_clean = find_clean_file_for_media(project, folder_name, media_path)
    if existing_clean is not None and not force_transcode and not export_transcode:
        valid, validation_error = validate_clean_output(existing_clean)
        if valid:
            clean_probe = probe_media(existing_clean)
            if not _zoom_transcode_required(project, media_path, clean_probe):
                entry.clean_path = str(existing_clean.resolve())
                entry.status = CLEAN_STATUS_CLEAN
                entry.probe = clean_probe
                entry.error = None
                entry.transcoded_at = entry.transcoded_at or datetime.now(timezone.utc)
                return entry
        if existing_clean != output_path:
            try:
                existing_clean.unlink(missing_ok=True)
            except OSError:
                pass
        entry.error = validation_error

    if not entry.needs_transcode and not force_transcode and not export_transcode:
        return entry

    from otio_app.services.otio_media_transform import build_export_video_filter

    if not source_probe.width or not source_probe.height:
        source_probe = probe_media(media_path)

    video_filter, expected_width, expected_height, filter_error = build_export_video_filter(
        source_width=source_probe.width,
        source_height=source_probe.height,
        project=project,
        auto_zoom_fill=auto_zoom_fill,
    )
    if filter_error:
        entry.status = CLEAN_STATUS_FAILED
        entry.error = filter_error
        return entry
    if export_transcode and not video_filter:
        dims = (
            f"{source_probe.width}×{source_probe.height}"
            if source_probe.width and source_probe.height
            else "unbekannt"
        )
        entry.status = CLEAN_STATUS_FAILED
        entry.error = (
            f"Auto-Zoom-Filter konnte nicht erstellt werden "
            f"(Quelle {dims}, Ziel {project.width}×{project.height})"
        )
        return entry
    if zoom_transcode and not source_probe.width and not force_transcode:
        entry.status = CLEAN_STATUS_FAILED
        entry.error = "Quell-Auflösung nicht lesbar — Zoom-Transcode nicht möglich"
        return entry

    try:
        transcode_to_clean(
            media_path,
            output_path,
            video_filter=video_filter,
            expected_width=expected_width if zoom_transcode else None,
            expected_height=expected_height if zoom_transcode else None,
        )
    except OSError as exc:
        entry.status = CLEAN_STATUS_FAILED
        entry.error = str(exc)
        return entry
    except RuntimeError as exc:
        entry.status = CLEAN_STATUS_FAILED
        entry.error = str(exc)
        return entry

    entry.clean_path = str(output_path.resolve())
    entry.status = CLEAN_STATUS_CLEAN
    entry.probe = probe_media(output_path)
    entry.transcoded_at = datetime.now(timezone.utc)
    entry.error = None
    return entry


def load_clean_media_manifest(manifest_path: Path) -> CleanMediaManifest | None:
    if not manifest_path.is_file():
        return None
    try:
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
        return CleanMediaManifest.model_validate(payload)
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError):
        return None


def save_clean_media_manifest(manifest_path: Path, manifest: CleanMediaManifest) -> None:
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(manifest.model_dump_json(indent=2), encoding="utf-8")


def upsert_clean_media_entry(
    project: Project,
    folder_name: str,
    entry: CleanMediaEntry,
) -> CleanMediaManifest:
    """Ersetzt oder ergänzt einen Manifest-Eintrag für dasselbe Original."""
    path = folder_manifest_path(project, folder_name)
    manifest = load_clean_media_manifest(path)
    if manifest is None:
        manifest = CleanMediaManifest(project_id=project.id, folder=folder_name, entries=[])

    try:
        target = str(Path(entry.original_path).expanduser().resolve())
    except OSError:
        target = str(Path(entry.original_path).expanduser())

    remaining: list[CleanMediaEntry] = []
    for existing in manifest.entries:
        try:
            existing_key = str(Path(existing.original_path).expanduser().resolve())
        except OSError:
            existing_key = str(Path(existing.original_path).expanduser())
        if existing_key == target:
            continue
        remaining.append(existing)
    remaining.append(entry)
    updated = manifest.model_copy(
        update={"project_id": project.id, "folder": folder_name, "entries": remaining}
    )
    save_clean_media_manifest(path, updated)
    return updated


def process_and_persist_media_file(
    project: Project,
    folder_name: str,
    media_path: Path,
    *,
    force_transcode: bool = False,
) -> CleanMediaEntry:
    """process_media_file + Manifest-Update — für gezielte Export-Reparaturen."""
    entry = process_media_file(
        project,
        folder_name,
        media_path,
        force_transcode=force_transcode,
    )
    upsert_clean_media_entry(project, folder_name, entry)
    return entry


def folder_manifest_path(project: Project, folder_name: str) -> Path:
    return get_folder_clean_manifest_path(project.work_dir_path, folder_name)


def list_folder_media(
    project: Project,
    folder_name: str,
    *,
    include_supplemental: bool = True,
) -> list[Path]:
    """Medien eines Asset-Ordners — optional inkl. `_supplemental/_provider/`."""
    folder_path = project.project_root_path / folder_name
    media = list(list_media_files(folder_path))
    if include_supplemental:
        media.extend(discover_supplemental_media_paths(project, folder_name))
    deduped: dict[str, Path] = {}
    for path in media:
        try:
            key = str(path.expanduser().resolve())
        except OSError:
            key = str(path)
        deduped[key] = path
    return sorted(deduped.values(), key=lambda path: str(path).casefold())


def discover_supplemental_media_paths(project: Project, folder_name: str) -> list[Path]:
    """Mediendateien unter `{folder}/_supplemental/_{provider}/`."""
    supplemental_root = get_folder_supplemental_dir(project.project_root_path, folder_name)
    if not supplemental_root.is_dir():
        return []
    found: list[Path] = []
    try:
        children = sorted(supplemental_root.iterdir(), key=lambda path: path.name.casefold())
    except OSError:
        return []
    for child in children:
        try:
            if not child.is_dir() or not child.name.startswith("_"):
                continue
        except OSError:
            continue
        found.extend(list_media_files(child))
    return found


def validate_folder(
    project: Project,
    folder_name: str,
    *,
    should_cancel: ShouldCancel | None = None,
    on_progress: Callable[[str, CleanMediaEntry], None] | None = None,
) -> CleanMediaManifest:
    """Nur Prüfung — kein Transcode."""
    entries: list[CleanMediaEntry] = []
    for media_path in list_folder_media(project, folder_name):
        if should_cancel and should_cancel():
            break
        entry = validate_media_file(media_path)
        entries.append(entry)
        if on_progress:
            on_progress("validate", entry)
    manifest = CleanMediaManifest(
        project_id=project.id,
        folder=folder_name,
        entries=entries,
    )
    save_clean_media_manifest(folder_manifest_path(project, folder_name), manifest)
    return manifest


def process_folder(
    project: Project,
    folder_name: str,
    *,
    should_cancel: ShouldCancel | None = None,
    on_progress: Callable[[str, CleanMediaEntry], None] | None = None,
) -> CleanMediaManifest:
    """Prüft und transkodiert alle Medien eines Ordners."""
    entries: list[CleanMediaEntry] = []
    for media_path in list_folder_media(project, folder_name):
        if should_cancel and should_cancel():
            break
        entry = process_media_file(project, folder_name, media_path)
        entries.append(entry)
        if on_progress:
            on_progress("process", entry)
    manifest = CleanMediaManifest(
        project_id=project.id,
        folder=folder_name,
        entries=entries,
    )
    save_clean_media_manifest(folder_manifest_path(project, folder_name), manifest)
    return manifest


def _entry_for_original(
    manifest: CleanMediaManifest | None,
    original_path: Path,
) -> CleanMediaEntry | None:
    if manifest is None:
        return None
    try:
        target = str(original_path.expanduser().resolve())
    except OSError:
        target = str(original_path.expanduser())
    target_name = original_path.name.casefold()
    target_stem = safe_folder_slug(original_path.stem).casefold()

    for entry in manifest.entries:
        if entry.original_path == target:
            return entry
        if entry.clean_path and entry.clean_path == target:
            return entry
        orig = Path(entry.original_path)
        if orig.name.casefold() == target_name:
            return entry
        if safe_folder_slug(orig.stem).casefold() == target_stem:
            return entry
        if entry.clean_path:
            clean = Path(entry.clean_path)
            if clean.name.casefold() == target_name:
                return entry
            if safe_folder_slug(clean.stem).casefold() == target_stem:
                return entry
    return None


def resolve_effective_media_path(
    project: Project,
    folder_name: str,
    media_path: Path,
) -> Path:
    """Liefert clean-Pfad wenn vorhanden und gültig, sonst lesbares Original."""
    try:
        resolved = media_path.expanduser().resolve()
    except OSError:
        resolved = media_path.expanduser()

    clean_candidate = find_clean_file_for_media(project, folder_name, resolved)
    if clean_file_is_present(clean_candidate):
        return clean_candidate

    manifest = load_clean_media_manifest(folder_manifest_path(project, folder_name))
    entry = _entry_for_original(manifest, resolved)
    if entry is not None:
        if entry.status == CLEAN_STATUS_CLEAN:
            for candidate in (
                Path(entry.clean_path) if entry.clean_path else None,
                clean_output_path_for_media(
                    project.work_dir_path,
                    folder_name,
                    Path(entry.original_path),
                ),
            ):
                if candidate is None:
                    continue
                try:
                    candidate = candidate.expanduser().resolve()
                except OSError:
                    candidate = candidate.expanduser()
                if clean_file_is_present(candidate):
                    return candidate

        if entry.status == CLEAN_STATUS_OK:
            original = Path(entry.original_path).expanduser()
            try:
                original = original.resolve()
            except OSError:
                pass
            if path_is_readable_file(original):
                return original

    if path_is_readable_file(resolved):
        return resolved

    if clean_candidate is not None:
        return clean_candidate

    return resolved


def entry_is_ready_on_disk(
    project: Project,
    folder_name: str,
    entry: CleanMediaEntry,
    *,
    strict: bool = False,
) -> bool:
    if entry.status == CLEAN_STATUS_FAILED:
        return False
    if entry.status == CLEAN_STATUS_NEEDS_TRANSCODE or entry.status == CLEAN_STATUS_PENDING:
        return False
    if entry.status == CLEAN_STATUS_CLEAN:
        clean = find_clean_file_for_media(
            project,
            folder_name,
            Path(entry.original_path),
        )
        if clean is None and entry.clean_path:
            clean = Path(entry.clean_path)
        if clean is None:
            return False
        if strict:
            valid, _ = validate_clean_output(clean)
            return valid
        return clean_file_is_present(clean)
    if entry.status == CLEAN_STATUS_OK:
        original = Path(entry.original_path)
        return path_is_readable_file(original)
    return False


def folder_clean_media_ready(
    project: Project,
    folder_name: str,
    *,
    strict: bool = False,
) -> bool:
    """True wenn alle Medien laut Manifest bereit sind (strict = mit ffmpeg-Decode-Test)."""
    media_files = list_folder_media(project, folder_name)
    if not media_files:
        return True

    manifest = load_clean_media_manifest(folder_manifest_path(project, folder_name))
    if manifest is None:
        return False

    entries_by_original: dict[str, CleanMediaEntry] = {}
    for entry in manifest.entries:
        entries_by_original[_path_key(Path(entry.original_path))] = entry

    for media_path in media_files:
        entry = entries_by_original.get(_path_key(media_path))
        if entry is None:
            # Name-/Asset-Fallback nur innerhalb derselben Zone (Primary vs. Supplemental),
            # damit z. B. `_supplemental/_pexels/clip.mp4` nicht den Primary-Eintrag trifft.
            media_supp = SUPPLEMENTAL_FOLDER_NAME in media_path.parts
            media_name = media_path.name.casefold()
            media_number = media_asset_number(media_path)
            for candidate in manifest.entries:
                cand_path = Path(candidate.original_path)
                if (SUPPLEMENTAL_FOLDER_NAME in cand_path.parts) != media_supp:
                    continue
                if cand_path.name.casefold() == media_name:
                    entry = candidate
                    break
                if media_number is not None and media_asset_number(cand_path) == media_number:
                    entry = candidate
                    break
        if entry is None:
            return False
        if not entry_is_ready_on_disk(project, folder_name, entry, strict=strict):
            return False
    return True


def audit_folder_clean_media(
    project: Project,
    folder_name: str,
    *,
    strict: bool = True,
) -> list[dict[str, str]]:
    """Diagnose je Medium — ffmpeg-Tests nur bei strict=True (nicht beim Seitenladen)."""
    issues: list[dict[str, str]] = []
    manifest = load_clean_media_manifest(folder_manifest_path(project, folder_name))

    for media_path in list_folder_media(project, folder_name):
        name = media_path.name
        resolved = resolve_effective_media_path(project, folder_name, media_path)
        if not path_is_readable_file(resolved):
            issues.append(
                {
                    "media": name,
                    "issue": "Keine lesbare Datei gefunden",
                    "resolved_path": str(resolved),
                }
            )
            continue
        if strict and resolved.suffix.lower() in {".mp4", ".mov", ".m4v"}:
            valid, validation_error = validate_clean_output(resolved)
            if not valid:
                issues.append(
                    {
                        "media": name,
                        "issue": validation_error or "Datei nicht Resolve-ready",
                        "resolved_path": str(resolved),
                    }
                )
                continue

        entry = _entry_for_original(manifest, media_path) if manifest else None
        if entry and entry.status == CLEAN_STATUS_NEEDS_TRANSCODE:
            issues.append(
                {
                    "media": name,
                    "issue": "Transcode noch ausstehend",
                    "resolved_path": str(resolved),
                }
            )
    return issues


def repair_folder_manifest(
    project: Project,
    folder_name: str,
    *,
    should_cancel: ShouldCancel | None = None,
    on_progress: Callable[[str, CleanMediaEntry], None] | None = None,
) -> CleanMediaManifest:
    """Synchronisiert Manifest mit Dateien auf Disk; transkodiert fehlende/ungültige Clean-Dateien."""
    return process_folder(
        project,
        folder_name,
        should_cancel=should_cancel,
        on_progress=on_progress,
    )


def selected_folders_have_clean_media(project: Project) -> bool:
    """Schneller Workflow-Check — Manifest + alle Disk-Medien (inkl. `_supplemental/`).

    Vergleicht bewusst mit `list_folder_media`, damit neue Supplement-Dateien
    den Status „bereit“ zurücksetzen, auch wenn ältere Manifest-Einträge ok sind.
    """
    folders = project.selected_asset_subdirs
    if not folders:
        return False
    return all(
        folder_clean_media_ready(project, folder_name, strict=False)
        for folder_name in folders
    )


def _manifest_entry_keys(manifest: CleanMediaManifest) -> set[str]:
    keys: set[str] = set()
    for entry in manifest.entries:
        try:
            keys.add(str(Path(entry.original_path).expanduser().resolve()))
        except OSError:
            keys.add(str(Path(entry.original_path)))
    return keys


def _path_key(path: Path) -> str:
    try:
        return str(path.expanduser().resolve())
    except OSError:
        return str(path)


def count_folder_clean_status(
    project: Project,
    folder_name: str,
) -> dict[str, int]:
    """Zählt Medien je Status für die UI (inkl. Dateien auf Disk ohne Manifest-Eintrag)."""
    counts = {
        CLEAN_STATUS_OK: 0,
        CLEAN_STATUS_CLEAN: 0,
        CLEAN_STATUS_NEEDS_TRANSCODE: 0,
        CLEAN_STATUS_FAILED: 0,
        CLEAN_STATUS_PENDING: 0,
    }
    media_files = list_folder_media(project, folder_name)
    manifest = load_clean_media_manifest(folder_manifest_path(project, folder_name))
    if manifest is None:
        counts[CLEAN_STATUS_PENDING] = len(media_files)
        return counts

    for entry in manifest.entries:
        key = entry.status if entry.status in counts else CLEAN_STATUS_PENDING
        counts[key] += 1

    known = _manifest_entry_keys(manifest)
    for media_path in media_files:
        if _path_key(media_path) not in known:
            counts[CLEAN_STATUS_PENDING] += 1
    return counts


def manifest_needs_processing(manifest: CleanMediaManifest | None) -> bool:
    if manifest is None:
        return True
    return any(
        entry.status in {CLEAN_STATUS_NEEDS_TRANSCODE, CLEAN_STATUS_FAILED, CLEAN_STATUS_PENDING}
        for entry in manifest.entries
    )


def original_path_key(path: Path) -> str:
    return safe_folder_slug(path.stem).casefold()
