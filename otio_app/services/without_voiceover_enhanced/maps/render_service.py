"""Isolated Remotion map renderer for OTIO Enhanced.

Vendored engine lives next to this module. There is no import of Thomas and
no absolute Thomas path. Tests inject ``command_runner`` / ``media_probe``.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import signal
import subprocess
import tempfile
from collections import deque
from pathlib import Path
from threading import Event, Lock, Timer
from typing import Any, Callable, Optional

from otio_app.models import Project
from otio_app.services.without_voiceover_enhanced.maps.models import (
    ENGINE_STYLE_VERSION,
    MAP_DURATION_FRAMES,
    MAP_FPS,
    RENDER_STATUS_BLOCKED,
    RENDER_STATUS_CANCELLED,
    RENDER_STATUS_DONE,
    RENDER_STATUS_FAILED,
    RENDER_STATUS_IDLE,
    MapPlanItem,
)
from otio_app.services.without_voiceover_enhanced.maps.remotion_payload import (
    remotion_payload,
)
from otio_app.services.without_voiceover_enhanced.paths import (
    map_output_dir,
    map_render_cache_dir,
)

PROGRESS_PREFIX = "OTIO_MAP_RENDER_PROGRESS="
RENDERER_VERSION = "remotion-map-renderer-v11"


class MapRenderError(RuntimeError):
    pass


class MapRenderCancelled(MapRenderError):
    pass


CommandRunner = Callable[..., Any]
MediaProbe = Callable[[Path], dict[str, Any]]
ProgressCallback = Callable[[float], None]


def packaged_renderer_root() -> Path:
    return Path(__file__).resolve().parent / "remotion_renderer"


def status_from_progress(progress: float, *, phase: str = "") -> str:
    if phase == "validating":
        return "validating"
    if progress >= 1.0:
        return "done"
    if progress <= 0.0:
        return "waiting"
    if progress < 0.12:
        return "preparing"
    return "rendering"


def output_file_nonempty(path: str | Path | None) -> bool:
    if not path:
        return False
    candidate = Path(path)
    try:
        return candidate.is_file() and candidate.stat().st_size > 0
    except OSError:
        return False


class MapRenderer:
    def __init__(
        self,
        *,
        renderer_root: Path | None = None,
        node_binary: str = "node",
        ffprobe_binary: str = "ffprobe",
        nice_binary: str = "nice",
        nice_level: int = 10,
        render_timeout_seconds: float = 900,
        command_runner: CommandRunner | None = None,
        media_probe: MediaProbe | None = None,
    ) -> None:
        self.renderer_root = (renderer_root or packaged_renderer_root()).expanduser().resolve()
        self.node_binary = node_binary
        self.ffprobe_binary = ffprobe_binary
        self.nice_binary = nice_binary
        if not 0 <= nice_level <= 19:
            raise ValueError("Die Render-Priorität muss zwischen 0 und 19 liegen.")
        self.nice_level = nice_level
        if render_timeout_seconds <= 0:
            raise ValueError("Das Render-Zeitlimit muss größer als null sein.")
        self.render_timeout_seconds = render_timeout_seconds
        self.command_runner = command_runner
        self.media_probe = media_probe or self._probe_media
        self._process_lock = Lock()
        self._processes: list[subprocess.Popen] = []
        self._kill_all = Event()

    def readiness(self) -> dict[str, Any]:
        entry_point = self.renderer_root / "scripts" / "render.mjs"
        node_modules = self.renderer_root / "node_modules"
        checks = {
            "renderer_entry_point": entry_point.is_file(),
            "renderer_dependencies": node_modules.is_dir() or self.command_runner is not None,
            "node_binary": self._command_available(self.node_binary),
            "ffprobe_binary": self._command_available(self.ffprobe_binary),
            "nice_binary": self._command_available(self.nice_binary),
        }
        return {
            "ready": all(checks.values()),
            "checks": checks,
            "style_version": ENGINE_STYLE_VERSION,
            "renderer_version": RENDERER_VERSION,
            "renderer_root": str(self.renderer_root),
        }

    def kill_all(self) -> None:
        self._kill_all.set()
        with self._process_lock:
            processes = list(self._processes)
        for process in processes:
            self._kill_process_group(process)

    def reset_kill_flag(self) -> None:
        self._kill_all.clear()

    def render_item(
        self,
        project: Project,
        item: MapPlanItem,
        *,
        overwrite: bool = False,
        progress_callback: ProgressCallback | None = None,
        should_cancel: Callable[[], bool] | None = None,
    ) -> dict[str, Any]:
        """Render one map. Reuses an identical plan_hash output when possible."""
        if item.render_status == RENDER_STATUS_BLOCKED:
            raise MapRenderError(item.blocked_reason or "Koordinaten fehlen oder sind unsicher.")
        payload = remotion_payload(item)
        output_dir = map_output_dir(project)
        output_dir.mkdir(parents=True, exist_ok=True)
        target = output_dir / item.output_filename
        cache_dir = map_render_cache_dir(project) / item.plan_hash[:2] / item.plan_hash
        cache_media = cache_dir / "map.mp4"
        cache_manifest = cache_dir / "manifest.json"

        last_progress = 0.0

        def emit(value: float) -> None:
            nonlocal last_progress
            last_progress = max(last_progress, max(0.0, min(1.0, value)))
            if progress_callback:
                progress_callback(last_progress)

        if should_cancel and should_cancel():
            raise MapRenderCancelled("Render abgebrochen.")

        cached = self._read_cached(cache_manifest, cache_media)
        if cached and not overwrite:
            self._publish_output(Path(cached["absolute_path"]), target, cached["content_hash"])
            self._write_sidecar(target, item.plan_hash, str(cached["content_hash"]))
            emit(1.0)
            return {**cached, "export_path": str(target), "reused": True}

        if (
            not overwrite
            and output_file_nonempty(target)
            and self._sidecar_plan_hash(target) == item.plan_hash
        ):
            emit(1.0)
            return {
                "reused": True,
                "export_path": str(target),
                "content_hash": self._hash_file(target),
                "plan_hash": item.plan_hash,
            }

        entry_point = self.renderer_root / "scripts" / "render.mjs"
        node_modules = self.renderer_root / "node_modules"
        if self.command_runner is None and (not entry_point.is_file() or not node_modules.is_dir()):
            raise MapRenderError(
                "Der Kartenrenderer ist noch nicht eingerichtet "
                "(Remotion-Bundle / node_modules fehlen)."
            )

        output_dir.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix=".map-render-", dir=str(output_dir)) as temporary:
            temporary_root = Path(temporary)
            input_path = temporary_root / "map-plan.json"
            raw_output = temporary_root / "map-transition.mp4"
            input_path.write_text(
                json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True),
                encoding="utf-8",
            )
            command = self._render_command(entry_point, input_path, raw_output)
            if should_cancel and should_cancel():
                raise MapRenderCancelled("Render abgebrochen.")
            completed = self._run_renderer(
                command,
                cwd=str(self.renderer_root),
                progress_callback=progress_callback,
                should_cancel=should_cancel,
            )
            if should_cancel and should_cancel():
                raise MapRenderCancelled("Render abgebrochen.")
            if getattr(completed, "returncode", 1) != 0:
                detail = (
                    getattr(completed, "stderr", "")
                    or getattr(completed, "stdout", "")
                    or "Unbekannter Renderfehler"
                )
                raise MapRenderError(
                    "Die Kartenanimation konnte nicht gerendert werden: "
                    f"{str(detail)[-1200:].strip()}"
                )
            if not raw_output.is_file() or raw_output.stat().st_size == 0:
                raise MapRenderError("Der Kartenrenderer hat keine Videodatei erzeugt.")
            emit(0.97)
            technical = self.media_probe(raw_output)
            self.validate_technical(item, technical)
            content_hash = self._hash_file(raw_output)
            cache_dir.mkdir(parents=True, exist_ok=True)
            cached_target = cache_media
            if cached_target.exists():
                if self._hash_file(cached_target) != content_hash:
                    if overwrite:
                        os.replace(raw_output, cached_target)
                    else:
                        raise MapRenderError(
                            "Vorhandenes Karten-Asset hat einen unerwarteten Inhalt."
                        )
            else:
                os.replace(raw_output, cached_target)
            self._publish_output(cached_target, target, content_hash)
            result = {
                "map_sequence_id": item.map_sequence_id,
                "project_id": item.project_id,
                "chapter_id": item.chapter_id,
                "plan_hash": item.plan_hash,
                "content_hash": content_hash,
                "absolute_path": str(cached_target),
                "export_path": str(target),
                "duration_in_frames": item.duration_in_frames,
                "fps": item.fps,
                "width": technical["width"],
                "height": technical["height"],
                "has_audio": technical["audio_stream_count"] > 0,
                "audio_codec": technical.get("audio_codec") or "",
                "style_version": ENGINE_STYLE_VERSION,
                "reused": False,
            }
            cache_manifest.write_text(
                json.dumps(result, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            self._write_sidecar(target, item.plan_hash, content_hash)
            emit(1.0)
            return result

    def _render_command(self, entry_point: Path, input_path: Path, output_path: Path) -> list[str]:
        node_args = [
            self.node_binary,
            str(entry_point),
            "--input",
            str(input_path),
            "--output",
            str(output_path),
        ]
        if self._command_available(self.nice_binary):
            return [self.nice_binary, "-n", str(self.nice_level), *node_args]
        return node_args

    def _run_renderer(
        self,
        command: list[str],
        cwd: str,
        progress_callback: ProgressCallback | None,
        should_cancel: Callable[[], bool] | None = None,
    ) -> subprocess.CompletedProcess:
        if self.command_runner is not None:
            completed = self.command_runner(
                command,
                cwd=cwd,
                capture_output=True,
                text=True,
                timeout=self.render_timeout_seconds,
                check=False,
            )
            return completed

        output_tail: deque[str] = deque(maxlen=240)
        last_progress = 0.0

        def emit(value: float) -> None:
            nonlocal last_progress
            last_progress = max(last_progress, max(0.0, min(1.0, value)))
            if progress_callback:
                progress_callback(last_progress)

        process = subprocess.Popen(
            command,
            cwd=cwd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
            start_new_session=os.name != "nt",
        )
        with self._process_lock:
            self._processes.append(process)
        timed_out = Event()

        def terminate_renderer() -> None:
            timed_out.set()
            self._kill_process_group(process)

        timeout = Timer(self.render_timeout_seconds, terminate_renderer)
        timeout.start()
        try:
            if self._kill_all.is_set() or (should_cancel and should_cancel()):
                self._kill_process_group(process)
            if process.stdout is not None:
                for line in process.stdout:
                    if self._kill_all.is_set() or (should_cancel and should_cancel()):
                        self._kill_process_group(process)
                        break
                    stripped = line.strip()
                    if stripped.startswith(PROGRESS_PREFIX):
                        try:
                            value = float(stripped.split("=", 1)[1])
                        except ValueError:
                            continue
                        emit(value)
                    else:
                        output_tail.append(stripped)
            returncode = process.wait()
        finally:
            timeout.cancel()
            if process.stdout is not None:
                process.stdout.close()
            with self._process_lock:
                if process in self._processes:
                    self._processes.remove(process)
        stderr = ""
        if timed_out.is_set():
            stderr = (
                "Der Kartenrenderer hat das Zeitlimit von "
                f"{self.render_timeout_seconds:g} Sekunden überschritten."
            )
        elif self._kill_all.is_set() or (should_cancel and should_cancel()):
            stderr = "Render abgebrochen."
            if returncode == 0:
                returncode = 1
        return subprocess.CompletedProcess(command, returncode, "\n".join(output_tail), stderr)

    @staticmethod
    def _kill_process_group(process: subprocess.Popen) -> None:
        try:
            if os.name != "nt" and process.pid:
                os.killpg(process.pid, signal.SIGKILL)
            else:
                process.kill()
        except (ProcessLookupError, PermissionError, OSError):
            try:
                process.kill()
            except (ProcessLookupError, PermissionError, OSError):
                pass

    @staticmethod
    def _command_available(command: str) -> bool:
        candidate = Path(command).expanduser()
        if candidate.is_absolute() or candidate.parent != Path("."):
            return candidate.is_file() and os.access(candidate, os.X_OK)
        return shutil.which(command) is not None

    def _probe_media(self, path: Path) -> dict[str, Any]:
        completed = subprocess.run(
            [
                self.ffprobe_binary,
                "-v",
                "error",
                "-show_entries",
                "stream=codec_type,codec_name,width,height,avg_frame_rate,nb_frames",
                "-of",
                "json",
                str(path),
            ],
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
        if completed.returncode != 0:
            raise MapRenderError("Das gerenderte Karten-Asset konnte nicht geprüft werden.")
        try:
            streams = json.loads(completed.stdout)["streams"]
            stream = next(item for item in streams if item.get("codec_type") == "video")
            audio_streams = [item for item in streams if item.get("codec_type") == "audio"]
            numerator, denominator = str(stream["avg_frame_rate"]).split("/", 1)
            frames = stream.get("nb_frames")
            return {
                "width": int(stream["width"]),
                "height": int(stream["height"]),
                "fps": int(numerator) / int(denominator),
                "frames": int(frames) if str(frames).isdigit() else 0,
                "audio_stream_count": len(audio_streams),
                "audio_codec": (
                    str(audio_streams[0].get("codec_name", "")) if audio_streams else ""
                ),
            }
        except (KeyError, IndexError, StopIteration, TypeError, ValueError) as error:
            raise MapRenderError("Das Karten-Asset enthält keine belastbaren Videodaten.") from error

    @staticmethod
    def validate_technical(item: MapPlanItem, technical: dict[str, Any]) -> None:
        if int(technical.get("width", 0)) != int(item.width) or int(
            technical.get("height", 0)
        ) != int(item.height):
            raise MapRenderError("Die Kartenanimation hat nicht die gewählte Auflösung.")
        if abs(float(technical.get("fps", 0)) - float(item.fps or MAP_FPS)) > 0.001:
            raise MapRenderError("Die Bildrate der Kartenanimation ist falsch.")
        frames = int(technical.get("frames", 0) or 0)
        expected = int(item.duration_in_frames or MAP_DURATION_FRAMES)
        if frames and frames != expected:
            raise MapRenderError("Die Kartenanimation hat nicht die geplante Frameanzahl.")
        if frames == 0:
            # Some encoders omit nb_frames; duration check is then skipped here
            # but empty files are already rejected before probe.
            pass
        if int(technical.get("audio_stream_count", 0)) != 0:
            raise MapRenderError("Die Kartenanimation muss ohne Audiospur erzeugt werden.")

    def _read_cached(self, manifest_path: Path, media_path: Path) -> dict[str, Any] | None:
        if not manifest_path.is_file() or not media_path.is_file():
            return None
        try:
            result = json.loads(manifest_path.read_text(encoding="utf-8"))
            if self._hash_file(media_path) != result.get("content_hash"):
                return None
            if media_path.stat().st_size == 0:
                return None
            result["absolute_path"] = str(media_path)
            return result
        except (OSError, ValueError, json.JSONDecodeError, KeyError):
            return None

    def _publish_output(self, source: Path, target: Path, content_hash: str) -> None:
        if target.is_file() and self._hash_file(target) == content_hash:
            return
        temporary = target.with_name(f".{target.stem}.{content_hash[:12]}.tmp{target.suffix}")
        shutil.copyfile(source, temporary)
        if self._hash_file(temporary) != content_hash:
            temporary.unlink(missing_ok=True)
            raise MapRenderError("Die ausgegebene Kartenkopie ist unvollständig.")
        os.replace(temporary, target)

    @staticmethod
    def _sidecar_path(target: Path) -> Path:
        return target.with_suffix(target.suffix + ".meta.json")

    def _write_sidecar(self, target: Path, plan_hash: str, content_hash: str) -> None:
        payload = {"plan_hash": plan_hash, "content_hash": content_hash}
        path = self._sidecar_path(target)
        path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")

    def _sidecar_plan_hash(self, target: Path) -> str:
        path = self._sidecar_path(target)
        if not path.is_file():
            return ""
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            return str(payload.get("plan_hash") or "")
        except (OSError, ValueError, json.JSONDecodeError):
            return ""

    @staticmethod
    def _hash_file(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()


def selectable_maps(
    items: list[MapPlanItem],
    *,
    mode: str,
    chapter_id: str | None = None,
) -> list[MapPlanItem]:
    """Pick maps for an explicit render click. Blocked maps are never selected."""
    available = [item for item in items if item.render_status != RENDER_STATUS_BLOCKED]
    if mode == "one" or chapter_id:
        if not chapter_id:
            return []
        return [item for item in available if item.chapter_id == chapter_id]
    if mode == "missing":
        selected: list[MapPlanItem] = []
        for item in available:
            if item.render_status in {
                RENDER_STATUS_IDLE,
                RENDER_STATUS_FAILED,
                RENDER_STATUS_CANCELLED,
            }:
                selected.append(item)
                continue
            if item.render_status == RENDER_STATUS_DONE and output_file_nonempty(item.output_path):
                continue
            if not output_file_nonempty(item.output_path):
                selected.append(item)
        return selected
    return available
