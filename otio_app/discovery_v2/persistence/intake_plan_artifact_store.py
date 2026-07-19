"""Atomische JSON-Speicherung der Discovery-V2 Media-Intake-Pläne."""

from __future__ import annotations

from pathlib import Path

from otio_app.discovery_v2.domain.media_intake import (
    IntakePlan,
    IntakePlanLatestPointer,
)
from otio_app.discovery_v2.persistence.inventory_artifact_store import (
    InventoryArtifactError,
    _atomic_write_text,
)
from otio_app.discovery_v2.paths import (
    assert_path_is_under_discovery_v2,
    get_discovery_v2_root,
)


def intake_dir(project_root: Path) -> Path:
    return get_discovery_v2_root(project_root) / "intake"


def intake_plans_dir(project_root: Path) -> Path:
    return intake_dir(project_root) / "plans"


def intake_plan_path(project_root: Path, plan_id: str) -> Path:
    return intake_plans_dir(project_root) / f"{plan_id}.json"


def latest_intake_plan_pointer_path(project_root: Path) -> Path:
    return intake_dir(project_root) / "latest_plan.json"


def ensure_intake_dirs(project_root: Path) -> None:
    try:
        intake_dir(project_root).mkdir(parents=True, exist_ok=True)
        intake_plans_dir(project_root).mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise InventoryArtifactError(
            f"Intake-Verzeichnis nicht beschreibbar: {exc}"
        ) from exc
    assert_path_is_under_discovery_v2(intake_dir(project_root), project_root)
    assert_path_is_under_discovery_v2(intake_plans_dir(project_root), project_root)


def save_intake_plan_artifact(project_root: Path, plan: IntakePlan) -> Path:
    """Schreibt einen neuen Plan (historisch unveränderlich) und den Pointer."""
    ensure_intake_dirs(project_root)
    target = intake_plan_path(project_root, plan.plan_id)
    assert_path_is_under_discovery_v2(target, project_root)
    if target.exists():
        raise InventoryArtifactError(
            f"Intake-Plan existiert bereits und darf nicht überschrieben werden: "
            f"{target}"
        )
    try:
        _atomic_write_text(target, plan.model_dump_json(indent=2))
    except OSError as exc:
        raise InventoryArtifactError(
            f"Intake-Plan konnte nicht atomar geschrieben werden: {exc}"
        ) from exc

    pointer = IntakePlanLatestPointer(
        plan_id=plan.plan_id,
        import_id=plan.import_id,
        selection_id=plan.selection_id,
        scan_id=plan.scan_id,
        validation_run_id=plan.validation_run_id,
        created_at=plan.created_at,
        status=plan.status,
        plan_relative_path=f"intake/plans/{plan.plan_id}.json",
    )
    latest = latest_intake_plan_pointer_path(project_root)
    assert_path_is_under_discovery_v2(latest, project_root)
    try:
        _atomic_write_text(latest, pointer.model_dump_json(indent=2))
    except OSError as exc:
        raise InventoryArtifactError(
            f"latest_plan.json konnte nicht atomar geschrieben werden: {exc}"
        ) from exc
    return target


def write_plan_json_only(project_root: Path, plan: IntakePlan) -> Path:
    """Schreibt nur das Plan-JSON (ohne Pointer) — für transaktionale Orchestrierung."""
    ensure_intake_dirs(project_root)
    target = intake_plan_path(project_root, plan.plan_id)
    assert_path_is_under_discovery_v2(target, project_root)
    if target.exists():
        raise InventoryArtifactError(
            f"Intake-Plan existiert bereits und darf nicht überschrieben werden: "
            f"{target}"
        )
    try:
        _atomic_write_text(target, plan.model_dump_json(indent=2))
    except OSError as exc:
        raise InventoryArtifactError(
            f"Intake-Plan konnte nicht atomar geschrieben werden: {exc}"
        ) from exc
    return target


def write_latest_plan_pointer(project_root: Path, plan: IntakePlan) -> Path:
    ensure_intake_dirs(project_root)
    pointer = IntakePlanLatestPointer(
        plan_id=plan.plan_id,
        import_id=plan.import_id,
        selection_id=plan.selection_id,
        scan_id=plan.scan_id,
        validation_run_id=plan.validation_run_id,
        created_at=plan.created_at,
        status=plan.status,
        plan_relative_path=f"intake/plans/{plan.plan_id}.json",
    )
    latest = latest_intake_plan_pointer_path(project_root)
    assert_path_is_under_discovery_v2(latest, project_root)
    try:
        _atomic_write_text(latest, pointer.model_dump_json(indent=2))
    except OSError as exc:
        raise InventoryArtifactError(
            f"latest_plan.json konnte nicht atomar geschrieben werden: {exc}"
        ) from exc
    return latest


def load_intake_plan(path: Path) -> IntakePlan:
    try:
        return IntakePlan.model_validate_json(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise InventoryArtifactError(
            f"Intake-Plan konnte nicht gelesen werden: {exc}"
        ) from exc


def load_latest_intake_plan(
    project_root: Path,
) -> tuple[IntakePlan | None, str | None]:
    """Lädt den zuletzt erfolgreichen Plan über den Pointer.

    Beschädigter Pointer → Warnung, kein Absturz.
    """
    latest = latest_intake_plan_pointer_path(project_root)
    if not latest.exists():
        return None, None
    try:
        pointer = IntakePlanLatestPointer.model_validate_json(
            latest.read_text(encoding="utf-8")
        )
    except (OSError, ValueError) as exc:
        return None, f"Beschädigter Intake-Plan-Pointer: {exc}"

    plan_file = get_discovery_v2_root(project_root) / pointer.plan_relative_path
    if not plan_file.exists():
        # Fallback über plan_id
        plan_file = intake_plan_path(project_root, pointer.plan_id)
    if not plan_file.exists():
        return None, (
            f"Intake-Plan-Datei fehlt für plan_id={pointer.plan_id}."
        )
    try:
        return load_intake_plan(plan_file), None
    except InventoryArtifactError as exc:
        return None, str(exc)
