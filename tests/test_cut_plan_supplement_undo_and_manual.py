"""Phase 11.6: Rücknahme (Undo) einer Übernahme + manuelle Asset-Zuweisung
aus dem Ordner-Inventory, für den isolierten Cut-Plan-Supplement-Workflow."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from otio_app.analysis_models import AssetFolderAnalysis, AssetMediaAnalysis, SupplementCandidate
from otio_app.defaults import CUT_PLAN_ERROR_SUPPLEMENT_REQUIRED
from otio_app.models import Project, ProjectMode
from otio_app.project_layout import get_folder_inventory_path
from otio_app.services.voiceover_generation.cut_plan_builder import load_cut_plan_draft, save_cut_plan_draft
from otio_app.services.voiceover_generation.cut_plan_generic_fallback_service import (
    apply_generic_fallback_for_cut_plan_request,
    apply_manual_asset_for_cut_plan_request,
    apply_manual_asset_to_cut_plan_item,
    list_manual_asset_options_for_request,
    select_generic_fallback_candidate,
)
from otio_app.services.voiceover_generation.cut_plan_models import (
    CutPlanDocument,
    CutPlanItem,
    CutPlanSettings,
    CutPlanSourceRef,
)
from otio_app.services.voiceover_generation.cut_plan_settings_service import save_cut_plan_settings
from otio_app.services.voiceover_generation.cut_plan_supplement_bridge import (
    accept_cut_plan_supplement_candidate,
    build_supplement_requests_from_cut_plan,
    load_cut_plan_supplement_requests,
    save_cut_plan_supplement_requests,
    unaccept_cut_plan_supplement_request,
    update_cut_plan_supplement_request,
)

_BRIDGE_MODULE = "otio_app.services.voiceover_generation.cut_plan_supplement_bridge"
_FALLBACK_MODULE = "otio_app.services.voiceover_generation.cut_plan_generic_fallback_service"
FOLDER_A = "Grand Canyon"


def _make_project(tmp_path: Path) -> Project:
    project_root = tmp_path / "USA"
    (project_root / FOLDER_A).mkdir(parents=True)
    return Project(
        id="undo-manual-project",
        name="Undo/Manual Test",
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


def _setup(tmp_path: Path, *, inventory: list[tuple[str, str]] | None = None):
    project = _make_project(tmp_path)
    if inventory is not None:
        _write_inventory(project, inventory)
    item = _minimal_item()
    cut_plan = CutPlanDocument(project_id=project.id, timeline_fps=25, items=[item])
    save_cut_plan_settings(project, CutPlanSettings(project_id=project.id))
    save_cut_plan_draft(project, cut_plan)
    document = build_supplement_requests_from_cut_plan(project, cut_plan)
    save_cut_plan_supplement_requests(project, document)
    return project, document.requests[0]


def _fake_stock_candidate(request_id: str) -> SupplementCandidate:
    return SupplementCandidate(
        candidate_id="cand_fake01",
        supplement_request_id=request_id,
        provider="pexels",
        title="Fake Canyon",
        media_type="image",
        width=1920,
        height=1080,
        duration_sec=0.0,
        download_url="https://example.com/fake.jpg",
        download_enabled=True,
        is_mock=False,
        requires_user_approval=False,
        match_score=0.9,
    )


def _accept_stock_candidate(project: Project, request_id: str) -> None:
    mock_adapter = MagicMock()
    mock_adapter.search.return_value = [_fake_stock_candidate(request_id)]

    def _fake_acquire(candidate, destination_folder):
        from otio_app.analysis_models import SupplementAssetSidecar
        from otio_app.services.supplement_sources.base import SupplementAsset

        destination_folder.mkdir(parents=True, exist_ok=True)
        target = destination_folder / "fake.jpg"
        target.write_bytes(b"FAKE_IMAGE_BYTES")
        sidecar = SupplementAssetSidecar(asset_id="asset_x", supplement_request_id=request_id, provider="pexels")
        return SupplementAsset(local_path=target, sidecar=sidecar)

    mock_adapter.acquire.side_effect = _fake_acquire
    with patch(f"{_BRIDGE_MODULE}.get_supplement_adapter", return_value=mock_adapter):
        from otio_app.services.voiceover_generation.cut_plan_supplement_bridge import (
            search_candidates_for_cut_plan_request,
        )

        search_candidates_for_cut_plan_request(project, request_id, {"provider": "pexels"})
        accept_cut_plan_supplement_candidate(project, request_id, "cand_fake01")


# --- unaccept_cut_plan_supplement_request ---


def test_unaccept_restores_item_to_pre_accept_state_after_stock_accept(tmp_path: Path) -> None:
    project, request = _setup(tmp_path, inventory=[])
    original_item = load_cut_plan_draft(project).items[0]

    _accept_stock_candidate(project, request.request_id)

    accepted_draft = load_cut_plan_draft(project)
    accepted_item = next(i for i in accepted_draft.items if i.cut_item_id == "cut_001")
    assert accepted_item.chosen_asset_id
    assert accepted_item.asset_selection_status == "SUPPLEMENT_USED"

    unaccept_cut_plan_supplement_request(project, request.request_id)

    restored_draft = load_cut_plan_draft(project)
    restored_item = next(i for i in restored_draft.items if i.cut_item_id == "cut_001")
    assert restored_item.chosen_asset_id == original_item.chosen_asset_id == ""
    assert restored_item.asset_selection_status == original_item.asset_selection_status == "SUPPLEMENT_REQUIRED"
    assert restored_item.needs_supplement_asset == original_item.needs_supplement_asset is True
    assert restored_item.blockers == original_item.blockers
    assert restored_item.planned_visual_segments == []

    reloaded_request = load_cut_plan_supplement_requests(project)
    persisted = next(r for r in reloaded_request.requests if r.request_id == request.request_id)
    assert persisted.accepted_asset_id == ""
    assert persisted.accepted_candidate_id == ""
    assert persisted.pre_accept_item_snapshot == {}


def test_after_unaccept_a_fresh_accept_without_force_replace_works_again(tmp_path: Path) -> None:
    project, request = _setup(tmp_path, inventory=[])
    _accept_stock_candidate(project, request.request_id)
    unaccept_cut_plan_supplement_request(project, request.request_id)

    # Erneutes Akzeptieren OHNE force_replace darf jetzt wieder funktionieren
    # (das Guard-ValueError griffe nur, wenn accepted_asset_id noch gesetzt wäre).
    _accept_stock_candidate(project, request.request_id)
    reloaded_request = load_cut_plan_supplement_requests(project)
    persisted = next(r for r in reloaded_request.requests if r.request_id == request.request_id)
    assert persisted.accepted_asset_id


def test_unaccept_raises_when_nothing_to_undo(tmp_path: Path) -> None:
    project, request = _setup(tmp_path, inventory=[])
    with pytest.raises(ValueError, match="keine Übernahme"):
        unaccept_cut_plan_supplement_request(project, request.request_id)


def test_unaccept_raises_when_no_snapshot_exists(tmp_path: Path) -> None:
    """Simuliert eine 'Legacy'-Übernahme von vor Phase 11.6: accepted_asset_id
    ist gesetzt, aber es existiert kein Snapshot."""
    project, request = _setup(tmp_path, inventory=[])
    update_cut_plan_supplement_request(project, request.request_id, accepted_asset_id="asset_legacy")
    with pytest.raises(ValueError, match="Kein gespeicherter Vorzustand"):
        unaccept_cut_plan_supplement_request(project, request.request_id)


def test_unaccept_restores_after_generic_fallback(tmp_path: Path) -> None:
    project, request = _setup(
        tmp_path, inventory=[("establishing.mp4", "Establishing landscape shot of the canyon.")]
    )
    with patch(f"{_FALLBACK_MODULE}.probe_duration_seconds", return_value=20.0):
        updated_cut_plan, candidate = apply_generic_fallback_for_cut_plan_request(project, request.request_id)
    assert candidate is not None

    unaccept_cut_plan_supplement_request(project, request.request_id)

    restored_draft = load_cut_plan_draft(project)
    restored_item = next(i for i in restored_draft.items if i.cut_item_id == "cut_001")
    assert restored_item.asset_selection_status == "SUPPLEMENT_REQUIRED"
    assert restored_item.needs_supplement_asset is True

    reloaded_request = load_cut_plan_supplement_requests(project)
    persisted = next(r for r in reloaded_request.requests if r.request_id == request.request_id)
    assert persisted.accepted_asset_id == ""


def test_second_snapshot_is_not_overwritten_by_replace(tmp_path: Path) -> None:
    """Ein 'Ersetzen' (force_replace) darf den ursprünglichen Snapshot NICHT
    überschreiben — sonst würde eine Rücknahme nach mehreren Ersetzungen nur
    zum letzten Zwischenzustand statt zum echten Ursprung zurückführen."""
    project, request = _setup(
        tmp_path, inventory=[("establishing.mp4", "Establishing landscape shot of the canyon.")]
    )
    with patch(f"{_FALLBACK_MODULE}.probe_duration_seconds", return_value=20.0):
        apply_generic_fallback_for_cut_plan_request(project, request.request_id)

    first_snapshot = load_cut_plan_supplement_requests(project).requests[0].pre_accept_item_snapshot
    assert first_snapshot

    # Ersetzen durch Stock-Akzeptanz (force_replace=True, da bereits ein
    # Asset via generischem Fallback zugewiesen ist).
    mock_adapter = MagicMock()
    mock_adapter.search.return_value = [_fake_stock_candidate(request.request_id)]

    def _fake_acquire(candidate, destination_folder):
        from otio_app.analysis_models import SupplementAssetSidecar
        from otio_app.services.supplement_sources.base import SupplementAsset

        destination_folder.mkdir(parents=True, exist_ok=True)
        target = destination_folder / "fake.jpg"
        target.write_bytes(b"FAKE_IMAGE_BYTES")
        sidecar = SupplementAssetSidecar(asset_id="asset_x", supplement_request_id=request.request_id, provider="pexels")
        return SupplementAsset(local_path=target, sidecar=sidecar)

    mock_adapter.acquire.side_effect = _fake_acquire
    with patch(f"{_BRIDGE_MODULE}.get_supplement_adapter", return_value=mock_adapter):
        from otio_app.services.voiceover_generation.cut_plan_supplement_bridge import (
            search_candidates_for_cut_plan_request,
        )

        search_candidates_for_cut_plan_request(project, request.request_id, {"provider": "pexels"})
        accept_cut_plan_supplement_candidate(project, request.request_id, "cand_fake01", force_replace=True)

    second_snapshot = load_cut_plan_supplement_requests(project).requests[0].pre_accept_item_snapshot
    assert second_snapshot == first_snapshot


# --- Guard-Tightening: accepted_asset_id statt nur status == ACCEPTED ---


def test_accept_blocks_when_generic_fallback_already_assigned_without_force_replace(tmp_path: Path) -> None:
    project, request = _setup(
        tmp_path, inventory=[("establishing.mp4", "Establishing landscape shot of the canyon.")]
    )
    with patch(f"{_FALLBACK_MODULE}.probe_duration_seconds", return_value=20.0):
        apply_generic_fallback_for_cut_plan_request(project, request.request_id)

    with pytest.raises(ValueError, match="already has an accepted asset"):
        _accept_stock_candidate(project, request.request_id)


def test_accept_with_force_replace_overwrites_generic_fallback(tmp_path: Path) -> None:
    project, request = _setup(
        tmp_path, inventory=[("establishing.mp4", "Establishing landscape shot of the canyon.")]
    )
    with patch(f"{_FALLBACK_MODULE}.probe_duration_seconds", return_value=20.0):
        apply_generic_fallback_for_cut_plan_request(project, request.request_id)

    mock_adapter = MagicMock()
    mock_adapter.search.return_value = [_fake_stock_candidate(request.request_id)]

    def _fake_acquire(candidate, destination_folder):
        from otio_app.analysis_models import SupplementAssetSidecar
        from otio_app.services.supplement_sources.base import SupplementAsset

        destination_folder.mkdir(parents=True, exist_ok=True)
        target = destination_folder / "fake.jpg"
        target.write_bytes(b"FAKE_IMAGE_BYTES")
        sidecar = SupplementAssetSidecar(asset_id="asset_x", supplement_request_id=request.request_id, provider="pexels")
        return SupplementAsset(local_path=target, sidecar=sidecar)

    mock_adapter.acquire.side_effect = _fake_acquire
    with patch(f"{_BRIDGE_MODULE}.get_supplement_adapter", return_value=mock_adapter):
        from otio_app.services.voiceover_generation.cut_plan_supplement_bridge import (
            search_candidates_for_cut_plan_request,
        )

        search_candidates_for_cut_plan_request(project, request.request_id, {"provider": "pexels"})
        updated_cut_plan = accept_cut_plan_supplement_candidate(
            project, request.request_id, "cand_fake01", force_replace=True
        )
    updated_item = next(i for i in updated_cut_plan.items if i.cut_item_id == "cut_001")
    assert updated_item.asset_selection_status == "SUPPLEMENT_USED"


def test_generic_fallback_blocks_second_call_without_force_replace(tmp_path: Path) -> None:
    project, request = _setup(
        tmp_path, inventory=[("establishing.mp4", "Establishing landscape shot of the canyon.")]
    )
    with patch(f"{_FALLBACK_MODULE}.probe_duration_seconds", return_value=20.0):
        apply_generic_fallback_for_cut_plan_request(project, request.request_id)
        with pytest.raises(ValueError, match="already has an accepted asset"):
            apply_generic_fallback_for_cut_plan_request(project, request.request_id)


# --- Manuelle Zuweisung ---


def test_list_manual_asset_options_reports_duration_and_usability(tmp_path: Path) -> None:
    project, request = _setup(
        tmp_path,
        inventory=[
            ("short.mp4", "Short clip"),
            ("long.mp4", "Establishing landscape shot"),
            ("photo.jpg", "A photo"),
        ],
    )

    def _fake_probe(path):
        return 2.0 if "short" in str(path) else 20.0

    with patch(f"{_FALLBACK_MODULE}.probe_duration_seconds", side_effect=_fake_probe):
        options = list_manual_asset_options_for_request(project, request, needed_duration_sec=5.0)

    by_id = {option.asset_id: option for option in options}
    assert by_id["asset_short"].likely_usable is False
    assert by_id["asset_long"].likely_usable is True
    assert by_id["asset_photo"].media_type == "image"
    assert by_id["asset_photo"].likely_usable is True


def test_list_manual_asset_options_empty_without_folder_name(tmp_path: Path) -> None:
    project, request = _setup(tmp_path, inventory=[("clip.mp4", "desc")])
    request = request.model_copy(update={"folder_name": ""})
    assert list_manual_asset_options_for_request(project, request, needed_duration_sec=5.0) == []


def test_apply_manual_asset_builds_segment_and_updates_item(tmp_path: Path) -> None:
    project, request = _setup(
        tmp_path, inventory=[("establishing.mp4", "Establishing landscape shot of the canyon.")]
    )
    draft = load_cut_plan_draft(project)
    with patch(f"{_FALLBACK_MODULE}.probe_duration_seconds", return_value=20.0):
        updated_cut_plan = apply_manual_asset_to_cut_plan_item(
            project,
            draft,
            request,
            asset_id="asset_establishing",
            asset_path=str(project.project_root_path / FOLDER_A / "establishing.mp4"),
        )
    updated_item = next(i for i in updated_cut_plan.items if i.cut_item_id == "cut_001")
    assert updated_item.asset_selection_status == "MANUAL_ASSET_USED"
    assert updated_item.chosen_asset_id == "asset_establishing"
    assert updated_item.planned_visual_segments[0].reason == "manual_asset"


def test_apply_manual_asset_raises_when_too_short(tmp_path: Path) -> None:
    project, request = _setup(tmp_path, inventory=[("clip.mp4", "desc")])
    draft = load_cut_plan_draft(project)
    with patch(f"{_FALLBACK_MODULE}.probe_duration_seconds", return_value=1.0):
        with pytest.raises(ValueError, match="zu kurz"):
            apply_manual_asset_to_cut_plan_item(
                project,
                draft,
                request,
                asset_id="asset_clip",
                asset_path=str(project.project_root_path / FOLDER_A / "clip.mp4"),
            )


def test_apply_manual_asset_for_cut_plan_request_persists(tmp_path: Path) -> None:
    project, request = _setup(
        tmp_path, inventory=[("establishing.mp4", "Establishing landscape shot of the canyon.")]
    )
    asset_path = str(project.project_root_path / FOLDER_A / "establishing.mp4")
    with patch(f"{_FALLBACK_MODULE}.probe_duration_seconds", return_value=20.0):
        apply_manual_asset_for_cut_plan_request(
            project, request.request_id, asset_id="asset_establishing", asset_path=asset_path
        )

    reloaded_draft = load_cut_plan_draft(project)
    updated_item = next(i for i in reloaded_draft.items if i.cut_item_id == "cut_001")
    assert updated_item.asset_selection_status == "MANUAL_ASSET_USED"

    reloaded_requests = load_cut_plan_supplement_requests(project)
    persisted = next(r for r in reloaded_requests.requests if r.request_id == request.request_id)
    assert persisted.accepted_asset_id == "asset_establishing"
    assert persisted.pre_accept_item_snapshot


def test_apply_manual_asset_for_cut_plan_request_blocked_without_force_replace(tmp_path: Path) -> None:
    project, request = _setup(
        tmp_path, inventory=[("establishing.mp4", "Establishing landscape shot of the canyon.")]
    )
    asset_path = str(project.project_root_path / FOLDER_A / "establishing.mp4")
    with patch(f"{_FALLBACK_MODULE}.probe_duration_seconds", return_value=20.0):
        apply_manual_asset_for_cut_plan_request(
            project, request.request_id, asset_id="asset_establishing", asset_path=asset_path
        )
        with pytest.raises(ValueError, match="already has an accepted asset"):
            apply_manual_asset_for_cut_plan_request(
                project, request.request_id, asset_id="asset_establishing", asset_path=asset_path
            )


def test_unaccept_restores_after_manual_assignment(tmp_path: Path) -> None:
    project, request = _setup(
        tmp_path, inventory=[("establishing.mp4", "Establishing landscape shot of the canyon.")]
    )
    asset_path = str(project.project_root_path / FOLDER_A / "establishing.mp4")
    with patch(f"{_FALLBACK_MODULE}.probe_duration_seconds", return_value=20.0):
        apply_manual_asset_for_cut_plan_request(
            project, request.request_id, asset_id="asset_establishing", asset_path=asset_path
        )

    unaccept_cut_plan_supplement_request(project, request.request_id)

    restored_draft = load_cut_plan_draft(project)
    restored_item = next(i for i in restored_draft.items if i.cut_item_id == "cut_001")
    assert restored_item.asset_selection_status == "SUPPLEMENT_REQUIRED"
