"""Bytegenaue Copy-Übernahme für Discovery-V2 Working Media (Phase 7B).

Ablauf: Temp-Datei → Source-/Output-Hash → Re-Probe → atomare Veröffentlichung.
Keine Remux-/Transcode-/Encode-Pfade. Originalquellen werden nicht verändert.
"""

from __future__ import annotations

import os
import shutil
from dataclasses import dataclass
from pathlib import Path

from otio_app.discovery_v2.adapters.media_probe import (
    MediaProbeAdapterError,
    NormalizedMediaProbe,
    probe_source_media,
)
from otio_app.discovery_v2.adapters.source_hash import compute_sha256_hex
from otio_app.discovery_v2.domain.inventory import MediaKind
from otio_app.discovery_v2.paths import assert_path_is_under_discovery_v2


@dataclass(frozen=True)
class ByteCopyResult:
    source_sha256: str
    output_sha256: str
    working_path: Path
    probe: NormalizedMediaProbe


class ByteCopyError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def publish_byte_exact_copy(
    *,
    project_root: Path,
    source_path: Path,
    temp_path: Path,
    working_path: Path,
    media_kind: MediaKind,
    expected_source_sha256: str | None = None,
) -> ByteCopyResult:
    """Kopiert bytegenau über Temp, prüft Hash + Probe, veröffentlicht atomar.

    ``source_path`` wird nur gelesen. ``temp_path`` und ``working_path`` müssen
    unter ``_otio_v2`` liegen.
    """
    assert_path_is_under_discovery_v2(temp_path, project_root)
    assert_path_is_under_discovery_v2(working_path, project_root)

    if not source_path.is_file() or source_path.is_symlink():
        raise ByteCopyError(
            "source_missing",
            f"Quelldatei nicht als reguläre Datei lesbar: {source_path}",
        )

    try:
        source_sha = compute_sha256_hex(source_path)
    except OSError as exc:
        raise ByteCopyError("source_hash_failed", str(exc)) from exc

    if expected_source_sha256 and source_sha != expected_source_sha256.lower():
        raise ByteCopyError(
            "source_hash_mismatch",
            "Quell-Hash weicht vom Plan/Validation-Hash ab.",
        )

    temp_path.parent.mkdir(parents=True, exist_ok=True)
    working_path.parent.mkdir(parents=True, exist_ok=True)

    # Vorherige Temp-Reste entfernen.
    try:
        if temp_path.exists():
            temp_path.unlink()
    except OSError:
        pass

    try:
        # Inhaltliche Kopie — Original bleibt unverändert.
        shutil.copyfile(source_path, temp_path)
    except OSError as exc:
        raise ByteCopyError("copy_failed", f"Temp-Kopie fehlgeschlagen: {exc}") from exc

    try:
        output_sha = compute_sha256_hex(temp_path)
    except OSError as exc:
        _cleanup(temp_path)
        raise ByteCopyError("output_hash_failed", str(exc)) from exc

    if output_sha != source_sha:
        _cleanup(temp_path)
        raise ByteCopyError(
            "hash_mismatch",
            "Source- und Output-Hash stimmen nicht überein.",
        )

    try:
        probe = probe_source_media(temp_path, media_kind=media_kind)
    except MediaProbeAdapterError as exc:
        _cleanup(temp_path)
        raise ByteCopyError(exc.code, exc.message) from exc
    except Exception as exc:  # noqa: BLE001
        _cleanup(temp_path)
        raise ByteCopyError("output_probe_failed", str(exc)) from exc

    try:
        os.replace(str(temp_path), str(working_path))
    except OSError as exc:
        _cleanup(temp_path)
        raise ByteCopyError(
            "publish_failed",
            f"Atomare Veröffentlichung fehlgeschlagen: {exc}",
        ) from exc

    return ByteCopyResult(
        source_sha256=source_sha,
        output_sha256=output_sha,
        working_path=working_path,
        probe=probe,
    )


def _cleanup(path: Path) -> None:
    try:
        if path.exists():
            path.unlink()
    except OSError:
        pass
