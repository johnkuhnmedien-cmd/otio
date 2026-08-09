"""Tests für Asset-Analyse-Signatur und Cache-Aktualität."""

from __future__ import annotations

import json
from pathlib import Path

from otio_app.analysis_models import AssetAnalysisSignature, AssetMediaAnalysis
from otio_app.services.asset_analysis_signature import (
    ANALYSIS_SCHEMA_VERSION,
    ASSET_SAMPLER_VERSION,
    build_analysis_signature,
    classify_asset_cache_status,
    compute_media_content_fingerprint,
    is_current_asset_analysis,
    is_usable_asset_analysis,
)
from otio_app.services.gemini_client import ASSET_DESCRIPTION_PROMPT_VERSION
from otio_app.services.media_inventory_cache import load_cached_media, save_cached_media


def _write_media(path: Path, payload: bytes = b"video-bytes-v1") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)
    return path


def test_fingerprint_stable_for_unchanged_file(tmp_path: Path) -> None:
    media = _write_media(tmp_path / "clip.mp4", b"abcdefgh" * 1000)
    a = compute_media_content_fingerprint(media)
    b = compute_media_content_fingerprint(media)
    assert a[0] == b[0]
    assert a[1] == media.stat().st_size


def test_fingerprint_changes_when_content_changes(tmp_path: Path) -> None:
    media = _write_media(tmp_path / "clip.mp4", b"head" + (b"x" * 200_000) + b"tail")
    before = compute_media_content_fingerprint(media)[0]
    media.write_bytes(b"HEAD" + (b"x" * 200_000) + b"TAIL")
    after = compute_media_content_fingerprint(media)[0]
    assert before != after


def test_current_when_signature_matches(tmp_path: Path) -> None:
    media = _write_media(tmp_path / "clip.mp4")
    signature = build_analysis_signature(media, resolved_model_id="gemini-test")
    entry = AssetMediaAnalysis(
        path=str(media),
        description="OK Beschreibung",
        analysis_parse_ok=True,
        analysis_schema_version=ANALYSIS_SCHEMA_VERSION,
        description_prompt_version=ASSET_DESCRIPTION_PROMPT_VERSION,
        analysis_signature=signature,
    )
    status = classify_asset_cache_status(
        entry, media, resolved_model_id="gemini-test"
    )
    assert status.status == "current"
    assert is_current_asset_analysis(entry, media, resolved_model_id="gemini-test")


def test_stale_when_file_changes(tmp_path: Path) -> None:
    media = _write_media(tmp_path / "clip.mp4", b"original")
    signature = build_analysis_signature(media, resolved_model_id="gemini-test")
    entry = AssetMediaAnalysis(
        path=str(media),
        description="OK",
        analysis_parse_ok=True,
        analysis_schema_version=ANALYSIS_SCHEMA_VERSION,
        description_prompt_version=ASSET_DESCRIPTION_PROMPT_VERSION,
        analysis_signature=signature,
    )
    media.write_bytes(b"changed-content")
    status = classify_asset_cache_status(
        entry, media, resolved_model_id="gemini-test"
    )
    assert status.status == "stale"
    assert "file_changed" in status.reasons


def test_stale_when_prompt_version_changes(tmp_path: Path) -> None:
    media = _write_media(tmp_path / "clip.mp4")
    signature = build_analysis_signature(media, resolved_model_id="gemini-test")
    signature.prompt_version = "asset_v2_structured"
    entry = AssetMediaAnalysis(
        path=str(media),
        description="OK",
        analysis_parse_ok=True,
        analysis_schema_version=ANALYSIS_SCHEMA_VERSION,
        description_prompt_version="asset_v2_structured",
        analysis_signature=signature,
    )
    status = classify_asset_cache_status(
        entry, media, resolved_model_id="gemini-test"
    )
    assert status.status == "stale"
    assert "prompt_mismatch" in status.reasons


def test_v3_editorial_r1_cache_stale_under_r2_prompt(
    tmp_path: Path,
) -> None:
    """v3-r1-Caches bleiben usable, sind aber bei aktueller r2-Version stale."""
    media = _write_media(tmp_path / "clip.mp4")
    assert ASSET_DESCRIPTION_PROMPT_VERSION == "asset_v3_editorial_r2"
    assert ANALYSIS_SCHEMA_VERSION == "asset-analysis-v3"
    assert ASSET_SAMPLER_VERSION == "uniform-v1"

    r1_signature = build_analysis_signature(
        media,
        resolved_model_id="gemini-test",
        prompt_version="asset_v3_editorial",
    )
    entry = AssetMediaAnalysis(
        path=str(media),
        description="R1 Analyse usable",
        analysis_parse_ok=True,
        analysis_schema_version=ANALYSIS_SCHEMA_VERSION,
        description_prompt_version="asset_v3_editorial",
        analysis_signature=r1_signature,
    )
    status = classify_asset_cache_status(
        entry, media, resolved_model_id="gemini-test"
    )
    assert status.status == "stale"
    assert "prompt_mismatch" in status.reasons
    assert is_usable_asset_analysis(entry)
    assert not is_current_asset_analysis(
        entry, media, resolved_model_id="gemini-test"
    )
    # Schema/Sampler der aktuellen Foundation bleiben unverändert.
    current_sig = build_analysis_signature(media, resolved_model_id="gemini-test")
    assert current_sig.analysis_schema_version == ANALYSIS_SCHEMA_VERSION
    assert current_sig.sampler_version == ASSET_SAMPLER_VERSION
    assert current_sig.prompt_version == ASSET_DESCRIPTION_PROMPT_VERSION


def test_stale_when_schema_version_changes(tmp_path: Path) -> None:
    media = _write_media(tmp_path / "clip.mp4")
    signature = build_analysis_signature(media, resolved_model_id="gemini-test")
    entry = AssetMediaAnalysis(
        path=str(media),
        description="OK",
        analysis_parse_ok=True,
        analysis_schema_version="asset-analysis-v2",
        description_prompt_version=ASSET_DESCRIPTION_PROMPT_VERSION,
        analysis_signature=signature.model_copy(
            update={"analysis_schema_version": "asset-analysis-v2"}
        ),
    )
    status = classify_asset_cache_status(
        entry, media, resolved_model_id="gemini-test"
    )
    assert status.status == "stale"
    assert "schema_mismatch" in status.reasons


def test_stale_when_sampler_version_changes(tmp_path: Path) -> None:
    media = _write_media(tmp_path / "clip.mp4")
    signature = build_analysis_signature(media, resolved_model_id="gemini-test")
    entry = AssetMediaAnalysis(
        path=str(media),
        description="OK",
        analysis_parse_ok=True,
        analysis_schema_version=ANALYSIS_SCHEMA_VERSION,
        description_prompt_version=ASSET_DESCRIPTION_PROMPT_VERSION,
        analysis_signature=signature.model_copy(
            update={"sampler_version": "scene-v1"}
        ),
    )
    status = classify_asset_cache_status(
        entry, media, resolved_model_id="gemini-test"
    )
    assert status.status == "stale"
    assert "sampler_mismatch" in status.reasons
    assert ASSET_SAMPLER_VERSION == "uniform-v1"


def test_stale_when_resolved_model_changes(tmp_path: Path) -> None:
    media = _write_media(tmp_path / "clip.mp4")
    signature = build_analysis_signature(media, resolved_model_id="model-a")
    entry = AssetMediaAnalysis(
        path=str(media),
        description="OK",
        analysis_parse_ok=True,
        analysis_schema_version=ANALYSIS_SCHEMA_VERSION,
        description_prompt_version=ASSET_DESCRIPTION_PROMPT_VERSION,
        analysis_signature=signature,
    )
    status = classify_asset_cache_status(
        entry, media, resolved_model_id="model-b"
    )
    assert status.status == "stale"
    assert "model_mismatch" in status.reasons


def test_missing_signature_is_legacy_not_current(tmp_path: Path) -> None:
    media = _write_media(tmp_path / "clip.mp4")
    entry = AssetMediaAnalysis(path=str(media), description="Legacy OK")
    status = classify_asset_cache_status(
        entry, media, resolved_model_id="gemini-test"
    )
    assert status.status == "legacy"
    assert "missing_signature" in status.reasons
    assert is_usable_asset_analysis(entry)
    assert not is_current_asset_analysis(
        entry, media, resolved_model_id="gemini-test"
    )


def test_parse_failed_is_invalid(tmp_path: Path) -> None:
    media = _write_media(tmp_path / "clip.mp4")
    entry = AssetMediaAnalysis(
        path=str(media),
        description="",
        analysis_parse_ok=False,
        analysis_raw_response="{bad",
        error="parse failed",
    )
    status = classify_asset_cache_status(
        entry, media, resolved_model_id="gemini-test"
    )
    assert status.status == "invalid"
    assert "parse_failed" in status.reasons
    assert not is_usable_asset_analysis(entry)


def test_legacy_cache_json_without_v3_fields_loads(tmp_path: Path) -> None:
    cache_file = tmp_path / "clip.mp4.json"
    cache_file.write_text(
        json.dumps({"path": "/tmp/clip.mp4", "description": "Altbestand"}),
        encoding="utf-8",
    )
    loaded = load_cached_media(cache_file)
    assert loaded is not None
    assert loaded.description == "Altbestand"
    assert loaded.analysis_parse_ok is None
    assert loaded.analysis_signature is None
    assert loaded.quality_profile is None


def test_corrupt_cache_json_handled(tmp_path: Path) -> None:
    cache_file = tmp_path / "clip.mp4.json"
    cache_file.write_bytes(b"\xceinvalid")
    loaded = load_cached_media(cache_file)
    assert loaded is None
    assert not cache_file.exists()


def test_roundtrip_persists_v3_fields(tmp_path: Path) -> None:
    from otio_app.analysis_models import (
        AssetFramingProfile,
        AssetLookProfile,
        AssetMotionProfile,
        AssetQualityProfile,
        AssetDefect,
    )

    media = _write_media(tmp_path / "clip.mp4")
    signature = build_analysis_signature(media, resolved_model_id="gemini-test")
    entry = AssetMediaAnalysis(
        path=str(media),
        description="OK",
        caption="Short caption",
        content_tags=["a", "b"],
        analysis_parse_ok=True,
        analysis_schema_version=ANALYSIS_SCHEMA_VERSION,
        description_prompt_version=ASSET_DESCRIPTION_PROMPT_VERSION,
        description_model="gemini-test",
        description_model_requested="",
        description_model_resolved="gemini-test",
        analysis_raw_response="",
        motion_profile=AssetMotionProfile(type="static", direction="none"),
        framing_profile=AssetFramingProfile(type="wide", shot_scale="wide"),
        look_profile=AssetLookProfile(brightness=None, color_temperature="cool"),
        quality_profile=AssetQualityProfile(
            technical_quality=80,
            composition_quality=70,
            visual_appeal=75,
            subject_clarity=90,
            hero_potential=60,
            defect_severity=5,
        ),
        defect_items=[AssetDefect(type="blur", severity=5, note="soft")],
        analysis_signature=signature,
    )
    cache_file = tmp_path / "out.json"
    dumped = entry.model_dump_json(indent=2)
    cache_file.write_text(dumped, encoding="utf-8")
    loaded = AssetMediaAnalysis.model_validate(json.loads(dumped))
    assert loaded.caption == "Short caption"
    assert loaded.content_tags == ["a", "b"]
    assert loaded.analysis_signature is not None
    assert loaded.analysis_signature.content_fingerprint == signature.content_fingerprint
    assert loaded.motion_profile is not None and loaded.motion_profile.type == "static"
    assert loaded.look_profile is not None and loaded.look_profile.brightness is None
    assert loaded.quality_profile is not None
    assert loaded.quality_profile.hero_potential == 60
    assert loaded.defect_items[0].type == "blur"
    assert loaded.analysis_raw_response == ""
    assert isinstance(loaded.analysis_signature, AssetAnalysisSignature)


def test_mtime_only_change_keeps_current(tmp_path: Path) -> None:
    """Content-Fingerprint ist maßgeblich — bloßes Touch erzwingt keinen Re-Run."""
    import os
    import time

    media = _write_media(tmp_path / "clip.mp4", b"stable-content")
    signature = build_analysis_signature(media, resolved_model_id="gemini-test")
    entry = AssetMediaAnalysis(
        path=str(media),
        description="OK",
        analysis_parse_ok=True,
        analysis_schema_version=ANALYSIS_SCHEMA_VERSION,
        description_prompt_version=ASSET_DESCRIPTION_PROMPT_VERSION,
        analysis_signature=signature,
    )
    later = time.time() + 120
    os.utime(media, (later, later))
    status = classify_asset_cache_status(
        entry, media, resolved_model_id="gemini-test"
    )
    assert status.status == "current"
    assert "file_changed" not in status.reasons


def test_fingerprint_changes_when_head_changes_same_size(tmp_path: Path) -> None:
    body = b"x" * 200_000
    media = _write_media(tmp_path / "clip.mp4", b"HEAD1" + body + b"TAIL1")
    before = compute_media_content_fingerprint(media)[0]
    media.write_bytes(b"HEAD2" + body + b"TAIL1")
    assert media.stat().st_size == len(b"HEAD1" + body + b"TAIL1")
    after = compute_media_content_fingerprint(media)[0]
    assert before != after


def test_fingerprint_changes_when_tail_changes_same_size(tmp_path: Path) -> None:
    body = b"x" * 200_000
    media = _write_media(tmp_path / "clip.mp4", b"HEAD1" + body + b"TAIL1")
    before = compute_media_content_fingerprint(media)[0]
    media.write_bytes(b"HEAD1" + body + b"TAIL2")
    assert media.stat().st_size == len(b"HEAD1" + body + b"TAIL1")
    after = compute_media_content_fingerprint(media)[0]
    assert before != after


def test_unreadable_media_is_not_current(tmp_path: Path) -> None:
    missing = tmp_path / "gone.mp4"
    entry = AssetMediaAnalysis(
        path=str(missing),
        description="OK",
        analysis_parse_ok=True,
        analysis_schema_version=ANALYSIS_SCHEMA_VERSION,
        description_prompt_version=ASSET_DESCRIPTION_PROMPT_VERSION,
        analysis_signature=AssetAnalysisSignature(
            analysis_schema_version=ANALYSIS_SCHEMA_VERSION,
            prompt_version=ASSET_DESCRIPTION_PROMPT_VERSION,
            sampler_version=ASSET_SAMPLER_VERSION,
            resolved_model_id="gemini-test",
            file_size=1,
            file_mtime_ns=1,
            content_fingerprint="deadbeef",
        ),
    )
    status = classify_asset_cache_status(
        entry, missing, resolved_model_id="gemini-test"
    )
    assert status.status == "stale"
    assert "file_changed" in status.reasons


def test_freshness_uses_effective_clean_media_path(
    temp_project_layout: dict[str, Path],
    monkeypatch,
) -> None:
    from otio_app.models import Project
    from otio_app.services.media_inventory_cache import (
        has_successful_asset_cache,
        media_cache_path,
        save_cached_media,
    )

    project = Project(
        id="clean-path-test",
        name="Test",
        project_root=str(temp_project_layout["project_root"]),
        work_dir=str(temp_project_layout["work_dir"]),
        asset_subdir_names=["Grand Canyon"],
        selected_asset_subdirs=["Grand Canyon"],
    )
    original = temp_project_layout["project_root"] / "Grand Canyon" / "clip.mp4"
    clean = temp_project_layout["work_dir"] / "clean" / "clip_clean.mp4"
    clean.parent.mkdir(parents=True, exist_ok=True)
    clean.write_bytes(b"clean-bytes-different-from-original")
    original.write_bytes(b"original-bytes")

    monkeypatch.setattr(
        "otio_app.services.media_inventory_cache.resolve_media_for_analysis",
        lambda project, folder_name, media_path: clean,
    )
    signature = build_analysis_signature(clean, resolved_model_id="gemini-test")
    # Signatur/Fingerprint vom Clean-Medium; Freshness muss denselben Pfad nutzen.
    from otio_app.services.gemini_client import resolve_gemini_model

    resolved = resolve_gemini_model(None)
    signature = build_analysis_signature(clean, resolved_model_id=resolved)
    save_cached_media(
        media_cache_path(project, "Grand Canyon", original),
        AssetMediaAnalysis(
            path=str(clean),
            description="Clean analyse",
            analysis_parse_ok=True,
            analysis_schema_version=ANALYSIS_SCHEMA_VERSION,
            description_prompt_version=ASSET_DESCRIPTION_PROMPT_VERSION,
            description_model_resolved=resolved,
            analysis_signature=signature,
        ),
    )
    assert has_successful_asset_cache(project, "Grand Canyon", original)
    # Fingerprint gegen Original wäre falsch → würde stale liefern; wir erwarten current.
