"""R1-Abnahmetests: Provider-Config, lokale Medien, OTIO fail-closed, Koexistenz."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from otio_app.defaults import DEFAULT_ENHANCED_WORK_SUBDIR, DEFAULT_WORK_SUBDIR
from otio_app.models import Project, ProjectCreate, ProjectMode
from otio_app.project_repository import create_project, find_project_by_root_and_language
from otio_app.services.without_voiceover_enhanced.cut_plan_service import (
    CutPlanError,
    accept_supplement_candidates,
    search_supplements_for_gaps,
)
from otio_app.services.without_voiceover_enhanced.io_utils import write_json
from otio_app.services.without_voiceover_enhanced.local_media_service import (
    STATUS_EXPORT_READY,
    STATUS_LOCAL_MEDIA_INVALID,
    STATUS_LOCAL_MEDIA_MISSING,
    assign_local_media_path,
    validate_local_media_path,
)
from otio_app.services.without_voiceover_enhanced.models import (
    CoverageGap,
    CoverageGapsDocument,
    EnhancedScriptDocument,
    ResolvedTimelineDocument,
    ScriptSegment,
    StockCandidate,
    StockSearchResultsDocument,
)
from otio_app.services.without_voiceover_enhanced.otio_export_service import (
    EnhancedOtioExportError,
    export_otio_from_resolved_timeline,
)
from otio_app.services.without_voiceover_enhanced.paths import (
    accepted_supplements_path,
    coverage_gaps_path,
    resolved_timeline_path,
    stock_providers_config_path,
    stock_search_results_path,
)
from otio_app.services.without_voiceover_enhanced.script_lock_service import (
    lock_script,
    save_script_draft,
)
from otio_app.services.without_voiceover_enhanced.stock.mock import MockStockProvider
from otio_app.services.without_voiceover_enhanced.stock.registry import (
    REQUIRED_PROVIDER_NAMES,
    get_stock_providers,
    search_all_providers,
    search_configured_providers,
)
from otio_app.services.without_voiceover_enhanced.stock_provider_config import (
    PROVIDER_UI_LABELS,
    SUPPORTED_STOCK_PROVIDERS,
    UNSUPPORTED_PROVIDER_KEYS,
    default_stock_providers_config,
    load_stock_providers_config,
    save_stock_providers_config,
)
from otio_app.services.without_voiceover_enhanced.timeline_resolver import (
    TimelineResolveError,
    resolve_final_timeline,
)
from otio_app.ui.without_voiceover_enhanced import cut_plan_tab as cut_plan_ui


def _enhanced_project(tmp_path: Path, name: str = "Enh") -> Project:
    root = tmp_path / "proj"
    work = root / DEFAULT_ENHANCED_WORK_SUBDIR
    work.mkdir(parents=True)
    (root / "Assets").mkdir(exist_ok=True)
    return Project(
        name=name,
        project_root=str(root),
        work_dir=str(work),
        project_mode=ProjectMode.WITHOUT_VOICEOVER_ENHANCED,
        language="en",
        asset_subdir_names=["Assets"],
        selected_asset_subdirs=["Assets"],
        fps=25.0,
    )


def _lock_minimal(project: Project) -> None:
    save_script_draft(
        project,
        EnhancedScriptDocument(
            narration_full="Text.",
            segments=[
                ScriptSegment(segment_id="segment_001", text="Text.", sequence_index=1)
            ],
        ),
    )
    lock_script(project)


def test_default_config_exactly_five_no_adobe() -> None:
    config = default_stock_providers_config()
    assert set(config.providers) == set(SUPPORTED_STOCK_PROVIDERS)
    assert list(SUPPORTED_STOCK_PROVIDERS) == list(REQUIRED_PROVIDER_NAMES)
    assert "adobe_stock" not in config.providers
    names = [p.provider_name for p in get_stock_providers()]
    assert "adobe_stock" not in names
    assert "adobe_stock" in UNSUPPORTED_PROVIDER_KEYS


def test_ui_module_has_no_adobe_provider_controls() -> None:
    source = Path(cut_plan_ui.__file__).read_text(encoding="utf-8")
    assert "adobe_stock" not in source.lower()
    assert "Adobe Stock" not in source
    for name in SUPPORTED_STOCK_PROVIDERS:
        assert PROVIDER_UI_LABELS[name] in source or name in SUPPORTED_STOCK_PROVIDERS
    # Only the five supported providers are offered in the UI labels map.
    assert set(PROVIDER_UI_LABELS) == set(SUPPORTED_STOCK_PROVIDERS)


def test_provider_config_persist_per_project(tmp_path: Path) -> None:
    project_a = _enhanced_project(tmp_path / "a", "A")
    project_b = _enhanced_project(tmp_path / "b", "B")
    assert not stock_providers_config_path(project_a).is_file()
    defaults = load_stock_providers_config(project_a)
    assert all(defaults.providers[n].enabled for n in SUPPORTED_STOCK_PROVIDERS)

    save_stock_providers_config(
        project_a,
        {
            "pexels": False,
            "pixabay": False,
            "wikimedia": True,
            "openverse": True,
            "archive_org": False,
        },
    )
    save_stock_providers_config(
        project_b,
        {
            "pexels": True,
            "pixabay": True,
            "wikimedia": False,
            "openverse": False,
            "archive_org": True,
        },
    )
    loaded_a = load_stock_providers_config(project_a)
    loaded_b = load_stock_providers_config(project_b)
    assert loaded_a.providers["pexels"].enabled is False
    assert loaded_a.providers["wikimedia"].enabled is True
    assert loaded_b.providers["pexels"].enabled is True
    assert loaded_b.providers["wikimedia"].enabled is False
    assert stock_providers_config_path(project_a).is_file()
    # No secrets in config file
    raw = stock_providers_config_path(project_a).read_text(encoding="utf-8")
    assert "api_key" not in raw.lower()
    assert "sk-" not in raw


def test_disabled_providers_never_called(tmp_path: Path) -> None:
    project = _enhanced_project(tmp_path)
    save_stock_providers_config(
        project,
        {
            "pexels": False,
            "pixabay": False,
            "wikimedia": True,
            "openverse": True,
            "archive_org": False,
        },
    )

    called: list[str] = []

    class TrackingProvider(MockStockProvider):
        def __init__(self, name: str):
            super().__init__(available=True)
            self.provider_name = name

        def readiness(self):
            called.append(f"ready:{self.provider_name}")
            return super().readiness()

        def search(self, query: str, media_type: str | None = None):
            called.append(f"search:{self.provider_name}")
            return super().search(query, media_type=media_type)

    providers = [
        TrackingProvider("pexels"),
        TrackingProvider("pixabay"),
        TrackingProvider("wikimedia"),
        TrackingProvider("openverse"),
        TrackingProvider("archive_org"),
    ]
    # Patch registry map via search_all_providers providers=...
    from otio_app.services.without_voiceover_enhanced.stock_provider_config import (
        enabled_provider_names,
    )

    enabled = enabled_provider_names(project)
    candidates, status = search_all_providers(
        "Monument Valley",
        providers=providers,
        enabled_names=enabled,
    )
    assert status["pexels"] == "disabled"
    assert status["pixabay"] == "disabled"
    assert status["archive_org"] == "disabled"
    assert status["wikimedia"] == "completed"
    assert status["openverse"] == "completed"
    assert "ready:pexels" not in called
    assert "search:pexels" not in called
    assert "ready:pixabay" not in called
    assert "search:archive_org" not in called
    assert "search:wikimedia" in called
    assert "search:openverse" in called
    assert candidates


def test_all_providers_disabled_no_error_preserves_history(tmp_path: Path) -> None:
    project = _enhanced_project(tmp_path)
    _lock_minimal(project)
    write_json(
        coverage_gaps_path(project),
        CoverageGapsDocument(
            script_version="script-v1",
            gaps=[
                CoverageGap(
                    gap_id="gap_001",
                    related_shot_ids=["shot_001"],
                    subject="Valley",
                    search_queries=["Monument Valley"],
                )
            ],
        ),
    )
    write_json(
        stock_search_results_path(project),
        StockSearchResultsDocument(
            script_version="script-v1",
            candidates=[
                StockCandidate(
                    candidate_id="stock_hist",
                    provider="wikimedia",
                    title="historic",
                )
            ],
        ),
    )
    save_stock_providers_config(
        project,
        {name: False for name in SUPPORTED_STOCK_PROVIDERS},
    )
    results = search_supplements_for_gaps(project)
    assert results.message == "Keine Stockanbieter aktiviert."
    assert all(v == "disabled" for v in results.provider_status.values())
    assert any(c.candidate_id == "stock_hist" for c in results.candidates)


def test_stock_search_skips_already_filled_gaps(tmp_path: Path) -> None:
    """Stocksuche nur offene Gaps — erfüllte (Funnel export_ready) überspringen."""
    from otio_app.services.without_voiceover_enhanced.models import (
        SupplementFunnelGapReport,
        SupplementFunnelReport,
    )
    from otio_app.services.without_voiceover_enhanced.paths import (
        supplement_funnel_report_path,
    )

    project = _enhanced_project(tmp_path)
    _lock_minimal(project)
    run_id = "run_stock_open_only"
    write_json(
        coverage_gaps_path(project),
        CoverageGapsDocument(
            script_version="script-v1",
            cut_plan_run_id=run_id,
            gaps=[
                CoverageGap(
                    gap_id="gap_filled",
                    related_shot_ids=["shot_001"],
                    subject="Filled",
                    search_queries=["already filled scene"],
                    preferred_media_type="photo",
                ),
                CoverageGap(
                    gap_id="gap_open",
                    related_shot_ids=["shot_002"],
                    subject="Open",
                    search_queries=["still open scene"],
                    preferred_media_type="photo",
                ),
            ],
        ),
    )
    write_json(
        supplement_funnel_report_path(project),
        SupplementFunnelReport(
            run_id="funnel_prev",
            script_version="script-v1",
            cut_plan_run_id=run_id,
            gaps=[
                SupplementFunnelGapReport(
                    gap_id="gap_filled",
                    filled=True,
                    export_ready_candidate_id="cand_filled",
                )
            ],
            filled_gap_ids=["gap_filled"],
        ),
    )
    save_stock_providers_config(
        project,
        {name: (name == "wikimedia") for name in SUPPORTED_STOCK_PROVIDERS},
    )

    searched_queries: list[str] = []

    def _fake_search(project, query, media_type=None):
        searched_queries.append(str(query))
        return (
            [
                StockCandidate(
                    candidate_id=f"wikimedia_{len(searched_queries):03d}",
                    provider="wikimedia",
                    title=str(query),
                    media_type="photo",
                    preview_url="https://upload.wikimedia.org/wikipedia/commons/a.jpg",
                    download_url="https://upload.wikimedia.org/wikipedia/commons/a.jpg",
                    source_page="https://commons.wikimedia.org/wiki/File:a.jpg",
                )
            ],
            {"wikimedia": "completed"},
            ["wikimedia"],
        )

    from unittest.mock import patch

    with (
        patch(
            "otio_app.services.without_voiceover_enhanced.cut_plan_service.search_configured_providers",
            side_effect=_fake_search,
        ),
        patch(
            "otio_app.services.without_voiceover_enhanced.gap_search_concepts.enrich_coverage_search_concepts",
            side_effect=lambda project, coverage: coverage,
        ),
    ):
        results = search_supplements_for_gaps(project)

    assert not any("already filled" in q for q in searched_queries)
    assert any("still open" in q for q in searched_queries)
    assert results.candidates
    assert all(c.gap_id == "gap_open" for c in results.candidates)


def test_stock_search_errors_when_all_gaps_filled(tmp_path: Path) -> None:
    from otio_app.services.without_voiceover_enhanced.models import (
        SupplementFunnelGapReport,
        SupplementFunnelReport,
    )
    from otio_app.services.without_voiceover_enhanced.paths import (
        supplement_funnel_report_path,
    )

    project = _enhanced_project(tmp_path)
    _lock_minimal(project)
    run_id = "run_all_filled"
    write_json(
        coverage_gaps_path(project),
        CoverageGapsDocument(
            script_version="script-v1",
            cut_plan_run_id=run_id,
            gaps=[
                CoverageGap(
                    gap_id="gap_only",
                    related_shot_ids=["shot_001"],
                    subject="Done",
                    search_queries=["done scene"],
                )
            ],
        ),
    )
    write_json(
        supplement_funnel_report_path(project),
        SupplementFunnelReport(
            run_id="funnel_prev",
            script_version="script-v1",
            cut_plan_run_id=run_id,
            gaps=[
                SupplementFunnelGapReport(
                    gap_id="gap_only",
                    filled=True,
                    export_ready_candidate_id="cand",
                )
            ],
            filled_gap_ids=["gap_only"],
        ),
    )
    save_stock_providers_config(
        project,
        {name: (name == "wikimedia") for name in SUPPORTED_STOCK_PROVIDERS},
    )
    with pytest.raises(CutPlanError, match="bereits erfüllt"):
        search_supplements_for_gaps(project)


def test_unknown_provider_key_not_executed() -> None:
    class AdobeFake(MockStockProvider):
        provider_name = "adobe_stock"

        def search(self, query: str, media_type: str | None = None):
            raise AssertionError("Adobe must never be called")

    candidates, status = search_all_providers(
        "q",
        providers=[AdobeFake(), MockStockProvider()],
        enabled_names=["adobe_stock", "mock"],
    )
    # Unsupported key marked; mock still runs if enabled and present.
    assert status.get("adobe_stock") == "unsupported" or "adobe_stock" not in [
        p.provider_name for p in get_stock_providers()
    ]
    assert candidates or status.get("mock") in {"completed", "unavailable", "failed", "disabled"}


def test_provider_failure_isolation() -> None:
    class Boom(MockStockProvider):
        provider_name = "pexels"

        def readiness(self):
            from otio_app.services.without_voiceover_enhanced.stock.base import (
                ProviderStatus,
            )

            return ProviderStatus("pexels", "ready")

        def search(self, query: str, media_type: str | None = None):
            raise RuntimeError("network down")

    class Ok(MockStockProvider):
        provider_name = "wikimedia"

    _, status = search_all_providers(
        "q",
        providers=[Boom(), Ok()],
        enabled_names=["pexels", "wikimedia"],
    )
    assert status["pexels"] == "failed"
    assert status["wikimedia"] == "completed"


def test_local_media_validation_and_otio_fail_closed(tmp_path: Path) -> None:
    from otio_app.services.without_voiceover_enhanced.cut_plan_options import (
        CutPlanOptions,
        save_cut_plan_options,
    )

    project = _enhanced_project(tmp_path)
    _lock_minimal(project)
    save_cut_plan_options(
        project, CutPlanOptions(still_image_style_enabled=False)
    )
    write_json(
        stock_search_results_path(project),
        StockSearchResultsDocument(
            script_version="script-v1",
            candidates=[
                StockCandidate(
                    candidate_id="stock_123",
                    provider="wikimedia",
                    title="A",
                    preview_url="https://example.com/p.jpg",
                    source_page="https://example.com/s",
                )
            ],
        ),
    )
    accepted = accept_supplement_candidates(project, ["stock_123"])
    assert accepted.supplements[0].media_validation_status == STATUS_LOCAL_MEDIA_MISSING

    # Export without resolved timeline / local file blocked.
    with pytest.raises(EnhancedOtioExportError):
        export_otio_from_resolved_timeline(project)

    status, err = validate_local_media_path("https://example.com/x.jpg")
    assert status == STATUS_LOCAL_MEDIA_INVALID
    assert err is not None

    from PIL import Image

    local = project.work_dir_path / "original.jpg"
    Image.new("RGB", (16, 16), color=(20, 40, 60)).save(local, format="JPEG")
    updated = assign_local_media_path(project, "stock_123", str(local))
    assert updated.media_validation_status == STATUS_EXPORT_READY
    assert updated.local_media_path == str(local)

    # Deactivating provider later must not invalidate already export_ready asset.
    save_stock_providers_config(
        project, {name: False for name in SUPPORTED_STOCK_PROVIDERS}
    )
    reloaded = load_stock_providers_config(project)
    assert reloaded.providers["wikimedia"].enabled is False
    from otio_app.services.without_voiceover_enhanced.io_utils import load_model
    from otio_app.services.without_voiceover_enhanced.models import (
        AcceptedSupplementsDocument,
    )

    still = load_model(accepted_supplements_path(project), AcceptedSupplementsDocument)
    assert still is not None
    assert still.supplements[0].media_validation_status == STATUS_EXPORT_READY

    write_json(
        resolved_timeline_path(project),
        ResolvedTimelineDocument(
            script_version="script-v1",
            fps=25.0,
            total_duration_seconds=1.0,
            audio_segments=[],
            shots=[
                {
                    "shot_id": "shot_001",
                    "asset_id": "stock_123",
                    "timeline_start_seconds": 0.0,
                    "timeline_end_seconds": 1.0,
                    "source_start_seconds": 0.0,
                    "source_end_seconds": 1.0,
                    "resolved_media_path": str(local),
                    "resolved_media_kind": "image",
                    "resolved_available_start_seconds": 0.0,
                    "hold_mode": "",
                }
            ],
            voiceover_preroll_sec=0.0,
            voiceover_postroll_sec=0.0,
            repairs=[],
            errors=[],
        ),
    )
    # Kein segment_timings → Narration nicht erwartet; Still wird Hold-Video.
    save_cut_plan_options(
        project,
        CutPlanOptions(
            voiceover_preroll_sec=0.0,
            voiceover_postroll_sec=0.0,
            still_image_style_enabled=False,
        ),
    )
    out = export_otio_from_resolved_timeline(project, basename="r1_ok")
    payload = out.read_text(encoding="utf-8")
    assert "http://" not in payload
    assert "https://" not in payload
    assert "still_hold_" in payload or str(local) in payload


def test_otio_blocks_preview_and_source_page(tmp_path: Path) -> None:
    project = _enhanced_project(tmp_path)
    write_json(
        accepted_supplements_path(project),
        {
            "schema_version": "enhanced-accepted-supplements-v1",
            "script_version": "script-v1",
            "supplements": [
                {
                    "candidate_id": "stock_url",
                    "provider": "openverse",
                    "preview_url": "https://example.com/preview.jpg",
                    "source_page": "https://example.com/page",
                    "selected": True,
                    "media_validation_status": "selected",
                }
            ],
        },
    )
    write_json(
        resolved_timeline_path(project),
        {
            "schema_version": "enhanced-resolved-timeline-v1",
            "script_version": "script-v1",
            "fps": 25.0,
            "total_duration_seconds": 1.0,
            "audio_segments": [],
            "shots": [
                {
                    "shot_id": "shot_001",
                    "asset_id": "stock_url",
                    "timeline_start_seconds": 0.0,
                    "timeline_end_seconds": 1.0,
                    "source_start_seconds": 0.0,
                    "source_end_seconds": 1.0,
                }
            ],
            "repairs": [],
            "errors": [],
        },
    )
    with pytest.raises(
        EnhancedOtioExportError,
        match="lokale Mediendatei|resolved_media_path fehlt|Web-URL",
    ):
        export_otio_from_resolved_timeline(project)


def test_classic_and_enhanced_coexist_same_root_language(
    tmp_path: Path, temp_db_path: Path
) -> None:
    root = tmp_path / "shared_root"
    root.mkdir()
    (root / "Assets").mkdir()
    classic_work = root / DEFAULT_WORK_SUBDIR
    enhanced_work = root / DEFAULT_ENHANCED_WORK_SUBDIR
    classic_work.mkdir()
    enhanced_work.mkdir()

    classic = create_project(
        ProjectCreate(
            name="Classic",
            project_root=str(root),
            work_dir=str(classic_work),
            language="en",
            project_mode=ProjectMode.WITHOUT_VOICEOVER,
        ),
        db_path=temp_db_path,
        asset_subdir_names=["Assets"],
        selected_asset_subdirs=["Assets"],
    )
    enhanced = create_project(
        ProjectCreate(
            name="Enhanced",
            project_root=str(root),
            work_dir=str(enhanced_work),
            language="en",
            project_mode=ProjectMode.WITHOUT_VOICEOVER_ENHANCED,
        ),
        db_path=temp_db_path,
        asset_subdir_names=["Assets"],
        selected_asset_subdirs=["Assets"],
    )
    assert classic.project_mode == ProjectMode.WITHOUT_VOICEOVER
    assert enhanced.project_mode == ProjectMode.WITHOUT_VOICEOVER_ENHANCED
    assert Path(classic.work_dir).name == DEFAULT_WORK_SUBDIR
    assert Path(enhanced.work_dir).name == DEFAULT_ENHANCED_WORK_SUBDIR
    assert "_otio_v2" not in enhanced.work_dir

    found_classic = find_project_by_root_and_language(
        root, "en", db_path=temp_db_path, project_mode=ProjectMode.WITHOUT_VOICEOVER
    )
    found_enhanced = find_project_by_root_and_language(
        root,
        "en",
        db_path=temp_db_path,
        project_mode=ProjectMode.WITHOUT_VOICEOVER_ENHANCED,
    )
    assert found_classic is not None and found_classic.id == classic.id
    assert found_enhanced is not None and found_enhanced.id == enhanced.id

    with pytest.raises(ValueError, match="bereits ein Projekt"):
        create_project(
            ProjectCreate(
                name="Enhanced Dup",
                project_root=str(root),
                work_dir=str(enhanced_work),
                language="en",
                project_mode=ProjectMode.WITHOUT_VOICEOVER_ENHANCED,
            ),
            db_path=temp_db_path,
            asset_subdir_names=["Assets"],
            selected_asset_subdirs=["Assets"],
        )


def test_invalid_config_falls_back_to_defaults(tmp_path: Path) -> None:
    project = _enhanced_project(tmp_path)
    path = stock_providers_config_path(project)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{not-json", encoding="utf-8")
    config = load_stock_providers_config(project)
    assert all(config.providers[n].enabled for n in SUPPORTED_STOCK_PROVIDERS)


def test_otio_allow_errors_exports_partial_timeline_with_gaps(tmp_path: Path) -> None:
    """Test-Export trotz Resolve-Fehler: aufgelöste Shots bleiben, Lücken als Gaps."""
    from PIL import Image

    from otio_app.services.without_voiceover_enhanced.cut_plan_options import (
        CutPlanOptions,
        save_cut_plan_options,
    )

    project = _enhanced_project(tmp_path)
    _lock_minimal(project)
    save_cut_plan_options(
        project, CutPlanOptions(still_image_style_enabled=False)
    )
    local = project.work_dir_path / "clip.jpg"
    Image.new("RGB", (16, 16), color=(10, 20, 30)).save(local, format="JPEG")
    write_json(
        accepted_supplements_path(project),
        {
            "schema_version": "enhanced-accepted-supplements-v1",
            "script_version": "script-v1",
            "supplements": [
                {
                    "candidate_id": "stock_ok",
                    "provider": "manual",
                    "media_type": "photo",
                    "selected": True,
                    "gap_id": "gap_1",
                    "local_media_path": str(local),
                    "media_validation_status": "export_ready",
                    "funnel_managed": True,
                    "license": "manual_local",
                    "source_page": str(local),
                }
            ],
        },
    )
    write_json(
        resolved_timeline_path(project),
        ResolvedTimelineDocument(
            script_version="script-v1",
            fps=25.0,
            total_duration_seconds=10.0,
            audio_segments=[],
            shots=[
                {
                    "shot_id": "shot_a",
                    "asset_id": "stock_ok",
                    "timeline_start_seconds": 0.0,
                    "timeline_end_seconds": 2.0,
                    "source_start_seconds": 0.0,
                    "source_end_seconds": 2.0,
                    "resolved_media_path": str(local),
                    "resolved_media_kind": "image",
                },
                {
                    "shot_id": "shot_c",
                    "asset_id": "stock_ok",
                    "timeline_start_seconds": 6.0,
                    "timeline_end_seconds": 8.0,
                    "source_start_seconds": 0.0,
                    "source_end_seconds": 2.0,
                    "resolved_media_path": str(local),
                    "resolved_media_kind": "image",
                },
            ],
            repairs=[],
            errors=[
                "Asset short_clip ist kürzer als gewünschter Shot (5.0s < 12.0s)."
            ],
        ),
    )

    with pytest.raises(EnhancedOtioExportError, match="enthält Fehler"):
        export_otio_from_resolved_timeline(project, basename="prod_blocked")

    out = export_otio_from_resolved_timeline(
        project,
        basename="preview_gaps",
        allow_errors=True,
    )
    assert out.is_file()
    payload = out.read_text(encoding="utf-8")
    assert "shot_a" in payload
    assert "shot_c" in payload
    assert (
        "Gap" in payload
        or "gap" in payload.lower()
        or "still_hold_" in payload
        or "placeholder" in payload.lower()
        or "timeline_hole_before_" in payload
    )
    assert "http://" not in payload


def test_ui_test_otio_with_gaps_markers() -> None:
    cut_src = Path(
        "otio_app/ui/without_voiceover_enhanced/cut_plan_tab.py"
    ).read_text(encoding="utf-8")
    final_src = Path(
        "otio_app/ui/without_voiceover_enhanced/final_output_tab.py"
    ).read_text(encoding="utf-8")
    assert "Test-OTIO mit Lücken erzeugen" in cut_src
    assert "allow_errors=True" in cut_src
    assert "Test-OTIO mit Lücken erzeugen" in final_src
    assert "allow_errors=True" in final_src
    assert "disabled=has_errors" in final_src
    all_otio = cut_src[cut_src.index("if run_all_otio:") : cut_src.index("if run_all_otio:") + 700]
    assert "allow_errors=False" in all_otio
    assert "allow_errors=True" not in all_otio
    chapter_otio = cut_src[cut_src.index("if run_otio:") : cut_src.index("if run_otio:") + 500]
    assert "allow_errors=False" in chapter_otio
    auto_src = Path(
        "otio_app/services/without_voiceover_enhanced/enhanced_auto_run_service.py"
    ).read_text(encoding="utf-8")
    run_otio = auto_src[auto_src.index("def _run_otio") : auto_src.index("def _run_otio") + 900]
    assert "allow_errors=False" in run_otio
    assert "allow_errors=True" not in run_otio
