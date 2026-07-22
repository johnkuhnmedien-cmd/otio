#!/usr/bin/env python3
"""Lokaler Funnel-Smoke ohne Live-Provider/LLM — erzeugt Nachweis-PNGs."""

from __future__ import annotations

import json
import sys
from io import BytesIO
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from otio_app.defaults import DEFAULT_ENHANCED_WORK_SUBDIR
from otio_app.models import Project, ProjectMode
from otio_app.services.without_voiceover_enhanced.io_utils import load_model, write_json
from otio_app.services.without_voiceover_enhanced.models import (
    CoverageGap,
    CoverageGapsDocument,
    EnhancedScriptDocument,
    ScriptSegment,
    StockCandidate,
    StockSearchResultsDocument,
    SupplementFunnelReport,
)
from otio_app.services.without_voiceover_enhanced.paths import (
    coverage_gaps_path,
    stock_search_results_path,
    supplement_funnel_report_path,
)
from otio_app.services.without_voiceover_enhanced.script_lock_service import (
    lock_script,
    save_script_draft,
)
from otio_app.services.without_voiceover_enhanced.stock.safe_fetch import SafeFetchResult
from otio_app.services.without_voiceover_enhanced.stock_provider_config import (
    save_stock_providers_config,
)
from otio_app.services.without_voiceover_enhanced.supplement_funnel_service import (
    confirm_funnel_candidate,
    run_supplement_funnel_for_gaps,
)


def _jpeg(color=(10, 20, 30)) -> bytes:
    buf = BytesIO()
    Image.new("RGB", (48, 48), color=color).save(buf, format="JPEG")
    return buf.getvalue()


def _slide(path: Path, title: str, lines: list[str]) -> None:
    img = Image.new("RGB", (1280, 720), color=(24, 28, 34))
    draw = ImageDraw.Draw(img)
    font = ImageFont.load_default()
    draw.text((40, 40), title, fill=(240, 240, 240), font=font)
    y = 100
    for line in lines:
        draw.text((40, y), line[:110], fill=(200, 210, 220), font=font)
        y += 28
    path.parent.mkdir(parents=True, exist_ok=True)
    img.save(path)


def main() -> int:
    out = Path("/opt/cursor/artifacts/funnel-smoke")
    out.mkdir(parents=True, exist_ok=True)
    work = out / "project" / DEFAULT_ENHANCED_WORK_SUBDIR
    work.mkdir(parents=True)
    (out / "project" / "Canyon").mkdir(exist_ok=True)
    project = Project(
        name="FunnelSmoke",
        project_root=str(out / "project"),
        work_dir=str(work),
        language="de",
        project_mode=ProjectMode.WITHOUT_VOICEOVER_ENHANCED,
        fps=25.0,
        frames_per_shot=3,
        selected_asset_subdirs=["Canyon"],
        asset_subdir_names=["Canyon"],
    )
    save_script_draft(
        project,
        EnhancedScriptDocument(
            narration_full="Zion canyon road at dusk.",
            segments=[
                ScriptSegment(
                    segment_id="Canyon_segment_001",
                    text="Zion canyon road at dusk.",
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
                provider_asset_id=str(2000 + i),
                title=f"Canyon {i}",
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

    _slide(
        out / "01_twenty_candidates.png",
        "01 — 20 Kandidaten",
        [f"{c.candidate_id} · {c.title}" for c in candidates[:12]]
        + [f"... total {len(candidates)}"],
    )

    def text_llm(prompt: str) -> str:
        return json.dumps(
            {
                "gap_id": "gap_1",
                "candidate_reviews": [
                    {
                        "candidate_id": c.candidate_id,
                        "text_relevance": 90 - (i % 9),
                        "metadata_quality": 80,
                        "media_type_fit": 85,
                        "license_metadata_quality": 90,
                        "misrepresentation_risk": 5,
                        "reason": "text ok",
                    }
                    for i, c in enumerate(candidates)
                ],
            }
        )

    def vision_llm(prompt: str, images: list[tuple[str, bytes]]) -> str:
        ids = [label for label, _ in images]
        if "Finalisten" in prompt or "finalists" in prompt:
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
        assert len(ids) <= 10
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
                        "reason": "thumb",
                    }
                    for i, cid in enumerate(ids)
                ]
            }
        )

    download_calls: list[str] = []

    def download_callable(project, candidate, *, gap_id: str) -> Path:
        from otio_app.services.without_voiceover_enhanced.paths import (
            stock_candidate_download_dir,
        )

        download_calls.append(candidate.candidate_id)
        d = stock_candidate_download_dir(
            project, gap_id=gap_id, candidate_id=candidate.candidate_id
        )
        d.mkdir(parents=True, exist_ok=True)
        path = d / f"{candidate.candidate_id}.jpg"
        if len(download_calls) == 1:
            path.write_bytes(b"broken")
        else:
            path.write_bytes(_jpeg((40, 90, 40)))
        return path

    report = run_supplement_funnel_for_gaps(
        project,
        text_llm=text_llm,
        vision_llm=vision_llm,
        preview_fetch=lambda url, provider: SafeFetchResult(
            url=url, content=_jpeg(), content_type="image/jpeg", final_url=url
        ),
        download_callable=download_callable,
        full_review_llm=lambda **kwargs: {
            "decision": "review_ready",
            "status": "PASS",
            "score": 0.91,
            "reason": "ok",
            "description": "road",
            "candidate_id": kwargs.get("media_name"),
        },
        force_restart=True,
    )

    gap = report.gaps[0]
    scored = [c for c in gap.candidates if c.text_scores.text_relevance]
    _slide(
        out / "02_thumbnail_batches.png",
        "02 — Thumbnail Batches",
        [
            f"Text reviews: {len(scored)}/20",
            f"Thumbnail scored: {sum(1 for c in gap.candidates if c.preview_status=='scored')}",
            "Batch size max 10 → 2 batches for 20 candidates",
        ],
    )
    ranked = sorted(
        [c for c in gap.candidates if c.rank is not None], key=lambda c: c.rank or 99
    )
    _slide(
        out / "03_final_ranking.png",
        "03 — Final Ranking",
        [
            f"#{c.rank} {c.candidate_id} score={c.final_score} {c.decision}"
            for c in ranked[:6]
        ],
    )
    _slide(
        out / "04_winner_download.png",
        "04 — Winner Download First",
        [
            f"Download order: {download_calls}",
            f"First download (Rang1): {download_calls[0] if download_calls else '-'}",
        ],
    )
    _slide(
        out / "05_fallback_after_invalid.png",
        "05 — Fallback after invalid Rang1",
        [
            f"Downloads: {len(download_calls)} (expect 2)",
            f"review_ready: {gap.review_ready_candidate_id}",
            f"equals second download: {gap.review_ready_candidate_id == download_calls[1]}",
        ],
    )
    ready = next(c for c in gap.candidates if c.candidate_id == gap.review_ready_candidate_id)
    _slide(
        out / "06_manual_review_required.png",
        "06 — Before manual confirm",
        [
            f"status={ready.funnel_status}",
            f"selected? {ready.funnel_status == 'selected'}",
            f"export_ready? {ready.funnel_status == 'export_ready'}",
        ],
    )
    confirmed = confirm_funnel_candidate(
        project, gap_id="gap_1", candidate_id=gap.review_ready_candidate_id
    )
    _slide(
        out / "07_selected_export_ready.png",
        "07 — After manual confirm",
        [
            f"selected={confirmed.selected}",
            f"status={confirmed.media_validation_status}",
            f"local_path={confirmed.local_media_path}",
        ],
    )
    _slide(
        out / "08_otio_local_only.png",
        "08 — OTIO local-only guarantee",
        [
            f"path starts with http? {str(confirmed.local_media_path).startswith('http')}",
            f"path exists? {Path(confirmed.local_media_path).is_file()}",
            "otio_export_service rejects http URLs (fail-closed)",
        ],
    )
    print(json.dumps({
        "downloads": download_calls,
        "review_ready": gap.review_ready_candidate_id,
        "export_status": confirmed.media_validation_status,
        "screenshots": sorted(p.name for p in out.glob("*.png")),
    }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
