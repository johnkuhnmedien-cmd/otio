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


def test_schema_17_creates_only_phase11_narration_tables(tmp_path: Path) -> None:
    conn = reg_db.get_registry_connection(tmp_path)
    try:
        assert reg_db.read_schema_version(conn) == REGISTRY_SCHEMA_VERSION == "17"
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
        assert "visual_edit_plans" not in tables
        assert "otio_exports" not in tables
    finally:
        conn.close()
    conn2 = reg_db.get_registry_connection(tmp_path)
    try:
        assert reg_db.read_schema_version(conn2) == "17"
    finally:
        conn2.close()


def test_schema_16_to_17_migration_preserves_existing_data(tmp_path: Path) -> None:
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
        assert reg_db.read_schema_version(conn) == "17"
        assert conn.execute("SELECT COUNT(*) AS n FROM assets").fetchone()["n"] == 1
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
