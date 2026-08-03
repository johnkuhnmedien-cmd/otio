"""Auto-Retry für Unified-LLM bei Parse-/Schema-Fehlern."""

from __future__ import annotations

import json
from typing import Any

import pytest

from otio_app.services.without_voiceover_enhanced.cut_plan_options import (
    CutPlanOptions,
)
from otio_app.services.without_voiceover_enhanced.cut_plan_service import (
    UNIFIED_CUT_LLM_MAX_ATTEMPTS,
    CutPlanError,
    _ChapterCutContext,
    _is_retryable_unified_cut_error,
    _unified_cut_retry_prompt_suffix,
    generate_unified_cut_for_folder,
)
from otio_app.services.without_voiceover_enhanced.models import (
    EnhancedScriptDocument,
    ScriptSegment,
    SegmentTiming,
    SegmentTimingsDocument,
)
from otio_app.services.without_voiceover_enhanced.unified_cut_plan import (
    UnifiedCutPlanError,
)


def test_retryable_unified_cut_error_markers() -> None:
    assert _is_retryable_unified_cut_error(
        UnifiedCutPlanError(
            "Invariante verletzt: len(slots)=11 muss len(boundaries)-1=12 sein."
        )
    )
    assert _is_retryable_unified_cut_error(
        CutPlanError("LLM-Antwort enthielt keine Slots.")
    )
    assert _is_retryable_unified_cut_error(
        ValueError("1 validation error for UnifiedCutPlanDocument")
    )
    assert not _is_retryable_unified_cut_error(
        CutPlanError("Segment-Timings fehlen.")
    )
    assert not _is_retryable_unified_cut_error(
        CutPlanError("Keyword Flow benötigt echte ElevenLabs-Wort-Timestamps")
    )


def test_retry_prompt_suffix_mentions_invariant() -> None:
    text = _unified_cut_retry_prompt_suffix(
        failed_attempt=1,
        error="Invariante verletzt: len(slots)=11 muss len(boundaries)-1=12 sein.",
    )
    assert "PREVIOUS ATTEMPT FAILED (attempt 1)" in text
    assert "len(slots) == len(boundaries) - 1" in text
    assert "len(slots)=11" in text


def _valid_payload() -> dict[str, Any]:
    return {
        "pause_directives": [],
        "boundaries": [
            {
                "cut_id": "cut_000",
                "sentence_id": "Coast_segment_001__s001",
                "position": "start",
                "alignment": "sentence_boundary",
            },
            {
                "cut_id": "cut_001",
                "sentence_id": "Coast_segment_001__s001",
                "position": "end",
                "alignment": "sentence_boundary",
            },
        ],
        "slots": [
            {
                "slot_id": "slot_001",
                "local_asset_id": "loc_a",
                "asset_fit": "strong",
                "asset_fit_reason": "match",
                "visual_intent": "coast",
                "narrative_function": "chapter_open",
                "coverage_gap_id": None,
            }
        ],
        "closing_fallback_asset_id": "loc_b",
    }


def _invalid_slots_payload() -> dict[str, Any]:
    payload = _valid_payload()
    payload["boundaries"].append(
        {
            "cut_id": "cut_002",
            "sentence_id": "Coast_segment_001__s001",
            "position": "end",
            "alignment": "sentence_boundary",
        }
    )
    return payload


def _locked_and_timings() -> tuple[EnhancedScriptDocument, SegmentTimingsDocument]:
    locked = EnhancedScriptDocument(
        script_version="v1",
        segments=[
            ScriptSegment(
                segment_id="Coast_segment_001",
                text="Waves hit the rocks.",
                sequence_index=0,
                folder_name="Coast",
            )
        ],
    )
    timings = SegmentTimingsDocument(
        script_version="v1",
        segments=[
            SegmentTiming(
                segment_id="Coast_segment_001",
                script_version="v1",
                audio_path="/tmp/coast.mp3",
                duration_seconds=4.0,
            )
        ],
    )
    return locked, timings


def _patch_common(monkeypatch: pytest.MonkeyPatch, mod: Any) -> _ChapterCutContext:
    locked, timings = _locked_and_timings()
    context = _ChapterCutContext(
        folder_name="Coast",
        folder_slug="Coast",
        previous_folder_name=None,
        next_folder_name=None,
        segment_ids={"Coast_segment_001"},
        script_slice=locked,
        timings_slice=timings,
    )
    monkeypatch.setattr(mod, "require_locked_script", lambda project: locked)
    monkeypatch.setattr(mod, "load_segment_timings", lambda project: timings)
    monkeypatch.setattr(mod, "load_cut_plan_options", lambda project: CutPlanOptions())
    monkeypatch.setattr(
        mod,
        "_local_assets_payload",
        lambda *a, **k: [
            {"asset_id": "loc_a", "media_type": "video", "duration_seconds": 12.0},
            {"asset_id": "loc_b", "media_type": "photo"},
        ],
    )
    monkeypatch.setattr(mod, "_chapter_dramaturgy_text_for_folder", lambda *a, **k: "")
    monkeypatch.setattr(mod, "_style_text", lambda project: "")
    monkeypatch.setattr(
        mod,
        "build_sentence_timings_json_for_segments",
        lambda *a, **k: json.dumps(
            [
                {
                    "sentence_id": "Coast_segment_001__s001",
                    "text": "Waves hit the rocks.",
                    "start_seconds": 0.0,
                    "end_seconds": 4.0,
                    "words": [],
                }
            ]
        ),
    )
    monkeypatch.setattr(
        "otio_app.services.voiceover_generation.model_settings_service.resolve_llm_model_id",
        lambda provider, model: model,
    )
    return context


def test_generate_unified_cut_retries_then_passes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from otio_app.services.without_voiceover_enhanced import cut_plan_service as mod

    calls: list[str] = []

    def fake_llm(*, prompt: str, model: str, images=None):  # noqa: ANN001
        calls.append(prompt)
        if len(calls) == 1:
            return json.dumps(_invalid_slots_payload())
        assert "PREVIOUS ATTEMPT FAILED" in prompt
        assert "len(slots) == len(boundaries) - 1" in prompt
        return json.dumps(_valid_payload())

    context = _patch_common(monkeypatch, mod)
    project = type("P", (), {"selected_asset_subdirs": ["Coast"], "id": "p1"})()
    result = generate_unified_cut_for_folder(
        project,  # type: ignore[arg-type]
        "Coast",
        llm_callable=fake_llm,
        context=context,
    )
    assert result.status == "PASS"
    assert result.attempts == 2
    assert len(calls) == 2
    assert result.plan is not None
    assert len(result.plan.slots) == 1
    assert UNIFIED_CUT_LLM_MAX_ATTEMPTS >= 2


def test_generate_unified_cut_exhausts_retries(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from otio_app.services.without_voiceover_enhanced import cut_plan_service as mod

    calls: list[int] = []

    def fake_llm(*, prompt: str, model: str, images=None):  # noqa: ANN001
        calls.append(1)
        return json.dumps(_invalid_slots_payload())

    context = _patch_common(monkeypatch, mod)
    project = type("P", (), {"selected_asset_subdirs": ["Coast"], "id": "p1"})()
    result = generate_unified_cut_for_folder(
        project,  # type: ignore[arg-type]
        "Coast",
        llm_callable=fake_llm,
        context=context,
    )
    assert result.status == "FAIL"
    assert result.attempts == UNIFIED_CUT_LLM_MAX_ATTEMPTS
    assert len(calls) == UNIFIED_CUT_LLM_MAX_ATTEMPTS
    assert result.error is not None
    assert "nach 3 Versuchen" in result.error
    assert "Invariante verletzt" in result.error or "len(slots)" in result.error
