"""Word-Timestamps aus ElevenLabs Character-Alignments für Unified-Cut-Prompts."""

from __future__ import annotations

import json
from pathlib import Path

from otio_app.defaults import DEFAULT_ENHANCED_WORK_SUBDIR
from otio_app.models import Project, ProjectMode
from otio_app.services.without_voiceover_enhanced.models import (
    SegmentAlignment,
    SegmentAlignmentsDocument,
    SentenceTiming,
)
from otio_app.services.without_voiceover_enhanced.paths import (
    segment_alignments_path,
    segment_timestamps_path,
)
from otio_app.services.without_voiceover_enhanced.sentence_timing_prompt import (
    attach_words_to_sentence_row,
    build_sentence_timings_json_for_segments,
    words_from_elevenlabs_alignment,
)


def test_words_from_elevenlabs_alignment_splits_on_whitespace() -> None:
    alignment = {
        "characters": list("Hi waterfall now"),
        "character_start_times_seconds": [
            0.0,
            0.1,
            0.2,
            0.3,
            0.4,
            0.5,
            0.6,
            0.7,
            0.8,
            0.9,
            1.0,
            1.1,
            1.2,
            1.3,
            1.4,
            1.5,
        ],
        "character_end_times_seconds": [
            0.1,
            0.2,
            0.3,
            0.4,
            0.5,
            0.6,
            0.7,
            0.8,
            0.9,
            1.0,
            1.1,
            1.2,
            1.3,
            1.4,
            1.5,
            1.6,
        ],
    }
    words = words_from_elevenlabs_alignment(alignment)
    assert [w["text"] for w in words] == ["Hi", "waterfall", "now"]
    assert words[0]["start_seconds"] == 0.0
    assert words[1]["start_seconds"] == 0.3
    assert words[1]["end_seconds"] == 1.2
    assert words[2]["start_seconds"] == 1.3


def test_attach_words_to_sentence_row_offsets_from_sentence_start() -> None:
    row = {
        "sentence_id": "seg_001__s001",
        "segment_id": "seg_001",
        "text": "A roaring waterfall.",
        "start_seconds": 2.0,
        "end_seconds": 5.0,
    }
    words = [
        {"text": "A", "start_seconds": 2.0, "end_seconds": 2.1},
        {"text": "roaring", "start_seconds": 2.2, "end_seconds": 2.7},
        {"text": "waterfall", "start_seconds": 2.8, "end_seconds": 3.5},
        {"text": "next", "start_seconds": 5.5, "end_seconds": 5.8},
    ]
    out = attach_words_to_sentence_row(row, words)
    assert [w["text"] for w in out["words"]] == ["A", "roaring", "waterfall"]
    assert out["words"][2]["offset_seconds"] == 0.8


def test_build_sentence_timings_json_includes_words(tmp_path: Path) -> None:
    root = tmp_path / "proj"
    work = root / DEFAULT_ENHANCED_WORK_SUBDIR
    work.mkdir(parents=True)
    (root / "Yosemite").mkdir()
    project = Project(
        name="Words",
        project_root=str(root),
        work_dir=str(work),
        language="en",
        project_mode=ProjectMode.WITHOUT_VOICEOVER_ENHANCED,
        fps=25.0,
        frames_per_shot=3,
        selected_asset_subdirs=["Yosemite"],
        asset_subdir_names=["Yosemite"],
    )
    seg_id = "Yosemite_segment_001"
    alignments = SegmentAlignmentsDocument(
        script_version="v1",
        segments=[
            SegmentAlignment(
                segment_id=seg_id,
                script_version="v1",
                audio_path="audio/Yosemite_segment_001.mp3",
                audio_duration_seconds=2.0,
                tts_text="A roaring waterfall.",
                timestamps_path=f"audio/alignments/{seg_id}/elevenlabs_timestamps.json",
                sentences=[
                    SentenceTiming(
                        sentence_id=f"{seg_id}__s001",
                        segment_id=seg_id,
                        text="A roaring waterfall.",
                        start_seconds=0.0,
                        end_seconds=2.0,
                        duration_seconds=2.0,
                    )
                ],
            )
        ],
    )
    alignments_path = segment_alignments_path(project)
    alignments_path.parent.mkdir(parents=True, exist_ok=True)
    alignments_path.write_text(
        alignments.model_dump_json(indent=2), encoding="utf-8"
    )

    ts_path = segment_timestamps_path(project, seg_id)
    ts_path.parent.mkdir(parents=True, exist_ok=True)
    chars = list("A roaring waterfall")
    starts = [i * 0.1 for i in range(len(chars))]
    ends = [s + 0.09 for s in starts]
    ts_path.write_text(
        json.dumps(
            {
                "alignment": {
                    "characters": chars,
                    "character_start_times_seconds": starts,
                    "character_end_times_seconds": ends,
                }
            }
        ),
        encoding="utf-8",
    )

    payload = json.loads(
        build_sentence_timings_json_for_segments(
            project, segment_ids=[seg_id], include_words=True
        )
    )
    assert len(payload) == 1
    words = payload[0]["words"]
    assert any(w["text"] == "waterfall" for w in words)
    waterfall = next(w for w in words if w["text"] == "waterfall")
    assert "offset_seconds" in waterfall
    assert waterfall["offset_seconds"] >= 0.0

    bare = json.loads(
        build_sentence_timings_json_for_segments(
            project, segment_ids=[seg_id], include_words=False
        )
    )
    assert "words" not in bare[0]
