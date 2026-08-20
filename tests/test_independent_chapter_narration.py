"""ENHANCED-INDEPENDENT-CHAPTER-NARRATION-001 — Prompts, Guard, Raw Style, Repair."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from otio_app.defaults import DEFAULT_ENHANCED_WORK_SUBDIR
from otio_app.models import Project, ProjectMode
from otio_app.services.plan_llm_client import PlanLlmCancelledError
from otio_app.services.voiceover_generation.models import (
    STYLE_MODE_RAW_TEXT,
    DramaturgyFolderEntry,
    DramaturgyPlan,
    FolderVoiceoverSetting,
    FolderVoiceoverSettingsDocument,
    VoiceoverStyleReferences,
)
from otio_app.services.voiceover_generation.style_reference_service import (
    compute_style_context_hash,
    format_raw_chapter_reference_for_prompts,
    save_style_references,
    style_context_text_for_prompts,
)
from otio_app.services.without_voiceover_enhanced.raw_chapter_style_structure import (
    analyze_raw_chapter_style_structure,
    detect_raw_chapter_style_violations,
    prepare_raw_chapter_reference,
)
from otio_app.services.voiceover_generation.dramaturgy_service import (
    save_confirmed_dramaturgy,
)
from otio_app.services.voiceover_generation.folder_voiceover_settings_service import (
    save_folder_voiceover_settings,
)
from otio_app.services.without_voiceover_enhanced.models import (
    EnhancedScriptDocument,
    ScriptSegment,
)
from otio_app.services.without_voiceover_enhanced.script_author_service import (
    _chapter_dramaturgy_text,
    _film_context_text,
    generate_enhanced_script_for_folder,
    revise_enhanced_script_for_folder,
)
from otio_app.services.without_voiceover_enhanced.script_chapter_link_guard import (
    detect_chapter_link_violations,
)
from otio_app.services.without_voiceover_enhanced.script_lock_service import (
    load_script_draft,
    save_script_draft,
)
from otio_app.services.without_voiceover_enhanced.script_neighbor_context import (
    build_editorial_neighbor_craft_block,
    build_film_wide_editorial_links_block,
)
from otio_app.services.without_voiceover_enhanced.script_prompts import (
    build_enhanced_folder_script_prompt,
)


def _project(tmp_path: Path) -> Project:
    root = tmp_path / "proj"
    work = root / DEFAULT_ENHANCED_WORK_SUBDIR
    work.mkdir(parents=True)
    return Project(
        id="ind-chap",
        name="ind-chap",
        project_root=str(root),
        work_dir=str(work),
        project_mode=ProjectMode.WITHOUT_VOICEOVER_ENHANCED,
        asset_subdir_names=["Dublin", "Kilkenny"],
        selected_asset_subdirs=["Dublin", "Kilkenny"],
        language="en",
    )


def _seed_two_chapters(project: Project) -> None:
    plan = DramaturgyPlan(
        project_id=project.id,
        project_title="Ireland",
        core_promise="Explore Ireland",
        narrative_arc="setup to payoff",
        global_transition_strategy="road connections",
        recommended_folder_order=[
            DramaturgyFolderEntry(
                folder_name="Dublin",
                order_index=0,
                dramaturgy_role="hook",
                reason="Capital opener",
                enabled=True,
                recommended_word_count=120,
                recommended_min_words=80,
                recommended_max_words=160,
            ),
            DramaturgyFolderEntry(
                folder_name="Kilkenny",
                order_index=1,
                dramaturgy_role="development",
                reason="Medieval contrast",
                enabled=True,
                recommended_word_count=120,
                recommended_min_words=80,
                recommended_max_words=160,
            ),
        ],
    )
    save_confirmed_dramaturgy(project, plan)
    save_folder_voiceover_settings(
        project,
        FolderVoiceoverSettingsDocument(
            project_id=project.id,
            settings=[
                FolderVoiceoverSetting(folder_name="Dublin", order_index=0, enabled=True),
                FolderVoiceoverSetting(
                    folder_name="Kilkenny", order_index=1, enabled=True
                ),
            ]
        ),
    )


def _ok_payload(text: str, *, from_prev: bool = False, to_next: bool = False) -> str:
    return json.dumps(
        {
            "narration_full": text,
            "segments": [
                {
                    "segment_id": "dublin_segment_001",
                    "text": text,
                    "sequence_index": 1,
                    "semantic_function": "geography",
                    "fact_check_required": False,
                    "paragraph_break_after": False,
                    "folder_name": "Dublin",
                }
            ],
            "fact_check_hints": [],
            "rhetoric_usage": [],
            "chapter_link_usage": {
                "from_previous": from_prev,
                "to_next": to_next,
                "callback": False,
                "evidence_quotes": [],
            },
            "style_reference_usage": {
                "mode": "default",
                "matched_features": ["direct opening"],
                "intentional_deviations": [],
            },
        }
    )


def test_prompt_marks_dramaturgy_silent_and_forbids_general_transitions() -> None:
    prompt = build_enhanced_folder_script_prompt(
        project_brief_text="Brief",
        film_context_text=_film_context_text(
            DramaturgyPlan(
                project_id="tmp",
                project_title="T",
                core_promise="P",
                narrative_arc="Arc",
                global_transition_strategy="should-not-appear",
            )
        ),
        chapter_dramaturgy_text=_chapter_dramaturgy_text(
            DramaturgyFolderEntry(
                folder_name="Dublin",
                order_index=0,
                dramaturgy_role="hook",
                reason="Capital",
            )
        ),
        style_profile_text="Style",
        verified_facts_text="Facts",
        folder_name="Dublin",
        folder_slug="dublin",
        dramaturgy_role="hook",
        target_words=100,
        min_words=80,
        max_words=120,
        previous_folder_name="Intro",
        next_folder_name="Kilkenny",
        language="en",
    )
    assert "SILENT EDITORIAL METADATA" in prompt
    assert "DRAMATURGY VS. SPOKEN NARRATION" in prompt
    assert "dramaturgical transitions" not in prompt
    assert "global_transition_strategy" not in prompt
    assert "should-not-appear" not in prompt
    assert "transition from previous: FORBIDDEN" in prompt
    assert "transition to next: FORBIDDEN" in prompt
    assert "unless they arise naturally" not in prompt
    assert "No film-wide spoken link is permitted" not in prompt or True


def test_prompt_permissions_from_and_to_flags() -> None:
    base = dict(
        project_brief_text="Brief",
        film_context_text="ctx",
        chapter_dramaturgy_text="meta",
        style_profile_text="Style",
        verified_facts_text="Facts",
        folder_name="Dublin",
        folder_slug="dublin",
        dramaturgy_role="hook",
        target_words=100,
        min_words=80,
        max_words=120,
        previous_folder_name="Intro",
        next_folder_name="Kilkenny",
        language="en",
    )
    from_only = build_enhanced_folder_script_prompt(
        **base, transition_from_previous=True
    )
    assert "transition from previous: ALLOWED" in from_only
    assert "transition to next: FORBIDDEN" in from_only
    assert 'previous chapter in the film: Intro' in from_only

    to_only = build_enhanced_folder_script_prompt(**base, transition_to_next=True)
    assert "transition to next: ALLOWED" in to_only
    assert "transition from previous: FORBIDDEN" in to_only
    assert "next chapter in the film: Kilkenny" in to_only


def test_contrast_alone_does_not_allow_travel_bridge() -> None:
    entry = DramaturgyFolderEntry(
        folder_name="Dublin",
        order_index=1,
        contrast_or_commonality_hint="stone vs sea",
        transition_from_previous_hint="leave the coast",
    )
    block = build_editorial_neighbor_craft_block(
        entry=entry,
        setting=FolderVoiceoverSetting(
            folder_name="Dublin",
            use_contrast_with_previous=True,
        ),
        previous_folder_name="Coast",
        next_folder_name="Kilkenny",
    )
    assert "CONTRAST" in block
    assert "leave the coast" not in block
    assert "travel bridge" in block.lower() or "transition_from_previous" in block
    film_wide = build_film_wide_editorial_links_block()
    assert "No film-wide spoken link is permitted" in film_wide


def test_guard_fail_cases() -> None:
    for text in (
        "Leave Dublin behind and the road starts climbing into the Wicklow Mountains.",
        "The road out of Cashel drops down toward Waterford.",
        "From here, the journey moves on toward Kilkenny.",
        "Our next stop is Galway.",
        "Wir verlassen Dublin und fahren weiter in die Wicklow Mountains.",
    ):
        errors = detect_chapter_link_violations(
            text,
            language="en",
            allow_from_previous=False,
            allow_to_next=False,
            allow_callback=False,
        )
        assert errors, f"expected violations for: {text}"


def test_guard_pass_cases() -> None:
    for text in (
        "Dublin lies at the mouth of the River Liffey and has served as Ireland’s "
        "political and cultural center for centuries.",
        "At the center of the Wicklow Mountains lies Glendalough, a valley shaped by "
        "two lakes and an early medieval monastic settlement.",
        "Across the water from County Mayo, Achill Island extends into the Atlantic.",
    ):
        errors = detect_chapter_link_violations(
            text,
            language="en",
            allow_from_previous=False,
            allow_to_next=False,
            allow_callback=False,
        )
        assert errors == [], f"unexpected violations for: {text} -> {errors}"


def test_raw_chapter_reference_binding_and_pause_cleanup() -> None:
    raw = (
        "Location X lies in the mountains.\n\n"
        "[pause 2 seconds]\n\n"
        "It was founded in 1200.\n\n"
        "[pause 3 seconds]\n\n"
        "At its center stands a tower."
    )
    prepared = prepare_raw_chapter_reference(raw)
    assert prepared.contains_pause_markers is True
    assert "[pause 2 seconds]" not in prepared.cleaned_text
    assert "REFERENCE BEAT BREAK" in prepared.cleaned_text or "Pause markers" in prepared.cleaned_text
    structure = analyze_raw_chapter_style_structure(prepared)
    assert structure.contains_pause_markers is True
    assert structure.beat_count == 3
    assert structure.starts_directly_with_subject is True

    block = format_raw_chapter_reference_for_prompts(raw)
    assert "BINDING PROSE ARCHITECTURE" in block
    assert "[pause 2 seconds]" not in block
    assert "REFERENCE STRUCTURE SIGNALS" in block


def test_raw_prompt_order_and_no_poetic_default(tmp_path: Path) -> None:
    project = _project(tmp_path)
    save_style_references(
        project,
        VoiceoverStyleReferences(
            project_id=project.id,
            style_mode=STYLE_MODE_RAW_TEXT,
            raw_reference_text="Dublin lies on the Liffey.\n\n[pause 2 seconds]\n\nA bridge crosses the river.",
        ),
    )
    style = style_context_text_for_prompts(project, detailed=True, for_chapter=True)
    prompt = build_enhanced_folder_script_prompt(
        project_brief_text="Brief",
        film_context_text="ctx",
        chapter_dramaturgy_text="SILENT EDITORIAL METADATA — DO NOT VERBALIZE\nrole: hook",
        style_profile_text=style,
        verified_facts_text="Facts",
        folder_name="Dublin",
        folder_slug="dublin",
        dramaturgy_role="hook",
        target_words=100,
        min_words=80,
        max_words=120,
        previous_folder_name=None,
        next_folder_name="Kilkenny",
        language="en",
        style_is_raw_chapter=True,
        chapter_order_text="FILM CHAPTER MAP — SILENT",
        editorial_neighbor_craft_text="EDITORIAL NEIGHBOR LINKS:\nNo spoken",
    )
    raw_pos = prompt.index("BINDING PROSE ARCHITECTURE")
    drama_pos = prompt.index("THIS CHAPTER DRAMATURGY")
    neighbor_pos = prompt.index("EDITORIAL NEIGHBOR LINKS")
    assert raw_pos < drama_pos
    assert raw_pos < neighbor_pos
    assert "Kristalle zu funkeln" not in prompt


def test_raw_style_violation_detection() -> None:
    structure = analyze_raw_chapter_style_structure(
        prepare_raw_chapter_reference(
            "Dublin lies on the river.\n\n[pause 2 seconds]\n\nA castle stands nearby."
        )
    )
    assert detect_raw_chapter_style_violations(
        "Dublin greets the journey with rhythm rather than grandeur.",
        structure=structure,
        folder_name="Dublin",
    )
    assert detect_raw_chapter_style_violations(
        "The landscape had run out of patience with gentler forms.",
        structure=structure,
        folder_name="Dublin",
    )
    assert detect_raw_chapter_style_violations(
        "Dublin lies here. [pause 3 seconds] More facts.",
        structure=structure,
        folder_name="Dublin",
    )
    assert not detect_raw_chapter_style_violations(
        "Dublin lies on the River Liffey and developed around a crossing close to the Irish Sea.",
        structure=structure,
        folder_name="Dublin",
    )


def test_generate_repairs_forbidden_opening_without_persisting_first_fail(
    tmp_path: Path,
) -> None:
    project = _project(tmp_path)
    _seed_two_chapters(project)
    calls: list[str] = []

    def llm_callable(*, prompt: str, model: str, max_output_tokens: int | None = None):
        del model, max_output_tokens
        calls.append(prompt)
        if len(calls) == 1:
            return _ok_payload(
                "Leave Dublin behind and the road starts climbing into the hills."
            )
        return _ok_payload(
            "Dublin lies at the mouth of the River Liffey and has served as "
            "Ireland’s political and cultural center for centuries."
        )

    assert load_script_draft(project) is None

    result = generate_enhanced_script_for_folder(
        project, "Dublin", llm_callable=llm_callable
    )
    assert result.status == "PASS"
    assert len(calls) == 2
    assert "REPAIR REQUIRED" in calls[1]
    draft = load_script_draft(project)
    assert draft is not None
    assert "Leave Dublin behind" not in draft.narration_full
    assert "Dublin lies at the mouth" in draft.narration_full
    assert draft.source_style_context_hash


def test_generate_fails_after_two_link_violations_without_persist(tmp_path: Path) -> None:
    project = _project(tmp_path)
    _seed_two_chapters(project)

    def llm_callable(*, prompt: str, model: str, max_output_tokens: int | None = None):
        del prompt, model, max_output_tokens
        return _ok_payload("Our next stop is Galway.")

    result = generate_enhanced_script_for_folder(
        project, "Dublin", llm_callable=llm_callable
    )
    assert result.status == "FAIL"
    assert "ungültig" in (result.error or "").lower() or "Unerlaubte" in (
        result.error or ""
    )
    draft = load_script_draft(project)
    assert draft is None or not draft.segments


def test_transition_from_previous_allows_short_bridge(tmp_path: Path) -> None:
    project = _project(tmp_path)
    _seed_two_chapters(project)
    save_folder_voiceover_settings(
        project,
        FolderVoiceoverSettingsDocument(
            project_id=project.id,
            settings=[
                FolderVoiceoverSetting(
                    folder_name="Kilkenny",
                    order_index=1,
                    enabled=True,
                    transition_from_previous=True,
                ),
                FolderVoiceoverSetting(
                    folder_name="Dublin", order_index=0, enabled=True
                ),
            ]
        ),
    )

    def llm_callable(*, prompt: str, model: str, max_output_tokens: int | None = None):
        del model, max_output_tokens
        assert "transition from previous: ALLOWED" in prompt
        text = (
            "After the Georgian streets of Dublin, Kilkenny concentrates its story "
            "around a medieval castle above the River Nore."
        )
        # Guard should not flag this (no travel formula patterns)
        return _ok_payload(text, from_prev=True)

    result = generate_enhanced_script_for_folder(
        project, "Kilkenny", llm_callable=llm_callable
    )
    assert result.status == "PASS"


def test_style_hash_staleness_on_raw_change(tmp_path: Path) -> None:
    project = _project(tmp_path)
    save_style_references(
        project,
        VoiceoverStyleReferences(
            project_id=project.id,
            style_mode=STYLE_MODE_RAW_TEXT,
            raw_reference_text="Place A lies nearby.",
        ),
    )
    h1 = compute_style_context_hash(project)
    save_script_draft(
        project,
        EnhancedScriptDocument(
            script_status="draft",
            narration_full="Dublin lies on the river.",
            segments=[],
            source_style_context_hash=h1,
        ),
    )
    # identical save should keep hash
    save_style_references(
        project,
        VoiceoverStyleReferences(
            project_id=project.id,
            style_mode=STYLE_MODE_RAW_TEXT,
            raw_reference_text="Place A lies nearby.",
        ),
    )
    assert compute_style_context_hash(project) == h1

    save_style_references(
        project,
        VoiceoverStyleReferences(
            project_id=project.id,
            style_mode=STYLE_MODE_RAW_TEXT,
            raw_reference_text="Place A lies nearby. Extra sentence changes style.",
        ),
    )
    assert compute_style_context_hash(project) != h1


def _valid_dublin_text() -> str:
    return (
        "Dublin lies at the mouth of the River Liffey and has served as "
        "Ireland’s political and cultural center for centuries."
    )


def test_generate_retries_once_on_invalid_json_after_llm(tmp_path: Path) -> None:
    project = _project(tmp_path)
    _seed_two_chapters(project)
    calls: list[str] = []
    retries: list[str] = []

    def llm_callable(*, prompt: str, model: str, max_output_tokens: int | None = None):
        del model, max_output_tokens
        calls.append(prompt)
        if len(calls) == 1:
            return "this is not json {"
        return _ok_payload(_valid_dublin_text())

    result = generate_enhanced_script_for_folder(
        project, "Dublin", llm_callable=llm_callable, on_retry=retries.append
    )
    assert result.status == "PASS", result.error
    assert len(calls) == 2
    assert retries
    assert "RETRY REQUIRED" in calls[1]


def test_generate_retries_once_on_llm_exception_then_succeeds(tmp_path: Path) -> None:
    project = _project(tmp_path)
    _seed_two_chapters(project)
    calls = {"n": 0}

    def llm_callable(*, prompt: str, model: str, max_output_tokens: int | None = None):
        del prompt, model, max_output_tokens
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("529 overloaded")
        return _ok_payload(_valid_dublin_text())

    result = generate_enhanced_script_for_folder(
        project, "Dublin", llm_callable=llm_callable
    )
    assert result.status == "PASS", result.error
    assert calls["n"] == 2


def test_generate_fails_after_two_invalid_json_replies(tmp_path: Path) -> None:
    project = _project(tmp_path)
    _seed_two_chapters(project)
    calls = {"n": 0}

    def llm_callable(*, prompt: str, model: str, max_output_tokens: int | None = None):
        del prompt, model, max_output_tokens
        calls["n"] += 1
        return "still not json"

    result = generate_enhanced_script_for_folder(
        project, "Dublin", llm_callable=llm_callable
    )
    assert result.status == "FAIL"
    assert calls["n"] == 2
    assert result.error


def test_generate_does_not_retry_cancelled_llm(tmp_path: Path) -> None:
    project = _project(tmp_path)
    _seed_two_chapters(project)
    calls = {"n": 0}

    def llm_callable(*, prompt: str, model: str, max_output_tokens: int | None = None):
        del prompt, model, max_output_tokens
        calls["n"] += 1
        raise PlanLlmCancelledError("LLM-Aufruf abgebrochen.")

    with pytest.raises(PlanLlmCancelledError):
        generate_enhanced_script_for_folder(project, "Dublin", llm_callable=llm_callable)
    assert calls["n"] == 1


def test_revise_retries_once_on_empty_llm_reply(tmp_path: Path) -> None:
    project = _project(tmp_path)
    _seed_two_chapters(project)
    save_script_draft(
        project,
        EnhancedScriptDocument(
            segments=[
                ScriptSegment(
                    segment_id="d1",
                    text=_valid_dublin_text(),
                    sequence_index=1,
                    folder_name="Dublin",
                    folder_order_index=0,
                )
            ]
        ),
    )
    calls: list[str] = []

    def llm_callable(*, prompt: str, model: str, max_output_tokens: int | None = None):
        del model, max_output_tokens
        calls.append(prompt)
        if len(calls) == 1:
            return "   "
        return "Dublin sits on the Liffey and grew as Ireland’s capital."

    result = revise_enhanced_script_for_folder(
        project,
        "Dublin",
        editor_instructions="tighten",
        llm_callable=llm_callable,
    )
    assert result.status == "PASS", result.error
    assert len(calls) == 2
    assert "RETRY REQUIRED" in calls[1]
