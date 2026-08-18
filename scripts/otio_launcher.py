#!/usr/bin/env python3
"""Lokaler OTIO-Launcher: Start / Stop / Restart / git pull / Branch — per Knopf."""

from __future__ import annotations

import sys
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from otio_app.app_ctl import (  # noqa: E402
    AppCtlError,
    CommandResult,
    format_status,
    get_status,
    git_current_branch,
    list_git_branches,
    restart_app,
    start_app,
    stop_app,
)


def _print_result(result: CommandResult) -> None:
    print(result.message)
    for line in result.details:
        if line:
            print(f"  {line}")


def run_terminal_menu() -> int:
    """Fallback ohne Tk: nummeriertes Menü im Terminal."""
    print("OTIO Schnittplaner — Launcher")
    print(f"Ordner: {ROOT}")
    while True:
        try:
            print()
            print(format_status(get_status(ROOT)))
        except Exception as exc:  # noqa: BLE001 — Diagnose im Menü
            print(f"Status nicht lesbar: {exc}")
        print()
        print("1) App starten (räumt Port 8501)")
        print("2) App stoppen (auch während LLM-Call)")
        print("3) Neu starten")
        print("4) Git pull (aktueller Branch) + neu starten")
        print("5) Anderen Branch holen + neu starten")
        print("6) Status aktualisieren")
        print("q) Beenden (App bleibt laufen, wenn sie gestartet ist)")
        choice = input("> ").strip().lower()
        try:
            if choice in {"q", "quit", "exit"}:
                return 0
            if choice == "1":
                _print_result(start_app(ROOT))
            elif choice == "2":
                _print_result(stop_app(ROOT))
            elif choice == "3":
                _print_result(restart_app(ROOT))
            elif choice == "4":
                _print_result(restart_app(ROOT, pull=True))
            elif choice == "5":
                print("Branches werden geholt …")
                names = list_git_branches(ROOT, fetch=True)
                current = git_current_branch(ROOT)
                for index, name in enumerate(names, start=1):
                    mark = " *" if name == current else ""
                    print(f"  {index}) {name}{mark}")
                raw = input("Nummer oder Branch-Name: ").strip()
                if not raw:
                    print("Abgebrochen.")
                    continue
                if raw.isdigit():
                    idx = int(raw)
                    if idx < 1 or idx > len(names):
                        print("Ungültige Nummer.")
                        continue
                    branch = names[idx - 1]
                else:
                    branch = raw
                _print_result(restart_app(ROOT, branch=branch))
            elif choice == "6":
                continue
            else:
                print("Unbekannte Auswahl.")
        except AppCtlError as exc:
            print(str(exc))
        except KeyboardInterrupt:
            print()
            return 0
    return 0


def run_gui() -> int:
    import tkinter as tk
    from tkinter import ttk

    root = tk.Tk()
    root.title("OTIO starten")
    root.minsize(640, 480)

    log_var_lines: list[str] = []

    status_var = tk.StringVar(value="Status wird gelesen …")
    pull_var = tk.BooleanVar(value=False)
    branch_var = tk.StringVar(value="")
    busy_var = tk.BooleanVar(value=False)

    header = ttk.Frame(root, padding=12)
    header.pack(fill="x")
    ttk.Label(header, text="OTIO Schnittplaner", font=("Helvetica", 16, "bold")).pack(
        anchor="w"
    )
    ttk.Label(
        header,
        text=(
            "Start und Stop unabhängig vom Terminal. "
            "Ein hängender LLM-Call wird mit Stoppen beendet (Prozessende)."
        ),
        wraplength=600,
    ).pack(anchor="w", pady=(4, 0))
    ttk.Label(header, textvariable=status_var, wraplength=600).pack(
        anchor="w", pady=(8, 0)
    )

    buttons = ttk.Frame(root, padding=(12, 0))
    buttons.pack(fill="x")

    git_row = ttk.Frame(root, padding=(12, 8))
    git_row.pack(fill="x")
    ttk.Checkbutton(
        git_row,
        text="Vorher git pull (aktueller Branch, nur fast-forward)",
        variable=pull_var,
    ).pack(anchor="w")

    branch_row = ttk.Frame(root, padding=(12, 0))
    branch_row.pack(fill="x")
    ttk.Label(branch_row, text="Branch:").pack(side="left")
    branch_combo = ttk.Combobox(branch_row, textvariable=branch_var, width=48)
    branch_combo.pack(side="left", padx=8, fill="x", expand=True)

    log_frame = ttk.Frame(root, padding=12)
    log_frame.pack(fill="both", expand=True)
    ttk.Label(log_frame, text="Protokoll").pack(anchor="w")
    log_text = tk.Text(log_frame, height=14, wrap="word", state="disabled")
    log_scroll = ttk.Scrollbar(log_frame, command=log_text.yview)
    log_text.configure(yscrollcommand=log_scroll.set)
    log_text.pack(side="left", fill="both", expand=True)
    log_scroll.pack(side="right", fill="y")

    def append_log(message: str) -> None:
        log_var_lines.append(message)
        log_text.configure(state="normal")
        log_text.insert("end", message.rstrip() + "\n")
        log_text.see("end")
        log_text.configure(state="disabled")

    def show_result(result: CommandResult) -> None:
        append_log(result.message)
        for line in result.details:
            if line:
                append_log("  " + line)

    def refresh_status() -> None:
        try:
            status_var.set(format_status(get_status(ROOT)))
        except Exception as exc:  # noqa: BLE001
            status_var.set(f"Statusfehler: {exc}")

    def set_busy(value: bool) -> None:
        busy_var.set(value)
        state = "disabled" if value else "normal"
        for child in (
            btn_start,
            btn_stop,
            btn_restart,
            btn_fetch,
            btn_switch,
        ):
            child.configure(state=state)

    def run_async(label: str, fn, on_ok=None) -> None:
        if busy_var.get():
            return

        def worker() -> None:
            try:
                result = fn()
            except AppCtlError as exc:
                result = CommandResult(ok=False, message=str(exc))
            except Exception as exc:  # noqa: BLE001
                result = CommandResult(ok=False, message=f"{label} fehlgeschlagen: {exc}")

            def done() -> None:
                show_result(result)
                if result.ok and on_ok is not None:
                    on_ok(result)
                set_busy(False)
                refresh_status()

            root.after(0, done)

        set_busy(True)
        append_log(label + " …")
        threading.Thread(target=worker, daemon=True).start()

    def apply_branch_list(result: CommandResult) -> None:
        names = list(result.payload or [])
        branch_combo["values"] = names
        current = git_current_branch(ROOT) or ""
        if current:
            branch_var.set(current)
        elif names and not branch_var.get():
            branch_var.set(names[0])

    def fetch_branches(*, remote: bool) -> CommandResult:
        names = list_git_branches(ROOT, fetch=remote)
        return CommandResult(
            ok=True,
            message=f"{len(names)} Branches geladen.",
            payload=names,
        )

    def switch_branch() -> CommandResult:
        name = (branch_var.get() or "").strip()
        if not name:
            raise AppCtlError("Bitte einen Branch auswählen.")
        return restart_app(ROOT, branch=name)

    btn_start = ttk.Button(
        buttons,
        text="App starten",
        command=lambda: run_async("Start", lambda: start_app(ROOT)),
    )
    btn_stop = ttk.Button(
        buttons,
        text="Stoppen",
        command=lambda: run_async("Stop", lambda: stop_app(ROOT)),
    )
    btn_restart = ttk.Button(
        buttons,
        text="Neu starten",
        command=lambda: run_async(
            "Neustart",
            lambda: restart_app(ROOT, pull=bool(pull_var.get())),
        ),
    )
    btn_start.pack(side="left")
    btn_stop.pack(side="left", padx=8)
    btn_restart.pack(side="left")

    git_buttons = ttk.Frame(root, padding=(12, 4))
    git_buttons.pack(fill="x")
    btn_fetch = ttk.Button(
        git_buttons,
        text="Branch-Liste aktualisieren",
        command=lambda: run_async(
            "Fetch",
            lambda: fetch_branches(remote=True),
            on_ok=apply_branch_list,
        ),
    )
    btn_switch = ttk.Button(
        git_buttons,
        text="Diesen Branch holen und neu starten",
        command=lambda: run_async("Branch-Wechsel", switch_branch),
    )
    btn_fetch.pack(side="left")
    btn_switch.pack(side="left", padx=8)

    def poll() -> None:
        if not busy_var.get():
            refresh_status()
        root.after(2500, poll)

    refresh_status()
    try:
        apply_branch_list(fetch_branches(remote=False))
    except Exception as exc:  # noqa: BLE001
        append_log(f"Branches: {exc}")
    append_log(f"Repo: {ROOT}")
    poll()
    root.mainloop()
    return 0


def main() -> int:
    try:
        import tkinter as tk  # noqa: F401
    except Exception:
        print("Kein Tkinter — Terminal-Menü.", file=sys.stderr)
        return run_terminal_menu()
    try:
        return run_gui()
    except Exception as exc:  # noqa: BLE001
        print(f"GUI nicht startbar ({exc}) — Terminal-Menü.", file=sys.stderr)
        return run_terminal_menu()


if __name__ == "__main__":
    raise SystemExit(main())
