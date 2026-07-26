"""OTIO-Export ausschließlich aus der technisch aufgelösten Timeline (fail-closed)."""

from __future__ import annotations

from pathlib import Path

import opentimelineio as otio

from otio_app.analysis_models import TimelineItem
from otio_app.models import Project
from otio_app.services.media_utils import (
    is_image_media,
    is_video_media,
    probe_duration_seconds,
    probe_media_timing,
)
from otio_app.services.opening_title_renderer import (
    build_opening_title_item,
    ensure_opening_titles_rendered,
)
from otio_app.services.otio_exporter import _build_v2_title_track
from otio_app.services.still_image_export_style import ensure_styled_still_for_export
from otio_app.services.without_voiceover_enhanced.cut_plan_options import (
    CutPlanOptions,
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
from otio_app.services.without_voiceover_enhanced.portable_export import (
    PortableExportError,
    assert_portable_target_urls,
    lookup_packaged_path,
    package_dir_for_export,
    relative_media_target_url,
    stage_media_into_package,
    write_media_manifest,
    write_package_readme,
)
from otio_app.services.without_voiceover_enhanced.script_lock_service import (
    load_locked_script,
)
from otio_app.services.without_voiceover_enhanced.timeline_resolver import (
    AssetCatalog,
    _is_intro_folder,
    build_asset_catalog,
    lookup_catalog_entry,
)


class EnhancedOtioExportError(RuntimeError):
    pass


def _time_range(duration_sec: float, rate: float, *, start_sec: float = 0.0) -> otio.opentime.TimeRange:
    """Sekunden → OTIO-TimeRange auf **ganzzahligen** Frames (Resolve-sicher).

    Fractional RationalTimes (z.B. start value 1.992 @24fps) führen in Resolve
    leicht zu einem Off-by-one: available wird auf Frame 2 gerundet, source auf
    Frame 1 → ein schwarzer/offline erster Frame, obwohl die Quelldatei ok ist.
    """
    media_rate = float(rate) if float(rate) > 0 else 25.0
    start_rt = otio.opentime.RationalTime.from_seconds(float(start_sec), media_rate)
    end_rt = otio.opentime.RationalTime.from_seconds(
        float(start_sec) + float(duration_sec), media_rate
    )
    start_frames = int(round(start_rt.value))
    end_frames = int(round(end_rt.value))
    if end_frames <= start_frames:
        end_frames = start_frames + 1
    return otio.opentime.TimeRange(
        start_time=otio.opentime.RationalTime(start_frames, media_rate),
        duration=otio.opentime.RationalTime(end_frames - start_frames, media_rate),
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


def _is_still_hold_shot(shot: ResolvedShot, path: Path) -> bool:
    """True wenn Resolve das Foto bereits zu still_hold_*.mp4 gemacht hat."""
    hold = str(shot.hold_mode or "").strip().lower()
    if hold in {"freeze_video", "still_hold"}:
        return True
    name = path.name.lower()
    return name.startswith("still_hold_") or "hold_cache" in path.parts


def _original_still_path_for_export(
    project: Project,
    shot: ResolvedShot,
    *,
    path: Path,
    catalog: AssetCatalog | None,
    fps: float,
) -> Path | None:
    """Original-JPEG/PNG für Stil — auch wenn resolved_media_path schon Hold-MP4 ist."""
    if is_image_media(path):
        return path
    if not _is_still_hold_shot(shot, path):
        return None
    cat = catalog if catalog is not None else build_asset_catalog(project, fps=fps)
    entry, _err = lookup_catalog_entry(cat, str(shot.asset_id or ""))
    if entry is None:
        return None
    original = Path(str(entry.get("path") or "")).expanduser()
    try:
        original = original.resolve()
    except OSError:
        pass
    if original.is_file() and is_image_media(original):
        return original
    return None


def _export_styled_still_hold(
    project: Project,
    shot: ResolvedShot,
    *,
    image_path: Path,
    fps: float,
    label: str,
) -> Path:
    """Still-Style → Hold-MP4 (Resolve braucht Video mit Dauer)."""
    options = load_cut_plan_options(project)
    styled = ensure_styled_still_for_export(
        project,
        shot.folder_name or "_enhanced",
        image_path,
        enabled=bool(options.still_image_style_enabled),
        zoom=float(options.still_image_zoom),
        background_style=str(options.still_image_background_style),
    )
    styled_path = _assert_local_file(str(styled), label=f"{label} (styled still)")
    timeline_dur = max(
        0.01, float(shot.timeline_end_seconds - shot.timeline_start_seconds)
    )
    try:
        hold = ensure_still_hold_video(
            project,
            styled_path,
            duration_seconds=timeline_dur,
            fps=fps,
            dynamic_zoom=bool(options.still_image_dynamic_zoom_enabled),
            zoom_factor=float(options.still_image_dynamic_zoom_factor),
        )
    except MediaHoldError as exc:
        raise EnhancedOtioExportError(f"{label}: {exc}") from exc
    return _assert_local_file(str(hold), label=f"{label} (still hold)")


def _ensure_shot_media_for_export(
    project: Project,
    shot: ResolvedShot,
    *,
    fps: float,
    catalog: AssetCatalog | None = None,
) -> tuple[Path, float, float, float, float]:
    """Validiert Shot-Medien; liefert path, avail_start, source_start, source_end, rate."""
    label = f"{shot.shot_id} / {shot.asset_id}"
    if not (shot.resolved_media_path or "").strip():
        raise EnhancedOtioExportError(
            f"{label}: resolved_media_path fehlt — Timeline erneut auflösen."
        )
    path = _assert_local_file(shot.resolved_media_path, label=label)

    # Still-Style / dynamischer Zoom: Originalbild nutzen — auch wenn Resolve
    # schon still_hold.mp4 geschrieben hat (sonst greifen Settings nicht).
    options = load_cut_plan_options(project)
    original_still = _original_still_path_for_export(
        project, shot, path=path, catalog=catalog, fps=fps
    )
    restyle = original_still is not None and (
        bool(options.still_image_style_enabled)
        or bool(options.still_image_dynamic_zoom_enabled)
    )
    if restyle and original_still is not None:
        hold_path = _export_styled_still_hold(
            project,
            shot,
            image_path=original_still,
            fps=fps,
            label=label,
        )
        timeline_dur = max(
            0.01, float(shot.timeline_end_seconds - shot.timeline_start_seconds)
        )
        return hold_path, 0.0, 0.0, timeline_dur, fps

    # Ohne Stil/Dynamic: Bild → Hold (oder bereits vorhandenes Hold-MP4 behalten).
    if is_image_media(path):
        timeline_dur = max(
            0.01, float(shot.timeline_end_seconds - shot.timeline_start_seconds)
        )
        try:
            path = ensure_still_hold_video(
                project,
                path,
                duration_seconds=timeline_dur,
                fps=fps,
                dynamic_zoom=bool(options.still_image_dynamic_zoom_enabled),
                zoom_factor=float(options.still_image_dynamic_zoom_factor),
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
    source_span = source_end - source_start
    # Content-Offset relativ zur beim Resolve gespeicherten Available-Start
    # beibehalten, wenn die Datei einen anderen Embedded-TC/PTS-Start hat.
    # (Früher: source auf avail_start schieben → Head-Trim/Offset verloren.)
    shot_avail = float(getattr(shot, "resolved_available_start_seconds", 0.0) or 0.0)
    content_offset = max(0.0, source_start - shot_avail)
    if abs(avail_start - shot_avail) > 1e-6:
        source_start = avail_start + content_offset
        source_end = source_start + source_span
    elif source_start + 1e-6 < avail_start:
        source_start = avail_start + content_offset
        source_end = source_start + source_span
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
    """Unabhängig von resolved.errors — reale Medien-/Range-/Kapitel-Prüfung."""
    errors: list[str] = []
    fps = float(resolved.fps or project.fps or 25.0)
    preroll = float(resolved.voiceover_preroll_sec or 0.0)
    postroll = float(resolved.voiceover_postroll_sec or 0.0)
    frame = 1.0 / fps if fps > 0 else 0.04

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

    if resolved.chapters:
        for chapter in resolved.chapters:
            if abs(
                chapter.chapter_audio_start
                - (chapter.chapter_video_start + chapter.preroll_seconds)
            ) > 1e-3:
                errors.append(
                    f"Kapitel {chapter.chapter_id}: Audio-Start "
                    f"{chapter.chapter_audio_start:.3f}s entspricht nicht "
                    f"Video-Start+Vorlauf."
                )
            if abs(
                chapter.chapter_video_end
                - (chapter.chapter_audio_end + chapter.postroll_seconds)
            ) > 1e-3:
                errors.append(
                    f"Kapitel {chapter.chapter_id}: Video-Ende "
                    f"{chapter.chapter_video_end:.3f}s entspricht nicht "
                    f"Audio-Ende+Nachlauf."
                )
            if chapter.visual_gap_count > 0:
                errors.append(
                    f"Kapitel {chapter.chapter_id}: {chapter.visual_gap_count} "
                    "visuelle Lücke(n) größer als ein Frame."
                )
            if chapter.visual_overlap_count > 0:
                errors.append(
                    f"Kapitel {chapter.chapter_id}: "
                    f"{chapter.visual_overlap_count} visuelle Überlappung(en) "
                    "größer als ein Frame."
                )
            chapter_shots = [
                s
                for s in resolved.shots
                if (s.chapter_id or s.folder_name) == chapter.chapter_id
            ]
            if not chapter_shots:
                errors.append(
                    f"Kapitel {chapter.chapter_id}: kein visueller Shot."
                )
                continue
            first = min(chapter_shots, key=lambda s: s.timeline_start_seconds)
            last = max(chapter_shots, key=lambda s: s.timeline_end_seconds)
            if first.timeline_start_seconds > chapter.chapter_video_start + 1e-3:
                errors.append(
                    f"Kapitel {chapter.chapter_id}: erster Shot "
                    f"{first.shot_id} deckt Vorlauf nicht ab."
                )
            if last.timeline_end_seconds + 1e-3 < chapter.chapter_video_end:
                errors.append(
                    f"Kapitel {chapter.chapter_id}: letzter Shot "
                    f"{last.shot_id} deckt Nachlauf nicht ab."
                )
            if str(chapter.last_shot_id or "").startswith("bridge_"):
                errors.append(
                    f"Kapitel {chapter.chapter_id}: last_shot_id ist Bridge "
                    f"({chapter.last_shot_id}) — Nachlauf muss am Inhalts-Shot hängen."
                )
            if chapter.postroll_hold_shot_id and str(
                chapter.postroll_hold_shot_id
            ).startswith("bridge_"):
                errors.append(
                    f"Kapitel {chapter.chapter_id}: postroll_hold_shot_id ist Bridge "
                    f"({chapter.postroll_hold_shot_id})."
                )
            from otio_app.services.without_voiceover_enhanced.timeline_resolver import (
                _is_intro_folder,
            )

            # Intro: feste 4s / 5–8s Hüllen — nicht gegen Kapitel-Settings prüfen.
            if not _is_intro_folder(chapter.chapter_id):
                if abs(chapter.preroll_seconds - preroll) > 1e-3:
                    errors.append(
                        f"Kapitel {chapter.chapter_id}: preroll "
                        f"{chapter.preroll_seconds:.2f}s ≠ Settings {preroll:.2f}s."
                    )
                if abs(chapter.postroll_seconds - postroll) > 1e-3:
                    errors.append(
                        f"Kapitel {chapter.chapter_id}: postroll "
                        f"{chapter.postroll_seconds:.2f}s ≠ Settings {postroll:.2f}s."
                    )
    elif resolved.audio_segments and preroll > 0:
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

    ordered = sorted(
        resolved.shots, key=lambda s: (s.timeline_start_seconds, s.shot_id)
    )
    for prev, curr in zip(ordered, ordered[1:]):
        delta = curr.timeline_start_seconds - prev.timeline_end_seconds
        chapter = curr.chapter_id or prev.chapter_id or prev.folder_name or "?"
        if delta > frame + 1e-9:
            errors.append(
                f"Visuelle Lücke in Kapitel {chapter}: "
                f"{prev.timeline_end_seconds:.3f}s–{curr.timeline_start_seconds:.3f}s "
                f"({delta:.3f}s) zwischen {prev.shot_id} ({prev.asset_id}) und "
                f"{curr.shot_id} ({curr.asset_id})."
            )
        elif delta < -(frame + 1e-9):
            # may_overlap_pause erlaubt keine Video-Clip-Überlappung ohne Transition.
            errors.append(
                f"Visuelle Überlappung in Kapitel {chapter}: "
                f"{curr.timeline_start_seconds:.3f}s–{prev.timeline_end_seconds:.3f}s "
                f"({abs(delta):.3f}s) zwischen {prev.shot_id} ({prev.asset_id}) und "
                f"{curr.shot_id} ({curr.asset_id})."
            )

    catalog = build_asset_catalog(project, fps=fps)
    for shot in resolved.shots:
        if bool(getattr(shot, "is_placeholder", False)) or bool(shot.open_gap):
            errors.append(
                f"{shot.shot_id}: Placeholder/offener Gap "
                f"({shot.coverage_gap_id or '—'}) — Produktions-Export gesperrt."
            )
            continue
        try:
            path, avail_start, source_start, source_end, _rate = (
                _ensure_shot_media_for_export(
                    project, shot, fps=fps, catalog=catalog
                )
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
            shot.hold_mode not in {"freeze_video", "placeholder_slate"}
            and "hold_cache" not in str(path)
            and timeline_dur > source_dur + 1e-3
        ):
            errors.append(
                f"{shot.shot_id}: Timeline ({timeline_dur:.3f}s) länger als "
                f"Source ({source_dur:.3f}s) ohne Hold — "
                f"Asset {shot.asset_id} · {path}"
            )

    if (
        not resolved.chapters
        and postroll > 0
        and resolved.shots
        and resolved.audio_segments
    ):
        last_audio_end = max(
            a.timeline_end_seconds for a in resolved.audio_segments
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


def build_enhanced_folder_title_items(
    project: Project,
    resolved: ResolvedTimelineDocument,
    options: CutPlanOptions | None = None,
) -> list[TimelineItem]:
    """Opening-Title-Items pro Nicht-Intro-Kapitel (absolute Timeline-Position)."""
    opts = options if options is not None else load_cut_plan_options(project)
    if not opts.folder_title_enabled:
        return []
    chapters = list(resolved.chapters or [])
    if not chapters:
        return []

    font_size = (
        float(opts.folder_title_font_size)
        if opts.folder_title_font_size and opts.folder_title_font_size > 0
        else None
    )
    items: list[TimelineItem] = []
    for chapter in chapters:
        chapter_id = str(chapter.chapter_id or chapter.folder_name or "").strip()
        folder_name = str(chapter.folder_name or chapter.chapter_id or "").strip()
        if not chapter_id and not folder_name:
            continue
        if _is_intro_folder(chapter_id) or _is_intro_folder(folder_name):
            continue
        section_id = chapter_id or folder_name
        item = build_opening_title_item(
            folder_name=folder_name or section_id,
            voice_file="",
            section_id=section_id,
            work_dir=project.language_work_dir_path,
            project=project,
            requested_font_family=str(opts.folder_title_font or "Phosphate"),
            duration_sec=float(opts.folder_title_duration_sec),
            font_size_px=font_size,
            fade_in_sec=float(opts.folder_title_fade_in_sec),
            fade_out_sec=float(opts.folder_title_fade_out_sec),
        )
        # Am Opening-Shot: first_shot Start, sonst Kapitel-Videoanfang (inkl. Vorlauf).
        start = max(0.0, float(chapter.chapter_video_start))
        first_id = str(chapter.first_shot_id or "").strip()
        if first_id:
            for shot in resolved.shots:
                if shot.shot_id == first_id:
                    start = max(0.0, float(shot.timeline_start_seconds))
                    break
        duration = max(0.1, float(item.duration_sec))
        items.append(
            item.model_copy(
                update={
                    "timeline_in_sec": start,
                    "timeline_out_sec": start + duration,
                    "duration_sec": duration,
                    "final_duration_sec": duration,
                }
            )
        )
    return items


def _render_folder_title_items(
    project: Project,
    resolved: ResolvedTimelineDocument,
    *,
    fail_closed: bool,
) -> tuple[list[TimelineItem], list[str]]:
    """Baut und rendert Ordner-Titel; fail-closed wenn aktiviert und Render fehlt."""
    options = load_cut_plan_options(project)
    items = build_enhanced_folder_title_items(project, resolved, options)
    if not items:
        return [], []
    rendered, notes = ensure_opening_titles_rendered(project, items)
    titles = [item for item in rendered if item.type == "opening_title"]
    ready: list[TimelineItem] = []
    for item in titles:
        media = str(item.rendered_media_path or item.resolved_media_path or "").strip()
        if media and Path(media).is_file():
            ready.append(item)
            continue
        message = (
            f"Ordner-Titel für {item.folder_name or item.section_id}: "
            "gerenderte Mediendatei fehlt."
        )
        if fail_closed:
            raise EnhancedOtioExportError(message)
        notes.append(message)
    return ready, notes


def _folder_title_media_paths(items: list[TimelineItem]) -> list[tuple[Path, str, str]]:
    """Staging-Tupel (path, asset_id, kind) für portable Exports."""
    out: list[tuple[Path, str, str]] = []
    for item in items:
        media = str(item.rendered_media_path or item.resolved_media_path or "").strip()
        if not media:
            continue
        path = Path(media)
        if not path.is_file():
            continue
        asset_id = f"title_{item.section_id or item.folder_name}"
        out.append((path.resolve(), asset_id, "opening_title"))
    return out


def export_otio_from_resolved_timeline(
    project: Project,
    *,
    basename: str = "enhanced_timeline",
    allow_errors: bool = False,
    resolved: ResolvedTimelineDocument | None = None,
    timeline_name: str | None = None,
) -> Path:
    """Exportiert die aufgelöste Timeline als OTIO.

    ``allow_errors=True`` ist ein Test-/Diagnose-Modus (Lücken erlaubt).
    Produktions-Export (`allow_errors=False`) ist fail-closed inkl. realer
    Medien-/Source-Range-Prüfung — unabhängig von ``resolved.errors``.

    Optional ``resolved``: In-Memory-Dokument (z. B. gefiltertes Intro) —
    die Datei ``resolved_timeline.json`` wird dann nicht angefasst.
    """
    assert_enhanced_work_root(project)
    if resolved is None:
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

    timeline = otio.schema.Timeline(
        name=timeline_name or f"{project.name} enhanced"
    )
    video_track = otio.schema.Track(name="Video", kind=otio.schema.TrackKind.Video)
    audio_track = otio.schema.Track(name="Narration", kind=otio.schema.TrackKind.Audio)
    catalog = build_asset_catalog(project, fps=fps)

    cursor = 0.0
    for shot in sorted(resolved.shots, key=lambda s: s.timeline_start_seconds):
        if shot.timeline_start_seconds > cursor + 1e-6:
            gap = shot.timeline_start_seconds - cursor
            video_track.append(
                otio.schema.Gap(source_range=_time_range(gap, fps))
            )
        try:
            media_path, avail_start, source_start, source_end, rate = (
                _ensure_shot_media_for_export(
                    project, shot, fps=fps, catalog=catalog
                )
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
        if shot.asset_fit:
            clip.metadata["asset_fit"] = shot.asset_fit
        if shot.asset_fit_reason:
            clip.metadata["begruendung"] = shot.asset_fit_reason
        if shot.cut_alignment:
            clip.metadata["cut_alignment"] = shot.cut_alignment
        if shot.coverage_gap_id:
            clip.metadata["coverage_gap_id"] = shot.coverage_gap_id
        if shot.open_gap:
            clip.metadata["open_gap"] = True
        if bool(getattr(shot, "is_placeholder", False)) or shot.open_gap:
            clip.metadata["placeholder"] = True
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

    title_items, title_notes = _render_folder_title_items(
        project,
        resolved,
        fail_closed=not allow_errors,
    )
    title_track = _build_v2_title_track(title_items, rate=fps) if title_items else None

    timeline.tracks.append(video_track)
    if title_track is not None:
        timeline.tracks.append(title_track)
        timeline.metadata["opening_title_count"] = len(title_items)
    timeline.tracks.append(audio_track)
    if title_notes:
        timeline.metadata["folder_title_notes"] = list(title_notes)
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


def export_portable_otio_package(
    project: Project,
    *,
    basename: str = "enhanced_timeline",
    allow_errors: bool = False,
) -> Path:
    """Produktions-Export als portables Paket mit eindeutigen Mediendateinamen.

    Struktur::

        <basename>_package/
            timeline.otio
            media/<unique-files>
            media_manifest.json
            README.md

    ``allow_errors=True`` ist nur für Diagnose gedacht und schreibt trotzdem
    kein Paket mit Lücken-Medien — bei Fehlern wird blockiert, außer wenn
    explizit Gaps-Export über ``export_otio_from_resolved_timeline`` genutzt wird.
    """
    if allow_errors:
        raise EnhancedOtioExportError(
            "Portabler Produktions-Export erlaubt keine allow_errors=True. "
            "Für Diagnose den Test-OTIO-Export mit Lücken verwenden."
        )

    assert_enhanced_work_root(project)
    resolved = load_model(resolved_timeline_path(project), ResolvedTimelineDocument)
    if resolved is None:
        raise EnhancedOtioExportError("Aufgelöste Timeline fehlt — kein OTIO-Export.")

    fps = float(resolved.fps or project.fps or 25.0)
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

    package_root = package_dir_for_export(project, basename)
    if package_root.exists():
        import shutil

        try:
            shutil.rmtree(package_root)
        except OSError as exc:
            raise EnhancedOtioExportError(
                f"Alter Paketordner nicht löschbar: {package_root} ({exc})"
            ) from exc
    try:
        package_root.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise EnhancedOtioExportError(
            f"Paketordner nicht beschreibbar: {package_root} ({exc})"
        ) from exc

    timeline = otio.schema.Timeline(name=f"{project.name} enhanced portable")
    video_track = otio.schema.Track(name="Video", kind=otio.schema.TrackKind.Video)
    audio_track = otio.schema.Track(name="Narration", kind=otio.schema.TrackKind.Audio)

    # (source_path, asset_id, media_kind) — vor dem Staging sammeln
    stage_items: list[tuple[Path, str, str]] = []
    # Clip → original media path for rewrite after staging
    pending_video: list[tuple[otio.schema.Clip, Path, str]] = []
    pending_audio: list[tuple[otio.schema.Clip, Path, str]] = []
    catalog = build_asset_catalog(project, fps=fps)

    cursor = 0.0
    for shot in sorted(resolved.shots, key=lambda s: s.timeline_start_seconds):
        if shot.timeline_start_seconds > cursor + 1e-6:
            gap = shot.timeline_start_seconds - cursor
            video_track.append(otio.schema.Gap(source_range=_time_range(gap, fps)))
        media_path, avail_start, source_start, source_end, rate = (
            _ensure_shot_media_for_export(
                project, shot, fps=fps, catalog=catalog
            )
        )
        source_duration = source_end - source_start
        file_avail_start, file_dur, file_rate = _validate_video_file(
            media_path, label=f"{shot.shot_id}", fps=fps
        )
        kind = "still_hold" if (shot.hold_mode or "").startswith(
            ("freeze_video", "still_hold")
        ) or "hold_cache" in media_path.parts else "video"
        stage_items.append((media_path, shot.asset_id, kind))
        # Placeholder URL — nach Staging umgeschrieben
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
        clip.metadata["original_media_path"] = str(media_path)
        if shot.hold_mode:
            clip.metadata["hold_mode"] = shot.hold_mode
        if shot.asset_fit:
            clip.metadata["asset_fit"] = shot.asset_fit
        if shot.asset_fit_reason:
            clip.metadata["begruendung"] = shot.asset_fit_reason
        if shot.cut_alignment:
            clip.metadata["cut_alignment"] = shot.cut_alignment
        if shot.coverage_gap_id:
            clip.metadata["coverage_gap_id"] = shot.coverage_gap_id
        if shot.open_gap:
            clip.metadata["open_gap"] = True
        if bool(getattr(shot, "is_placeholder", False)) or shot.open_gap:
            clip.metadata["placeholder"] = True
        video_track.append(clip)
        pending_video.append((clip, media_path, shot.asset_id))
        cursor = shot.timeline_end_seconds

    audio_cursor = 0.0
    for segment in resolved.audio_segments:
        if segment.timeline_start_seconds > audio_cursor + 1e-6:
            gap = segment.timeline_start_seconds - audio_cursor
            audio_track.append(otio.schema.Gap(source_range=_time_range(gap, fps)))
        audio_path = _assert_local_file(
            segment.audio_path, label=f"Audio {segment.segment_id}"
        )
        stage_items.append((audio_path, segment.segment_id, "audio"))
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
        clip.metadata["segment_id"] = segment.segment_id
        clip.metadata["original_media_path"] = str(audio_path)
        audio_track.append(clip)
        pending_audio.append((clip, audio_path, segment.segment_id))
        audio_cursor = segment.timeline_end_seconds
        if segment.pause_after_seconds > 0:
            audio_track.append(
                otio.schema.Gap(
                    source_range=_time_range(segment.pause_after_seconds, fps)
                )
            )
            audio_cursor += segment.pause_after_seconds

    title_items, title_notes = _render_folder_title_items(
        project,
        resolved,
        fail_closed=True,
    )
    title_track = _build_v2_title_track(title_items, rate=fps) if title_items else None
    pending_titles: list[tuple[otio.schema.Clip, Path, str]] = []
    if title_track is not None:
        for stage_path, asset_id, kind in _folder_title_media_paths(title_items):
            stage_items.append((stage_path, asset_id, kind))
        for child in title_track:
            media = getattr(child, "media_reference", None)
            if media is None:
                continue
            target = str(getattr(media, "target_url", "") or "").strip()
            if not target:
                continue
            media_path = Path(target)
            if not media_path.is_file():
                raise EnhancedOtioExportError(
                    f"Ordner-Titel-Medien fehlen für Staging: {media_path}"
                )
            asset_id = str(
                (getattr(child, "metadata", None) or {}).get("timeline_item_id")
                or media_path.stem
            )
            child.metadata["original_media_path"] = str(media_path.resolve())
            pending_titles.append((child, media_path.resolve(), asset_id))

    timeline.tracks.append(video_track)
    if title_track is not None:
        timeline.tracks.append(title_track)
        timeline.metadata["opening_title_count"] = len(title_items)
    timeline.tracks.append(audio_track)
    if title_notes:
        timeline.metadata["folder_title_notes"] = list(title_notes)
    timeline.metadata["enhanced_export_mode"] = "production_portable"

    try:
        entries = stage_media_into_package(project, package_root, stage_items)
    except PortableExportError as exc:
        raise EnhancedOtioExportError(str(exc)) from exc

    # Rewrite target_urls → relative media/<unique>
    for clip, original, _asset_id in pending_video + pending_audio + pending_titles:
        try:
            entry = lookup_packaged_path(entries, original)
        except PortableExportError as exc:
            raise EnhancedOtioExportError(str(exc)) from exc
        rel_url = relative_media_target_url(entry.packaged_filename)
        clip.media_reference.target_url = rel_url
        clip.metadata["packaged_filename"] = entry.packaged_filename
        clip.metadata["packaged_sha256"] = entry.sha256

    urls = _collect_target_urls(timeline)
    try:
        assert_portable_target_urls(urls, package_root=package_root)
    except PortableExportError as exc:
        raise EnhancedOtioExportError(str(exc)) from exc

    # Basename uniqueness of packaged files already enforced in stage;
    # additionally ensure every target_url maps to a distinct file when assets differ.
    url_to_assets: dict[str, set[str]] = {}
    for clip, _original, asset_id in pending_video:
        url = str(clip.media_reference.target_url)
        url_to_assets.setdefault(url, set()).add(asset_id)
    for url, assets in url_to_assets.items():
        if len(assets) > 1:
            raise EnhancedOtioExportError(
                "Mehrere Asset-IDs teilen dieselbe OTIO-Medienreferenz "
                f"{url!r}: {sorted(assets)}. Export blockiert."
            )

    safe_basename = package_root.name.removesuffix("_package")
    write_media_manifest(package_root / "media_manifest.json", entries)
    write_package_readme(package_root / "README.md", basename=safe_basename)

    out_otio = package_root / "timeline.otio"
    otio.adapters.write_to_file(timeline, str(out_otio))

    # Post-write readback: keine Host-Pfade
    readback = otio.adapters.read_from_file(str(out_otio))
    try:
        assert_portable_target_urls(
            _collect_target_urls(readback), package_root=package_root
        )
    except PortableExportError as exc:
        raise EnhancedOtioExportError(
            f"Portable OTIO nach Schreiben ungültig: {exc}"
        ) from exc

    return package_root
