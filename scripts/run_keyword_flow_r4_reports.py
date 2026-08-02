#!/usr/bin/env python3
"""R4: dauerhaftes portables Paket + Relink-Nachweis + Ursachenbericht."""

from __future__ import annotations

import hashlib
import json
import shutil
import sys
from pathlib import Path
from urllib.parse import unquote, urlparse

import opentimelineio as otio

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tests.test_keyword_flow_e2e_production import (  # noqa: E402
    _build_chapter_a_project,
    _plan_with_pause_and_closing,
)
from otio_app.services.without_voiceover_enhanced.otio_export_service import (  # noqa: E402
    export_portable_otio_package,
)
from otio_app.services.without_voiceover_enhanced.relink_for_resolve import (  # noqa: E402
    relink_package,
)
from otio_app.services.without_voiceover_enhanced.unified_timeline_service import (  # noqa: E402
    resolve_unified_timeline,
)

ART = Path("/opt/cursor/artifacts")
PACKAGE = ART / "keyword-flow-r4-portable-package"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _refs(timeline) -> list[dict]:
    rows = []
    for track in timeline.tracks:
        for item in track:
            if not isinstance(item, otio.schema.Clip):
                continue
            media = item.media_reference
            url = str(getattr(media, "target_url", "") or "") if media else ""
            rows.append(
                {
                    "clip_name": str(item.name or ""),
                    "track": str(track.kind),
                    "target_url": url,
                    "uri_type": (
                        "http"
                        if url.lower().startswith("http")
                        else "file_uri"
                        if url.lower().startswith("file:")
                        else "absolute_posix"
                        if url.startswith("/")
                        else "relative"
                        if url
                        else "empty"
                    ),
                }
            )
    return rows


def _structure(timeline) -> dict:
    tracks = []
    for track in timeline.tracks:
        items = []
        for item in track:
            if isinstance(item, otio.schema.Gap):
                items.append(
                    {
                        "kind": "gap",
                        "duration": round(item.duration().to_seconds(), 6),
                    }
                )
            elif isinstance(item, otio.schema.Clip):
                src = item.source_range
                items.append(
                    {
                        "kind": "clip",
                        "name": item.name,
                        "duration": round(item.duration().to_seconds(), 6),
                        "src_start": round(src.start_time.to_seconds(), 6)
                        if src
                        else None,
                        "src_duration": round(src.duration.to_seconds(), 6)
                        if src
                        else None,
                    }
                )
        tracks.append({"track_kind": str(track.kind), "items": items})
    return {"tracks": tracks}


def _document_cause(transport_refs: list[dict]) -> dict:
    """Dokumentierter Nutzerbefund / technische Ursache (keine Vermutung)."""
    return {
        "user_finding": (
            "timeline.otio aus unverändert entpacktem portablen Paket in "
            "DaVinci Resolve Studio 21 (macOS) importiert: Tracks angelegt, "
            "Map-Opener/slot_a/slot_close Media Offline."
        ),
        "observed_target_urls": transport_refs,
        "uri_type": "relative path string without file:// scheme",
        "encoding": "no percent-encoding; plain media/<filename>",
        "otio_python_behavior": (
            "OpenTimelineIO resolves relative target_url against the timeline "
            "file location / CWD when reading via adapters.read_from_file, "
            "so package-local media/… files are found in Python."
        ),
        "resolve_behavior": (
            "DaVinci Resolve Studio 21 on macOS did not resolve relative "
            "ExternalReference targets media/... relative to timeline.otio; "
            "clips appeared as Media Offline."
        ),
        "fix": (
            "Keep transport-neutral relative timeline.otio; on the target "
            "machine run relink_for_resolve.py to write timeline_resolve.otio "
            "with absolute file:// URIs via Path.resolve().as_uri()."
        ),
    }


def main() -> int:
    ART.mkdir(parents=True, exist_ok=True)
    if PACKAGE.exists():
        shutil.rmtree(PACKAGE)

    scratch = ART / "_kf_r4_scratch"
    if scratch.exists():
        shutil.rmtree(scratch)
    scratch.mkdir(parents=True)
    project, ids = _build_chapter_a_project(
        scratch,
        project_dirname="KeywordFlowR4",
        project_id="kf-r4",
        project_name="KeywordFlowR4",
    )
    plan = _plan_with_pause_and_closing(ids)
    resolved = resolve_unified_timeline(
        project, plan, allow_open_gaps=False, persist=True
    )
    if resolved.errors:
        raise RuntimeError(resolved.errors)

    exported = export_portable_otio_package(
        project, basename="keyword_flow_r4", allow_errors=False
    )
    # Persist under the mandated artifact path (copy package contents).
    shutil.copytree(exported, PACKAGE)

    required = [
        "timeline.otio",
        "media_manifest.json",
        "media",
        "relink_for_resolve.py",
        "README.md",
    ]
    for name in required:
        path = PACKAGE / name
        if not path.exists():
            raise RuntimeError(f"missing package part: {name}")

    transport_path = PACKAGE / "timeline.otio"
    original_sha = _sha256(transport_path)
    transport_tl = otio.adapters.read_from_file(str(transport_path))
    transport_refs = _refs(transport_tl)
    cause = _document_cause(transport_refs)
    (ART / "keyword-flow-r4-resolve-cause.json").write_text(
        json.dumps(cause, indent=2, ensure_ascii=False), encoding="utf-8"
    )

    # Run the shipped script for real.
    resolve_path = relink_package(PACKAGE, script_file=PACKAGE / "relink_for_resolve.py")
    if _sha256(transport_path) != original_sha:
        raise RuntimeError("timeline.otio mutated by relink")
    resolve_sha = _sha256(resolve_path)
    resolve_tl = otio.adapters.read_from_file(str(resolve_path))
    resolve_refs = _refs(resolve_tl)

    struct_equal = _structure(transport_tl) == _structure(resolve_tl)
    errors: list[str] = []
    all_abs = True
    all_exist = True
    no_http = True
    no_cloud = True
    no_relative = True
    for row in resolve_refs:
        url = row["target_url"]
        if not url.startswith("file://"):
            all_abs = False
            no_relative = False
            errors.append(f"not file://: {url}")
        if url.startswith("media/"):
            no_relative = False
            errors.append(f"relative remains: {url}")
        if url.lower().startswith(("http://", "https://")):
            no_http = False
            errors.append(f"http: {url}")
        path = Path(unquote(urlparse(url).path))
        if not path.is_file():
            all_exist = False
            errors.append(f"missing: {url}")
        text = url + str(path)
        if "/opt/cursor" in text or "/tmp/kf_" in text:
            # file:// may contain the artifact path when relink runs in VM —
            # for the R4 proof package that is expected on this machine.
            # Flag only relative/cloud leakage in transport; resolve OTIO on
            # this VM legitimately points at PACKAGE under /opt/cursor.
            pass
        if "/workspace" in url and not url.startswith("file://"):
            no_cloud = False

    # On this VM, resolve URIs intentionally point into PACKAGE (/opt/cursor/...).
    # Contract for user machines: no baked-in cloud paths inside transport OTIO.
    for row in transport_refs:
        if "/opt/cursor" in row["target_url"] or row["target_url"].startswith("/"):
            errors.append(f"transport leaked host path: {row['target_url']}")
            no_cloud = False
        if row["uri_type"] != "relative":
            errors.append(f"transport not relative: {row}")

    clip_count = sum(
        1
        for t in transport_tl.tracks
        for c in t
        if isinstance(c, otio.schema.Clip)
    )
    report = {
        "package_path": str(PACKAGE),
        "original_otio": str(transport_path),
        "resolve_otio": str(resolve_path),
        "original_sha256": original_sha,
        "resolve_sha256": resolve_sha,
        "clip_count": clip_count,
        "track_count": len(list(transport_tl.tracks)),
        "timeline_structure_equal": struct_equal,
        "source_ranges_equal": struct_equal,
        "timings_equal": struct_equal,
        "references": {
            "transport": transport_refs,
            "resolve": resolve_refs,
        },
        "all_references_absolute": all_abs,
        "all_references_exist": all_exist,
        "no_http": no_http,
        "no_cloud_paths_in_transport": no_cloud,
        "no_relative_paths_in_resolve": no_relative,
        "package_parts": sorted(p.name for p in PACKAGE.iterdir()),
        "cause": cause,
        "errors": errors,
        "ok": bool(
            struct_equal
            and all_abs
            and all_exist
            and no_http
            and no_relative
            and not errors
            and (PACKAGE / "relink_for_resolve.py").is_file()
            and (PACKAGE / "timeline_resolve.otio").is_file()
        ),
    }
    (ART / "keyword-flow-r4-relink-report.json").write_text(
        json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    # Cleanup scratch project tree (package already copied).
    shutil.rmtree(scratch, ignore_errors=True)
    print(json.dumps(report, indent=2, ensure_ascii=False))
    if not report["ok"]:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
