"""OTIO-Export ausschließlich aus der technisch aufgelösten Timeline (fail-closed)."""

from __future__ import annotations

from pathlib import Path

import opentimelineio as otio

from otio_app.models import Project
from otio_app.services.media_utils import (
    is_image_media,
    is_video_media,
    probe_duration_seconds,
    probe_media_timing,
)
from otio_app.services.still_image_export_style import ensure_styled_still_for_export
from otio_app.services.without_voiceover_enhanced.cut_plan_options import (
    load_cut_plan_options,
)
from otio_app.services.without_voiceover_enhanced.io_utils import load_model
from otio_app.services.without_voiceover_enhanced.local_media_service import is_http_url
from otio_app.services.without_voiceover_enhanced.media_hold import (
    MediaHoldError,
    ensure_still_hold_video,
)
from otio_app.services.without_voiceover_enhanced.models import (
    ResolvedShot,
    ResolvedTimelineDocument,
)
from otio_app.services.without_voiceover_enhanced.paths import (
    assert_enhanced_work_root,
    exports_dir,
    resolved_timeline_path,
    segment_timings_path,
)
from otio_app.services.without_voiceover_enhanced.script_lock_service import (
    load_locked_script,
)


class EnhancedOtioExportError(RuntimeError):
    pass


def _time_range(duration_sec: float, rate: float, *, start_sec: float = 0.0) -> otio.opentime.TimeRange:
    return otio.opentime.TimeRange(
        start_time=otio.opentime.RationalTime.from_seconds(start_sec, rate),
        duration=otio.opentime.RationalTime.from_seconds(duration_sec, rate),
    )


def _assert_local_file(path: str, *, label: str) -> Path:
    text = str(path or "").strip()
    if not text:
        raise EnhancedOtioExportError(f"{label}: leere Medienreferenz.")
    if is_http_url(text) or text.lower().startswith(("http://", "https://")):
        raise EnhancedOtioExportError(
            f"{label}: OTIO darf keine Web-URL enthalten ({text})."
        )
    local = Path(text).expanduser()
    if not local.is_file():
        raise EnhancedOtioExportError(f"{label}: lokale Datei fehlt: {local}")
    return local.resolve()


def _validate_video_file(path: Path, *, label: str, fps: float) -> tuple[float, float, float]:
    """Returns (available_start, duration, rate)."""
    timing = probe_media_timing(path, default_rate=fps)
    duration = timing.duration_sec
    if duration is None:
        duration = probe_duration_seconds(path)
    if duration is None or duration <= 0:
        raise EnhancedOtioExportError(
            f"{label}: ungültige/fehlende Videodauer · {path}"
        )
    # Auflösung / Videostream prüfen
    try:
        import json
        import subprocess

        result = subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-select_streams",
                "v:0",
                "-show_entries",
                "stream=width,height,codec_type",
                "-of",
                "json",
                str(path),
            ],
            capture_output=True,
            check=False,
            timeout=60,
        )
        payload = json.loads((result.stdout or b"").decode("utf-8", errors="replace") or "{}")
        streams = payload.get("streams") or []
        if not streams:
            raise EnhancedOtioExportError(
                f"{label}: kein gültiger Videostream · {path}"
            )
        width = int(streams[0].get("width") or 0)
        height = int(streams[0].get("height") or 0)
        if width <= 0 or height <= 0:
            raise EnhancedOtioExportError(
                f"{label}: ungültige Auflösung {width}x{height} · {path}"
            )
    except EnhancedOtioExportError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise EnhancedOtioExportError(
            f"{label}: Videoprüfung fehlgeschlagen ({exc}) · {path}"
        ) from exc
    return float(timing.start_sec or 0.0), float(duration), float(timing.rate or fps)


def _ensure_shot_media_for_export(
    project: Project,
    shot: ResolvedShot,
    *,
    fps: float,
) -> tuple[Path, float, float, float, float]:
    """Validiert Shot-Medien; liefert path, avail_start, source_start, source_end, rate."""
    label = f"{shot.shot_id} / {shot.asset_id}"
    if not (shot.resolved_media_path or "").strip():
        raise EnhancedOtioExportError(
            f"{label}: resolved_media_path fehlt — Timeline erneut auflösen."
        )
    path = _assert_local_file(shot.resolved_media_path, label=label)

    # Optionales Still-Styling nur wenn Original noch Bild ist.
    options = load_cut_plan_options(project)
    if is_image_media(path):
        styled = ensure_styled_still_for_export(
            project,
            shot.folder_name or "_enhanced",
            path,
            enabled=bool(options.still_image_style_enabled),
            zoom=float(options.still_image_zoom),
            background_style=str(options.still_image_background_style),
        )
        path = _assert_local_file(str(styled), label=f"{label} (styled still)")
        timeline_dur = max(
            0.01, float(shot.timeline_end_seconds - shot.timeline_start_seconds)
        )
        try:
            path = ensure_still_hold_video(
                project, path, duration_seconds=timeline_dur, fps=fps
            )
        except MediaHoldError as exc:
            raise EnhancedOtioExportError(f"{label}: {exc}") from exc
        path = _assert_local_file(str(path), label=f"{label} (still hold)")
        return path, 0.0, 0.0, timeline_dur, fps

    if not is_video_media(path):
        raise EnhancedOtioExportError(
            f"{label}: Medientyp weder Video noch Bild · {path}"
        )

    avail_start, media_dur, rate = _validate_video_file(path, label=label, fps=fps)
    source_start = float(shot.source_start_seconds)
    source_end = float(shot.source_end_seconds)
    if source_end <= source_start:
        raise EnhancedOtioExportError(
            f"{label}: source_end ({source_end}) muss > source_start "
            f"({source_start}) sein."
        )
    # Falls ResolvedShot Source relativ (ohne Embedded) gespeichert hat, aber
    # Datei Embedded-TC hat: angleichen, wenn source_start < avail_start.
    if source_start + 1e-6 < avail_start:
        shift = avail_start - source_start
        source_start += shift
        source_end += shift
    avail_end = avail_start + media_dur
    if source_start < avail_start - 1e-6 or source_end > avail_end + 1e-6:
        raise EnhancedOtioExportError(
            f"{label}: Source-Range außerhalb der realen verfügbaren Range "
            f"(source {source_start:.3f}–{source_end:.3f}, "
            f"available {avail_start:.3f}–{avail_end:.3f}) · {path}"
        )
    return path, avail_start, source_start, source_end, rate


def validate_resolved_timeline_for_production(
    project: Project,
    resolved: ResolvedTimelineDocument,
) -> list[str]:
    """Unabhängig von resolved.errors — reale Medien-/Range-Prüfung."""
    errors: list[str] = []
    fps = float(resolved.fps or project.fps or 25.0)
    options = load_cut_plan_options(project)
    preroll = float(resolved.voiceover_preroll_sec or 0.0)
    postroll = float(resolved.voiceover_postroll_sec or 0.0)

    # Narration erwartet?
    locked = load_locked_script(project)
    timings_path = segment_timings_path(project)
    narration_expected = bool(
        locked
        and locked.segments
        and timings_path.is_file()
    )
    if narration_expected and not resolved.audio_segments:
        errors.append(
            "Produktions-OTIO erwartet Narrationsclips, audio_segments ist leer."
        )

    if resolved.audio_segments:
        first_audio = min(
            resolved.audio_segments, key=lambda a: a.timeline_start_seconds
        )
        # Intro kann bei 0 starten; non-intro sollte preroll haben.
        # Prüfe: wenn preroll > 0, muss mindestens ein Audio bei >= preroll starten
        # und die Timeline bei 0 mit Video beginnen.
        if preroll > 0:
            non_zero = [
                a
                for a in resolved.audio_segments
                if a.timeline_start_seconds + 1e-6 >= preroll
            ]
            if not non_zero and all(
                a.timeline_start_seconds < 1e-6 for a in resolved.audio_segments
            ):
                errors.append(
                    f"Voice-over-Vorlauf {preroll:.2f}s ist gesetzt, aber alle "
                    "Narrationsclips starten bei 0.0s."
                )
        for audio in resolved.audio_segments:
            try:
                _assert_local_file(
                    audio.audio_path, label=f"Audio {audio.segment_id}"
                )
            except EnhancedOtioExportError as exc:
                errors.append(str(exc))

    if not resolved.shots:
        errors.append("Keine Video-Shots in der aufgelösten Timeline.")

    for shot in resolved.shots:
        try:
            path, avail_start, source_start, source_end, _rate = (
                _ensure_shot_media_for_export(project, shot, fps=fps)
            )
        except EnhancedOtioExportError as exc:
            errors.append(str(exc))
            continue
        source_dur = source_end - source_start
        timeline_dur = shot.timeline_end_seconds - shot.timeline_start_seconds
        if source_dur <= 0:
            errors.append(
                f"{shot.shot_id}: source_duration muss positiv sein."
            )
        # Hold-Videos decken Timeline ab; sonst Source <= Timeline ok.
        if (
            shot.hold_mode != "freeze_video"
            and "hold_cache" not in str(path)
            and timeline_dur > source_dur + 1e-3
        ):
            errors.append(
                f"{shot.shot_id}: Timeline ({timeline_dur:.3f}s) länger als "
                f"Source ({source_dur:.3f}s) ohne Hold — "
                f"Asset {shot.asset_id} · {path}"
            )

    if postroll > 0 and resolved.shots and resolved.audio_segments:
        last_audio_end = max(
            a.timeline_end_seconds + a.pause_after_seconds
            for a in resolved.audio_segments
        )
        last_shot_end = max(s.timeline_end_seconds for s in resolved.shots)
        if last_shot_end + 1e-3 < last_audio_end + postroll:
            errors.append(
                f"Nachlauf {postroll:.2f}s nicht vollständig abgedeckt "
                f"(Audio-Ende {last_audio_end:.2f}s, Video-Ende {last_shot_end:.2f}s)."
            )

    return errors


def _collect_target_urls(timeline: otio.schema.Timeline) -> list[str]:
    urls: list[str] = []
    for track in timeline.tracks:
        for item in track:
            media = getattr(item, "media_reference", None)
            if media is None:
                continue
            target = getattr(media, "target_url", None)
            if target:
                urls.append(str(target))
    return urls


def export_otio_from_resolved_timeline(
    project: Project,
    *,
    basename: str = "enhanced_timeline",
    allow_errors: bool = False,
) -> Path:
    """Exportiert die aufgelöste Timeline als OTIO.

    ``allow_errors=True`` ist ein Test-/Diagnose-Modus (Lücken erlaubt).
    Produktions-Export (`allow_errors=False`) ist fail-closed inkl. realer
    Medien-/Source-Range-Prüfung — unabhängig von ``resolved.errors``.
    """
    assert_enhanced_work_root(project)
    resolved = load_model(resolved_timeline_path(project), ResolvedTimelineDocument)
    if resolved is None:
        raise EnhancedOtioExportError("Aufgelöste Timeline fehlt — kein OTIO-Export.")

    fps = float(resolved.fps or project.fps or 25.0)

    if not allow_errors:
        if resolved.errors:
            raise EnhancedOtioExportError(
                "Aufgelöste Timeline enthält Fehler: " + "; ".join(resolved.errors)
            )
        gate_errors = validate_resolved_timeline_for_production(project, resolved)
        if gate_errors:
            raise EnhancedOtioExportError(
                "Produktions-Export blockiert (Medien/Range-Gate): "
                + "; ".join(gate_errors)
            )

    timeline = otio.schema.Timeline(name=f"{project.name} enhanced")
    video_track = otio.schema.Track(name="Video", kind=otio.schema.TrackKind.Video)
    audio_track = otio.schema.Track(name="Narration", kind=otio.schema.TrackKind.Audio)

    cursor = 0.0
    for shot in sorted(resolved.shots, key=lambda s: s.timeline_start_seconds):
        if shot.timeline_start_seconds > cursor + 1e-6:
            gap = shot.timeline_start_seconds - cursor
            video_track.append(
                otio.schema.Gap(source_range=_time_range(gap, fps))
            )
        try:
            media_path, avail_start, source_start, source_end, rate = (
                _ensure_shot_media_for_export(project, shot, fps=fps)
            )
        except EnhancedOtioExportError:
            if not allow_errors:
                raise
            # Test-Modus: Lücke statt ungültigem Clip.
            gap = max(
                0.01, shot.timeline_end_seconds - shot.timeline_start_seconds
            )
            video_track.append(otio.schema.Gap(source_range=_time_range(gap, fps)))
            cursor = shot.timeline_end_seconds
            continue

        source_duration = source_end - source_start
        avail_duration = max(
            source_duration,
            float(shot.resolved_media_duration_seconds or source_duration),
        )
        # available_range muss die Source enthalten.
        media_ref = otio.schema.ExternalReference(
            target_url=str(media_path),
            available_range=_time_range(
                avail_duration, rate, start_sec=avail_start
            ),
        )
        # Re-probe for accurate available_range on real file.
        file_avail_start, file_dur, file_rate = _validate_video_file(
            media_path, label=f"{shot.shot_id}", fps=fps
        )
        media_ref = otio.schema.ExternalReference(
            target_url=str(media_path),
            available_range=_time_range(
                file_dur, file_rate, start_sec=file_avail_start
            ),
        )
        clip = otio.schema.Clip(
            name=shot.shot_id,
            media_reference=media_ref,
            source_range=_time_range(
                source_duration, file_rate, start_sec=source_start
            ),
        )
        clip.metadata["asset_id"] = shot.asset_id
        clip.metadata["resolved_media_path"] = str(media_path)
        if shot.hold_mode:
            clip.metadata["hold_mode"] = shot.hold_mode
        video_track.append(clip)
        cursor = shot.timeline_end_seconds

    audio_cursor = 0.0
    for segment in resolved.audio_segments:
        if segment.timeline_start_seconds > audio_cursor + 1e-6:
            gap = segment.timeline_start_seconds - audio_cursor
            audio_track.append(
                otio.schema.Gap(source_range=_time_range(gap, fps))
            )
        try:
            audio_path = _assert_local_file(
                segment.audio_path, label=f"Audio {segment.segment_id}"
            )
        except EnhancedOtioExportError:
            if not allow_errors:
                raise
            gap = max(
                0.01,
                segment.timeline_end_seconds - segment.timeline_start_seconds,
            )
            audio_track.append(otio.schema.Gap(source_range=_time_range(gap, fps)))
            audio_cursor = segment.timeline_end_seconds
            continue
        duration = segment.timeline_end_seconds - segment.timeline_start_seconds
        source_start = float(getattr(segment, "source_start_seconds", 0.0) or 0.0)
        clip_name = segment.segment_id
        split_label = str(getattr(segment, "split_label", "") or "").strip()
        if split_label:
            clip_name = f"{segment.segment_id}:{split_label}"
        audio_dur = probe_duration_seconds(audio_path) or max(duration, 0.01)
        clip = otio.schema.Clip(
            name=clip_name,
            media_reference=otio.schema.ExternalReference(
                target_url=str(audio_path),
                available_range=_time_range(audio_dur, fps, start_sec=0.0),
            ),
            source_range=_time_range(max(0.01, duration), fps, start_sec=source_start),
        )
        audio_track.append(clip)
        audio_cursor = segment.timeline_end_seconds
        if segment.pause_after_seconds > 0:
            audio_track.append(
                otio.schema.Gap(
                    source_range=_time_range(segment.pause_after_seconds, fps)
                )
            )
            audio_cursor += segment.pause_after_seconds

    timeline.tracks.append(video_track)
    timeline.tracks.append(audio_track)
    if allow_errors:
        timeline.metadata["enhanced_export_mode"] = "test_gaps"
    else:
        timeline.metadata["enhanced_export_mode"] = "production"

    for url in _collect_target_urls(timeline):
        if is_http_url(url) or url.lower().startswith(("http://", "https://")):
            raise EnhancedOtioExportError(
                f"OTIO enthält verbotene Web-URL als Medienreferenz: {url}"
            )

    out_dir = exports_dir(project)
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{basename}.otio"
    otio.adapters.write_to_file(timeline, str(out_path))
    return out_path
