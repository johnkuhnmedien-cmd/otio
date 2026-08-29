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


# Bekannte Provider-Lizenzen aus den Adaptern (kein LLM-Erfinden).
_KNOWN_PROVIDER_LICENSES: dict[str, str] = {
    "pexels": "Pexels License",
    "pixabay": "Pixabay License",
}

LICENSE_METADATA_COMPLETE = "complete"
LICENSE_METADATA_PARTIAL = "partial"
LICENSE_METADATA_MISSING = "missing"


def license_metadata_complete(candidate: StockCandidate) -> bool:
    """Klassifikation „vollständig“ — LLM darf Lizenz nicht erzeugen/ergänzen.

    Providerregeln (nur informativ; blockiert export_ready seit R3 nicht):
    - Pexels/Pixabay: bekannte Adapter-Lizenz; Creator optional.
    - Wikimedia/Openverse/Archive.org: konkrete Asset-Lizenz + Creator/Attribution.
    """
    provider = (candidate.provider or "").strip().lower()
    if not provider:
        return False
    if not str(candidate.provider_asset_id or "").strip():
        return False
    source = (candidate.source_page or "").strip()
    if not source:
        return False
    license_name = (candidate.license or "").strip()
    creator = (candidate.creator or "").strip()
    attribution = (candidate.attribution or "").strip()
    has_creator = bool(creator or attribution)

    if provider in _KNOWN_PROVIDER_LICENSES:
        known = _KNOWN_PROVIDER_LICENSES[provider]
        if not license_name:
            return False
        if license_name.casefold() != known.casefold():
            return False
        return True

    if provider in {"wikimedia", "openverse", "archive_org"}:
        return bool(license_name) and has_creator

    return bool(license_name) and has_creator


def classify_license_metadata_status(candidate: StockCandidate) -> str:
    """Informativer Status: complete | partial | missing. Kein LLM-Erfinden."""
    if license_metadata_complete(candidate):
        return LICENSE_METADATA_COMPLETE
    has_any = any(
        str(value or "").strip()
        for value in (
            candidate.license,
            getattr(candidate, "license_url", None),
            candidate.source_page,
            candidate.creator,
            candidate.attribution,
        )
    )
    if has_any:
        return LICENSE_METADATA_PARTIAL
    return LICENSE_METADATA_MISSING


def _apply_funnel_license_gate_if_needed(
    candidate: StockCandidate,
    *,
    technical_status: str,
    technical_error: str | None,
) -> StockCandidate:
    """R3: Lizenzmetadaten sind informativ — blockieren export_ready nicht."""
    if technical_status != STATUS_EXPORT_READY:
        candidate.media_validation_status = technical_status
        candidate.media_validation_error = technical_error
        if getattr(candidate, "funnel_managed", False):
            candidate.license_metadata_status = classify_license_metadata_status(
                candidate
            )
        return candidate
    candidate.media_validation_status = STATUS_EXPORT_READY
    candidate.media_validation_error = None
    if getattr(candidate, "funnel_managed", False):
        candidate.license_metadata_status = classify_license_metadata_status(candidate)
    return candidate


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
    if is_http_url(candidate.local_media_path):
        candidate.media_validation_status = STATUS_LOCAL_MEDIA_INVALID
        candidate.media_validation_error = (
            "Remote-URL als local_media_path ist nicht erlaubt."
        )
        return candidate
    status, error = validate_local_media_path(
        candidate.local_media_path,
        media_type=candidate.media_type,
    )
    return _apply_funnel_license_gate_if_needed(
        candidate, technical_status=status, technical_error=error
    )


def apply_license_export_gate(candidate: StockCandidate) -> StockCandidate:
    """Explizites Funnel-Lizenz-Gate (setzt funnel_managed und prüft erneut)."""
    candidate.funnel_managed = True
    return refresh_supplement_validation(candidate)


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
        path_value = str(local_media_path).strip()
        if is_http_url(path_value):
            raise LocalMediaError(
                "Remote-URL als local_media_path ist nicht erlaubt."
            )
        candidate.local_media_path = path_value
        candidate.selected = True
        # Funnel-Merkmal bleibt erhalten — Gate darf nicht umgangen werden.
        candidate = refresh_supplement_validation(candidate)
        accepted.supplements[index] = candidate
        updated = candidate
        break
    if updated is None:
        raise LocalMediaError(f"Unbekanntes Supplement: {candidate_id}")
    write_json(accepted_supplements_path(project), accepted)
    return updated


def _expected_cut_plan_run_id(project: Project) -> str:
    """Aktuelle Cut-Plan-Run-ID aus coverage_gaps / unified_cut_plan."""
    from otio_app.services.without_voiceover_enhanced.gap_status_service import (
        compute_cut_plan_run_id_from_path,
    )
    from otio_app.services.without_voiceover_enhanced.models import CoverageGapsDocument
    from otio_app.services.without_voiceover_enhanced.paths import (
        coverage_gaps_path,
        unified_cut_plan_path,
    )

    coverage = load_model(coverage_gaps_path(project), CoverageGapsDocument)
    run_id = str(getattr(coverage, "cut_plan_run_id", "") or "").strip()
    if run_id:
        return run_id
    return compute_cut_plan_run_id_from_path(unified_cut_plan_path(project))


def _is_stale_accepted_supplement(
    candidate: StockCandidate, *, expected_run_id: str
) -> bool:
    """E2E-4: ohne/fremde run_id oder Legacy-Bridge-Gap → stale.

    Migration ohne run_id nur wenn ein aktueller Cut-Plan-Lauf bekannt ist.
    """
    gap_id = str(candidate.gap_id or "").strip()
    if gap_id.startswith("gap_bridge_") or gap_id == "gap_bridge_001":
        return True
    if not expected_run_id:
        return False
    cand_run = str(getattr(candidate, "cut_plan_run_id", "") or "").strip()
    if not cand_run:
        return True
    return cand_run != expected_run_id


def _candidate_id_filename_needles(candidate_id: str) -> list[str]:
    """Dateiname-Varianten: Bindestrich-UUID, Slug, Unterstriche."""
    cid = (candidate_id or "").strip()
    if not cid:
        return []
    from otio_app.project_layout import safe_folder_slug

    variants = [cid, safe_folder_slug(cid), cid.replace("-", "_"), cid.replace("_", "-")]
    out: list[str] = []
    seen: set[str] = set()
    for raw in variants:
        text = str(raw or "").strip()
        key = text.casefold()
        if not text or key in seen:
            continue
        seen.add(key)
        out.append(text)
    return out


def _pick_preferred_media_match(matches: list[Path]) -> Path | None:
    files = [path for path in matches if path.is_file()]
    if not files:
        return None
    files.sort(
        key=lambda p: (
            "/clean/" not in str(p).replace("\\", "/").lower(),
            p.suffix.lower() != ".mp4",
            -len(p.name),
            str(p),
        )
    )
    return files[0]


def find_clean_media_for_candidate(
    project: Project, *, candidate_id: str, folder_name: str = ""
) -> Path | None:
    """Sucht Clean-Kopie zu einer Funnel-/Stock-ID.

    Enhanced legt Clean unter ``work_dir/clean/<Ordner>/`` ab, nicht unter
    ``project_root/clean/``. Dateinamen nutzen oft den Slug (Bindestriche →
    Unterstriche), der Cut-Plan aber die originale ``openverse_<uuid>``.
    """
    return find_local_media_for_candidate_id(
        project, candidate_id=candidate_id, folder_name=folder_name
    )


def find_local_media_for_candidate_id(
    project: Project, *, candidate_id: str, folder_name: str = ""
) -> Path | None:
    """Clean (Work-Dir + Legacy-Root) und ``stock/downloads`` zur Stock-ID."""
    from otio_app.project_layout import (
        get_clean_media_output_dir,
        get_folder_clean_output_dir,
    )
    from otio_app.services.media_utils import MEDIA_EXTENSIONS
    from otio_app.services.without_voiceover_enhanced.paths import (
        iter_stock_download_dirs,
    )

    needles = _candidate_id_filename_needles(candidate_id)
    if not needles:
        return None
    roots: list[Path] = []
    folder = (folder_name or "").strip()
    if folder:
        roots.append(get_folder_clean_output_dir(project.work_dir_path, folder))
    roots.append(get_clean_media_output_dir(project.work_dir_path))
    roots.append(Path(project.project_root).expanduser() / "clean")
    roots.extend(iter_stock_download_dirs(project))

    matches: list[Path] = []
    seen: set[str] = set()
    for root in roots:
        try:
            if not root.is_dir():
                continue
        except OSError:
            continue
        for needle in needles:
            try:
                found = list(root.rglob(f"*{needle}*"))
            except OSError:
                continue
            for path in found:
                try:
                    if not path.is_file():
                        continue
                except OSError:
                    continue
                if path.suffix.lower() not in MEDIA_EXTENSIONS:
                    continue
                try:
                    key = str(path.resolve())
                except OSError:
                    key = str(path)
                if key in seen:
                    continue
                seen.add(key)
                matches.append(path)
    return _pick_preferred_media_match(matches)


def reconcile_accepted_supplement_paths(project: Project) -> int:
    """Accepted ``stock/downloads`` → vorhandene Clean-Kopie umbiegen.

    Behebt Altbestand, bei dem Funnel Accepted vor Clean geschrieben hat.
    """
    path = accepted_supplements_path(project)
    accepted = load_model(path, AcceptedSupplementsDocument)
    if accepted is None or not accepted.supplements:
        return 0
    updated: list[StockCandidate] = []
    changed_n = 0
    for candidate in accepted.supplements:
        local = str(candidate.local_media_path or "").replace("\\", "/")
        if "/stock/downloads/" not in local.lower():
            updated.append(candidate)
            continue
        clean = find_clean_media_for_candidate(
            project, candidate_id=str(candidate.candidate_id or "")
        )
        if clean is None or not clean.is_file():
            updated.append(candidate)
            continue
        updated.append(
            candidate.model_copy(update={"local_media_path": str(clean.resolve())})
        )
        changed_n += 1
    if changed_n:
        write_json(
            path,
            accepted.model_copy(update={"supplements": updated}),
        )
    return changed_n


def migrate_accepted_supplements(project: Project) -> AcceptedSupplementsDocument | None:
    """Bereinigt Bridge-Gaps; Run-ID-Drift wird rebound statt gelöscht.

    Früher: fremde/fehlende ``cut_plan_run_id`` → Eintrag weg. Das hat nach
    LLM-Recuts alle manuellen/Funnel-Fills vernichtet. Jetzt: Rebind auf den
    aktuellen Lauf (gleiche Gap-ID), nur Legacy-``gap_bridge_*`` fliegt raus.
    Zusätzlich: Accepted-Pfade von stock/downloads auf clean umbiegen.
    """
    from otio_app.services.without_voiceover_enhanced.gap_status_service import (
        rebind_gap_fills_to_current_run,
    )

    rebind_gap_fills_to_current_run(project)
    reconcile_accepted_supplement_paths(project)

    path = accepted_supplements_path(project)
    accepted = load_model(path, AcceptedSupplementsDocument)
    if accepted is None:
        return None
    kept: list[StockCandidate] = []
    changed = False
    for candidate in accepted.supplements:
        gap_id = str(candidate.gap_id or "").strip()
        if gap_id.startswith("gap_bridge_") or gap_id == "gap_bridge_001":
            changed = True
            continue
        kept.append(candidate)
    if changed:
        accepted = AcceptedSupplementsDocument(
            schema_version=accepted.schema_version,
            script_version=accepted.script_version,
            supplements=kept,
        )
        write_json(path, accepted)
    return accepted


def list_export_ready_supplements(project: Project) -> list[StockCandidate]:
    """export_ready Supplements der aktuellen Cut-Plan-Run-ID.

    Migration reboundet Run-ID-Drift und entfernt nur Legacy-Bridge-Gaps.
    """
    accepted = migrate_accepted_supplements(project)
    if accepted is None:
        return []
    expected_run_id = _expected_cut_plan_run_id(project)
    ready: list[StockCandidate] = []
    refreshed_list: list[StockCandidate] = []
    changed = False
    for candidate in accepted.supplements:
        gap_id = str(candidate.gap_id or "").strip()
        if gap_id.startswith("gap_bridge_") or gap_id == "gap_bridge_001":
            changed = True
            continue
        cand_run = str(getattr(candidate, "cut_plan_run_id", "") or "").strip()
        if expected_run_id and cand_run and cand_run != expected_run_id:
            # Noch nicht reboundbar (Gap fehlt im aktuellen Plan) — behalten,
            # aber nicht als export_ready für diesen Lauf ausliefern.
            refreshed_list.append(candidate)
            continue
        if expected_run_id and not cand_run:
            refreshed_list.append(candidate)
            continue
        refreshed = refresh_supplement_validation(candidate)
        if refreshed.model_dump() != candidate.model_dump():
            changed = True
        refreshed_list.append(refreshed)
        if refreshed.media_validation_status == STATUS_EXPORT_READY:
            ready.append(refreshed)
    if changed:
        write_json(
            accepted_supplements_path(project),
            AcceptedSupplementsDocument(
                schema_version=accepted.schema_version,
                script_version=accepted.script_version,
                supplements=refreshed_list,
            ),
        )
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
