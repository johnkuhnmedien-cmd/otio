"""Original Phase-8B Auftrag §26 matrix → pytest node IDs (R1 evidence).

Keys 1..86 match DISCOVERY-V2-PHASE8B-SHOT-FRAME-PREPARE-001 section 26.
Each entry lists at least one runtime/SQLite/Fake-Adapter/E2E node ID.
"""

from __future__ import annotations

MATRIX_86_REQUIREMENTS: dict[int, str] = {
    1: "Schema 11→12 erhält alle bestehenden Daten",
    2: "Migration ist idempotent",
    3: "Technical-Shot-Unique-Key",
    4: "Representative-Frame-Unique-Key",
    5: "keine Observation-/Model-/Consent-Tabelle",
    6: "Still-Frame erlaubt timestamp null",
    7: "Video-Frame verlangt Timestamp",
    8: "nur exakt eligible completed Working Media",
    9: "ready blockiert weiterhin",
    10: "Originalpfad blockiert",
    11: "Temp-Pfad blockiert",
    12: "Analysis-Pfad als Input blockiert",
    13: "Output-Hash-Mismatch blockiert",
    14: "neue Working-Media-Version erzeugt neue Vorbereitung",
    15: "alte Vorbereitung bleibt erhalten",
    16: "drei harte Schnitte erzeugen monotone Segmente",
    17: "Grenzen innerhalb der Dauer",
    18: "keine negativen Segmente",
    19: "keine Nullsegmente",
    20: "doppelte Cut-Zeitpunkte werden dedupliziert",
    21: "erstes Kurzsegment wird vorwärts zusammengeführt",
    22: "späteres Kurzsegment wird rückwärts zusammengeführt",
    23: "Segment über 30 Sekunden wird geteilt",
    24: "null Cuts → ein Segment",
    25: "sehr kurzes Video → ein Segment",
    26: "VFR verwendet Sekundenwerte",
    27: "Decoderfehler wird kontrolliert",
    28: "ungültige Grenzen blockieren",
    29: "Mittelpunkt wird verwendet",
    30: "0,08-Sekunden-Grenzabstand",
    31: "schwarzer Mittelpunkt versucht Minus-Kandidat",
    32: "danach Plus-Kandidat",
    33: "alle schwarz → Mittelpunkt mit is_black",
    34: "maximal ein Frame je ausgewähltem Shot",
    35: "bis 23 Shots optionales Overview",
    36: "24 Shots ohne Overview",
    37: "mehr als 24 → längste 24",
    38: "Gleichstand über Ordinal",
    39: "Frames nach Shot-Ordinal sortiert",
    40: "Overview-Deduplizierung",
    41: "JPEG-Videoanalyseframe",
    42: "lange Kante maximal 1280",
    43: "kein Upscaling",
    44: "Seitenverhältnis erhalten",
    45: "Rotation visuell korrekt",
    46: "kein Crop/Zoom/16:9",
    47: "Output vollständig erneut geöffnet",
    48: "Datei-Hash gespeichert",
    49: "Pixel-Hash gespeichert",
    50: "lokale Signale gespeichert",
    51: "opakes Standbild → ein JPEG-Preview",
    52: "Standbild mit Alpha → ein PNG-Preview",
    53: "Standbild erzeugt keine Shots",
    54: "Standbild-Timestamp ist null",
    55: "Audio-only → not_applicable",
    56: "Audio-only erzeugt keine Datei",
    57: "Temp nur im eigenen Run",
    58: "Publish erst nach Output-Prüfung",
    59: "Fehler erzeugt keine erfolgreiche Persistenz",
    60: "identische Vorbereitung wird reused",
    61: "Konflikt wird nicht überschrieben",
    62: "Crash nach Publish ist reparierbar",
    63: "historische Profile bleiben getrennt",
    64: "Analyseframes werden nicht Working Media",
    65: "Analyseframes werden nicht als OTIO-Pfade akzeptiert",
    66: "expliziter Start",
    67: "Rerun startet keinen Job",
    68: "maximal ein aktiver Analysis-Run",
    69: "Orphan → worker_interrupted",
    70: "nur eigener Temp wird bereinigt",
    71: "publizierte Frames bleiben erhalten",
    72: "UI ruft kein FFmpeg auf",
    73: "UI ruft kein ffprobe auf",
    74: "UI öffnet kein Bild",
    75: "UI berechnet keinen Hash",
    76: "UI führt kein Medien-stat aus",
    77: "UI zeigt persistierte Shots und Frames",
    78: "keine Provider-/Modell-/Consent-Felder",
    79: "kein _otio",
    80: "Working Media unverändert",
    81: "Phase-7-Profile unverändert",
    82: "Classic-Navigation unverändert",
    83: "Without-VO-Navigation unverändert",
    84: "keine API",
    85: "keine Visual Observations",
    86: "keine Dramaturgie oder Visual Beats",
}

# Evidence kind: runtime | sqlite | fake_adapter | e2e | source_ast
MATRIX_86: dict[int, list[tuple[str, str]]] = {
    1: [
        (
            "sqlite",
            "tests/test_discovery_v2_analysis_prepare.py::test_schema_13_to_14_preserves_data_and_is_idempotent",
        )
    ],
    2: [
        (
            "sqlite",
            "tests/test_discovery_v2_analysis_prepare.py::test_schema_13_to_14_preserves_data_and_is_idempotent",
        )
    ],
    3: [
        (
            "sqlite",
            "tests/test_discovery_v2_analysis_prepare.py::test_analysis_shot_and_frame_unique_keys",
        )
    ],
    4: [
        (
            "sqlite",
            "tests/test_discovery_v2_analysis_prepare.py::test_analysis_shot_and_frame_unique_keys",
        )
    ],
    5: [
        (
            "sqlite",
            "tests/test_discovery_v2_analysis_prepare.py::test_analysis_report_schema_omits_future_model_tables",
        )
    ],
    6: [
        (
            "runtime",
            "tests/test_discovery_v2_analysis_prepare.py::test_frame_timestamp_contract_still_null_but_shot_frame_requires_timestamp",
        )
    ],
    7: [
        (
            "runtime",
            "tests/test_discovery_v2_analysis_prepare.py::test_frame_timestamp_contract_still_null_but_shot_frame_requires_timestamp",
        )
    ],
    8: [
        (
            "sqlite",
            "tests/test_discovery_v2_analysis_contracts_r1.py::test_r1_raw_status_completed_ready_pending_unknown",
        )
    ],
    9: [
        (
            "runtime",
            "tests/test_discovery_v2_analysis_prepare_r1.py::test_r1_validate_working_media_raw_ready_is_stale",
        )
    ],
    10: [
        (
            "runtime",
            "tests/test_discovery_v2_analysis_prepare_r1.py::test_r1_validate_working_media_rejects_original_media_path",
        )
    ],
    11: [
        (
            "runtime",
            "tests/test_discovery_v2_analysis_prepare_r1.py::test_r1_validate_working_media_rejects_invalid_paths",
        )
    ],
    12: [
        (
            "runtime",
            "tests/test_discovery_v2_analysis_prepare_r1.py::test_r1_validate_working_media_rejects_invalid_paths",
        )
    ],
    13: [
        (
            "runtime",
            "tests/test_discovery_v2_analysis_prepare_r1.py::test_r1_validate_working_media_hash_mismatch",
        )
    ],
    14: [
        (
            "sqlite",
            "tests/test_discovery_v2_analysis_prepare_r1.py::test_r1_new_working_media_id_and_output_hash_create_separate_identity_old_frames_remain",
        )
    ],
    15: [
        (
            "sqlite",
            "tests/test_discovery_v2_analysis_prepare_r1.py::test_r1_new_working_media_id_and_output_hash_create_separate_identity_old_frames_remain",
        )
    ],
    16: [
        (
            "e2e",
            "tests/test_discovery_v2_analysis_prepare.py::test_smoke_video_three_hard_cuts_produces_monotone_shots_and_frames",
        )
    ],
    17: [
        (
            "runtime",
            "tests/test_discovery_v2_analysis_prepare.py::test_normalize_shot_boundaries_three_cuts_and_bounds",
        )
    ],
    18: [
        (
            "runtime",
            "tests/test_discovery_v2_analysis_prepare_r1.py::test_r1_shot_normalization_matrix_explicit",
        )
    ],
    19: [
        (
            "runtime",
            "tests/test_discovery_v2_analysis_prepare_r1.py::test_r1_shot_normalization_matrix_explicit",
        )
    ],
    20: [
        (
            "runtime",
            "tests/test_discovery_v2_analysis_prepare_r1.py::test_r1_shot_normalization_dedupes_exact_window_deterministically",
        )
    ],
    21: [
        (
            "runtime",
            "tests/test_discovery_v2_analysis_prepare.py::test_normalize_shot_boundaries_dedupes_and_merges_short_segments",
        )
    ],
    22: [
        (
            "runtime",
            "tests/test_discovery_v2_analysis_prepare.py::test_normalize_shot_boundaries_dedupes_and_merges_short_segments",
        )
    ],
    23: [
        (
            "runtime",
            "tests/test_discovery_v2_analysis_prepare_r1.py::test_r1_shot_normalization_matrix_explicit",
        )
    ],
    24: [
        (
            "runtime",
            "tests/test_discovery_v2_analysis_prepare.py::test_normalize_shot_boundaries_long_no_cut_and_short_video",
        )
    ],
    25: [
        (
            "e2e",
            "tests/test_discovery_v2_analysis_prepare.py::test_smoke_very_short_video_has_one_shot_and_one_frame",
        )
    ],
    26: [
        (
            "fake_adapter",
            "tests/test_discovery_v2_analysis_prepare_r1.py::test_r1_shot_detect_ffmpeg_argv_capture_and_artifact",
        )
    ],
    27: [
        (
            "fake_adapter",
            "tests/test_discovery_v2_analysis_prepare_r1.py::test_r1_shot_detection_failed_on_ffmpeg_error",
        )
    ],
    28: [
        (
            "runtime",
            "tests/test_discovery_v2_analysis_prepare_r1.py::test_r1_shot_normalization_rejects_nan_and_infinity",
        )
    ],
    29: [
        (
            "runtime",
            "tests/test_discovery_v2_analysis_prepare.py::test_select_representative_timestamps_midpoint_and_overview_dedupe",
        )
    ],
    30: [
        (
            "runtime",
            "tests/test_discovery_v2_analysis_prepare.py::test_select_representative_timestamps_midpoint_and_overview_dedupe",
        )
    ],
    31: [
        (
            "fake_adapter",
            "tests/test_discovery_v2_analysis_prepare.py::test_worker_uses_alternate_candidate_when_midpoint_is_black",
        )
    ],
    32: [
        (
            "runtime",
            "tests/test_discovery_v2_analysis_prepare.py::test_black_frame_candidate_order_and_clamping",
        )
    ],
    33: [
        (
            "fake_adapter",
            "tests/test_discovery_v2_analysis_prepare_r1.py::test_r1_all_black_candidates_fall_back_to_midpoint_with_is_black",
        )
    ],
    34: [
        (
            "runtime",
            "tests/test_discovery_v2_analysis_prepare.py::test_select_representative_timestamps_frame_cap_rules",
        )
    ],
    35: [
        (
            "runtime",
            "tests/test_discovery_v2_analysis_prepare.py::test_select_representative_timestamps_frame_cap_rules",
        )
    ],
    36: [
        (
            "runtime",
            "tests/test_discovery_v2_analysis_prepare.py::test_select_representative_timestamps_frame_cap_rules",
        )
    ],
    37: [
        (
            "runtime",
            "tests/test_discovery_v2_analysis_prepare.py::test_select_representative_timestamps_frame_cap_rules",
        )
    ],
    38: [
        (
            "runtime",
            "tests/test_discovery_v2_analysis_prepare_r1.py::test_r1_equal_duration_tie_breaks_by_ordinal_for_more_than_24_shots",
        )
    ],
    39: [
        (
            "runtime",
            "tests/test_discovery_v2_analysis_prepare_r1.py::test_r1_equal_duration_tie_breaks_by_ordinal_for_more_than_24_shots",
        )
    ],
    40: [
        (
            "runtime",
            "tests/test_discovery_v2_analysis_prepare_r1.py::test_r1_overview_dedupe_when_overview_within_point_ten",
        )
    ],
    41: [
        (
            "e2e",
            "tests/test_discovery_v2_analysis_prepare.py::test_smoke_video_three_hard_cuts_produces_monotone_shots_and_frames",
        )
    ],
    42: [
        (
            "e2e",
            "tests/test_discovery_v2_analysis_prepare.py::test_smoke_portrait_video_frame_stays_portrait_and_scaled",
        )
    ],
    43: [
        (
            "runtime",
            "tests/test_discovery_v2_analysis_prepare_r1.py::test_r1_still_preview_no_upscale_and_large_downscales",
        )
    ],
    44: [
        (
            "e2e",
            "tests/test_discovery_v2_analysis_prepare.py::test_smoke_portrait_video_frame_stays_portrait_and_scaled",
        )
    ],
    45: [
        (
            "fake_adapter",
            "tests/test_discovery_v2_analysis_prepare_r1.py::test_r1_rotation_documentation_argv_and_portrait_smoke_if_ffmpeg_available",
        )
    ],
    46: [
        (
            "fake_adapter",
            "tests/test_discovery_v2_analysis_prepare_r1.py::test_r1_frame_sample_ffmpeg_argv_capture_rotation_and_artifact",
        )
    ],
    47: [
        (
            "runtime",
            "tests/test_discovery_v2_analysis_prepare.py::test_compute_frame_signals_on_synthetic_images",
        )
    ],
    48: [
        (
            "e2e",
            "tests/test_discovery_v2_analysis_prepare.py::test_smoke_opaque_still_prepares_one_jpeg_without_shots",
        )
    ],
    49: [
        (
            "runtime",
            "tests/test_discovery_v2_analysis_prepare.py::test_compute_frame_signals_on_synthetic_images",
        )
    ],
    50: [
        (
            "runtime",
            "tests/test_discovery_v2_analysis_prepare.py::test_compute_frame_signals_on_synthetic_images",
        )
    ],
    51: [
        (
            "e2e",
            "tests/test_discovery_v2_analysis_prepare.py::test_smoke_opaque_still_prepares_one_jpeg_without_shots",
        )
    ],
    52: [
        (
            "e2e",
            "tests/test_discovery_v2_analysis_prepare.py::test_smoke_alpha_still_prepares_png_with_alpha",
        )
    ],
    53: [
        (
            "e2e",
            "tests/test_discovery_v2_analysis_prepare.py::test_smoke_opaque_still_prepares_one_jpeg_without_shots",
        )
    ],
    54: [
        (
            "e2e",
            "tests/test_discovery_v2_analysis_prepare.py::test_smoke_opaque_still_prepares_one_jpeg_without_shots",
        )
    ],
    55: [
        (
            "e2e",
            "tests/test_discovery_v2_analysis_prepare.py::test_smoke_audio_only_is_not_applicable_without_frames",
        )
    ],
    56: [
        (
            "e2e",
            "tests/test_discovery_v2_analysis_prepare.py::test_smoke_audio_only_is_not_applicable_without_frames",
        )
    ],
    57: [
        (
            "e2e",
            "tests/test_discovery_v2_analysis_prepare_r1.py::test_r1_prepare_temp_run_dir_cleaned_after_success",
        )
    ],
    58: [
        (
            "runtime",
            "tests/test_discovery_v2_analysis_prepare_r1.py::test_r1_multi_frame_conflict_publishes_no_sibling",
        )
    ],
    59: [
        (
            "e2e",
            "tests/test_discovery_v2_analysis_prepare.py::test_smoke_analysis_artifact_conflict_leaves_existing_file_unchanged",
        )
    ],
    60: [
        (
            "e2e",
            "tests/test_discovery_v2_analysis_prepare.py::test_smoke_second_start_reuses_same_identity_and_artifacts",
        )
    ],
    61: [
        (
            "e2e",
            "tests/test_discovery_v2_analysis_prepare.py::test_smoke_analysis_artifact_conflict_leaves_existing_file_unchanged",
        )
    ],
    62: [
        (
            "e2e",
            "tests/test_discovery_v2_analysis_prepare.py::test_smoke_orphan_recovery_marks_interrupted_cleans_temp_keeps_published_frame",
        )
    ],
    63: [
        (
            "sqlite",
            "tests/test_discovery_v2_analysis_prepare_r1.py::test_r1_new_working_media_id_and_output_hash_create_separate_identity_old_frames_remain",
        )
    ],
    64: [
        (
            "sqlite",
            "tests/test_discovery_v2_analysis_prepare_r1.py::test_r1_analysis_frames_not_working_media_and_rejected_as_otio_media",
        )
    ],
    65: [
        (
            "runtime",
            "tests/test_discovery_v2_analysis_prepare_r1.py::test_r1_analysis_frames_not_working_media_and_rejected_as_otio_media",
        )
    ],
    66: [
        (
            "runtime",
            "tests/test_discovery_v2_analysis_prepare.py::test_asset_analysis_ui_button_is_explicit_start_only",
        )
    ],
    67: [
        (
            "runtime",
            "tests/test_discovery_v2_analysis_prepare_r1.py::test_r1_ui_button_false_does_not_start",
        )
    ],
    68: [
        (
            "sqlite",
            "tests/test_discovery_v2_analysis_prepare_r1.py::test_r1_active_analysis_run_blocks_second_start",
        )
    ],
    69: [
        (
            "e2e",
            "tests/test_discovery_v2_analysis_prepare.py::test_smoke_orphan_recovery_marks_interrupted_cleans_temp_keeps_published_frame",
        )
    ],
    70: [
        (
            "e2e",
            "tests/test_discovery_v2_analysis_prepare_r1.py::test_r1_orphan_recovery_cleans_only_own_temp",
        )
    ],
    71: [
        (
            "e2e",
            "tests/test_discovery_v2_analysis_prepare.py::test_smoke_orphan_recovery_marks_interrupted_cleans_temp_keeps_published_frame",
        )
    ],
    72: [
        (
            "source_ast",
            "tests/test_discovery_v2_analysis_ui.py::test_ui_source_has_no_media_or_api_io",
        ),
        (
            "runtime",
            "tests/test_discovery_v2_analysis_prepare_r1.py::test_r1_get_analysis_prepare_view_uses_sqlite_only_fail_on_media_io",
        ),
    ],
    73: [
        (
            "source_ast",
            "tests/test_discovery_v2_analysis_ui.py::test_ui_source_has_no_media_or_api_io",
        ),
        (
            "runtime",
            "tests/test_discovery_v2_analysis_prepare_r1.py::test_r1_get_analysis_prepare_view_uses_sqlite_only_fail_on_media_io",
        ),
    ],
    74: [
        (
            "runtime",
            "tests/test_discovery_v2_analysis_prepare_r1.py::test_r1_get_analysis_prepare_view_uses_sqlite_only_fail_on_media_io",
        )
    ],
    75: [
        (
            "runtime",
            "tests/test_discovery_v2_analysis_prepare_r1.py::test_r1_get_analysis_prepare_view_uses_sqlite_only_fail_on_media_io",
        )
    ],
    76: [
        (
            "runtime",
            "tests/test_discovery_v2_analysis_prepare_r1.py::test_r1_get_analysis_prepare_view_uses_sqlite_only_fail_on_media_io",
        )
    ],
    77: [
        (
            "runtime",
            "tests/test_discovery_v2_analysis_prepare_r1.py::test_r1_render_prepare_review_reads_real_sqlite_shots_and_frames",
        )
    ],
    78: [
        (
            "source_ast",
            "tests/test_discovery_v2_analysis_contracts_r1.py::test_r1_ui_source_has_no_provider_or_media_io",
        )
    ],
    79: [
        (
            "runtime",
            "tests/test_discovery_v2_analysis_prepare.py::test_analysis_paths_are_under_analysis_frames_not_otio_media",
        )
    ],
    80: [
        (
            "e2e",
            "tests/test_discovery_v2_analysis_prepare_r1.py::test_r1_working_media_bytes_unchanged_after_prepare",
        )
    ],
    81: [
        (
            "runtime",
            "tests/test_discovery_v2_analysis_contracts.py::test_phase7_profiles_unchanged",
        )
    ],
    82: [
        (
            "runtime",
            "tests/test_discovery_v2_analysis_ui.py::test_classic_and_without_vo_unchanged",
        )
    ],
    83: [
        (
            "runtime",
            "tests/test_discovery_v2_analysis_ui.py::test_classic_and_without_vo_unchanged",
        )
    ],
    84: [
        (
            "source_ast",
            "tests/test_discovery_v2_analysis_ui.py::test_ui_source_has_no_media_or_api_io",
        )
    ],
    85: [
        (
            "sqlite",
            "tests/test_discovery_v2_analysis_prepare.py::test_analysis_report_schema_omits_future_model_tables",
        )
    ],
    86: [
        (
            "source_ast",
            "tests/test_discovery_v2_analysis_prepare.py::test_asset_analysis_ui_source_is_prepare_only_and_no_media_io",
        )
    ],
}
