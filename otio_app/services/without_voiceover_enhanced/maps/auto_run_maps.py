"""Auto-Lauf: Kartenplan, Geocode, Bestätigen, Rendern — vor Python Timing."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from otio_app.models import Project
from otio_app.services.voiceover_generation.dramaturgy_service import (
    load_confirmed_dramaturgy,
)
from otio_app.services.without_voiceover_enhanced.maps.geocode_service import (
    lookup_missing_coordinates,
)
from otio_app.services.without_voiceover_enhanced.maps.models import (
    RENDER_STATUS_BLOCKED,
    RENDER_STATUS_DONE,
    RENDER_STATUS_FAILED,
)
from otio_app.services.without_voiceover_enhanced.maps.plan_service import (
    MapPlanError,
    build_map_plan,
    confirm_all_valid_map_coordinates,
    dramaturgy_fingerprint,
    load_map_plan,
    load_map_settings,
    save_map_plan,
)
from otio_app.services.without_voiceover_enhanced.maps.render_service import (
    MapRenderCancelled,
    MapRenderError,
    MapRenderer,
    output_file_nonempty,
    selectable_maps,
)
from otio_app.services.without_voiceover_enhanced.paths import map_output_dir

ProgressFn = Callable[[str], None]
CancelFn = Callable[[], bool]


def _item_output_path(project: Project, item) -> str:
    if item.output_path:
        return item.output_path
    return str(map_output_dir(project) / item.output_filename)


def maps_complete(project: Project) -> bool:
    """True wenn der Plan zur Dramaturgie passt und jede renderbare Karte fertig ist."""
    confirmed = load_confirmed_dramaturgy(project)
    if confirmed is None:
        return False
    plan = load_map_plan(project)
    if plan is None or not plan.maps:
        return False
    if plan.dramaturgy_fingerprint != dramaturgy_fingerprint(confirmed):
        return False
    for item in plan.maps:
        if item.render_status == RENDER_STATUS_BLOCKED:
            continue
        path = _item_output_path(project, item)
        if item.render_status == RENDER_STATUS_DONE and output_file_nonempty(path):
            continue
        return False
    return True


def run_maps_for_auto_run(
    project: Project,
    *,
    should_cancel: CancelFn | None = None,
    on_message: ProgressFn | None = None,
    geocode_fn=None,
    renderer: MapRenderer | None = None,
) -> dict[str, Any]:
    """Kartenplan erzeugen, Koordinaten prüfen/bestätigen, alle Karten rendern."""

    def emit(message: str) -> None:
        if on_message is not None:
            on_message(message)

    def cancelled() -> bool:
        return bool(should_cancel and should_cancel())

    if cancelled():
        raise MapRenderCancelled("Auto-Lauf gestoppt.")

    settings = load_map_settings(project)
    emit("Kartenplan aus Dramaturgie erzeugen…")
    previous = load_map_plan(project)
    plan = build_map_plan(project, settings=settings, previous=previous)
    save_map_plan(project, plan)

    if cancelled():
        raise MapRenderCancelled("Auto-Lauf gestoppt.")

    emit("Koordinaten prüfen…")
    _coords, plan, geocode_errors = lookup_missing_coordinates(
        project,
        settings=settings,
        plan=plan,
        geocode_fn=geocode_fn,
    )
    save_map_plan(project, plan)

    emit("Koordinaten bestätigen…")
    _coords, plan = confirm_all_valid_map_coordinates(
        project,
        settings=settings,
        previous=plan,
    )

    blocked = [item for item in plan.maps if item.render_status == RENDER_STATUS_BLOCKED]
    targets = selectable_maps(plan.maps, mode="missing")
    rendered: list[str] = []
    failed: list[tuple[str, str]] = []
    reused = 0

    if not targets:
        emit(
            "Keine Karten zu rendern"
            + (f" — {len(blocked)} ohne Koordinaten." if blocked else ".")
        )
        return {
            "plan": plan,
            "rendered": rendered,
            "reused": reused,
            "blocked": [item.chapter_id for item in blocked],
            "failed": failed,
            "geocode_errors": list(geocode_errors),
        }

    active = renderer or MapRenderer()
    readiness = active.readiness()
    if not readiness.get("ready"):
        missing = [
            name for name, ok in (readiness.get("checks") or {}).items() if not ok
        ]
        raise MapPlanError(
            "Kartenrenderer ist nicht bereit ("
            + ", ".join(missing)
            + "). Im Ordner des Vendored Remotion-Renderers einmal `npm ci` ausführen."
        )

    total = len(targets)
    for index, item in enumerate(targets, start=1):
        if cancelled():
            raise MapRenderCancelled("Auto-Lauf gestoppt.")
        emit(f"Karte {index}/{total}: {item.original_chapter_label}")
        try:
            result = active.render_item(
                project,
                item,
                overwrite=False,
                should_cancel=cancelled,
            )
        except MapRenderCancelled:
            raise
        except MapRenderError as exc:
            failed.append((item.chapter_id, str(exc)))
            item.render_status = RENDER_STATUS_FAILED
            item.error_detail = str(exc)
            continue
        except Exception as exc:  # noqa: BLE001
            failed.append((item.chapter_id, str(exc) or type(exc).__name__))
            item.render_status = RENDER_STATUS_FAILED
            item.error_detail = str(exc) or type(exc).__name__
            continue
        export_path = str(result.get("export_path") or "")
        item.render_status = RENDER_STATUS_DONE
        item.output_path = export_path
        item.media_hash = str(result.get("content_hash") or "")
        item.progress = 1.0
        item.error_detail = ""
        item.blocked_reason = ""
        rendered.append(item.chapter_id)
        if result.get("reused"):
            reused += 1
        save_map_plan(project, plan)

    save_map_plan(project, plan)
    return {
        "plan": plan,
        "rendered": rendered,
        "reused": reused,
        "blocked": [item.chapter_id for item in blocked],
        "failed": failed,
        "geocode_errors": list(geocode_errors),
    }
