"""SFX artefacts: plan/result JSON, fingerprints, staleness."""

from __future__ import annotations

import hashlib
import json
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from otio_app.models import Project
from otio_app.services.without_voiceover_enhanced.io_utils import write_json
from otio_app.services.without_voiceover_enhanced.paths import (
    sound_effects_audio_dir,
    sound_effects_scope_dir,
    sfx_plan_path,
    sfx_result_path,
    sfx_wav_path,
)

SfxScope = Literal["intro", "chapter"]
SfxStatus = Literal[
    "completed",
    "completed_partial",
    "failed",
    "stale",
    "unavailable",
    "missing",
]

OUTPUT_CONTRACT = "wav_pcm_s16le_48000_stereo"


def ensure_sfx_dir(project: Project, *, scope: SfxScope, folder_name: str = "") -> Path:
    path = sound_effects_scope_dir(project, scope=scope, folder_name=folder_name)
    path.mkdir(parents=True, exist_ok=True)
    audio = sound_effects_audio_dir(project, scope=scope, folder_name=folder_name)
    audio.mkdir(parents=True, exist_ok=True)
    return path


def fingerprint_text(value: str) -> str:
    return hashlib.sha256((value or "").encode("utf-8")).hexdigest()[:32]


def resolved_timeline_fingerprint_from_shots(
    *,
    script_version: str,
    shots: list[dict[str, Any]],
) -> str:
    """Fingerprint used shots + positions (Music changes do not affect this)."""
    rows = []
    for shot in shots:
        rows.append(
            {
                "shot_id": str(shot.get("shot_id") or ""),
                "asset_id": str(shot.get("asset_id") or ""),
                "timeline_start": round(float(shot.get("timeline_start") or 0.0), 6),
                "timeline_end": round(float(shot.get("timeline_end") or 0.0), 6),
                "source_start": round(float(shot.get("source_start") or 0.0), 6),
                "source_end": round(float(shot.get("source_end") or 0.0), 6),
            }
        )
    rows.sort(key=lambda r: (r["timeline_start"], r["shot_id"]))
    return fingerprint_text(
        f"{script_version}|{json.dumps(rows, ensure_ascii=False, sort_keys=True)}"
    )


def load_json_if_present(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def _strip_secrets(payload: dict[str, Any]) -> dict[str, Any]:
    return {
        k: v
        for k, v in payload.items()
        if "key" not in str(k).lower()
        and "secret" not in str(k).lower()
        and "authorization" not in str(k).lower()
    }


def save_sfx_plan(project: Project, payload: dict[str, Any]) -> Path:
    scope = str(payload.get("scope") or "chapter")
    folder = str(payload.get("chapter_id") or "")
    ensure_sfx_dir(
        project,
        scope="intro" if scope == "intro" else "chapter",
        folder_name=folder,
    )
    path = sfx_plan_path(project, scope=scope, folder_name=folder)
    write_json(path, _strip_secrets(payload))
    return path


def save_sfx_result(project: Project, payload: dict[str, Any]) -> Path:
    scope = str(payload.get("scope") or "chapter")
    folder = str(payload.get("chapter_id") or "")
    ensure_sfx_dir(
        project,
        scope="intro" if scope == "intro" else "chapter",
        folder_name=folder,
    )
    path = sfx_result_path(project, scope=scope, folder_name=folder)
    write_json(path, _strip_secrets(payload))
    return path


def load_sfx_plan(
    project: Project, *, scope: SfxScope, folder_name: str = ""
) -> dict[str, Any] | None:
    return load_json_if_present(sfx_plan_path(project, scope=scope, folder_name=folder_name))


def load_sfx_result(
    project: Project, *, scope: SfxScope, folder_name: str = ""
) -> dict[str, Any] | None:
    return load_json_if_present(
        sfx_result_path(project, scope=scope, folder_name=folder_name)
    )


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _effect_wav_ok(project: Project, *, scope: SfxScope, folder_name: str, effect: dict[str, Any]) -> bool:
    status = str(effect.get("status") or "")
    if status != "completed":
        return False
    path_raw = str(effect.get("wav_path") or "").strip()
    if path_raw:
        path = Path(path_raw)
        if path.is_file():
            return True
    sfx_id = str(effect.get("sfx_id") or "").strip()
    if not sfx_id:
        return False
    return sfx_wav_path(
        project, scope=scope, folder_name=folder_name, sfx_id=sfx_id
    ).is_file()


def canonical_usable_sfx_result(
    project: Project, *, scope: SfxScope, folder_name: str = ""
) -> dict[str, Any] | None:
    """Return completed / completed_partial result when at least one valid WAV exists.

    Used so failed regenerations do not destroy a prior usable canonical set.
    """
    result = load_sfx_result(project, scope=scope, folder_name=folder_name)
    if result is None:
        return None
    status = str(result.get("status") or "")
    if status not in {"completed", "completed_partial"}:
        return None
    effects = list(result.get("effects") or [])
    if status == "completed" and not effects:
        # Intentional empty plan is a valid canonical state.
        return result
    if any(
        _effect_wav_ok(project, scope=scope, folder_name=folder_name, effect=e)
        for e in effects
        if isinstance(e, dict)
    ):
        return result
    if status == "completed" and not effects:
        return result
    return None


def sfx_status_for_scope(
    project: Project,
    *,
    scope: SfxScope,
    folder_name: str = "",
    script_fingerprint: str,
    resolved_timeline_fingerprint: str,
    api_key_present: bool,
) -> dict[str, Any]:
    result = load_sfx_result(project, scope=scope, folder_name=folder_name)
    effects = list((result or {}).get("effects") or [])
    completed_count = sum(
        1
        for e in effects
        if isinstance(e, dict)
        and _effect_wav_ok(project, scope=scope, folder_name=folder_name, effect=e)
    )
    stored_status = str((result or {}).get("status") or "")
    if result is None and completed_count == 0:
        if not api_key_present:
            return {
                "status": "unavailable",
                "message": "Sound Effects nicht verfügbar – ElevenLabs API-Key fehlt.",
                "effect_count": 0,
            }
        return {
            "status": "missing",
            "message": "Sound Effects fehlen",
            "effect_count": 0,
        }

    stored_script = str((result or {}).get("script_fingerprint") or "")
    stored_timeline = str((result or {}).get("resolved_timeline_fingerprint") or "")
    fingerprints_ok = (
        stored_script == script_fingerprint
        and stored_timeline == resolved_timeline_fingerprint
    )

    if stored_status in {"completed", "completed_partial"}:
        if not fingerprints_ok:
            return {
                "status": "stale",
                "message": "⚠ Sound Effects veraltet",
                "effect_count": completed_count,
            }
        if stored_status == "completed" and completed_count == 0 and not effects:
            return {
                "status": "completed",
                "message": "✅ Sound Effects · keine benötigt",
                "effect_count": 0,
            }
        if completed_count > 0:
            label = (
                f"✅ Sound Effects · {completed_count}"
                if stored_status == "completed"
                else f"✅ Sound Effects · {completed_count} (teilweise)"
            )
            return {
                "status": "completed" if stored_status == "completed" else "completed_partial",
                "message": label,
                "effect_count": completed_count,
            }

    if stored_status == "failed":
        if not api_key_present and completed_count == 0:
            return {
                "status": "unavailable",
                "message": "Sound Effects nicht verfügbar – ElevenLabs API-Key fehlt.",
                "effect_count": 0,
            }
        return {
            "status": "failed",
            "message": str((result or {}).get("message") or "Sound Effects fehlgeschlagen"),
            "effect_count": completed_count,
        }

    if completed_count > 0 and not fingerprints_ok:
        return {
            "status": "stale",
            "message": "⚠ Sound Effects veraltet",
            "effect_count": completed_count,
        }

    if not api_key_present:
        return {
            "status": "unavailable",
            "message": "Sound Effects nicht verfügbar – ElevenLabs API-Key fehlt.",
            "effect_count": 0,
        }
    return {
        "status": "missing",
        "message": "Sound Effects fehlen",
        "effect_count": 0,
    }


def usable_sfx_effects_for_otio(
    project: Project,
    *,
    scope: SfxScope,
    folder_name: str = "",
    script_fingerprint: str,
    resolved_timeline_fingerprint: str,
) -> list[dict[str, Any]]:
    """Return completed non-stale effects with local WAV paths."""
    status = sfx_status_for_scope(
        project,
        scope=scope,
        folder_name=folder_name,
        script_fingerprint=script_fingerprint,
        resolved_timeline_fingerprint=resolved_timeline_fingerprint,
        api_key_present=True,
    )
    if status.get("status") not in {"completed", "completed_partial"}:
        return []
    result = load_sfx_result(project, scope=scope, folder_name=folder_name) or {}
    out: list[dict[str, Any]] = []
    for effect in list(result.get("effects") or []):
        if not isinstance(effect, dict):
            continue
        if not _effect_wav_ok(project, scope=scope, folder_name=folder_name, effect=effect):
            continue
        sfx_id = str(effect.get("sfx_id") or "").strip()
        path = Path(str(effect.get("wav_path") or ""))
        if not path.is_file():
            path = sfx_wav_path(
                project, scope=scope, folder_name=folder_name, sfx_id=sfx_id
            )
        if not path.is_file():
            continue
        item = dict(effect)
        item["wav_path"] = str(path)
        out.append(item)
    return out


def replace_canonical_sfx_set(
    project: Project,
    *,
    scope: SfxScope,
    folder_name: str,
    plan_payload: dict[str, Any],
    result_payload: dict[str, Any],
    temp_audio_dir: Path | None,
) -> None:
    """Atomically promote a temp regeneration into the canonical SFX set."""
    ensure_sfx_dir(project, scope=scope, folder_name=folder_name)
    audio_dir = sound_effects_audio_dir(project, scope=scope, folder_name=folder_name)
    backup_dir = audio_dir.with_name(f".audio_backup_{utc_now_iso().replace(':', '')}")
    had_audio = audio_dir.is_dir() and any(audio_dir.iterdir())
    if had_audio:
        if backup_dir.exists():
            shutil.rmtree(backup_dir, ignore_errors=True)
        shutil.move(str(audio_dir), str(backup_dir))
    audio_dir.mkdir(parents=True, exist_ok=True)
    try:
        if temp_audio_dir is not None and temp_audio_dir.is_dir():
            for src in sorted(temp_audio_dir.glob("*.wav")):
                dest = audio_dir / src.name
                shutil.copy2(src, dest)
        # Rewrite wav_path fields to canonical locations.
        effects = []
        for effect in list(result_payload.get("effects") or []):
            if not isinstance(effect, dict):
                continue
            item = dict(effect)
            sfx_id = str(item.get("sfx_id") or "").strip()
            if sfx_id and str(item.get("status") or "") == "completed":
                item["wav_path"] = str(
                    sfx_wav_path(
                        project, scope=scope, folder_name=folder_name, sfx_id=sfx_id
                    )
                )
            effects.append(item)
        result_payload = dict(result_payload)
        result_payload["effects"] = effects
        save_sfx_plan(project, plan_payload)
        save_sfx_result(project, result_payload)
    except Exception:
        # Restore previous audio on failure to write metadata.
        if backup_dir.is_dir():
            if audio_dir.exists():
                shutil.rmtree(audio_dir, ignore_errors=True)
            shutil.move(str(backup_dir), str(audio_dir))
        raise
    if backup_dir.is_dir():
        shutil.rmtree(backup_dir, ignore_errors=True)
    if temp_audio_dir is not None and temp_audio_dir.exists():
        shutil.rmtree(temp_audio_dir, ignore_errors=True)
