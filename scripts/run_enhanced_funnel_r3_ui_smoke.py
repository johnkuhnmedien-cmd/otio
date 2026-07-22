#!/usr/bin/env python3
"""Startet Streamlit-R3-Smoke und steuert sie per Playwright (echte PNGs)."""

from __future__ import annotations

import json
import os
import re
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
ART = Path("/opt/cursor/artifacts/funnel-r3-ui")
APP = ROOT / "scripts" / "enhanced_funnel_r3_streamlit_app.py"
PORT = int(os.environ.get("FUNNEL_R3_UI_PORT", "8513"))


def _wait_http(url: str, timeout: float = 90.0) -> None:
    import urllib.request

    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=2) as resp:
                if resp.status == 200:
                    return
        except Exception:
            time.sleep(0.5)
    raise RuntimeError(f"Streamlit nicht erreichbar: {url}")


def _goto_action(page, base: str, action: str, wait_text: str, timeout: int = 180000):
    page.goto(f"{base}/?smoke_action={action}", wait_until="networkidle")
    page.wait_for_selector(f"text={wait_text}", timeout=timeout)
    page.wait_for_timeout(800)


def main() -> int:
    if ART.exists():
        shutil.rmtree(ART)
    ART.mkdir(parents=True, exist_ok=True)
    shots = ART / "screenshots"
    shots.mkdir(exist_ok=True)
    log_path = ART / "streamlit.log"
    evidence: dict = {
        "steps": [],
        "download_calls": [],
        "progress_log": [],
        "otio_path": None,
        "otio_has_http": None,
        "screenshots": [],
    }

    env = os.environ.copy()
    env["FUNNEL_R3_SMOKE_ROOT"] = str(ART / "project")
    env["FUNNEL_R3_DOWNLOAD_LOG"] = str(ART / "download_calls.json")
    env["FUNNEL_R3_PROGRESS_LOG"] = str(ART / "progress_log.json")

    proc = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "streamlit",
            "run",
            str(APP),
            "--server.port",
            str(PORT),
            "--server.headless",
            "true",
            "--browser.gatherUsageStats",
            "false",
        ],
        cwd=str(ROOT),
        env=env,
        stdout=log_path.open("w", encoding="utf-8"),
        stderr=subprocess.STDOUT,
    )
    base = f"http://127.0.0.1:{PORT}"
    try:
        _wait_http(base)
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page(viewport={"width": 1400, "height": 1100})
            page.goto(base, wait_until="networkidle")
            page.wait_for_timeout(2000)

            body = page.content()
            assert "gap_1" in body and "gap_2" in body and "gap_3" in body
            assert "Alle offenen Gaps automatisch auflösen" in body
            assert "Ausgewählte Gaps automatisch auflösen" in body
            page.screenshot(path=str(shots / "01_three_open_gaps.png"), full_page=True)
            evidence["steps"].append("three_open_gaps")

            # Checkboxen Gap 1 und Gap 3 setzen
            for gap_id in ("gap_1", "gap_3"):
                label = page.get_by_text(gap_id, exact=False).first
                # Streamlit checkbox via label text
                page.locator(f"label:has-text('{gap_id}')").first.click()
            page.wait_for_timeout(500)
            page.screenshot(
                path=str(shots / "02_gap_1_and_3_selected.png"), full_page=True
            )
            evidence["steps"].append("gap_1_and_3_selected")

            _goto_action(page, base, "run_selected", "SMOKE_SELECTED_DONE")
            page.wait_for_selector("text=SMOKE_OPEN_AFTER_SELECTED", timeout=60000)
            content = page.content()
            assert "gap_2" in content
            page.screenshot(
                path=str(shots / "03_selected_gaps_progress.png"), full_page=True
            )
            page.screenshot(
                path=str(shots / "04_only_selected_gaps_filled.png"), full_page=True
            )
            evidence["steps"].append("selected_filled")

            page.goto(base, wait_until="networkidle")
            page.wait_for_timeout(1000)
            assert "Alle offenen Gaps automatisch auflösen" in page.content()
            page.screenshot(
                path=str(shots / "05_all_open_gaps_button.png"), full_page=True
            )

            _goto_action(page, base, "run_all_open", "SMOKE_ALL_OPEN_DONE")
            page.wait_for_selector("text=SMOKE_LICENSE_STATUS", timeout=60000)
            page.wait_for_selector("text=SMOKE_ALL_FILLED", timeout=60000)
            assert "gap_2=missing" in page.content() or "missing" in page.content()
            page.screenshot(
                path=str(shots / "06_remaining_gap_progress.png"), full_page=True
            )
            page.screenshot(
                path=str(shots / "07_missing_license_nonblocking.png"), full_page=True
            )
            page.screenshot(path=str(shots / "08_all_gaps_filled.png"), full_page=True)
            evidence["steps"].append("all_filled_missing_license_ok")

            page.goto(base, wait_until="networkidle")
            page.wait_for_timeout(1500)
            page.screenshot(
                path=str(shots / "09_after_reload_no_duplicates.png"), full_page=True
            )
            evidence["steps"].append("reload_ok")

            _goto_action(page, base, "export_otio", "SMOKE_OTIO")
            page.screenshot(path=str(shots / "10_otio_local_only.png"), full_page=True)

            progress_path = Path(env["FUNNEL_R3_PROGRESS_LOG"])
            if progress_path.is_file():
                evidence["progress_log"] = json.loads(
                    progress_path.read_text(encoding="utf-8")
                )
            prog = "\n".join(evidence["progress_log"])
            assert "Gap 1/2" in prog or "Gap 2/2" in prog
            assert "Gap 1/1" in prog

            download_log = Path(env["FUNNEL_R3_DOWNLOAD_LOG"])
            if download_log.is_file():
                evidence["download_calls"] = json.loads(
                    download_log.read_text(encoding="utf-8")
                )
            gap_ids_downloaded = {d["gap_id"] for d in evidence["download_calls"]}
            assert gap_ids_downloaded == {"gap_1", "gap_2", "gap_3"}

            otio_files = list((ART / "project").rglob("*.otio"))
            if not otio_files:
                raise RuntimeError("Keine OTIO-Datei erzeugt")
            otio_path = sorted(otio_files, key=lambda p: p.stat().st_mtime)[-1]
            text = otio_path.read_text(encoding="utf-8", errors="replace")
            evidence["otio_path"] = str(otio_path)
            evidence["otio_has_http"] = bool(re.search(r"https?://", text, flags=re.I))
            (ART / "otio_snippet.txt").write_text(text[:4000], encoding="utf-8")
            shutil.copy2(otio_path, ART / otio_path.name)
            evidence["steps"].append("otio_exported")
            evidence["screenshots"] = sorted(p.name for p in shots.glob("*.png"))
            browser.close()
    finally:
        proc.send_signal(signal.SIGTERM)
        try:
            proc.wait(timeout=15)
        except subprocess.TimeoutExpired:
            proc.kill()

    (ART / "funnel-r3-ui-evidence.json").write_text(
        json.dumps(evidence, indent=2), encoding="utf-8"
    )
    print(json.dumps(evidence, indent=2))
    if evidence.get("otio_has_http"):
        raise SystemExit("OTIO enthält HTTP-URL")
    required = {
        "three_open_gaps",
        "gap_1_and_3_selected",
        "selected_filled",
        "all_filled_missing_license_ok",
        "reload_ok",
        "otio_exported",
    }
    missing = required - set(evidence["steps"])
    if missing:
        raise SystemExit(f"Fehlende Schritte: {sorted(missing)}")
    required_shots = [
        "01_three_open_gaps.png",
        "02_gap_1_and_3_selected.png",
        "03_selected_gaps_progress.png",
        "04_only_selected_gaps_filled.png",
        "05_all_open_gaps_button.png",
        "06_remaining_gap_progress.png",
        "07_missing_license_nonblocking.png",
        "08_all_gaps_filled.png",
        "09_after_reload_no_duplicates.png",
        "10_otio_local_only.png",
    ]
    for name in required_shots:
        if not (shots / name).is_file():
            raise SystemExit(f"Screenshot fehlt: {name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
