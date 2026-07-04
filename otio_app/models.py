"""Pydantic-Datenmodelle für Projekte."""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Optional

try:
    from typing import Self
except ImportError:  # Python < 3.11
    from typing_extensions import Self

from uuid import uuid4

from pydantic import BaseModel, Field, field_validator, model_validator

from otio_app.defaults import DEFAULT_FRAMES_PER_SHOT, DEFAULT_VOICE_OVER_SUBDIR
from otio_app.paths import (
    PathValidationError,
    normalize_path,
    resolve_work_dir,
    validate_project_layout,
)
from otio_app.project_layout import (
    get_edit_plan_path,
    get_folder_inventory_path,
    get_inventory_dir,
    get_inventory_path,
    get_voice_analysis_path,
    get_voice_folder_mapping_path,
    get_voice_over_dir,
    scan_project_structure,
)


class ProjectStatus(str, Enum):
    DRAFT = "DRAFT"
    ANALYZING = "ANALYZING"
    READY = "READY"


def validate_asset_selection(
    available: list[str],
    selected: list[str],
) -> list[str]:
    """Prüft, dass die Auswahl eine nicht-leere Teilmenge der verfügbaren Ordner ist."""
    if not selected:
        raise ValueError("Mindestens ein Asset-Ordner muss ausgewählt werden.")
    available_set = set(available)
    invalid = [name for name in selected if name not in available_set]
    if invalid:
        raise ValueError(
            "Ungültige Ordnerauswahl: " + ", ".join(invalid)
        )
    return selected


class ProjectCreate(BaseModel):
    name: str
    project_root: str
    work_dir: Optional[str] = None
    voice_over_subdir: str = DEFAULT_VOICE_OVER_SUBDIR
    language: str = "de"
    frames_per_shot: int = DEFAULT_FRAMES_PER_SHOT
    fps: float = 25.0
    width: int = 3840
    height: int = 2160
    aspect_ratio: str = "16:9"
    target_platform: str = "YouTube"
    notes: Optional[str] = None

    @field_validator("name")
    @classmethod
    def name_not_empty(cls, value: str) -> str:
        trimmed = value.strip()
        if not trimmed:
            raise ValueError("Projektname darf nicht leer sein.")
        return trimmed

    @field_validator("voice_over_subdir", "language", "aspect_ratio", "target_platform")
    @classmethod
    def string_fields_not_empty(cls, value: str) -> str:
        trimmed = value.strip()
        if not trimmed:
            raise ValueError("Feld darf nicht leer sein.")
        return trimmed

    @field_validator("fps")
    @classmethod
    def fps_positive(cls, value: float) -> float:
        if value <= 0:
            raise ValueError("fps muss größer als 0 sein.")
        return value

    @field_validator("width", "height")
    @classmethod
    def dimensions_positive(cls, value: int) -> int:
        if value <= 0:
            raise ValueError("Breite und Höhe müssen größer als 0 sein.")
        return value

    @field_validator("frames_per_shot")
    @classmethod
    def frames_positive(cls, value: int) -> int:
        if value <= 0:
            raise ValueError("frames_per_shot muss größer als 0 sein.")
        return value

    @model_validator(mode="after")
    def validate_paths(self) -> Self:
        root = normalize_path(self.project_root)
        work = resolve_work_dir(root, self.work_dir)

        try:
            validate_project_layout(root, work, self.voice_over_subdir)
        except PathValidationError as exc:
            raise ValueError(str(exc)) from exc

        self.project_root = str(root)
        self.work_dir = str(work)
        return self

    @property
    def project_root_path(self) -> Path:
        return Path(self.project_root)

    @property
    def work_dir_path(self) -> Path:
        return Path(self.work_dir)

    @property
    def work_dir_exists(self) -> bool:
        return self.work_dir_path.exists()

    @property
    def voice_over_dir(self) -> Path:
        return get_voice_over_dir(
            self.project_root_path,
            self.voice_over_subdir,
            self.language,
        )

    @property
    def asset_subdir_names(self) -> list[str]:
        scan = scan_project_structure(
            self.project_root_path,
            self.work_dir_path,
            self.voice_over_subdir,
            self.language,
        )
        return scan.asset_subdir_names

    @property
    def asset_subdirs(self) -> list[Path]:
        return [self.project_root_path / name for name in self.asset_subdir_names]

    @property
    def inventory_path(self) -> Path:
        return get_inventory_path(self.project_root_path)

    @property
    def inventory_dir(self) -> Path:
        return get_inventory_dir(self.work_dir_path)

    def folder_inventory_path(self, folder_name: str) -> Path:
        return get_folder_inventory_path(self.work_dir_path, folder_name)

    @property
    def voice_analysis_path(self) -> Path:
        return get_voice_analysis_path(self.project_root_path)

    @property
    def voice_folder_mapping_path(self) -> Path:
        return get_voice_folder_mapping_path(self.project_root_path)

    @property
    def edit_plan_path(self) -> Path:
        return get_edit_plan_path(self.project_root_path)


class Project(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid4()))
    name: str
    project_root: str
    work_dir: str
    voice_over_subdir: str = DEFAULT_VOICE_OVER_SUBDIR
    language: str = "de"
    frames_per_shot: int = DEFAULT_FRAMES_PER_SHOT
    fps: float = 25.0
    width: int = 3840
    height: int = 2160
    aspect_ratio: str = "16:9"
    target_platform: str = "YouTube"
    status: ProjectStatus = ProjectStatus.DRAFT
    asset_subdir_names: list[str]
    selected_asset_subdirs: list[str]
    notes: Optional[str] = None
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc)
    )
    updated_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc)
    )

    @property
    def project_root_path(self) -> Path:
        return Path(self.project_root)

    @property
    def work_dir_path(self) -> Path:
        return Path(self.work_dir)

    @property
    def voice_over_dir(self) -> Path:
        return get_voice_over_dir(
            self.project_root_path,
            self.voice_over_subdir,
            self.language,
        )

    @property
    def selected_asset_subdirs_paths(self) -> list[Path]:
        return [self.project_root_path / name for name in self.selected_asset_subdirs]

    @property
    def asset_subdirs(self) -> list[Path]:
        return [self.project_root_path / name for name in self.asset_subdir_names]

    @property
    def inventory_path(self) -> Path:
        return get_inventory_path(self.project_root_path)

    @property
    def inventory_dir(self) -> Path:
        return get_inventory_dir(self.work_dir_path)

    def folder_inventory_path(self, folder_name: str) -> Path:
        return get_folder_inventory_path(self.work_dir_path, folder_name)

    @property
    def voice_analysis_path(self) -> Path:
        return get_voice_analysis_path(self.project_root_path)

    @property
    def voice_folder_mapping_path(self) -> Path:
        return get_voice_folder_mapping_path(self.project_root_path)

    @property
    def edit_plan_path(self) -> Path:
        return get_edit_plan_path(self.project_root_path)

    @classmethod
    def from_create(
        cls,
        data: ProjectCreate,
        asset_subdir_names: list[str],
        selected_asset_subdirs: list[str],
    ) -> Project:
        now = datetime.now(timezone.utc)
        return cls(
            name=data.name,
            project_root=data.project_root,
            work_dir=data.work_dir,
            voice_over_subdir=data.voice_over_subdir,
            language=data.language,
            frames_per_shot=data.frames_per_shot,
            fps=data.fps,
            width=data.width,
            height=data.height,
            aspect_ratio=data.aspect_ratio,
            target_platform=data.target_platform,
            asset_subdir_names=asset_subdir_names,
            selected_asset_subdirs=selected_asset_subdirs,
            notes=data.notes,
            status=ProjectStatus.DRAFT,
            created_at=now,
            updated_at=now,
        )
