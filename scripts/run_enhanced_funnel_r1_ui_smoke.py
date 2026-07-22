#!/usr/bin/env python3
"""Startet Streamlit-Smoke-App und steuert sie per Playwright (echte PNGs)."""

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
ART = Path("/opt/cursor/artifacts/funnel-r1-ui")
APP = ROOT / "scripts" / "enhanced_funnel_r1_streamlit_app.py"
PORT = int(os.environ.get("FUNNEL_R1_UI_PORT", "8511"))


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
        "otio_path": None,
        "otio_has_http": None,
        "screenshots": [],
    }

    env = os.environ.copy()
    env["FUNNEL_R1_SMOKE_ROOT"] = str(ART / "project")
    env["FUNNEL_R1_DOWNLOAD_LOG"] = str(ART / "download_calls.json")

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
            page = browser.new_page(viewport={"width": 1400, "height": 1000})
            page.goto(base, wait_until="networkidle")
            page.wait_for_timeout(2000)

            body = page.content()
            assert "⑦ Cut Plan" in body or "Cut Plan" in body
            assert "Supplements sequenziell prüfen" not in body
            assert "20 Kandidaten vorprüfen" in body
            page.screenshot(path=str(shots / "01_enhanced_cut_plan.png"), full_page=True)
            page.screenshot(
                path=str(shots / "02_old_auto_accept_absent.png"), full_page=True
            )
            evidence["steps"] += ["opened_cut_plan", "old_button_absent"]

            _goto_action(page, base, "run_funnel", "SMOKE_FUNNEL_DONE")
            page.wait_for_selector("text=Funnel-Ergebnis", timeout=60000)
            page.screenshot(
                path=str(shots / "03_twenty_candidate_progress.png"), full_page=True
            )
            page.screenshot(
                path=str(shots / "04_thumbnail_batches.png"), full_page=True
            )
            page.screenshot(path=str(shots / "05_final_ranking.png"), full_page=True)
            download_log = Path(env["FUNNEL_R1_DOWNLOAD_LOG"])
            if download_log.is_file():
                evidence["download_calls"] = json.loads(
                    download_log.read_text(encoding="utf-8")
                )
            page.screenshot(
                path=str(shots / "06_rank1_invalid_fallback.png"), full_page=True
            )
            assert "review_ready" in page.content()
            page.screenshot(
                path=str(shots / "07_review_ready_before_reload.png"), full_page=True
            )

            page.goto(base, wait_until="networkidle")
            page.wait_for_timeout(1500)
            assert "review_ready" in page.content()
            assert "export_ready" not in page.content() or "review_ready" in page.content()
            page.screenshot(
                path=str(shots / "08_review_ready_after_reload.png"), full_page=True
            )
            evidence["steps"].append("reload_still_review_ready")

            _goto_action(page, base, "strip_license", "SMOKE_LICENSE_STRIPPED")
            _goto_action(page, base, "confirm", "SMOKE_CONFIRM")
            page.wait_for_selector("text=license_review_required", timeout=60000)
            page.screenshot(path=str(shots / "09_license_gate.png"), full_page=True)
            evidence["steps"].append("license_gate")

            _goto_action(page, base, "restore_license", "SMOKE_LICENSE_RESTORED")
            _goto_action(page, base, "confirm", "SMOKE_CONFIRM")
            page.wait_for_selector("text=export_ready", timeout=60000)
            page.screenshot(
                path=str(shots / "10_manual_confirm_export_ready.png"), full_page=True
            )
            evidence["steps"].append("manual_confirm_export_ready")

            _goto_action(page, base, "export_otio", "SMOKE_OTIO")
            page.screenshot(path=str(shots / "11_otio_local_path.png"), full_page=True)
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

    (ART / "funnel-r1-ui-evidence.json").write_text(
        json.dumps(evidence, indent=2), encoding="utf-8"
    )
    print(json.dumps(evidence, indent=2))
    if evidence.get("otio_has_http"):
        raise SystemExit("OTIO enthält HTTP-URL")
    required = {
        "old_button_absent",
        "reload_still_review_ready",
        "license_gate",
        "manual_confirm_export_ready",
        "otio_exported",
    }
    missing = required - set(evidence["steps"])
    if missing:
        raise SystemExit(f"Fehlende Schritte: {sorted(missing)}")
    if len(evidence.get("screenshots") or []) < 11:
        raise SystemExit("Nicht alle Screenshots vorhanden")
    if len(evidence.get("download_calls") or []) < 2:
        raise SystemExit("Download-Fallback nicht nachgewiesen")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
