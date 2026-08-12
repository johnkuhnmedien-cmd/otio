#!/usr/bin/env python3
"""Real Streamlit smoke for ElevenLabs Music MVP UI (no paid Music API calls).

Starts repository ``app.py`` (not a /tmp mini-app), seeds an Enhanced project
with Intro + 4 body chapters so Music buttons are visible, then screenshots
the Cut Plan page.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from otio_app.defaults import DEFAULT_ENHANCED_WORK_SUBDIR  # noqa: E402
from otio_app.models import ProjectCreate, ProjectMode  # noqa: E402
from otio_app.project_repository import (  # noqa: E402
    create_project,
    find_project_by_root_and_language,
)
from otio_app.services.without_voiceover_enhanced.cut_plan_options import (  # noqa: E402
    CUT_PLAN_MODE_UNIFIED,
    CutPlanOptions,
    save_cut_plan_options,
)
from otio_app.services.without_voiceover_enhanced.io_utils import write_json  # noqa: E402
from otio_app.services.without_voiceover_enhanced.models import (  # noqa: E402
    CutBoundary,
    CutSlot,
    EnhancedScriptDocument,
    ResolvedChapterEnvelope,
    ResolvedShot,
    ResolvedTimelineDocument,
    ScriptSegment,
    UnifiedCutPlanDocument,
)
from otio_app.services.without_voiceover_enhanced.intro_cut_service import (  # noqa: E402
    intro_resolved_timeline_path,
    intro_unified_cut_plan_path,
)
from otio_app.services.without_voiceover_enhanced.paths import (  # noqa: E402
    chapter_resolved_timeline_path,
    chapter_unified_cut_plan_path,
    script_locked_path,
)

PORT = int(os.environ.get("MUSIC_MVP_UI_PORT", "8542"))
PROJECT_ROOT = Path("/opt/cursor/artifacts/music-mvp-smoke-project")
OUT = Path("/opt/cursor/artifacts/screenshots")
REPORT = Path("/opt/cursor/artifacts/reports/elevenlabs_music_mvp_ui_smoke.json")
LOG = Path("/opt/cursor/artifacts/reports/elevenlabs_music_mvp_streamlit.log")


def _plan(folder: str) -> UnifiedCutPlanDocument:
    return UnifiedCutPlanDocument(
        script_version="v1",
        boundaries=[
            CutBoundary(
                cut_id=f"{folder}_cut_000",
                sentence_id=f"{folder}_s1",
                position="start",
                alignment="sentence_boundary",
            ),
            CutBoundary(
                cut_id=f"{folder}_cut_001",
                sentence_id=f"{folder}_s1",
                position="end",
                alignment="sentence_boundary",
            ),
        ],
        slots=[
            CutSlot(
                slot_id=f"{folder}_slot_001",
                local_asset_id="a1",
                asset_fit="strong",
                asset_fit_reason="smoke",
                visual_intent="landscape",
            )
        ],
    )


def _resolved(folder: str, duration: float) -> ResolvedTimelineDocument:
    return ResolvedTimelineDocument(
        script_version="v1",
        fps=25.0,
        total_duration_seconds=duration,
        shots=[
            ResolvedShot(
                shot_id=f"{folder}_slot_001",
                asset_id="a1",
                timeline_start_seconds=0.0,
                timeline_end_seconds=duration,
                source_start_seconds=0.0,
                source_end_seconds=min(duration, 4.0),
                folder_name=folder,
                chapter_id=folder,
            )
        ],
        chapters=[
            ResolvedChapterEnvelope(
                chapter_id=folder,
                folder_name=folder,
                chapter_video_start=0.0,
                chapter_audio_start=0.0,
                chapter_audio_end=duration,
                chapter_video_end=duration,
                first_shot_id=f"{folder}_slot_001",
                last_shot_id=f"{folder}_slot_001",
                segment_ids=[f"{folder}_seg"],
            )
        ],
    )


def _seed_project() -> str:
    chapters = ["Yosemite", "Caddo", "Zion", "Bryce"]
    PROJECT_ROOT.mkdir(parents=True, exist_ok=True)
    for name in chapters:
        (PROJECT_ROOT / name).mkdir(exist_ok=True)
        (PROJECT_ROOT / name / "clip.mp4").write_bytes(b"fake")
    work = PROJECT_ROOT / DEFAULT_ENHANCED_WORK_SUBDIR
    work.mkdir(exist_ok=True)

    existing = find_project_by_root_and_language(
        PROJECT_ROOT, "de", project_mode=ProjectMode.WITHOUT_VOICEOVER_ENHANCED
    )
    if existing is None:
        create_project(
            ProjectCreate(
                name="MusicMVPSmoke",
                project_root=str(PROJECT_ROOT.resolve()),
                work_dir=str(work.resolve()),
                language="de",
                project_mode=ProjectMode.WITHOUT_VOICEOVER_ENHANCED,
                fps=25.0,
                width=1920,
                height=1080,
            ),
            asset_subdir_names=chapters,
            selected_asset_subdirs=chapters,
        )
        existing = find_project_by_root_and_language(
            PROJECT_ROOT, "de", project_mode=ProjectMode.WITHOUT_VOICEOVER_ENHANCED
        )
    assert existing is not None
    project = existing

    segs = [
        ScriptSegment(
            segment_id="intro_1",
            folder_name="Intro",
            text="Welcome to the national parks.",
            sequence_index=0,
            folder_order_index=0,
        )
    ]
    texts = {
        "Yosemite": "Yosemite granite walls rise above the valley floor.",
        "Caddo": "Caddo Lake cypress knees emerge from dark water.",
        "Zion": "Zion sandstone corridors channel desert light.",
        "Bryce": "Bryce hoodoos glow under high desert sun.",
    }
    for i, folder in enumerate(chapters, start=1):
        segs.append(
            ScriptSegment(
                segment_id=f"ch_{i}",
                folder_name=folder,
                text=texts[folder],
                sequence_index=i,
                folder_order_index=i,
            )
        )
    write_json(
        script_locked_path(project),
        EnhancedScriptDocument(
            script_version="v1",
            script_status="locked",
            narration_full=" ".join(s.text for s in segs),
            segments=segs,
        ),
    )
    save_cut_plan_options(
        project,
        CutPlanOptions(cut_plan_mode=CUT_PLAN_MODE_UNIFIED),
    )
    write_json(intro_unified_cut_plan_path(project), _plan("Intro"))
    write_json(intro_resolved_timeline_path(project), _resolved("Intro", 8.0))
    # Chapter 1 has timing (Music enabled if key present; else unavailable)
    write_json(chapter_unified_cut_plan_path(project, "Yosemite"), _plan("Yosemite"))
    write_json(
        chapter_resolved_timeline_path(project, "Yosemite"),
        _resolved("Yosemite", 12.0),
    )
    # Chapter 2 plan only (Music disabled — no timing)
    write_json(chapter_unified_cut_plan_path(project, "Caddo"), _plan("Caddo"))
    # Chapters 3–4 plans + timing for ch3; ch4 shows MVP gate
    write_json(chapter_unified_cut_plan_path(project, "Zion"), _plan("Zion"))
    write_json(
        chapter_resolved_timeline_path(project, "Zion"),
        _resolved("Zion", 10.0),
    )
    write_json(chapter_unified_cut_plan_path(project, "Bryce"), _plan("Bryce"))
    write_json(
        chapter_resolved_timeline_path(project, "Bryce"),
        _resolved("Bryce", 9.0),
    )
    return project.name


def _wait_http(url: str, timeout: float = 120.0) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=2) as resp:
                if resp.status == 200:
                    return
        except Exception:
            time.sleep(0.5)
    raise RuntimeError(f"Streamlit not reachable: {url}")


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    findings: dict = {
        "ok": False,
        "port": PORT,
        "paid_api_call": False,
        "screenshots": [],
        "checks": {},
    }
    name = _seed_project()
    findings["project_name"] = name
    head = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=str(ROOT), text=True
    ).strip()
    findings["head"] = head

    LOG.write_text("", encoding="utf-8")
    proc = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "streamlit",
            "run",
            str(ROOT / "app.py"),
            "--server.port",
            str(PORT),
            "--server.headless",
            "true",
            "--browser.gatherUsageStats",
            "false",
        ],
        cwd=str(ROOT),
        stdout=LOG.open("a", encoding="utf-8"),
        stderr=subprocess.STDOUT,
        env={**os.environ, "STREAMLIT_BROWSER_GATHER_USAGE_STATS": "false"},
    )
    base = f"http://127.0.0.1:{PORT}"
    try:
        _wait_http(base)
        from playwright.sync_api import sync_playwright

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page(viewport={"width": 1600, "height": 2200})

            # Activate Enhanced project via Gespeicherte Projekte
            page.goto(base + "/projekte", wait_until="domcontentloaded", timeout=120_000)
            time.sleep(2.5)
            body = page.inner_text("body")
            findings["checks"]["project_listed"] = "MusicMVPSmoke" in body
            # Streamlit expander: open the MusicMVPSmoke row, then "Projekt bearbeiten"
            expanders = page.locator('[data-testid="stExpander"]')
            opened = False
            for i in range(expanders.count()):
                exp = expanders.nth(i)
                try:
                    text = exp.inner_text(timeout=2_000)
                except Exception:
                    continue
                if "MusicMVPSmoke" not in text:
                    continue
                # Click summary to expand if needed
                summary = exp.locator("summary, [data-testid='stExpanderToggleIcon'], details > summary").first
                if summary.count():
                    try:
                        summary.click(timeout=3_000)
                    except Exception:
                        exp.click(timeout=3_000)
                else:
                    exp.click(timeout=3_000)
                time.sleep(1)
                btn = exp.get_by_role("button", name="Projekt bearbeiten")
                if not btn.count():
                    btn = page.get_by_role("button", name="Projekt bearbeiten")
                if btn.count():
                    btn.first.click(timeout=10_000)
                    opened = True
                    time.sleep(4)
                break
            findings["checks"]["project_opened"] = opened
            if not opened:
                # Fallback: any Projekt bearbeiten on page
                btn = page.get_by_role("button", name="Projekt bearbeiten")
                if btn.count():
                    btn.first.click(timeout=10_000)
                    time.sleep(4)
                    findings["checks"]["project_opened"] = True

            # After open, Enhanced nav should appear (Cut Plan / Final Output / Intro …)
            page.goto(base + "/cut-plan", wait_until="domcontentloaded", timeout=120_000)
            time.sleep(4)
            try:
                page.keyboard.press("Escape")
            except Exception:
                pass
            body = page.inner_text("body")
            findings["checks"]["enhanced_nav"] = (
                "Final Output" in body
                or "Folder Voiceovers" in body
                or "⑦ Cut Plan" in body
                or "Intro Cut" in body
                or "0. Intro Cut" in body
            )
            if "ElevenLabs Music" not in body and "Python Timing" not in body:
                for label in ("⑦ Cut Plan", "Cut Plan"):
                    loc = page.get_by_role("link", name=label)
                    if not loc.count():
                        loc = page.get_by_text(label, exact=False)
                    if loc.count():
                        try:
                            loc.first.click(timeout=5_000)
                            time.sleep(3)
                            body = page.inner_text("body")
                            if "ElevenLabs Music" in body or "Python Timing" in body:
                                break
                        except Exception:
                            pass

            shot_cut = OUT / "elevenlabs-music-mvp-cut-plan.png"
            page.screenshot(path=str(shot_cut), full_page=True)
            findings["screenshots"].append(str(shot_cut))

            findings["checks"]["has_elevenlabs_music"] = "ElevenLabs Music" in body
            findings["checks"]["has_python_timing"] = "Python Timing" in body
            findings["checks"]["has_llm_cut"] = "LLM Cut" in body or "LLM Schnitt" in body
            findings["checks"]["has_otio"] = "OTIO" in body
            findings["checks"]["has_intro_music"] = (
                "ElevenLabs Music" in body and ("Intro" in body)
            )
            findings["checks"]["has_chapter4_gate"] = (
                "nur Kapitel 1–3" in body or "Music MVP" in body
            )
            findings["checks"]["no_traceback"] = "Traceback" not in body
            findings["checks"]["music_unavailable_or_missing"] = (
                "Music nicht verfügbar" in body
                or "API-Key" in body
                or "Music fehlt" in body
                or "ElevenLabs Music" in body
            )
            findings["body_excerpt"] = body[:2500]

            shot_intro = OUT / "elevenlabs-music-mvp-intro-row.png"
            if page.get_by_text("Intro Cut", exact=False).count():
                page.get_by_text("Intro Cut", exact=False).first.scroll_into_view_if_needed()
                time.sleep(0.4)
            page.screenshot(path=str(shot_intro), full_page=False)
            findings["screenshots"].append(str(shot_intro))

            if page.get_by_text("Yosemite", exact=False).count():
                page.get_by_text("Yosemite", exact=False).first.scroll_into_view_if_needed()
                time.sleep(0.5)
            shot_ch = OUT / "elevenlabs-music-mvp-chapter-row.png"
            page.screenshot(path=str(shot_ch), full_page=False)
            findings["screenshots"].append(str(shot_ch))

            browser.close()

        findings["ok"] = bool(
            findings["checks"].get("has_elevenlabs_music")
            and findings["checks"].get("has_python_timing")
            and findings["checks"].get("no_traceback")
        )
    except Exception as exc:  # noqa: BLE001
        findings["error"] = str(exc)
    finally:
        proc.send_signal(subprocess.signal.SIGTERM)
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()

    REPORT.write_text(json.dumps(findings, indent=2), encoding="utf-8")
    print(json.dumps(findings, indent=2))
    return 0 if findings.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
