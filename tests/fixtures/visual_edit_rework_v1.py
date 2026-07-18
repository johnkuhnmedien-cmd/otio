"""Deterministic fixtures for Visual Edit / Repair Rework V1 (reproduction only)."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from otio_app.discovery_v2.application.observation_review_service import (
    EditorialReadyObservationView,
)
from otio_app.discovery_v2.domain.asset_analysis import (
    ANALYSIS_CONTRACT_PROFILE_VERSION,
    SHOT_DETECT_PROFILE_VERSION,
    TechnicalShotRecord,
)
from otio_app.discovery_v2.domain.media_intake import (
    COPY_WORKING_ACTION,
    COPY_WORKING_PROFILE_VERSION,
    WorkingMediaRecord,
    WorkingMediaStatus,
)
from otio_app.discovery_v2.domain.observation_review import ObservationReviewRecord
from otio_app.discovery_v2.domain.visual_edit import (
    ASSET_REUSE_MAX,
    SOURCE_RANGE_OVERLAP_RATIO_MAX,
)
from otio_app.discovery_v2.paths import get_discovery_v2_root
from otio_app.discovery_v2.persistence import asset_analysis_repository as analysis_repo
from otio_app.discovery_v2.persistence import asset_registry_database as reg_db
from otio_app.discovery_v2.persistence import copy_intake_repository as copy_repo

# Stable synthetic asset labels for the alpha E3/E4 reproduction.
ASSET_LABELS = ("A", "B", "C", "D", "E", "F")
ASSET_IDS = tuple(f"asset-rework-v1-{label}" for label in ASSET_LABELS)
ASSET_A_ID = ASSET_IDS[0]

# Short technical shot forces Fake bias variants into ≥90% overlapping ranges (E4).
TECH_SHOT_START_SECONDS = 0.0
TECH_SHOT_END_SECONDS = 1.2


def _now() -> datetime:
    return datetime.now(timezone.utc)


def plan_content_fingerprint(bundle) -> str:
    """Stable content fingerprint for loop reproduction (not a product API)."""

    payload = {
        "plan_id": bundle.plan.plan_id,
        "plan_version": bundle.plan.plan_version,
        "input_fingerprint": bundle.plan.input_fingerprint,
        "shots": [
            {
                "shot_id": shot.shot_id,
                "ordinal": shot.ordinal,
                "media_strategy": shot.media_strategy,
                "duration_seconds": shot.duration_seconds,
            }
            for shot in sorted(bundle.shots, key=lambda item: item.ordinal)
        ],
        "assignments": [
            {
                "assignment_id": item.assignment_id,
                "shot_id": item.shot_id,
                "asset_id": item.asset_id,
                "working_media_id": item.working_media_id,
                "technical_shot_id": item.technical_shot_id,
                "technical_source_in_seconds": item.technical_source_in_seconds,
                "technical_source_out_seconds": item.technical_source_out_seconds,
            }
            for item in sorted(bundle.assignments, key=lambda item: item.assignment_id)
        ],
    }
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def normalize_issue_signature(
    *,
    issues: list[Any],
    plan_fingerprint: str,
) -> str:
    """Normalized issue signature for unchanged-plan loop checks (test helper)."""

    parts: list[str] = []
    for issue in sorted(
        issues,
        key=lambda item: (
            str(getattr(item, "error_code", "")),
            str(getattr(item, "shot_id", "") or ""),
            str(getattr(item, "assignment_id", "") or ""),
            str(getattr(item, "technical_details", "")),
        ),
    ):
        details = str(getattr(issue, "technical_details", "") or "")
        asset_token = ""
        for asset_id in ASSET_IDS:
            if asset_id in details:
                asset_token = asset_id
                break
        rule = "E3" if "E3" in details else ("E4" if "E4" in details else "other")
        parts.append(
            "|".join(
                [
                    str(getattr(issue, "error_code", "")),
                    asset_token,
                    str(getattr(issue, "assignment_id", "") or ""),
                    str(getattr(issue, "shot_id", "") or ""),
                    rule,
                    plan_fingerprint,
                ]
            )
        )
    raw = json.dumps(parts, sort_keys=False, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def ensure_six_visual_intents(project) -> list[dict[str, Any]]:
    """Pad locked script JSON to six visual intents without media I/O."""

    conn = reg_db.get_registry_connection(project.project_root_path)
    try:
        row = conn.execute(
            """
            SELECT script_id, relative_json_path
            FROM script_drafts
            WHERE project_id = ?
            ORDER BY script_version DESC
            LIMIT 1
            """,
            (project.id,),
        ).fetchone()
        assert row is not None
        relative = str(row["relative_json_path"])
        path = get_discovery_v2_root(project.project_root_path) / relative
        payload = json.loads(path.read_text(encoding="utf-8"))
        intents = list(payload.get("visual_intents") or [])
        assert intents, "script bundle must already contain visual intents"
        beats = list(payload.get("visual_beats") or [])
        assert beats, "script bundle must already contain visual beats"
        while len(intents) < 6:
            template = dict(intents[len(intents) % len(intents)])
            beat = beats[len(intents) % len(beats)]
            template["visual_intent_id"] = f"intent-rework-v1-{len(intents) + 1}"
            template["visual_beat_id"] = beat["visual_beat_id"]
            template["desired_motif"] = f"Rework V1 motif {len(intents) + 1}"
            template["priority"] = len(intents) + 1
            intents.append(template)
        payload["visual_intents"] = intents[:6]
        path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        # Keep SQL table aligned when present (bundle reader uses JSON).
        for intent in payload["visual_intents"]:
            exists = conn.execute(
                "SELECT 1 FROM visual_intents WHERE visual_intent_id = ?",
                (intent["visual_intent_id"],),
            ).fetchone()
            if exists is not None:
                continue
            conn.execute(
                """
                INSERT INTO visual_intents (
                    visual_intent_id, visual_beat_id, desired_motif, action, setting,
                    geographic_requirements, authenticity_requirements_json,
                    allowed_media_kinds_json, priority
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    intent["visual_intent_id"],
                    intent["visual_beat_id"],
                    intent["desired_motif"],
                    intent.get("action", "observe"),
                    intent.get("setting", "local"),
                    intent.get("geographic_requirements"),
                    json.dumps(intent.get("authenticity_requirements") or []),
                    json.dumps(intent.get("allowed_media_kinds") or ["video"]),
                    int(intent.get("priority") or 1),
                ),
            )
        conn.commit()
        return list(payload["visual_intents"])
    finally:
        conn.close()


def seed_video_candidates(
    project,
    *,
    labels: tuple[str, ...] = ASSET_LABELS,
    tech_ranges: list[tuple[float, float]] | None = None,
    id_prefix: str = "rework-v1",
) -> list[dict[str, str]]:
    """Insert completed video candidates (metadata only; no media I/O)."""

    ranges = tech_ranges or [(TECH_SHOT_START_SECONDS, TECH_SHOT_END_SECONDS)]
    conn = reg_db.get_registry_connection(project.project_root_path)
    try:
        existing = copy_repo.list_working_media(conn, project_id=project.id)
        assert existing, "fixture requires an existing intake plan/run to reuse FKs"
        plan_id = existing[0].plan_id
        intake_run_id = existing[0].intake_run_id
        now = _now()
        seeded: list[dict[str, str]] = []
        for index, label in enumerate(labels):
            asset_id = f"asset-{id_prefix}-{label}"
            wm_id = f"wm-{id_prefix}-{label}"
            obs_id = f"obs-{id_prefix}-{label}"
            sha = hashlib.sha256(f"{id_prefix}-{asset_id}".encode("utf-8")).hexdigest()
            conn.execute(
                """
                INSERT OR IGNORE INTO assets (
                    asset_id, project_id, source_relative_path, source_group, file_name,
                    extension, media_kind, size_bytes, mtime_ns, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    asset_id,
                    project.id,
                    f"Media/{id_prefix}-{label}.mp4",
                    "Media",
                    f"{id_prefix}-{label}.mp4",
                    ".mp4",
                    "video",
                    1,
                    1,
                    now.isoformat(),
                    now.isoformat(),
                ),
            )
            wm = WorkingMediaRecord(
                working_media_id=wm_id,
                project_id=project.id,
                asset_id=asset_id,
                plan_id=plan_id,
                intake_run_id=intake_run_id,
                source_relative_path=f"Media/{id_prefix}-{label}.mp4",
                working_relative_path=(
                    f"media/working/{asset_id}/{sha}/{COPY_WORKING_PROFILE_VERSION}/"
                    f"{asset_id}.mp4"
                ),
                source_sha256=sha,
                output_sha256=sha,
                media_kind="video",
                extension=".mp4",
                action=COPY_WORKING_ACTION,
                processing_profile_version=COPY_WORKING_PROFILE_VERSION,
                status=WorkingMediaStatus.COMPLETED,
                created_at=now,
                updated_at=now,
            )
            existing_wm = conn.execute(
                "SELECT 1 FROM working_media WHERE working_media_id = ?",
                (wm_id,),
            ).fetchone()
            if existing_wm is None:
                copy_repo.insert_working_media(conn, wm)
            identity = analysis_repo.find_or_create_analysis_identity(
                conn,
                project_id=project.id,
                asset_id=asset_id,
                working_media_id=wm_id,
                output_sha256=sha,
                processing_profile_version=COPY_WORKING_PROFILE_VERSION,
                analysis_profile_version=ANALYSIS_CONTRACT_PROFILE_VERSION,
            )
            tech_ids: list[str] = []
            for tech_ordinal, (tech_start, tech_end) in enumerate(ranges):
                tech_id = f"tech-{id_prefix}-{label}-{tech_ordinal}"
                tech_ids.append(tech_id)
                if (
                    conn.execute(
                        "SELECT 1 FROM technical_shots WHERE shot_id = ?", (tech_id,)
                    ).fetchone()
                    is None
                ):
                    analysis_repo.insert_technical_shot(
                        conn,
                        TechnicalShotRecord(
                            shot_id=tech_id,
                            analysis_identity_id=identity.analysis_identity_id,
                            project_id=project.id,
                            asset_id=asset_id,
                            working_media_id=wm_id,
                            ordinal=tech_ordinal,
                            start_seconds=tech_start,
                            end_seconds=tech_end,
                            duration_seconds=tech_end - tech_start,
                            detection_profile_version=SHOT_DETECT_PROFILE_VERSION,
                            created_at=now,
                        ),
                    )
            seeded.append(
                {
                    "label": label,
                    "asset_id": asset_id,
                    "working_media_id": wm_id,
                    "observation_id": obs_id,
                    "analysis_identity_id": identity.analysis_identity_id,
                    "technical_shot_id": tech_ids[0],
                    "technical_shot_ids": ",".join(tech_ids),
                    "frame_set_fingerprint": f"fp-{id_prefix}-{label}",
                    "observation_sha256": hashlib.sha256(
                        f"obs-{asset_id}".encode("utf-8")
                    ).hexdigest(),
                }
            )
        conn.commit()
        return seeded
    finally:
        conn.close()


def seed_six_video_candidates(project) -> list[dict[str, str]]:
    """Insert six completed video candidates (metadata only; no media I/O)."""

    return seed_video_candidates(project, labels=ASSET_LABELS, id_prefix="rework-v1")


def editorial_ready_views_for_seed(
    project,
    seeded: list[dict[str, str]],
) -> list[EditorialReadyObservationView]:
    """Build accepted editorial-ready views for seeded video candidates (A..F order)."""

    views: list[EditorialReadyObservationView] = []
    for item in seeded:
        review = ObservationReviewRecord(
            review_id=f"review-{item['observation_id']}",
            observation_id=item["observation_id"],
            analysis_identity_id=item["analysis_identity_id"],
            project_id=project.id,
            asset_id=item["asset_id"],
            working_media_id=item["working_media_id"],
            observation_sha256=item["observation_sha256"],
            frame_set_fingerprint=item["frame_set_fingerprint"],
            review_revision=1,
            decision="accepted",
            created_at=_now(),
        )
        views.append(
            EditorialReadyObservationView(
                observation_id=item["observation_id"],
                asset_id=item["asset_id"],
                analysis_identity_id=item["analysis_identity_id"],
                working_media_id=item["working_media_id"],
                summary=f"Synthetic accepted observation for {item['label']}",
                evidence_frame_ids=[f"frame-{item['label']}"],
                geographic_confidence=0.0,
                synthetic_confidence=0.0,
                uncertainty_notes=[],
                review=review,
                observation_sha256=item["observation_sha256"],
                frame_set_fingerprint=item["frame_set_fingerprint"],
            )
        )
    return views


def install_six_candidate_observation_hook(monkeypatch, project, seeded) -> None:
    """Monkeypatch only observation listing; Feasibility/E3/E4 remain real."""

    views = editorial_ready_views_for_seed(project, seeded)

    def _list(_project=None):
        return list(views)

    monkeypatch.setattr(
        "otio_app.discovery_v2.application.visual_edit_plan_service.list_editorial_ready_observations",
        _list,
    )
    monkeypatch.setattr(
        "otio_app.discovery_v2.application.observation_review_service.list_editorial_ready_observations",
        _list,
    )


def install_no_media_io_guards(monkeypatch) -> None:
    """Fail the test if unexpected media I/O or subprocess work occurs."""

    import subprocess

    import otio_app.discovery_v2.adapters.ffmpeg_runner as ffmpeg_runner
    import otio_app.discovery_v2.adapters.image_probe as image_probe
    import otio_app.discovery_v2.adapters.media_probe as media_probe
    import otio_app.discovery_v2.adapters.source_hash as source_hash

    def _fail(name: str):
        def _inner(*_args, **_kwargs):
            raise AssertionError(f"Unerlaubter Medien-/Prozess-Aufruf: {name}")

        return _inner

    monkeypatch.setattr(subprocess, "run", _fail("subprocess.run"))
    monkeypatch.setattr(subprocess, "Popen", _fail("subprocess.Popen"))
    if hasattr(ffmpeg_runner, "run_ffmpeg"):
        monkeypatch.setattr(ffmpeg_runner, "run_ffmpeg", _fail("run_ffmpeg"))
    if hasattr(media_probe, "probe_media_file"):
        monkeypatch.setattr(media_probe, "probe_media_file", _fail("probe_media_file"))
    if hasattr(image_probe, "probe_image_file"):
        monkeypatch.setattr(image_probe, "probe_image_file", _fail("probe_image_file"))
    if hasattr(source_hash, "compute_sha256_hex"):
        monkeypatch.setattr(source_hash, "compute_sha256_hex", _fail("compute_sha256_hex"))


def overlap_ratio(left, right) -> float:
    left_start = float(left.technical_source_in_seconds)
    left_end = float(left.technical_source_out_seconds)
    right_start = float(right.technical_source_in_seconds)
    right_end = float(right.technical_source_out_seconds)
    overlap = max(0.0, min(left_end, right_end) - max(left_start, right_start))
    shortest = min(left_end - left_start, right_end - right_start)
    return 0.0 if shortest <= 0 else overlap / shortest


__all__ = [
    "ASSET_A_ID",
    "ASSET_IDS",
    "ASSET_LABELS",
    "ASSET_REUSE_MAX",
    "SOURCE_RANGE_OVERLAP_RATIO_MAX",
    "TECH_SHOT_END_SECONDS",
    "TECH_SHOT_START_SECONDS",
    "editorial_ready_views_for_seed",
    "ensure_six_visual_intents",
    "install_no_media_io_guards",
    "install_six_candidate_observation_hook",
    "normalize_issue_signature",
    "overlap_ratio",
    "plan_content_fingerprint",
    "seed_six_video_candidates",
    "seed_video_candidates",
]
