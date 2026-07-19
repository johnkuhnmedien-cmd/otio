"""Worker: Discovery-V2 analysis-prepare (Phase 8B)."""

from __future__ import annotations

import math
import os
import shutil
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath

from PIL import Image, ImageOps, UnidentifiedImageError

from otio_app.discovery_v2.adapters.frame_sample import (
    MAX_FRAMES_PER_VIDEO,
    FrameSampleError,
    black_frame_candidate_timestamps,
    extract_video_frame_jpeg,
    prepare_still_preview,
    select_representative_timestamps,
)
from otio_app.discovery_v2.adapters.frame_signals import (
    FrameSignals,
    FrameSignalsError,
    compute_frame_signals,
)
from otio_app.discovery_v2.adapters.media_probe import (
    MediaProbeAdapterError,
    NormalizedMediaProbe,
    probe_source_media,
)
from otio_app.discovery_v2.adapters.shot_detect import (
    ShotDetectError,
    detect_scene_cut_seconds,
    normalize_shot_boundaries,
)
from otio_app.discovery_v2.adapters.source_hash import compute_sha256_hex
from otio_app.discovery_v2.analysis_paths import (
    analysis_frame_relative_path,
    analysis_temp_dir,
    analysis_temp_frame_relative_path,
    resolve_analysis_relative_path,
)
from otio_app.discovery_v2.domain.asset_analysis import (
    ANALYSIS_CONTRACT_PROFILE_VERSION,
    ANALYSIS_PREPARE_PROFILE_VERSION,
    ANALYSIS_RUN_SCOPE_PREPARE_ONLY,
    FRAME_SAMPLE_PROFILE_VERSION,
    SHOT_DETECT_PROFILE_VERSION,
    AnalysisInputIdentity,
    AnalysisPrepareAssetStatus,
    AnalysisRun,
    AnalysisRunAsset,
    AnalysisRunReport,
    AnalysisRunReportAsset,
    AnalysisRunReportCounts,
    AnalysisRunReportError,
    AnalysisRunStatus,
    RepresentativeFrameRecord,
    TechnicalShotRecord,
)
from otio_app.discovery_v2.domain.inventory import MediaKind
from otio_app.discovery_v2.persistence.asset_analysis_repository import (
    cleanup_analysis_temp,
    find_or_create_analysis_identity,
    get_analysis_run,
    list_analysis_run_assets,
    list_representative_frames,
    list_technical_shots,
    new_frame_id,
    new_shot_id,
    open_analysis_registry,
    replace_prepare_artifacts,
    save_analysis_run_report,
    update_analysis_run,
    update_analysis_run_asset,
)
from otio_app.discovery_v2.persistence.inventory_artifact_store import (
    InventoryArtifactError,
)
from otio_app.discovery_v2.paths import get_discovery_v2_root


_MIN_FREE_BYTES = 256 * 1024 * 1024
_ESTIMATED_FRAME_BYTES = 8 * 1024 * 1024
_ERROR_MESSAGES = {
    "stale_working_media": "Working-Media-Zeile passt nicht mehr zum Analysis-Run.",
    "working_media_missing": "Working-Media-Datei fehlt.",
    "working_media_hash_mismatch": "Working-Media-SHA-256 stimmt nicht mehr.",
    "invalid_working_media_path": "Working-Media-Pfad ist ungültig.",
    "unsupported_media_kind": "Medientyp wird von Analysis-Prepare nicht unterstützt.",
    "shot_detection_failed": "Shot-Detection fehlgeschlagen.",
    "invalid_shot_boundaries": "Ungültige Shot-Grenzen.",
    "frame_extraction_failed": "Frame-Extraktion fehlgeschlagen.",
    "frame_decode_failed": "Frame konnte nicht dekodiert werden.",
    "no_usable_frame": "Kein verwendbares Representative Frame erzeugt.",
    "analysis_frame_limit_exceeded": "Zu viele Representative Frames ausgewählt.",
    "analysis_artifact_conflict": "Analysis-Frame-Ziel enthält abweichende Datei.",
    "analysis_artifact_write_failed": "Analysis-Artefakt konnte nicht geschrieben werden.",
    "analysis_registry_write_failed": "Analysis-Artefakte konnten nicht registriert werden.",
    "insufficient_disk_space": "Zu wenig freier Speicherplatz für Analysis-Prepare.",
    "worker_interrupted": "Analysis-Prepare-Worker wurde unterbrochen.",
    "report_write_failed": "Analysis-Runbericht konnte nicht geschrieben werden.",
}


class AnalysisPrepareWorkerError(Exception):
    def __init__(self, code: str, message: str | None = None) -> None:
        super().__init__(message or _ERROR_MESSAGES.get(code, code))
        self.code = code
        self.message = message or _ERROR_MESSAGES.get(code, code)


@dataclass(frozen=True)
class ValidatedWorkingMedia:
    row: dict[str, object]
    path: Path
    file_size_bytes: int
    output_sha256: str


@dataclass(frozen=True)
class PreparedFrame:
    record: RepresentativeFrameRecord
    temp_path: Path
    final_path: Path
    final_exists_exact: bool = False


def _now() -> datetime:
    return datetime.now(timezone.utc)


def process_analysis_prepare_run(project_root: Path, run_id: str) -> None:
    """Process one queued analysis-prepare run."""
    root = Path(project_root).expanduser().resolve()
    conn = open_analysis_registry(root)
    try:
        run = get_analysis_run(conn, run_id=run_id)
        if run is None:
            return
        run = run.model_copy(
            update={
                "status": AnalysisRunStatus.RUNNING,
                "started_at": run.started_at or _now(),
                "analysis_profile_version": ANALYSIS_PREPARE_PROFILE_VERSION,
                "scope": run.scope or ANALYSIS_RUN_SCOPE_PREPARE_ONLY,
            }
        )
        update_analysis_run(conn, run)
        conn.commit()

        for asset in list_analysis_run_assets(conn, run_id=run.run_id):
            if asset.status == AnalysisPrepareAssetStatus.PREPARED:
                continue
            if asset.status == AnalysisPrepareAssetStatus.NOT_APPLICABLE:
                _mark_not_applicable(conn, asset)
                continue
            try:
                _assert_sufficient_disk_space(
                    root,
                    run_id=run.run_id,
                    estimated_frame_count=_estimated_frame_count(asset),
                )
                _process_one_asset(conn, root, run, asset)
            except AnalysisPrepareWorkerError as exc:
                _fail_asset(conn, asset, code=exc.code, message=exc.message)
            except Exception as exc:  # noqa: BLE001
                _fail_asset(
                    conn,
                    asset,
                    code="analysis_artifact_write_failed",
                    message=str(exc),
                )

        final_run = _finalize_run(conn, root, run.run_id)
        try:
            save_analysis_run_report(
                root,
                _build_report_from_analysis_run(conn, final_run),
            )
        except (InventoryArtifactError, OSError, ValueError) as exc:
            final_run = final_run.model_copy(
                update={
                    "status": (
                        AnalysisRunStatus.COMPLETED_WITH_ERRORS
                        if final_run.status == AnalysisRunStatus.COMPLETED
                        else final_run.status
                    ),
                    "error_summary": _append_error_summary(
                        final_run.error_summary,
                        f"report_write_failed: {exc}",
                    ),
                }
            )
            update_analysis_run(conn, final_run)
            conn.commit()
    finally:
        conn.close()
        cleanup_analysis_temp(root, run_id=run_id)


def _process_one_asset(
    conn: sqlite3.Connection,
    project_root: Path,
    run: AnalysisRun,
    asset: AnalysisRunAsset,
) -> None:
    kind = (asset.media_kind or "").strip().lower()
    if kind == MediaKind.AUDIO.value:
        _mark_not_applicable(conn, asset)
        return
    if kind not in {MediaKind.VIDEO.value, MediaKind.IMAGE.value}:
        raise AnalysisPrepareWorkerError("unsupported_media_kind")

    working = _validate_working_media(conn, project_root, run=run, asset=asset)
    try:
        identity = find_or_create_analysis_identity(
            conn,
            project_id=run.project_id,
            asset_id=asset.asset_id,
            working_media_id=asset.working_media_id,
            output_sha256=asset.output_sha256,
            processing_profile_version=asset.processing_profile_version,
            analysis_profile_version=ANALYSIS_CONTRACT_PROFILE_VERSION,
        )
        conn.commit()
    except Exception as exc:  # noqa: BLE001
        conn.rollback()
        raise AnalysisPrepareWorkerError(
            "analysis_registry_write_failed",
            str(exc),
        ) from exc

    if _existing_prepare_is_reusable(
        conn,
        project_root=project_root,
        analysis_identity_id=identity.analysis_identity_id,
        media_kind=kind,
    ):
        _mark_prepared(
            conn,
            asset,
            analysis_identity_id=identity.analysis_identity_id,
            reused=True,
        )
        return

    if kind == MediaKind.IMAGE.value:
        _prepare_image_asset(
            conn,
            project_root,
            run,
            asset.model_copy(
                update={"analysis_identity_id": identity.analysis_identity_id}
            ),
            working,
        )
        return

    _prepare_video_asset(
        conn,
        project_root,
        run,
        asset.model_copy(update={"analysis_identity_id": identity.analysis_identity_id}),
        working,
    )


def _prepare_image_asset(
    conn: sqlite3.Connection,
    project_root: Path,
    run: AnalysisRun,
    asset: AnalysisRunAsset,
    working: ValidatedWorkingMedia,
) -> None:
    assert asset.analysis_identity_id is not None
    asset = asset.model_copy(
        update={"status": AnalysisPrepareAssetStatus.EXTRACTING_FRAMES}
    )
    update_analysis_run_asset(conn, asset)
    conn.commit()

    frame_id = new_frame_id()
    temp_rel = analysis_temp_frame_relative_path(
        run_id=run.run_id,
        frame_id=frame_id,
        extension="jpg",
    )
    temp_requested = resolve_analysis_relative_path(project_root, temp_rel)
    temp_paths: list[Path] = [temp_requested]
    try:
        try:
            preview = prepare_still_preview(working.path, temp_requested)
        except FrameSampleError as exc:
            raise AnalysisPrepareWorkerError(
                _map_frame_sample_error(exc.code),
                exc.message,
            ) from exc
        temp_paths.append(preview.output_path)
        signals = _compute_signals(preview.output_path)
        width, height = _frame_dimensions(preview.output_path)
        size = preview.output_path.stat().st_size
        extension = preview.output_path.suffix.lower().lstrip(".") or "jpg"
        final_rel = analysis_frame_relative_path(
            working_media_id=asset.working_media_id,
            sampling_profile_version=FRAME_SAMPLE_PROFILE_VERSION,
            shot_or_still="still",
            frame_id=frame_id,
            extension=extension,
        )
        final_path = resolve_analysis_relative_path(project_root, final_rel)
        final_exists_exact = _final_path_exact_or_free(
            final_path,
            frame_sha256=signals.frame_sha256,
            file_size_bytes=size,
        )
        frame = RepresentativeFrameRecord(
            frame_id=frame_id,
            analysis_identity_id=asset.analysis_identity_id,
            project_id=run.project_id,
            asset_id=asset.asset_id,
            working_media_id=asset.working_media_id,
            shot_id=None,
            ordinal=0,
            timestamp_seconds=None,
            relative_path=final_rel,
            frame_sha256=signals.frame_sha256,
            pixel_sha256=signals.pixel_sha256,
            file_size_bytes=size,
            width=width,
            height=height,
            sampling_profile_version=FRAME_SAMPLE_PROFILE_VERSION,
            brightness_mean=signals.brightness_mean,
            black_fraction=signals.black_fraction,
            sharpness_score=signals.sharpness_score,
            is_black=signals.is_black,
            created_at=_now(),
        )
        _publish_prepared_frames(
            [
                PreparedFrame(
                    record=frame,
                    temp_path=preview.output_path,
                    final_path=final_path,
                    final_exists_exact=final_exists_exact,
                )
            ]
        )
        _replace_prepare_artifacts_or_fail(
            conn,
            asset.analysis_identity_id,
            shots=[],
            frames=[frame],
        )
        _mark_prepared(
            conn,
            asset,
            analysis_identity_id=asset.analysis_identity_id,
            reused=False,
        )
    finally:
        _cleanup_temp_paths(temp_paths)


def _prepare_video_asset(
    conn: sqlite3.Connection,
    project_root: Path,
    run: AnalysisRun,
    asset: AnalysisRunAsset,
    working: ValidatedWorkingMedia,
) -> None:
    assert asset.analysis_identity_id is not None
    asset = asset.model_copy(
        update={"status": AnalysisPrepareAssetStatus.DETECTING_SHOTS}
    )
    update_analysis_run_asset(conn, asset)
    conn.commit()

    try:
        probe = probe_source_media(working.path, media_kind=MediaKind.VIDEO)
    except MediaProbeAdapterError as exc:
        raise AnalysisPrepareWorkerError("shot_detection_failed", exc.message) from exc

    duration = _valid_video_duration(probe)
    try:
        cuts = detect_scene_cut_seconds(working.path, duration_seconds=duration)
        boundaries = normalize_shot_boundaries(duration, cuts)
    except ShotDetectError as exc:
        raise AnalysisPrepareWorkerError(exc.code, exc.message) from exc

    shots = [
        TechnicalShotRecord(
            shot_id=new_shot_id(),
            analysis_identity_id=asset.analysis_identity_id,
            project_id=run.project_id,
            asset_id=asset.asset_id,
            working_media_id=asset.working_media_id,
            ordinal=index,
            start_seconds=start,
            end_seconds=end,
            duration_seconds=end - start,
            detection_profile_version=SHOT_DETECT_PROFILE_VERSION,
            created_at=_now(),
        )
        for index, (start, end) in enumerate(boundaries)
    ]
    if not shots:
        raise AnalysisPrepareWorkerError("invalid_shot_boundaries")

    selections = select_representative_timestamps(shots)
    if len(selections) > MAX_FRAMES_PER_VIDEO:
        raise AnalysisPrepareWorkerError("analysis_frame_limit_exceeded")
    if not selections:
        raise AnalysisPrepareWorkerError("no_usable_frame")

    asset = asset.model_copy(
        update={"status": AnalysisPrepareAssetStatus.EXTRACTING_FRAMES}
    )
    update_analysis_run_asset(conn, asset)
    conn.commit()

    prepared: list[PreparedFrame] = []
    temp_paths: list[Path] = []
    try:
        for ordinal, (shot, timestamp) in enumerate(selections):
            prepared_frame = _extract_representative_video_frame(
                project_root=project_root,
                run=run,
                asset=asset,
                working_path=working.path,
                probe=probe,
                shot=shot if isinstance(shot, TechnicalShotRecord) else None,
                ordinal=ordinal,
                timestamp=timestamp,
            )
            prepared.append(prepared_frame)
            temp_paths.append(prepared_frame.temp_path)

        # Check every final path before publishing any frame.
        for frame in prepared:
            object.__setattr__(
                frame,
                "final_exists_exact",
                _final_path_exact_or_free(
                    frame.final_path,
                    frame_sha256=frame.record.frame_sha256,
                    file_size_bytes=frame.record.file_size_bytes,
                ),
            )

        _publish_prepared_frames(prepared)
        _replace_prepare_artifacts_or_fail(
            conn,
            asset.analysis_identity_id,
            shots=shots,
            frames=[frame.record for frame in prepared],
        )
        _mark_prepared(
            conn,
            asset,
            analysis_identity_id=asset.analysis_identity_id,
            reused=False,
        )
    finally:
        _cleanup_temp_paths(temp_paths)


def _extract_representative_video_frame(
    *,
    project_root: Path,
    run: AnalysisRun,
    asset: AnalysisRunAsset,
    working_path: Path,
    probe: NormalizedMediaProbe,
    shot: TechnicalShotRecord | None,
    ordinal: int,
    timestamp: float,
) -> PreparedFrame:
    frame_id = new_frame_id()
    temp_rel = analysis_temp_frame_relative_path(
        run_id=run.run_id,
        frame_id=frame_id,
        extension="jpg",
    )
    temp_path = resolve_analysis_relative_path(project_root, temp_rel)
    first_black: tuple[float, FrameSignals, int, int, int] | None = None
    last_error: AnalysisPrepareWorkerError | None = None
    if shot is not None:
        candidate_timestamps = black_frame_candidate_timestamps(shot) or [timestamp]
    else:
        candidate_timestamps = [timestamp]

    for candidate in candidate_timestamps:
        try:
            extract_video_frame_jpeg(
                working_path,
                temp_path,
                candidate,
                rotation_degrees=probe.rotation_degrees,
                source_probe=probe,
            )
            signals = _compute_signals(temp_path)
            width, height = _frame_dimensions(temp_path)
            size = temp_path.stat().st_size
        except FrameSampleError as exc:
            last_error = AnalysisPrepareWorkerError(
                _map_frame_sample_error(exc.code),
                exc.message,
            )
            continue
        except AnalysisPrepareWorkerError as exc:
            last_error = exc
            continue
        if not signals.is_black:
            return _prepared_video_frame(
                project_root=project_root,
                run=run,
                asset=asset,
                shot=shot,
                frame_id=frame_id,
                ordinal=ordinal,
                timestamp=candidate,
                temp_path=temp_path,
                signals=signals,
                width=width,
                height=height,
                file_size_bytes=size,
            )
        if first_black is None:
            first_black = (candidate, signals, width, height, size)

    if first_black is not None:
        # Temp-Datei wurde ggf. von späteren Kandidaten überschrieben —
        # Mittelpunkt deterministisch erneut extrahieren.
        candidate, _, _, _, _ = first_black
        try:
            extract_video_frame_jpeg(
                working_path,
                temp_path,
                candidate,
                rotation_degrees=probe.rotation_degrees,
                source_probe=probe,
            )
            signals = _compute_signals(temp_path)
            width, height = _frame_dimensions(temp_path)
            size = temp_path.stat().st_size
        except FrameSampleError as exc:
            raise AnalysisPrepareWorkerError(
                _map_frame_sample_error(exc.code),
                exc.message,
            ) from exc
        return _prepared_video_frame(
            project_root=project_root,
            run=run,
            asset=asset,
            shot=shot,
            frame_id=frame_id,
            ordinal=ordinal,
            timestamp=candidate,
            temp_path=temp_path,
            signals=signals,
            width=width,
            height=height,
            file_size_bytes=size,
        )
    if last_error is not None:
        raise last_error
    raise AnalysisPrepareWorkerError("no_usable_frame")


def _prepared_video_frame(
    *,
    project_root: Path,
    run: AnalysisRun,
    asset: AnalysisRunAsset,
    shot: TechnicalShotRecord | None,
    frame_id: str,
    ordinal: int,
    timestamp: float,
    temp_path: Path,
    signals: FrameSignals,
    width: int,
    height: int,
    file_size_bytes: int,
) -> PreparedFrame:
    shot_or_still = shot.shot_id if shot is not None else "overview"
    final_rel = analysis_frame_relative_path(
        working_media_id=asset.working_media_id,
        sampling_profile_version=FRAME_SAMPLE_PROFILE_VERSION,
        shot_or_still=shot_or_still,
        frame_id=frame_id,
        extension="jpg",
    )
    final_path = resolve_analysis_relative_path(project_root, final_rel)
    return PreparedFrame(
        record=RepresentativeFrameRecord(
            frame_id=frame_id,
            analysis_identity_id=asset.analysis_identity_id or "",
            project_id=run.project_id,
            asset_id=asset.asset_id,
            working_media_id=asset.working_media_id,
            shot_id=None if shot is None else shot.shot_id,
            ordinal=ordinal,
            timestamp_seconds=timestamp,
            relative_path=final_rel,
            frame_sha256=signals.frame_sha256,
            pixel_sha256=signals.pixel_sha256,
            file_size_bytes=file_size_bytes,
            width=width,
            height=height,
            sampling_profile_version=FRAME_SAMPLE_PROFILE_VERSION,
            brightness_mean=signals.brightness_mean,
            black_fraction=signals.black_fraction,
            sharpness_score=signals.sharpness_score,
            is_black=signals.is_black,
            created_at=_now(),
        ),
        temp_path=temp_path,
        final_path=final_path,
    )


def _validate_working_media(
    conn: sqlite3.Connection,
    project_root: Path,
    *,
    run: AnalysisRun,
    asset: AnalysisRunAsset,
) -> ValidatedWorkingMedia:
    row = conn.execute(
        """
        SELECT *
        FROM working_media
        WHERE working_media_id = ?
        """,
        (asset.working_media_id,),
    ).fetchone()
    if row is None:
        raise AnalysisPrepareWorkerError("stale_working_media")
    data = {key: row[key] for key in row.keys()}

    if str(data.get("status") or "") != "completed":
        raise AnalysisPrepareWorkerError("stale_working_media")
    for key, expected in (
        ("project_id", run.project_id),
        ("asset_id", asset.asset_id),
        ("working_media_id", asset.working_media_id),
        ("source_sha256", asset.source_sha256.lower()),
        ("output_sha256", asset.output_sha256.lower()),
        ("processing_profile_version", asset.processing_profile_version),
        ("media_kind", (asset.media_kind or "").strip().lower()),
    ):
        actual = str(data.get(key) or "")
        if key.endswith("sha256"):
            actual = actual.lower()
        if actual != str(expected):
            raise AnalysisPrepareWorkerError("stale_working_media")

    relative = str(data.get("working_relative_path") or "")
    working_path = _resolve_working_media_path(project_root, relative)
    if not working_path.exists():
        raise AnalysisPrepareWorkerError("working_media_missing")
    if working_path.is_symlink() or not working_path.is_file():
        raise AnalysisPrepareWorkerError("invalid_working_media_path")
    stat = working_path.stat()

    expected_size = _stored_working_media_size(data)
    if expected_size is not None and stat.st_size != expected_size:
        raise AnalysisPrepareWorkerError("stale_working_media")

    try:
        digest = compute_sha256_hex(working_path)
    except OSError as exc:
        raise AnalysisPrepareWorkerError("working_media_missing", str(exc)) from exc
    if digest.lower() != asset.output_sha256.lower():
        raise AnalysisPrepareWorkerError("working_media_hash_mismatch")

    return ValidatedWorkingMedia(
        row=data,
        path=working_path,
        file_size_bytes=stat.st_size,
        output_sha256=digest.lower(),
    )


def _resolve_working_media_path(project_root: Path, relative_path: str) -> Path:
    raw = relative_path.strip().replace("\\", "/")
    if not raw or raw.startswith("/") or (len(raw) > 1 and raw[1] == ":"):
        raise AnalysisPrepareWorkerError("invalid_working_media_path")
    parts = PurePosixPath(raw).parts
    if (
        len(parts) < 3
        or parts[0] != "media"
        or parts[1] != "working"
        or ".." in parts
        or any(part in {"", "."} for part in parts)
        or any(part.startswith("_otio") for part in parts)
    ):
        raise AnalysisPrepareWorkerError("invalid_working_media_path")
    root = get_discovery_v2_root(project_root)
    candidate = root / Path(*parts)
    try:
        resolved = candidate.resolve(strict=False)
        resolved.relative_to(root / "media" / "working")
        resolved.relative_to(root)
    except (OSError, ValueError) as exc:
        raise AnalysisPrepareWorkerError("invalid_working_media_path") from exc
    return candidate


def _stored_working_media_size(data: dict[str, object]) -> int | None:
    for key in ("file_size_bytes", "size_bytes", "output_size_bytes"):
        if key not in data or data[key] is None:
            continue
        try:
            value = int(str(data[key]))
        except (TypeError, ValueError):
            continue
        if value >= 0:
            return value
    return None


def _assert_sufficient_disk_space(
    project_root: Path,
    *,
    run_id: str,
    estimated_frame_count: int,
) -> None:
    target = analysis_temp_dir(project_root, run_id)
    check_path = target if target.exists() else target.parent
    if not check_path.exists():
        check_path = get_discovery_v2_root(project_root)
    needed = max(_MIN_FREE_BYTES, max(1, estimated_frame_count) * _ESTIMATED_FRAME_BYTES)
    try:
        usage = shutil.disk_usage(str(check_path))
    except OSError as exc:
        raise AnalysisPrepareWorkerError("insufficient_disk_space", str(exc)) from exc
    if usage.free < needed:
        raise AnalysisPrepareWorkerError(
            "insufficient_disk_space",
            f"Zu wenig freier Speicher: {usage.free} < {needed} Bytes.",
        )


def _estimated_frame_count(asset: AnalysisRunAsset) -> int:
    kind = (asset.media_kind or "").strip().lower()
    if kind == MediaKind.VIDEO.value:
        return MAX_FRAMES_PER_VIDEO
    if kind == MediaKind.IMAGE.value:
        return 1
    return 1


def _existing_prepare_is_reusable(
    conn: sqlite3.Connection,
    *,
    project_root: Path,
    analysis_identity_id: str,
    media_kind: str,
) -> bool:
    shots = list_technical_shots(conn, analysis_identity_id=analysis_identity_id)
    frames = list_representative_frames(conn, analysis_identity_id=analysis_identity_id)
    if media_kind == MediaKind.IMAGE.value:
        if shots or len(frames) != 1:
            return False
    elif media_kind == MediaKind.VIDEO.value:
        if not shots or not frames:
            return False
        if not _shot_bounds_are_valid(shots):
            return False
    else:
        return False
    return all(_frame_record_is_reusable(project_root, frame) for frame in frames)


def _shot_bounds_are_valid(shots: list[TechnicalShotRecord]) -> bool:
    previous_end: float | None = None
    for index, shot in enumerate(sorted(shots, key=lambda item: item.ordinal)):
        if shot.ordinal != index:
            return False
        if (
            not math.isfinite(shot.start_seconds)
            or not math.isfinite(shot.end_seconds)
            or shot.end_seconds <= shot.start_seconds
            or shot.duration_seconds <= 0
            or abs((shot.end_seconds - shot.start_seconds) - shot.duration_seconds)
            > 1e-6
        ):
            return False
        if previous_end is not None and abs(shot.start_seconds - previous_end) > 1e-6:
            return False
        previous_end = shot.end_seconds
    return True


def _frame_record_is_reusable(
    project_root: Path,
    frame: RepresentativeFrameRecord,
) -> bool:
    try:
        path = resolve_analysis_relative_path(project_root, frame.relative_path)
    except Exception:  # noqa: BLE001
        return False
    if path.is_symlink() or not path.is_file():
        return False
    try:
        if path.stat().st_size != frame.file_size_bytes:
            return False
        signals = compute_frame_signals(path)
        width, height = _frame_dimensions(path)
    except (OSError, FrameSignalsError, AnalysisPrepareWorkerError):
        return False
    return (
        signals.frame_sha256.lower() == frame.frame_sha256.lower()
        and signals.pixel_sha256.lower() == frame.pixel_sha256.lower()
        and width == frame.width
        and height == frame.height
        and abs(signals.brightness_mean - frame.brightness_mean) <= 1e-9
        and abs(signals.black_fraction - frame.black_fraction) <= 1e-9
        and abs(signals.sharpness_score - frame.sharpness_score) <= 1e-9
        and signals.is_black == frame.is_black
    )


def _final_path_exact_or_free(
    final_path: Path,
    *,
    frame_sha256: str,
    file_size_bytes: int,
) -> bool:
    if final_path.is_symlink():
        raise AnalysisPrepareWorkerError("analysis_artifact_conflict")
    if not final_path.exists():
        return False
    if not final_path.is_file():
        raise AnalysisPrepareWorkerError("analysis_artifact_conflict")
    try:
        stat = final_path.stat()
        digest = compute_sha256_hex(final_path)
    except OSError as exc:
        raise AnalysisPrepareWorkerError(
            "analysis_artifact_conflict",
            str(exc),
        ) from exc
    if stat.st_size == file_size_bytes and digest.lower() == frame_sha256.lower():
        return True
    raise AnalysisPrepareWorkerError("analysis_artifact_conflict")


def _publish_prepared_frames(frames: list[PreparedFrame]) -> None:
    for frame in frames:
        if frame.final_exists_exact:
            continue
        frame.final_path.parent.mkdir(parents=True, exist_ok=True)
    for frame in frames:
        if frame.final_exists_exact:
            continue
        try:
            os.replace(frame.temp_path, frame.final_path)
        except OSError as exc:
            raise AnalysisPrepareWorkerError(
                "analysis_artifact_write_failed",
                str(exc),
            ) from exc


def _replace_prepare_artifacts_or_fail(
    conn: sqlite3.Connection,
    analysis_identity_id: str,
    *,
    shots: list[TechnicalShotRecord],
    frames: list[RepresentativeFrameRecord],
) -> None:
    try:
        replace_prepare_artifacts(
            conn,
            analysis_identity_id=analysis_identity_id,
            shots=shots,
            frames=frames,
        )
        conn.commit()
    except Exception as exc:  # noqa: BLE001
        conn.rollback()
        raise AnalysisPrepareWorkerError(
            "analysis_registry_write_failed",
            str(exc),
        ) from exc


def _mark_prepared(
    conn: sqlite3.Connection,
    asset: AnalysisRunAsset,
    *,
    analysis_identity_id: str,
    reused: bool,
) -> None:
    updated = asset.model_copy(
        update={
            "status": AnalysisPrepareAssetStatus.PREPARED,
            "error_code": "reused" if reused else None,
            "error_message": (
                "Vorhandene Analysis-Prepare-Artefakte wiederverwendet."
                if reused
                else None
            ),
            "analysis_identity_id": analysis_identity_id,
            "completed_at": _now(),
        }
    )
    update_analysis_run_asset(conn, updated)
    conn.commit()


def _mark_not_applicable(
    conn: sqlite3.Connection,
    asset: AnalysisRunAsset,
) -> None:
    updated = asset.model_copy(
        update={
            "status": AnalysisPrepareAssetStatus.NOT_APPLICABLE,
            "error_code": "not_applicable",
            "error_message": "Audio besitzt keine visuellen Prepare-Artefakte.",
            "completed_at": asset.completed_at or _now(),
        }
    )
    update_analysis_run_asset(conn, updated)
    conn.commit()


def _fail_asset(
    conn: sqlite3.Connection,
    asset: AnalysisRunAsset,
    *,
    code: str,
    message: str,
) -> None:
    failed = asset.model_copy(
        update={
            "status": AnalysisPrepareAssetStatus.FAILED,
            "error_code": code,
            "error_message": message,
            "completed_at": _now(),
        }
    )
    update_analysis_run_asset(conn, failed)
    conn.commit()


def _finalize_run(
    conn: sqlite3.Connection,
    project_root: Path,
    run_id: str,
) -> AnalysisRun:
    run = get_analysis_run(conn, run_id=run_id)
    if run is None:
        raise AnalysisPrepareWorkerError("stale_working_media", f"Run fehlt: {run_id}")
    assets = list_analysis_run_assets(conn, run_id=run_id)
    total = len(assets)
    prepared = sum(
        1 for asset in assets if asset.status == AnalysisPrepareAssetStatus.PREPARED
    )
    reused = sum(
        1
        for asset in assets
        if asset.status == AnalysisPrepareAssetStatus.PREPARED
        and asset.error_code == "reused"
    )
    not_applicable = sum(
        1
        for asset in assets
        if asset.status == AnalysisPrepareAssetStatus.NOT_APPLICABLE
    )
    failed = sum(
        1 for asset in assets if asset.status == AnalysisPrepareAssetStatus.FAILED
    )
    interrupted = sum(
        1
        for asset in assets
        if asset.status == AnalysisPrepareAssetStatus.INTERRUPTED
    )
    error_count = failed + interrupted
    if total > 0 and error_count >= total:
        status = AnalysisRunStatus.FAILED
    elif error_count > 0:
        status = AnalysisRunStatus.COMPLETED_WITH_ERRORS
    else:
        status = AnalysisRunStatus.COMPLETED
    summary = None
    if error_count:
        first_error = next(
            (asset.error_code for asset in assets if asset.error_code),
            "analysis_prepare_failed",
        )
        summary = f"{error_count} Asset(s) fehlgeschlagen ({first_error})."
    final = run.model_copy(
        update={
            "status": status,
            "completed_at": _now(),
            "total_assets": total,
            "prepared_assets": prepared,
            "reused_assets": reused,
            "not_applicable_assets": not_applicable,
            "failed_assets": failed,
            "interrupted_assets": interrupted,
            "error_summary": summary,
        }
    )
    update_analysis_run(conn, final)
    conn.commit()
    cleanup_analysis_temp(project_root, run_id=run_id)
    return final


def _build_report_from_analysis_run(
    conn: sqlite3.Connection,
    run: AnalysisRun,
) -> AnalysisRunReport:
    assets = list_analysis_run_assets(conn, run_id=run.run_id)
    report_assets: list[AnalysisRunReportAsset] = []
    errors: list[AnalysisRunReportError] = []
    shot_total = 0
    frame_total = 0
    for asset in assets:
        shots = []
        frames = []
        if asset.analysis_identity_id:
            shots = list_technical_shots(
                conn,
                analysis_identity_id=asset.analysis_identity_id,
            )
            frames = list_representative_frames(
                conn,
                analysis_identity_id=asset.analysis_identity_id,
            )
        shot_total += len(shots)
        frame_total += len(frames)
        if asset.error_code and asset.error_code not in {"reused", "not_applicable"}:
            errors.append(
                AnalysisRunReportError(
                    asset_id=asset.asset_id,
                    error_code=asset.error_code,
                    error_message=asset.error_message,
                )
            )
        report_assets.append(
            AnalysisRunReportAsset(
                analysis_identity_id=asset.analysis_identity_id,
                asset_id=asset.asset_id,
                working_media_id=asset.working_media_id,
                media_kind=asset.media_kind,
                status=asset.status,
                shot_count=len(shots),
                frame_count=len(frames),
                relative_frame_paths=[frame.relative_path for frame in frames],
                error_code=asset.error_code,
                error_message=asset.error_message,
            )
        )
    counts = AnalysisRunReportCounts(
        total_assets=run.total_assets,
        prepared_assets=run.prepared_assets,
        reused_assets=run.reused_assets,
        not_applicable_assets=run.not_applicable_assets,
        failed_assets=run.failed_assets,
        interrupted_assets=run.interrupted_assets,
        shot_count=shot_total,
        frame_count=frame_total,
    )
    return AnalysisRunReport(
        run_id=run.run_id,
        project_id=run.project_id,
        scope=run.scope,
        analysis_profile_version=run.analysis_profile_version,
        status=run.status,
        started_at=run.started_at,
        completed_at=run.completed_at,
        created_at=run.created_at,
        input_identities=[
            AnalysisInputIdentity(
                project_id=run.project_id,
                asset_id=asset.asset_id,
                working_media_id=asset.working_media_id,
                validation_id=asset.validation_id,
                source_sha256=asset.source_sha256,
                output_sha256=asset.output_sha256,
                processing_profile_version=asset.processing_profile_version,
                media_kind=asset.media_kind,
                analysis_profile_version=asset.analysis_profile_version,
            )
            for asset in assets
        ],
        counts=counts,
        assets=report_assets,
        errors=errors,
        total_assets=counts.total_assets,
        prepared_assets=counts.prepared_assets,
        reused_assets=counts.reused_assets,
        not_applicable_assets=counts.not_applicable_assets,
        failed_assets=counts.failed_assets,
        interrupted_assets=counts.interrupted_assets,
        shot_count=counts.shot_count,
        frame_count=counts.frame_count,
    )


def _valid_video_duration(probe: NormalizedMediaProbe) -> float:
    try:
        duration = float(probe.duration_seconds or 0.0)
    except (TypeError, ValueError) as exc:
        raise AnalysisPrepareWorkerError("invalid_shot_boundaries") from exc
    if not math.isfinite(duration) or duration <= 0.0:
        raise AnalysisPrepareWorkerError("invalid_shot_boundaries")
    return duration


def _compute_signals(path: Path) -> FrameSignals:
    try:
        return compute_frame_signals(path)
    except FrameSignalsError as exc:
        raise AnalysisPrepareWorkerError("frame_decode_failed", exc.message) from exc


def _frame_dimensions(path: Path) -> tuple[int, int]:
    try:
        with Image.open(path) as image:
            working = ImageOps.exif_transpose(image)
            if working is None:
                raise AnalysisPrepareWorkerError("frame_decode_failed")
            working.load()
            return int(working.size[0]), int(working.size[1])
    except AnalysisPrepareWorkerError:
        raise
    except (UnidentifiedImageError, OSError) as exc:
        raise AnalysisPrepareWorkerError("frame_decode_failed", str(exc)) from exc


def _map_frame_sample_error(code: str) -> str:
    if code in {
        "frame_extraction_failed",
        "frame_decode_failed",
        "analysis_frame_limit_exceeded",
        "no_usable_frame",
    }:
        return code
    if code == "still_preview_failed":
        return "frame_extraction_failed"
    return "frame_extraction_failed"


def _cleanup_temp_paths(paths: list[Path]) -> None:
    seen: set[Path] = set()
    for path in paths:
        if path in seen:
            continue
        seen.add(path)
        try:
            if path.exists() and (path.is_file() or path.is_symlink()):
                path.unlink(missing_ok=True)
        except OSError:
            pass


def _append_error_summary(current: str | None, addition: str) -> str:
    if current:
        return f"{current}; {addition}"
    return addition


__all__ = ["process_analysis_prepare_run"]
