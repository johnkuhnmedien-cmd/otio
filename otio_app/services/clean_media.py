"""Clean Media — lokale ffprobe/ffmpeg-Prüfung und Transcode für Resolve."""

from __future__ import annotations

import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional

from otio_app.analysis_models import CleanMediaEntry, CleanMediaManifest, MediaProbeInfo
from otio_app.models import Project
from otio_app.project_layout import (
    clean_output_path_for_media,
    get_clean_media_output_dir,
    get_folder_clean_manifest_path,
    safe_folder_slug,
)
from otio_app.services.media_utils import (
    is_image_media,
    is_video_media,
    list_media_files,
    probe_duration_seconds,
)

CLEAN_STATUS_OK = "ok"
CLEAN_STATUS_CLEAN = "clean"
CLEAN_STATUS_FAILED = "failed"
CLEAN_STATUS_PENDING = "pending"
CLEAN_STATUS_NEEDS_TRANSCODE = "needs_transcode"

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

ShouldCancel = Callable[[], bool]


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


def transcode_to_clean(original: Path, output_path: Path) -> None:
    """Transkodiert zu H.264/AAC MP4 (Resolve-freundlich)."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    if is_image_media(original):
        command = [
            "ffmpeg",
            "-y",
            "-nostdin",
            "-loop",
            "1",
            "-i",
            str(original),
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-t",
            "5",
            "-movflags",
            "+faststart",
            str(output_path),
        ]
    else:
        command = [
            "ffmpeg",
            "-y",
            "-nostdin",
            "-i",
            str(original),
            "-map",
            "0:v:0?",
            "-map",
            "0:a:0?",
            "-c:v",
            "libx264",
            "-preset",
            "medium",
            "-crf",
            "18",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-b:a",
            "192k",
            "-movflags",
            "+faststart",
            str(output_path),
        ]

    try:
        result = _run_command(command, timeout_sec=3600)
    except FileNotFoundError as exc:
        raise RuntimeError("ffmpeg nicht gefunden") from exc
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError("Transcode-Timeout") from exc

    if result.returncode != 0:
        message = (result.stderr or result.stdout or "").strip()
        raise RuntimeError(message or f"Transcode fehlgeschlagen (Exit {result.returncode})")

    if not output_path.is_file() or output_path.stat().st_size == 0:
        raise RuntimeError("Transcode lieferte keine gültige Ausgabedatei")


def validate_media_file(path: Path) -> CleanMediaEntry:
    """Prüft eine Datei (Probe + Decode) ohne Transcode."""
    entry = CleanMediaEntry(original_path=str(path.resolve()))
    if not path.is_file():
        entry.status = CLEAN_STATUS_FAILED
        entry.error = "Datei nicht gefunden"
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


def process_media_file(
    project: Project,
    folder_name: str,
    media_path: Path,
    *,
    force_transcode: bool = False,
) -> CleanMediaEntry:
    """Validiert und transkodiert bei Bedarf; Original bleibt unverändert."""
    entry = validate_media_file(media_path)
    if entry.status == CLEAN_STATUS_FAILED:
        return entry

    if not entry.needs_transcode and not force_transcode:
        return entry

    output_path = clean_output_path_for_media(
        project.work_dir_path,
        folder_name,
        media_path,
    )
    try:
        transcode_to_clean(media_path, output_path)
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


def folder_manifest_path(project: Project, folder_name: str) -> Path:
    return get_folder_clean_manifest_path(project.work_dir_path, folder_name)


def list_folder_media(project: Project, folder_name: str) -> list[Path]:
    folder_path = project.project_root_path / folder_name
    return list_media_files(folder_path)


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
    """Liefert clean-Pfad wenn vorhanden, sonst Original."""
    try:
        resolved = media_path.expanduser().resolve()
    except OSError:
        resolved = media_path.expanduser()

    clean_root = get_clean_media_output_dir(project.work_dir_path)
    try:
        if clean_root in resolved.parents and resolved.is_file():
            return resolved
    except OSError:
        pass

    manifest = load_clean_media_manifest(folder_manifest_path(project, folder_name))
    entry = _entry_for_original(manifest, resolved)
    if entry is None:
        return resolved

    if entry.status == CLEAN_STATUS_CLEAN and entry.clean_path:
        clean = Path(entry.clean_path).expanduser()
        try:
            clean = clean.resolve()
        except OSError:
            pass
        if clean.is_file():
            return clean
        expected = clean_output_path_for_media(
            project.work_dir_path,
            folder_name,
            Path(entry.original_path),
        )
        if expected.is_file():
            return expected

    if entry.status == CLEAN_STATUS_OK:
        original = Path(entry.original_path).expanduser()
        try:
            original = original.resolve()
        except OSError:
            pass
        if original.is_file():
            return original

    return resolved


def folder_clean_media_ready(project: Project, folder_name: str) -> bool:
    """True wenn Manifest existiert und alle Medien ok/clean sind."""
    manifest = load_clean_media_manifest(folder_manifest_path(project, folder_name))
    if manifest is None:
        return False
    media_files = list_folder_media(project, folder_name)
    if not media_files:
        return True
    if len(manifest.entries) < len(media_files):
        return False
    ok_statuses = {CLEAN_STATUS_OK, CLEAN_STATUS_CLEAN}
    return all(entry.status in ok_statuses for entry in manifest.entries)


def selected_folders_have_clean_media(project: Project) -> bool:
    folders = project.selected_asset_subdirs
    if not folders:
        return False
    return all(folder_clean_media_ready(project, folder) for folder in folders)


def count_folder_clean_status(
    project: Project,
    folder_name: str,
) -> dict[str, int]:
    """Zählt Medien je Status für die UI."""
    counts = {
        CLEAN_STATUS_OK: 0,
        CLEAN_STATUS_CLEAN: 0,
        CLEAN_STATUS_NEEDS_TRANSCODE: 0,
        CLEAN_STATUS_FAILED: 0,
        CLEAN_STATUS_PENDING: 0,
    }
    manifest = load_clean_media_manifest(folder_manifest_path(project, folder_name))
    if manifest is None:
        media_count = len(list_folder_media(project, folder_name))
        counts[CLEAN_STATUS_PENDING] = media_count
        return counts
    for entry in manifest.entries:
        key = entry.status if entry.status in counts else CLEAN_STATUS_PENDING
        counts[key] += 1
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
