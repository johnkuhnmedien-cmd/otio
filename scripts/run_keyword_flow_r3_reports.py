#!/usr/bin/env python3
"""R3: persistentes Produktionsprojekt + Referenzmanifest + Closing-Fallback-Proof.

Erzeugt Medien und OTIO dauerhaft unter /opt/cursor/artifacts (kein /tmp).
"""

from __future__ import annotations

import hashlib
import json
import shutil
import sys
from pathlib import Path

import opentimelineio as otio

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tests.test_keyword_flow_e2e_production import (  # noqa: E402
    _build_chapter_a_project,
    _ffmpeg_color_video,
    _plan_with_pause_and_closing,
)
from otio_app.services.without_voiceover_enhanced.local_media_service import (  # noqa: E402
    STATUS_EXPORT_READY,
    is_http_url,
    validate_local_media_path,
)
from otio_app.services.without_voiceover_enhanced.otio_export_service import (  # noqa: E402
    export_otio_from_resolved_timeline,
    export_portable_otio_package,
)
from otio_app.services.without_voiceover_enhanced.unified_timeline_service import (  # noqa: E402
    UnifiedTimelineError,
    resolve_unified_timeline,
)

ART = Path("/opt/cursor/artifacts")
PROJECT_ROOT = ART / "keyword-flow-r3-test-project"
FALLBACK_ROOT = ART / "keyword-flow-r3-fallback-proof"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _iter_external_refs(timeline) -> list[dict]:
    rows: list[dict] = []
    for track in timeline.tracks:
        track_kind = str(getattr(track, "kind", "") or "")
        for clip in track:
            if not isinstance(clip, otio.schema.Clip):
                continue
            media = clip.media_reference
            target = str(getattr(media, "target_url", "") or "") if media else ""
            rows.append(
                {
                    "clip_name": str(clip.name or ""),
                    "track": track_kind,
                    "target_url": target,
                }
            )
    return rows


def _classify_media_kind(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix in {".mp4", ".mov", ".mkv", ".m4v"}:
        return "video"
    if suffix in {".jpg", ".jpeg", ".png", ".webp"}:
        return "photo"
    if suffix in {".wav", ".mp3", ".m4a", ".aac"}:
        return "audio"
    return suffix.lstrip(".") or "unknown"


def _validate_refs(
    rows: list[dict],
    *,
    portable_package: Path | None = None,
) -> list[dict]:
    manifest: list[dict] = []
    package_resolved = portable_package.resolve() if portable_package else None
    for row in rows:
        target = str(row.get("target_url") or "").strip()
        entry = {
            **row,
            "exists": False,
            "media_kind": "unknown",
            "technical_status": "missing_target",
            "technical_detail": None,
            "sha256": None,
        }
        if not target:
            manifest.append(entry)
            continue
        if is_http_url(target):
            entry["technical_status"] = "http_url_forbidden"
            manifest.append(entry)
            continue
        if "/tmp/kf_" in target:
            entry["technical_status"] = "tmp_kf_path_forbidden"
            manifest.append(entry)
            continue

        path = Path(target)
        if not path.is_absolute() and portable_package is not None:
            path = (portable_package / target).resolve()
        entry["exists"] = path.is_file()
        if not entry["exists"]:
            entry["technical_status"] = "file_missing"
            manifest.append(entry)
            continue

        if package_resolved is not None:
            try:
                path.resolve().relative_to(package_resolved)
            except ValueError:
                entry["technical_status"] = "outside_portable_package"
                entry["media_kind"] = _classify_media_kind(path)
                entry["sha256"] = _sha256(path)
                manifest.append(entry)
                continue

        kind = _classify_media_kind(path)
        entry["media_kind"] = kind
        entry["sha256"] = _sha256(path)
        if kind == "audio":
            entry["technical_status"] = "audio_reference"
            manifest.append(entry)
            continue
        media_type = "video" if kind == "video" else "photo" if kind == "photo" else None
        if media_type is None:
            entry["technical_status"] = "unsupported_media_kind"
            manifest.append(entry)
            continue
        status, detail = validate_local_media_path(str(path), media_type=media_type)
        entry["technical_status"] = (
            STATUS_EXPORT_READY if status == STATUS_EXPORT_READY else status
        )
        entry["technical_detail"] = detail
        manifest.append(entry)
    return manifest


def _assert_manifest_ok(manifest: list[dict], *, label: str) -> None:
    allowed = {STATUS_EXPORT_READY, "audio_reference"}
    bad = [
        row
        for row in manifest
        if row.get("technical_status") not in allowed
        or not row.get("exists")
        or is_http_url(str(row.get("target_url") or ""))
        or "/tmp/kf_" in str(row.get("target_url") or "")
    ]
    if bad:
        raise RuntimeError(f"{label}: ungültige ExternalReferences: {bad[:5]}")


def _prepare_project(dest: Path, *, project_id: str, project_name: str):
    if dest.exists():
        shutil.rmtree(dest)
    parent = dest.parent
    parent.mkdir(parents=True, exist_ok=True)
    return _build_chapter_a_project(
        parent,
        project_dirname=dest.name,
        project_id=project_id,
        project_name=project_name,
    )


def _run_happy_path() -> dict:
    project, ids = _prepare_project(
        PROJECT_ROOT,
        project_id="kf-r3",
        project_name="KeywordFlowR3",
    )
    plan = _plan_with_pause_and_closing(ids)
    resolved = resolve_unified_timeline(
        project, plan, allow_open_gaps=False, persist=True
    )
    if resolved.errors:
        raise RuntimeError(f"resolve errors: {resolved.errors}")

    local_otio = export_otio_from_resolved_timeline(
        project, basename="keyword_flow_r3_local", resolved=resolved
    )
    package = export_portable_otio_package(
        project, basename="keyword_flow_r3_portable", allow_errors=False
    )
    portable_otio = package / "timeline.otio"

    local_tl = otio.adapters.read_from_file(str(local_otio))
    portable_tl = otio.adapters.read_from_file(str(portable_otio))
    local_manifest = _validate_refs(_iter_external_refs(local_tl))
    portable_manifest = _validate_refs(
        _iter_external_refs(portable_tl), portable_package=package
    )
    _assert_manifest_ok(local_manifest, label="local")
    _assert_manifest_ok(portable_manifest, label="portable")

    report = {
        "project_root": str(PROJECT_ROOT.resolve()),
        "local_otio": str(local_otio),
        "portable_package": str(package),
        "portable_otio": str(portable_otio),
        "errors": list(resolved.errors),
        "repairs": list(resolved.repairs),
        "closing_fallback_asset_id": plan.closing_fallback_asset_id,
        "closing_fallback_asset_fit": plan.closing_fallback_asset_fit,
        "tmp_refs_local": [
            r["target_url"]
            for r in local_manifest
            if "/tmp/" in str(r.get("target_url") or "")
        ],
        "tmp_refs_portable": [
            r["target_url"]
            for r in portable_manifest
            if "/tmp/" in str(r.get("target_url") or "")
        ],
        "synthetic_timeline": False,
    }
    if report["tmp_refs_local"] or report["tmp_refs_portable"]:
        raise RuntimeError(f"/tmp refs after script: {report}")

    (ART / "keyword-flow-r3-reference-manifest-local.json").write_text(
        json.dumps(local_manifest, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    (ART / "keyword-flow-r3-reference-manifest-portable.json").write_text(
        json.dumps(portable_manifest, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    (ART / "keyword-flow-r3-timeline-report.json").write_text(
        json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8"
    )

    readme = f"""Keyword Flow R3 — portables OTIO-Paket (ohne Resolve-Import in der VM)

Paket: {package}

Inhalt:
  - timeline.otio
  - media_manifest.json
  - media/

Importanleitung (manuell auf dem Nutzerrechner mit DaVinci Resolve):
  1. Gesamtes Paketverzeichnis unverändert kopieren (Ordnerstruktur erhalten).
  2. In Resolve: File → Import → Timeline / OTIO → timeline.otio wählen.
  3. Relativpfade unter media/ müssen neben timeline.otio liegen.
  4. Keinen tatsächlichen Resolve-Import in dieser Cloud-VM behaupten oder erwarten.

Lokale Produktions-OTIO (gleiche Maschine): {local_otio}
Dauerhaftes Testprojekt: {PROJECT_ROOT}
"""
    (ART / "keyword-flow-r3-portable-import-readme.txt").write_text(
        readme, encoding="utf-8"
    )
    (ART / "keyword-flow-r3-resolve-smoke.txt").write_text(
        f"R3 production OTIO local: {local_otio}\n"
        f"R3 portable package: {package}\n"
        f"project: {PROJECT_ROOT}\n"
        "Resolve CLI: not available — OTIO via export + re-read only.\n",
        encoding="utf-8",
    )
    return report


def _run_fallback_proof() -> dict:
    project, ids = _prepare_project(
        FALLBACK_ROOT,
        project_id="kf-r3-fb",
        project_name="KeywordFlowR3Fallback",
    )
    primary = Path(project.project_root) / "ChapterA" / "close_a.mp4"
    primary.write_bytes(b"KF-R3-INVALID-PRIMARY-BYTES")
    plan = _plan_with_pause_and_closing(ids)
    assert plan.closing_fallback_asset_fit in {"strong", "acceptable"}
    resolved = resolve_unified_timeline(
        project, plan, allow_open_gaps=False, persist=True
    )
    if resolved.errors:
        raise RuntimeError(f"fallback proof resolve errors: {resolved.errors}")
    fallback_repairs = [r for r in resolved.repairs if "Fallback" in r]
    if not fallback_repairs:
        raise RuntimeError("fallback proof: no Fallback repair note")
    if not any(ids["fallback"] in r for r in fallback_repairs):
        raise RuntimeError(f"fallback id missing in repairs: {fallback_repairs}")

    local_otio = export_otio_from_resolved_timeline(
        project, basename="keyword_flow_r3_fallback", resolved=resolved
    )
    urls: list[str] = []
    tl = otio.adapters.read_from_file(str(local_otio))
    for track in tl.tracks:
        for clip in track:
            if isinstance(clip, otio.schema.Clip) and clip.media_reference:
                urls.append(str(clip.media_reference.target_url or ""))
    if not any("fallback_a.mp4" in u for u in urls):
        raise RuntimeError(f"OTIO does not reference fallback: {urls}")
    if any("close_a.mp4" in u for u in urls):
        raise RuntimeError(f"OTIO still references damaged primary: {urls}")

    negatives: dict[str, str] = {}
    short_root = ART / "keyword-flow-r3-short-primary"
    p2, ids2 = _prepare_project(
        short_root, project_id="kf-r3-short", project_name="KeywordFlowR3Short"
    )
    _ffmpeg_color_video(
        Path(p2.project_root) / "ChapterA" / "close_a.mp4",
        duration=0.4,
        color="red",
    )
    plan2 = _plan_with_pause_and_closing(ids2)
    r2 = resolve_unified_timeline(p2, plan2, allow_open_gaps=False, persist=True)
    if r2.errors:
        raise RuntimeError(r2.errors)
    if not any("Fallback" in x for x in r2.repairs):
        raise RuntimeError("short primary did not use fallback")
    negatives["primary_too_short"] = "fallback_used"

    both_root = ART / "keyword-flow-r3-both-invalid"
    p3, ids3 = _prepare_project(
        both_root, project_id="kf-r3-both", project_name="KeywordFlowR3Both"
    )
    (Path(p3.project_root) / "ChapterA" / "close_a.mp4").write_bytes(b"x")
    (Path(p3.project_root) / "ChapterA" / "fallback_a.mp4").write_bytes(b"y")
    try:
        resolve_unified_timeline(
            p3, _plan_with_pause_and_closing(ids3), allow_open_gaps=False, persist=True
        )
        raise RuntimeError("both invalid should block")
    except UnifiedTimelineError:
        negatives["both_invalid"] = "blocked"

    weak_root = ART / "keyword-flow-r3-weak-fit"
    p4, ids4 = _prepare_project(
        weak_root, project_id="kf-r3-weak", project_name="KeywordFlowR3Weak"
    )
    plan4 = _plan_with_pause_and_closing(ids4)
    plan4.closing_fallback_asset_fit = "weak"
    try:
        resolve_unified_timeline(p4, plan4, allow_open_gaps=False, persist=True)
        raise RuntimeError("weak fit should block")
    except UnifiedTimelineError:
        negatives["weak_fallback_fit"] = "blocked"

    selection = {
        "primary_asset_id": ids["close"],
        "fallback_asset_id": ids["fallback"],
        "primary_plan_fit": "strong",
        "fallback_plan_fit": plan.closing_fallback_asset_fit,
        "selected_asset_id": ids["fallback"],
        "selection_reason": fallback_repairs[0],
        "otio_path": str(local_otio),
        "otio_urls_sample": urls[:12],
        "negatives": negatives,
        "canonical_validator": "validate_local_media_path / STATUS_EXPORT_READY",
    }
    (ART / "keyword-flow-r3-closing-selection-report.json").write_text(
        json.dumps(selection, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    return selection


def main() -> int:
    ART.mkdir(parents=True, exist_ok=True)
    happy = _run_happy_path()
    proof = _run_fallback_proof()
    summary = {"happy": happy, "fallback_proof": proof, "ok": True}
    (ART / "keyword-flow-r3-reports-run.json").write_text(
        json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    print(json.dumps(summary, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
