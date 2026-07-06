"""Inventory-Hash für Stale-Erkennung beim Schnittplan."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from otio_app.analysis_models import AssetFolderAnalysis, AssetMediaAnalysis
from otio_app.models import Project
from otio_app.project_layout import get_folder_inventory_path
from otio_app.services.inventory_loader import load_folder_inventory


def _asset_fingerprint(asset: AssetMediaAnalysis) -> dict:
    return {
        "path": asset.path,
        "asset_id": asset.asset_id,
        "description": asset.description,
        "asset_origin": asset.asset_origin,
        "rights_status": asset.rights_status,
    }


def compute_folder_inventory_hash(item: AssetFolderAnalysis | None) -> str:
    if item is None or not item.assets:
        return ""
    payload = {
        "folder": item.folder,
        "assets": sorted(
            [_asset_fingerprint(asset) for asset in item.assets],
            key=lambda entry: entry["path"],
        ),
    }
    digest = hashlib.sha256(
        json.dumps(payload, sort_keys=True, ensure_ascii=False).encode("utf-8")
    ).hexdigest()
    return digest[:16]


def current_folder_inventory_hash(project: Project, folder_name: str) -> str:
    path = get_folder_inventory_path(project.work_dir_path, folder_name)
    if not path.is_file():
        return ""
    item = load_folder_inventory(project, folder_name)
    return compute_folder_inventory_hash(item)


def inventory_hash_is_stale(project: Project, folder_name: str, plan_hash: str) -> bool:
    if not plan_hash:
        return False
    current = current_folder_inventory_hash(project, folder_name)
    if not current:
        return False
    return current != plan_hash
