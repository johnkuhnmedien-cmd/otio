"""Kapitelende-CTAs: Dramaturgie-Planung, Normalisierung, Skript-Prompt."""

from __future__ import annotations

from otio_app.services.voiceover_generation.chapter_cta import (
    CTA_STAY_STYLE_REFERENCES_DE,
    apply_chapter_ctas,
    build_chapter_end_cta_prompt_block,
    build_dramaturgy_cta_task_block,
    like_cta_spoken_line,
    normalize_chapter_ctas_after_llm,
    sanitize_chapter_ctas,
)
from otio_app.services.voiceover_generation.models import DramaturgyFolderEntry
from otio_app.services.voiceover_generation.prompts import (
    build_dramaturgy_prompt,
    build_folder_voiceover_prompt,
)
from otio_app.services.without_voiceover_enhanced.script_chapter_link_guard import (
    detect_chapter_link_violations,
)
from otio_app.services.without_voiceover_enhanced.script_prompts import (
    build_enhanced_folder_script_prompt,
)
from tests.test_voiceover_generation_prompts import (
    _sample_brief,
    _sample_dramaturgy_entry,
    _sample_folder_summaries,
    _sample_inventory_assets,
    _sample_setting,
    _sample_style_profile,
)


def _entry(
    name: str,
    order: int,
    *,
    enabled: bool = True,
    like: bool = False,
    stay: bool = False,
    stay_text: str = "",
    targets: list[str] | None = None,
) -> DramaturgyFolderEntry:
    return DramaturgyFolderEntry(
        folder_name=name,
        order_index=order,
        enabled=enabled,
        cta_like=like,
        cta_stay=stay,
        cta_stay_text=stay_text,
        cta_stay_target_folders=targets or [],
    )


def test_like_template_inserts_next_chapter_name() -> None:
    line = like_cta_spoken_line("de", "Kilkenny")
    assert "Kilkenny" in line
    assert "Like" in line
    english = like_cta_spoken_line("en", "Kilkenny")
    assert "Kilkenny" in english
    assert "like" in english.lower()


def test_normalize_assigns_like_and_stay_on_non_last_chapters() -> None:
    entries = [
        _entry("A", 1),
        _entry("B", 2),
        _entry("C", 3),
        _entry("D", 4),
    ]
    result = normalize_chapter_ctas_after_llm(entries)
    by_name = {entry.folder_name: entry for entry in result}
    assert by_name["D"].cta_like is False
    assert by_name["D"].cta_stay is False
    likes = [name for name, entry in by_name.items() if entry.cta_like]
    stays = [name for name, entry in by_name.items() if entry.cta_stay]
    assert likes == [likes[0]]
    assert stays == [stays[0]]
    assert likes[0] != "D"
    assert stays[0] != "D"


def test_normalize_keeps_llm_stay_text_and_drops_last_chapter_cta() -> None:
    stay_line = CTA_STAY_STYLE_REFERENCES_DE[0]
    entries = [
        _entry("A", 1, like=True),
        _entry(
            "B",
            2,
            stay=True,
            stay_text=stay_line,
            targets=["C", "D"],
        ),
        _entry("C", 3, like=True, stay=True, stay_text="should not win"),
        _entry("D", 4, like=True, stay=True, stay_text="last must be cleared"),
    ]
    result = normalize_chapter_ctas_after_llm(entries)
    by_name = {entry.folder_name: entry for entry in result}
    assert by_name["A"].cta_like is True
    assert by_name["A"].cta_stay is False
    assert by_name["B"].cta_stay is True
    assert by_name["B"].cta_stay_text == stay_line
    assert by_name["B"].cta_stay_target_folders == ["C", "D"]
    assert by_name["C"].cta_like is False
    assert by_name["C"].cta_stay is False
    assert by_name["D"].cta_like is False
    assert by_name["D"].cta_stay is False
    assert by_name["D"].cta_stay_text == ""


def test_sanitize_does_not_invent_ctas_but_strips_last_chapter() -> None:
    entries = [
        _entry("A", 1),
        _entry("B", 2, like=True, stay=True, stay_text="Hanglage."),
        _entry("C", 3, like=True, stay=True, stay_text="last"),
    ]
    result = sanitize_chapter_ctas(entries)
    by_name = {entry.folder_name: entry for entry in result}
    assert by_name["A"].cta_like is False
    assert by_name["A"].cta_stay is False
    assert by_name["B"].cta_like is True
    assert by_name["B"].cta_stay is True
    assert by_name["B"].cta_stay_text == "Hanglage."
    assert by_name["C"].cta_like is False
    assert by_name["C"].cta_stay is False
    assert by_name["C"].cta_stay_text == ""


def test_single_chapter_gets_no_ctas() -> None:
    result = normalize_chapter_ctas_after_llm([_entry("Only", 1, like=True, stay=True)])
    assert result[0].cta_like is False
    assert result[0].cta_stay is False


def test_two_chapters_put_both_ctas_on_first() -> None:
    result = normalize_chapter_ctas_after_llm([_entry("First", 1), _entry("Last", 2)])
    by_name = {entry.folder_name: entry for entry in result}
    assert by_name["First"].cta_like is True
    assert by_name["First"].cta_stay is True
    assert by_name["Last"].cta_like is False


def test_prompt_block_empty_when_film_has_no_ctas() -> None:
    entry = _entry("Dublin", 1)
    block = build_chapter_end_cta_prompt_block(
        entry=entry,
        entries=[entry, _entry("Kilkenny", 2)],
        next_folder_name="Kilkenny",
        language="de",
    )
    assert block == ""


def test_prompt_block_speaks_stay_then_like() -> None:
    stay_text = "Später sehen wir einen Ort, an dem die Häuser dicht an einem steilen Hang gebaut wurden. Allein die Lage ist ziemlich verrückt!"
    dublin = _entry("Dublin", 1, like=True, stay=True, stay_text=stay_text, targets=["Kilkenny"])
    kilkenny = _entry("Kilkenny", 2)
    block = build_chapter_end_cta_prompt_block(
        entry=dublin,
        entries=[dublin, kilkenny],
        next_folder_name="Kilkenny",
        language="de",
    )
    assert stay_text in block
    assert "Kilkenny" in block
    assert "Like" in block
    assert "stay tease" in block
    assert "like-ask" in block
    assert "chapter_link_usage.to_next" in block


def test_prompt_block_forbids_other_chapters() -> None:
    dublin = _entry("Dublin", 1, stay=True, stay_text="Dranbleiben lohnt sich hier wirklich!")
    kilkenny = _entry("Kilkenny", 2)
    cork = _entry("Cork", 3)
    block = build_chapter_end_cta_prompt_block(
        entry=kilkenny,
        entries=[dublin, kilkenny, cork],
        next_folder_name="Cork",
        language="de",
    )
    assert "no CTA assigned" in block
    assert "Do NOT add a like-ask" in block
    assert "stay_tuned_payoff" in block


def test_dramaturgy_prompt_contains_stay_references_and_cta_fields() -> None:
    prompt = build_dramaturgy_prompt(
        project_brief=_sample_brief(),
        style_profile=_sample_style_profile(),
        folder_summaries=_sample_folder_summaries(),
    )
    assert "cta_like" in prompt
    assert "cta_stay" in prompt
    assert "cta_stay_text" in prompt
    assert "cta_stay_target_folders" in prompt
    for sample in CTA_STAY_STYLE_REFERENCES_DE:
        assert sample in prompt
    assert "Dranbleiben lohnt sich hier wirklich!" in prompt
    assert "use_transition_from_previous" not in prompt
    assert build_dramaturgy_cta_task_block() in prompt


def test_enhanced_script_prompt_includes_stay_and_like() -> None:
    stay_text = "Gegen Ende geht es zu einem Ort, bei dem schon die Anfahrt fast spektakulärer ist als das Ziel selbst. Nicht verpassen!"
    dublin = _entry("Dublin", 1, like=True, stay=True, stay_text=stay_text)
    block = build_chapter_end_cta_prompt_block(
        entry=dublin,
        entries=[dublin, _entry("Kilkenny", 2)],
        next_folder_name="Kilkenny",
        language="de",
    )
    prompt = build_enhanced_folder_script_prompt(
        project_brief_text="Brief",
        film_context_text="ctx",
        chapter_dramaturgy_text="SILENT",
        style_profile_text="Style",
        verified_facts_text="Facts",
        folder_name="Dublin",
        folder_slug="dublin",
        dramaturgy_role="setup",
        target_words=100,
        min_words=80,
        max_words=120,
        previous_folder_name=None,
        next_folder_name="Kilkenny",
        language="de",
        chapter_end_cta_text=block,
    )
    assert stay_text in prompt
    assert 'Bevor es mit "Kilkenny" weitergeht' in prompt
    assert "EXTRA words" in prompt
    assert "cta_stay" in prompt
    assert "cta_like" in prompt


def test_classic_voiceover_prompt_includes_stay_cta() -> None:
    stay_text = "Später kommen wir zu einem kleinen Ort, der so abgeschieden liegt, dass ihn viele Reisende einfach auslassen. Dranbleiben lohnt sich hier wirklich!"
    entry = _sample_dramaturgy_entry().model_copy(
        update={
            "cta_stay": True,
            "cta_stay_text": stay_text,
            "cta_stay_target_folders": ["Yellowstone"],
        }
    )
    later = DramaturgyFolderEntry(folder_name="Yellowstone", order_index=2, enabled=True)
    prompt = build_folder_voiceover_prompt(
        project_brief=_sample_brief(),
        style_profile=_sample_style_profile(),
        dramaturgy_entry=entry,
        setting=_sample_setting(),
        previous_folder_name=None,
        next_folder_name="Yellowstone",
        inventory_assets=_sample_inventory_assets(),
        all_dramaturgy_entries=[entry, later],
    )
    assert stay_text in prompt
    assert "CHAPTER-END CTAs" in prompt


def test_guard_ignores_tail_to_next_for_cta_closing() -> None:
    text = (
        "Dublin lies at the mouth of the Liffey. "
        "The city has been a political center for centuries. "
        "Our next stop is Kilkenny after a like."
    )
    blocked = detect_chapter_link_violations(
        text, language="en", allow_to_next=False, ignore_tail_to_next=False
    )
    allowed = detect_chapter_link_violations(
        text, language="en", allow_to_next=False, ignore_tail_to_next=True
    )
    assert blocked
    assert allowed == []


def test_apply_chapter_ctas_fill_targets_from_later_folders() -> None:
    entries = [
        _entry("A", 1, stay=True, stay_text="Hanglage."),
        _entry("B", 2),
        _entry("C", 3),
    ]
    result = apply_chapter_ctas(entries, assign_if_missing=False)
    stay = next(entry for entry in result if entry.cta_stay)
    assert stay.folder_name == "A"
    assert stay.cta_stay_target_folders == ["B", "C"]
