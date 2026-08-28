"""ElevenLabs Music MVP service for without_voiceover_enhanced.

Additive: LLM Cut / Python Timing / OTIO remain usable without Music.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from otio_app.models import Project
from otio_app.services.media_utils import probe_duration_seconds
from otio_app.services.without_voiceover_enhanced.chapter_cut_service import (
    ChapterCutStatus,
    list_body_chapter_names,
    list_chapter_cut_statuses,
    load_chapter_resolved,
    load_chapter_unified_plan,
)
from otio_app.services.without_voiceover_enhanced.cut_plan_options import (
    DEFAULT_ELEVENLABS_MUSIC_COUNT,
    load_cut_plan_options,
)
from otio_app.services.without_voiceover_enhanced.elevenlabs_music_client import (
    MUSIC_LENGTH_MS_MAX,
    MUSIC_LENGTH_MS_MIN,
    MUSIC_MODEL_ID,
    ElevenLabsMusicError,
    compose_music,
    is_elevenlabs_music_configured,
)
from otio_app.services.without_voiceover_enhanced.intro_cut_service import (
    intro_resolved_timeline_path,
    intro_unified_cut_plan_path,
)
from otio_app.services.without_voiceover_enhanced.intro_script_bridge import (
    ENHANCED_INTRO_FOLDER_NAME,
)
from otio_app.services.without_voiceover_enhanced.io_utils import load_model
from otio_app.services.without_voiceover_enhanced.models import (
    ResolvedTimelineDocument,
    UnifiedCutPlanDocument,
)
from otio_app.services.without_voiceover_enhanced.music_artifacts import (
    OUTPUT_CONTRACT,
    canonical_completed_music_result,
    fingerprint_text,
    music_status_for_scope,
    resolved_timing_fingerprint,
    save_music_request,
    save_music_result,
    utc_now_iso,
    usable_music_wav_path,
)
from otio_app.services.without_voiceover_enhanced.music_prompt import (
    MUSIC_PROMPT_MAX_CHARS,
    build_chapter_music_prompt,
    build_intro_music_prompt,
    music_prompt_within_limit,
)
from otio_app.services.without_voiceover_enhanced.paths import (
    legacy_music_wav_path,
    music_wav_path,
    resolve_existing_music_wav_path,
)
from otio_app.services.without_voiceover_enhanced.script_author_service import (
    chapter_display_text_for_folder,
    segments_for_folder,
)
from otio_app.services.without_voiceover_enhanced.script_lock_service import (
    load_locked_script,
    require_locked_script,
)

__all__ = [
    "MusicServiceError",
    "MusicGenerationResult",
    "MUSIC_MVP_MAX_BODY_CHAPTERS",
    "get_elevenlabs_music_count",
    "music_max_body_chapters",
    "music_out_of_scope_message",
    "music_bulk_button_label",
    "list_music_generation_targets",
    "is_music_mvp_chapter_allowed",
    "body_chapter_music_index",
    "music_length_ms_from_seconds",
    "resolve_music_target_duration_seconds",
    "resolve_chapter_narration_end_seconds",
    "narration_text_for_music",
    "generate_music_for_intro",
    "generate_music_for_chapter",
    "generate_music_for_allowed_targets",
    "music_ui_status_intro",
    "music_ui_status_chapter",
    "usable_music_path_for_otio",
    "convert_and_normalize_to_wav",
    "validate_final_music_wav",
]

# Default body-chapter cap when settings are missing (Intro + 3 chapters = 4).
MUSIC_MVP_MAX_BODY_CHAPTERS = DEFAULT_ELEVENLABS_MUSIC_COUNT - 1
_DURATION_MATCH_TOLERANCE_SEC = 0.05
_GROSS_DURATION_RATIO_LO = 0.5
_GROSS_DURATION_RATIO_HI = 1.5


def load_intro_resolved_timeline(project: Project) -> ResolvedTimelineDocument | None:
    return load_model(intro_resolved_timeline_path(project), ResolvedTimelineDocument)


def load_intro_unified_plan(project: Project) -> UnifiedCutPlanDocument | None:
    return load_model(intro_unified_cut_plan_path(project), UnifiedCutPlanDocument)


class MusicServiceError(RuntimeError):
    """Music-only failure — must not break Cut / Timing / OTIO."""


@dataclass
class MusicGenerationResult:
    status: str
    message: str
    music_path: str = ""
    target_duration_seconds: float = 0.0
    music_length_ms: int = 0
    actual_duration_seconds: float | None = None


def music_length_ms_from_seconds(target_duration_seconds: float) -> int:
    return int(round(float(target_duration_seconds) * 1000.0))


def body_chapter_music_index(project: Project, folder_name: str) -> int | None:
    names = list_body_chapter_names(project)
    target = (folder_name or "").strip()
    for index, name in enumerate(names):
        if name == target:
            return index
    return None


def get_elevenlabs_music_count(project: Project) -> int:
    """Saved Cut Plan setting: Intro + first N-1 body chapters."""
    try:
        count = int(load_cut_plan_options(project).elevenlabs_music_count)
    except Exception:  # noqa: BLE001
        count = DEFAULT_ELEVENLABS_MUSIC_COUNT
    if count < 1:
        return 1
    return count


def music_max_body_chapters(project: Project) -> int:
    return max(0, get_elevenlabs_music_count(project) - 1)


def music_out_of_scope_message(project: Project) -> str:
    body = music_max_body_chapters(project)
    if body <= 0:
        return "Music: nur Intro"
    if body == 1:
        return "Music: nur Intro + Kapitel 1"
    return f"Music: nur Intro + Kapitel 1–{body}"


def music_bulk_button_label(project: Project) -> str:
    body = music_max_body_chapters(project)
    if body <= 0:
        return "ElevenLabs Music · nur Intro"
    if body == 1:
        return "ElevenLabs Music · Intro + 1. Kapitel"
    return f"ElevenLabs Music · Intro + erste {body} Kapitel"


def list_music_generation_targets(project: Project) -> list[tuple[str, str]]:
    """[(kind, folder_name), ...] — Intro first, then allowed body chapters."""
    targets: list[tuple[str, str]] = []
    count = get_elevenlabs_music_count(project)
    if count >= 1:
        targets.append(("intro", ""))
    max_body = max(0, count - 1)
    for name in list_body_chapter_names(project)[:max_body]:
        targets.append(("chapter", str(name)))
    return targets


def is_music_mvp_chapter_allowed(project: Project, folder_name: str) -> bool:
    index = body_chapter_music_index(project, folder_name)
    return index is not None and index < music_max_body_chapters(project)


def resolve_music_target_duration_seconds(
    resolved: ResolvedTimelineDocument,
) -> float:
    """Canonical duration from resolved timing (full visual envelope)."""
    if resolved.chapters:
        env = resolved.chapters[0]
        span = float(env.chapter_video_end) - float(env.chapter_video_start)
        if span > 1e-6:
            return round(span, 6)
    total = float(resolved.total_duration_seconds or 0.0)
    if total > 1e-6:
        return round(total, 6)
    raise MusicServiceError(
        "Resolved Timing hat keine positive Dauer für ElevenLabs Music."
    )


def resolve_chapter_narration_end_seconds(
    resolved: ResolvedTimelineDocument,
) -> float:
    """Canonical VO end within the chapter track (local to chapter_video_start).

    Source: ``ResolvedChapterEnvelope.chapter_audio_end`` from Python Timing.
    Not estimated from script length or TTS raw data.
    """
    if not resolved.chapters:
        raise MusicServiceError(
            "Kapitel Resolved Timing hat keine Chapter-Envelope — "
            "Voice-over-Endposition für Music-Prompt nicht bestimmbar."
        )
    env = resolved.chapters[0]
    video_start = float(env.chapter_video_start)
    audio_end = float(env.chapter_audio_end)
    video_end = float(env.chapter_video_end)
    narration_end = audio_end - video_start
    total = video_end - video_start
    if narration_end <= 1e-6:
        raise MusicServiceError(
            "Kapitel Resolved Timing: chapter_audio_end liefert keine positive "
            "Voice-over-Endposition für Music."
        )
    if total <= 1e-6:
        raise MusicServiceError(
            "Kapitel Resolved Timing: Kapitel-Gesamtdauer ungültig für Music."
        )
    if narration_end > total + 1e-3:
        raise MusicServiceError(
            "Kapitel Resolved Timing inkonsistent: Narration-Ende "
            f"({narration_end:.3f}s) liegt hinter Kapitelende ({total:.3f}s)."
        )
    # Clamp tiny float overshoot into the track length.
    return round(min(narration_end, total), 6)


def narration_text_for_music(project: Project, *, scope: str, folder_name: str = "") -> str:
    locked = require_locked_script(project)
    if scope == "intro":
        folder = folder_name or ENHANCED_INTRO_FOLDER_NAME
        # Prefer display text (includes [pause …] markers from final TTS text).
        text = chapter_display_text_for_folder(locked, folder)
        if not text.strip():
            # Fallback: any intro-named folder present in locked script.
            from otio_app.services.without_voiceover_enhanced.intro_script_bridge import (
                is_intro_folder_name,
            )

            for seg in locked.segments:
                if is_intro_folder_name(seg.folder_name):
                    text = chapter_display_text_for_folder(locked, seg.folder_name)
                    break
        return text.strip()
    return chapter_display_text_for_folder(locked, folder_name).strip()


def _script_fingerprint(project: Project, *, scope: str, folder_name: str = "") -> tuple[str, str, str]:
    locked = load_locked_script(project)
    if locked is None:
        raise MusicServiceError("Locked Script fehlt.")
    text = narration_text_for_music(project, scope=scope, folder_name=folder_name)
    return locked.script_version, text, fingerprint_text(text)


def convert_and_normalize_to_wav(
    source_path: Path,
    *,
    target_duration_seconds: float,
    output_path: Path,
) -> float:
    """Decode → pad/trim to exact duration → 48 kHz stereo pcm_s16le WAV."""
    probed = probe_duration_seconds(source_path)
    if probed is None or probed <= 0:
        raise MusicServiceError("Audio nicht decodierbar (ffprobe).")
    target = float(target_duration_seconds)
    if target <= 0:
        raise MusicServiceError("Ziellänge für Music ist ungültig.")
    ratio = probed / target
    if ratio < _GROSS_DURATION_RATIO_LO or ratio > _GROSS_DURATION_RATIO_HI:
        raise MusicServiceError(
            f"Music-Dauer weicht stark ab ({probed:.2f}s vs Ziel {target:.2f}s)."
        )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    # whole_dur pads to at least target; atrim cuts exactly to target.
    filt = f"apad=whole_dur={target:.6f},atrim=0:{target:.6f},asetpts=N/SR/TB"
    cmd = [
        "ffmpeg",
        "-y",
        "-hide_banner",
        "-nostdin",
        "-i",
        str(source_path),
        "-af",
        filt,
        "-ar",
        "48000",
        "-ac",
        "2",
        "-c:a",
        "pcm_s16le",
        "-f",
        "wav",
        str(output_path),
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, check=False, timeout=300)
    except (FileNotFoundError, OSError, subprocess.TimeoutExpired) as exc:
        raise MusicServiceError(f"FFmpeg Music-Konvertierung fehlgeschlagen: {exc}") from exc
    if result.returncode != 0 or not output_path.is_file():
        err = (result.stderr or result.stdout or "")[-500:]
        raise MusicServiceError(f"FFmpeg Music-Konvertierung fehlgeschlagen: {err}")
    return validate_final_music_wav(output_path, target_duration_seconds=target)


def validate_final_music_wav(
    path: Path,
    *,
    target_duration_seconds: float,
) -> float:
    if not path.is_file():
        raise MusicServiceError("Finale Music-WAV fehlt.")
    # Reject fake WAV (e.g. raw bytes renamed).
    header = path.read_bytes()[:12]
    if len(header) < 12 or header[0:4] != b"RIFF" or header[8:12] != b"WAVE":
        raise MusicServiceError("Finale Datei ist kein gültiges WAV (RIFF/WAVE).")
    info = _ffprobe_audio_stream(path)
    if not info:
        raise MusicServiceError("WAV ohne gültigen Audiostream.")
    sample_rate = int(info.get("sample_rate") or 0)
    channels = int(info.get("channels") or 0)
    codec = str(info.get("codec_name") or "")
    duration = probe_duration_seconds(path)
    if duration is None or duration <= 0:
        raise MusicServiceError("WAV-Dauer ungültig.")
    if sample_rate != 48000:
        raise MusicServiceError(f"WAV Sample-Rate {sample_rate} ≠ 48000.")
    if channels != 2:
        raise MusicServiceError(f"WAV Kanäle {channels} ≠ 2.")
    if codec not in {"pcm_s16le", "pcm_s16be"}:
        # Accept only signed 16-bit PCM as contracted.
        raise MusicServiceError(f"WAV Codec {codec!r} ≠ pcm_s16le.")
    if abs(duration - float(target_duration_seconds)) > _DURATION_MATCH_TOLERANCE_SEC:
        raise MusicServiceError(
            f"WAV-Dauer {duration:.3f}s weicht von Ziel "
            f"{float(target_duration_seconds):.3f}s ab."
        )
    return float(duration)


def _ffprobe_audio_stream(path: Path) -> dict[str, Any] | None:
    try:
        result = subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-select_streams",
                "a:0",
                "-show_entries",
                "stream=codec_name,sample_rate,channels",
                "-of",
                "json",
                str(path),
            ],
            capture_output=True,
            check=False,
        )
    except FileNotFoundError:
        return None
    if result.returncode != 0:
        return None
    try:
        payload = json.loads((result.stdout or b"").decode("utf-8", errors="replace") or "{}")
    except json.JSONDecodeError:
        return None
    streams = payload.get("streams") or []
    return streams[0] if streams else None


def _remove_legacy_music_wav(
    project: Project, *, scope: str, folder_name: str
) -> None:
    """Drop old ``music.wav`` once ``Music_<Kapitel>.wav`` is written."""
    canonical = music_wav_path(project, scope=scope, folder_name=folder_name)
    legacy = legacy_music_wav_path(project, scope=scope, folder_name=folder_name)
    if legacy == canonical or not legacy.is_file():
        return
    try:
        legacy.unlink()
    except OSError:
        return


def _write_failed_result(
    project: Project,
    *,
    scope: str,
    folder_name: str,
    message: str,
    script_version: str,
    script_fp: str,
    timing_fp: str,
    target_duration_seconds: float,
    music_length_ms: int,
) -> MusicGenerationResult:
    """Persist failed only when no canonical completed Music exists.

    If a successful ``music_result.json`` + WAV already exist (current
    or stale fingerprints), keep them untouched so OTIO usability / staleness
    is not destroyed by a failed regeneration.
    """
    scope_lit = "intro" if scope == "intro" else "chapter"
    preserved = canonical_completed_music_result(
        project, scope=scope_lit, folder_name=folder_name
    )
    wav = resolve_existing_music_wav_path(project, scope=scope, folder_name=folder_name)
    if preserved is not None:
        return MusicGenerationResult(
            status="failed",
            message=message,
            music_path=str(wav) if wav is not None else "",
            target_duration_seconds=target_duration_seconds,
            music_length_ms=music_length_ms,
            actual_duration_seconds=(
                float(preserved["actual_duration_seconds"])
                if preserved.get("actual_duration_seconds") is not None
                else None
            ),
        )

    save_music_result(
        project,
        {
            "scope": scope,
            "chapter_id": folder_name if scope == "chapter" else "",
            "status": "failed",
            "music_path": "",
            "actual_duration_seconds": None,
            "sample_rate": 48000,
            "channels": 2,
            "codec": "pcm_s16le",
            "model_id": MUSIC_MODEL_ID,
            "generated_at": utc_now_iso(),
            "resolved_timing_fingerprint": timing_fp,
            "script_fingerprint": script_fp,
            "script_version": script_version,
            "target_duration_seconds": target_duration_seconds,
            "music_length_ms": music_length_ms,
            "message": message,
        },
    )
    return MusicGenerationResult(
        status="failed",
        message=message,
        target_duration_seconds=target_duration_seconds,
        music_length_ms=music_length_ms,
    )


def _generate(
    project: Project,
    *,
    scope: str,
    folder_name: str,
    resolved: ResolvedTimelineDocument,
    prompt_builder: Callable[[str], str],
    compose_callable: Callable[..., Any] | None = None,
) -> MusicGenerationResult:
    if not is_elevenlabs_music_configured():
        return MusicGenerationResult(
            status="unavailable",
            message="ElevenLabs Music nicht verfügbar – API-Key fehlt.",
        )
    script_version, narration, script_fp = _script_fingerprint(
        project, scope=scope, folder_name=folder_name
    )
    if not narration:
        raise MusicServiceError("Kein Narrationstext für Music-Prompt.")
    target = resolve_music_target_duration_seconds(resolved)
    length_ms = music_length_ms_from_seconds(target)
    timing_fp = resolved_timing_fingerprint(
        script_version=script_version, target_duration_seconds=target
    )
    if length_ms < MUSIC_LENGTH_MS_MIN or length_ms > MUSIC_LENGTH_MS_MAX:
        msg = (
            f"ElevenLabs Music nicht erzeugt: Dauer {length_ms} ms liegt außerhalb "
            f"{MUSIC_LENGTH_MS_MIN}–{MUSIC_LENGTH_MS_MAX} ms."
        )
        return _write_failed_result(
            project,
            scope=scope,
            folder_name=folder_name,
            message=msg,
            script_version=script_version,
            script_fp=script_fp,
            timing_fp=timing_fp,
            target_duration_seconds=target,
            music_length_ms=length_ms,
        )

    prompt = prompt_builder(narration)
    if not music_prompt_within_limit(prompt):
        msg = (
            "ElevenLabs Music nicht erzeugt: "
            "Der Musikprompt überschreitet das aktuelle API-Limit. "
            "Schnitt und Export bleiben unverändert verfügbar."
        )
        save_music_request(
            project,
            {
                "scope": scope,
                "chapter_id": folder_name if scope == "chapter" else "",
                "script_version": script_version,
                "target_duration_seconds": target,
                "music_length_ms": length_ms,
                "model_id": MUSIC_MODEL_ID,
                "force_instrumental": True,
                "prompt": prompt[:500] + "…",
                "prompt_chars": len(prompt),
                "prompt_limit": MUSIC_PROMPT_MAX_CHARS,
                "output_contract": OUTPUT_CONTRACT,
                "resolved_timing_fingerprint": timing_fp,
                "script_fingerprint": script_fp,
                "blocked": "prompt_too_long",
            },
        )
        return _write_failed_result(
            project,
            scope=scope,
            folder_name=folder_name,
            message=msg,
            script_version=script_version,
            script_fp=script_fp,
            timing_fp=timing_fp,
            target_duration_seconds=target,
            music_length_ms=length_ms,
        )

    save_music_request(
        project,
        {
            "scope": scope,
            "chapter_id": folder_name if scope == "chapter" else "",
            "script_version": script_version,
            "target_duration_seconds": target,
            "music_length_ms": length_ms,
            "model_id": MUSIC_MODEL_ID,
            "force_instrumental": True,
            "prompt": prompt,
            "output_contract": OUTPUT_CONTRACT,
            "resolved_timing_fingerprint": timing_fp,
            "script_fingerprint": script_fp,
        },
    )

    final_wav = music_wav_path(project, scope=scope, folder_name=folder_name)
    final_wav.parent.mkdir(parents=True, exist_ok=True)
    compose_fn = compose_callable or compose_music
    tmp_dir = Path(tempfile.mkdtemp(prefix="el_music_"))
    try:
        try:
            api_result = compose_fn(
                prompt=prompt,
                music_length_ms=length_ms,
                model_id=MUSIC_MODEL_ID,
                force_instrumental=True,
            )
        except ElevenLabsMusicError as exc:
            return _write_failed_result(
                project,
                scope=scope,
                folder_name=folder_name,
                message=f"ElevenLabs Music Fehler: {exc}",
                script_version=script_version,
                script_fp=script_fp,
                timing_fp=timing_fp,
                target_duration_seconds=target,
                music_length_ms=length_ms,
            )

        raw_bytes = getattr(api_result, "audio_bytes", b"") or b""
        if not raw_bytes:
            return _write_failed_result(
                project,
                scope=scope,
                folder_name=folder_name,
                message="ElevenLabs Music lieferte leeres Audio.",
                script_version=script_version,
                script_fp=script_fp,
                timing_fp=timing_fp,
                target_duration_seconds=target,
                music_length_ms=length_ms,
            )
        # Never persist transport bytes as final .wav.
        transport_path = tmp_dir / "transport.bin"
        transport_path.write_bytes(raw_bytes)
        normalized_path = tmp_dir / "normalized.wav"
        try:
            actual = convert_and_normalize_to_wav(
                transport_path,
                target_duration_seconds=target,
                output_path=normalized_path,
            )
        except MusicServiceError as exc:
            return _write_failed_result(
                project,
                scope=scope,
                folder_name=folder_name,
                message=str(exc),
                script_version=script_version,
                script_fp=script_fp,
                timing_fp=timing_fp,
                target_duration_seconds=target,
                music_length_ms=length_ms,
            )

        # Atomic replace of previous valid WAV only after full success.
        tmp_final = final_wav.with_suffix(".wav.tmp")
        shutil.copy2(normalized_path, tmp_final)
        os.replace(tmp_final, final_wav)
        _remove_legacy_music_wav(project, scope=scope, folder_name=folder_name)

        song_id = getattr(api_result, "song_id", None)
        save_music_result(
            project,
            {
                "scope": scope,
                "chapter_id": folder_name if scope == "chapter" else "",
                "status": "completed",
                "music_path": str(final_wav),
                "actual_duration_seconds": actual,
                "sample_rate": 48000,
                "channels": 2,
                "codec": "pcm_s16le",
                "model_id": MUSIC_MODEL_ID,
                "generated_at": utc_now_iso(),
                "resolved_timing_fingerprint": timing_fp,
                "script_fingerprint": script_fp,
                "script_version": script_version,
                "target_duration_seconds": target,
                "music_length_ms": length_ms,
                "song_id": song_id,
                "message": "completed",
            },
        )
        return MusicGenerationResult(
            status="completed",
            message=f"✅ ElevenLabs Music · {final_wav.name} · {actual:.2f}s",
            music_path=str(final_wav),
            target_duration_seconds=target,
            music_length_ms=length_ms,
            actual_duration_seconds=actual,
        )
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


def generate_music_for_intro(
    project: Project,
    *,
    compose_callable: Callable[..., Any] | None = None,
) -> MusicGenerationResult:
    resolved = load_intro_resolved_timeline(project)
    if resolved is None:
        raise MusicServiceError(
            "Intro Python Timing fehlt — zuerst Intro: Python Timing ausführen."
        )
    plan = load_intro_unified_plan(project)
    if plan is None:
        raise MusicServiceError("Intro-Cut-Plan fehlt.")
    total = resolve_music_target_duration_seconds(resolved)
    return _generate(
        project,
        scope="intro",
        folder_name=ENHANCED_INTRO_FOLDER_NAME,
        resolved=resolved,
        prompt_builder=lambda text: build_intro_music_prompt(
            narration_text=text,
            total_duration_seconds=total,
        ),
        compose_callable=compose_callable,
    )


def generate_music_for_chapter(
    project: Project,
    folder_name: str,
    *,
    compose_callable: Callable[..., Any] | None = None,
) -> MusicGenerationResult:
    folder = (folder_name or "").strip()
    if not is_music_mvp_chapter_allowed(project, folder):
        raise MusicServiceError(f"{music_out_of_scope_message(project)}.")
    statuses = {s.folder_name: s for s in list_chapter_cut_statuses(project)}
    status = statuses.get(folder)
    if status is None or not status.has_resolved or not status.matches:
        raise MusicServiceError(
            "Kapitel braucht aktuelles erfolgreiches Python Timing "
            "(passend zum Cut-Plan)."
        )
    resolved = load_chapter_resolved(project, folder)
    if resolved is None:
        raise MusicServiceError("Kapitel Resolved-Timeline fehlt.")
    plan = load_chapter_unified_plan(project, folder)
    if plan is None:
        raise MusicServiceError("Kapitel-Cut-Plan fehlt.")
    total = resolve_music_target_duration_seconds(resolved)
    narration_end = resolve_chapter_narration_end_seconds(resolved)
    return _generate(
        project,
        scope="chapter",
        folder_name=folder,
        resolved=resolved,
        prompt_builder=lambda text: build_chapter_music_prompt(
            narration_text=text,
            total_duration_seconds=total,
            narration_end_seconds=narration_end,
        ),
        compose_callable=compose_callable,
    )


def _music_target_label(kind: str, folder_name: str) -> str:
    if kind == "intro":
        return "Intro"
    return (folder_name or "").strip() or "Kapitel"


def generate_music_for_allowed_targets(
    project: Project,
    *,
    skip_completed: bool = True,
    compose_callable: Callable[..., Any] | None = None,
    on_progress: Callable[[str, int, int], None] | None = None,
    should_stop: Callable[[], bool] | None = None,
) -> dict[str, Any]:
    """Generate ElevenLabs Music for Intro + the first N-1 body chapters.

    Completed (not stale) WAVs are skipped by default to avoid re-billing.
    Missing Python Timing is skipped rather than aborting the batch.
    """
    generated: list[dict[str, str]] = []
    skipped: list[dict[str, str]] = []
    failed: list[dict[str, str]] = []
    stopped = False
    targets = list_music_generation_targets(project)
    total = len(targets)
    for index, (kind, folder) in enumerate(targets, start=1):
        if should_stop is not None and should_stop():
            stopped = True
            break
        label = _music_target_label(kind, folder)
        if on_progress is not None:
            on_progress(label, index, total)
        if skip_completed:
            if kind == "intro":
                ui = music_ui_status_intro(project)
            else:
                ui = music_ui_status_chapter(project, folder)
            if str(ui.get("status") or "") == "completed":
                skipped.append(
                    {
                        "kind": kind,
                        "folder": folder,
                        "label": label,
                        "reason": "bereits vorhanden",
                    }
                )
                continue
        try:
            if kind == "intro":
                result = generate_music_for_intro(
                    project, compose_callable=compose_callable
                )
            else:
                result = generate_music_for_chapter(
                    project, folder, compose_callable=compose_callable
                )
        except MusicServiceError as exc:
            failed.append(
                {
                    "kind": kind,
                    "folder": folder,
                    "label": label,
                    "reason": str(exc),
                }
            )
            continue
        except Exception as exc:  # noqa: BLE001
            failed.append(
                {
                    "kind": kind,
                    "folder": folder,
                    "label": label,
                    "reason": str(exc),
                }
            )
            continue
        status = str(result.status or "")
        if status == "completed":
            generated.append(
                {
                    "kind": kind,
                    "folder": folder,
                    "label": label,
                    "reason": result.message or "ok",
                }
            )
        elif status in {"unavailable", "failed"}:
            failed.append(
                {
                    "kind": kind,
                    "folder": folder,
                    "label": label,
                    "reason": result.message or status,
                }
            )
        else:
            skipped.append(
                {
                    "kind": kind,
                    "folder": folder,
                    "label": label,
                    "reason": result.message or status,
                }
            )
    return {
        "generated": generated,
        "skipped": skipped,
        "failed": failed,
        "stopped": stopped,
        "target_count": total,
    }


_NO_KEY_REGENERATE_HELP = "Neuerstellung nicht möglich – API-Key fehlt."


def music_ui_status_intro(project: Project) -> dict[str, Any]:
    from otio_app.services.without_voiceover_enhanced.intro_cut_service import (
        intro_resolved_matches_plan,
    )

    key_ok = is_elevenlabs_music_configured()
    try:
        script_version, _text, script_fp = _script_fingerprint(
            project, scope="intro", folder_name=ENHANCED_INTRO_FOLDER_NAME
        )
        resolved = load_intro_resolved_timeline(project)
        plan = load_intro_unified_plan(project)
        timing_ok = (
            resolved is not None
            and plan is not None
            and intro_resolved_matches_plan(plan, resolved, project=project)
            and resolve_music_target_duration_seconds(resolved) > 0
        )
        if not timing_ok:
            # Still surface existing completed Music when key missing, if any.
            status = music_status_for_scope(
                project,
                scope="intro",
                folder_name=ENHANCED_INTRO_FOLDER_NAME,
                script_fingerprint=script_fp if resolved is not None else "",
                resolved_timing_fingerprint=(
                    resolved_timing_fingerprint(
                        script_version=script_version,
                        target_duration_seconds=resolve_music_target_duration_seconds(
                            resolved
                        ),
                    )
                    if resolved is not None
                    else ""
                ),
                api_key_present=key_ok,
            )
            if status.get("status") == "completed":
                status["enabled"] = False
                status["help"] = (
                    _NO_KEY_REGENERATE_HELP
                    if not key_ok
                    else "Zuerst aktuelles Intro: Python Timing."
                )
                return status
            return {
                "status": status.get("status") or ("unavailable" if not key_ok else "missing"),
                "message": (
                    "Music nicht verfügbar"
                    if not key_ok and status.get("status") == "unavailable"
                    else (status.get("message") or "Music fehlt")
                ),
                "enabled": False,
                "help": (
                    "ElevenLabs Music nicht verfügbar – API-Key fehlt."
                    if not key_ok
                    else "Zuerst aktuelles Intro: Python Timing."
                ),
                "actual_duration_seconds": status.get("actual_duration_seconds"),
                "music_path": status.get("music_path") or "",
            }
        target = resolve_music_target_duration_seconds(resolved)
        timing_fp = resolved_timing_fingerprint(
            script_version=script_version, target_duration_seconds=target
        )
    except Exception:  # noqa: BLE001
        if not key_ok:
            return {
                "status": "unavailable",
                "message": "Music nicht verfügbar",
                "enabled": False,
                "help": "ElevenLabs Music nicht verfügbar – API-Key fehlt.",
            }
        return {
            "status": "missing",
            "message": "Music fehlt",
            "enabled": False,
            "help": "Zuerst Intro: Python Timing.",
        }
    status = music_status_for_scope(
        project,
        scope="intro",
        folder_name=ENHANCED_INTRO_FOLDER_NAME,
        script_fingerprint=script_fp,
        resolved_timing_fingerprint=timing_fp,
        api_key_present=key_ok,
    )
    if not key_ok:
        status["enabled"] = False
        if status.get("status") == "completed":
            status["help"] = _NO_KEY_REGENERATE_HELP
        else:
            status["help"] = "ElevenLabs Music nicht verfügbar – API-Key fehlt."
            if status.get("status") == "unavailable":
                status["message"] = "Music nicht verfügbar"
        return status
    status["enabled"] = True
    status["help"] = status.get("message") or ""
    return status


def music_ui_status_chapter(project: Project, folder_name: str, status: ChapterCutStatus | None = None) -> dict[str, Any]:
    folder = (folder_name or "").strip()
    allowed = is_music_mvp_chapter_allowed(project, folder)
    if not allowed:
        msg = music_out_of_scope_message(project)
        return {
            "status": "unavailable",
            "message": msg,
            "enabled": False,
            "help": msg,
        }
    if status is None:
        statuses = {s.folder_name: s for s in list_chapter_cut_statuses(project)}
        status = statuses.get(folder)
    timing_ok = bool(status and status.has_resolved and status.matches)
    key_ok = is_elevenlabs_music_configured()
    if not timing_ok:
        return {
            "status": "missing" if key_ok else "unavailable",
            "message": "Music fehlt" if key_ok else "Music nicht verfügbar",
            "enabled": False,
            "help": (
                "Zuerst erfolgreiches Python Timing."
                if key_ok
                else "ElevenLabs Music nicht verfügbar – API-Key fehlt."
            ),
        }
    try:
        script_version, _text, script_fp = _script_fingerprint(
            project, scope="chapter", folder_name=folder
        )
        resolved = load_chapter_resolved(project, folder)
        assert resolved is not None
        target = resolve_music_target_duration_seconds(resolved)
        timing_fp = resolved_timing_fingerprint(
            script_version=script_version, target_duration_seconds=target
        )
    except Exception:  # noqa: BLE001
        return {
            "status": "missing" if key_ok else "unavailable",
            "message": "Music fehlt" if key_ok else "Music nicht verfügbar",
            "enabled": False,
            "help": (
                "Zuerst erfolgreiches Python Timing."
                if key_ok
                else "ElevenLabs Music nicht verfügbar – API-Key fehlt."
            ),
        }
    ui = music_status_for_scope(
        project,
        scope="chapter",
        folder_name=folder,
        script_fingerprint=script_fp,
        resolved_timing_fingerprint=timing_fp,
        api_key_present=key_ok,
    )
    if not key_ok:
        ui["enabled"] = False
        if ui.get("status") == "completed":
            ui["help"] = _NO_KEY_REGENERATE_HELP
        else:
            ui["help"] = "ElevenLabs Music nicht verfügbar – API-Key fehlt."
            if ui.get("status") == "unavailable":
                ui["message"] = "Music nicht verfügbar"
        return ui
    ui["enabled"] = True
    ui["help"] = ui.get("message") or ""
    return ui


def usable_music_path_for_otio(
    project: Project,
    *,
    scope: str,
    folder_name: str = "",
) -> Path | None:
    """Optional Music for OTIO — fail-soft None when missing/stale/invalid."""
    try:
        script_version, _text, script_fp = _script_fingerprint(
            project, scope=scope, folder_name=folder_name
        )
        if scope == "intro":
            resolved = load_intro_resolved_timeline(project)
        else:
            resolved = load_chapter_resolved(project, folder_name)
        if resolved is None:
            return None
        target = resolve_music_target_duration_seconds(resolved)
        timing_fp = resolved_timing_fingerprint(
            script_version=script_version, target_duration_seconds=target
        )
        path = usable_music_wav_path(
            project,
            scope=scope if scope in {"intro", "chapter"} else "chapter",  # type: ignore[arg-type]
            folder_name=folder_name,
            script_fingerprint=script_fp,
            resolved_timing_fingerprint=timing_fp,
        )
        if path is None:
            return None
        validate_final_music_wav(path, target_duration_seconds=target)
        return path
    except Exception:  # noqa: BLE001
        return None
