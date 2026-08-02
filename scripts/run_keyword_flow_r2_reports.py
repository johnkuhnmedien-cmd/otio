#!/usr/bin/env python3
"""R2 reports from real resolve_unified_timeline + production OTIO export."""

from __future__ import annotations

import json
import shutil
import tempfile
from pathlib import Path

import opentimelineio as otio

from tests.test_keyword_flow_e2e_production import (
    _build_chapter_a_project,
    _plan_with_pause_and_closing,
)
from otio_app.services.without_voiceover_enhanced.otio_export_service import (
    export_otio_from_resolved_timeline,
)
from otio_app.services.without_voiceover_enhanced.unified_timeline_service import (
    resolve_unified_timeline,
)

ART = Path("/opt/cursor/artifacts")


def main() -> None:
    tmp = Path(tempfile.mkdtemp(prefix="kf_r2_prod_"))
    try:
        project, ids = _build_chapter_a_project(tmp)
        plan = _plan_with_pause_and_closing(ids)
        resolved = resolve_unified_timeline(
            project, plan, allow_open_gaps=False, persist=True
        )
        out = export_otio_from_resolved_timeline(
            project, basename="keyword_flow_r2_production", resolved=resolved
        )
        tl = otio.adapters.read_from_file(str(out))
        audio = next(t for t in tl.tracks if t.kind == otio.schema.TrackKind.Audio)
        video = next(t for t in tl.tracks if t.kind == otio.schema.TrackKind.Video)
        audio_gaps = [
            round(c.duration().to_seconds(), 3)
            for c in audio
            if isinstance(c, otio.schema.Gap)
        ]
        video_clips = [
            {
                "name": c.name,
                "duration": round(c.duration().to_seconds(), 3),
            }
            for c in video
            if isinstance(c, otio.schema.Clip)
        ]
        pause_after = [
            {
                "segment_id": a.segment_id,
                "pause_after_seconds": a.pause_after_seconds,
                "source_start_seconds": a.source_start_seconds,
                "source_end_seconds": a.source_end_seconds,
                "timeline_start_seconds": a.timeline_start_seconds,
                "timeline_end_seconds": a.timeline_end_seconds,
                "split_label": a.split_label,
            }
            for a in resolved.audio_segments
        ]
        map_shots = [
            {
                "shot_id": s.shot_id,
                "editorial_function": s.editorial_function,
                "duration": round(
                    float(s.timeline_end_seconds) - float(s.timeline_start_seconds), 3
                ),
                "path": s.resolved_media_path,
            }
            for s in resolved.shots
            if str(s.editorial_function or "") == "technical_chapter_map_opener"
        ]
        report = {
            "source": "resolve_unified_timeline + export_otio_from_resolved_timeline",
            "otio_path": str(out),
            "errors": list(resolved.errors),
            "repairs": list(resolved.repairs),
            "audio_pause_segments": pause_after,
            "otio_audio_gap_durations": audio_gaps,
            "otio_video_clips": video_clips,
            "map_shots": map_shots,
            "closing_fallback_asset_id": plan.closing_fallback_asset_id,
            "synthetic_timeline": False,
        }
        (ART / "keyword-flow-r2-timeline-report.json").write_text(
            json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        (ART / "keyword-flow-r2-pause-report.json").write_text(
            json.dumps(
                {
                    "pause_after_segments": pause_after,
                    "otio_audio_gaps": audio_gaps,
                    "source_audio_mutated": False,
                },
                indent=2,
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        (ART / "keyword-flow-r2-map-report.json").write_text(
            json.dumps({"map_shots": map_shots, "repairs": resolved.repairs}, indent=2),
            encoding="utf-8",
        )
        # Copy OTIO into artifacts test project for download.
        dest_root = ART / "keyword-flow-r2-test-project"
        if dest_root.exists():
            shutil.rmtree(dest_root)
        shutil.copytree(project.project_root_path, dest_root)
        dest_otio = dest_root / "_otio_enhanced" / "exports"
        dest_otio.mkdir(parents=True, exist_ok=True)
        shutil.copy2(out, dest_otio / out.name)
        (ART / "keyword-flow-r2-resolve-smoke.txt").write_text(
            f"R2 production OTIO: {dest_otio / out.name}\n"
            f"errors={resolved.errors}\n"
            f"audio_gaps={audio_gaps}\n"
            f"map_shots={map_shots}\n"
            "Resolve CLI: not available in this environment — OTIO produced via "
            "export_otio_from_resolved_timeline and re-read with OTIO.\n",
            encoding="utf-8",
        )
        print(json.dumps(report, indent=2, ensure_ascii=False))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    main()
