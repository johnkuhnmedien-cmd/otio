#!/usr/bin/env python3
"""Bestandsaufnahme beschaffter Assets für ein bestehendes Projekt.

Trägt Assets nach, die vor dem gemeinsamen Eingangstor beschafft wurden und
deren Inventarzeile ein früherer Ordner-Sync entfernt hat. Quellen sind die
Acceptance-Listen aller Sprachen, die Clean-Media-Manifeste und die
Stock-Downloads.

Beispiele:

    # Nur berichten, nichts ändern und nichts bezahlen
    python scripts/recover_supplement_inventory.py --list

    python scripts/recover_supplement_inventory.py \\
        --project-root "/Volumes/Media/Irland" --dry-run

    # Nachtragen und wie Originale analysieren (kostet Gemini-Aufrufe)
    python scripts/recover_supplement_inventory.py \\
        --project-root "/Volumes/Media/Irland" --language de
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from otio_app.models import Project  # noqa: E402
from otio_app.project_repository import find_projects_by_root, list_projects  # noqa: E402
from otio_app.services.supplement_recovery import (  # noqa: E402
    recover_supplements_into_inventory,
    scan_recoverable_supplements,
)


def _print_projects() -> int:
    projects = list_projects()
    if not projects:
        print("Keine Projekte in der Datenbank.")
        return 1
    print(f"{len(projects)} Projekt(e):\n")
    for project in projects:
        print(
            f"  {project.name}  [{project.language}]  {project.mode}\n"
            f"    root: {project.project_root}\n"
            f"    work: {project.work_dir}"
        )
    return 0


def _select_project(project_root: str, language: str | None) -> Project | None:
    candidates = find_projects_by_root(project_root)
    if not candidates:
        print(f"Kein Projekt mit diesem Medienordner gefunden: {project_root}")
        return None
    if language:
        wanted = language.strip().casefold()
        for project in candidates:
            if (project.language or "").casefold() == wanted:
                return project
        available = ", ".join(sorted({p.language for p in candidates}))
        print(f"Sprache '{language}' nicht gefunden. Vorhanden: {available}")
        return None
    if len(candidates) > 1:
        langs = ", ".join(sorted({p.language for p in candidates}))
        print(
            f"Mehrere Sprachen an diesem Pfad ({langs}). "
            "Das Inventar ist geteilt — jede Sprache liefert dasselbe Ergebnis. "
            f"Verwende '{candidates[0].language}'."
        )
    return candidates[0]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", help="Medienordner des Projekts")
    parser.add_argument("--language", help="Sprache des DB-Projekts (z. B. de)")
    parser.add_argument(
        "--folder",
        action="append",
        dest="folders",
        help="Nur diese Asset-Ordner (mehrfach möglich)",
    )
    parser.add_argument("--model", help="Gemini-Modell für die Analyse")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Nur zeigen, was nachgetragen würde",
    )
    parser.add_argument(
        "--list",
        action="store_true",
        dest="list_projects",
        help="Projekte der Datenbank auflisten",
    )
    args = parser.parse_args()

    if args.list_projects:
        return _print_projects()
    if not args.project_root:
        parser.error("--project-root fehlt (oder --list nutzen)")

    project = _select_project(args.project_root, args.language)
    if project is None:
        return 1

    print(f"Projekt: {project.name} [{project.language}]")
    print(f"Arbeitsordner (geteilt): {project.work_dir}\n")

    items, scan_report = scan_recoverable_supplements(
        project, folder_names=args.folders
    )
    missing = [item for item in items if not item.in_inventory]

    print(f"Gefunden: {len(items)} beschaffte Asset(s), davon {len(missing)} ohne Inventarzeile.")
    for item in missing:
        print(f"  + {item.media_path.name} → {item.folder_name}  ({item.source})")
    for note in scan_report.unresolved:
        print(f"  ! {note}")

    if args.dry_run or not missing:
        if not missing:
            print("\nNichts nachzutragen.")
        return 0

    print("\nTrage nach und analysiere wie Originale …")
    report = recover_supplements_into_inventory(
        project, folder_names=args.folders, model=args.model
    )
    print(
        f"Nachgetragen: {report.recovered} "
        f"(neu analysiert: {report.analyzed}, aus Cache: {report.already_complete})"
    )
    for folder, count in sorted(report.recovered_by_folder.items()):
        print(f"  {folder}: {count}")
    for failure in report.failures:
        print(f"  ! {failure}")
    return 1 if report.failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
