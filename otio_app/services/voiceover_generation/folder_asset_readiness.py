"""Phase 2 (Asset-bewusste Cut-Plan-Vorbereitung): rein lesende
Asset-Readiness-Diagnose für EINEN Folder-Voice-over-Draft.

Liest AUSSCHLIESSLICH bereits vorhandene sentence_items sowie das lokale
Folder-Inventory. Schreibt NICHTS, ruft KEIN LLM auf, löst KEINE
Supplement-Suche aus und verändert KEINEN bestehenden Draft — reine
Diagnose, damit Probleme (fehlende/ungültige Assets, direkte
Asset-Wiederholungen, lange Sätze mit zu wenig visuellen Alternativen,
empfohlene Supplement-Kandidaten) VOR dem späteren Cut Plan sichtbar
werden, statt dort erst als Validierungs-Blocker aufzufallen.

Die geschätzte Satzdauer ist eine grobe Wort-basierte Heuristik (siehe
FOLDER_ASSET_READINESS_WORDS_PER_SECOND_HEURISTIC) — SentenceItem.
estimated_duration_sec wird vom LLM aktuell NICHT verlässlich befüllt (im
Prompt nur ein Schema-Platzhalter ohne inhaltliche Anweisung), und eine
echte Audio-Alignment-Dauer liegt vor der TTS-Synthese ohnehin noch nicht
vor. Diese Diagnose ist deshalb bewusst nur ein früher, ungefährer
Hinweis, kein verbindlicher technischer Wert."""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from otio_app.defaults import (
    FOLDER_ASSET_READINESS_SHOT_MAX_SEC_HEURISTIC,
    FOLDER_ASSET_READINESS_WORDS_PER_SECOND_HEURISTIC,
)
from otio_app.models import Project
from otio_app.services.generic_outro_selector import asset_id_for_path
from otio_app.services.inventory_loader import load_folder_inventory
from otio_app.services.voiceover_generation.models import FolderVoiceoverDraft

__all__ = [
    "READINESS_STATUS_PASS",
    "READINESS_STATUS_NEEDS_REVIEW",
    "ISSUE_TYPE_INVALID_ASSET_ID",
    "ISSUE_TYPE_DIRECT_REPEAT",
    "ISSUE_TYPE_LONG_SENTENCE_LOW_ALTERNATIVES",
    "ISSUE_TYPE_SUPPLEMENT_RECOMMENDED",
    "SentenceAssetReadinessIssue",
    "FolderAssetReadinessReport",
    "estimate_sentence_duration_sec",
    "build_folder_asset_readiness_report",
]

READINESS_STATUS_PASS = "PASS"
READINESS_STATUS_NEEDS_REVIEW = "NEEDS_REVIEW"

ISSUE_TYPE_INVALID_ASSET_ID = "INVALID_ASSET_ID"
ISSUE_TYPE_DIRECT_REPEAT = "DIRECT_REPEAT"
ISSUE_TYPE_LONG_SENTENCE_LOW_ALTERNATIVES = "LONG_SENTENCE_LOW_ALTERNATIVES"
ISSUE_TYPE_SUPPLEMENT_RECOMMENDED = "SUPPLEMENT_RECOMMENDED"


def estimate_sentence_duration_sec(text: str) -> float:
    """Grobe Wort-basierte Dauer-Schätzung — siehe Moduldocstring für den
    Grund, warum SentenceItem.estimated_duration_sec hierfür nicht genutzt
    wird."""
    word_count = len([word for word in text.split() if word.strip()])
    if word_count == 0:
        return 0.0
    return round(word_count / FOLDER_ASSET_READINESS_WORDS_PER_SECOND_HEURISTIC, 2)


@dataclass
class SentenceAssetReadinessIssue:
    """EIN gefundenes Problem für GENAU EINEN Satz — rein informativ, kein
    Blocker/Fehlertyp eines bestehenden Validierungs-Enums."""

    sentence_id: str
    issue_type: str
    message: str = ""


@dataclass
class FolderAssetReadinessReport:
    """Aggregiertes Diagnose-Ergebnis für EINEN Folder-Voice-over-Draft."""

    folder_name: str
    sentence_count: int = 0
    with_primary_count: int = 0
    with_backup_count: int = 0
    direct_repeat_count: int = 0
    long_sentence_low_alternative_count: int = 0
    supplement_recommended_count: int = 0
    invalid_asset_id_count: int = 0
    status: str = READINESS_STATUS_PASS
    issues: list[SentenceAssetReadinessIssue] = field(default_factory=list)


def build_folder_asset_readiness_report(
    project: Project,
    draft: FolderVoiceoverDraft,
    *,
    shot_max_sec: float = FOLDER_ASSET_READINESS_SHOT_MAX_SEC_HEURISTIC,
) -> FolderAssetReadinessReport:
    """Reine Funktion — liest das Folder-Inventory (lesend) und die
    sentence_items des übergebenen Drafts, schreibt/ändert NICHTS."""
    inventory = load_folder_inventory(project, draft.folder_name)
    known_asset_ids = {
        asset.asset_id or asset_id_for_path(asset.path) for asset in inventory.assets
    }

    report = FolderAssetReadinessReport(
        folder_name=draft.folder_name, sentence_count=len(draft.sentence_items)
    )
    previous_primary_asset_id = ""

    for sentence in draft.sentence_items:
        has_primary = bool(sentence.primary_asset_id)
        has_backup = bool(sentence.backup_asset_ids)
        if has_primary:
            report.with_primary_count += 1
        if has_backup:
            report.with_backup_count += 1

        referenced_asset_ids = (
            [sentence.primary_asset_id] if sentence.primary_asset_id else []
        ) + list(sentence.backup_asset_ids)
        for asset_id in referenced_asset_ids:
            if asset_id and asset_id not in known_asset_ids:
                report.invalid_asset_id_count += 1
                report.issues.append(
                    SentenceAssetReadinessIssue(
                        sentence_id=sentence.sentence_id,
                        issue_type=ISSUE_TYPE_INVALID_ASSET_ID,
                        message=(
                            f"Asset '{asset_id}' ist nicht (mehr) im Inventory von "
                            f"'{draft.folder_name}' vorhanden."
                        ),
                    )
                )

        if has_primary and sentence.primary_asset_id == previous_primary_asset_id:
            report.direct_repeat_count += 1
            report.issues.append(
                SentenceAssetReadinessIssue(
                    sentence_id=sentence.sentence_id,
                    issue_type=ISSUE_TYPE_DIRECT_REPEAT,
                    message=(
                        f"Gleiches Primary-Asset '{sentence.primary_asset_id}' wie im "
                        "unmittelbar vorherigen Satz — visuell ggf. wirkungslos."
                    ),
                )
            )
        if has_primary:
            previous_primary_asset_id = sentence.primary_asset_id

        estimated_duration_sec = estimate_sentence_duration_sec(sentence.text)
        needed_segments = (
            max(1, math.ceil(estimated_duration_sec / shot_max_sec))
            if estimated_duration_sec > 0
            else 1
        )
        usable_candidate_count = len(
            {asset_id for asset_id in referenced_asset_ids if asset_id and asset_id in known_asset_ids}
        )
        if needed_segments > 1 and usable_candidate_count < needed_segments:
            report.long_sentence_low_alternative_count += 1
            report.issues.append(
                SentenceAssetReadinessIssue(
                    sentence_id=sentence.sentence_id,
                    issue_type=ISSUE_TYPE_LONG_SENTENCE_LOW_ALTERNATIVES,
                    message=(
                        f"Satz ist geschätzt {estimated_duration_sec:.1f}s lang (ca. "
                        f"{needed_segments} Shots nötig), aber nur {usable_candidate_count} "
                        "nutzbare(s) lokale(s) Asset(s) zugeordnet."
                    ),
                )
            )

        if sentence.needs_supplement_asset or (not has_primary and not has_backup):
            report.supplement_recommended_count += 1
            report.issues.append(
                SentenceAssetReadinessIssue(
                    sentence_id=sentence.sentence_id,
                    issue_type=ISSUE_TYPE_SUPPLEMENT_RECOMMENDED,
                    message=sentence.supplement_reason or "Kein lokales Asset zugeordnet.",
                )
            )

    report.status = READINESS_STATUS_PASS if not report.issues else READINESS_STATUS_NEEDS_REVIEW
    return report
