#!/usr/bin/env python3
"""Streamlit-App für R3-UI-Smoke: Gap-Auswahl + nonblocking Lizenzmetadaten."""

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
        "FUNNEL_R3_SMOKE_ROOT",
        "/opt/cursor/artifacts/funnel-r3-ui/project",
    )
)

from otio_app.defaults import DEFAULT_ENHANCED_WORK_SUBDIR
from otio_app.models import Project, ProjectMode
from otio_app.services.without_voiceover_enhanced.io_utils import load_model, write_json
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
    accepted_supplements_path,
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
from otio_app.services.without_voiceover_enhanced.supplement_funnel_service import (
    list_open_funnel_gap_ids,
)
import otio_app.ui.without_voiceover_enhanced.cut_plan_tab as cut_plan_tab
import otio_app.services.without_voiceover_enhanced.supplement_funnel_service as funnel_svc


def _jpeg(color=(20, 40, 60)) -> bytes:
    buf = BytesIO()
    Image.new("RGB", (64, 64), color=color).save(buf, format="JPEG")
    return buf.getvalue()


def _progress_log_path() -> Path:
    return Path(
        os.environ.get(
            "FUNNEL_R3_PROGRESS_LOG",
            "/opt/cursor/artifacts/funnel-r3-ui/progress_log.json",
        )
    )


def _ensure_project() -> Project:
    root = SMOKE_ROOT
    work = root / DEFAULT_ENHANCED_WORK_SUBDIR
    work.mkdir(parents=True, exist_ok=True)
    (root / "Canyon").mkdir(exist_ok=True)
    project = Project(
        name="FunnelR3Smoke",
        project_root=str(root),
        work_dir=str(work),
        language="de",
        project_mode=ProjectMode.WITHOUT_VOICEOVER_ENHANCED,
        fps=25.0,
        frames_per_shot=3,
        selected_asset_subdirs=["Canyon"],
        asset_subdir_names=["Canyon"],
    )
    from otio_app.services.without_voiceover_enhanced.paths import script_locked_path

    if not script_locked_path(project).is_file():
        save_script_draft(
            project,
            EnhancedScriptDocument(
                narration_full="Zion canyon road river forest.",
                segments=[
                    ScriptSegment(
                        segment_id="Canyon_segment_001",
                        text="Zion canyon road river forest.",
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
                gaps=[
                    CoverageGap(gap_id="gap_1", needed_visual="canyon road"),
                    CoverageGap(gap_id="gap_2", needed_visual="river bend"),
                    CoverageGap(gap_id="gap_3", needed_visual="pine forest"),
                ],
            ),
        )
        candidates = []
        for gap_id in ("gap_1", "gap_2", "gap_3"):
            for i in range(1, 21):
                # gap_2 ohne Lizenzmetadaten (missing, nonblocking)
                no_license = gap_id == "gap_2"
                candidates.append(
                    StockCandidate(
                        candidate_id=f"pexels_{gap_id}_{i:03d}",
                        provider="pexels",
                        provider_asset_id=f"{gap_id}-{i}",
                        title=f"{gap_id} still {i}",
                        media_type="photo",
                        creator="" if no_license else "Smoke",
                        source_page="" if no_license else f"https://www.pexels.com/photo/{gap_id}-{i}/",
                        preview_url=f"https://images.pexels.com/photos/{gap_id}-{i}/p.jpg",
                        download_url=f"https://images.pexels.com/photos/{gap_id}-{i}/f.jpg",
                        width=1920,
                        height=1080,
                        license="" if no_license else "Pexels License",
                        attribution="" if no_license else "Smoke",
                        gap_id=gap_id,
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
    download_log = Path(
        os.environ.get(
            "FUNNEL_R3_DOWNLOAD_LOG",
            "/opt/cursor/artifacts/funnel-r3-ui/download_calls.json",
        )
    )

    def text_llm(prompt: str) -> str:
        import re

        m = re.search(r"Coverage Gap:\s*([A-Za-z0-9_\-]+)", prompt)
        gap_id = m.group(1) if m else "gap_1"
        cands = json.loads(
            stock_search_results_path(project).read_text(encoding="utf-8")
        )["candidates"]
        ids = [
            c["candidate_id"]
            for c in cands
            if c.get("gap_id") == gap_id and c["candidate_id"] in prompt
        ]
        if not ids:
            ids = [c["candidate_id"] for c in cands if c.get("gap_id") == gap_id]
        return json.dumps(
            {
                "gap_id": gap_id,
                "candidate_reviews": [
                    {
                        "candidate_id": cid,
                        "text_relevance": 90 - (i % 8),
                        "metadata_quality": 80,
                        "media_type_fit": 85,
                        "license_metadata_quality": 90,
                        "misrepresentation_risk": 5,
                        "reason": "text ok",
                    }
                    for i, cid in enumerate(ids)
                ],
            }
        )

    def vision_llm(prompt: str, images):
        import re

        ids = [label for label, _ in images]
        m = re.search(r"gap_id[=:]?\s*[\"']?([A-Za-z0-9_\-]+)", prompt)
        if not m:
            m = re.search(r"Coverage Gap:\s*([A-Za-z0-9_\-]+)", prompt)
        gap_id = m.group(1) if m else "gap_1"
        if "Finalisten" in prompt:
            return json.dumps(
                {
                    "gap_id": gap_id,
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

    def download_callable(proj, candidate, *, gap_id: str) -> Path:
        from otio_app.services.without_voiceover_enhanced.paths import (
            stock_candidate_download_dir,
        )

        download_log.parent.mkdir(parents=True, exist_ok=True)
        existing: list[dict] = []
        if download_log.is_file():
            try:
                existing = json.loads(download_log.read_text(encoding="utf-8"))
            except Exception:
                existing = []
        existing.append({"gap_id": gap_id, "candidate_id": candidate.candidate_id})
        download_log.write_text(json.dumps(existing), encoding="utf-8")
        d = stock_candidate_download_dir(
            proj, gap_id=gap_id, candidate_id=candidate.candidate_id
        )
        d.mkdir(parents=True, exist_ok=True)
        path = d / f"{candidate.candidate_id}.jpg"
        path.write_bytes(_jpeg((30, 120, 40)))
        return path

    original = getattr(
        funnel_svc, "_r3_smoke_original_run", funnel_svc.run_supplement_funnel_for_gaps
    )
    funnel_svc._r3_smoke_original_run = original

    def wrapped(proj, **kwargs):
        progress_lines: list[str] = []
        user_cb = kwargs.get("progress_callback")

        def _cb(event):
            progress_lines.append(event.message or event.phase)
            if user_cb is not None:
                user_cb(event)

        kwargs["progress_callback"] = _cb
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
        report = original(proj, **kwargs)
        log_path = _progress_log_path()
        log_path.parent.mkdir(parents=True, exist_ok=True)
        existing = []
        if log_path.is_file():
            try:
                existing = json.loads(log_path.read_text(encoding="utf-8"))
            except Exception:
                existing = []
        log_path.write_text(
            json.dumps(existing + progress_lines, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        return report

    funnel_svc.run_supplement_funnel_for_gaps = wrapped
    cut_plan_tab.run_supplement_funnel_for_gaps = wrapped
    cut_plan_tab.get_enhanced_project = lambda: project


def _handle_smoke_actions(project: Project) -> None:
    action = str(st.query_params.get("smoke_action", "") or "").strip()
    if not action:
        return
    if action == "run_selected":
        report = cut_plan_tab.run_supplement_funnel_for_gaps(
            project,
            gap_ids=["gap_1", "gap_3"],
            force_restart=True,
        )
        st.success(f"SMOKE_SELECTED_DONE {report.message}")
        st.info(f"requested={report.requested_gap_ids} filled={report.filled_gap_ids}")
        open_ids = list_open_funnel_gap_ids(project)
        st.warning(f"SMOKE_OPEN_AFTER_SELECTED {open_ids}")
    elif action == "run_all_open":
        open_ids = list_open_funnel_gap_ids(project)
        report = cut_plan_tab.run_supplement_funnel_for_gaps(
            project,
            gap_ids=open_ids,
            skip_filled=True,
            force_restart=False,
        )
        st.success(f"SMOKE_ALL_OPEN_DONE {report.message}")
        st.info(f"requested={report.requested_gap_ids} filled={report.filled_gap_ids}")
        # Nachweis missing license auf gap_2
        for gap_rep in report.gaps:
            if gap_rep.gap_id == "gap_2":
                st.success(
                    f"SMOKE_LICENSE_STATUS gap_2={gap_rep.license_metadata_status}"
                )
        remaining = list_open_funnel_gap_ids(project)
        st.success(f"SMOKE_ALL_FILLED open={remaining}")
    elif action == "export_otio":
        from otio_app.services.without_voiceover_enhanced.models import (
            AcceptedSupplementsDocument,
        )

        accepted = load_model(
            accepted_supplements_path(project), AcceptedSupplementsDocument
        )
        if accepted is None or not accepted.supplements:
            st.error("SMOKE_OTIO_FAIL no accepted")
            return
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
            project, basename="funnel_r3_smoke"
        )
        st.success(f"SMOKE_OTIO {otio_path}")
        st.code(otio_path.read_text(encoding="utf-8")[:2000])


def main() -> None:
    st.set_page_config(page_title="Funnel R3 Smoke", layout="wide")
    project = _ensure_project()
    _install_fakes(project)
    st.sidebar.success(f"Smoke-Projekt: {project.name}")
    st.caption("R3 Smoke: Gap-Auswahl + Lizenz nonblocking")
    _handle_smoke_actions(project)
    cut_plan_tab.render_enhanced_cut_plan_page()


if __name__ == "__main__":
    main()
