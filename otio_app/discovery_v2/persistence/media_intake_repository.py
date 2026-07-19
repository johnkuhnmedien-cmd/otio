"""SQLite-Persistenz für unveränderliche Discovery-V2 Media-Intake-Pläne."""

from __future__ import annotations

import sqlite3
from datetime import datetime
from pathlib import Path

from otio_app.discovery_v2.domain.media_intake import (
    IntakeAction,
    IntakePlan,
    IntakePlanItem,
    IntakePlanItemStatus,
    IntakePlanStatus,
)
from otio_app.discovery_v2.persistence.asset_registry_database import (
    get_registry_connection,
)


def open_registry(project_root: Path) -> sqlite3.Connection:
    return get_registry_connection(project_root)


def insert_intake_plan(conn: sqlite3.Connection, plan: IntakePlan) -> None:
    conn.execute(
        """
        INSERT INTO intake_plans (
            plan_id, project_id, import_id, selection_id, scan_id,
            validation_run_id, planner_version, status, created_at,
            total_assets, copy_count, remux_count, transcode_count,
            blocked_count, duplicate_warning_count
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            plan.plan_id,
            plan.project_id,
            plan.import_id,
            plan.selection_id,
            plan.scan_id,
            plan.validation_run_id,
            plan.planner_version,
            plan.status.value,
            plan.created_at.isoformat(),
            plan.total_assets,
            plan.copy_count,
            plan.remux_count,
            plan.transcode_count,
            plan.blocked_count,
            plan.duplicate_warning_count,
        ),
    )
    for item in plan.items:
        conn.execute(
            """
            INSERT INTO intake_plan_assets (
                plan_id, asset_id, validation_id, source_sha256,
                source_relative_path, source_group, media_kind,
                planned_action, status, reason_code, reason_detail,
                proposed_target_extension, processing_profile_version,
                duplicate_group_id
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                plan.plan_id,
                item.asset_id,
                item.validation_id,
                item.source_sha256,
                item.source_relative_path,
                item.source_group,
                item.media_kind,
                item.planned_action.value,
                item.status.value,
                item.reason_code,
                item.reason_detail,
                item.proposed_target_extension,
                item.processing_profile_version,
                item.duplicate_group_id,
            ),
        )


def get_latest_intake_plan_record(
    conn: sqlite3.Connection, *, project_id: str
) -> IntakePlan | None:
    row = conn.execute(
        """
        SELECT * FROM intake_plans
        WHERE project_id = ?
        ORDER BY created_at DESC
        LIMIT 1
        """,
        (project_id,),
    ).fetchone()
    if row is None:
        return None
    items = list_plan_items(conn, plan_id=str(row["plan_id"]))
    return _row_to_plan(row, items)


def get_intake_plan(
    conn: sqlite3.Connection, *, plan_id: str
) -> IntakePlan | None:
    row = conn.execute(
        "SELECT * FROM intake_plans WHERE plan_id = ?",
        (plan_id,),
    ).fetchone()
    if row is None:
        return None
    items = list_plan_items(conn, plan_id=plan_id)
    return _row_to_plan(row, items)


def list_plan_items(
    conn: sqlite3.Connection, *, plan_id: str
) -> list[IntakePlanItem]:
    rows = conn.execute(
        """
        SELECT
            p.*,
            a.extension AS asset_extension,
            v.container_format,
            v.video_codec,
            v.audio_codec,
            v.width,
            v.height,
            v.frame_rate_numerator,
            v.frame_rate_denominator,
            v.embedded_timecode,
            v.pixel_format,
            v.bit_depth
        FROM intake_plan_assets p
        LEFT JOIN assets a ON a.asset_id = p.asset_id
        LEFT JOIN asset_validations v ON v.validation_id = p.validation_id
        WHERE p.plan_id = ?
        ORDER BY p.source_relative_path
        """,
        (plan_id,),
    ).fetchall()
    return [_row_to_item(row) for row in rows]


def count_intake_plans(conn: sqlite3.Connection, *, project_id: str) -> int:
    row = conn.execute(
        "SELECT COUNT(*) AS c FROM intake_plans WHERE project_id = ?",
        (project_id,),
    ).fetchone()
    return int(row["c"]) if row else 0


def _parse_dt(value: str | None) -> datetime:
    if not value:
        raise ValueError("created_at fehlt")
    return datetime.fromisoformat(value)


def _row_to_plan(row: sqlite3.Row, items: list[IntakePlanItem]) -> IntakePlan:
    return IntakePlan(
        plan_id=str(row["plan_id"]),
        project_id=str(row["project_id"]),
        import_id=str(row["import_id"]),
        selection_id=str(row["selection_id"]),
        scan_id=str(row["scan_id"]),
        validation_run_id=str(row["validation_run_id"]),
        planner_version=str(row["planner_version"]),
        status=IntakePlanStatus(str(row["status"])),
        created_at=_parse_dt(str(row["created_at"])),
        total_assets=int(row["total_assets"]),
        copy_count=int(row["copy_count"]),
        remux_count=int(row["remux_count"]),
        transcode_count=int(row["transcode_count"]),
        blocked_count=int(row["blocked_count"]),
        duplicate_warning_count=int(row["duplicate_warning_count"]),
        items=items,
    )


def _row_to_item(row: sqlite3.Row) -> IntakePlanItem:
    keys = set(row.keys())
    extension = ""
    if "asset_extension" in keys and row["asset_extension"]:
        extension = str(row["asset_extension"])
        if extension and not extension.startswith("."):
            extension = f".{extension.lower()}"
        else:
            extension = extension.lower()

    def _opt(name: str):
        return row[name] if name in keys else None

    return IntakePlanItem(
        asset_id=str(row["asset_id"]),
        validation_id=str(row["validation_id"]),
        source_relative_path=str(row["source_relative_path"]),
        source_group=str(row["source_group"]),
        media_kind=str(row["media_kind"]),
        source_sha256=row["source_sha256"],
        extension=extension,
        container_format=_opt("container_format"),
        video_codec=_opt("video_codec"),
        audio_codec=_opt("audio_codec"),
        width=_opt("width"),
        height=_opt("height"),
        frame_rate_numerator=_opt("frame_rate_numerator"),
        frame_rate_denominator=_opt("frame_rate_denominator"),
        embedded_timecode=_opt("embedded_timecode"),
        pixel_format=_opt("pixel_format"),
        bit_depth=_opt("bit_depth"),
        duplicate_group_id=row["duplicate_group_id"],
        planned_action=IntakeAction(str(row["planned_action"])),
        status=IntakePlanItemStatus(str(row["status"])),
        reason_code=str(row["reason_code"]),
        reason_detail=str(row["reason_detail"]),
        proposed_target_extension=row["proposed_target_extension"],
        processing_profile_version=str(row["processing_profile_version"]),
    )
