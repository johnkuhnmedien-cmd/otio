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
from otio_app.services.edit_plan_builder import (
    EditPlanBuildStatus,
    EditPlanPromptMode,
    EditPlanValidationMode,
    build_edit_plan,
    unwrap_accepted_edit_plan,
)


def _build_plan(project, **kwargs):
    return unwrap_accepted_edit_plan(build_edit_plan(project, **kwargs))
from otio_app.services.asset_usage import usage_count_by_asset_id_from_shots


def _sample_project(layout: dict[str, Path]) -> Project:
    project = Project(
        id="plan-test",
        name="Test",
        project_root=str(layout["project_root"]),
        work_dir=str(layout["work_dir"]),
        asset_subdir_names=["Grand Canyon"],
        selected_asset_subdirs=["Grand Canyon"],
    )
    from otio_app.services.edit_plan_rules import RULE_MAX_ASSET_USES, default_rules, save_edit_plan_rules

    rules_doc = default_rules(project)
    for rule in rules_doc.rules:
        if rule.rule_type == RULE_MAX_ASSET_USES:
            rule.enabled = False
    save_edit_plan_rules(project, rules_doc)
    return project


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

    document = _build_plan(project, use_api=False)
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


def test_build_edit_plan_calls_progress_callback_per_folder(
    temp_project_layout: dict[str, Path],
) -> None:
    """Regression: Der gesamtheitliche Gemini-Call meldet Fortschritt einmal
    pro Ordner statt pro Segment."""
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
                    VoiceSegment(start_sec=10.0, end_sec=12.0, text="   "),
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
    unwrap_accepted_edit_plan(
        build_edit_plan(
            project,
            use_api=False,
            progress_callback=lambda folder, index, total: progress_calls.append((folder, index, total)),
        )
    )

    assert progress_calls == [("Grand Canyon", 0, 1), ("Grand Canyon", 1, 1)]


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
        "otio_app.services.edit_plan_builder.plan_folder_assets",
        side_effect=RuntimeError("network down"),
    ):
        document = unwrap_accepted_edit_plan(build_edit_plan(project, use_api=True))

    assert document.shots
    narrative = next(shot for shot in document.shots if not shot.section_outro)
    assert narrative.match_quality
    if narrative.match_quality != "unpassend":
        assert narrative.asset_path == media_path


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

    document = _build_plan(project, settings=settings, use_api=False, rules_doc=rules)
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

    def fake_plan_folder_assets(**kwargs):
        return [
            {
                "beat_id": "beat_001",
                "parts": [
                    {
                        "text": "Ein Wasserfall stürzt tosend in die Tiefe",
                        "motif": "Wasserfall",
                        "asset_path": waterfall_path,
                        "match_quality": "sehr_gut",
                    },
                    {
                        "text": "ein Adler majestätisch über dem Tal kreist",
                        "motif": "Adler",
                        "asset_path": eagle_path,
                        "match_quality": "gut",
                    },
                ],
            }
        ]

    with patch(
        "otio_app.services.edit_plan_builder.plan_folder_assets",
        side_effect=fake_plan_folder_assets,
    ):
        document = unwrap_accepted_edit_plan(build_edit_plan(project, use_api=True))

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
    document = _build_plan(project, settings=settings, use_api=False)

    outro_shots = [shot for shot in document.shots if shot.section_outro]
    assert outro_shots, "Es sollten Outro-Shots erzeugt worden sein."
    assert all(shot.duration_sec <= 5.0 + 0.01 for shot in outro_shots), (
        "Kein Outro-Shot darf die konfigurierte Max.-Shot-Regel (5.0s) verletzen."
    )


def test_inventory_hash_not_stale_immediately_after_build_with_supplement_asset(
    temp_project_layout: dict[str, Path],
) -> None:
    """Regression: Nach dem Hinzufügen eines Supplement-Assets (liegt unter
    `<Ordner>/_supplemental/<provider>/`) und einem frischen build_edit_plan()
    zeigte die Stale-Hash-Prüfung sofort 'Inventory changed', obwohl sich am
    Inventar seit dem Bauen des Plans nichts geändert hatte. Ursache:
    load_folder_inventory() verglich das gespeicherte Inventar mit einem
    NICHT-rekursiven Top-Level-Dateiscan, der Supplement-Assets im
    Unterordner grundsätzlich nicht findet — und verwarf das gespeicherte
    Inventar dadurch fälschlich als 'veraltet'."""
    from otio_app.analysis_models import SupplementAssetSidecar
    from otio_app.services.inventory_hash import inventory_hash_is_stale
    from otio_app.services.supplement_pipeline import extend_folder_inventory, save_sidecar

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
                segments=[VoiceSegment(start_sec=0.0, end_sec=6.0, text="Ein Canyon mit Fluss.")],
            )
        ],
    )
    project.voice_analysis_path.write_text(voice_doc.model_dump_json(indent=2), encoding="utf-8")
    Path(media_path).write_bytes(b"mp4")

    from otio_app.services.inventory_loader import save_folder_inventory

    save_folder_inventory(
        project.folder_inventory_path("Grand Canyon"),
        AssetFolderAnalysis(
            folder="Grand Canyon",
            assets=[AssetMediaAnalysis(path=media_path, description="Canyon", asset_id="asset_clip")],
        ),
    )

    supplement_dir = (
        temp_project_layout["project_root"] / "Grand Canyon" / "_supplemental" / "_pexels"
    )
    supplement_dir.mkdir(parents=True, exist_ok=True)
    supplement_path = supplement_dir / "new_asset.mp4"
    supplement_path.write_bytes(b"mp4")
    save_sidecar(
        SupplementAssetSidecar(
            asset_id="asset_supplement",
            supplement_request_id="supp_req_test",
            provider="pexels",
            local_path=str(supplement_path),
        )
    )
    extend_folder_inventory(
        project,
        folder_name="Grand Canyon",
        asset=AssetMediaAnalysis(
            path=str(supplement_path),
            description="Neuer Supplement-Canyon-Shot",
            asset_id="asset_supplement",
            asset_origin="pexels",
            analysis_status="complete",
            frames_used=["frame1.jpg"],
            approved_for_cut_plan=True,
            supplement_validation_status="PASS",
        ),
    )

    document = _build_plan(project, use_api=False)

    assert document.inventory_hash_at_plan_time
    assert inventory_hash_is_stale(
        project, "Grand Canyon", document.inventory_hash_at_plan_time
    ) is False


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

    document = _build_plan(project, use_api=False)

    non_outro_shots = [shot for shot in document.shots if not shot.section_outro]
    assert non_outro_shots
    assert any(shot.asset_id == "asset_matching" for shot in non_outro_shots), (
        "Das inhaltlich passende Asset sollte für die Narration gewählt werden, "
        "nicht blind das erste Asset im Ordner."
    )


def test_unpassend_match_quality_creates_supplement_request(
    temp_project_layout: dict[str, Path],
) -> None:
    from unittest.mock import patch

    from otio_app.defaults import MATCH_QUALITY_UNPASSEND

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
                    VoiceSegment(
                        start_sec=0.0,
                        end_sec=6.0,
                        text="Ein Wasserfall stürzt tosend in die Tiefe.",
                    )
                ],
            )
        ],
    )
    project.voice_analysis_path.write_text(voice_doc.model_dump_json(indent=2), encoding="utf-8")
    Path(media_path).write_bytes(b"mp4")

    from otio_app.services.inventory_loader import save_folder_inventory

    save_folder_inventory(
        project.folder_inventory_path("Grand Canyon"),
        AssetFolderAnalysis(
            folder="Grand Canyon",
            assets=[
                AssetMediaAnalysis(
                    path=media_path,
                    description="Parkplatz und Souvenirshop",
                    asset_id="asset_irrelevant",
                ),
                AssetMediaAnalysis(
                    path=str(temp_project_layout["project_root"] / "Grand Canyon" / "aerial.mp4"),
                    description="Luftaufnahme Establishing Overview der Landschaft",
                    asset_id="asset_aerial",
                ),
            ],
        ),
    )
    aerial_path = str(temp_project_layout["project_root"] / "Grand Canyon" / "aerial.mp4")
    Path(aerial_path).write_bytes(b"mp4")

    def fake_plan_folder_assets(**kwargs):
        return [
            {
                "beat_id": "beat_001",
                "parts": [
                    {
                        "text": "Ein Wasserfall stürzt tosend in die Tiefe.",
                        "motif": "Wasserfall",
                        "asset_path": None,
                        "match_quality": "unpassend",
                    }
                ],
            }
        ]

    with patch(
        "otio_app.services.edit_plan_builder.plan_folder_assets",
        side_effect=fake_plan_folder_assets,
    ):
        document = unwrap_accepted_edit_plan(build_edit_plan(project, use_api=True))

    narrative_shots = [shot for shot in document.shots if not shot.section_outro]
    assert len(narrative_shots) == 1
    shot = narrative_shots[0]
    assert shot.match_quality == MATCH_QUALITY_UNPASSEND
    assert shot.asset_path is not None
    assert shot.asset_id == "asset_aerial"
    assert shot.supplement_request_id
    assert document.supplement_request_ids


def test_build_edit_plan_skip_validation_accepts_despite_failure(
    temp_project_layout: dict[str, Path],
) -> None:
    from unittest.mock import patch

    from otio_app.services.edit_plan_validator import (
        FinalPlanValidationResult,
        PlanValidationError,
        ValidationStatus,
    )

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
                duration_sec=6.0,
                segments=[VoiceSegment(start_sec=0.0, end_sec=6.0, text="Kurzer Text über den Canyon.")],
            )
        ],
    )
    project.voice_analysis_path.write_text(voice_doc.model_dump_json(indent=2), encoding="utf-8")
    Path(media_path).write_bytes(b"mp4")

    from otio_app.services.inventory_loader import save_folder_inventory

    save_folder_inventory(
        project.folder_inventory_path("Grand Canyon"),
        AssetFolderAnalysis(
            folder="Grand Canyon",
            assets=[
                AssetMediaAnalysis(
                    path=media_path,
                    description="Weite Canyon-Landschaft",
                    asset_id="asset_canyon",
                )
            ],
        ),
    )

    gemini_calls = 0

    def fake_plan_folder_assets(**kwargs):
        nonlocal gemini_calls
        gemini_calls += 1
        return [
            {
                "beat_id": "beat_001",
                "parts": [
                    {
                        "text": "Kurzer Text über den Canyon.",
                        "motif": "Canyon",
                        "asset_path": media_path,
                        "match_quality": "gut",
                    }
                ],
            }
        ]

    def always_fail_validation(*args, **kwargs):
        return FinalPlanValidationResult(
            ok=False,
            status=ValidationStatus.BLOCKED,
            errors=[
                PlanValidationError(
                    type="ASSET_USAGE_LIMIT_EXCEEDED",
                    asset_id="asset_canyon",
                    usage_count=2,
                    max_allowed=1,
                )
            ],
        )

    with (
        patch(
            "otio_app.services.edit_plan_builder.plan_folder_assets",
            side_effect=fake_plan_folder_assets,
        ),
        patch(
            "otio_app.services.edit_plan_builder.validate_final_edit_plan",
            side_effect=always_fail_validation,
        ),
        patch(
            "otio_app.services.timeline_plan_builder.probe_duration_seconds",
            return_value=6.0,
        ),
    ):
        result = build_edit_plan(
            project,
            use_api=True,
            validation_mode=EditPlanValidationMode.SKIP,
        )

    assert result.status == EditPlanBuildStatus.ACCEPTED
    assert result.document is not None
    assert result.validation_status == "SKIPPED"
    assert gemini_calls == 1


def test_build_edit_plan_free_mode_passes_prompt_mode_to_gemini(
    temp_project_layout: dict[str, Path],
) -> None:
    from unittest.mock import patch

    from otio_app.services.edit_plan_rules import load_edit_plan_rules, save_edit_plan_rules

    project = _sample_project(temp_project_layout)
    voice_path = str(temp_project_layout["voice_file"])
    media_path = str(temp_project_layout["project_root"] / "Grand Canyon" / "clip.mp4")

    doc = load_edit_plan_rules(project)
    doc = doc.model_copy(update={"gemini_prompt": "Use cinematic pacing."})
    save_edit_plan_rules(project, doc)

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
                duration_sec=6.0,
                segments=[VoiceSegment(start_sec=0.0, end_sec=6.0, text="Kurzer Text.")],
            )
        ],
    )
    project.voice_analysis_path.write_text(voice_doc.model_dump_json(indent=2), encoding="utf-8")
    Path(media_path).write_bytes(b"mp4")

    from otio_app.services.inventory_loader import save_folder_inventory

    save_folder_inventory(
        project.folder_inventory_path("Grand Canyon"),
        AssetFolderAnalysis(
            folder="Grand Canyon",
            assets=[
                AssetMediaAnalysis(
                    path=media_path,
                    description="Landschaft",
                    asset_id="asset_1",
                )
            ],
        ),
    )

    captured: list[dict] = []

    def fake_plan_folder_assets(**kwargs):
        captured.append(kwargs)
        return [
            {
                "beat_id": "beat_001",
                "parts": [
                    {
                        "text": "Kurzer Text.",
                        "motif": "Landschaft",
                        "asset_path": media_path,
                        "match_quality": "gut",
                    }
                ],
            }
        ]

    with (
        patch(
            "otio_app.services.edit_plan_builder.plan_folder_assets",
            side_effect=fake_plan_folder_assets,
        ),
        patch(
            "otio_app.services.timeline_plan_builder.probe_duration_seconds",
            return_value=6.0,
        ),
    ):
        result = build_edit_plan(
            project,
            use_api=True,
            prompt_mode=EditPlanPromptMode.FREE,
            validation_mode=EditPlanValidationMode.SKIP,
        )

    assert result.status == EditPlanBuildStatus.ACCEPTED
    assert captured
    assert captured[0]["prompt_mode"] == "free"
    assert captured[0]["extra_instructions"] == "Use cinematic pacing."
    assert captured[0]["max_asset_usage"] is None
    assert result.used_rules.get("prompt_mode") == "free"
    assert result.used_rules.get("gemini_prompt") == "Use cinematic pacing."


def test_build_edit_plan_holistic_v1_skips_normalization_and_rules(
    temp_project_layout: dict[str, Path],
) -> None:
    from unittest.mock import patch

    from otio_app.services.edit_plan_rules import load_edit_plan_rules, save_edit_plan_rules

    project = _sample_project(temp_project_layout)
    voice_path = str(temp_project_layout["voice_file"])
    media_path = str(temp_project_layout["project_root"] / "Grand Canyon" / "clip.mp4")

    doc = load_edit_plan_rules(project)
    doc = doc.model_copy(update={"gemini_prompt": "Klassische Schnittführung."})
    save_edit_plan_rules(project, doc)

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
                duration_sec=18.0,
                segments=[
                    VoiceSegment(
                        start_sec=0.0,
                        end_sec=18.0,
                        text="Langer Abschnitt mit mehreren Motiven im Park.",
                    )
                ],
            )
        ],
    )
    project.voice_analysis_path.write_text(voice_doc.model_dump_json(indent=2), encoding="utf-8")
    Path(media_path).write_bytes(b"mp4")

    from otio_app.services.inventory_loader import save_folder_inventory

    save_folder_inventory(
        project.folder_inventory_path("Grand Canyon"),
        AssetFolderAnalysis(
            folder="Grand Canyon",
            assets=[
                AssetMediaAnalysis(
                    path=media_path,
                    description="Landschaft",
                    asset_id="asset_1",
                )
            ],
        ),
    )

    captured: list[dict] = []
    normalize_calls: list[dict] = []
    rules_calls: list[int] = []

    def fake_plan_folder_assets(**kwargs):
        captured.append(kwargs)
        return [
            {
                "beat_id": "beat_001",
                "parts": [
                    {
                        "text": "Erstes Motiv.",
                        "motif": "Landschaft",
                        "asset_path": media_path,
                        "match_quality": "gut",
                    },
                    {
                        "text": "Zweites Motiv.",
                        "motif": "Detail",
                        "asset_path": None,
                        "match_quality": "unpassend",
                    },
                ],
            }
        ]

    def fake_normalize(**kwargs):
        normalize_calls.append(kwargs)
        raise AssertionError("normalize_gemini_parts_for_segment should not run in holistic v1")

    def fake_apply_rules(shots, rules_doc, assets_payload_by_folder):
        rules_calls.append(len(shots))
        return shots

    with (
        patch(
            "otio_app.services.edit_plan_builder.plan_folder_assets",
            side_effect=fake_plan_folder_assets,
        ),
        patch(
            "otio_app.services.edit_plan_builder.normalize_gemini_parts_for_segment",
            side_effect=fake_normalize,
        ),
        patch(
            "otio_app.services.edit_plan_builder.apply_edit_plan_rules",
            side_effect=fake_apply_rules,
        ),
        patch(
            "otio_app.services.timeline_plan_builder.probe_duration_seconds",
            return_value=18.0,
        ),
    ):
        result = build_edit_plan(
            project,
            use_api=True,
            prompt_mode=EditPlanPromptMode.HOLISTIC_V1,
            validation_mode=EditPlanValidationMode.SKIP,
        )

    assert result.status == EditPlanBuildStatus.ACCEPTED
    assert captured
    assert captured[0]["prompt_mode"] == "holistic_v1"
    assert captured[0]["section_outro_sec"] == 0.0
    assert captured[0]["max_asset_usage"] is None
    assert not normalize_calls
    assert not rules_calls
    assert result.used_rules.get("prompt_mode") == "holistic_v1"
    assert result.used_rules.get("pipeline") == "holistic_v1"
    assert result.document is not None
    narrative_shots = [shot for shot in result.document.shots if not shot.section_outro]
    assert len(narrative_shots) >= 2
    assert any(shot.asset_path is None for shot in narrative_shots)
    assert any("Holistic v1" in note for note in (result.plan_generation_notes or []))
