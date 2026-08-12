"""ElevenLabs Sound Effects service: plan + generate + validate + UI status."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Literal

from otio_app.models import Project
from otio_app.services.media_utils import probe_duration_seconds
from otio_app.services.without_voiceover_enhanced.chapter_cut_service import (
    ChapterCutStatus,
    list_chapter_cut_statuses,
    load_chapter_resolved,
    load_chapter_unified_plan,
)
from otio_app.services.without_voiceover_enhanced.cut_plan_options import (
    load_cut_plan_options,
)
from otio_app.services.without_voiceover_enhanced.elevenlabs_music_service import (
    body_chapter_music_index,
    load_intro_resolved_timeline,
    load_intro_unified_plan,
    narration_text_for_music,
    resolve_music_target_duration_seconds,
)
from otio_app.services.without_voiceover_enhanced.elevenlabs_sfx_client import (
    SFX_DURATION_SECONDS_MAX,
    SFX_DURATION_SECONDS_MIN,
    SFX_MODEL_ID,
    SFX_PROMPT_INFLUENCE_DEFAULT,
    SFX_PROMPT_MAX_CHARS,
    ElevenLabsSfxError,
    generate_sound_effect,
    is_elevenlabs_sfx_configured,
)
from otio_app.services.without_voiceover_enhanced.intro_script_bridge import (
    ENHANCED_INTRO_FOLDER_NAME,
)
from otio_app.services.without_voiceover_enhanced.models import (
    ResolvedShot,
    ResolvedTimelineDocument,
)
from otio_app.services.without_voiceover_enhanced.paths import (
    sound_effects_audio_dir,
    sfx_wav_path,
)
from otio_app.services.without_voiceover_enhanced.script_lock_service import (
    load_locked_script,
    require_locked_script,
)
from otio_app.services.without_voiceover_enhanced.sfx_artifacts import (
    OUTPUT_CONTRACT,
    canonical_usable_sfx_result,
    fingerprint_text,
    replace_canonical_sfx_set,
    resolved_timeline_fingerprint_from_shots,
    save_sfx_plan,
    save_sfx_result,
    sfx_status_for_scope,
    usable_sfx_effects_for_otio,
    utc_now_iso,
)
from otio_app.services.without_voiceover_enhanced.sfx_planner import (
    DURATION_CLASS_SECONDS,
    SfxPlannerError,
    SfxPlanValidationError,
    build_used_shots_for_planner,
    build_word_flow_for_planner,
    run_sfx_planner,
)

__all__ = [
    "SfxServiceError",
    "SfxGenerationResult",
    "SFX_MVP_MAX_BODY_CHAPTERS",
    "is_sfx_mvp_chapter_allowed",
    "body_chapter_sfx_index",
    "generate_sfx_for_intro",
    "generate_sfx_for_chapter",
    "sfx_ui_status_intro",
    "sfx_ui_status_chapter",
    "usable_sfx_placements_for_otio",
    "resolve_sfx_anchor",
    "convert_and_normalize_sfx_wav",
    "validate_final_sfx_wav",
]

SFX_MVP_MAX_BODY_CHAPTERS = 3
_DURATION_MATCH_TOLERANCE_SEC = 0.15
_WORD_SHOT_PROXIMITY_SEC = 0.75
_NO_KEY_REGENERATE_HELP = "Neuerstellung nicht möglich – API-Key fehlt."

SfxScope = Literal["intro", "chapter"]


class SfxServiceError(RuntimeError):
    """Non-secret SFX service failure."""


@dataclass
class SfxGenerationResult:
    status: str
    message: str
    effect_count: int = 0
    effects: list[dict[str, Any]] = field(default_factory=list)
    planner_model: str = ""


def body_chapter_sfx_index(project: Project, folder_name: str) -> int | None:
    return body_chapter_music_index(project, folder_name)


def is_sfx_mvp_chapter_allowed(project: Project, folder_name: str) -> bool:
    index = body_chapter_sfx_index(project, folder_name)
    return index is not None and index < SFX_MVP_MAX_BODY_CHAPTERS


def _script_fingerprint(
    project: Project, *, scope: str, folder_name: str = ""
) -> tuple[str, str, str]:
    locked = load_locked_script(project)
    if locked is None:
        raise SfxServiceError("Locked Script fehlt.")
    text = narration_text_for_music(project, scope=scope, folder_name=folder_name)
    return locked.script_version, text, fingerprint_text(text)


def _scope_total_duration(resolved: ResolvedTimelineDocument) -> float:
    return float(resolve_music_target_duration_seconds(resolved))


def _narration_window(resolved: ResolvedTimelineDocument) -> tuple[float, float]:
    if resolved.chapters:
        env = resolved.chapters[0]
        return float(env.chapter_audio_start), float(env.chapter_audio_end)
    if resolved.audio_segments:
        start = min(float(a.timeline_start_seconds) for a in resolved.audio_segments)
        end = max(float(a.timeline_end_seconds) for a in resolved.audio_segments)
        return start, end
    return 0.0, _scope_total_duration(resolved)


def _shots_index(resolved: ResolvedTimelineDocument) -> dict[str, ResolvedShot]:
    return {str(s.shot_id): s for s in list(resolved.shots or [])}


def _word_onset_index(word_flow: list[dict[str, Any]]) -> dict[str, float]:
    out: dict[str, float] = {}
    for item in word_flow:
        ref = str(item.get("word_ref") or "").strip()
        if ref:
            out[ref] = float(item.get("onset") or 0.0)
    return out


def resolve_sfx_anchor(
    *,
    item: dict[str, Any],
    shot: ResolvedShot,
    word_onsets: dict[str, float],
    scope_total_duration: float,
) -> tuple[float, float]:
    """Return (timeline_start, duration) clamped to the scope."""
    shot_start = float(shot.timeline_start_seconds)
    shot_end = float(shot.timeline_end_seconds)
    shot_dur = max(0.0, shot_end - shot_start)
    anchor = str(item.get("anchor_type") or "").strip()
    duration_class = str(item.get("duration_class") or "medium").strip().lower()

    if anchor == "span_shot":
        duration = min(SFX_DURATION_SECONDS_MAX, max(SFX_DURATION_SECONDS_MIN, shot_dur))
        start = shot_start
    else:
        duration = float(DURATION_CLASS_SECONDS.get(duration_class, 5.0))
        duration = min(SFX_DURATION_SECONDS_MAX, max(SFX_DURATION_SECONDS_MIN, duration))
        if anchor == "shot_start":
            start = shot_start
        elif anchor == "shot_end":
            start = shot_end - duration
        elif anchor == "shot_center":
            center = shot_start + shot_dur / 2.0
            start = center - duration / 2.0
        elif anchor == "narration_word":
            word_ref = str(item.get("word_ref") or "").strip()
            if word_ref not in word_onsets:
                raise SfxServiceError(f"word_ref unbekannt: {word_ref}")
            onset = float(word_onsets[word_ref])
            # Must sit within / immediately at the referenced shot.
            if onset < shot_start - _WORD_SHOT_PROXIMITY_SEC or onset > shot_end + _WORD_SHOT_PROXIMITY_SEC:
                raise SfxServiceError(
                    f"word_ref {word_ref} liegt nicht am Shot {shot.shot_id}."
                )
            start = onset
        else:
            raise SfxServiceError(f"Ungültiger anchor_type: {anchor}")

    total = float(scope_total_duration)
    start = max(0.0, float(start))
    if start >= total:
        raise SfxServiceError("SFX-Start liegt außerhalb der Scope-Dauer.")
    # Clamp duration into remaining window — no shift into next chapter.
    max_dur = min(duration, total - start)
    if max_dur < SFX_DURATION_SECONDS_MIN - 1e-9:
        # Try pulling start earlier within scope while keeping end <= total.
        start = max(0.0, total - duration)
        max_dur = min(duration, total - start)
    if max_dur <= 0:
        raise SfxServiceError("SFX-Dauer nach Clamp ungültig.")
    duration = max(SFX_DURATION_SECONDS_MIN, min(SFX_DURATION_SECONDS_MAX, max_dur))
    end = start + duration
    if end > total + 1e-6:
        duration = max(0.0, total - start)
        if duration <= 0:
            raise SfxServiceError("SFX überschreitet Scope-Grenze.")
    return round(start, 6), round(duration, 6)


def convert_and_normalize_sfx_wav(
    source_path: Path,
    *,
    target_duration_seconds: float,
    output_path: Path,
) -> float:
    probed = probe_duration_seconds(source_path)
    if probed is None or probed <= 0:
        raise SfxServiceError("SFX-Audio nicht decodierbar (ffprobe).")
    target = float(target_duration_seconds)
    if target <= 0:
        raise SfxServiceError("Ziellänge für SFX ist ungültig.")
    output_path.parent.mkdir(parents=True, exist_ok=True)
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
        result = subprocess.run(
            cmd, capture_output=True, text=True, check=False, timeout=180
        )
    except (FileNotFoundError, OSError, subprocess.TimeoutExpired) as exc:
        raise SfxServiceError(f"FFmpeg SFX-Konvertierung fehlgeschlagen: {exc}") from exc
    if result.returncode != 0 or not output_path.is_file():
        err = (result.stderr or result.stdout or "")[-500:]
        raise SfxServiceError(f"FFmpeg SFX-Konvertierung fehlgeschlagen: {err}")
    return validate_final_sfx_wav(output_path, target_duration_seconds=target)


def validate_final_sfx_wav(path: Path, *, target_duration_seconds: float) -> float:
    if not path.is_file():
        raise SfxServiceError("Finale SFX-WAV fehlt.")
    header = path.read_bytes()[:12]
    if len(header) < 12 or header[0:4] != b"RIFF" or header[8:12] != b"WAVE":
        raise SfxServiceError("Finale Datei ist kein gültiges WAV (RIFF/WAVE).")
    info = _ffprobe_audio_stream(path)
    if not info:
        raise SfxServiceError("WAV ohne gültigen Audiostream.")
    sample_rate = int(info.get("sample_rate") or 0)
    channels = int(info.get("channels") or 0)
    codec = str(info.get("codec_name") or "")
    duration = probe_duration_seconds(path)
    if duration is None or duration <= 0:
        raise SfxServiceError("WAV-Dauer ungültig.")
    if sample_rate != 48000:
        raise SfxServiceError(f"WAV Sample-Rate {sample_rate} ≠ 48000.")
    if channels != 2:
        raise SfxServiceError(f"WAV Kanäle {channels} ≠ 2.")
    if codec not in {"pcm_s16le", "pcm_s16be"}:
        raise SfxServiceError(f"WAV Codec {codec!r} ≠ pcm_s16le.")
    if abs(duration - float(target_duration_seconds)) > _DURATION_MATCH_TOLERANCE_SEC:
        raise SfxServiceError(
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
        payload = json.loads(
            (result.stdout or b"").decode("utf-8", errors="replace") or "{}"
        )
    except json.JSONDecodeError:
        return None
    streams = payload.get("streams") or []
    return streams[0] if streams else None


def _generate_one_effect(
    *,
    item: dict[str, Any],
    start: float,
    duration: float,
    output_wav: Path,
    generate_callable: Callable[..., Any],
) -> dict[str, Any]:
    prompt = str(item.get("prompt") or "").strip()
    if len(prompt) > SFX_PROMPT_MAX_CHARS:
        return {
            **item,
            "status": "invalid",
            "message": f"Prompt > {SFX_PROMPT_MAX_CHARS} Zeichen",
            "timeline_start": start,
            "timeline_end": start,
            "duration": 0.0,
            "wav_path": "",
        }
    tmp_dir = Path(tempfile.mkdtemp(prefix="el_sfx_"))
    try:
        transport = tmp_dir / "transport.bin"
        result = generate_callable(
            text=prompt,
            duration_seconds=duration,
            model_id=SFX_MODEL_ID,
            loop=False,
            prompt_influence=SFX_PROMPT_INFLUENCE_DEFAULT,
        )
        audio_bytes = getattr(result, "audio_bytes", None) or b""
        if not audio_bytes:
            raise SfxServiceError("Leere SFX-Antwort.")
        transport.write_bytes(audio_bytes)
        tmp_wav = tmp_dir / "normalized.wav"
        actual = convert_and_normalize_sfx_wav(
            transport, target_duration_seconds=duration, output_path=tmp_wav
        )
        output_wav.parent.mkdir(parents=True, exist_ok=True)
        tmp_final = output_wav.with_suffix(".wav.tmp")
        shutil.copy2(tmp_wav, tmp_final)
        os.replace(tmp_final, output_wav)
        return {
            **item,
            "status": "completed",
            "message": "completed",
            "timeline_start": start,
            "timeline_end": round(start + actual, 6),
            "duration": actual,
            "wav_path": str(output_wav),
            "sample_rate": 48000,
            "channels": 2,
            "codec": "pcm_s16le",
            "output_contract": OUTPUT_CONTRACT,
        }
    except (ElevenLabsSfxError, SfxServiceError) as exc:
        return {
            **item,
            "status": "failed",
            "message": str(exc),
            "timeline_start": start,
            "timeline_end": start,
            "duration": 0.0,
            "wav_path": "",
        }
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


def _generate(
    project: Project,
    *,
    scope: SfxScope,
    folder_name: str,
    resolved: ResolvedTimelineDocument,
    plan,
    generate_callable: Callable[..., Any] | None = None,
    llm_callable: Callable[..., Any] | None = None,
) -> SfxGenerationResult:
    if not is_elevenlabs_sfx_configured():
        return SfxGenerationResult(
            status="unavailable",
            message="Sound Effects nicht verfügbar – ElevenLabs API-Key fehlt.",
        )

    script_version, script_text, script_fp = _script_fingerprint(
        project, scope=scope, folder_name=folder_name
    )
    used_shots = build_used_shots_for_planner(
        project, resolved=resolved, plan=plan, folder_name=folder_name
    )
    if not used_shots:
        return SfxGenerationResult(
            status="failed",
            message="Keine resolved Shots für SFX-Planner verfügbar.",
        )
    timeline_fp = resolved_timeline_fingerprint_from_shots(
        script_version=script_version, shots=used_shots
    )
    total = _scope_total_duration(resolved)
    narr_start, narr_end = _narration_window(resolved)
    options = load_cut_plan_options(project)
    max_sfx = int(options.max_sfx_per_chapter)

    prior = canonical_usable_sfx_result(
        project, scope=scope, folder_name=folder_name
    )

    try:
        planner = run_sfx_planner(
            project,
            resolved=resolved,
            plan=plan,
            folder_name=folder_name,
            locked_script_text=script_text,
            scope=scope,
            narration_start=narr_start,
            narration_end=narr_end,
            scope_total_duration=total,
            max_sfx=max_sfx,
            llm_callable=llm_callable,
        )
    except SfxPlanValidationError as exc:
        if prior is not None:
            return SfxGenerationResult(
                status="failed",
                message=f"SFX-Plan ungültig — bisheriges Set erhalten. {exc}",
                effect_count=len(list(prior.get("effects") or [])),
                planner_model=str(options.sfx_planner_model or ""),
            )
        save_sfx_result(
            project,
            {
                "status": "failed",
                "scope": scope,
                "chapter_id": folder_name if scope == "chapter" else "",
                "planner_model": str(options.sfx_planner_model or ""),
                "script_fingerprint": script_fp,
                "resolved_timeline_fingerprint": timeline_fp,
                "effects": [],
                "generated_at": utc_now_iso(),
                "message": str(exc),
            },
        )
        return SfxGenerationResult(
            status="failed",
            message=str(exc),
            planner_model=str(options.sfx_planner_model or ""),
        )
    except SfxPlannerError as exc:
        if prior is not None:
            return SfxGenerationResult(
                status="failed",
                message=f"SFX Planner fehlgeschlagen — bisheriges Set erhalten. {exc}",
                effect_count=len(list(prior.get("effects") or [])),
            )
        return SfxGenerationResult(status="unavailable", message=str(exc))

    plan_out = planner.plan
    word_onsets = _word_onset_index(planner.word_flow)
    shots = _shots_index(resolved)
    resolved_anchors: list[dict[str, Any]] = []
    to_generate: list[tuple[dict[str, Any], float, float]] = []

    for item in list(plan_out.get("sfx") or []):
        shot = shots.get(str(item.get("shot_id") or ""))
        if shot is None:
            resolved_anchors.append({**item, "status": "invalid", "message": "shot_id"})
            continue
        try:
            start, duration = resolve_sfx_anchor(
                item=item,
                shot=shot,
                word_onsets=word_onsets,
                scope_total_duration=total,
            )
        except SfxServiceError as exc:
            resolved_anchors.append({**item, "status": "invalid", "message": str(exc)})
            continue
        resolved_anchors.append(
            {
                **item,
                "status": "resolved",
                "timeline_start": start,
                "duration": duration,
            }
        )
        to_generate.append((item, start, duration))

    plan_payload = {
        "schema_version": plan_out.get("schema_version"),
        "scope": scope,
        "chapter_id": folder_name if scope == "chapter" else "",
        "planner_model": planner.planner_model,
        "max_sfx": max_sfx,
        "script_fingerprint": script_fp,
        "resolved_timeline_fingerprint": timeline_fp,
        "created_at": utc_now_iso(),
        "planner_output": plan_out,
        "resolved_anchors": resolved_anchors,
    }

    # Intentional empty plan is success and may replace prior set.
    if not to_generate and not any(
        str(a.get("status")) == "invalid" for a in resolved_anchors
    ):
        result_payload = {
            "status": "completed",
            "scope": scope,
            "chapter_id": folder_name if scope == "chapter" else "",
            "planner_model": planner.planner_model,
            "script_fingerprint": script_fp,
            "resolved_timeline_fingerprint": timeline_fp,
            "effects": [],
            "generated_at": utc_now_iso(),
            "message": "keine SFX benötigt",
            "output_contract": OUTPUT_CONTRACT,
        }
        replace_canonical_sfx_set(
            project,
            scope=scope,
            folder_name=folder_name,
            plan_payload=plan_payload,
            result_payload=result_payload,
            temp_audio_dir=None,
        )
        # Clear any leftover audio for empty plan.
        audio_dir = sound_effects_audio_dir(
            project, scope=scope, folder_name=folder_name
        )
        if audio_dir.is_dir():
            for wav in audio_dir.glob("*.wav"):
                try:
                    wav.unlink()
                except OSError:
                    pass
        return SfxGenerationResult(
            status="completed",
            message="✅ Sound Effects · keine benötigt",
            effect_count=0,
            planner_model=planner.planner_model,
        )

    gen = generate_callable or generate_sound_effect
    temp_root = Path(tempfile.mkdtemp(prefix="el_sfx_set_"))
    temp_audio = temp_root / "audio"
    temp_audio.mkdir(parents=True, exist_ok=True)
    effects: list[dict[str, Any]] = []
    try:
        for item, start, duration in to_generate:
            sfx_id = str(item.get("sfx_id") or "sfx")
            out_wav = temp_audio / f"{sfx_id}.wav"
            effect = _generate_one_effect(
                item=item,
                start=start,
                duration=duration,
                output_wav=out_wav,
                generate_callable=gen,
            )
            effects.append(effect)

        completed = [e for e in effects if e.get("status") == "completed"]
        failed = [e for e in effects if e.get("status") != "completed"]
        full_success = len(failed) == 0 and len(completed) == len(to_generate)

        if prior is not None and not full_success:
            # Safe regeneration: keep previous canonical set.
            shutil.rmtree(temp_root, ignore_errors=True)
            return SfxGenerationResult(
                status="failed",
                message=(
                    "SFX-Regeneration unvollständig — bisheriges gültiges Set erhalten."
                ),
                effect_count=len(list(prior.get("effects") or [])),
                planner_model=planner.planner_model,
            )

        if full_success:
            status = "completed"
            message = (
                f"✅ Sound Effects · {len(completed)}"
                if completed
                else "✅ Sound Effects · keine benötigt"
            )
        else:
            status = "completed_partial"
            message = f"✅ Sound Effects · {len(completed)} (teilweise)"

        # Rewrite paths to canonical before replace helper adjusts them.
        for effect in completed:
            sfx_id = str(effect.get("sfx_id") or "")
            effect["wav_path"] = str(
                sfx_wav_path(
                    project, scope=scope, folder_name=folder_name, sfx_id=sfx_id
                )
            )

        result_payload = {
            "status": status,
            "scope": scope,
            "chapter_id": folder_name if scope == "chapter" else "",
            "planner_model": planner.planner_model,
            "script_fingerprint": script_fp,
            "resolved_timeline_fingerprint": timeline_fp,
            "effects": effects,
            "generated_at": utc_now_iso(),
            "message": message,
            "output_contract": OUTPUT_CONTRACT,
        }
        replace_canonical_sfx_set(
            project,
            scope=scope,
            folder_name=folder_name,
            plan_payload=plan_payload,
            result_payload=result_payload,
            temp_audio_dir=temp_audio,
        )
        return SfxGenerationResult(
            status=status,
            message=message,
            effect_count=len(completed),
            effects=effects,
            planner_model=planner.planner_model,
        )
    finally:
        if temp_root.exists():
            shutil.rmtree(temp_root, ignore_errors=True)


def generate_sfx_for_intro(
    project: Project,
    *,
    generate_callable: Callable[..., Any] | None = None,
    llm_callable: Callable[..., Any] | None = None,
) -> SfxGenerationResult:
    require_locked_script(project)
    resolved = load_intro_resolved_timeline(project)
    if resolved is None:
        raise SfxServiceError(
            "Intro Python Timing fehlt — zuerst Intro: Python Timing ausführen."
        )
    plan = load_intro_unified_plan(project)
    if plan is None:
        raise SfxServiceError("Intro-Cut-Plan fehlt.")
    return _generate(
        project,
        scope="intro",
        folder_name=ENHANCED_INTRO_FOLDER_NAME,
        resolved=resolved,
        plan=plan,
        generate_callable=generate_callable,
        llm_callable=llm_callable,
    )


def generate_sfx_for_chapter(
    project: Project,
    folder_name: str,
    *,
    generate_callable: Callable[..., Any] | None = None,
    llm_callable: Callable[..., Any] | None = None,
) -> SfxGenerationResult:
    folder = (folder_name or "").strip()
    if not is_sfx_mvp_chapter_allowed(project, folder):
        raise SfxServiceError("Sound Effects MVP: nur Kapitel 1–3.")
    require_locked_script(project)
    statuses = {s.folder_name: s for s in list_chapter_cut_statuses(project)}
    status = statuses.get(folder)
    if status is None or not status.has_resolved or not status.matches:
        raise SfxServiceError(
            "Kapitel braucht aktuelles erfolgreiches Python Timing "
            "(passend zum Cut-Plan)."
        )
    resolved = load_chapter_resolved(project, folder)
    if resolved is None:
        raise SfxServiceError("Kapitel Resolved-Timeline fehlt.")
    plan = load_chapter_unified_plan(project, folder)
    if plan is None:
        raise SfxServiceError("Kapitel-Cut-Plan fehlt.")
    return _generate(
        project,
        scope="chapter",
        folder_name=folder,
        resolved=resolved,
        plan=plan,
        generate_callable=generate_callable,
        llm_callable=llm_callable,
    )


def sfx_ui_status_intro(project: Project) -> dict[str, Any]:
    from otio_app.services.without_voiceover_enhanced.intro_cut_service import (
        intro_resolved_matches_plan,
    )

    key_ok = is_elevenlabs_sfx_configured()
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
            and _scope_total_duration(resolved) > 0
        )
        if not timing_ok:
            status = sfx_status_for_scope(
                project,
                scope="intro",
                folder_name=ENHANCED_INTRO_FOLDER_NAME,
                script_fingerprint=script_fp if resolved is not None else "",
                resolved_timeline_fingerprint=(
                    resolved_timeline_fingerprint_from_shots(
                        script_version=script_version,
                        shots=build_used_shots_for_planner(
                            project,
                            resolved=resolved,
                            plan=plan,
                            folder_name=ENHANCED_INTRO_FOLDER_NAME,
                        ),
                    )
                    if resolved is not None
                    else ""
                ),
                api_key_present=key_ok,
            )
            status["enabled"] = False
            if status.get("status") in {"completed", "completed_partial"} and not key_ok:
                status["help"] = _NO_KEY_REGENERATE_HELP
            else:
                status["help"] = (
                    "Sound Effects nicht verfügbar – ElevenLabs API-Key fehlt."
                    if not key_ok
                    else "Zuerst aktuelles Intro: Python Timing."
                )
            return status
        used = build_used_shots_for_planner(
            project,
            resolved=resolved,
            plan=plan,
            folder_name=ENHANCED_INTRO_FOLDER_NAME,
        )
        timeline_fp = resolved_timeline_fingerprint_from_shots(
            script_version=script_version, shots=used
        )
    except Exception:  # noqa: BLE001
        if not key_ok:
            return {
                "status": "unavailable",
                "message": "Sound Effects nicht verfügbar – ElevenLabs API-Key fehlt.",
                "enabled": False,
                "help": "Sound Effects nicht verfügbar – ElevenLabs API-Key fehlt.",
                "effect_count": 0,
            }
        return {
            "status": "missing",
            "message": "Sound Effects fehlen",
            "enabled": False,
            "help": "Zuerst Intro: Python Timing.",
            "effect_count": 0,
        }
    status = sfx_status_for_scope(
        project,
        scope="intro",
        folder_name=ENHANCED_INTRO_FOLDER_NAME,
        script_fingerprint=script_fp,
        resolved_timeline_fingerprint=timeline_fp,
        api_key_present=key_ok,
    )
    if not key_ok:
        status["enabled"] = False
        if status.get("status") in {"completed", "completed_partial"}:
            status["help"] = _NO_KEY_REGENERATE_HELP
        else:
            status["help"] = "Sound Effects nicht verfügbar – ElevenLabs API-Key fehlt."
        return status
    status["enabled"] = True
    status["help"] = status.get("message") or "Sound Effects (optional)."
    return status


def sfx_ui_status_chapter(
    project: Project,
    folder_name: str,
    status: ChapterCutStatus | None = None,
) -> dict[str, Any]:
    folder = (folder_name or "").strip()
    if not is_sfx_mvp_chapter_allowed(project, folder):
        return {
            "status": "unavailable",
            "message": "Sound Effects MVP: nur Kapitel 1–3",
            "enabled": False,
            "help": "Sound Effects MVP: nur Kapitel 1–3",
            "effect_count": 0,
        }
    if status is None:
        statuses = {s.folder_name: s for s in list_chapter_cut_statuses(project)}
        status = statuses.get(folder)
    timing_ok = bool(status and status.has_resolved and status.matches)
    key_ok = is_elevenlabs_sfx_configured()
    if not timing_ok:
        return {
            "status": "missing" if key_ok else "unavailable",
            "message": (
                "Sound Effects fehlen"
                if key_ok
                else "Sound Effects nicht verfügbar – ElevenLabs API-Key fehlt."
            ),
            "enabled": False,
            "help": (
                "Zuerst erfolgreiches Python Timing."
                if key_ok
                else "Sound Effects nicht verfügbar – ElevenLabs API-Key fehlt."
            ),
            "effect_count": 0,
        }
    try:
        script_version, _text, script_fp = _script_fingerprint(
            project, scope="chapter", folder_name=folder
        )
        resolved = load_chapter_resolved(project, folder)
        assert resolved is not None
        plan = load_chapter_unified_plan(project, folder)
        used = build_used_shots_for_planner(
            project, resolved=resolved, plan=plan, folder_name=folder
        )
        timeline_fp = resolved_timeline_fingerprint_from_shots(
            script_version=script_version, shots=used
        )
    except Exception:  # noqa: BLE001
        return {
            "status": "missing" if key_ok else "unavailable",
            "message": "Sound Effects fehlen" if key_ok else "Sound Effects nicht verfügbar – ElevenLabs API-Key fehlt.",
            "enabled": False,
            "help": "Zuerst erfolgreiches Python Timing.",
            "effect_count": 0,
        }
    ui = sfx_status_for_scope(
        project,
        scope="chapter",
        folder_name=folder,
        script_fingerprint=script_fp,
        resolved_timeline_fingerprint=timeline_fp,
        api_key_present=key_ok,
    )
    if not key_ok:
        ui["enabled"] = False
        if ui.get("status") in {"completed", "completed_partial"}:
            ui["help"] = _NO_KEY_REGENERATE_HELP
        else:
            ui["help"] = "Sound Effects nicht verfügbar – ElevenLabs API-Key fehlt."
        return ui
    ui["enabled"] = True
    ui["help"] = ui.get("message") or "Sound Effects (optional)."
    return ui


def usable_sfx_placements_for_otio(
    project: Project,
    *,
    scope: SfxScope,
    folder_name: str = "",
) -> list[dict[str, Any]]:
    """Resolve current non-stale SFX effects for OTIO (fail-soft)."""
    try:
        script_version, _text, script_fp = _script_fingerprint(
            project, scope=scope, folder_name=folder_name
        )
        if scope == "intro":
            resolved = load_intro_resolved_timeline(project)
            plan = load_intro_unified_plan(project)
        else:
            resolved = load_chapter_resolved(project, folder_name)
            plan = load_chapter_unified_plan(project, folder_name)
        if resolved is None:
            return []
        used = build_used_shots_for_planner(
            project, resolved=resolved, plan=plan, folder_name=folder_name
        )
        timeline_fp = resolved_timeline_fingerprint_from_shots(
            script_version=script_version, shots=used
        )
    except Exception:  # noqa: BLE001
        return []
    return usable_sfx_effects_for_otio(
        project,
        scope=scope,
        folder_name=folder_name,
        script_fingerprint=script_fp,
        resolved_timeline_fingerprint=timeline_fp,
    )
