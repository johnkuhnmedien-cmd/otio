"""Discovery V2 — isolierte dritte Pipeline.

Artefakte gehören ausschließlich unter ``_otio_v2/``.
Classic- und Without-VO-Code unter ``_otio/`` wird hier nicht verändert.
"""

from __future__ import annotations

from otio_app.defaults import DEFAULT_DISCOVERY_V2_WORK_SUBDIR
from otio_app.discovery_v2.paths import (
    assert_path_is_under_discovery_v2,
    get_discovery_v2_root,
    is_under_discovery_v2,
)
from otio_app.project_work_root import resolve_project_work_root

__all__ = [
    "DEFAULT_DISCOVERY_V2_WORK_SUBDIR",
    "assert_path_is_under_discovery_v2",
    "get_discovery_v2_root",
    "is_under_discovery_v2",
    "resolve_project_work_root",
]
