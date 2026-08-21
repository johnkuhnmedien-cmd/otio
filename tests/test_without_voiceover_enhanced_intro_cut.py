"""Intro Unified Cut: gebündeltes Inventar, strong-only, Intro-Hüllen."""

from __future__ import annotations

import json

import pytest

from otio_app.services.without_voiceover_enhanced.intro_cut_service import (
    INTRO_CLOSING_HOLD_DEFAULT_SEC,
    INTRO_CLOSING_HOLD_MAX_SEC,
    INTRO_OPENING_HOLD_SEC,
    _slim_intro_asset_row,
    clamp_intro_closing_hold,
    enforce_intro_strong_only,
    filter_resolved_timeline_to_intro,
    format_bundled_inventory_for_prompt,
    intro_envelope_asset_errors,
    merge_intro_and_body_plans,
    split_intro_from_unified,
)
from otio_app.services.without_voiceover_enhanced.models import (
    CutBoundary,
    CutSlot,
    PauseDirective,
    ResolvedAudioSegment,
    ResolvedChapterEnvelope,
    ResolvedShot,
    ResolvedTimelineDocument,
    UnifiedCutPlanDocument,
)
from otio_app.services.without_voiceover_enhanced.script_prompts import (
    CUT_ASSET_SELECTION_PROMPT_VERSION,
    build_intro_unified_cut_prompt,
)


def _slot(slot_id: str, fit: str, asset: str | None) -> CutSlot:
    return CutSlot(
        slot_id=slot_id,
        local_asset_id=asset,
        asset_fit=fit,  # type: ignore[arg-type]
        asset_fit_reason="test",
        visual_intent="valley",
        coverage_gap_id=None if fit == "strong" else f"gap_{slot_id}",
        needed_visual="valley" if fit != "strong" else "",
        search_concepts=["valley wide"] if fit != "strong" else [],
    )


def _bound(cut_id: str, sentence_id: str, position: str = "start") -> CutBoundary:
    return CutBoundary(
        cut_id=cut_id,
        sentence_id=sentence_id,
        position=position,  # type: ignore[arg-type]
        alignment="sentence_boundary",
    )


def test_format_bundled_inventory_for_prompt_drops_duplicate_and_trims() -> None:
    long_desc = "x" * 500
    bundled = {
        "schema_version": "enhanced-intro-bundled-inventory-v1",
        "chapter_count": 1,
        "asset_count": 1,
        "chapters": {
            "Yosemite": [
                {
                    "local_asset_id": "yo_01",
                    "asset_id": "yo_01",
                    "folder": "Yosemite",
                    "file": "a.mp4",
                    "media_type": "video",
                    "duration_seconds": 12.0,
                    "description": long_desc,
                    "motion": "pan",
                }
            ]
        },
        "all_assets": [
            {
                "local_asset_id": "yo_01",
                "description": long_desc,
            }
        ],
    }
    text = format_bundled_inventory_for_prompt(bundled)
    assert "all_assets" not in text
    assert "yo_01" in text
    assert long_desc not in text
    assert "..." in text
    assert text.count("yo_01") == 1


def test_slim_intro_asset_row_keeps_compact_v2_subset() -> None:
    row = {
        "local_asset_id": "asset_a",
        "asset_id": "asset_a",
        "folder": "Albarracín",
        "file": "a.mov",
        "media_type": "video",
        "duration_seconds": 24.8,
        "description": "Luftaufnahme einer historischen Bergstadt.",
        "tags": ["a", "b", "c", "d", "e"],
        "motion": "drone",
        "motion_intensity": 35,
        "motion_direction": "forward",
        "framing": "aerial",
        "shot_scale": "wide",
        "quality": {
            "technical": 82,
            "composition": 85,
            "appeal": 86,
            "clarity": 88,
            "hero": 85,
            "defect": 0,
        },
        "look": {
            "brightness": 65,
            "contrast": 70,
            "saturation": 60,
            "temperature": "warm",
            "colors": ["Ziegelrot", "Beige", "Rotbraun"],
        },
        "people": False,
        "analysis_confidence": 0.95,
    }
    out = _slim_intro_asset_row(row)
    assert out["local_asset_id"] == "asset_a"
    assert out["tags"] == ["a", "b", "c"]
    assert out["shot_scale"] == "wide"
    assert out["quality"] == {
        "technical": 82,
        "appeal": 86,
        "hero": 85,
        "defect": 0,
    }
    assert out["look"] == {"temperature": "warm", "colors": ["Ziegelrot", "Beige"]}
    assert "composition" not in out["quality"]
    assert "clarity" not in out["quality"]
    assert "brightness" not in out["look"]
    assert "contrast" not in out["look"]
    assert "saturation" not in out["look"]
    assert "motion_intensity" not in out
    assert "analysis_confidence" not in out
    assert "confidence" not in out
    assert "is_16_9" not in out


def test_slim_intro_photo_row_sets_is_16_9_from_filename() -> None:
    wide = _slim_intro_asset_row(
        {
            "local_asset_id": "photo_wide",
            "file": "cave_1920x1080.jpg",
            "media_type": "photo",
            "folder": "Cliffs",
        }
    )
    square = _slim_intro_asset_row(
        {
            "local_asset_id": "photo_sq",
            "file": "portrait.jpg",
            "media_type": "image",
            "folder": "Cliffs",
        }
    )
    four_three = _slim_intro_asset_row(
        {
            "local_asset_id": "photo_43",
            "file": "wall_1440x1080.jpg",
            "media_type": "photo",
            "folder": "Cliffs",
        }
    )
    assert wide["is_16_9"] is True
    assert square["is_16_9"] is False
    assert four_three["is_16_9"] is False


def test_intro_photo_is_16_9_probes_image_file(tmp_path) -> None:
    from PIL import Image

    from otio_app.services.without_voiceover_enhanced.intro_cut_service import (
        intro_photo_is_16_9,
    )

    wide = tmp_path / "hold.jpg"
    Image.new("RGB", (1920, 1080), color=(10, 20, 30)).save(wide, "JPEG")
    tall = tmp_path / "tower.jpg"
    Image.new("RGB", (800, 1200), color=(10, 20, 30)).save(tall, "JPEG")
    row = {"file": "hold.jpg", "media_type": "photo"}
    assert intro_photo_is_16_9(row, media_path=wide) is True
    assert intro_photo_is_16_9(row, media_path=tall) is False


def test_intro_bundled_prompt_stays_compact_for_many_chapters() -> None:
    chapters: dict[str, list[dict]] = {}
    all_assets: list[dict] = []
    for i in range(12):
        folder = f"Chapter_{i:02d}"
        rows = []
        for j in range(8):
            body_row = {
                "local_asset_id": f"{folder}_asset_{j}",
                "asset_id": f"{folder}_asset_{j}",
                "folder": folder,
                "file": f"{folder}_{j}.mov",
                "media_type": "video",
                "duration_seconds": 10.0 + j,
                "description": f"Scenic view of {folder} landmark {j}.",
                "tags": [f"tag{k}" for k in range(6)],
                "motion": "pan",
                "motion_intensity": 20,
                "motion_direction": "left",
                "framing": "wide",
                "shot_scale": "wide",
                "quality": {
                    "technical": 70,
                    "composition": 71,
                    "appeal": 72,
                    "clarity": 73,
                    "hero": 74,
                    "defect": 0,
                },
                "look": {
                    "brightness": 50,
                    "contrast": 50,
                    "saturation": 50,
                    "temperature": "neutral",
                    "colors": ["Grün", "Blau", "Braun"],
                },
                "people": False,
            }
            rows.append(body_row)
            all_assets.append(body_row)
        chapters[folder] = rows
    bundled = {
        "schema_version": "enhanced-intro-bundled-inventory-v1",
        "chapter_count": len(chapters),
        "asset_count": len(all_assets),
        "chapters": chapters,
        "all_assets": all_assets,
    }
    text = format_bundled_inventory_for_prompt(bundled)
    payload = json.loads(text)
    assert "all_assets" not in payload
    assert payload["asset_count"] == 96
    sample = payload["chapters"]["Chapter_00"][0]
    assert len(sample["tags"]) == 3
    assert set(sample["quality"]) <= {"technical", "appeal", "hero", "defect"}
    assert "composition" not in sample["quality"]
    assert "clarity" not in sample["quality"]
    assert set(sample["look"]) <= {"temperature", "colors"}
    assert len(sample["look"]["colors"]) == 2
    # Keine großen redundanten Strukturen: Prompt ohne Duplikat, kompakter als
    # chapters+all_assets Body-Dump.
    naive = json.dumps(bundled, ensure_ascii=False)
    assert len(text) < len(naive) * 0.65
    assert text.count("composition") == 0
    assert text.count("clarity") == 0
    assert text.count("brightness") == 0


def test_intro_prompt_rules() -> None:
    prompt = build_intro_unified_cut_prompt(
        locked_script_json="{}",
        segment_timings_json="{}",
        bundled_inventory_json='{"chapters":{}}',
        style_profile_text="s",
        dramaturgy_text="d",
        intro_audio_duration_seconds=9.5,
        sentence_timings_json=(
            '[{"sentence_id":"Intro_segment_001__s001",'
            '"words":[{"text":"Antelope","offset_seconds":1.4}]}]'
        ),
    )
    assert "BUNDLED INVENTORY" in prompt
    assert "strong" in prompt
    assert "acceptable" in prompt
    assert "4.0" in prompt
    assert "voiceover_postroll_sec between" in prompt
    assert "5.0 and 8.0" in prompt
    assert "SEPARATE preroll shot" in prompt
    assert "SEPARATE postroll shot" in prompt
    assert "intro_opener_asset_id" in prompt
    assert "intro_closing_asset_id" in prompt
    assert "pairwise distinct" in prompt
    assert "NEVER a copy of slot 1" in prompt or "NEVER" in prompt
    assert "9.500" in prompt
    assert "shot_min" in prompt
    assert "shot_max" in prompt
    assert "NOT enforced" in prompt
    assert "KEYWORD / CONTEXT CUTS" in prompt
    assert "KEYWORD / ENUMERATION SYNC" not in prompt
    assert "NOT every named place" in prompt
    assert "understand" in prompt.lower() or "FULL Intro VO" in prompt
    assert "keyword onset" in prompt
    assert "words[]" in prompt
    assert "Prefer WORD TIMINGS" in prompt
    assert "word times are not listed" not in prompt
    assert "offset_seconds" in prompt
    assert "mid_sentence" in prompt
    assert "ElevenLabs" in prompt
    assert "When both are present, offset_seconds wins." in prompt
    assert "Do NOT pre-roll" in prompt
    assert "NEVER put mid_sentence" in prompt
    assert "TWO DIFFERENT FIELDS" in prompt
    assert "do NOT fill gaps inside the VO" in prompt
    assert "After the LAST justified keyword/list cut" in prompt
    assert "Last boundary: last Intro sentence, position end" in prompt
    assert "first = VO start; last = VO end" in prompt
    assert "PAUSE RULES (DISABLED)" in prompt
    assert '"pause_directives": []' in prompt
    assert "ENUMERATION PACING" in prompt
    assert "Pulled pause_directives are DISABLED" in prompt
    assert "Antelope" in prompt
    assert CUT_ASSET_SELECTION_PROMPT_VERSION == "cut-asset-selection-v2"
    assert "ASSET SELECTION FROM SLIM INVENTORY" in prompt
    assert "hero and appeal help opener/closing" in prompt.lower() or (
        "Intro / closing note" in prompt
    )
    assert "PHOTOS / STILLS (CRITICAL)" in prompt
    assert "is_16_9" in prompt
    assert "landscape 16:9" in prompt
    assert "photo only if is_16_9" in prompt
    assert "Photos/stills only if is_16_9 is true" in prompt


def test_intro_prompt_uses_configured_hold_timings() -> None:
    prompt = build_intro_unified_cut_prompt(
        locked_script_json="{}",
        segment_timings_json="{}",
        bundled_inventory_json='{"chapters":{}}',
        style_profile_text="s",
        dramaturgy_text="d",
        intro_preroll_sec=3.0,
        intro_postroll_sec=7.0,
        intro_postroll_min_sec=6.0,
        intro_postroll_max_sec=9.0,
    )
    assert "SEPARATE preroll shot for 3.0s" in prompt
    assert "voiceover_preroll_sec to" in prompt and "3.0" in prompt
    assert "voiceover_postroll_sec between" in prompt
    assert "6.0 and 9.0" in prompt
    assert "prefer ~7.0" in prompt
    assert '"voiceover_preroll_sec": 3.0' in prompt
    assert '"voiceover_postroll_sec": 7.0' in prompt
    assert '"intro_opener_asset_id"' in prompt
    assert '"intro_closing_asset_id"' in prompt
    assert "SEPARATE postroll shot" in prompt
    assert "not an extension of slot 1" in prompt
    assert "extension of the last VO" in prompt
    assert "NEVER a copy of the last slot" in prompt


def test_enforce_intro_strong_only_rejects_acceptable() -> None:
    plan = UnifiedCutPlanDocument(
        script_version="v1",
        boundaries=[
            _bound("Intro_cut_000", "Intro_segment_001__s001", "start"),
            _bound("Intro_cut_001", "Intro_segment_001__s001", "middle"),
            _bound("Intro_cut_002", "Intro_segment_001__s001", "end"),
        ],
        slots=[
            _slot("Intro_slot_001", "acceptable", "yo_01"),
            _slot("Intro_slot_002", "strong", "ca_01"),
        ],
        voiceover_postroll_sec=9.0,
    )
    out = enforce_intro_strong_only(plan)
    assert out.slots[0].local_asset_id is None
    assert out.slots[0].asset_fit == "none"
    assert out.slots[0].coverage_gap_id
    assert out.slots[1].asset_fit == "strong"
    assert out.voiceover_preroll_sec == INTRO_OPENING_HOLD_SEC
    assert out.voiceover_postroll_sec == INTRO_CLOSING_HOLD_MAX_SEC  # clamped from 9


def test_intro_envelope_asset_errors_require_distinct_llm_assets() -> None:
    plan = UnifiedCutPlanDocument(
        script_version="v1",
        boundaries=[
            _bound("Intro_cut_000", "Intro_segment_001__s001", "start"),
            _bound("Intro_cut_001", "Intro_segment_001__s001", "end"),
        ],
        slots=[_slot("Intro_slot_001", "strong", "doors")],
        intro_opener_asset_id="doors",
        intro_closing_asset_id="doors",
        closing_fallback_asset_id="doors",
    )
    errors = intro_envelope_asset_errors(plan)
    assert any("intro_opener_asset_id" in e for e in errors)
    assert any("intro_closing_asset_id" in e for e in errors)
    assert any("verschieden" in e for e in errors)


def test_intro_envelopes_use_llm_assets_not_content_copies(tmp_path) -> None:
    """Vorlauf/Nachlauf = LLM-Assets; niemals First/Last-Content klonen."""
    from unittest.mock import patch

    from otio_app.defaults import DEFAULT_ENHANCED_WORK_SUBDIR
    from otio_app.models import Project, ProjectMode
    from otio_app.services.without_voiceover_enhanced.models import (
        EnhancedScriptDocument,
        FinalCutPlanDocument,
        FinalShot,
        NarrationAnchor,
        ScriptSegment,
    )
    from otio_app.services.without_voiceover_enhanced.timeline_resolver import (
        _apply_chapter_envelopes,
    )

    root = tmp_path / "proj"
    work = root / DEFAULT_ENHANCED_WORK_SUBDIR
    work.mkdir(parents=True)
    media = root / "still.jpg"
    media.write_bytes(b"fake")
    project = Project(
        id="intro-env",
        name="Irland",
        project_root=str(root),
        work_dir=str(work),
        project_mode=ProjectMode.WITHOUT_VOICEOVER_ENHANCED,
        language="de",
        asset_subdir_names=["Dublin"],
        selected_asset_subdirs=["Dublin"],
        fps=25.0,
        width=1920,
        height=1080,
    )
    locked = EnhancedScriptDocument(
        script_version="v1",
        segments=[
            ScriptSegment(
                segment_id="Intro_segment_001",
                folder_name="Intro",
                text="hello",
                sequence_index=1,
            )
        ],
    )
    final = FinalCutPlanDocument(
        script_version="v1",
        shots=[
            FinalShot(
                shot_id="Intro_slot_001",
                asset_id="doors",
                narration_start_anchor=NarrationAnchor(
                    segment_id="Intro_segment_001", offset_seconds=0.0
                ),
                narration_end_anchor=NarrationAnchor(
                    segment_id="Intro_segment_001", offset_seconds=6.0
                ),
            )
        ],
    )
    content = ResolvedShot(
        shot_id="Intro_slot_001",
        asset_id="doors",
        timeline_start_seconds=0.0,
        timeline_end_seconds=6.0,
        source_start_seconds=0.0,
        source_end_seconds=6.0,
        folder_name="Intro",
        chapter_id="Intro",
        resolved_media_path=str(media),
        resolved_media_kind="image",
        resolved_media_duration_seconds=6.0,
        hold_mode="freeze_video",
    )
    audio = ResolvedAudioSegment(
        segment_id="Intro_segment_001",
        audio_path="/tmp/intro.wav",
        timeline_start_seconds=0.0,
        timeline_end_seconds=6.0,
    )
    ordered = [content]
    repairs: list[str] = []
    errors: list[str] = []

    def _fake_envelope_from_asset(
        project,
        *,
        asset_id,
        catalog,
        shot_id,
        timeline_start,
        timeline_end,
        editorial_function,
        chapter_id,
        fps,
        head_trim,
        short_tolerance,
        repairs,
        errors,
        label,
    ):
        del (
            project,
            catalog,
            fps,
            head_trim,
            short_tolerance,
            repairs,
            errors,
            label,
        )
        return ResolvedShot(
            shot_id=shot_id,
            asset_id=str(asset_id),
            timeline_start_seconds=float(timeline_start),
            timeline_end_seconds=float(timeline_end),
            source_start_seconds=0.0,
            source_end_seconds=max(0.01, float(timeline_end) - float(timeline_start)),
            editorial_function=editorial_function,
            folder_name=chapter_id,
            chapter_id=chapter_id,
            resolved_media_path=str(media),
            resolved_media_kind="image",
            resolved_media_duration_seconds=10.0,
            hold_mode="freeze_video",
            asset_fit="strong",
            asset_fit_reason=f"LLM envelope ({editorial_function})",
        )

    with patch(
        "otio_app.services.without_voiceover_enhanced.timeline_resolver."
        "_make_envelope_shot_from_asset_id",
        _fake_envelope_from_asset,
    ):
        envs = _apply_chapter_envelopes(
            project,
            locked=locked,
            final=final,
            ordered=ordered,
            audio_segments=[audio],
            preroll=4.0,
            postroll=6.5,
            fps=25.0,
            repairs=repairs,
            errors=errors,
            catalog=object(),  # type: ignore[arg-type]
            intro_opener_asset_id="opener_wide",
            intro_closing_asset_id="closing_hold",
        )

    assert not errors
    assert len(envs) == 1
    env = envs[0]
    by_id = {s.shot_id: s for s in ordered}
    assert "Intro_preroll" in by_id
    assert "Intro_postroll" in by_id
    preroll = by_id["Intro_preroll"]
    postroll = by_id["Intro_postroll"]
    body = by_id["Intro_slot_001"]
    assert preroll.asset_id == "opener_wide"
    assert postroll.asset_id == "closing_hold"
    assert preroll.asset_id != body.asset_id
    assert postroll.asset_id != body.asset_id
    assert preroll.editorial_function == "technical_chapter_preroll"
    assert postroll.editorial_function == "technical_chapter_postroll"
    assert preroll.timeline_start_seconds == pytest.approx(0.0, abs=1e-3)
    assert preroll.timeline_end_seconds == pytest.approx(env.chapter_audio_start, abs=1e-3)
    assert body.timeline_start_seconds == pytest.approx(env.chapter_audio_start, abs=1e-3)
    assert body.timeline_end_seconds == pytest.approx(env.chapter_audio_end, abs=1e-3)
    assert postroll.timeline_start_seconds == pytest.approx(env.chapter_audio_end, abs=1e-3)
    assert postroll.timeline_end_seconds == pytest.approx(env.chapter_video_end, abs=1e-3)
    assert env.preroll_hold_shot_id == "Intro_preroll"
    assert env.postroll_hold_shot_id == "Intro_postroll"


def test_intro_envelopes_reject_missing_llm_assets_without_cloning(tmp_path) -> None:
    from otio_app.defaults import DEFAULT_ENHANCED_WORK_SUBDIR
    from otio_app.models import Project, ProjectMode
    from otio_app.services.without_voiceover_enhanced.models import (
        EnhancedScriptDocument,
        FinalCutPlanDocument,
        FinalShot,
        NarrationAnchor,
        ScriptSegment,
    )
    from otio_app.services.without_voiceover_enhanced.timeline_resolver import (
        _apply_chapter_envelopes,
    )

    root = tmp_path / "proj"
    work = root / DEFAULT_ENHANCED_WORK_SUBDIR
    work.mkdir(parents=True)
    media = root / "still.jpg"
    media.write_bytes(b"fake")
    project = Project(
        id="intro-env-missing",
        name="Irland",
        project_root=str(root),
        work_dir=str(work),
        project_mode=ProjectMode.WITHOUT_VOICEOVER_ENHANCED,
        language="de",
        asset_subdir_names=["Dublin"],
        selected_asset_subdirs=["Dublin"],
        fps=25.0,
    )
    locked = EnhancedScriptDocument(
        script_version="v1",
        segments=[
            ScriptSegment(
                segment_id="Intro_segment_001",
                folder_name="Intro",
                text="hello",
                sequence_index=1,
            )
        ],
    )
    final = FinalCutPlanDocument(
        script_version="v1",
        shots=[
            FinalShot(
                shot_id="Intro_slot_001",
                asset_id="doors",
                narration_start_anchor=NarrationAnchor(
                    segment_id="Intro_segment_001", offset_seconds=0.0
                ),
                narration_end_anchor=NarrationAnchor(
                    segment_id="Intro_segment_001", offset_seconds=6.0
                ),
            )
        ],
    )
    content = ResolvedShot(
        shot_id="Intro_slot_001",
        asset_id="doors",
        timeline_start_seconds=0.0,
        timeline_end_seconds=6.0,
        source_start_seconds=0.0,
        source_end_seconds=6.0,
        folder_name="Intro",
        chapter_id="Intro",
        resolved_media_path=str(media),
        resolved_media_kind="image",
        resolved_media_duration_seconds=6.0,
    )
    ordered = [content]
    errors: list[str] = []
    _apply_chapter_envelopes(
        project,
        locked=locked,
        final=final,
        ordered=ordered,
        audio_segments=[
            ResolvedAudioSegment(
                segment_id="Intro_segment_001",
                audio_path="/tmp/intro.wav",
                timeline_start_seconds=0.0,
                timeline_end_seconds=6.0,
            )
        ],
        preroll=4.0,
        postroll=6.5,
        fps=25.0,
        repairs=[],
        errors=errors,
        catalog=object(),  # type: ignore[arg-type]
        intro_opener_asset_id=None,
        intro_closing_asset_id=None,
    )
    assert any("intro_opener_asset_id fehlt" in e for e in errors)
    assert any("intro_closing_asset_id fehlt" in e for e in errors)
    assert not any(s.shot_id == "Intro_preroll" for s in ordered)
    assert not any(s.shot_id == "Intro_postroll" for s in ordered)


def test_clamp_intro_closing_hold() -> None:
    assert clamp_intro_closing_hold(None) == INTRO_CLOSING_HOLD_DEFAULT_SEC
    assert clamp_intro_closing_hold(3.0) == 5.0
    assert clamp_intro_closing_hold(7.0) == 7.0
    assert clamp_intro_closing_hold(12.0) == 8.0
    from otio_app.services.without_voiceover_enhanced.cut_plan_options import (
        CutPlanOptions,
    )

    opts = CutPlanOptions(
        intro_voiceover_postroll_sec=4.0,
        intro_voiceover_postroll_min_sec=2.0,
        intro_voiceover_postroll_max_sec=5.0,
    )
    assert clamp_intro_closing_hold(None, options=opts) == 4.0
    assert clamp_intro_closing_hold(1.0, options=opts) == 2.0
    assert clamp_intro_closing_hold(9.0, options=opts) == 5.0


def test_split_and_merge_intro_body() -> None:
    intro_pause = PauseDirective(
        after_segment_id="Intro_segment_001",
        after_sentence_id="Intro_segment_001__s001",
        pause_function="emphasis",
        duration_class="medium",
        visual_behavior="hold_current_shot",
        editorial_reason="breath between list places",
    )
    body_pause = PauseDirective(
        after_segment_id="Yosemite_segment_001",
        after_sentence_id="seg_a__s001",
        pause_function="breath",
        duration_class="short",
        visual_behavior="hold_current_shot",
        editorial_reason="chapter breath",
    )
    intro = UnifiedCutPlanDocument(
        script_version="v1",
        pause_directives=[intro_pause],
        boundaries=[
            _bound("Intro_cut_000", "Intro_segment_001__s001", "start"),
            _bound("Intro_cut_001", "Intro_segment_001__s001", "end"),
        ],
        slots=[_slot("Intro_slot_001", "strong", "yo_01")],
        voiceover_preroll_sec=4.0,
        voiceover_postroll_sec=6.5,
    )
    body = UnifiedCutPlanDocument(
        script_version="v1",
        pause_directives=[body_pause],
        boundaries=[
            _bound("Yosemite_cut_000", "seg_a__s001", "start"),
            _bound("Yosemite_cut_001", "seg_a__s002", "end"),
        ],
        slots=[_slot("Yosemite_slot_001", "strong", "yo_02")],
    )
    merged = merge_intro_and_body_plans(
        intro=intro, body=body, script_version="v1"
    )
    assert merged.slots[0].slot_id.startswith("Intro_")
    assert merged.slots[1].slot_id.startswith("Yosemite_")
    assert merged.slots[1].start_sentence_id == "seg_a__s001"
    assert len(merged.boundaries) == 3  # intro 2 + body without first
    assert len(merged.pause_directives) == 2
    assert merged.pause_directives[0].after_sentence_id == (
        "Intro_segment_001__s001"
    )

    split_intro, split_body = split_intro_from_unified(merged)
    assert split_intro is not None
    assert split_body is not None
    assert len(split_intro.slots) == 1
    assert len(split_body.slots) == 1
    assert len(split_intro.pause_directives) == 1
    assert split_intro.pause_directives[0].pause_function == "emphasis"
    assert len(split_body.pause_directives) == 1
    assert split_body.pause_directives[0].after_segment_id.startswith(
        "Yosemite"
    )


def test_resolve_intro_timeline_requires_intro_plan(tmp_path) -> None:
    from otio_app.defaults import DEFAULT_ENHANCED_WORK_SUBDIR
    from otio_app.models import Project, ProjectMode
    from otio_app.services.without_voiceover_enhanced.intro_cut_service import (
        IntroCutError,
        resolve_intro_timeline,
    )

    root = tmp_path / "proj"
    work = root / DEFAULT_ENHANCED_WORK_SUBDIR
    work.mkdir(parents=True)
    project = Project(
        name="IntroTiming",
        project_root=str(root),
        work_dir=str(work),
        project_mode=ProjectMode.WITHOUT_VOICEOVER_ENHANCED,
        language="de",
        asset_subdir_names=["Yosemite"],
        selected_asset_subdirs=["Yosemite"],
        fps=25.0,
    )
    try:
        resolve_intro_timeline(project)
        raise AssertionError("expected IntroCutError")
    except IntroCutError as exc:
        assert "Intro-Cut-Plan fehlt" in str(exc)


def test_resolve_intro_timeline_calls_unified_without_persist(tmp_path) -> None:
    from unittest.mock import patch

    from otio_app.defaults import DEFAULT_ENHANCED_WORK_SUBDIR
    from otio_app.models import Project, ProjectMode
    from otio_app.services.without_voiceover_enhanced.intro_cut_service import (
        INTRO_OPENING_HOLD_SEC,
        intro_resolved_timeline_path,
        intro_unified_cut_plan_path,
        resolve_intro_timeline,
    )
    from otio_app.services.without_voiceover_enhanced.io_utils import write_json
    from otio_app.services.without_voiceover_enhanced.paths import (
        resolved_timeline_path,
    )

    root = tmp_path / "proj"
    work = root / DEFAULT_ENHANCED_WORK_SUBDIR
    work.mkdir(parents=True)
    project = Project(
        name="IntroTiming",
        project_root=str(root),
        work_dir=str(work),
        project_mode=ProjectMode.WITHOUT_VOICEOVER_ENHANCED,
        language="de",
        asset_subdir_names=["Yosemite"],
        selected_asset_subdirs=["Yosemite"],
        fps=25.0,
    )
    intro_plan = UnifiedCutPlanDocument(
        script_version="v1",
        boundaries=[
            _bound("Intro_cut_000", "Intro_segment_001__s001", "start"),
            _bound("Intro_cut_001", "Intro_segment_001__s001", "end"),
        ],
        slots=[_slot("Intro_slot_001", "strong", "yo_01")],
        voiceover_preroll_sec=4.0,
        voiceover_postroll_sec=6.5,
        intro_opener_asset_id="opener_a",
        intro_closing_asset_id="closing_a",
        closing_fallback_asset_id="fallback_a",
    )
    write_json(intro_unified_cut_plan_path(project), intro_plan)

    fake = ResolvedTimelineDocument(
        script_version="v1",
        fps=25.0,
        total_duration_seconds=12.0,
        audio_segments=[
            ResolvedAudioSegment(
                segment_id="Intro_segment_001",
                audio_path="/tmp/intro.wav",
                timeline_start_seconds=4.0,
                timeline_end_seconds=10.0,
            )
        ],
        shots=[
            ResolvedShot(
                shot_id="Intro_slot_001",
                asset_id="yo_01",
                timeline_start_seconds=0.0,
                timeline_end_seconds=12.0,
                source_start_seconds=0.0,
                source_end_seconds=1.0,
                folder_name="Intro",
                chapter_id="Intro",
            )
        ],
        chapters=[
            ResolvedChapterEnvelope(
                chapter_id="Intro",
                folder_name="Intro",
                chapter_video_start=0.0,
                chapter_audio_start=4.0,
                chapter_audio_end=10.0,
                chapter_video_end=12.0,
                preroll_seconds=4.0,
                postroll_seconds=6.5,
                first_shot_id="Intro_slot_001",
                last_shot_id="Intro_slot_001",
                segment_ids=["Intro_segment_001"],
            )
        ],
        voiceover_preroll_sec=4.0,
        voiceover_postroll_sec=6.5,
    )
    captured: dict = {}

    def _fake_resolve(project, plan=None, **kwargs):
        captured["persist"] = kwargs.get("persist")
        captured["preroll_override"] = kwargs.get("preroll_override")
        captured["postroll_override"] = kwargs.get("postroll_override")
        captured["include_chapter"] = kwargs.get("include_chapter")
        captured["plan_slots"] = len(plan.slots) if plan else 0
        return fake

    with patch(
        "otio_app.services.without_voiceover_enhanced.unified_timeline_service.resolve_unified_timeline",
        _fake_resolve,
    ):
        out = resolve_intro_timeline(project)

    assert captured["persist"] is False
    assert captured["preroll_override"] == INTRO_OPENING_HOLD_SEC
    assert captured["postroll_override"] == 6.5
    assert captured["include_chapter"] is not None
    assert captured["include_chapter"]("Intro")
    assert not captured["include_chapter"]("Yosemite")
    assert captured["plan_slots"] == 1
    assert out.total_duration_seconds == 12.0
    assert intro_resolved_timeline_path(project).is_file()
    assert not resolved_timeline_path(project).exists()


def test_resolve_intro_timeline_uses_cut_plan_intro_holds(tmp_path) -> None:
    from unittest.mock import patch

    from otio_app.defaults import DEFAULT_ENHANCED_WORK_SUBDIR
    from otio_app.models import Project, ProjectMode
    from otio_app.services.without_voiceover_enhanced.cut_plan_options import (
        CutPlanOptions,
        save_cut_plan_options,
    )
    from otio_app.services.without_voiceover_enhanced.intro_cut_service import (
        intro_unified_cut_plan_path,
        resolve_intro_timeline,
    )
    from otio_app.services.without_voiceover_enhanced.io_utils import write_json

    root = tmp_path / "proj"
    work = root / DEFAULT_ENHANCED_WORK_SUBDIR
    work.mkdir(parents=True)
    project = Project(
        name="IntroHolds",
        project_root=str(root),
        work_dir=str(work),
        project_mode=ProjectMode.WITHOUT_VOICEOVER_ENHANCED,
        language="de",
        asset_subdir_names=["Yosemite"],
        selected_asset_subdirs=["Yosemite"],
        fps=25.0,
    )
    save_cut_plan_options(
        project,
        CutPlanOptions(
            intro_voiceover_preroll_sec=2.5,
            intro_voiceover_postroll_sec=7.0,
            intro_voiceover_postroll_min_sec=6.0,
            intro_voiceover_postroll_max_sec=9.0,
        ),
    )
    write_json(
        intro_unified_cut_plan_path(project),
        UnifiedCutPlanDocument(
            script_version="v1",
            boundaries=[
                _bound("Intro_cut_000", "Intro_segment_001__s001", "start"),
                _bound("Intro_cut_001", "Intro_segment_001__s001", "end"),
            ],
            slots=[_slot("Intro_slot_001", "strong", "yo_01")],
            voiceover_postroll_sec=7.0,
            intro_opener_asset_id="opener_a",
            intro_closing_asset_id="closing_a",
            closing_fallback_asset_id="fallback_a",
        ),
    )
    fake = ResolvedTimelineDocument(
        script_version="v1",
        fps=25.0,
        total_duration_seconds=10.0,
        audio_segments=[
            ResolvedAudioSegment(
                segment_id="Intro_segment_001",
                audio_path="/tmp/intro.wav",
                timeline_start_seconds=2.5,
                timeline_end_seconds=8.0,
            )
        ],
        shots=[
            ResolvedShot(
                shot_id="Intro_slot_001",
                asset_id="yo_01",
                timeline_start_seconds=0.0,
                timeline_end_seconds=10.0,
                source_start_seconds=0.0,
                source_end_seconds=1.0,
                folder_name="Intro",
                chapter_id="Intro",
            )
        ],
        chapters=[],
        voiceover_preroll_sec=2.5,
        voiceover_postroll_sec=7.0,
    )
    captured: dict = {}

    def _fake_resolve(project, plan=None, **kwargs):
        del project, plan
        captured.update(kwargs)
        return fake

    with patch(
        "otio_app.services.without_voiceover_enhanced.unified_timeline_service.resolve_unified_timeline",
        _fake_resolve,
    ):
        out = resolve_intro_timeline(project)

    assert captured["preroll_override"] == 2.5
    assert captured["postroll_override"] == 7.0
    assert out.voiceover_preroll_sec == 2.5
    assert out.voiceover_postroll_sec == 7.0


def test_persist_intro_plan_invalidates_stale_resolved(tmp_path) -> None:
    from unittest.mock import patch

    from otio_app.defaults import DEFAULT_ENHANCED_WORK_SUBDIR
    from otio_app.models import Project, ProjectMode
    from otio_app.services.without_voiceover_enhanced.intro_cut_service import (
        intro_resolved_timeline_path,
        persist_intro_unified_plan,
    )
    from otio_app.services.without_voiceover_enhanced.io_utils import write_json
    from otio_app.services.without_voiceover_enhanced.models import (
        EnhancedScriptDocument,
    )

    root = tmp_path / "proj"
    work = root / DEFAULT_ENHANCED_WORK_SUBDIR
    work.mkdir(parents=True)
    project = Project(
        name="Invalidate",
        project_root=str(root),
        work_dir=str(work),
        project_mode=ProjectMode.WITHOUT_VOICEOVER_ENHANCED,
        language="de",
        asset_subdir_names=["Yosemite"],
        selected_asset_subdirs=["Yosemite"],
        fps=25.0,
    )
    write_json(
        intro_resolved_timeline_path(project),
        ResolvedTimelineDocument(
            script_version="v1",
            fps=25.0,
            total_duration_seconds=10.0,
            shots=[
                ResolvedShot(
                    shot_id=f"Intro_slot_{i:03d}",
                    asset_id="yo_01",
                    timeline_start_seconds=float(i),
                    timeline_end_seconds=float(i + 1),
                    source_start_seconds=0.0,
                    source_end_seconds=1.0,
                    folder_name="Intro",
                )
                for i in range(10)
            ],
        ),
    )
    assert intro_resolved_timeline_path(project).is_file()
    plan = UnifiedCutPlanDocument(
        script_version="v1",
        boundaries=[
            _bound("Intro_cut_000", "Intro_segment_001__s001", "start"),
            _bound("Intro_cut_001", "Intro_segment_001__s001", "end"),
        ],
        slots=[_slot("Intro_slot_001", "strong", "yo_01")],
        voiceover_preroll_sec=4.0,
        voiceover_postroll_sec=6.5,
    )
    locked = EnhancedScriptDocument(script_version="v1", script_status="locked")
    with patch(
        "otio_app.services.without_voiceover_enhanced.intro_cut_service.require_locked_script",
        return_value=locked,
    ):
        persist_intro_unified_plan(project, plan)
    assert not intro_resolved_timeline_path(project).exists()


def test_export_intro_otio_reresolves_when_plan_and_resolved_mismatch(
    tmp_path,
) -> None:
    from pathlib import Path
    from unittest.mock import patch

    from otio_app.defaults import DEFAULT_ENHANCED_WORK_SUBDIR
    from otio_app.models import Project, ProjectMode
    from otio_app.services.without_voiceover_enhanced.intro_cut_service import (
        export_intro_otio,
        intro_resolved_timeline_path,
        intro_unified_cut_plan_path,
    )
    from otio_app.services.without_voiceover_enhanced.io_utils import write_json

    root = tmp_path / "proj"
    work = root / DEFAULT_ENHANCED_WORK_SUBDIR
    work.mkdir(parents=True)
    project = Project(
        name="StaleExport",
        project_root=str(root),
        work_dir=str(work),
        project_mode=ProjectMode.WITHOUT_VOICEOVER_ENHANCED,
        language="de",
        asset_subdir_names=["Yosemite"],
        selected_asset_subdirs=["Yosemite"],
        fps=25.0,
    )
    plan = UnifiedCutPlanDocument(
        script_version="v1",
        boundaries=[
            _bound("Intro_cut_000", "Intro_segment_001__s001", "start"),
            _bound("Intro_cut_001", "Intro_segment_001__s001", "middle"),
            _bound("Intro_cut_002", "Intro_segment_001__s001", "end"),
        ],
        slots=[
            _slot("Intro_slot_001", "strong", "yo_01"),
            _slot("Intro_slot_002", "strong", "yo_02"),
        ],
        voiceover_preroll_sec=4.0,
        voiceover_postroll_sec=6.5,
    )
    write_json(intro_unified_cut_plan_path(project), plan)
    # Altes Timing mit 10 Shots (wie vor Prompt-Änderung).
    write_json(
        intro_resolved_timeline_path(project),
        ResolvedTimelineDocument(
            script_version="v1",
            fps=25.0,
            total_duration_seconds=40.0,
            shots=[
                ResolvedShot(
                    shot_id=f"Intro_slot_{i:03d}",
                    asset_id="yo_01",
                    timeline_start_seconds=float(i),
                    timeline_end_seconds=float(i + 1),
                    source_start_seconds=0.0,
                    source_end_seconds=1.0,
                    folder_name="Intro",
                    chapter_id="Intro",
                )
                for i in range(1, 11)
            ],
            audio_segments=[
                ResolvedAudioSegment(
                    segment_id="Intro_segment_001",
                    audio_path="/tmp/intro.wav",
                    timeline_start_seconds=4.0,
                    timeline_end_seconds=20.0,
                )
            ],
        ),
    )
    fresh = ResolvedTimelineDocument(
        script_version="v1",
        fps=25.0,
        total_duration_seconds=12.0,
        shots=[
            ResolvedShot(
                shot_id="Intro_slot_001",
                asset_id="yo_01",
                timeline_start_seconds=0.0,
                timeline_end_seconds=6.0,
                source_start_seconds=0.0,
                source_end_seconds=1.0,
                folder_name="Intro",
                chapter_id="Intro",
            ),
            ResolvedShot(
                shot_id="Intro_slot_002",
                asset_id="yo_02",
                timeline_start_seconds=6.0,
                timeline_end_seconds=12.0,
                source_start_seconds=0.0,
                source_end_seconds=1.0,
                folder_name="Intro",
                chapter_id="Intro",
            ),
        ],
        audio_segments=[
            ResolvedAudioSegment(
                segment_id="Intro_segment_001",
                audio_path="/tmp/intro.wav",
                timeline_start_seconds=4.0,
                timeline_end_seconds=10.0,
            )
        ],
    )
    captured: dict = {}

    def _fake_resolve(project, **_kwargs):
        write_json(intro_resolved_timeline_path(project), fresh)
        return fresh

    def _fake_export(project, *, basename, allow_errors=False, resolved=None, timeline_name=None):
        captured["n_shots"] = len(resolved.shots) if resolved else 0
        out = Path(project.language_work_dir_path) / "exports" / f"{basename}.otio"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text("otio", encoding="utf-8")
        return out

    with (
        patch(
            "otio_app.services.without_voiceover_enhanced.intro_cut_service.resolve_intro_timeline",
            _fake_resolve,
        ),
        patch(
            "otio_app.services.without_voiceover_enhanced.intro_cut_service.export_otio_from_resolved_timeline",
            _fake_export,
        ),
    ):
        export_intro_otio(project, basename="stale_intro", allow_errors=True)

    assert captured["n_shots"] == 2


def test_export_intro_otio_does_not_rewrite_full_timeline(tmp_path) -> None:
    """Regression: Intro-Export darf resolved_timeline.json nicht überschreiben."""
    from pathlib import Path
    from unittest.mock import patch

    from otio_app.defaults import DEFAULT_ENHANCED_WORK_SUBDIR
    from otio_app.models import Project, ProjectMode
    from otio_app.services.without_voiceover_enhanced.intro_cut_service import (
        export_intro_otio,
    )
    from otio_app.services.without_voiceover_enhanced.io_utils import write_json
    from otio_app.services.without_voiceover_enhanced.paths import (
        resolved_timeline_path,
    )

    root = tmp_path / "proj"
    work = root / DEFAULT_ENHANCED_WORK_SUBDIR
    work.mkdir(parents=True)
    project = Project(
        name="IntroExport",
        project_root=str(root),
        work_dir=str(work),
        project_mode=ProjectMode.WITHOUT_VOICEOVER_ENHANCED,
        language="de",
        asset_subdir_names=["Yosemite"],
        selected_asset_subdirs=["Yosemite"],
        fps=25.0,
    )
    full = ResolvedTimelineDocument(
        script_version="v1",
        fps=25.0,
        total_duration_seconds=30.0,
        audio_segments=[
            ResolvedAudioSegment(
                segment_id="Intro_segment_001",
                audio_path="/tmp/intro.wav",
                timeline_start_seconds=0.0,
                timeline_end_seconds=5.0,
            ),
            ResolvedAudioSegment(
                segment_id="Yosemite_segment_001",
                audio_path="/tmp/y.wav",
                timeline_start_seconds=10.0,
                timeline_end_seconds=20.0,
            ),
        ],
        shots=[
            ResolvedShot(
                shot_id="Intro_slot_001",
                asset_id="yo_01",
                timeline_start_seconds=0.0,
                timeline_end_seconds=5.0,
                source_start_seconds=0.0,
                source_end_seconds=1.0,
                folder_name="Intro",
            ),
            ResolvedShot(
                shot_id="Yosemite_slot_001",
                asset_id="yo_02",
                timeline_start_seconds=10.0,
                timeline_end_seconds=20.0,
                source_start_seconds=0.0,
                source_end_seconds=1.0,
                folder_name="Yosemite",
            ),
        ],
        chapters=[
            ResolvedChapterEnvelope(
                chapter_id="Intro",
                folder_name="Intro",
                chapter_video_start=0.0,
                chapter_audio_start=0.0,
                chapter_audio_end=5.0,
                chapter_video_end=5.0,
                first_shot_id="Intro_slot_001",
                last_shot_id="Intro_slot_001",
                segment_ids=["Intro_segment_001"],
            )
        ],
    )
    write_json(resolved_timeline_path(project), full)
    before = resolved_timeline_path(project).read_text(encoding="utf-8")

    captured: dict = {}

    def _fake_export(project, *, basename, allow_errors=False, resolved=None, timeline_name=None):
        captured["resolved"] = resolved
        captured["basename"] = basename
        captured["allow_errors"] = allow_errors
        out = Path(project.language_work_dir_path) / "exports" / f"{basename}.otio"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text("otio", encoding="utf-8")
        return out

    with patch(
        "otio_app.services.without_voiceover_enhanced.intro_cut_service.export_otio_from_resolved_timeline",
        _fake_export,
    ):
        path = export_intro_otio(project, basename="only_intro", allow_errors=True)

    assert path.name == "only_intro.otio"
    assert captured["allow_errors"] is True
    assert captured["resolved"] is not None
    assert {s.shot_id for s in captured["resolved"].shots} == {"Intro_slot_001"}
    assert {a.segment_id for a in captured["resolved"].audio_segments} == {
        "Intro_segment_001"
    }
    after = resolved_timeline_path(project).read_text(encoding="utf-8")
    assert before == after  # Gesamt-Timeline unverändert


def test_filter_resolved_timeline_to_intro() -> None:
    resolved = ResolvedTimelineDocument(
        script_version="v1",
        fps=25.0,
        total_duration_seconds=30.0,
        audio_segments=[
            ResolvedAudioSegment(
                segment_id="Intro_segment_001",
                audio_path="/tmp/intro.wav",
                timeline_start_seconds=4.0,
                timeline_end_seconds=10.0,
            ),
            ResolvedAudioSegment(
                segment_id="Yosemite_segment_001",
                audio_path="/tmp/y.wav",
                timeline_start_seconds=16.5,
                timeline_end_seconds=25.0,
            ),
        ],
        shots=[
            ResolvedShot(
                shot_id="Intro_slot_001",
                asset_id="yo_01",
                timeline_start_seconds=0.0,
                timeline_end_seconds=16.5,
                source_start_seconds=0.0,
                source_end_seconds=1.0,
                folder_name="Intro",
            ),
            ResolvedShot(
                shot_id="Yosemite_slot_001",
                asset_id="yo_02",
                timeline_start_seconds=16.5,
                timeline_end_seconds=30.0,
                source_start_seconds=0.0,
                source_end_seconds=1.0,
                folder_name="Yosemite",
            ),
        ],
        chapters=[
            ResolvedChapterEnvelope(
                chapter_id="Intro",
                folder_name="Intro",
                chapter_video_start=0.0,
                chapter_audio_start=4.0,
                chapter_audio_end=10.0,
                chapter_video_end=16.5,
                preroll_seconds=4.0,
                postroll_seconds=6.5,
                first_shot_id="Intro_slot_001",
                last_shot_id="Intro_slot_001",
            ),
            ResolvedChapterEnvelope(
                chapter_id="Yosemite",
                folder_name="Yosemite",
                chapter_video_start=16.5,
                chapter_audio_start=16.5,
                chapter_audio_end=25.0,
                chapter_video_end=30.0,
                preroll_seconds=0.0,
                postroll_seconds=5.0,
                first_shot_id="Yosemite_slot_001",
                last_shot_id="Yosemite_slot_001",
            ),
        ],
    )
    intro = filter_resolved_timeline_to_intro(resolved)
    assert len(intro.chapters) == 1
    assert intro.chapters[0].folder_name == "Intro"
    assert intro.chapters[0].chapter_video_start == 0.0
    assert len(intro.shots) == 1
    assert intro.shots[0].shot_id == "Intro_slot_001"
    assert intro.total_duration_seconds == 16.5


def test_intro_plan_stale_when_locked_script_version_changes(tmp_path) -> None:
    """Nach Script-Regen zählen alte Intro-Cuts nicht mehr als aktuell."""
    from otio_app.defaults import DEFAULT_ENHANCED_WORK_SUBDIR
    from otio_app.models import Project, ProjectMode
    from otio_app.services.without_voiceover_enhanced.intro_cut_service import (
        intro_plan_matches_locked_script,
        intro_resolved_matches_plan,
    )
    from otio_app.services.without_voiceover_enhanced.io_utils import write_json
    from otio_app.services.without_voiceover_enhanced.models import (
        EnhancedScriptDocument,
    )
    from otio_app.services.without_voiceover_enhanced.paths import script_locked_path

    root = tmp_path / "proj"
    work = root / DEFAULT_ENHANCED_WORK_SUBDIR
    work.mkdir(parents=True)
    project = Project(
        name="IntroStale",
        project_root=str(root),
        work_dir=str(work),
        project_mode=ProjectMode.WITHOUT_VOICEOVER_ENHANCED,
        language="de",
        asset_subdir_names=["Yosemite"],
        selected_asset_subdirs=["Yosemite"],
        fps=25.0,
    )
    plan = UnifiedCutPlanDocument(
        script_version="v1",
        boundaries=[
            _bound("Intro_cut_000", "Intro_segment_001__s001", "start"),
            _bound("Intro_cut_001", "Intro_segment_001__s001", "end"),
        ],
        slots=[_slot("Intro_slot_001", "strong", "yo_01")],
    )
    resolved = ResolvedTimelineDocument(
        script_version="v1",
        fps=25.0,
        total_duration_seconds=1.0,
        shots=[
            ResolvedShot(
                shot_id="Intro_slot_001",
                asset_id="yo_01",
                timeline_start_seconds=0.0,
                timeline_end_seconds=1.0,
                source_start_seconds=0.0,
                source_end_seconds=1.0,
            )
        ],
    )
    write_json(
        script_locked_path(project),
        EnhancedScriptDocument(
            script_version="script-v2",
            script_status="locked",
            narration_full="New intro.",
            segments=[],
        ),
    )
    assert intro_plan_matches_locked_script(project, plan) is False
    assert intro_resolved_matches_plan(plan, resolved, project=project) is False
    # Ohne project-Arg bleibt der Slot-Count-Check (Abwärtskompatibilität).
    assert intro_resolved_matches_plan(plan, resolved) is True
