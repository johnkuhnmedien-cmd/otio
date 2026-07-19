"""Schema-20-compatible batch identity helpers (no DDL migration).

Batch IDs are encoded into existing TEXT fields (`review_note`, claim `reason`)
so append-only decision rows stay joinable without Schema 21.
"""

from __future__ import annotations

BATCH_MARKER_PREFIX = "__batch__:"


def encode_batch_marker(batch_id: str, *, trailing: str | None = None) -> str:
    batch = str(batch_id).strip()
    if not batch:
        raise ValueError("batch_id required")
    marker = f"{BATCH_MARKER_PREFIX}{batch}"
    note = (trailing or "").strip()
    if note:
        return f"{marker}\n{note}"
    return marker


def parse_batch_id(text: str | None) -> str | None:
    if not text:
        return None
    raw = str(text).strip()
    if not raw.startswith(BATCH_MARKER_PREFIX):
        return None
    rest = raw[len(BATCH_MARKER_PREFIX) :]
    token = rest.split("\n", 1)[0].strip()
    return token or None


__all__ = [
    "BATCH_MARKER_PREFIX",
    "encode_batch_marker",
    "parse_batch_id",
]
