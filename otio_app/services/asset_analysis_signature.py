"""Datei-/Analyse-Signatur und Cache-Aktualität für Asset Analysis v3."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal, Optional

from otio_app.analysis_models import AssetAnalysisSignature, AssetMediaAnalysis
from otio_app.services.gemini_client import ASSET_DESCRIPTION_PROMPT_VERSION
from otio_app.services.media_utils import NO_ANALYZABLE_MEDIA_DESCRIPTION

ANALYSIS_SCHEMA_VERSION = "asset-analysis-v3"
ASSET_SAMPLER_VERSION = "uniform-v1"
ANALYSIS_SCOPE_FRAMES = "media_frames"

_FINGERPRINT_BLOCK_BYTES = 64 * 1024

AssetCacheStatusName = Literal["current", "stale", "legacy", "invalid", "failed"]


@dataclass(frozen=True)
class AssetCacheStatus:
    status: AssetCacheStatusName
    reasons: list[str] = field(default_factory=list)


def compute_media_content_fingerprint(media_path: Path) -> tuple[str, int, int]:
    """SHA-256 über Größe + ersten/letzten Block (große Dateien nicht vollständig hashen).

    Returns:
        (fingerprint_hex, file_size, mtime_ns)
    """
    stat = media_path.stat()
    size = int(stat.st_size)
    mtime_ns = int(getattr(stat, "st_mtime_ns", int(stat.st_mtime * 1_000_000_000)))
    digest = hashlib.sha256()
    digest.update(str(size).encode("ascii"))
    digest.update(b"|")

    if size <= 0:
        return digest.hexdigest(), size, mtime_ns

    with media_path.open("rb") as handle:
        if size <= _FINGERPRINT_BLOCK_BYTES:
            digest.update(handle.read(size))
        elif size <= _FINGERPRINT_BLOCK_BYTES * 2:
            digest.update(handle.read(size))
        else:
            head = handle.read(_FINGERPRINT_BLOCK_BYTES)
            digest.update(head)
            digest.update(b"|")
            handle.seek(size - _FINGERPRINT_BLOCK_BYTES)
            digest.update(handle.read(_FINGERPRINT_BLOCK_BYTES))
    return digest.hexdigest(), size, mtime_ns


def build_analysis_signature(
    media_path: Path,
    *,
    resolved_model_id: str,
    analysis_schema_version: str = ANALYSIS_SCHEMA_VERSION,
    prompt_version: str = ASSET_DESCRIPTION_PROMPT_VERSION,
    sampler_version: str = ASSET_SAMPLER_VERSION,
) -> AssetAnalysisSignature:
    fingerprint, size, mtime_ns = compute_media_content_fingerprint(media_path)
    return AssetAnalysisSignature(
        analysis_schema_version=analysis_schema_version,
        prompt_version=prompt_version,
        sampler_version=sampler_version,
        resolved_model_id=resolved_model_id,
        file_size=size,
        file_mtime_ns=mtime_ns,
        content_fingerprint=fingerprint,
    )


def try_build_analysis_signature(
    media_path: Path,
    *,
    resolved_model_id: str,
    analysis_schema_version: str = ANALYSIS_SCHEMA_VERSION,
    prompt_version: str = ASSET_DESCRIPTION_PROMPT_VERSION,
    sampler_version: str = ASSET_SAMPLER_VERSION,
) -> Optional[AssetAnalysisSignature]:
    try:
        return build_analysis_signature(
            media_path,
            resolved_model_id=resolved_model_id,
            analysis_schema_version=analysis_schema_version,
            prompt_version=prompt_version,
            sampler_version=sampler_version,
        )
    except OSError:
        return None


def is_usable_asset_analysis(entry: AssetMediaAnalysis) -> bool:
    """Legacy-/Anzeige-tauglich: echte Beschreibung, kein Parse-/Analysefehler."""
    description = entry.description.strip()
    if not description:
        return False
    if description == NO_ANALYZABLE_MEDIA_DESCRIPTION:
        return False
    if entry.analysis_parse_ok is False:
        return False
    if entry.error and entry.error.strip():
        return False
    return True


def classify_asset_cache_status(
    entry: AssetMediaAnalysis | None,
    media_path: Path,
    *,
    resolved_model_id: str,
    analysis_schema_version: str = ANALYSIS_SCHEMA_VERSION,
    prompt_version: str = ASSET_DESCRIPTION_PROMPT_VERSION,
    sampler_version: str = ASSET_SAMPLER_VERSION,
) -> AssetCacheStatus:
    """Maschinelesbarer Cache-Status inkl. Gründe."""
    if entry is None:
        return AssetCacheStatus("failed", ["missing_cache"])

    if entry.analysis_parse_ok is False:
        return AssetCacheStatus("invalid", ["parse_failed"])

    if entry.error and entry.error.strip():
        return AssetCacheStatus("failed", ["analysis_error"])

    description = entry.description.strip()
    if not description or description == NO_ANALYZABLE_MEDIA_DESCRIPTION:
        return AssetCacheStatus("failed", ["analysis_error"])

    signature = entry.analysis_signature
    if signature is None:
        # Altbestand ohne v3-Signatur: anzeigbar, aber nicht current.
        return AssetCacheStatus("legacy", ["missing_signature"])

    reasons: list[str] = []
    if (
        entry.analysis_schema_version != analysis_schema_version
        or signature.analysis_schema_version != analysis_schema_version
    ):
        reasons.append("schema_mismatch")
    if (
        entry.description_prompt_version != prompt_version
        or signature.prompt_version != prompt_version
    ):
        reasons.append("prompt_mismatch")
    if signature.sampler_version != sampler_version:
        reasons.append("sampler_mismatch")
    if signature.resolved_model_id != resolved_model_id:
        reasons.append("model_mismatch")
    if entry.analysis_parse_ok is not True:
        # v3-Signatur ohne explizites parse_ok=True ist nicht current.
        reasons.append("parse_failed" if entry.analysis_parse_ok is False else "missing_signature")

    expected = try_build_analysis_signature(
        media_path,
        resolved_model_id=resolved_model_id,
        analysis_schema_version=analysis_schema_version,
        prompt_version=prompt_version,
        sampler_version=sampler_version,
    )
    if expected is None:
        reasons.append("file_changed")
    elif signature.content_fingerprint != expected.content_fingerprint:
        reasons.append("file_changed")
    elif (
        signature.file_size != expected.file_size
        or signature.file_mtime_ns != expected.file_mtime_ns
    ):
        # Fingerprint ist maßgeblich; abweichende Meta ohne Fingerprint-Diff
        # signalisiert trotzdem eine Dateiänderung.
        if signature.content_fingerprint == expected.content_fingerprint:
            pass
        else:
            reasons.append("file_changed")

    # Deduplizieren, Reihenfolge stabil halten.
    ordered: list[str] = []
    for reason in reasons:
        if reason not in ordered:
            ordered.append(reason)

    if ordered:
        if "missing_signature" in ordered and all(
            reason in {"missing_signature"} for reason in ordered
        ):
            return AssetCacheStatus("legacy", ordered)
        return AssetCacheStatus("stale", ordered)

    if not is_usable_asset_analysis(entry):
        return AssetCacheStatus("failed", ["analysis_error"])

    return AssetCacheStatus("current", [])


def is_current_asset_analysis(
    entry: AssetMediaAnalysis | None,
    media_path: Path,
    *,
    resolved_model_id: str,
    analysis_schema_version: str = ANALYSIS_SCHEMA_VERSION,
    prompt_version: str = ASSET_DESCRIPTION_PROMPT_VERSION,
    sampler_version: str = ASSET_SAMPLER_VERSION,
) -> bool:
    status = classify_asset_cache_status(
        entry,
        media_path,
        resolved_model_id=resolved_model_id,
        analysis_schema_version=analysis_schema_version,
        prompt_version=prompt_version,
        sampler_version=sampler_version,
    )
    return status.status == "current"
