"""Phase 8C R1 acceptance matrix — Handoff Testplan groups 1..15."""

from __future__ import annotations

# Requirement text mirrors DISCOVERY_V2_HANDOFF.md § Phase 8C Testplan.
MATRIX_15_REQUIREMENTS: dict[int, str] = {
    1: "zentrale Gateway-Nutzung — keine direkten Providerimporte in UI/Domain/Worker",
    2: "Fake-Adapter End-to-End — Observation persistiert ohne Netz",
    3: "keine hart codierten Modelle in Fachmodulen",
    4: "explizite Nutzerfreigabe erforderlich",
    5: "Rerun startet keinen Model-Job",
    6: "nur persistierte Frames; Originale/Working-Videos blockiert",
    7: "Frame- und Run-Limits",
    8: "Pydantic-Validierung gültiger/ungültiger Responses",
    9: "ungültige Evidence-IDs abgelehnt",
    10: "Retry-Limit (max. 2)",
    11: "Caching und historische Versionen getrennt",
    12: "Providerfehler ohne Secret-Leak",
    13: "Orphan-Recovery (worker_interrupted)",
    14: "UI-No-I/O beim Rendering",
    15: "keine Dramaturgie-Felder oder -Tabellen in Phase-8C-Modulen",
}

# Evidence kinds: runtime | sqlite | fake_adapter | e2e | source_ast
MATRIX_15: dict[int, list[tuple[str, str]]] = {
    1: [
        (
            "source_ast",
            "tests/test_discovery_v2_model_analysis_fake.py::test_new_modules_have_no_real_provider_or_http_imports",
        ),
        (
            "source_ast",
            "tests/test_discovery_v2_model_analysis_fake.py::test_worker_imports_gateway_not_fake_directly",
        ),
    ],
    2: [
        (
            "e2e",
            "tests/test_discovery_v2_model_analysis_fake.py::test_fake_e2e_start_with_consent_persists_observation",
        ),
        (
            "runtime",
            "tests/test_discovery_v2_model_analysis_r1.py::test_r1_observation_json_under_analysis_observations",
        ),
    ],
    3: [
        (
            "source_ast",
            "tests/test_discovery_v2_model_analysis_fake.py::test_no_hard_coded_gemini_model_in_domain_or_application",
        ),
    ],
    4: [
        (
            "runtime",
            "tests/test_discovery_v2_model_analysis_fake.py::test_without_consent_returns_required_error",
        ),
        (
            "sqlite",
            "tests/test_discovery_v2_model_analysis_r1.py::test_r1_consent_is_per_run_and_not_reused",
        ),
    ],
    5: [
        (
            "runtime",
            "tests/test_discovery_v2_model_analysis_fake.py::test_ui_button_false_no_start_and_view_has_no_media_io",
        ),
    ],
    6: [
        (
            "runtime",
            "tests/test_discovery_v2_model_analysis_fake.py::test_frame_missing_and_hash_mismatch",
        ),
        (
            "runtime",
            "tests/test_discovery_v2_model_analysis_r1.py::test_r1_working_media_relative_path_rejected_as_model_input",
        ),
    ],
    7: [
        (
            "runtime",
            "tests/test_discovery_v2_model_analysis_fake.py::test_frame_limit_exceeded_from_config",
        ),
        (
            "runtime",
            "tests/test_discovery_v2_model_analysis_r1.py::test_r1_worker_enforces_max_frames_per_run",
        ),
    ],
    8: [
        (
            "runtime",
            "tests/test_discovery_v2_model_analysis_fake.py::test_visual_observation_rejects_extra_fields_and_empty_evidence",
        ),
        (
            "fake_adapter",
            "tests/test_discovery_v2_model_analysis_r1.py::test_r1_invalid_response_type_is_model_response_invalid",
        ),
    ],
    9: [
        (
            "fake_adapter",
            "tests/test_discovery_v2_model_analysis_fake.py::test_invalid_evidence_from_fake_hook_fails_without_success_persist",
        ),
    ],
    10: [
        (
            "fake_adapter",
            "tests/test_discovery_v2_model_analysis_fake.py::test_retry_succeeds_once",
        ),
        (
            "fake_adapter",
            "tests/test_discovery_v2_model_analysis_r1.py::test_r1_retry_exhausted_after_max_retries",
        ),
    ],
    11: [
        (
            "e2e",
            "tests/test_discovery_v2_model_analysis_fake.py::test_cache_reuses_identical_attempt",
        ),
        (
            "e2e",
            "tests/test_discovery_v2_model_analysis_fake.py::test_new_prompt_version_creates_new_observation",
        ),
        (
            "e2e",
            "tests/test_discovery_v2_model_analysis_fake.py::test_frame_fingerprint_change_creates_new_observation",
        ),
    ],
    12: [
        (
            "fake_adapter",
            "tests/test_discovery_v2_model_analysis_r1.py::test_r1_provider_error_messages_omit_secrets",
        ),
        (
            "runtime",
            "tests/test_discovery_v2_model_analysis_r1.py::test_r1_gateway_disabled_is_unconfigured",
        ),
    ],
    13: [
        (
            "e2e",
            "tests/test_discovery_v2_model_analysis_fake.py::test_orphan_recovery_marks_model_run_interrupted",
        ),
    ],
    14: [
        (
            "runtime",
            "tests/test_discovery_v2_model_analysis_fake.py::test_ui_button_false_no_start_and_view_has_no_media_io",
        ),
        (
            "source_ast",
            "tests/test_discovery_v2_analysis_ui.py::test_ui_source_has_no_media_or_api_io",
        ),
    ],
    15: [
        (
            "sqlite",
            "tests/test_discovery_v2_model_analysis_fake.py::test_schema_15_tables_include_phase9_without_dramaturgy",
        ),
        (
            "source_ast",
            "tests/test_discovery_v2_model_analysis_r1.py::test_r1_no_dramaturgy_or_visual_beats_in_phase8c_modules",
        ),
    ],
}

__all__ = ["MATRIX_15", "MATRIX_15_REQUIREMENTS"]
