#!/usr/bin/env python3
"""Real Streamlit smoke for ElevenLabs SFX MVP UI (no paid LLM/SFX calls).

Starts repository ``app.py`` (not a /tmp mini-app), seeds an Enhanced project
with Intro + 4 body chapters so Music + Sound Effects buttons are visible,
then screenshots the Cut Plan page (settings + button row + chapter-4 gate).
"""

from __future__ import annotations

import json
import os
import signal
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

PORT = int(os.environ.get("SFX_MVP_UI_PORT", "8543"))
PROJECT_ROOT = Path("/opt/cursor/artifacts/sfx-mvp-smoke-project")
OUT = Path("/opt/cursor/artifacts/screenshots")
REPORT = Path("/opt/cursor/artifacts/reports/elevenlabs_sfx_mvp_ui_smoke.json")
LOG = Path("/opt/cursor/artifacts/reports/elevenlabs_sfx_mvp_streamlit.log")


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
                name="SfxMVPSmoke",
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
        CutPlanOptions(
            cut_plan_mode=CUT_PLAN_MODE_UNIFIED,
            sfx_planner_model="openai:gpt-5.6-sol",
            max_sfx_per_chapter=3,
        ),
    )
    write_json(intro_unified_cut_plan_path(project), _plan("Intro"))
    write_json(intro_resolved_timeline_path(project), _resolved("Intro", 8.0))
    write_json(chapter_unified_cut_plan_path(project, "Yosemite"), _plan("Yosemite"))
    write_json(
        chapter_resolved_timeline_path(project, "Yosemite"),
        _resolved("Yosemite", 12.0),
    )
    write_json(chapter_unified_cut_plan_path(project, "Caddo"), _plan("Caddo"))
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
            page = browser.new_page(viewport={"width": 1600, "height": 2400})

            page.goto(base + "/projekte", wait_until="networkidle", timeout=120_000)
            time.sleep(2.0)
            body = page.inner_text("body")
            findings["checks"]["project_listed"] = "SfxMVPSmoke" in body
            opened = False
            try:
                page.get_by_text("SfxMVPSmoke", exact=False).first.click(timeout=8_000)
                time.sleep(0.6)
                page.get_by_role("button", name="Projekt bearbeiten").click(timeout=10_000)
                opened = True
                time.sleep(3.5)
            except Exception as exc:
                findings["checks"]["project_open_error"] = str(exc)
            findings["checks"]["project_opened"] = opened

            # After open, Streamlit switches to Analysen; expand nav + Cut Plan.
            try:
                page.get_by_text("View 5 more", exact=False).click(timeout=4_000)
                time.sleep(0.5)
            except Exception:
                pass
            navigated = False
            for label in ("⑥ Cut Plan", "⑦ Cut Plan", "Cut Plan"):
                loc = page.get_by_text(label, exact=False)
                if loc.count():
                    try:
                        loc.first.click(timeout=8_000)
                        time.sleep(3.5)
                        navigated = True
                        break
                    except Exception:
                        pass
            if not navigated:
                page.goto(base + "/cut-plan", wait_until="domcontentloaded", timeout=120_000)
                time.sleep(3.5)
            findings["checks"]["navigated_cut_plan"] = navigated

            try:
                page.keyboard.press("Escape")
            except Exception:
                pass
            body = page.inner_text("body")
            findings["checks"]["on_cut_plan"] = (
                "Sound Effects" in body
                or "Python Timing" in body
                or "Cut Plan Settings" in body
                or "Unified Cut" in body
            )

            # Open Cut Plan Settings for SFX settings screenshot.
            settings = page.get_by_text("Cut Plan Settings", exact=False)
            if settings.count():
                try:
                    settings.first.click(timeout=5_000)
                    time.sleep(1.5)
                except Exception:
                    pass
            body = page.inner_text("body")
            shot_settings = OUT / "elevenlabs-sfx-mvp-settings.png"
            page.screenshot(path=str(shot_settings), full_page=True)
            findings["screenshots"].append(str(shot_settings))
            findings["checks"]["has_sfx_planner_model"] = "SFX Planner Model" in body
            findings["checks"]["has_max_sfx"] = "Maximum SFX per chapter" in body
            findings["checks"]["has_gpt56_sol"] = (
                "GPT-5.6 Sol" in body
                or "gpt-5.6-sol" in body
                or "Flagship" in body
            )

            shot_cut = OUT / "elevenlabs-sfx-mvp-cut-plan.png"
            page.screenshot(path=str(shot_cut), full_page=True)
            findings["screenshots"].append(str(shot_cut))

            findings["checks"]["has_sound_effects"] = "Sound Effects" in body
            findings["checks"]["has_music"] = "Music" in body or "ElevenLabs Music" in body
            findings["checks"]["has_python_timing"] = "Python Timing" in body
            findings["checks"]["has_llm_cut"] = "LLM Cut" in body or "LLM Schnitt" in body
            findings["checks"]["has_otio"] = "OTIO" in body
            findings["checks"]["has_chapter4_sfx_gate"] = (
                "Sound Effects MVP: nur Kapitel 1–3" in body
                or ("nur Kapitel 1–3" in body and "Sound Effects" in body)
            )
            findings["checks"]["no_traceback"] = "Traceback" not in body
            findings["checks"]["sfx_unavailable_or_present"] = (
                "Sound Effects nicht verfügbar" in body
                or "API-Key" in body
                or "Sound Effects" in body
            )
            findings["body_excerpt"] = body[:3000]

            if page.get_by_text("Intro Cut", exact=False).count():
                page.get_by_text("Intro Cut", exact=False).first.scroll_into_view_if_needed()
                time.sleep(0.4)
            shot_intro = OUT / "elevenlabs-sfx-mvp-intro-row.png"
            page.screenshot(path=str(shot_intro), full_page=False)
            findings["screenshots"].append(str(shot_intro))

            if page.get_by_text("Bryce", exact=False).count():
                page.get_by_text("Bryce", exact=False).first.scroll_into_view_if_needed()
                time.sleep(0.5)
            shot_ch4 = OUT / "elevenlabs-sfx-mvp-chapter4-gate.png"
            page.screenshot(path=str(shot_ch4), full_page=False)
            findings["screenshots"].append(str(shot_ch4))

            browser.close()

        findings["ok"] = bool(
            findings["checks"].get("has_sound_effects")
            and findings["checks"].get("has_python_timing")
            and findings["checks"].get("no_traceback")
            and findings["checks"].get("has_sfx_planner_model")
        )
    except Exception as exc:  # noqa: BLE001
        findings["error"] = str(exc)
    finally:
        proc.send_signal(signal.SIGTERM)
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()

    REPORT.write_text(json.dumps(findings, indent=2), encoding="utf-8")
    print(json.dumps(findings, indent=2))
    return 0 if findings.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
