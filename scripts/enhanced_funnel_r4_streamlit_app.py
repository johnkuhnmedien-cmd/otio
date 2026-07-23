#!/usr/bin/env python3
"""Streamlit-App für R4-UI-Smoke: Multiselect + provider-balancierter Pool.

Keine smoke_action-Query-Parameter für Auswahl oder Funnel-Start.
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
        "FUNNEL_R4_SMOKE_ROOT",
        "/opt/cursor/artifacts/funnel-r4-button-evidence/project",
    )
)

from otio_app.defaults import DEFAULT_ENHANCED_WORK_SUBDIR
from otio_app.models import Project, ProjectMode
from otio_app.services.without_voiceover_enhanced.io_utils import write_json
from otio_app.services.without_voiceover_enhanced.models import (
    CoverageGap,
    CoverageGapsDocument,
    EnhancedScriptDocument,
    ScriptSegment,
    StockCandidate,
    StockSearchResultsDocument,
)
from otio_app.services.without_voiceover_enhanced.paths import (
    coverage_gaps_path,
    script_locked_path,
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
import otio_app.ui.without_voiceover_enhanced.cut_plan_tab as cut_plan_tab
import otio_app.services.without_voiceover_enhanced.supplement_funnel_service as funnel_svc


def _jpeg(color=(20, 40, 60)) -> bytes:
    buf = BytesIO()
    Image.new("RGB", (64, 64), color=color).save(buf, format="JPEG")
    return buf.getvalue()


def _progress_log_path() -> Path:
    return Path(
        os.environ.get(
            "FUNNEL_R4_PROGRESS_LOG",
            "/opt/cursor/artifacts/funnel-r4-button-evidence/progress_log.json",
        )
    )


def _provider_urls(provider: str, gap_id: str, i: int) -> tuple[str, str, str]:
    if provider == "pexels":
        return (
            f"https://www.pexels.com/photo/{gap_id}-{i}/",
            f"https://images.pexels.com/photos/{gap_id}-{i}/p.jpg",
            f"https://images.pexels.com/photos/{gap_id}-{i}/f.jpg",
        )
    if provider == "pixabay":
        return (
            f"https://pixabay.com/photos/{gap_id}-{i}/",
            f"https://cdn.pixabay.com/photo/{gap_id}-{i}/p.jpg",
            f"https://cdn.pixabay.com/photo/{gap_id}-{i}/f.jpg",
        )
    if provider == "wikimedia":
        return (
            f"https://commons.wikimedia.org/wiki/File:{gap_id}_{i}.jpg",
            (
                "https://upload.wikimedia.org/wikipedia/commons/thumb/a/a1/"
                f"{gap_id}_{i}.jpg/320px-{gap_id}_{i}.jpg"
            ),
            f"https://upload.wikimedia.org/wikipedia/commons/a/a1/{gap_id}_{i}.jpg",
        )
    # openverse
    return (
        f"https://openverse.org/image/{gap_id}-{i}/",
        f"https://api.openverse.org/v1/images/{gap_id}-{i}/thumb.jpg",
        f"https://api.openverse.org/v1/images/{gap_id}-{i}/full.jpg",
    )


def _ensure_project() -> Project:
    root = SMOKE_ROOT
    work = root / DEFAULT_ENHANCED_WORK_SUBDIR
    work.mkdir(parents=True, exist_ok=True)
    (root / "Canyon").mkdir(exist_ok=True)
    # Stabile Project-ID: Widget-Keys (z. B. Gap-Auswahl) dürfen zwischen
    # Streamlit-Reruns nicht wechseln — sonst bleibt die Auswahl wirkungslos.
    project = Project(
        id="funnel-r4-smoke",
        name="FunnelR4Smoke",
        project_root=str(root),
        work_dir=str(work),
        language="de",
        project_mode=ProjectMode.WITHOUT_VOICEOVER_ENHANCED,
        fps=25.0,
        frames_per_shot=3,
        selected_asset_subdirs=["Canyon"],
        asset_subdir_names=["Canyon"],
    )

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
                "pixabay": True,
                "wikimedia": True,
                "openverse": True,
                "archive_org": False,
            },
        )
        write_json(
            coverage_gaps_path(project),
            CoverageGapsDocument(
                script_version="script-v1",
                gaps=[
                    CoverageGap(
                        gap_id="gap_1",
                        needed_visual="canyon road",
                        preferred_media_type="photo",
                    ),
                    CoverageGap(
                        gap_id="gap_2",
                        needed_visual="river bend",
                        preferred_media_type="photo",
                    ),
                    CoverageGap(
                        gap_id="gap_3",
                        needed_visual="pine forest",
                        preferred_media_type="photo",
                    ),
                ],
            ),
        )
        # Pro Gap: Pexels 8, Pixabay 8, Wikimedia 2, Openverse 8
        counts = {
            "pexels": 8,
            "pixabay": 8,
            "wikimedia": 2,
            "openverse": 8,
        }
        candidates: list[StockCandidate] = []
        for gap_id in ("gap_1", "gap_2", "gap_3"):
            for provider, n in counts.items():
                for i in range(1, n + 1):
                    source, preview, download = _provider_urls(provider, gap_id, i)
                    candidates.append(
                        StockCandidate(
                            candidate_id=f"{provider}_{gap_id}_{i:03d}",
                            provider=provider,
                            provider_asset_id=f"{gap_id}-{provider}-{i}",
                            title=f"{gap_id} {provider} still {i}",
                            media_type="photo",
                            creator="Smoke",
                            source_page=source,
                            preview_url=preview,
                            download_url=download,
                            width=1920,
                            height=1080,
                            license=f"{provider} License",
                            attribution="Smoke",
                            gap_id=gap_id,
                        )
                    )
        write_json(
            stock_search_results_path(project),
            StockSearchResultsDocument(
                script_version="script-v1",
                provider_status={
                    "pexels": "completed",
                    "pixabay": "completed",
                    "wikimedia": "completed",
                    "openverse": "completed",
                    "archive_org": "disabled",
                },
                candidates=candidates,
            ),
        )
    return project


def _install_fakes(project: Project) -> None:
    download_log = Path(
        os.environ.get(
            "FUNNEL_R4_DOWNLOAD_LOG",
            "/opt/cursor/artifacts/funnel-r4-button-evidence/download_calls.json",
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
            ids = [c["candidate_id"] for c in cands if c.get("gap_id") == gap_id][:20]
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
        funnel_svc, "_r4_smoke_original_run", funnel_svc.run_supplement_funnel_for_gaps
    )
    funnel_svc._r4_smoke_original_run = original

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
        # Markiere Laufgrenzen für Evidence
        marker_start = f"__RUN_START__ gap_ids={kwargs.get('gap_ids')}"
        marker_end = f"__RUN_END__ filled={list(report.filled_gap_ids)} requested={list(report.requested_gap_ids)}"
        log_path.write_text(
            json.dumps(
                existing + [marker_start] + progress_lines + [marker_end],
                indent=2,
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        return report

    funnel_svc.run_supplement_funnel_for_gaps = wrapped
    cut_plan_tab.run_supplement_funnel_for_gaps = wrapped
    cut_plan_tab.get_enhanced_project = lambda: project


def main() -> None:
    st.set_page_config(page_title="Funnel R4 Smoke", layout="wide")
    project = _ensure_project()
    _install_fakes(project)
    st.sidebar.success(f"Smoke-Projekt: {project.name}")
    st.caption("R4 Smoke: Multiselect + Provider-Balancing — echte Button-Klicks")
    cut_plan_tab.render_enhanced_cut_plan_page()


if __name__ == "__main__":
    main()
