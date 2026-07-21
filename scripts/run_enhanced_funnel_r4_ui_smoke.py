#!/usr/bin/env python3
"""R4 Streamlit-Smoke: echte Multiselect- und Button-Interaktion (kein smoke_action)."""

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
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
ART = Path("/opt/cursor/artifacts/funnel-r4-button-evidence")
APP = ROOT / "scripts" / "enhanced_funnel_r4_streamlit_app.py"
PORT = int(os.environ.get("FUNNEL_R4_UI_PORT", "8514"))


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


def _button(page, label: str):
    return page.get_by_role("button", name=label)


def _select_gaps_in_multiselect(page, gap_ids: list[str]) -> None:
    """Bedient die sichtbare Gap-Mehrfachauswahl (st.pills) ohne Query-Parameter."""
    label = page.get_by_text("Offene Coverage Gaps auswählen", exact=False).first
    label.scroll_into_view_if_needed()
    page.wait_for_timeout(300)
    for gap_id in gap_ids:
        # Pill-Buttons tragen format_func-Text: "gap_1 · canyon road"
        pill = page.get_by_role("button", name=re.compile(rf"^{re.escape(gap_id)}\s·"))
        if pill.count() == 0:
            pill = page.get_by_role("button", name=re.compile(re.escape(gap_id)))
        pill.first.scroll_into_view_if_needed()
        pill.first.click()
        page.wait_for_timeout(500)


def _wait_progress(page, text: str, timeout: int = 180000) -> None:
    page.wait_for_selector(f"text={text}", timeout=timeout)


def _export_otio(project_root: Path) -> Path:
    from otio_app.defaults import DEFAULT_ENHANCED_WORK_SUBDIR
    from otio_app.models import Project, ProjectMode
    from otio_app.services.without_voiceover_enhanced.io_utils import load_model, write_json
    from otio_app.services.without_voiceover_enhanced.models import (
        AcceptedSupplementsDocument,
        ResolvedShot,
        ResolvedTimelineDocument,
    )
    from otio_app.services.without_voiceover_enhanced.otio_export_service import (
        export_otio_from_resolved_timeline,
    )
    from otio_app.services.without_voiceover_enhanced.paths import (
        accepted_supplements_path,
        resolved_timeline_path,
    )

    work = project_root / DEFAULT_ENHANCED_WORK_SUBDIR
    project = Project(
        id="funnel-r4-smoke",
        name="FunnelR4Smoke",
        project_root=str(project_root),
        work_dir=str(work),
        language="de",
        project_mode=ProjectMode.WITHOUT_VOICEOVER_ENHANCED,
        fps=25.0,
        frames_per_shot=3,
        selected_asset_subdirs=["Canyon"],
        asset_subdir_names=["Canyon"],
    )
    accepted = load_model(
        accepted_supplements_path(project), AcceptedSupplementsDocument
    )
    if accepted is None or not accepted.supplements:
        raise RuntimeError("Keine Accepted Supplements für OTIO")
    asset_id = accepted.supplements[0].candidate_id
    write_json(
        resolved_timeline_path(project),
        ResolvedTimelineDocument(
            script_version="script-v1",
            fps=25.0,
            total_duration_seconds=4.0,
            shots=[
                ResolvedShot(
                    shot_id="shot_001",
                    asset_id=asset_id,
                    timeline_start_seconds=0.0,
                    timeline_end_seconds=4.0,
                    source_start_seconds=0.0,
                    source_end_seconds=4.0,
                )
            ],
            audio_segments=[],
        ),
    )
    return export_otio_from_resolved_timeline(
        project, basename="funnel_r4_button_evidence"
    )


def main() -> int:
    if ART.exists():
        shutil.rmtree(ART)
    ART.mkdir(parents=True, exist_ok=True)
    shots = ART / "screenshots"
    shots.mkdir(exist_ok=True)
    log_path = ART / "streamlit.log"
    evidence: dict = {
        "selected_button_disabled_without_selection": False,
        "selected_gap_ids": ["gap_1", "gap_3"],
        "processed_by_selected_button": [],
        "processed_by_all_button": [],
        "selected_progress": [],
        "all_progress": [],
        "provider_candidate_counts": {},
        "candidate_pool_count": 0,
        "button_actions_triggered_by_query_params": False,
        "duplicate_accepted_count": 0,
        "duplicate_inventory_count": 0,
        "otio_has_http": None,
        "steps": [],
        "screenshots": [],
        "errors": [],
    }

    env = os.environ.copy()
    env["FUNNEL_R4_SMOKE_ROOT"] = str(ART / "project")
    env["FUNNEL_R4_DOWNLOAD_LOG"] = str(ART / "download_calls.json")
    env["FUNNEL_R4_PROGRESS_LOG"] = str(ART / "progress_log.json")

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
            page = browser.new_page(viewport={"width": 1400, "height": 1200})
            page.goto(base, wait_until="networkidle")
            page.wait_for_timeout(2500)

            body = page.content()
            assert "gap_1" in body and "gap_2" in body and "gap_3" in body
            assert "Offene Coverage Gaps auswählen" in body
            assert "Alle offenen Gaps automatisch auflösen" in body
            assert "Ausgewählte Gaps automatisch auflösen" in body

            selected_btn = _button(page, "Ausgewählte Gaps automatisch auflösen")
            assert selected_btn.is_disabled(), "Auswahlbutton muss ohne Auswahl disabled sein"
            evidence["selected_button_disabled_without_selection"] = True
            page.screenshot(path=str(shots / "01_selected_disabled.png"), full_page=True)
            evidence["steps"].append("selected_disabled")

            page.screenshot(path=str(shots / "02_gap_multiselect.png"), full_page=True)
            _select_gaps_in_multiselect(page, ["gap_1", "gap_3"])
            page.wait_for_timeout(800)
            # Button sollte nun enabled sein
            selected_btn = _button(page, "Ausgewählte Gaps automatisch auflösen")
            page.wait_for_timeout(500)
            # Streamlit rerun nach Multiselect
            for _ in range(20):
                selected_btn = _button(page, "Ausgewählte Gaps automatisch auflösen")
                if not selected_btn.is_disabled():
                    break
                page.wait_for_timeout(250)
            if selected_btn.is_disabled():
                raise RuntimeError("Auswahlbutton bleibt nach Multiselect disabled")
            page.screenshot(
                path=str(shots / "03_gap_1_and_3_selected.png"), full_page=True
            )
            evidence["steps"].append("gap_1_and_3_selected")

            # Progress-Log zurücksetzen vor Selected-Lauf
            Path(env["FUNNEL_R4_PROGRESS_LOG"]).write_text("[]", encoding="utf-8")
            Path(env["FUNNEL_R4_DOWNLOAD_LOG"]).write_text("[]", encoding="utf-8")

            selected_btn.click()
            page.screenshot(
                path=str(shots / "04_selected_button_clicked.png"), full_page=True
            )
            _wait_progress(page, "Gap 1/2", timeout=180000)
            evidence["steps"].append("selected_progress_started")
            # Warten bis Selected-Lauf im Progress-Log abgeschlossen ist
            deadline = time.time() + 180
            while time.time() < deadline:
                if Path(env["FUNNEL_R4_PROGRESS_LOG"]).is_file():
                    prog_probe = json.loads(
                        Path(env["FUNNEL_R4_PROGRESS_LOG"]).read_text(encoding="utf-8")
                    )
                    if any(
                        str(m).startswith("__RUN_END__") and "gap_1" in str(m)
                        for m in prog_probe
                    ) or any(
                        str(m).startswith("__RUN_END__") and "filled=" in str(m)
                        for m in prog_probe
                    ):
                        break
                page.wait_for_timeout(500)
            else:
                raise RuntimeError("Selected-Lauf Timeout (kein __RUN_END__)")

            page.wait_for_timeout(1500)
            page.goto(base, wait_until="networkidle")
            page.wait_for_timeout(2000)
            page.screenshot(path=str(shots / "05_only_gap_2_open.png"), full_page=True)
            evidence["steps"].append("only_gap_2_open")

            from otio_app.defaults import DEFAULT_ENHANCED_WORK_SUBDIR
            from otio_app.models import Project, ProjectMode
            from otio_app.services.without_voiceover_enhanced.io_utils import load_model
            from otio_app.services.without_voiceover_enhanced.models import (
                AcceptedSupplementsDocument,
                SupplementFunnelReport,
            )
            from otio_app.services.without_voiceover_enhanced.paths import (
                accepted_supplements_path,
                supplement_funnel_report_path,
            )
            from otio_app.services.without_voiceover_enhanced.supplement_funnel_service import (
                list_open_funnel_gap_ids,
            )

            project = Project(
                id="funnel-r4-smoke",
                name="FunnelR4Smoke",
                project_root=str(ART / "project"),
                work_dir=str(ART / "project" / DEFAULT_ENHANCED_WORK_SUBDIR),
                language="de",
                project_mode=ProjectMode.WITHOUT_VOICEOVER_ENHANCED,
                fps=25.0,
                frames_per_shot=3,
                selected_asset_subdirs=["Canyon"],
                asset_subdir_names=["Canyon"],
            )

            prog = json.loads(
                Path(env["FUNNEL_R4_PROGRESS_LOG"]).read_text(encoding="utf-8")
            )
            selected_msgs = [m for m in prog if not str(m).startswith("__")]
            evidence["selected_progress"] = [
                m for m in selected_msgs if re.match(r"Gap \d+/2$", m or "")
            ]
            dlog = json.loads(
                Path(env["FUNNEL_R4_DOWNLOAD_LOG"]).read_text(encoding="utf-8")
            )
            processed_selected = sorted({d["gap_id"] for d in dlog})
            evidence["processed_by_selected_button"] = processed_selected
            if processed_selected != ["gap_1", "gap_3"]:
                raise RuntimeError(f"Selected-Lauf falsch: {processed_selected}")
            if "Gap 1/2" not in selected_msgs or "Gap 2/2" not in selected_msgs:
                raise RuntimeError(f"Selected-Progress fehlt: {selected_msgs[:30]}")

            open_ids = list_open_funnel_gap_ids(project)
            if open_ids != ["gap_2"]:
                raise RuntimeError(f"Erwartet nur gap_2 offen, got {open_ids}")

            report = load_model(
                supplement_funnel_report_path(project), SupplementFunnelReport
            )
            if report and report.gaps:
                for g in report.gaps:
                    if g.provider_candidate_counts:
                        evidence["provider_candidate_counts"] = dict(
                            g.provider_candidate_counts
                        )
                        evidence["candidate_pool_count"] = sum(
                            g.provider_candidate_counts.values()
                        )
                        break
            page.screenshot(
                path=str(shots / "08_provider_balanced_pool.png"), full_page=True
            )

            # Alle-Button: nur gap_2 — Download-Log für zweiten Lauf zurücksetzen
            Path(env["FUNNEL_R4_DOWNLOAD_LOG"]).write_text("[]", encoding="utf-8")

            all_btn = _button(page, "Alle offenen Gaps automatisch auflösen")
            assert not all_btn.is_disabled()
            all_btn.click()
            page.screenshot(path=str(shots / "06_all_button_clicked.png"), full_page=True)
            _wait_progress(page, "Gap 1/1", timeout=180000)
            deadline = time.time() + 180
            while time.time() < deadline:
                prog2 = json.loads(
                    Path(env["FUNNEL_R4_PROGRESS_LOG"]).read_text(encoding="utf-8")
                )
                ends = [m for m in prog2 if str(m).startswith("__RUN_END__")]
                if len(ends) >= 2:
                    break
                page.wait_for_timeout(500)
            else:
                raise RuntimeError("Alle-Lauf Timeout")

            page.wait_for_timeout(1500)
            page.goto(base, wait_until="networkidle")
            page.wait_for_timeout(1500)
            assert "Keine offenen Coverage Gaps" in page.content()
            page.screenshot(path=str(shots / "07_all_gaps_filled.png"), full_page=True)
            evidence["steps"].append("all_gaps_filled")

            prog2 = json.loads(
                Path(env["FUNNEL_R4_PROGRESS_LOG"]).read_text(encoding="utf-8")
            )
            all_msgs: list[str] = []
            collecting = False
            for m in prog2:
                s = str(m)
                if s.startswith("__RUN_START__") and "gap_2" in s:
                    collecting = True
                    continue
                if s.startswith("__RUN_END__") and collecting:
                    break
                if collecting and not s.startswith("__"):
                    all_msgs.append(s)
            if not all_msgs:
                all_msgs = [str(m) for m in prog2 if "Gap 1/1" in str(m)]
            evidence["all_progress"] = [
                m for m in all_msgs if re.match(r"Gap \d+/1$", m or "")
            ] or (["Gap 1/1"] if any("Gap 1/1" in str(m) for m in prog2) else [])
            dlog2 = json.loads(
                Path(env["FUNNEL_R4_DOWNLOAD_LOG"]).read_text(encoding="utf-8")
            )
            processed_all = sorted({d["gap_id"] for d in dlog2})
            evidence["processed_by_all_button"] = processed_all
            if processed_all != ["gap_2"]:
                raise RuntimeError(f"Alle-Lauf falsch: {processed_all}")

            page.reload(wait_until="networkidle")
            page.wait_for_timeout(1500)
            page.screenshot(
                path=str(shots / "09_reload_no_duplicates.png"), full_page=True
            )
            accepted = load_model(
                accepted_supplements_path(project), AcceptedSupplementsDocument
            )
            ids = [s.candidate_id for s in (accepted.supplements if accepted else [])]
            evidence["duplicate_accepted_count"] = len(ids) - len(set(ids))
            inv_ids = []
            for pth in (ART / "project").rglob("*.json"):
                if "inventory" in pth.name.lower():
                    try:
                        data = json.loads(pth.read_text(encoding="utf-8"))
                    except Exception:
                        continue
                    for a in data.get("assets") or []:
                        aid = a.get("asset_id") or a.get("candidate_id")
                        if aid:
                            inv_ids.append(aid)
            evidence["duplicate_inventory_count"] = len(inv_ids) - len(set(inv_ids))
            open_after = list_open_funnel_gap_ids(project)
            if open_after:
                raise RuntimeError(f"Nach Reload noch offen: {open_after}")
            if evidence["duplicate_accepted_count"] or evidence["duplicate_inventory_count"]:
                raise RuntimeError("Duplikate nach Reload")
            evidence["steps"].append("reload_ok")

            otio_path = _export_otio(ART / "project")
            text = otio_path.read_text(encoding="utf-8", errors="replace")
            evidence["otio_path"] = str(otio_path)
            evidence["otio_has_http"] = bool(re.search(r"https?://", text, flags=re.I))
            (ART / "otio_snippet.txt").write_text(text[:4000], encoding="utf-8")
            shutil.copy2(otio_path, ART / otio_path.name)
            page.goto(base, wait_until="networkidle")
            page.wait_for_timeout(500)
            # OTIO-Nachweis als Screenshot der Funnel-Abschlussseite + Snippet-Overlay via caption
            page.evaluate(
                """(snippet) => {
                    const pre = document.createElement('pre');
                    pre.id = 'otio-evidence';
                    pre.textContent = snippet;
                    pre.style.cssText = 'white-space:pre-wrap;background:#111;color:#eee;padding:12px;margin:12px;';
                    document.body.prepend(pre);
                }""",
                text[:1800],
            )
            page.screenshot(path=str(shots / "10_otio_local_only.png"), full_page=True)
            evidence["steps"].append("otio_exported")
            evidence["screenshots"] = sorted(p.name for p in shots.glob("*.png"))
            browser.close()
    except Exception as exc:
        evidence["errors"].append(str(exc))
        try:
            page.screenshot(path=str(shots / "ERROR.png"), full_page=True)
        except Exception:
            pass
        (ART / "funnel-r4-button-evidence.json").write_text(
            json.dumps(evidence, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        raise
    finally:
        proc.send_signal(signal.SIGTERM)
        try:
            proc.wait(timeout=15)
        except subprocess.TimeoutExpired:
            proc.kill()

    if evidence["candidate_pool_count"] <= 0 and evidence["provider_candidate_counts"]:
        evidence["candidate_pool_count"] = sum(
            evidence["provider_candidate_counts"].values()
        )

    (ART / "funnel-r4-button-evidence.json").write_text(
        json.dumps(evidence, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    shutil.copy2(
        ART / "funnel-r4-button-evidence.json",
        Path("/opt/cursor/artifacts/funnel-r4-button-evidence.json"),
    )
    print(json.dumps(evidence, indent=2, ensure_ascii=False))

    if evidence.get("otio_has_http"):
        raise SystemExit("OTIO enthält HTTP-URL")
    if evidence["errors"]:
        raise SystemExit(evidence["errors"][0])
    required = {
        "selected_disabled",
        "gap_1_and_3_selected",
        "only_gap_2_open",
        "all_gaps_filled",
        "reload_ok",
        "otio_exported",
    }
    missing = required - set(evidence["steps"])
    if missing:
        raise SystemExit(f"Fehlende Schritte: {sorted(missing)}")
    required_shots = [
        "01_selected_disabled.png",
        "02_gap_multiselect.png",
        "03_gap_1_and_3_selected.png",
        "04_selected_button_clicked.png",
        "05_only_gap_2_open.png",
        "06_all_button_clicked.png",
        "07_all_gaps_filled.png",
        "08_provider_balanced_pool.png",
        "09_reload_no_duplicates.png",
        "10_otio_local_only.png",
    ]
    for name in required_shots:
        if not (shots / name).is_file():
            raise SystemExit(f"Screenshot fehlt: {name}")
    if evidence["processed_by_selected_button"] != ["gap_1", "gap_3"]:
        raise SystemExit("Selected gaps mismatch")
    if evidence["processed_by_all_button"] != ["gap_2"]:
        raise SystemExit("All-button gaps mismatch")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
