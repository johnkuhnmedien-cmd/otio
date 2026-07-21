"""Manuelle lokale Dateizuordnung für akzeptierte Stock-Supplements (R1/R2).

R2: export_ready nur nach echter Bild-/Videovalidierung — keine Endung und
kein bloßer 16-Byte-Leseversuch als Nachweis.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

from otio_app.models import Project
from otio_app.services.without_voiceover_enhanced.io_utils import load_model, write_json
from otio_app.services.without_voiceover_enhanced.models import (
    AcceptedSupplementsDocument,
    StockCandidate,
)
from otio_app.services.without_voiceover_enhanced.paths import accepted_supplements_path

STATUS_SELECTED = "selected"
STATUS_LOCAL_MEDIA_MISSING = "local_media_missing"
STATUS_LOCAL_MEDIA_INVALID = "local_media_invalid"
STATUS_LICENSE_REVIEW_REQUIRED = "license_review_required"
STATUS_EXPORT_READY = "export_ready"

# Supplements im Enhanced-Modell: photo/image/video (kein Audio).
_IMAGE_MEDIA_TYPES = frozenset({"photo", "image"})
_VIDEO_MEDIA_TYPES = frozenset({"video"})
_ALLOWED_MEDIA_TYPES = _IMAGE_MEDIA_TYPES | _VIDEO_MEDIA_TYPES


class LocalMediaError(RuntimeError):
    pass


def is_http_url(value: str | None) -> bool:
    text = (value or "").strip().lower()
    return text.startswith("http://") or text.startswith("https://")


def normalize_media_type(media_type: str | None) -> str | None:
    """Normalisiert erlaubte Medienarten; unbekannt → None (nicht raten)."""
    raw = (media_type or "").strip().lower()
    if not raw:
        return None
    if raw in _ALLOWED_MEDIA_TYPES:
        return raw
    return None


def _validate_image_media(path: Path) -> tuple[str, str | None]:
    """Bild muss mit Pillow verifizierbar und decodierbar sein (Format + Maße)."""
    try:
        from PIL import Image, UnidentifiedImageError
    except ImportError:
        return (
            STATUS_LOCAL_MEDIA_INVALID,
            "Bildvalidierung nicht möglich: Pillow (PIL) ist nicht installiert.",
        )

    try:
        with Image.open(path) as image:
            image.verify()
    except UnidentifiedImageError:
        return (
            STATUS_LOCAL_MEDIA_INVALID,
            f"Bildformat nicht erkannt oder Datei beschädigt: {path.name}",
        )
    except OSError as exc:
        return (
            STATUS_LOCAL_MEDIA_INVALID,
            f"Bild technisch ungültig/unvollständig: {exc}",
        )
    except Exception as exc:  # noqa: BLE001 — PIL kann diverse Fehler werfen
        return (
            STATUS_LOCAL_MEDIA_INVALID,
            f"Bildverifikation fehlgeschlagen: {exc}",
        )

    # verify() kann den Decoderzustand verbrauchen — erneut öffnen und laden.
    try:
        with Image.open(path) as image:
            image.load()
            width, height = image.size
            fmt = (image.format or "").strip()
    except OSError as exc:
        return (
            STATUS_LOCAL_MEDIA_INVALID,
            f"Bild nicht vollständig decodierbar: {exc}",
        )
    except Exception as exc:  # noqa: BLE001
        return (
            STATUS_LOCAL_MEDIA_INVALID,
            f"Bilddecodierung fehlgeschlagen: {exc}",
        )

    if not fmt:
        return (
            STATUS_LOCAL_MEDIA_INVALID,
            f"Kein Bildformat erkannt: {path.name}",
        )
    if width is None or height is None or int(width) <= 0 or int(height) <= 0:
        return (
            STATUS_LOCAL_MEDIA_INVALID,
            f"Ungültige Bildauflösung {width}×{height}: {path.name}",
        )
    return STATUS_EXPORT_READY, None


def _ffprobe_video_payload(path: Path) -> tuple[dict | None, str | None]:
    """Liefert ffprobe-JSON oder (None, Fehlertext)."""
    try:
        result = subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-show_entries",
                "format=format_name,duration:stream=codec_type,codec_name,width,height,duration",
                "-of",
                "json",
                str(path),
            ],
            capture_output=True,
            text=True,
            check=False,
            timeout=120,
        )
    except FileNotFoundError:
        return None, "ffprobe nicht gefunden — Videovalidierung nicht möglich."
    except subprocess.TimeoutExpired:
        return None, "ffprobe Timeout bei Videovalidierung."

    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip()
        return None, detail or f"ffprobe Exit {result.returncode}"

    try:
        payload = json.loads(result.stdout or "{}")
    except json.JSONDecodeError:
        return None, "ffprobe lieferte kein gültiges JSON."
    if not isinstance(payload, dict):
        return None, "ffprobe-Antwort ungültig."
    return payload, None


def _validate_video_media(path: Path) -> tuple[str, str | None]:
    """Video nur export_ready mit verwertbarer Videospur (Dauer + Auflösung)."""
    payload, probe_error = _ffprobe_video_payload(path)
    if payload is None:
        return (
            STATUS_LOCAL_MEDIA_INVALID,
            f"Video technisch ungültig: {probe_error}",
        )

    format_info = payload.get("format") or {}
    format_name = str(format_info.get("format_name") or "").strip()
    if not format_name:
        return (
            STATUS_LOCAL_MEDIA_INVALID,
            f"Container/Format nicht erkannt: {path.name}",
        )

    streams = payload.get("streams") or []
    video_stream: dict | None = None
    for stream in streams:
        if not isinstance(stream, dict):
            continue
        if (stream.get("codec_type") or "").lower() == "video":
            video_stream = stream
            break
    if video_stream is None:
        return (
            STATUS_LOCAL_MEDIA_INVALID,
            f"Keine Videospur erkannt: {path.name}",
        )

    width = video_stream.get("width")
    height = video_stream.get("height")
    try:
        width_i = int(width) if width is not None else 0
        height_i = int(height) if height is not None else 0
    except (TypeError, ValueError):
        width_i, height_i = 0, 0
    if width_i <= 0 or height_i <= 0:
        return (
            STATUS_LOCAL_MEDIA_INVALID,
            f"Ungültige Videoauflösung {width}×{height}: {path.name}",
        )

    duration: float | None = None
    for raw in (video_stream.get("duration"), format_info.get("duration")):
        if raw is None:
            continue
        try:
            duration = float(raw)
            break
        except (TypeError, ValueError):
            continue
    if duration is None or duration <= 0:
        return (
            STATUS_LOCAL_MEDIA_INVALID,
            f"Video-Dauer fehlt oder ist 0: {path.name}",
        )

    return STATUS_EXPORT_READY, None


def validate_local_media_path(
    raw_path: str | None,
    media_type: str | None = None,
) -> tuple[str, str | None]:
    """Gibt (status, error) zurück.

    media_type kommt bevorzugt von StockCandidate.media_type (photo|image|video).
    Unbekannte Medienart wird nicht geraten.
    """
    if raw_path is None or not str(raw_path).strip():
        return STATUS_LOCAL_MEDIA_MISSING, "Keine lokale Mediendatei zugeordnet."
    text = str(raw_path).strip()
    if is_http_url(text):
        return (
            STATUS_LOCAL_MEDIA_INVALID,
            "local_media_path darf keine HTTP/HTTPS-URL sein.",
        )
    path = Path(text).expanduser()
    if not path.is_file():
        return STATUS_LOCAL_MEDIA_MISSING, f"Lokale Datei existiert nicht: {path}"

    try:
        if path.stat().st_size <= 0:
            return (
                STATUS_LOCAL_MEDIA_INVALID,
                f"Lokale Datei ist leer: {path.name}",
            )
        with path.open("rb") as handle:
            head = handle.read(16)
        if not head:
            return (
                STATUS_LOCAL_MEDIA_INVALID,
                f"Lokale Datei ist leer/unlesbar: {path.name}",
            )
    except OSError as exc:
        return STATUS_LOCAL_MEDIA_INVALID, f"Lokale Datei technisch unlesbar: {exc}"

    normalized = normalize_media_type(media_type)
    if normalized is None:
        shown = (media_type or "").strip() or "(leer)"
        return (
            STATUS_LOCAL_MEDIA_INVALID,
            f"Unbekannte Medienart {shown!r}. Erlaubt: photo, image, video "
            "(keine Ableitung aus der Dateiendung).",
        )

    if normalized in _IMAGE_MEDIA_TYPES:
        return _validate_image_media(path)
    if normalized in _VIDEO_MEDIA_TYPES:
        return _validate_video_media(path)
    return (
        STATUS_LOCAL_MEDIA_INVALID,
        f"Unbekannte Medienart {normalized!r}.",
    )


def license_metadata_complete(candidate: StockCandidate) -> bool:
    """Separates Lizenz-Gate — LLM darf Lizenz nicht aus Bildern ableiten."""
    provider = (candidate.provider or "").strip().lower()
    has_license = bool((candidate.license or "").strip())
    has_source = bool((candidate.source_page or "").strip())
    if provider == "archive_org":
        return has_source
    return has_license and has_source


def refresh_supplement_validation(candidate: StockCandidate) -> StockCandidate:
    if not candidate.selected and not candidate.local_media_path:
        candidate.media_validation_status = STATUS_SELECTED
        candidate.media_validation_error = None
        return candidate
    if not candidate.local_media_path:
        candidate.media_validation_status = STATUS_LOCAL_MEDIA_MISSING
        candidate.media_validation_error = (
            f"Supplement {candidate.candidate_id} besitzt keine validierte lokale "
            "Mediendatei. Ordne zuerst eine lokale Originaldatei zu."
        )
        return candidate
    status, error = validate_local_media_path(
        candidate.local_media_path,
        media_type=candidate.media_type,
    )
    candidate.media_validation_status = status
    candidate.media_validation_error = error
    return candidate


def apply_license_export_gate(candidate: StockCandidate) -> StockCandidate:
    """Funnel/Lizenz-Gate: technisch gültig, aber Lizenz unvollständig → kein export_ready."""
    candidate = refresh_supplement_validation(candidate)
    if candidate.media_validation_status != STATUS_EXPORT_READY:
        return candidate
    if not license_metadata_complete(candidate):
        candidate.media_validation_status = STATUS_LICENSE_REVIEW_REQUIRED
        candidate.media_validation_error = (
            "Lizenzmetadaten unvollständig — kein export_ready."
        )
    return candidate


def assign_local_media_path(
    project: Project,
    candidate_id: str,
    local_media_path: str,
) -> StockCandidate:
    accepted = load_model(accepted_supplements_path(project), AcceptedSupplementsDocument)
    if accepted is None:
        raise LocalMediaError("Keine akzeptierten Supplements vorhanden.")
    updated: StockCandidate | None = None
    for index, candidate in enumerate(accepted.supplements):
        if candidate.candidate_id != candidate_id:
            continue
        candidate.local_media_path = str(local_media_path).strip()
        candidate.selected = True
        candidate = refresh_supplement_validation(candidate)
        accepted.supplements[index] = candidate
        updated = candidate
        break
    if updated is None:
        raise LocalMediaError(f"Unbekanntes Supplement: {candidate_id}")
    write_json(accepted_supplements_path(project), accepted)
    return updated


def list_export_ready_supplements(project: Project) -> list[StockCandidate]:
    accepted = load_model(accepted_supplements_path(project), AcceptedSupplementsDocument)
    if accepted is None:
        return []
    ready: list[StockCandidate] = []
    changed = False
    for index, candidate in enumerate(accepted.supplements):
        refreshed = refresh_supplement_validation(candidate)
        if refreshed.model_dump() != candidate.model_dump():
            accepted.supplements[index] = refreshed
            changed = True
        if refreshed.media_validation_status == STATUS_EXPORT_READY:
            ready.append(refreshed)
    if changed:
        write_json(accepted_supplements_path(project), accepted)
    return ready


def require_export_ready_local_path(candidate: StockCandidate) -> str:
    refreshed = refresh_supplement_validation(candidate)
    if refreshed.media_validation_status != STATUS_EXPORT_READY:
        raise LocalMediaError(
            refreshed.media_validation_error
            or (
                f"Supplement {candidate.candidate_id} besitzt keine validierte "
                "lokale Mediendatei. Ordne zuerst eine lokale Originaldatei zu."
            )
        )
    path = str(refreshed.local_media_path or "").strip()
    if is_http_url(path):
        raise LocalMediaError(
            f"Supplement {candidate.candidate_id}: lokale Mediendatei darf keine Web-URL sein."
        )
    return path
