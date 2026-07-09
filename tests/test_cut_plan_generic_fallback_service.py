"""Phase 11.4: generischer Ordner-Fallback für den Cut-Plan-Auto-Resolver."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from otio_app.analysis_models import AssetFolderAnalysis, AssetMediaAnalysis
from otio_app.defaults import CUT_PLAN_ERROR_SUPPLEMENT_REQUIRED
from otio_app.models import Project, ProjectMode
from otio_app.project_layout import get_folder_inventory_path
from otio_app.services.voiceover_generation.cut_plan_builder import load_cut_plan_draft, save_cut_plan_draft
from otio_app.services.voiceover_generation.cut_plan_generic_fallback_service import (
    apply_generic_fallback_for_cut_plan_request,
    apply_generic_fallback_to_cut_plan_item,
    select_generic_fallback_candidate,
)
from otio_app.services.voiceover_generation.cut_plan_models import CutPlanDocument, CutPlanItem, CutPlanSourceRef
from otio_app.services.voiceover_generation.cut_plan_settings_service import save_cut_plan_settings
from otio_app.services.voiceover_generation.cut_plan_models import CutPlanSettings
from otio_app.services.voiceover_generation.cut_plan_supplement_bridge import (
    build_supplement_requests_from_cut_plan,
    load_cut_plan_supplement_requests,
    save_cut_plan_supplement_requests,
)
from otio_app.services.voiceover_generation.cut_plan_supplement_models import CutPlanSupplementRequest

_MODULE = "otio_app.services.voiceover_generation.cut_plan_generic_fallback_service"
FOLDER_A = "Grand Canyon"


def _make_project(tmp_path: Path) -> Project:
    project_root = tmp_path / "USA"
    (project_root / FOLDER_A).mkdir(parents=True)
    return Project(
        id="generic-fallback-project",
        name="Generic Fallback Test",
        project_root=str(project_root),
        work_dir=str(project_root / "_otio"),
        project_mode=ProjectMode.WITHOUT_VOICEOVER,
        asset_subdir_names=[FOLDER_A],
        selected_asset_subdirs=[FOLDER_A],
    )


def _write_inventory(project: Project, filenames_and_descriptions: list[tuple[str, str]]) -> None:
    entries = []
    for filename, description in filenames_and_descriptions:
        (project.project_root_path / FOLDER_A / filename).write_bytes(b"FAKE_MEDIA_BYTES")
        entries.append(AssetMediaAnalysis(path=f"{FOLDER_A}/{filename}", description=description))
    inv_path = get_folder_inventory_path(project.work_dir_path, FOLDER_A)
    inv_path.parent.mkdir(parents=True, exist_ok=True)
    inv_path.write_text(
        AssetFolderAnalysis(folder=FOLDER_A, assets=entries).model_dump_json(indent=2), encoding="utf-8"
    )


def _minimal_item(**overrides) -> CutPlanItem:
    defaults = dict(
        cut_item_id="cut_001",
        source_refs=[CutPlanSourceRef(source_sentence_id="s1", text="Text")],
        source_scope="folder",
        folder_name=FOLDER_A,
        text="Ein Canyon im Abendlicht.",
        visual_intent="wide canyon shot at sunset",
        timeline_start_sec=1.0,
        timeline_end_sec=6.0,
        duration_sec=5.0,
        audio_start_sec=0.0,
        audio_end_sec=5.0,
        chosen_asset_id="",
        asset_selection_status="SUPPLEMENT_REQUIRED",
        needs_supplement_asset=True,
        supplement_reason="No local asset available.",
        blockers=[CUT_PLAN_ERROR_SUPPLEMENT_REQUIRED],
    )
    defaults.update(overrides)
    return CutPlanItem(**defaults)


def _setup(tmp_path: Path, *, inventory: list[tuple[str, str]] | None = None) -> tuple[Project, CutPlanDocument, CutPlanSupplementRequest]:
    project = _make_project(tmp_path)
    if inventory is not None:
        _write_inventory(project, inventory)
    item = _minimal_item()
    cut_plan = CutPlanDocument(project_id=project.id, timeline_fps=25, items=[item])
    save_cut_plan_settings(project, CutPlanSettings(project_id=project.id))
    save_cut_plan_draft(project, cut_plan)
    document = build_supplement_requests_from_cut_plan(project, cut_plan)
    save_cut_plan_supplement_requests(project, document)
    return project, cut_plan, document.requests[0]


# --- select_generic_fallback_candidate ---


def test_selects_candidate_when_duration_is_sufficient(tmp_path: Path) -> None:
    project, cut_plan, request = _setup(
        tmp_path, inventory=[("establishing.mp4", "Establishing landscape shot of the canyon.")]
    )
    with patch(f"{_MODULE}.probe_duration_seconds", return_value=20.0):
        candidate = select_generic_fallback_candidate(project, cut_plan, request, needed_duration_sec=5.0)
    assert candidate is not None
    assert candidate.asset_id == "asset_establishing"


def test_returns_none_when_all_candidates_too_short(tmp_path: Path) -> None:
    project, cut_plan, request = _setup(
        tmp_path, inventory=[("clip.mp4", "Establishing landscape shot of the canyon.")]
    )
    with patch(f"{_MODULE}.probe_duration_seconds", return_value=1.0):
        candidate = select_generic_fallback_candidate(project, cut_plan, request, needed_duration_sec=5.0)
    assert candidate is None


def test_returns_none_when_folder_has_no_assets(tmp_path: Path) -> None:
    project, cut_plan, request = _setup(tmp_path, inventory=[])
    candidate = select_generic_fallback_candidate(project, cut_plan, request, needed_duration_sec=5.0)
    assert candidate is None


def test_returns_none_when_request_has_no_folder_name(tmp_path: Path) -> None:
    project, cut_plan, request = _setup(
        tmp_path, inventory=[("establishing.mp4", "Establishing landscape shot.")]
    )
    request = request.model_copy(update={"folder_name": ""})
    candidate = select_generic_fallback_candidate(project, cut_plan, request, needed_duration_sec=5.0)
    assert candidate is None


def test_respects_max_asset_usage_limit(tmp_path: Path) -> None:
    project, cut_plan, request = _setup(
        tmp_path, inventory=[("establishing.mp4", "Establishing landscape shot of the canyon.")]
    )
    save_cut_plan_settings(project, CutPlanSettings(project_id=project.id, max_asset_usage=1))
    cut_plan = cut_plan.model_copy(
        update={
            "settings_snapshot": CutPlanSettings(project_id=project.id, max_asset_usage=1).model_dump(
                exclude={"project_id"}
            ),
            "asset_usage_summary": {"asset_establishing": 1},
        }
    )
    with patch(f"{_MODULE}.probe_duration_seconds", return_value=20.0):
        candidate = select_generic_fallback_candidate(project, cut_plan, request, needed_duration_sec=5.0)
    assert candidate is None


def test_image_asset_ignores_duration_requirement(tmp_path: Path) -> None:
    project, cut_plan, request = _setup(
        tmp_path, inventory=[("establishing.jpg", "Establishing landscape photo of the canyon.")]
    )
    candidate = select_generic_fallback_candidate(project, cut_plan, request, needed_duration_sec=999.0)
    assert candidate is not None
    assert candidate.asset_id == "asset_establishing"


# --- apply_generic_fallback_to_cut_plan_item ---


def test_apply_generic_fallback_builds_video_segment_and_updates_item(tmp_path: Path) -> None:
    project, cut_plan, request = _setup(
        tmp_path, inventory=[("establishing.mp4", "Establishing landscape shot of the canyon.")]
    )
    with patch(f"{_MODULE}.probe_duration_seconds", return_value=20.0):
        candidate = select_generic_fallback_candidate(project, cut_plan, request, needed_duration_sec=5.0)
        assert candidate is not None
        updated_cut_plan = apply_generic_fallback_to_cut_plan_item(project, cut_plan, request, candidate)

    updated_item = next(item for item in updated_cut_plan.items if item.cut_item_id == "cut_001")
    assert updated_item.asset_selection_status == "GENERIC_FALLBACK_USED"
    assert updated_item.chosen_asset_id == candidate.asset_id
    assert not updated_item.needs_supplement_asset
    assert CUT_PLAN_ERROR_SUPPLEMENT_REQUIRED not in updated_item.blockers
    assert len(updated_item.planned_visual_segments) == 1
    segment = updated_item.planned_visual_segments[0]
    assert segment.asset_id == candidate.asset_id
    assert segment.reason == "generic_fallback_asset"
    assert segment.asset_type == "video"


def test_apply_generic_fallback_builds_image_segment(tmp_path: Path) -> None:
    project, cut_plan, request = _setup(
        tmp_path, inventory=[("establishing.jpg", "Establishing landscape photo of the canyon.")]
    )
    candidate = select_generic_fallback_candidate(project, cut_plan, request, needed_duration_sec=5.0)
    assert candidate is not None
    updated_cut_plan = apply_generic_fallback_to_cut_plan_item(project, cut_plan, request, candidate)
    updated_item = next(item for item in updated_cut_plan.items if item.cut_item_id == "cut_001")
    segment = updated_item.planned_visual_segments[0]
    assert segment.asset_type == "image"
    assert segment.source_out_sec == pytest.approx(5.0)


def test_apply_generic_fallback_raises_when_video_too_short(tmp_path: Path) -> None:
    """Verteidigungslinie: selbst wenn select_generic_fallback_candidate
    (theoretisch) einen zu kurzen Kandidaten liefern würde, blockt
    apply_generic_fallback_to_cut_plan_item die Übernahme hart ab, statt
    ein zu kurzes Segment zu erzeugen (analog zum Supplement-Accept-Pfad)."""
    project, cut_plan, request = _setup(
        tmp_path, inventory=[("clip.mp4", "Establishing landscape shot of the canyon.")]
    )
    with patch(f"{_MODULE}.probe_duration_seconds", return_value=20.0):
        candidate = select_generic_fallback_candidate(project, cut_plan, request, needed_duration_sec=5.0)
    assert candidate is not None

    with patch(f"{_MODULE}.probe_duration_seconds", return_value=1.0):
        with pytest.raises(ValueError, match="zu kurz"):
            apply_generic_fallback_to_cut_plan_item(project, cut_plan, request, candidate)


def test_apply_generic_fallback_raises_for_unknown_cut_item_id(tmp_path: Path) -> None:
    project, cut_plan, request = _setup(
        tmp_path, inventory=[("establishing.jpg", "Establishing landscape photo of the canyon.")]
    )
    candidate = select_generic_fallback_candidate(project, cut_plan, request, needed_duration_sec=5.0)
    assert candidate is not None
    bad_request = request.model_copy(update={"cut_item_id": "does_not_exist"})
    with pytest.raises(ValueError, match="nicht im Cut Plan gefunden"):
        apply_generic_fallback_to_cut_plan_item(project, cut_plan, bad_request, candidate)


# --- apply_generic_fallback_for_cut_plan_request (I/O-Orchestrator) ---


def test_orchestrator_applies_and_persists_fallback(tmp_path: Path) -> None:
    project, cut_plan, request = _setup(
        tmp_path, inventory=[("establishing.mp4", "Establishing landscape shot of the canyon.")]
    )
    with patch(f"{_MODULE}.probe_duration_seconds", return_value=20.0):
        updated_cut_plan, candidate = apply_generic_fallback_for_cut_plan_request(project, request.request_id)

    assert candidate is not None
    assert updated_cut_plan is not None
    reloaded_draft = load_cut_plan_draft(project)
    updated_item = next(item for item in reloaded_draft.items if item.cut_item_id == "cut_001")
    assert updated_item.asset_selection_status == "GENERIC_FALLBACK_USED"

    reloaded_requests = load_cut_plan_supplement_requests(project)
    persisted = next(r for r in reloaded_requests.requests if r.request_id == request.request_id)
    assert persisted.accepted_asset_id == candidate.asset_id


def test_orchestrator_returns_none_none_when_no_candidate_found(tmp_path: Path) -> None:
    project, cut_plan, request = _setup(tmp_path, inventory=[])
    updated_cut_plan, candidate = apply_generic_fallback_for_cut_plan_request(project, request.request_id)
    assert updated_cut_plan is None
    assert candidate is None


def test_orchestrator_raises_when_request_missing(tmp_path: Path) -> None:
    project, cut_plan, request = _setup(tmp_path, inventory=[])
    with pytest.raises(ValueError, match="nicht gefunden"):
        apply_generic_fallback_for_cut_plan_request(project, "does_not_exist")
