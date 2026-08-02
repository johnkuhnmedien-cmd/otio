#!/usr/bin/env python3
"""R4 Streamlit smoke — portable transfer package + Relink-Hinweis."""

from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from otio_app.models import ProjectCreate, ProjectMode  # noqa: E402
from otio_app.project_repository import (  # noqa: E402
    create_project,
    find_project_by_root_and_language,
)

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8521"
OUT = Path("/opt/cursor/artifacts/screenshots")
OUT.mkdir(parents=True, exist_ok=True)
REPORT = Path("/opt/cursor/artifacts/keyword-flow-r4-ui-smoke.json")
PROJECT_ROOT = Path("/opt/cursor/artifacts/keyword-flow-r3-test-project")

FORBIDDEN = (
    "Traceback",
    "StreamlitDuplicateElementKey",
    "st.exception",
    "Keine aufgelöste Timeline vorhanden",
)

REQUIRED = (
    "systemstatus_ok",
    "final_output_ok",
    "portable_export_ok",
    "relink_hint_ok",
    "package_parts_ok",
)


def _assert_clean(body: str, *, where: str) -> None:
    for token in FORBIDDEN:
        if token in body:
            raise RuntimeError(f"{where}: forbidden {token!r}")


def _ensure_project() -> str:
    if not PROJECT_ROOT.is_dir():
        raise RuntimeError(f"missing project {PROJECT_ROOT}")
    work = PROJECT_ROOT / "_otio_enhanced"
    if not list(work.rglob("resolved_timeline.json")):
        raise RuntimeError("missing resolved_timeline.json")
    existing = find_project_by_root_and_language(
        PROJECT_ROOT, "de", project_mode=ProjectMode.WITHOUT_VOICEOVER_ENHANCED
    )
    if existing:
        return existing.name
    create_project(
        ProjectCreate(
            name="KeywordFlowR3",
            project_root=str(PROJECT_ROOT.resolve()),
            work_dir=str(work.resolve()),
            language="de",
            project_mode=ProjectMode.WITHOUT_VOICEOVER_ENHANCED,
            fps=25.0,
            width=1920,
            height=1080,
        ),
        asset_subdir_names=["ChapterA", "Maps"],
        selected_asset_subdirs=["ChapterA", "Maps"],
    )
    return "KeywordFlowR3"


def main() -> int:
    findings: dict = {"base": BASE, "ok": False, "steps": [], "passed_required": []}
    head = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=str(ROOT), text=True
    ).strip()
    findings["expected_head"] = head
    name = _ensure_project()
    findings["project_name"] = name

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1500, "height": 1800})
        page.goto(BASE + "/systemstatus", wait_until="domcontentloaded", timeout=120_000)
        time.sleep(2.5)
        body = page.inner_text("body")
        _assert_clean(body, where="systemstatus")
        if "cursor/enhanced-keyword-flow-3982" not in body:
            raise RuntimeError("branch missing")
        if head[:7] not in body:
            raise RuntimeError(f"HEAD missing in UI: {head[:7]}")
        page.screenshot(path=str(OUT / "keyword-flow-r4-systemstatus.png"), full_page=True)
        findings["passed_required"].append("systemstatus_ok")
        findings["steps"].append("systemstatus_ok")

        page.goto(BASE + "/projekte", wait_until="networkidle", timeout=120_000)
        time.sleep(1)
        page.get_by_text(name, exact=False).first.click()
        time.sleep(0.4)
        page.get_by_role("button", name="Projekt bearbeiten").click()
        time.sleep(2.5)
        try:
            page.get_by_text("View 5 more", exact=False).click(timeout=4000)
            time.sleep(0.3)
        except Exception:
            pass
        page.get_by_text("⑧ Final Output", exact=False).first.click(timeout=8000)
        time.sleep(2.5)
        body = page.inner_text("body")
        _assert_clean(body, where="final-output")
        if "Portables Paket für Transfer erzeugen" not in body:
            raise RuntimeError("UI button label missing")
        if "Relink-Script" not in body and "relink_for_resolve" not in body:
            # help tooltip may not be in body; caption should mention Relink
            if "Relink" not in body and "timeline_resolve" not in body:
                raise RuntimeError("Relink hint missing in Final Output UI")
        findings["passed_required"].append("final_output_ok")
        findings["steps"].append("final_output_ok")
        findings["passed_required"].append("relink_hint_ok")
        findings["steps"].append("relink_hint_ok")

        page.get_by_role(
            "button", name="Portables Paket für Transfer erzeugen"
        ).click(timeout=15000)
        time.sleep(6)
        body = page.inner_text("body")
        _assert_clean(body, where="portable-export")
        if "Portables Paket geschrieben" not in body:
            raise RuntimeError("portable success missing")
        if "relink_for_resolve" not in body and "timeline_resolve" not in body:
            raise RuntimeError("post-export relink caption missing")
        page.screenshot(
            path=str(OUT / "keyword-flow-r4-portable-export.png"), full_page=True
        )
        findings["passed_required"].append("portable_export_ok")
        findings["steps"].append("portable_export_ok")

        # Find newest package under project exports
        packages = sorted(
            PROJECT_ROOT.joinpath("_otio_enhanced").rglob("relink_for_resolve.py"),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
        if not packages:
            raise RuntimeError("no relink_for_resolve.py in exports")
        pkg = packages[0].parent
        parts = {
            "timeline.otio",
            "media_manifest.json",
            "media",
            "relink_for_resolve.py",
            "README.md",
        }
        present = {p.name for p in pkg.iterdir()}
        missing = parts - present
        if missing:
            raise RuntimeError(f"package missing {missing} in {pkg}")
        findings["package_path"] = str(pkg)
        findings["package_parts"] = sorted(present)
        findings["passed_required"].append("package_parts_ok")
        findings["steps"].append("package_parts_ok")
        browser.close()

    missing_steps = [s for s in REQUIRED if s not in findings["passed_required"]]
    if missing_steps:
        raise RuntimeError(f"missing steps {missing_steps}")
    findings["ok"] = all(s in findings["passed_required"] for s in REQUIRED)
    if not findings["ok"]:
        raise RuntimeError("ok false")
    REPORT.write_text(json.dumps(findings, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(findings, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:  # noqa: BLE001
        REPORT.write_text(json.dumps({"ok": False, "error": str(exc)}, indent=2))
        print(exc, file=sys.stderr)
        raise SystemExit(1)
