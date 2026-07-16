"""Adapter: streaming SHA-256 über vorhandenen media_utils-Helfer."""

from __future__ import annotations

from pathlib import Path

from otio_app.services.media_utils import file_sha256 as _file_sha256


DEFAULT_HASH_CHUNK_BYTES = 1024 * 1024


def compute_sha256_hex(path: Path, *, chunk_size: int = DEFAULT_HASH_CHUNK_BYTES) -> str:
    """Berechnet einen kleingeschriebenen Hex-SHA-256 blockweise über den Dateiinhalt.

    Nutzt denselben Streaming-Algorithmus wie ``media_utils.file_sha256``.
    Der Dateiname und absolute Pfad fließen nicht in den Hash ein.
    """
    if chunk_size == DEFAULT_HASH_CHUNK_BYTES:
        return _file_sha256(path).lower()

    # Explizite Chunk-Größe für Tests / Sonderfälle (weiterhin gestreamt).
    import hashlib

    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while True:
            chunk = handle.read(chunk_size)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest().lower()
