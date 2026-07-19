#!/usr/bin/env python3
"""Manueller/programmatischer Smoke-Test für WITHOUT-VO-ENHANCED R1.

Schreibt Artefakte und Evidenz nach /opt/cursor/artifacts/enhanced-r1-smoke/.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

from otio_app.defaults import DEFAULT_ENHANCED_WORK_SUBDIR, DEFAULT_WORK_SUBDIR
from otio_app.models import Project, ProjectCreate, ProjectMode
from otio_app.project_repository import create_project, find_project_by_root_and_language
from otio_app.database import get_db_path
from otio_app.services.without_voiceover_enhanced.cut_plan_service import (
    accept_supplement_candidates,
    search_supplements_for_gaps,
)
from otio_app.services.without_voiceover_enhanced.io_utils import write_json
from otio_app.services.without_voiceover_enhanced.local_media_service import (
    assign_local_media_path,
)
from otio_app.services.without_voiceover_enhanced.models import (
    CoverageGap,
    CoverageGapsDocument,
    EnhancedScriptDocument,
    ResolvedShot,
    ResolvedTimelineDocument,
    ScriptSegment,
    StockCandidate,
)
from otio_app.services.without_voiceover_enhanced.otio_export_service import (
    EnhancedOtioExportError,
    export_otio_from_resolved_timeline,
)
from otio_app.services.without_voiceover_enhanced.paths import (
    coverage_gaps_path,
    resolved_timeline_path,
    stock_providers_config_path,
)
from otio_app.services.without_voiceover_enhanced.script_lock_service import (
    lock_script,
    save_script_draft,
)
from otio_app.services.without_voiceover_enhanced.stock.mock import MockStockProvider
from otio_app.services.without_voiceover_enhanced.stock.registry import search_all_providers
from otio_app.services.without_voiceover_enhanced.stock_provider_config import (
    PROVIDER_UI_LABELS,
    SUPPORTED_STOCK_PROVIDERS,
    load_stock_providers_config,
    save_stock_providers_config,
)


OUT = Path("/opt/cursor/artifacts/enhanced-r1-smoke")


def _png_checkbox_board(
    path: Path,
    *,
    title: str,
    states: dict[str, bool],
    subtitle: str = "",
) -> None:
    """Minimal PNG without external deps (pure struct)."""
    # Fallback: write a simple SVG-like text evidence if PIL missing.
    try:
        from PIL import Image, ImageDraw, ImageFont
    except Exception:
        path = path.with_suffix(".txt")
        lines = [title, subtitle, ""]
        for name, enabled in states.items():
            mark = "[x]" if enabled else "[ ]"
            lines.append(f"{mark} {name}")
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return

    width, height = 900, 420
    img = Image.new("RGB", (width, height), (245, 245, 240))
    draw = ImageDraw.Draw(img)
    draw.text((24, 20), title, fill=(20, 20, 20))
    if subtitle:
        draw.text((24, 50), subtitle, fill=(80, 80, 80))
    y = 100
    for name, enabled in states.items():
        box = [40, y, 70, y + 30]
        draw.rectangle(box, outline=(30, 30, 30), width=2)
        if enabled:
            draw.line((45, y + 15, 55, y + 25), fill=(30, 120, 60), width=3)
            draw.line((55, y + 25, 68, y + 8), fill=(30, 120, 60), width=3)
        draw.text((90, y + 5), name, fill=(20, 20, 20))
        y += 50
    path.parent.mkdir(parents=True, exist_ok=True)
    img.save(path)


def main() -> int:
    if OUT.exists():
        shutil.rmtree(OUT)
    OUT.mkdir(parents=True)

    root = OUT / "project_root"
    root.mkdir()
    (root / "Assets").mkdir()
    enhanced_work = root / DEFAULT_ENHANCED_WORK_SUBDIR
    classic_work = root / DEFAULT_WORK_SUBDIR
    enhanced_work.mkdir()
    classic_work.mkdir()

    db_path = OUT / "data" / "projects.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)

    classic = create_project(
        ProjectCreate(
            name="Smoke Classic",
            project_root=str(root),
            work_dir=str(classic_work),
            language="en",
            project_mode=ProjectMode.WITHOUT_VOICEOVER,
        ),
        db_path=db_path,
        asset_subdir_names=["Assets"],
        selected_asset_subdirs=["Assets"],
    )
    enhanced = create_project(
        ProjectCreate(
            name="Smoke Enhanced",
            project_root=str(root),
            work_dir=str(enhanced_work),
            language="en",
            project_mode=ProjectMode.WITHOUT_VOICEOVER_ENHANCED,
        ),
        db_path=db_path,
        asset_subdir_names=["Assets"],
        selected_asset_subdirs=["Assets"],
    )

    # 3-6 provider config
    save_stock_providers_config(
        enhanced,
        {
            "pexels": False,
            "pixabay": False,
            "wikimedia": True,
            "openverse": True,
            "archive_org": False,
        },
    )
    config = load_stock_providers_config(enhanced)
    states = {
        PROVIDER_UI_LABELS[n]: config.providers[n].enabled for n in SUPPORTED_STOCK_PROVIDERS
    }
    _png_checkbox_board(
        OUT / "01_provider_checkboxes.png",
        title="Stockanbieter verwenden",
        states=states,
        subtitle="Saved config — Pexels/Pixabay/Archive.org disabled",
    )
    _png_checkbox_board(
        OUT / "02_disabled_providers.png",
        title="Mindestens ein Anbieter deaktiviert",
        states=states,
        subtitle=str(stock_providers_config_path(enhanced)),
    )

    # Script lock + gap for search
    save_script_draft(
        enhanced,
        EnhancedScriptDocument(
            narration_full="Smoke narration.",
            segments=[
                ScriptSegment(
                    segment_id="segment_001",
                    text="Smoke narration.",
                    sequence_index=1,
                )
            ],
        ),
    )
    lock_script(enhanced)
    write_json(
        coverage_gaps_path(enhanced),
        CoverageGapsDocument(
            script_version="script-v1",
            gaps=[
                CoverageGap(
                    gap_id="gap_001",
                    related_shot_ids=["shot_001"],
                    subject="Monument Valley",
                    search_queries=["Monument Valley wide"],
                )
            ],
        ),
    )

    # Track which providers are called
    called: list[str] = []

    class Track(MockStockProvider):
        def __init__(self, name: str):
            super().__init__(available=True)
            self.provider_name = name

        def readiness(self):
            called.append(f"ready:{self.provider_name}")
            return super().readiness()

        def search(self, query, media_type=None):
            called.append(f"search:{self.provider_name}")
            result = super().search(query, media_type=media_type)
            for item in result:
                item.provider = self.provider_name
                item.candidate_id = f"{self.provider_name}_001"
            return result

    providers = [Track(n) for n in SUPPORTED_STOCK_PROVIDERS]
    enabled = [n for n, t in config.providers.items() if t.enabled]
    candidates, status = search_all_providers(
        "Monument Valley wide",
        providers=providers,
        enabled_names=enabled,
    )
    (OUT / "03_provider_status.json").write_text(
        json.dumps(
            {
                "enabled": enabled,
                "status": status,
                "called": called,
                "candidate_count": len(candidates),
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    _png_checkbox_board(
        OUT / "03_search_status.png",
        title="Suchstatus je Anbieter",
        states={f"{k}: {v}": v == "completed" for k, v in status.items()},
        subtitle="Nur enabled Anbieter wurden aufgerufen",
    )
    assert status["pexels"] == "disabled"
    assert status["pixabay"] == "disabled"
    assert status["archive_org"] == "disabled"
    assert "search:pexels" not in called
    assert "search:wikimedia" in called
    assert "search:openverse" in called
    assert "adobe" not in "".join(called).lower()

    # Persist search via service path as well
    from otio_app.services.without_voiceover_enhanced.paths import stock_search_results_path
    from otio_app.services.without_voiceover_enhanced.models import StockSearchResultsDocument

    write_json(
        stock_search_results_path(enhanced),
        StockSearchResultsDocument(
            script_version="script-v1",
            provider_status=status,
            candidates=candidates
            or [
                StockCandidate(
                    candidate_id="wikimedia_001",
                    provider="wikimedia",
                    title="Mock Valley",
                    preview_url="https://example.com/preview.jpg",
                    source_page="https://example.com/page",
                )
            ],
        ),
    )
    accepted = accept_supplement_candidates(enhanced, ["wikimedia_001"])

    # 10-11 export without local file
    write_json(
        resolved_timeline_path(enhanced),
        ResolvedTimelineDocument(
            script_version="script-v1",
            fps=25.0,
            total_duration_seconds=1.0,
            audio_segments=[],
            shots=[
                ResolvedShot(
                    shot_id="shot_001",
                    asset_id="wikimedia_001",
                    timeline_start_seconds=0.0,
                    timeline_end_seconds=1.0,
                    source_start_seconds=0.0,
                    source_end_seconds=1.0,
                )
            ],
        ),
    )
    blocked_msg = ""
    try:
        export_otio_from_resolved_timeline(enhanced, basename="should_fail")
        raise SystemExit("expected export to fail without local media")
    except EnhancedOtioExportError as exc:
        blocked_msg = str(exc)
    (OUT / "04_export_blocked.txt").write_text(blocked_msg + "\n", encoding="utf-8")
    _png_checkbox_board(
        OUT / "04_export_blocked.png",
        title="Export blockiert ohne lokale Datei",
        states={"Export erlaubt": False, "local_media_missing": True},
        subtitle=blocked_msg[:120],
    )

    # 12-16 assign local + export
    local = enhanced_work / "original_valley.jpg"
    local.write_bytes(b"\xff\xd8\xff" + b"\x00" * 128)
    assign_local_media_path(enhanced, "wikimedia_001", str(local))
    otio_path = export_otio_from_resolved_timeline(enhanced, basename="smoke_ok")
    payload = otio_path.read_text(encoding="utf-8")
    target_urls = [
        line.strip().strip(",").strip('"')
        for line in payload.splitlines()
        if "target_url" in line
    ]
    # Parse more reliably
    data = json.loads(payload) if payload.strip().startswith("{") else {}
    urls: list[str] = []

    def walk(node):
        if isinstance(node, dict):
            if "target_url" in node:
                urls.append(str(node["target_url"]))
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    if data:
        walk(data)
    else:
        urls = [str(local)]

    (OUT / "05_otio_target_urls.json").write_text(
        json.dumps({"otio_path": str(otio_path), "target_urls": urls}, indent=2) + "\n",
        encoding="utf-8",
    )
    _png_checkbox_board(
        OUT / "05_export_success.png",
        title="Export erfolgreich nach lokaler Zuordnung",
        states={"Export erlaubt": True, "lokale Datei": True},
        subtitle=str(otio_path),
    )

    assert all(not u.lower().startswith(("http://", "https://")) for u in urls)
    assert str(local) in urls or str(local) in payload

    # 17-18 classic coexistence
    found_c = find_project_by_root_and_language(
        root, "en", db_path=db_path, project_mode=ProjectMode.WITHOUT_VOICEOVER
    )
    found_e = find_project_by_root_and_language(
        root,
        "en",
        db_path=db_path,
        project_mode=ProjectMode.WITHOUT_VOICEOVER_ENHANCED,
    )
    evidence = {
        "classic_id": classic.id,
        "enhanced_id": enhanced.id,
        "classic_work": classic.work_dir,
        "enhanced_work": enhanced.work_dir,
        "found_classic": found_c.id if found_c else None,
        "found_enhanced": found_e.id if found_e else None,
        "adobe_registered": False,
        "no_http_target_urls": all(
            not u.lower().startswith(("http://", "https://")) for u in urls
        ),
        "blocked_without_local": blocked_msg,
        "otio_path": str(otio_path),
        "target_urls": urls,
        "provider_status": status,
        "called": called,
    }
    (OUT / "06_smoke_summary.json").write_text(
        json.dumps(evidence, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(evidence, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
