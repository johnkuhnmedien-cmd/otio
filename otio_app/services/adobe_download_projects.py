"""Persistente Adobe-Stock-Download-Projekte (unabhängig von OTIO-Projekten).

Speicherort: `data/adobe_download_projects/{id}/`
  - meta.json
  - research.xlsx  (Kopie des Research-Templates)
"""

from __future__ import annotations

import json
import re
import shutil
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from otio_app.config import ensure_data_dir
from otio_app.services.adobe_research_import import (
    AdobeResearchImportPlan,
    parse_research_excel,
)

_PROJECTS_DIRNAME = "adobe_download_projects"
_META_NAME = "meta.json"
_EXCEL_NAME = "research.xlsx"
_INDEX_NAME = "index.json"
_INVALID_NAME = re.compile(r"\s+")


@dataclass
class AdobeDownloadProject:
    id: str
    name: str
    target_root: str
    excel_filename: str = ""
    sheet_name: str = ""
    selected_chapters: list[str] = field(default_factory=list)
    skip_existing_ids: bool = True
    created_at: str = ""
    updated_at: str = ""
    chapter_count: int = 0
    asset_count: int = 0

    @property
    def has_excel(self) -> bool:
        return project_excel_path(self.id).is_file()


def projects_root() -> Path:
    path = ensure_data_dir() / _PROJECTS_DIRNAME
    path.mkdir(parents=True, exist_ok=True)
    return path


def project_dir(project_id: str) -> Path:
    return projects_root() / project_id


def project_excel_path(project_id: str) -> Path:
    return project_dir(project_id) / _EXCEL_NAME


def project_meta_path(project_id: str) -> Path:
    return project_dir(project_id) / _META_NAME


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _write_index(projects: list[AdobeDownloadProject]) -> None:
    payload = {
        "schema_version": "adobe-download-projects-v1",
        "updated_at": _now_iso(),
        "projects": [
            {
                "id": p.id,
                "name": p.name,
                "target_root": p.target_root,
                "updated_at": p.updated_at,
                "chapter_count": p.chapter_count,
                "asset_count": p.asset_count,
            }
            for p in projects
        ],
    }
    path = projects_root() / _INDEX_NAME
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _load_meta(path: Path) -> AdobeDownloadProject | None:
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict) or not data.get("id"):
        return None
    return AdobeDownloadProject(
        id=str(data["id"]),
        name=str(data.get("name") or "Unbenannt"),
        target_root=str(data.get("target_root") or ""),
        excel_filename=str(data.get("excel_filename") or ""),
        sheet_name=str(data.get("sheet_name") or ""),
        selected_chapters=[str(x) for x in (data.get("selected_chapters") or [])],
        skip_existing_ids=bool(data.get("skip_existing_ids", True)),
        created_at=str(data.get("created_at") or ""),
        updated_at=str(data.get("updated_at") or ""),
        chapter_count=int(data.get("chapter_count") or 0),
        asset_count=int(data.get("asset_count") or 0),
    )


def list_download_projects() -> list[AdobeDownloadProject]:
    root = projects_root()
    projects: list[AdobeDownloadProject] = []
    for child in sorted(root.iterdir(), key=lambda p: p.name):
        if not child.is_dir():
            continue
        project = _load_meta(child / _META_NAME)
        if project is not None:
            projects.append(project)
    projects.sort(key=lambda p: p.updated_at or p.created_at, reverse=True)
    _write_index(projects)
    return projects


def get_download_project(project_id: str) -> AdobeDownloadProject | None:
    return _load_meta(project_meta_path(project_id))


def save_download_project(project: AdobeDownloadProject) -> AdobeDownloadProject:
    folder = project_dir(project.id)
    folder.mkdir(parents=True, exist_ok=True)
    project.updated_at = _now_iso()
    if not project.created_at:
        project.created_at = project.updated_at
    path = project_meta_path(project.id)
    path.write_text(
        json.dumps(asdict(project), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    list_download_projects()  # refresh index
    return project


def create_download_project(
    *,
    name: str,
    target_root: str,
    excel_bytes: bytes,
    excel_filename: str = "research.xlsx",
    selected_chapters: list[str] | None = None,
    skip_existing_ids: bool = True,
) -> AdobeDownloadProject:
    cleaned_name = _INVALID_NAME.sub(" ", (name or "").strip()) or "Download-Projekt"
    if not target_root.strip():
        raise ValueError("Zielordner fehlt.")
    if not excel_bytes:
        raise ValueError("Research-Excel fehlt.")

    plan = parse_research_excel(excel_bytes)
    project_id = uuid.uuid4().hex[:12]
    project = AdobeDownloadProject(
        id=project_id,
        name=cleaned_name,
        target_root=str(Path(target_root).expanduser()),
        excel_filename=excel_filename or "research.xlsx",
        sheet_name=plan.sheet_name,
        selected_chapters=list(selected_chapters or []),
        skip_existing_ids=skip_existing_ids,
        chapter_count=plan.chapter_count,
        asset_count=plan.asset_count,
    )
    folder = project_dir(project_id)
    folder.mkdir(parents=True, exist_ok=True)
    project_excel_path(project_id).write_bytes(excel_bytes)
    return save_download_project(project)


def update_download_project(
    project_id: str,
    *,
    name: str | None = None,
    target_root: str | None = None,
    excel_bytes: bytes | None = None,
    excel_filename: str | None = None,
    selected_chapters: list[str] | None = None,
    skip_existing_ids: bool | None = None,
) -> AdobeDownloadProject:
    project = get_download_project(project_id)
    if project is None:
        raise KeyError(f"Download-Projekt nicht gefunden: {project_id}")
    if name is not None:
        project.name = _INVALID_NAME.sub(" ", name.strip()) or project.name
    if target_root is not None:
        project.target_root = str(Path(target_root).expanduser())
    if selected_chapters is not None:
        project.selected_chapters = list(selected_chapters)
    if skip_existing_ids is not None:
        project.skip_existing_ids = bool(skip_existing_ids)
    if excel_bytes is not None:
        plan = parse_research_excel(excel_bytes)
        project_excel_path(project_id).write_bytes(excel_bytes)
        project.excel_filename = excel_filename or project.excel_filename or "research.xlsx"
        project.sheet_name = plan.sheet_name
        project.chapter_count = plan.chapter_count
        project.asset_count = plan.asset_count
    return save_download_project(project)


def delete_download_project(project_id: str, *, delete_media: bool = False) -> None:
    """Löscht Registry + Excel-Kopie. Medien im Zielordner bleiben standardmäßig erhalten."""
    folder = project_dir(project_id)
    if folder.is_dir():
        shutil.rmtree(folder)
    list_download_projects()
    if delete_media:
        # bewusst nicht implementiert — zu gefährlich ohne explizite UI
        pass


def load_project_plan(project_id: str) -> AdobeResearchImportPlan:
    path = project_excel_path(project_id)
    if not path.is_file():
        raise FileNotFoundError(f"Research-Excel für Projekt {project_id} fehlt.")
    return parse_research_excel(path)
