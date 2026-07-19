"""Canonical Coverage Gap Semantic Identity (coverage-gap-semantic-key-v1).

Domain-only contract for the fachliche Problemidentität eines Coverage Gaps.
Does not persist keys, match predecessors, or alter gap_id / materialization.
"""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from typing import Any, Iterable, Literal, Sequence

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from otio_app.discovery_v2.domain.supplementation import (
    CoverageLevel,
    CoverageRiskFlag,
)

COVERAGE_GAP_SEMANTIC_KEY_SCHEMA_VERSION = "coverage-gap-semantic-key-v1"
COVERAGE_GAP_SEMANTIC_KEY_PREFIX = f"{COVERAGE_GAP_SEMANTIC_KEY_SCHEMA_VERSION}:"

COVERAGE_GAP_SEMANTIC_IDENTITY_INVALID = "coverage_gap_semantic_identity_invalid"
COVERAGE_GAP_SEMANTIC_IDENTITY_UNAVAILABLE = "coverage_gap_semantic_identity_unavailable"
COVERAGE_GAP_SEMANTIC_KEY_COLLISION = "coverage_gap_semantic_key_collision"

_ALLOWED_MEDIA_KINDS = frozenset({"video", "image", "audio", "unknown"})
_WHITESPACE_RE = re.compile(r"\s+")


def normalize_semantic_text(value: str | None) -> str:
    """Conservative text normalization: NFC, strip, collapse internal whitespace."""

    if value is None:
        return ""
    text = unicodedata.normalize("NFC", str(value))
    text = text.strip()
    return _WHITESPACE_RE.sub(" ", text)


def normalize_optional_semantic_text(value: str | None) -> str | None:
    normalized = normalize_semantic_text(value)
    return normalized or None


def normalize_semantic_collection(values: Iterable[Any] | None) -> list[str]:
    """Sort, dedupe, drop empties after conservative text normalization."""

    seen: set[str] = set()
    out: list[str] = []
    for raw in values or ():
        item = normalize_semantic_text(None if raw is None else str(raw))
        if not item or item in seen:
            continue
        seen.add(item)
        out.append(item)
    out.sort()
    return out


def _stable_json_bytes(payload: dict[str, Any]) -> bytes:
    return json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


class CanonicalVisualIntentSemantics(BaseModel):
    """Intent semantics that define the coverage problem (no technical IDs)."""

    model_config = ConfigDict(extra="forbid")

    desired_motif: str
    action: str
    setting: str
    geographic_requirements: str | None = None
    authenticity_requirements: list[str] = Field(default_factory=list)
    allowed_media_kinds: list[str] = Field(default_factory=list)

    @field_validator("desired_motif", "action", "setting", mode="before")
    @classmethod
    def _normalize_required_text(cls, value: Any) -> str:
        return normalize_semantic_text(None if value is None else str(value))

    @field_validator("geographic_requirements", mode="before")
    @classmethod
    def _normalize_geo(cls, value: Any) -> str | None:
        if value is None:
            return None
        return normalize_optional_semantic_text(str(value))

    @field_validator("authenticity_requirements", mode="before")
    @classmethod
    def _normalize_auth(cls, value: Any) -> list[str]:
        return normalize_semantic_collection(value)

    @field_validator("allowed_media_kinds", mode="before")
    @classmethod
    def _normalize_media(cls, value: Any) -> list[str]:
        return normalize_semantic_collection(value)

    @model_validator(mode="after")
    def _validate_required_semantics(self) -> CanonicalVisualIntentSemantics:
        if not self.desired_motif or not self.action or not self.setting:
            raise ValueError("required visual-intent texts must be non-empty")
        unknown = [kind for kind in self.allowed_media_kinds if kind not in _ALLOWED_MEDIA_KINDS]
        if unknown:
            raise ValueError(f"unknown allowed_media_kinds: {unknown}")
        return self


class CanonicalCoverageProblemSignature(BaseModel):
    """Coverage-problem signature independent of audit / gap instance IDs."""

    model_config = ConfigDict(extra="forbid")

    coverage_level: str
    missing_properties: list[str] = Field(default_factory=list)
    risk_codes: list[str] = Field(default_factory=list)

    @field_validator("coverage_level", mode="before")
    @classmethod
    def _normalize_level(cls, value: Any) -> str:
        if isinstance(value, CoverageLevel):
            return value.value
        text = normalize_semantic_text(None if value is None else str(value))
        try:
            return CoverageLevel(text).value
        except ValueError as exc:
            raise ValueError(f"unknown coverage_level: {value!r}") from exc

    @field_validator("missing_properties", mode="before")
    @classmethod
    def _normalize_missing(cls, value: Any) -> list[str]:
        return normalize_semantic_collection(value)

    @field_validator("risk_codes", mode="before")
    @classmethod
    def _normalize_risks(cls, value: Any) -> list[str]:
        normalized = normalize_semantic_collection(value)
        validated: list[str] = []
        for code in normalized:
            try:
                validated.append(CoverageRiskFlag(code).value)
            except ValueError as exc:
                raise ValueError(f"unknown risk_code: {code!r}") from exc
        return validated


class CanonicalCoverageGapSemanticIdentity(BaseModel):
    """Versioned canonical identity payload for coverage-gap-semantic-key-v1."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["coverage-gap-semantic-key-v1"] = (
        COVERAGE_GAP_SEMANTIC_KEY_SCHEMA_VERSION
    )
    project_id: str
    visual_intent: CanonicalVisualIntentSemantics
    coverage_problem: CanonicalCoverageProblemSignature

    @field_validator("project_id", mode="before")
    @classmethod
    def _normalize_project_id(cls, value: Any) -> str:
        text = normalize_semantic_text(None if value is None else str(value))
        if not text:
            raise ValueError("project_id must be non-empty")
        return text


def compute_canonical_payload_sha256(
    identity: CanonicalCoverageGapSemanticIdentity,
) -> str:
    payload = identity.model_dump(mode="json")
    return hashlib.sha256(_stable_json_bytes(payload)).hexdigest()


def format_gap_semantic_key(payload_sha256: str) -> str:
    digest = normalize_semantic_text(payload_sha256).lower()
    if len(digest) != 64 or any(ch not in "0123456789abcdef" for ch in digest):
        raise ValueError("canonical_payload_sha256 must be 64 lowercase hex chars")
    return f"{COVERAGE_GAP_SEMANTIC_KEY_PREFIX}{digest}"


def compute_gap_semantic_key(identity: CanonicalCoverageGapSemanticIdentity) -> str:
    """SHA-256 over versioned, UTF-8, sort-stable JSON → prefixed semantic key."""

    return format_gap_semantic_key(compute_canonical_payload_sha256(identity))


def build_canonical_gap_semantic_identity(
    *,
    project_id: str,
    desired_motif: str,
    action: str,
    setting: str,
    geographic_requirements: str | None = None,
    authenticity_requirements: Sequence[Any] | None = None,
    allowed_media_kinds: Sequence[Any] | None = None,
    coverage_level: str | CoverageLevel,
    missing_properties: Sequence[Any] | None = None,
    risk_codes: Sequence[Any] | None = None,
) -> CanonicalCoverageGapSemanticIdentity:
    """Build a validated canonical identity from semantic parts (no IDs)."""

    return CanonicalCoverageGapSemanticIdentity(
        project_id=project_id,
        visual_intent=CanonicalVisualIntentSemantics(
            desired_motif=desired_motif,
            action=action,
            setting=setting,
            geographic_requirements=geographic_requirements,
            authenticity_requirements=list(authenticity_requirements or ()),
            allowed_media_kinds=list(allowed_media_kinds or ()),
        ),
        coverage_problem=CanonicalCoverageProblemSignature(
            coverage_level=coverage_level,
            missing_properties=list(missing_properties or ()),
            risk_codes=list(risk_codes or ()),
        ),
    )


def compare_gap_semantic_identities(
    left: CanonicalCoverageGapSemanticIdentity,
    right: CanonicalCoverageGapSemanticIdentity,
    *,
    left_key: str | None = None,
    right_key: str | None = None,
) -> tuple[bool, str | None, str]:
    """Compare identities under the future exact-match contract.

    Returns ``(ok, error_code, message)``.
    Same schema + same key + identical payload → ok.
    Same key + different payload → collision.
    """

    left_digest = compute_canonical_payload_sha256(left)
    right_digest = compute_canonical_payload_sha256(right)
    resolved_left_key = left_key or format_gap_semantic_key(left_digest)
    resolved_right_key = right_key or format_gap_semantic_key(right_digest)

    if left.schema_version != right.schema_version:
        return (
            False,
            COVERAGE_GAP_SEMANTIC_IDENTITY_INVALID,
            "schema_version mismatch",
        )

    if resolved_left_key == resolved_right_key and left_digest != right_digest:
        return (
            False,
            COVERAGE_GAP_SEMANTIC_KEY_COLLISION,
            "semantic key collision: identical key with different payload",
        )

    if (
        resolved_left_key == resolved_right_key
        and left_digest == right_digest
        and left.model_dump(mode="json") == right.model_dump(mode="json")
    ):
        return True, None, "identical semantic identity"

    if left.model_dump(mode="json") == right.model_dump(mode="json"):
        return True, None, "identical semantic identity"

    return False, None, "semantic identities differ"


class CoverageGapIdentityError(ValueError):
    """Fail-closed domain error for semantic gap identity checks."""

    def __init__(self, message: str, *, error_code: str) -> None:
        super().__init__(message)
        self.error_code = error_code


def require_compatible_gap_semantic_identities(
    left: CanonicalCoverageGapSemanticIdentity,
    right: CanonicalCoverageGapSemanticIdentity,
    *,
    left_key: str | None = None,
    right_key: str | None = None,
) -> None:
    """Raise on semantic-key collision or invalid schema pairing."""

    ok, error_code, message = compare_gap_semantic_identities(
        left,
        right,
        left_key=left_key,
        right_key=right_key,
    )
    if error_code == COVERAGE_GAP_SEMANTIC_KEY_COLLISION:
        raise CoverageGapIdentityError(message, error_code=error_code)
    if error_code == COVERAGE_GAP_SEMANTIC_IDENTITY_INVALID:
        raise CoverageGapIdentityError(message, error_code=error_code)
    if not ok and error_code:
        raise CoverageGapIdentityError(message, error_code=error_code)


__all__ = [
    "COVERAGE_GAP_SEMANTIC_IDENTITY_INVALID",
    "COVERAGE_GAP_SEMANTIC_IDENTITY_UNAVAILABLE",
    "COVERAGE_GAP_SEMANTIC_KEY_COLLISION",
    "COVERAGE_GAP_SEMANTIC_KEY_PREFIX",
    "COVERAGE_GAP_SEMANTIC_KEY_SCHEMA_VERSION",
    "CanonicalCoverageGapSemanticIdentity",
    "CanonicalCoverageProblemSignature",
    "CanonicalVisualIntentSemantics",
    "CoverageGapIdentityError",
    "build_canonical_gap_semantic_identity",
    "compare_gap_semantic_identities",
    "compute_canonical_payload_sha256",
    "compute_gap_semantic_key",
    "format_gap_semantic_key",
    "normalize_optional_semantic_text",
    "normalize_semantic_collection",
    "normalize_semantic_text",
    "require_compatible_gap_semantic_identities",
]
