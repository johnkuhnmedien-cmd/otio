"""Streamlit-App für R1-UI-Smoke des Enhanced Supplement-Funnels.

Startet die echte Enhanced-Cut-Plan-Seite mit Fake-LLM/Preview/Download.
"""

from __future__ import annotations

import json
import os
from io import BytesIO
from pathlib import Path

import streamlit as st
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
SMOKE_ROOT = Path(
    os.environ.get(
        "FUNNEL_R1_SMOKE_ROOT",
        "/opt/cursor/artifacts/funnel-r1-ui/project",
    )
)

from otio_app.defaults import DEFAULT_ENHANCED_WORK_SUBDIR
from otio_app.models import Project, ProjectMode
from otio_app.services.without_voiceover_enhanced.io_utils import write_json
from otio_app.services.without_voiceover_enhanced.models import (
    CoverageGap,
    CoverageGapsDocument,
    EnhancedScriptDocument,
    ResolvedShot,
    ResolvedTimelineDocument,
    ScriptSegment,
    StockCandidate,
    StockSearchResultsDocument,
)
from otio_app.services.without_voiceover_enhanced.paths import (
    coverage_gaps_path,
    resolved_timeline_path,
    stock_search_results_path,
)
from otio_app.services.without_voiceover_enhanced.script_lock_service import (
    lock_script,
    save_script_draft,
)
from otio_app.services.without_voiceover_enhanced.stock.safe_fetch import SafeFetchResult
from otio_app.services.without_voiceover_enhanced.stock_provider_config import (
    save_stock_providers_config,
)
from otio_app.services.without_voiceover_enhanced.otio_export_service import (
    export_otio_from_resolved_timeline,
)
import otio_app.ui.without_voiceover_enhanced.cut_plan_tab as cut_plan_tab
import otio_app.services.without_voiceover_enhanced.supplement_funnel_service as funnel_svc


def _jpeg(color=(20, 40, 60)) -> bytes:
    buf = BytesIO()
    Image.new("RGB", (64, 64), color=color).save(buf, format="JPEG")
    return buf.getvalue()


def _ensure_project() -> Project:
    root = SMOKE_ROOT
    work = root / DEFAULT_ENHANCED_WORK_SUBDIR
    work.mkdir(parents=True, exist_ok=True)
    (root / "Canyon").mkdir(exist_ok=True)
    project = Project(
        name="FunnelR1Smoke",
        project_root=str(root),
        work_dir=str(work),
        language="de",
        project_mode=ProjectMode.WITHOUT_VOICEOVER_ENHANCED,
        fps=25.0,
        frames_per_shot=3,
        selected_asset_subdirs=["Canyon"],
        asset_subdir_names=["Canyon"],
    )
    # Projekt nur einmal seeden — language_work_dir kann DE/de sein.
    from otio_app.services.without_voiceover_enhanced.paths import script_locked_path

    if not script_locked_path(project).is_file():
        save_script_draft(
            project,
            EnhancedScriptDocument(
                narration_full="Zion canyon road.",
                segments=[
                    ScriptSegment(
                        segment_id="Canyon_segment_001",
                        text="Zion canyon road.",
                        sequence_index=1,
                        folder_name="Canyon",
                    )
                ],
            ),
        )
        lock_script(project)
        save_stock_providers_config(
            project,
            {
                "pexels": True,
                "pixabay": False,
                "wikimedia": False,
                "openverse": False,
                "archive_org": False,
            },
        )
        write_json(
            coverage_gaps_path(project),
            CoverageGapsDocument(
                script_version="script-v1",
                gaps=[CoverageGap(gap_id="gap_1", needed_visual="canyon road")],
            ),
        )
        candidates = []
        for i in range(1, 21):
            candidates.append(
                StockCandidate(
                    candidate_id=f"pexels_photo_{i:03d}",
                    provider="pexels",
                    provider_asset_id=str(3000 + i),
                    title=f"Canyon still {i}",
                    media_type="photo",
                    creator="Smoke",
                    source_page=f"https://www.pexels.com/photo/{i}/",
                    preview_url=f"https://images.pexels.com/photos/{i}/preview.jpg",
                    download_url=f"https://images.pexels.com/photos/{i}/full.jpg",
                    width=1920,
                    height=1080,
                    license="Pexels License",
                    attribution="Smoke",
                    gap_id="gap_1",
                )
            )
        write_json(
            stock_search_results_path(project),
            StockSearchResultsDocument(
                script_version="script-v1", candidates=candidates
            ),
        )
    return project


def _install_fakes(project: Project) -> None:
    candidates = [
        StockCandidate.model_validate(c)
        for c in json.loads(
            stock_search_results_path(project).read_text(encoding="utf-8")
        ).get("candidates", [])
    ]
    download_log = Path(os.environ.get(
        "FUNNEL_R1_DOWNLOAD_LOG",
        "/opt/cursor/artifacts/funnel-r1-ui/download_calls.json",
    ))

    def text_llm(prompt: str) -> str:
        return json.dumps(
            {
                "gap_id": "gap_1",
                "candidate_reviews": [
                    {
                        "candidate_id": c["candidate_id"]
                        if isinstance(c, dict)
                        else c.candidate_id,
                        "text_relevance": 90 - (i % 8),
                        "metadata_quality": 80,
                        "media_type_fit": 85,
                        "license_metadata_quality": 90,
                        "misrepresentation_risk": 5,
                        "reason": "text ok",
                    }
                    for i, c in enumerate(
                        json.loads(
                            stock_search_results_path(project).read_text(
                                encoding="utf-8"
                            )
                        )["candidates"]
                    )
                ],
            }
        )

    def vision_llm(prompt: str, images):
        ids = [label for label, _ in images]
        if "Finalisten" in prompt:
            return json.dumps(
                {
                    "gap_id": "gap_1",
                    "finalists": [
                        {
                            "candidate_id": cid,
                            "final_score": 96 - i,
                            "rank": i + 1,
                            "decision": "winner" if i == 0 else "fallback",
                            "reason": "final",
                        }
                        for i, cid in enumerate(ids)
                    ],
                }
            )
        return json.dumps(
            {
                "candidate_reviews": [
                    {
                        "candidate_id": cid,
                        "semantic_fit": 88 - i,
                        "editorial_function_fit": 80,
                        "style_fit": 70,
                        "continuity_fit": 60,
                        "composition_quality": 75,
                        "visual_quality": 80,
                        "misrepresentation_risk": 5,
                        "reason": "thumb batch",
                    }
                    for i, cid in enumerate(ids)
                ]
            }
        )

    calls: list[str] = []

    def download_callable(proj, candidate, *, gap_id: str) -> Path:
        from otio_app.services.without_voiceover_enhanced.paths import (
            stock_candidate_download_dir,
        )

        calls.append(candidate.candidate_id)
        download_log.parent.mkdir(parents=True, exist_ok=True)
        download_log.write_text(json.dumps(calls), encoding="utf-8")
        d = stock_candidate_download_dir(
            proj, gap_id=gap_id, candidate_id=candidate.candidate_id
        )
        d.mkdir(parents=True, exist_ok=True)
        path = d / f"{candidate.candidate_id}.jpg"
        if len(calls) == 1:
            path.write_bytes(b"broken-rank1")
        else:
            path.write_bytes(_jpeg((30, 120, 40)))
        return path

    original = funnel_svc.run_supplement_funnel_for_gaps

    def wrapped(proj, **kwargs):
        kwargs.setdefault("text_llm", text_llm)
        kwargs.setdefault("vision_llm", vision_llm)
        kwargs.setdefault(
            "preview_fetch",
            lambda url, provider: SafeFetchResult(
                url=url,
                content=_jpeg(),
                content_type="image/jpeg",
                final_url=url,
            ),
        )
        kwargs.setdefault("download_callable", download_callable)
        kwargs.setdefault(
            "full_review_llm",
            lambda **kw: {
                "decision": "review_ready",
                "status": "PASS",
                "score": 0.92,
                "reason": "ok",
                "description": "canyon road",
                "candidate_id": kw.get("media_name"),
            },
        )
        kwargs.setdefault("force_restart", True)
        return original(proj, **kwargs)

    funnel_svc.run_supplement_funnel_for_gaps = wrapped
    cut_plan_tab.run_supplement_funnel_for_gaps = wrapped
    cut_plan_tab.get_enhanced_project = lambda: project
    _ = candidates


def _handle_smoke_actions(project: Project) -> None:
    """Query-Param-Aktionen für stabilen Playwright-Smoke (echte Streamlit-Runs)."""
    action = str(st.query_params.get("smoke_action", "") or "").strip()
    if not action:
        return
    if action == "run_funnel":
        report = cut_plan_tab.run_supplement_funnel_for_gaps(
            project, force_restart=True
        )
        st.success(f"SMOKE_FUNNEL_DONE {report.message}")
    elif action == "strip_license":
        from otio_app.services.without_voiceover_enhanced.io_utils import load_model
        from otio_app.services.without_voiceover_enhanced.models import (
            StockSearchResultsDocument,
        )

        results = load_model(
            stock_search_results_path(project), StockSearchResultsDocument
        )
        if results is not None:
            for cand in results.candidates:
                if cand.candidate_id == "pexels_photo_002":
                    cand.license = ""
                    cand.source_page = ""
            write_json(stock_search_results_path(project), results)
        st.warning("SMOKE_LICENSE_STRIPPED")
    elif action == "restore_license":
        from otio_app.services.without_voiceover_enhanced.io_utils import load_model
        from otio_app.services.without_voiceover_enhanced.models import (
            StockSearchResultsDocument,
        )

        results = load_model(
            stock_search_results_path(project), StockSearchResultsDocument
        )
        if results is not None:
            for cand in results.candidates:
                if cand.candidate_id == "pexels_photo_002":
                    cand.license = "Pexels License"
                    cand.source_page = "https://www.pexels.com/photo/2/"
            write_json(stock_search_results_path(project), results)
        st.success("SMOKE_LICENSE_RESTORED")
    elif action == "confirm":
        confirmed = cut_plan_tab.confirm_funnel_candidate(
            project, gap_id="gap_1", candidate_id="pexels_photo_002"
        )
        st.success(
            f"SMOKE_CONFIRM {confirmed.candidate_id}={confirmed.media_validation_status}"
        )
    elif action == "export_otio":
        from otio_app.services.without_voiceover_enhanced.paths import (
            accepted_supplements_path,
        )
        from otio_app.services.without_voiceover_enhanced.io_utils import load_model
        from otio_app.services.without_voiceover_enhanced.models import (
            AcceptedSupplementsDocument,
        )

        accepted = load_model(
            accepted_supplements_path(project), AcceptedSupplementsDocument
        )
        if accepted is None or not accepted.supplements:
            st.error("SMOKE_OTIO_FAIL no accepted")
        else:
            asset_id = accepted.supplements[0].candidate_id
            write_json(
                resolved_timeline_path(project),
                ResolvedTimelineDocument(
                    script_version="script-v1",
                    fps=25.0,
                    total_duration_seconds=4.0,
                    shots=[
                        ResolvedShot(
                            shot_id="shot_001",
                            asset_id=asset_id,
                            timeline_start_seconds=0.0,
                            timeline_end_seconds=4.0,
                            source_start_seconds=0.0,
                            source_end_seconds=4.0,
                        )
                    ],
                    audio_segments=[],
                ),
            )
            otio_path = export_otio_from_resolved_timeline(
                project, basename="funnel_r1_smoke"
            )
            st.success(f"SMOKE_OTIO {otio_path}")
            st.code(otio_path.read_text(encoding="utf-8")[:2000])


def main() -> None:
    st.set_page_config(page_title="Funnel R1 Smoke", layout="wide")
    project = _ensure_project()
    _install_fakes(project)
    st.sidebar.success(f"Smoke-Projekt: {project.name}")
    st.sidebar.caption(str(project.work_dir_path))
    # Marker for screenshot assertions
    st.markdown("<!-- FUNNEL_R1_SMOKE_MARKER -->")
    st.caption("R1 Smoke: Enhanced Cut Plan mit Fake-Provider/LLM")
    _handle_smoke_actions(project)
    cut_plan_tab.render_enhanced_cut_plan_page()

    st.divider()
    st.subheader("R1 Smoke-Hilfen")
    cols = st.columns(3)
    with cols[0]:
        if st.button("Lizenzmetadaten entfernen (Gate)", key="r1_strip_license"):
            from otio_app.services.without_voiceover_enhanced.io_utils import load_model
            from otio_app.services.without_voiceover_enhanced.models import (
                StockSearchResultsDocument,
            )

            results = load_model(
                stock_search_results_path(project), StockSearchResultsDocument
            )
            if results is not None:
                for cand in results.candidates:
                    if cand.candidate_id == "pexels_photo_002":
                        cand.license = ""
                        cand.source_page = ""
                write_json(stock_search_results_path(project), results)
                st.warning("Lizenzmetadaten für pexels_photo_002 entfernt.")
                st.rerun()
    with cols[1]:
        if st.button("Lizenzmetadaten wiederherstellen", key="r1_restore_license"):
            from otio_app.services.without_voiceover_enhanced.io_utils import load_model
            from otio_app.services.without_voiceover_enhanced.models import (
                StockSearchResultsDocument,
            )

            results = load_model(
                stock_search_results_path(project), StockSearchResultsDocument
            )
            if results is not None:
                for cand in results.candidates:
                    if cand.candidate_id == "pexels_photo_002":
                        cand.license = "Pexels License"
                        cand.source_page = "https://www.pexels.com/photo/2/"
                write_json(stock_search_results_path(project), results)
                st.success("Lizenzmetadaten wiederhergestellt.")
                st.rerun()
    with cols[2]:
        if st.button("OTIO aus Funnel-Supplement erzeugen", key="r1_otio_export"):
            from otio_app.services.without_voiceover_enhanced.paths import (
                accepted_supplements_path,
            )
            from otio_app.services.without_voiceover_enhanced.io_utils import load_model
            from otio_app.services.without_voiceover_enhanced.models import (
                AcceptedSupplementsDocument,
            )

            accepted = load_model(
                accepted_supplements_path(project), AcceptedSupplementsDocument
            )
            if accepted is None or not accepted.supplements:
                st.error("Kein akzeptiertes Supplement.")
            else:
                asset_id = accepted.supplements[0].candidate_id
                write_json(
                    resolved_timeline_path(project),
                    ResolvedTimelineDocument(
                        script_version="script-v1",
                        fps=25.0,
                        total_duration_seconds=4.0,
                        shots=[
                            ResolvedShot(
                                shot_id="shot_001",
                                asset_id=asset_id,
                                timeline_start_seconds=0.0,
                                timeline_end_seconds=4.0,
                                source_start_seconds=0.0,
                                source_end_seconds=4.0,
                            )
                        ],
                        audio_segments=[],
                    ),
                )
                try:
                    otio_path = export_otio_from_resolved_timeline(
                        project, basename="funnel_r1_smoke"
                    )
                    st.success(f"OTIO: {otio_path}")
                    st.code(otio_path.read_text(encoding="utf-8")[:2000])
                except Exception as exc:  # noqa: BLE001
                    st.error(str(exc))


if __name__ == "__main__":
    main()
