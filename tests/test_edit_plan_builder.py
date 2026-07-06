"""Tests für Schnittplan-Erstellung."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from otio_app.analysis_models import (
    AssetFolderAnalysis,
    AssetMediaAnalysis,
    EditPlanRule,
    EditPlanRulesDocument,
    EditPlanSettings,
    InventoryDocument,
    VoiceAnalysisDocument,
    VoiceFileAnalysis,
    VoiceFolderMappingDocument,
    VoiceFolderMappingEntry,
    VoiceSegment,
)
from otio_app.models import Project
from otio_app.services.edit_plan_rules import RULE_MAX_ASSET_USES
from otio_app.services.edit_plan_builder import build_edit_plan
from otio_app.services.asset_usage import usage_count_by_asset_id_from_shots


def _sample_project(layout: dict[str, Path]) -> Project:
    return Project(
        id="plan-test",
        name="Test",
        project_root=str(layout["project_root"]),
        work_dir=str(layout["work_dir"]),
        asset_subdir_names=["Grand Canyon"],
        selected_asset_subdirs=["Grand Canyon"],
    )


def test_build_edit_plan_without_gemini(
    temp_project_layout: dict[str, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = _sample_project(temp_project_layout)
    voice_path = str(temp_project_layout["voice_file"])
    media_path = str(temp_project_layout["project_root"] / "Grand Canyon" / "clip.mp4")

    mapping = VoiceFolderMappingDocument(
        project_id=project.id,
        confirmed=True,
        entries=[
            VoiceFolderMappingEntry(
                voice_file=voice_path,
                folder="Grand Canyon",
                confirmed=True,
            )
        ],
    )
    project.voice_folder_mapping_path.write_text(
        mapping.model_dump_json(indent=2),
        encoding="utf-8",
    )

    voice_doc = VoiceAnalysisDocument(
        project_id=project.id,
        language="de",
        files=[
            VoiceFileAnalysis(
                path=voice_path,
                segments=[
                    VoiceSegment(
                        start_sec=0.0,
                        end_sec=10.0,
                        text="Der Canyon ist riesig, und der Fluss ist tief.",
                    )
                ],
            )
        ],
    )
    project.voice_analysis_path.write_text(
        voice_doc.model_dump_json(indent=2),
        encoding="utf-8",
    )

    (temp_project_layout["project_root"] / "Grand Canyon" / "broll.mp4").write_bytes(b"mp4")

    inventory = InventoryDocument(
        project_id=project.id,
        items=[
            AssetFolderAnalysis(
                folder="Grand Canyon",
                assets=[
                    AssetMediaAnalysis(
                        path=media_path,
                        description="Steile Felswand und Fluss",
                    ),
                    AssetMediaAnalysis(
                        path=str(temp_project_layout["project_root"] / "Grand Canyon" / "broll.mp4"),
                        description="Ruhige Landschaft establishing overview",
                    ),
                ],
            )
        ],
    )
    from otio_app.services.inventory_loader import save_folder_inventory

    save_folder_inventory(
        project.folder_inventory_path("Grand Canyon"),
        inventory.items[0],
    )

    document = build_edit_plan(project, use_api=False)
    assert document.shots
    assert document.shots[0].folder == "Grand Canyon"
    assert all(shot.duration_sec >= 3.0 for shot in document.shots if not shot.section_outro)
    assert document.shots[-1].section_outro is True
    assert document.shots[-1].duration_sec == 5.0
    assert document.shots[-1].motif == "Ausklingen"
    # Regression: Ohne explizit übergebene settings darf build_edit_plan
    # NICHT auf ein anderes Gemini-Modell zurückfallen als der App-weite
    # Standard (vorher hardcoded "gemini-2.0-flash" in EditPlanSettings).
    from otio_app.services.gemini_client import get_default_gemini_model

    assert document.settings.gemini_model == get_default_gemini_model()


def test_build_edit_plan_calls_progress_callback_per_segment(
    temp_project_layout: dict[str, Path],
) -> None:
    """Regression: Bei vielen Voice-over-Segmenten (v.a. mit langsameren
    Gemini-Modellen wie 'Pro Preview') kann 'Schnittplan vorschlagen'
    mehrere Minuten dauern, ohne dass die UI Fortschritt anzeigt. Ein
    optionaler progress_callback erlaubt es der UI, den Fortschritt pro
    Segment sichtbar zu machen."""
    project = _sample_project(temp_project_layout)
    voice_path = str(temp_project_layout["voice_file"])
    media_path = str(temp_project_layout["project_root"] / "Grand Canyon" / "clip.mp4")

    mapping = VoiceFolderMappingDocument(
        project_id=project.id,
        confirmed=True,
        entries=[
            VoiceFolderMappingEntry(voice_file=voice_path, folder="Grand Canyon", confirmed=True)
        ],
    )
    project.voice_folder_mapping_path.write_text(mapping.model_dump_json(indent=2), encoding="utf-8")

    voice_doc = VoiceAnalysisDocument(
        project_id=project.id,
        language="de",
        files=[
            VoiceFileAnalysis(
                path=voice_path,
                segments=[
                    VoiceSegment(start_sec=0.0, end_sec=5.0, text="Erstes Segment."),
                    VoiceSegment(start_sec=5.0, end_sec=10.0, text="Zweites Segment."),
                    VoiceSegment(start_sec=10.0, end_sec=12.0, text="   "),  # leer -> übersprungen
                ],
            )
        ],
    )
    project.voice_analysis_path.write_text(voice_doc.model_dump_json(indent=2), encoding="utf-8")
    (temp_project_layout["project_root"] / "Grand Canyon" / "clip.mp4").write_bytes(b"mp4")

    from otio_app.services.inventory_loader import save_folder_inventory

    save_folder_inventory(
        project.folder_inventory_path("Grand Canyon"),
        AssetFolderAnalysis(
            folder="Grand Canyon",
            assets=[AssetMediaAnalysis(path=media_path, description="Canyon", asset_id="asset_clip")],
        ),
    )

    progress_calls: list[tuple[str, int, int]] = []
    build_edit_plan(
        project,
        use_api=False,
        progress_callback=lambda folder, index, total: progress_calls.append((folder, index, total)),
    )

    assert progress_calls == [
        ("Grand Canyon", 1, 2),
        ("Grand Canyon", 2, 2),
    ]


def test_build_edit_plan_falls_back_when_gemini_network_fails(
    temp_project_layout: dict[str, Path],
) -> None:
    project = _sample_project(temp_project_layout)
    voice_path = str(temp_project_layout["voice_file"])
    media_path = str(temp_project_layout["project_root"] / "Grand Canyon" / "clip.mp4")

    mapping = VoiceFolderMappingDocument(
        project_id=project.id,
        confirmed=True,
        entries=[
            VoiceFolderMappingEntry(
                voice_file=voice_path,
                folder="Grand Canyon",
                confirmed=True,
            )
        ],
    )
    project.voice_folder_mapping_path.write_text(mapping.model_dump_json(indent=2), encoding="utf-8")
    voice_doc = VoiceAnalysisDocument(
        project_id=project.id,
        language="de",
        files=[
            VoiceFileAnalysis(
                path=voice_path,
                segments=[
                    VoiceSegment(
                        start_sec=0.0,
                        end_sec=5.0,
                        text="Der Canyon ist eng und hell.",
                    )
                ],
            )
        ],
    )
    project.voice_analysis_path.write_text(voice_doc.model_dump_json(indent=2), encoding="utf-8")

    from otio_app.services.inventory_loader import save_folder_inventory

    save_folder_inventory(
        project.folder_inventory_path("Grand Canyon"),
        AssetFolderAnalysis(
            folder="Grand Canyon",
            assets=[
                AssetMediaAnalysis(
                    path=media_path,
                    description="Enger Canyon mit Licht",
                    asset_id="asset_clip",
                )
            ],
        ),
    )

    from unittest.mock import patch

    with patch(
        "otio_app.services.edit_plan_builder.plan_passage_assets",
        side_effect=RuntimeError("network down"),
    ):
        document = build_edit_plan(project, use_api=True)

    assert document.shots
    assert document.shots[0].asset_path == media_path


def test_max_asset_usage_applies_after_timing_split(
    temp_project_layout: dict[str, Path],
) -> None:
    project = _sample_project(temp_project_layout)
    voice_path = str(temp_project_layout["voice_file"])
    media_path = str(temp_project_layout["project_root"] / "Grand Canyon" / "clip.mp4")

    mapping = VoiceFolderMappingDocument(
        project_id=project.id,
        confirmed=True,
        entries=[
            VoiceFolderMappingEntry(
                voice_file=voice_path,
                folder="Grand Canyon",
                confirmed=True,
            )
        ],
    )
    project.voice_folder_mapping_path.write_text(mapping.model_dump_json(indent=2), encoding="utf-8")

    voice_doc = VoiceAnalysisDocument(
        project_id=project.id,
        language="de",
        files=[
            VoiceFileAnalysis(
                path=voice_path,
                segments=[
                    VoiceSegment(
                        start_sec=0.0,
                        end_sec=10.0,
                        text="Ein langer Satz über Canyonwände und Licht im engen Durchgang.",
                    )
                ],
            )
        ],
    )
    project.voice_analysis_path.write_text(voice_doc.model_dump_json(indent=2), encoding="utf-8")

    from otio_app.services.inventory_loader import save_folder_inventory

    save_folder_inventory(
        project.folder_inventory_path("Grand Canyon"),
        AssetFolderAnalysis(
            folder="Grand Canyon",
            assets=[
                AssetMediaAnalysis(
                    path=media_path,
                    description="Canyonwände und Licht im engen Durchgang",
                    asset_id="asset_clip",
                )
            ],
        ),
    )

    rules = EditPlanRulesDocument(
        project_id=project.id,
        rules=[
            EditPlanRule(
                id="max1",
                rule_type=RULE_MAX_ASSET_USES,
                enabled=True,
                params={"max_count": 1},
                label="Max 1",
            )
        ],
    )
    settings = EditPlanSettings(
        shot_min_sec=3.0,
        shot_max_sec=5.0,
        section_outro_sec=0.0,
    )

    document = build_edit_plan(project, settings=settings, use_api=False, rules_doc=rules)
    counts = usage_count_by_asset_id_from_shots(
        [shot for shot in document.shots if not shot.section_outro]
    )
    assert counts.get("asset_clip", 0) == 1
    assert any(shot.asset_path is None for shot in document.shots)


def test_segment_coverage_reconciled_when_all_shots_get_local_asset(
    temp_project_layout: dict[str, Path],
) -> None:
    """Regression: die Beat-Coverage wird vorab nur grob geschätzt (Keyword-
    Heuristik auf den GESAMTEN Beat-Text). Wenn Gemini den Beat in mehrere
    Teile mit je einem passenden lokalen Asset aufteilt, darf die finale
    Coverage nicht mehr SUPPLEMENT_REQUIRED zeigen, obwohl alle Shots am Ende
    ein Asset bekommen haben — sonst entsteht der widersprüchliche Zustand
    '0 Shots ohne Asset' + 'N Beats offen'."""
    from unittest.mock import patch

    from otio_app.services.supplement_coverage import COVERAGE_LOCAL_GOOD, COVERAGE_SUPPLEMENT_REQUIRED

    project = _sample_project(temp_project_layout)
    voice_path = str(temp_project_layout["voice_file"])
    waterfall_path = str(temp_project_layout["project_root"] / "Grand Canyon" / "waterfall.mp4")
    eagle_path = str(temp_project_layout["project_root"] / "Grand Canyon" / "eagle.mp4")

    mapping = VoiceFolderMappingDocument(
        project_id=project.id,
        confirmed=True,
        entries=[
            VoiceFolderMappingEntry(voice_file=voice_path, folder="Grand Canyon", confirmed=True)
        ],
    )
    project.voice_folder_mapping_path.write_text(mapping.model_dump_json(indent=2), encoding="utf-8")

    combined_text = (
        "Ein Wasserfall stürzt tosend in die Tiefe, während in weiter Ferne "
        "ein Adler majestätisch über dem Tal kreist."
    )
    voice_doc = VoiceAnalysisDocument(
        project_id=project.id,
        language="de",
        files=[
            VoiceFileAnalysis(
                path=voice_path,
                segments=[VoiceSegment(start_sec=0.0, end_sec=10.0, text=combined_text)],
            )
        ],
    )
    project.voice_analysis_path.write_text(voice_doc.model_dump_json(indent=2), encoding="utf-8")

    (temp_project_layout["project_root"] / "Grand Canyon").mkdir(exist_ok=True)
    Path(waterfall_path).write_bytes(b"mp4")
    Path(eagle_path).write_bytes(b"mp4")

    from otio_app.services.inventory_loader import save_folder_inventory

    save_folder_inventory(
        project.folder_inventory_path("Grand Canyon"),
        AssetFolderAnalysis(
            folder="Grand Canyon",
            assets=[
                AssetMediaAnalysis(
                    path=waterfall_path,
                    description="Wasserfall stürzt tosend in die Tiefe",
                    asset_id="asset_waterfall",
                ),
                AssetMediaAnalysis(
                    path=eagle_path,
                    description="Adler kreist majestätisch über dem Tal",
                    asset_id="asset_eagle",
                ),
            ],
        ),
    )

    def fake_plan_passage_assets(passage_text, folder_name, assets, language, **kwargs):
        return [
            {
                "text": "Ein Wasserfall stürzt tosend in die Tiefe",
                "motif": "Wasserfall",
                "asset_path": waterfall_path,
                "confidence": "high",
            },
            {
                "text": "ein Adler majestätisch über dem Tal kreist",
                "motif": "Adler",
                "asset_path": eagle_path,
                "confidence": "high",
            },
        ]

    with patch(
        "otio_app.services.edit_plan_builder.plan_passage_assets",
        side_effect=fake_plan_passage_assets,
    ):
        document = build_edit_plan(project, use_api=True)

    non_outro_shots = [shot for shot in document.shots if not shot.section_outro]
    assert non_outro_shots
    assert all(shot.asset_path for shot in non_outro_shots), (
        "Alle Shots sollten ein lokales Asset bekommen haben."
    )
    assert document.segment_coverage, "Coverage sollte für den Beat berechnet worden sein."
    assert all(
        coverage.coverage_status != COVERAGE_SUPPLEMENT_REQUIRED
        for coverage in document.segment_coverage
    ), "Coverage darf nicht mehr SUPPLEMENT_REQUIRED zeigen, wenn alle Shots ein Asset haben."
    assert any(
        coverage.coverage_status == COVERAGE_LOCAL_GOOD for coverage in document.segment_coverage
    )
    assert not document.supplement_request_ids


def test_outro_items_respect_configured_max_shot_sec(
    temp_project_layout: dict[str, Path],
) -> None:
    """Regression: Das Ordner-Ausklingen wurde bisher immer in Blöcken à
    max. 8s (hardcoded Default) aufgeteilt — unabhängig davon, welchen
    Max.-Shot-Wert der Nutzer in den Timing-Regeln konfiguriert hat."""
    project = _sample_project(temp_project_layout)
    voice_path = str(temp_project_layout["voice_file"])
    media_path = str(temp_project_layout["project_root"] / "Grand Canyon" / "clip.mp4")
    broll_path = str(temp_project_layout["project_root"] / "Grand Canyon" / "broll.mp4")
    broll2_path = str(temp_project_layout["project_root"] / "Grand Canyon" / "broll2.mp4")

    mapping = VoiceFolderMappingDocument(
        project_id=project.id,
        confirmed=True,
        entries=[
            VoiceFolderMappingEntry(voice_file=voice_path, folder="Grand Canyon", confirmed=True)
        ],
    )
    project.voice_folder_mapping_path.write_text(mapping.model_dump_json(indent=2), encoding="utf-8")

    voice_doc = VoiceAnalysisDocument(
        project_id=project.id,
        language="de",
        files=[
            VoiceFileAnalysis(
                path=voice_path,
                segments=[VoiceSegment(start_sec=0.0, end_sec=6.0, text="Kurzer Text über den Canyon.")],
            )
        ],
    )
    project.voice_analysis_path.write_text(voice_doc.model_dump_json(indent=2), encoding="utf-8")

    Path(media_path).write_bytes(b"mp4")
    Path(broll_path).write_bytes(b"mp4")
    Path(broll2_path).write_bytes(b"mp4")

    from otio_app.services.inventory_loader import save_folder_inventory

    save_folder_inventory(
        project.folder_inventory_path("Grand Canyon"),
        AssetFolderAnalysis(
            folder="Grand Canyon",
            assets=[
                AssetMediaAnalysis(path=media_path, description="Canyon Weitwinkel", asset_id="asset_clip"),
                AssetMediaAnalysis(path=broll_path, description="Establishing Landschaft", asset_id="asset_broll"),
                AssetMediaAnalysis(path=broll2_path, description="Panorama Übersicht", asset_id="asset_broll2"),
            ],
        ),
    )

    settings = EditPlanSettings(shot_min_sec=2.0, shot_max_sec=5.0, section_outro_sec=10.0)
    document = build_edit_plan(project, settings=settings, use_api=False)

    outro_shots = [shot for shot in document.shots if shot.section_outro]
    assert outro_shots, "Es sollten Outro-Shots erzeugt worden sein."
    assert all(shot.duration_sec <= 5.0 + 0.01 for shot in outro_shots), (
        "Kein Outro-Shot darf die konfigurierte Max.-Shot-Regel (5.0s) verletzen."
    )


def test_local_fallback_picks_best_matching_asset_not_just_first(
    temp_project_layout: dict[str, Path],
) -> None:
    """Regression: Ohne Gemini (z.B. Netzwerkfehler) wurde bisher IMMER das
    erste Asset im Ordner gewählt, unabhängig vom Inhalt. Das führte u.a.
    dazu, dass frisch supplementierte Assets nie für ihre eigentliche Passage
    verwendet wurden (sie blieben "ungenutzt" und wurden stattdessen vom
    generischen Outro-Filler eingesammelt — wirkte wie eine feste Bindung an
    eine andere Stelle). Jetzt wird das inhaltlich beste Asset gewählt."""
    project = _sample_project(temp_project_layout)
    voice_path = str(temp_project_layout["voice_file"])
    irrelevant_path = str(temp_project_layout["project_root"] / "Grand Canyon" / "irrelevant.mp4")
    matching_path = str(temp_project_layout["project_root"] / "Grand Canyon" / "matching.mp4")

    mapping = VoiceFolderMappingDocument(
        project_id=project.id,
        confirmed=True,
        entries=[
            VoiceFolderMappingEntry(voice_file=voice_path, folder="Grand Canyon", confirmed=True)
        ],
    )
    project.voice_folder_mapping_path.write_text(mapping.model_dump_json(indent=2), encoding="utf-8")

    voice_doc = VoiceAnalysisDocument(
        project_id=project.id,
        language="de",
        files=[
            VoiceFileAnalysis(
                path=voice_path,
                segments=[
                    VoiceSegment(
                        start_sec=0.0,
                        end_sec=6.0,
                        text="Ein schmaler Slot Canyon mit warmem orangenem Licht.",
                    )
                ],
            )
        ],
    )
    project.voice_analysis_path.write_text(voice_doc.model_dump_json(indent=2), encoding="utf-8")

    Path(irrelevant_path).write_bytes(b"mp4")
    Path(matching_path).write_bytes(b"mp4")

    from otio_app.services.inventory_loader import save_folder_inventory

    save_folder_inventory(
        project.folder_inventory_path("Grand Canyon"),
        AssetFolderAnalysis(
            folder="Grand Canyon",
            assets=[
                # Absichtlich zuerst in der Liste, aber inhaltlich irrelevant.
                AssetMediaAnalysis(
                    path=irrelevant_path,
                    description="Parkplatz und Souvenirshop am Eingang",
                    asset_id="asset_irrelevant",
                ),
                AssetMediaAnalysis(
                    path=matching_path,
                    description="Schmaler Slot Canyon mit warmem orangenem Licht",
                    asset_id="asset_matching",
                ),
            ],
        ),
    )

    document = build_edit_plan(project, use_api=False)

    non_outro_shots = [shot for shot in document.shots if not shot.section_outro]
    assert non_outro_shots
    assert any(shot.asset_id == "asset_matching" for shot in non_outro_shots), (
        "Das inhaltlich passende Asset sollte für die Narration gewählt werden, "
        "nicht blind das erste Asset im Ordner."
    )
