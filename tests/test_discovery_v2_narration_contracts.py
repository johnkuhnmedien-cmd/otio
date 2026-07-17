from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from otio_app.discovery_v2.domain.asset_registry import REGISTRY_SCHEMA_VERSION
from otio_app.discovery_v2.domain.narration import (
    CLOSING_HOLD_MAX_S,
    COLD_OPEN_MAX_S,
    MAX_PAUSE_RATIO,
    MAX_SEGMENTS,
    MAX_TEXT_LEN,
    MIN_SENTENCE_S,
    NORMAL_PAUSE_MAX_S,
    PauseDirection,
    PauseFunction,
    PauseHardness,
    PausePositionKind,
    PauseUncertainty,
    VOICE_ADAPTER_VERSION_FAKE,
    VOICE_AUDIO_FORMAT,
    VOICE_CHANNELS,
    VOICE_IDENTIFIER_FAKE,
    VOICE_PROVIDER_FAKE,
    VOICE_SAMPLE_RATE_HZ,
    clamp_pause_duration,
    fake_voice_sample_count,
    timebase_from_fps,
)
from otio_app.discovery_v2.narration_paths import (
    NarrationPathError,
    assert_narration_relative_path,
    narration_audio_relative_path,
)
from otio_app.discovery_v2.persistence import asset_registry_database as reg_db


def test_narration_contract_constants_and_helpers() -> None:
    assert VOICE_PROVIDER_FAKE == "fake"
    assert VOICE_IDENTIFIER_FAKE == "fake-neutral-v1"
    assert VOICE_ADAPTER_VERSION_FAKE == "fake-voice-v1"
    assert VOICE_AUDIO_FORMAT == "wav-pcm-s16le"
    assert VOICE_SAMPLE_RATE_HZ == 48000
    assert VOICE_CHANNELS == 1
    assert MIN_SENTENCE_S == 0.40
    assert MAX_TEXT_LEN == 2000
    assert MAX_SEGMENTS == 500
    assert COLD_OPEN_MAX_S == 3.0
    assert NORMAL_PAUSE_MAX_S == 2.5
    assert CLOSING_HOLD_MAX_S == 5.0
    assert MAX_PAUSE_RATIO == 0.55
    assert fake_voice_sample_count("abc") == int(0.4 * 48000)


def test_schema_19_creates_phase11_and_phase12_tables(tmp_path: Path) -> None:
    conn = reg_db.get_registry_connection(tmp_path)
    try:
        assert reg_db.read_schema_version(conn) == REGISTRY_SCHEMA_VERSION == "20"
        tables = {
            row["name"]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            ).fetchall()
        }
        assert {
            "narration_project_state",
            "voice_profiles",
            "voice_generation_runs",
            "voice_generation_attempts",
            "voice_segments",
            "pause_direction_plans",
            "pause_directions",
            "narration_timelines",
            "narration_timeline_entries",
        }.issubset(tables)
        profile_cols = {
            row["name"] for row in conn.execute("PRAGMA table_info(voice_profiles)").fetchall()
        }
        assert {
            "version",
            "adapter_version",
            "audio_format",
            "sample_rate",
            "channels",
            "supersedes_voice_profile_id",
        }.issubset(profile_cols)
        segment_cols = {
            row["name"] for row in conn.execute("PRAGMA table_info(voice_segments)").fetchall()
        }
        assert "script_id" in segment_cols
        direction_cols = {
            row["name"] for row in conn.execute("PRAGMA table_info(pause_directions)").fetchall()
        }
        assert "ordinal" in direction_cols
        assert "visual_edit_plans" in tables
        assert "visual_edit_project_state" in tables
        assert "otio_exports" not in tables
    finally:
        conn.close()
    conn2 = reg_db.get_registry_connection(tmp_path)
    try:
        assert reg_db.read_schema_version(conn2) == "20"
    finally:
        conn2.close()


def test_schema_16_to_19_migration_preserves_existing_data(tmp_path: Path) -> None:
    db_dir = reg_db.ensure_registry_dir(tmp_path)
    db_path = db_dir / "assets.sqlite3"
    raw = sqlite3.connect(str(db_path))
    raw.execute(
        "CREATE TABLE registry_schema (schema_version TEXT PRIMARY KEY, initialized_at TEXT NOT NULL, updated_at TEXT NOT NULL)"
    )
    raw.execute(
        "CREATE TABLE assets (asset_id TEXT PRIMARY KEY, project_id TEXT NOT NULL, source_relative_path TEXT NOT NULL, source_group TEXT NOT NULL, file_name TEXT NOT NULL, extension TEXT NOT NULL, media_kind TEXT NOT NULL, size_bytes INTEGER NOT NULL, mtime_ns INTEGER NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL, UNIQUE(project_id, source_relative_path))"
    )
    raw.execute(
        "INSERT INTO registry_schema VALUES ('16', '2026-01-01T00:00:00+00:00', '2026-01-01T00:00:00+00:00')"
    )
    raw.execute(
        "INSERT INTO assets VALUES ('asset-1','project-1','Media/a.mov','Media','a.mov','.mov','video',1,1,'now','now')"
    )
    raw.commit()
    raw.close()
    conn = reg_db.get_registry_connection(tmp_path)
    try:
        assert reg_db.read_schema_version(conn) == "20"
        assert conn.execute("SELECT COUNT(*) AS n FROM assets").fetchone()["n"] == 1
    finally:
        conn.close()


def test_schema_17_to_19_migration_backfills_narration_contracts(tmp_path: Path) -> None:
    db_dir = reg_db.ensure_registry_dir(tmp_path)
    db_path = db_dir / "assets.sqlite3"
    raw = sqlite3.connect(str(db_path))
    raw.executescript(
        """
        CREATE TABLE registry_schema (
            schema_version TEXT PRIMARY KEY,
            initialized_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        INSERT INTO registry_schema VALUES (
            '17', '2026-01-01T00:00:00+00:00', '2026-01-01T00:00:00+00:00'
        );
        CREATE TABLE voice_profiles (
            voice_profile_id TEXT PRIMARY KEY,
            project_id TEXT NOT NULL,
            language TEXT NOT NULL,
            provider TEXT NOT NULL,
            voice_identifier TEXT NOT NULL,
            voice_settings_version TEXT NOT NULL,
            output_profile_json TEXT NOT NULL,
            status TEXT NOT NULL,
            relative_json_path TEXT NOT NULL,
            created_at TEXT NOT NULL,
            UNIQUE (project_id, voice_profile_id)
        );
        CREATE TABLE voice_generation_runs (
            run_id TEXT PRIMARY KEY,
            project_id TEXT NOT NULL,
            script_lock_id TEXT NOT NULL,
            script_id TEXT NOT NULL,
            voice_profile_id TEXT NOT NULL,
            input_fingerprint TEXT NOT NULL,
            provider TEXT NOT NULL,
            adapter_version TEXT NOT NULL,
            scope TEXT NOT NULL,
            status TEXT NOT NULL,
            sentence_count INTEGER NOT NULL,
            segments_created INTEGER NOT NULL DEFAULT 0,
            segments_reused INTEGER NOT NULL DEFAULT 0,
            segments_failed INTEGER NOT NULL DEFAULT 0,
            error_code TEXT,
            error_message TEXT,
            relative_report_path TEXT,
            created_at TEXT NOT NULL,
            started_at TEXT,
            finished_at TEXT,
            schema_version TEXT NOT NULL
        );
        CREATE TABLE voice_segments (
            segment_id TEXT PRIMARY KEY,
            run_id TEXT NOT NULL,
            script_lock_id TEXT NOT NULL,
            sentence_id TEXT NOT NULL,
            sentence_ordinal INTEGER NOT NULL,
            text_hash TEXT NOT NULL,
            voice_profile_id TEXT NOT NULL,
            provider TEXT NOT NULL,
            voice_identifier TEXT NOT NULL,
            voice_settings_version TEXT NOT NULL,
            adapter_version TEXT NOT NULL,
            audio_format TEXT NOT NULL,
            sample_rate_hz INTEGER NOT NULL,
            channels INTEGER NOT NULL,
            sample_count INTEGER NOT NULL,
            duration_seconds REAL NOT NULL,
            byte_size INTEGER NOT NULL,
            audio_sha256 TEXT NOT NULL,
            relative_path TEXT NOT NULL,
            status TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
        CREATE TABLE pause_direction_plans (
            pause_plan_id TEXT PRIMARY KEY,
            project_id TEXT NOT NULL,
            script_lock_id TEXT NOT NULL,
            voice_run_id TEXT NOT NULL,
            prompt_version TEXT NOT NULL,
            model_identifier TEXT NOT NULL,
            gateway_version TEXT NOT NULL,
            response_schema_version TEXT NOT NULL,
            provider TEXT NOT NULL,
            input_fingerprint TEXT NOT NULL,
            global_notes_json TEXT NOT NULL,
            status TEXT NOT NULL,
            relative_json_path TEXT NOT NULL,
            created_at TEXT NOT NULL,
            schema_version TEXT NOT NULL
        );
        CREATE TABLE pause_directions (
            direction_id TEXT NOT NULL,
            pause_plan_id TEXT NOT NULL,
            position_kind TEXT NOT NULL,
            sentence_id TEXT,
            segment_id TEXT,
            anchor_ordinal INTEGER,
            function TEXT NOT NULL,
            min_duration_intent_s REAL NOT NULL,
            preferred_duration_intent_s REAL NOT NULL,
            max_duration_intent_s REAL NOT NULL,
            hardness TEXT NOT NULL,
            rationale TEXT NOT NULL,
            uncertainty TEXT NOT NULL,
            PRIMARY KEY (pause_plan_id, direction_id)
        );
        """
    )
    output_profile_json = (
        '{"audio_format":"wav-pcm-s16le","channels":1,"sample_rate_hz":48000}'
    )
    raw.execute(
        """
        INSERT INTO voice_profiles VALUES (
            'profile-1','project-1','de','fake','fake-neutral-v1',
            'fake-voice-settings-v1',?,'active',
            'narration/voice_profiles/profile-1.json',
            '2026-01-01T00:00:00+00:00'
        )
        """,
        (output_profile_json,),
    )
    raw.execute(
        """
        INSERT INTO voice_generation_runs VALUES (
            'run-1','project-1','lock-1','script-1','profile-1','fp','fake',
            'fake-voice-v1','voice_generation_only','completed',1,1,0,0,
            NULL,NULL,NULL,'2026-01-01T00:00:00+00:00',NULL,NULL,'narration-v1'
        )
        """
    )
    raw.execute(
        """
        INSERT INTO voice_segments VALUES (
            'segment-1','run-1','lock-1','sentence-1',0,'text-hash',
            'profile-1','fake','fake-neutral-v1','fake-voice-settings-v1',
            'fake-voice-v1','wav-pcm-s16le',48000,1,48000,1.0,44,
            'audio-sha','narration/audio/run-1/segment-1.wav','published',
            '2026-01-01T00:00:00+00:00'
        )
        """
    )
    raw.execute(
        """
        INSERT INTO pause_direction_plans VALUES (
            'plan-1','project-1','lock-1','run-1','pause-direction-v1',
            'fake-text-model-v1','discovery-text-gateway-v1',
            'pause-direction-response-v1','fake','fp','[]','completed',
            'narration/pause_plans/plan-1.json',
            '2026-01-01T00:00:00+00:00','narration-v1'
        )
        """
    )
    for direction_id in ("direction-b", "direction-a"):
        raw.execute(
            """
            INSERT INTO pause_directions VALUES (
                ?, 'plan-1', 'after_sentence', 'sentence-1', 'segment-1', 0,
                'emphasis', 0.15, 0.25, 2.5, 'soft', 'legacy', 'low'
            )
            """,
            (direction_id,),
        )
    raw.commit()
    raw.close()

    conn = reg_db.get_registry_connection(tmp_path)
    try:
        assert reg_db.read_schema_version(conn) == "20"
        profile = conn.execute(
            "SELECT * FROM voice_profiles WHERE voice_profile_id = 'profile-1'"
        ).fetchone()
        assert profile["version"] == 1
        assert profile["adapter_version"] == "fake-voice-v1"
        assert profile["audio_format"] == "wav-pcm-s16le"
        assert profile["sample_rate"] == 48000
        assert profile["channels"] == 1
        assert profile["supersedes_voice_profile_id"] is None
        assert profile["output_profile_json"] == output_profile_json
        segment = conn.execute(
            "SELECT script_id FROM voice_segments WHERE segment_id = 'segment-1'"
        ).fetchone()
        assert segment["script_id"] == "script-1"
        directions = conn.execute(
            """
            SELECT direction_id, ordinal
            FROM pause_directions
            WHERE pause_plan_id = 'plan-1'
            ORDER BY ordinal
            """
        ).fetchall()
        assert [(row["direction_id"], row["ordinal"]) for row in directions] == [
            ("direction-a", 0),
            ("direction-b", 1),
        ]
    finally:
        conn.close()


def test_narration_paths_reject_classic_absolute_and_escape() -> None:
    assert assert_narration_relative_path("narration/audio/run/seg.wav")
    assert narration_audio_relative_path("run", "seg").startswith("narration/audio/")
    for value in ["/tmp/x", "_otio/narration/x", "narration/../x", "_otio_v2/narration/x"]:
        with pytest.raises(NarrationPathError):
            assert_narration_relative_path(value)


def test_pause_direction_duration_contract_and_timebases() -> None:
    direction = PauseDirection(
        direction_id="d1",
        pause_plan_id="p1",
        ordinal=0,
        position_kind=PausePositionKind.AFTER_SENTENCE,
        sentence_id="s1",
        segment_id="seg1",
        anchor_ordinal=0,
        function=PauseFunction.EMPHASIS,
        min_duration_intent_s=0.0,
        preferred_duration_intent_s=9.0,
        max_duration_intent_s=9.0,
        hardness=PauseHardness.SOFT,
        rationale="soft clamp",
        uncertainty=PauseUncertainty.LOW,
    )
    assert clamp_pause_duration(direction) == NORMAL_PAUSE_MAX_S
    assert (timebase_from_fps(23.976).fps_numerator, timebase_from_fps(23.976).fps_denominator) == (24000, 1001)
    assert (timebase_from_fps(25.0).fps_numerator, timebase_from_fps(25.0).fps_denominator) == (25, 1)
