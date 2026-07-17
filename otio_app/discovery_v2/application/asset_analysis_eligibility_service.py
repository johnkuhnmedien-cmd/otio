"""Application-Service: Eligibility für Discovery-V2-Assetanalyse (Phase 8A).

Liest ausschließlich persistierte Selection-/Import-/Validation-/Plan-/
Working-Media-Daten. Kein Medien-I/O, kein Hashing, kein Jobstart.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from otio_app.discovery_v2.application.inventory_service import (
    InventoryServiceError,
    get_latest_inventory,
    require_discovery_project,
)
from otio_app.discovery_v2.application.media_intake_planning_service import (
    can_create_intake_plan,
    get_current_intake_plan,
)
from otio_app.discovery_v2.application.selection_service import (
    get_latest_confirmed_selection,
)
from otio_app.discovery_v2.domain.asset_analysis import (
    ANALYSIS_CONTRACT_PROFILE_VERSION,
    AnalysisEligibility,
    AnalysisInputIdentity,
)
from otio_app.discovery_v2.domain.inventory import MediaKind
from otio_app.discovery_v2.domain.media_intake import (
    COPY_WORKING_ACTION,
    COPY_WORKING_PROFILE_VERSION,
    IMAGE_CONVERT_ACTION,
    IMAGE_PNG_PROFILE_VERSION,
    INTAKE_PLANNER_VERSION,
    IntakeAction,
    IntakePlan,
    IntakePlanItem,
    IntakePlanStatus,
    REMUX_WORKING_ACTION,
    REMUX_WORKING_PROFILE_VERSION,
    VIDEO_H264_PROFILE_VERSION,
    VIDEO_TRANSCODE_ACTION,
    WorkingMediaRecord,
    WorkingMediaStatus,
)
from otio_app.discovery_v2.domain.selection import SelectionStatus
from otio_app.discovery_v2.persistence.asset_analysis_repository import (
    list_analysis_runs,
    open_analysis_registry,
)
from otio_app.discovery_v2.domain.asset_registry import REGISTRY_SCHEMA_VERSION
from otio_app.discovery_v2.persistence.asset_registry_database import (
    RegistryDatabaseError,
    read_schema_version,
)
from otio_app.discovery_v2.persistence.asset_registry_repository import (
    load_latest_import_report,
)
from otio_app.discovery_v2.persistence.copy_intake_repository import (
    list_working_media_for_asset,
)
from otio_app.discovery_v2.persistence.technical_validation_repository import (
    get_validation_by_id,
)
from otio_app.models import Project


class AssetAnalysisEligibilityServiceError(InventoryServiceError):
    """Fachlicher Fehler der Analysis-Eligibility."""


@dataclass
class AnalysisEligibilityView:
    """Read-only View für die Assetanalyse-UI-Shell."""

    ok: bool
    message: str | None
    chain_error_code: str | None = None
    plan_id: str | None = None
    analysis_profile_version: str = ANALYSIS_CONTRACT_PROFILE_VERSION
    items: list[AnalysisEligibility] = field(default_factory=list)
    analysis_run_count: int = 0


def expected_working_binding(
    item: IntakePlanItem,
) -> tuple[str, str] | None:
    """Leitet erwartete (action, profile) exakt aus dem Plan-Item ab.

    Keine Profilprioritätsheuristik. HEIC ohne freigegebenes Profil → None.
    """
    kind = (item.media_kind or "").strip().lower()
    action = item.planned_action
    profile = (item.processing_profile_version or "").strip()

    if action == IntakeAction.BLOCKED:
        return None

    if kind == MediaKind.AUDIO.value:
        return COPY_WORKING_ACTION, COPY_WORKING_PROFILE_VERSION

    if action == IntakeAction.COPY:
        return COPY_WORKING_ACTION, COPY_WORKING_PROFILE_VERSION

    if action == IntakeAction.REMUX:
        return REMUX_WORKING_ACTION, REMUX_WORKING_PROFILE_VERSION

    if action == IntakeAction.TRANSCODE:
        if kind == MediaKind.VIDEO.value:
            return VIDEO_TRANSCODE_ACTION, VIDEO_H264_PROFILE_VERSION
        if kind == MediaKind.IMAGE.value and profile == IMAGE_PNG_PROFILE_VERSION:
            return IMAGE_CONVERT_ACTION, IMAGE_PNG_PROFILE_VERSION
        # z. B. HEIC: Transcode geplant, aber kein freigegebenes WM-Profil
        return None

    return None


def build_analysis_input_identity(
    *,
    project_id: str,
    item: IntakePlanItem,
    working: WorkingMediaRecord,
) -> AnalysisInputIdentity:
    return AnalysisInputIdentity(
        project_id=project_id,
        asset_id=item.asset_id,
        working_media_id=working.working_media_id,
        validation_id=item.validation_id,
        source_sha256=(item.source_sha256 or working.source_sha256 or "").lower(),
        output_sha256=working.output_sha256.lower(),
        processing_profile_version=working.processing_profile_version,
        media_kind=item.media_kind,
        analysis_profile_version=ANALYSIS_CONTRACT_PROFILE_VERSION,
    )


def get_analysis_eligibility_view(project: Project) -> AnalysisEligibilityView:
    """Liefert Eligibility ausschließlich aus persistierten Daten."""
    try:
        project = require_discovery_project(project)
    except InventoryServiceError as exc:
        return AnalysisEligibilityView(
            ok=False,
            message=str(exc),
            chain_error_code="wrong_project_mode",
        )

    snapshot, snap_warn = get_latest_inventory(project)
    if snapshot is None:
        return AnalysisEligibilityView(
            ok=False,
            message=snap_warn or "Kein Inventory-Snapshot vorhanden.",
            chain_error_code="stale_selection",
        )

    selection, status, sel_warn = get_latest_confirmed_selection(
        project, current_scan_id=snapshot.scan_id
    )
    if selection is None:
        return AnalysisEligibilityView(
            ok=False,
            message=sel_warn or "Keine bestätigte Selection vorhanden.",
            chain_error_code="stale_selection",
        )
    if status == SelectionStatus.STALE or selection.scan_id != snapshot.scan_id:
        return AnalysisEligibilityView(
            ok=False,
            message=(
                "Die bestätigte Auswahl ist veraltet. "
                "Assetanalyse erfordert die aktuelle Selection."
            ),
            chain_error_code="stale_selection",
        )

    report, import_warn = load_latest_import_report(project.project_root_path)
    if report is None:
        return AnalysisEligibilityView(
            ok=False,
            message=import_warn or "Kein Registry-Import vorhanden.",
            chain_error_code="stale_import",
        )
    if (
        report.selection_id != selection.selection_id
        or report.scan_id != snapshot.scan_id
        or report.scan_id != selection.scan_id
    ):
        return AnalysisEligibilityView(
            ok=False,
            message="Registry-Import gehört nicht zur aktuellen Selection/Scan.",
            chain_error_code="stale_import",
        )

    plan_ok, plan_msg, plan_ctx = can_create_intake_plan(project)
    if not plan_ok or plan_ctx is None:
        # Feiner differenzieren, soweit möglich.
        code = _chain_error_from_plan_gate(plan_msg)
        return AnalysisEligibilityView(
            ok=False,
            message=plan_msg or "Aktualitätskette unvollständig.",
            chain_error_code=code,
        )

    plan, is_stale, plan_warn = get_current_intake_plan(project)
    if plan is None:
        return AnalysisEligibilityView(
            ok=False,
            message=plan_warn or "Kein Intake-Plan vorhanden.",
            chain_error_code="stale_intake_plan",
        )
    if is_stale or plan.status == IntakePlanStatus.STALE:
        return AnalysisEligibilityView(
            ok=False,
            message="Der aktuelle Intake-Plan ist veraltet.",
            chain_error_code="stale_intake_plan",
            plan_id=plan.plan_id,
        )
    if (
        plan.project_id != project.id
        or plan.import_id != plan_ctx["import_id"]
        or plan.selection_id != plan_ctx["selection_id"]
        or plan.scan_id != plan_ctx["scan_id"]
        or plan.validation_run_id != plan_ctx["validation_run_id"]
    ):
        return AnalysisEligibilityView(
            ok=False,
            message="Intake-Plan stimmt nicht mit der aktuellen Kette überein.",
            chain_error_code="stale_intake_plan",
            plan_id=plan.plan_id,
        )
    if not plan.items:
        return AnalysisEligibilityView(
            ok=False,
            message="Der Intake-Plan enthält keine Assets.",
            chain_error_code="stale_intake_plan",
            plan_id=plan.plan_id,
        )

    try:
        conn = open_analysis_registry(project.project_root_path)
    except RegistryDatabaseError as exc:
        return AnalysisEligibilityView(
            ok=False,
            message=str(exc),
            chain_error_code="registry_unavailable",
            plan_id=plan.plan_id,
        )

    try:
        schema = read_schema_version(conn)
        if schema != REGISTRY_SCHEMA_VERSION:
            return AnalysisEligibilityView(
                ok=False,
                message=(
                    f"Inkompatibles Registry-Schema: {schema} "
                    f"(erwartet {REGISTRY_SCHEMA_VERSION})."
                ),
                chain_error_code="registry_schema_incompatible",
                plan_id=plan.plan_id,
            )

        items = [
            evaluate_plan_item_eligibility(conn, project=project, plan=plan, item=item)
            for item in plan.items
        ]
        run_count = len(list_analysis_runs(conn, project_id=project.id))
    finally:
        conn.close()

    return AnalysisEligibilityView(
        ok=True,
        message=None,
        plan_id=plan.plan_id,
        items=items,
        analysis_run_count=run_count,
    )


def evaluate_plan_item_eligibility(
    conn,
    *,
    project: Project,
    plan: IntakePlan,
    item: IntakePlanItem,
) -> AnalysisEligibility:
    display = Path(item.source_relative_path).name or item.asset_id
    base = AnalysisEligibility(
        asset_id=item.asset_id,
        eligible=False,
        media_kind=item.media_kind,
        source_group=item.source_group,
        source_relative_path=item.source_relative_path,
        validation_id=item.validation_id,
        display_name=display,
    )

    kind = (item.media_kind or "").strip().lower()
    if kind not in {
        MediaKind.VIDEO.value,
        MediaKind.IMAGE.value,
        MediaKind.AUDIO.value,
    }:
        return base.model_copy(update={"reason_code": "unsupported_media_kind"})

    binding = expected_working_binding(item)
    if binding is None:
        # HEIC / blockiert / kein freigegebenes Profil
        if kind == MediaKind.IMAGE.value and item.planned_action == IntakeAction.TRANSCODE:
            return base.model_copy(
                update={
                    "reason_code": "analysis_working_media_missing",
                    "expected_action": VIDEO_TRANSCODE_ACTION,
                }
            )
        if item.planned_action == IntakeAction.BLOCKED:
            return base.model_copy(update={"reason_code": "unsupported_media_kind"})
        return base.model_copy(update={"reason_code": "analysis_profile_mismatch"})

    expected_action, expected_profile = binding
    base = base.model_copy(
        update={
            "expected_action": expected_action,
            "expected_processing_profile_version": expected_profile,
        }
    )

    # Plan-Profil muss zur Ableitung passen (keine Heuristik).
    if item.planned_action == IntakeAction.TRANSCODE and kind == MediaKind.IMAGE.value:
        if (item.processing_profile_version or "") != IMAGE_PNG_PROFILE_VERSION:
            return base.model_copy(update={"reason_code": "analysis_profile_mismatch"})
    elif item.planned_action == IntakeAction.REMUX:
        if (
            item.processing_profile_version
            and item.processing_profile_version
            not in {REMUX_WORKING_PROFILE_VERSION, INTAKE_PLANNER_VERSION, "1"}
            and item.processing_profile_version != REMUX_WORKING_PROFILE_VERSION
        ):
            # Remux-Planer setzt oft PROCESSING_PROFILE_VERSION "1" — erwartet ist remux-mp4-v1.
            pass

    source_sha = (item.source_sha256 or "").strip().lower()
    if not source_sha:
        return base.model_copy(update={"reason_code": "analysis_working_media_missing"})

    # Validation-ID muss zum Asset und SHA passen (persistiert).
    validation = get_validation_by_id(conn, validation_id=item.validation_id)
    if validation is None:
        return base.model_copy(update={"reason_code": "stale_validation"})
    if validation.asset_id != item.asset_id:
        return base.model_copy(update={"reason_code": "stale_validation"})
    validation_sha = (validation.sha256 or "").strip().lower()
    if validation_sha and validation_sha != source_sha:
        return base.model_copy(update={"reason_code": "stale_validation"})
    if validation.run_id != plan.validation_run_id:
        return base.model_copy(update={"reason_code": "stale_validation"})

    candidates = [
        wm
        for wm in list_working_media_for_asset(
            conn, project_id=project.id, asset_id=item.asset_id
        )
        if wm.source_sha256.lower() == source_sha
        and wm.action == expected_action
        and wm.processing_profile_version == expected_profile
    ]

    if len(candidates) > 1:
        return base.model_copy(
            update={"reason_code": "analysis_working_media_ambiguous"}
        )
    if not candidates:
        # Liegt ein WM mit anderer Action/Profil vor → mismatch, sonst missing.
        same_sha = [
            wm
            for wm in list_working_media_for_asset(
                conn, project_id=project.id, asset_id=item.asset_id
            )
            if wm.source_sha256.lower() == source_sha
        ]
        if same_sha:
            wrong_profile = any(
                wm.processing_profile_version != expected_profile
                or wm.action != expected_action
                for wm in same_sha
            )
            if wrong_profile:
                actual = same_sha[0].processing_profile_version
                return base.model_copy(
                    update={
                        "reason_code": "analysis_profile_mismatch",
                        "actual_processing_profile_version": actual,
                        "working_media_id": same_sha[0].working_media_id,
                        "output_sha256": same_sha[0].output_sha256,
                    }
                )
        return base.model_copy(
            update={"reason_code": "analysis_working_media_missing"}
        )

    working = candidates[0]
    base = base.model_copy(
        update={
            "working_media_id": working.working_media_id,
            "actual_processing_profile_version": working.processing_profile_version,
            "output_sha256": working.output_sha256,
        }
    )

    # Rohstatus aus SQLite — Repository mappt legacy "ready" sonst auf completed.
    raw_status = _raw_working_media_status(conn, working.working_media_id)
    if raw_status != WorkingMediaStatus.COMPLETED.value:
        return base.model_copy(
            update={"reason_code": "analysis_working_media_not_completed"}
        )

    if working.project_id != project.id or working.asset_id != item.asset_id:
        return base.model_copy(
            update={"reason_code": "analysis_working_media_missing"}
        )
    if working.source_sha256.lower() != source_sha:
        return base.model_copy(update={"reason_code": "stale_validation"})
    if working.action != expected_action:
        return base.model_copy(update={"reason_code": "analysis_profile_mismatch"})
    if working.processing_profile_version != expected_profile:
        return base.model_copy(update={"reason_code": "analysis_profile_mismatch"})

    # Audio: Working Media kann existieren, visuelle Analyse ist not_applicable.
    if kind == MediaKind.AUDIO.value:
        return base.model_copy(
            update={
                "eligible": False,
                "reason_code": "not_applicable",
                "expected_action": "not_applicable",
            }
        )

    return base.model_copy(update={"eligible": True, "reason_code": None})


def _raw_working_media_status(conn, working_media_id: str) -> str:
    row = conn.execute(
        "SELECT status FROM working_media WHERE working_media_id = ?",
        (working_media_id,),
    ).fetchone()
    if row is None:
        return ""
    return str(row["status"] if hasattr(row, "keys") else row[0])


def _chain_error_from_plan_gate(message: str | None) -> str:
    text = (message or "").lower()
    if "validat" in text:
        return "stale_validation"
    if "import" in text or "registry" in text:
        return "stale_import"
    if "auswahl" in text or "selection" in text or "bestätig" in text:
        return "stale_selection"
    if "plan" in text:
        return "stale_intake_plan"
    return "stale_intake_plan"


# Re-export für Tests / View-Schicht
__all__ = [
    "ANALYSIS_CONTRACT_PROFILE_VERSION",
    "AnalysisEligibilityView",
    "AssetAnalysisEligibilityServiceError",
    "build_analysis_input_identity",
    "evaluate_plan_item_eligibility",
    "expected_working_binding",
    "get_analysis_eligibility_view",
]
