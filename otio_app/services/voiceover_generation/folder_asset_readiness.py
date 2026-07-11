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
from collections import defaultdict
from dataclasses import dataclass, field

from otio_app.defaults import (
    FOLDER_ASSET_READINESS_MAX_TOTAL_OCCURRENCES_PER_ASSET,
    FOLDER_ASSET_READINESS_MIN_REUSE_DISTANCE_SHOTS,
    FOLDER_ASSET_READINESS_SHOT_MAX_SEC_HEURISTIC,
    FOLDER_ASSET_READINESS_WORDS_PER_SECOND_HEURISTIC,
)
from otio_app.models import Project
from otio_app.services.generic_outro_selector import asset_id_for_path
from otio_app.services.inventory_loader import load_folder_inventory
from otio_app.services.voiceover_generation.models import ClosingVisualPlan, FolderVoiceoverDraft, SentenceItem

__all__ = [
    "READINESS_STATUS_PASS",
    "READINESS_STATUS_NEEDS_REVIEW",
    "ISSUE_TYPE_INVALID_ASSET_ID",
    "ISSUE_TYPE_DIRECT_REPEAT",
    "ISSUE_TYPE_LONG_SENTENCE_LOW_ALTERNATIVES",
    "ISSUE_TYPE_SUPPLEMENT_RECOMMENDED",
    "ISSUE_TYPE_CLOSING_SHOT_MISSING",
    "ISSUE_TYPE_CLOSING_SHOT_REUSES_RECENT_SENTENCE",
    "ISSUE_TYPE_ASSET_OVER_FOLDER_LIMIT",
    "ISSUE_TYPE_ASSET_REUSE_DISTANCE_TOO_SHORT",
    "ISSUE_TYPE_SCARCE_ASSET_ASSIGNED_TO_FLEXIBLE_SENTENCE",
    "CLOSING_SHOT_ISSUE_SENTENCE_ID",
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
# Nutzervorgabe (Juli 2026): Closing-Shot- und folder-weite Asset-
# Allokations-Checks — siehe ClosingVisualPlan (models.py) und "Asset
# allocation across this whole location" im Autor-Prompt (prompts.py).
ISSUE_TYPE_CLOSING_SHOT_MISSING = "CLOSING_SHOT_MISSING"
ISSUE_TYPE_CLOSING_SHOT_REUSES_RECENT_SENTENCE = "CLOSING_SHOT_REUSES_RECENT_SENTENCE"
ISSUE_TYPE_ASSET_OVER_FOLDER_LIMIT = "ASSET_OVER_FOLDER_LIMIT"
ISSUE_TYPE_ASSET_REUSE_DISTANCE_TOO_SHORT = "ASSET_REUSE_DISTANCE_TOO_SHORT"
ISSUE_TYPE_SCARCE_ASSET_ASSIGNED_TO_FLEXIBLE_SENTENCE = "SCARCE_ASSET_ASSIGNED_TO_FLEXIBLE_SENTENCE"

# Marker-sentence_id für Issues, die den Closing Shot betreffen (kein
# echter Satz — analog zu _CLOSING_VISUAL_PLAN_SENTENCE_ID in
# voiceover_author_service.py).
CLOSING_SHOT_ISSUE_SENTENCE_ID = "closing"


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
    # Nutzervorgabe (Juli 2026): Closing-Shot- und Asset-Allokations-Zähler
    # — siehe ISSUE_TYPE_CLOSING_SHOT_*/ISSUE_TYPE_ASSET_*/ISSUE_TYPE_SCARCE_*.
    closing_shot_missing_count: int = 0
    closing_shot_reuse_conflict_count: int = 0
    asset_over_folder_limit_count: int = 0
    asset_reuse_distance_violation_count: int = 0
    scarce_asset_conflict_count: int = 0
    status: str = READINESS_STATUS_PASS
    issues: list[SentenceAssetReadinessIssue] = field(default_factory=list)


@dataclass(frozen=True)
class _ShotSlot:
    """Ein einzelner 'Shot' in geschriebener Reihenfolge — Grundlage für die
    folder-weite Abstands-/Gesamtnutzungs-Zählung (siehe
    `_collect_asset_shot_positions`). Ein Satz mit `planned_segments` zählt
    als mehrere Slots (einer pro Segment), ein Satz ohne als ein Slot, der
    Closing Shot ist immer der letzte Slot."""

    label: str
    asset_ids: tuple[str, ...]


def _shot_slots_for_sentence(sentence: SentenceItem) -> list[_ShotSlot]:
    if sentence.planned_segments:
        return [
            _ShotSlot(
                label=f"{sentence.sentence_id}#segment{segment.segment_order}",
                asset_ids=tuple(
                    aid for aid in ([segment.primary_asset_id] + list(segment.backup_asset_ids)) if aid
                ),
            )
            for segment in sentence.planned_segments
        ]
    return [
        _ShotSlot(
            label=sentence.sentence_id,
            asset_ids=tuple(
                aid
                for aid in (
                    [sentence.primary_asset_id]
                    + list(sentence.backup_asset_ids)
                    + list(sentence.second_backup_asset_ids)
                )
                if aid
            ),
        )
    ]


def _shot_slots_for_draft(draft: FolderVoiceoverDraft) -> list[_ShotSlot]:
    """Flache, geschriebene Shot-Reihenfolge über ALLE sentence_items
    (inkl. planned_segments) UND den Closing Shot als letzten Slot — die
    gemeinsame Grundlage für die Gesamtnutzungs- UND Abstands-Prüfung."""
    slots: list[_ShotSlot] = []
    for sentence in draft.sentence_items:
        slots.extend(_shot_slots_for_sentence(sentence))
    closing = draft.closing_visual_plan
    slots.append(
        _ShotSlot(
            label=CLOSING_SHOT_ISSUE_SENTENCE_ID,
            asset_ids=tuple(
                aid
                for aid in (
                    [closing.primary_asset_id]
                    + list(closing.backup_asset_ids)
                    + list(closing.second_backup_asset_ids)
                )
                if aid
            ),
        )
    )
    return slots


def _collect_asset_shot_positions(draft: FolderVoiceoverDraft) -> dict[str, list[tuple[int, str]]]:
    """Für jedes über den gesamten Ordner (sentence_items + planned_segments
    + Closing Shot, OHNE Intro — siehe Nutzervorgabe) referenzierte Asset:
    Liste von (shot_position_index, shot_label) — EIN Eintrag PRO
    Vorkommen (primary/backup/second_backup zählen einzeln, auch wenn sie
    im selben Slot stehen), in geschriebener Reihenfolge."""
    positions: dict[str, list[tuple[int, str]]] = defaultdict(list)
    for position_index, slot in enumerate(_shot_slots_for_draft(draft)):
        for asset_id in slot.asset_ids:
            positions[asset_id].append((position_index, slot.label))
    return positions


def _check_asset_allocation_across_folder(
    draft: FolderVoiceoverDraft, report: FolderAssetReadinessReport
) -> None:
    """Nutzervorgabe (Juli 2026): folder-weite Gesamtnutzungs- und
    Mindestabstands-Prüfung — Intro-Shots zählen bewusst NICHT mit (dieser
    Report kennt ohnehin nur den einen übergebenen Folder-Draft, niemals
    Intro-Daten)."""
    positions_by_asset = _collect_asset_shot_positions(draft)
    for asset_id, occurrences in positions_by_asset.items():
        if len(occurrences) > FOLDER_ASSET_READINESS_MAX_TOTAL_OCCURRENCES_PER_ASSET:
            report.asset_over_folder_limit_count += 1
            labels = ", ".join(label for _, label in occurrences)
            report.issues.append(
                SentenceAssetReadinessIssue(
                    sentence_id="",
                    issue_type=ISSUE_TYPE_ASSET_OVER_FOLDER_LIMIT,
                    message=(
                        f"Asset '{asset_id}' kommt {len(occurrences)}x in diesem Ordner vor "
                        f"(max. {FOLDER_ASSET_READINESS_MAX_TOTAL_OCCURRENCES_PER_ASSET} empfohlen): "
                        f"{labels}."
                    ),
                )
            )

        sorted_occurrences = sorted(occurrences, key=lambda entry: entry[0])
        for (previous_index, previous_label), (current_index, current_label) in zip(
            sorted_occurrences, sorted_occurrences[1:]
        ):
            distance = current_index - previous_index
            if distance < FOLDER_ASSET_READINESS_MIN_REUSE_DISTANCE_SHOTS:
                report.asset_reuse_distance_violation_count += 1
                report.issues.append(
                    SentenceAssetReadinessIssue(
                        sentence_id=current_label,
                        issue_type=ISSUE_TYPE_ASSET_REUSE_DISTANCE_TOO_SHORT,
                        message=(
                            f"Asset '{asset_id}' wird in '{current_label}' erneut verwendet, nur "
                            f"{distance} Shot(s) nach '{previous_label}' (min. "
                            f"{FOLDER_ASSET_READINESS_MIN_REUSE_DISTANCE_SHOTS} empfohlen)."
                        ),
                    )
                )


def _check_closing_shot(draft: FolderVoiceoverDraft, report: FolderAssetReadinessReport) -> None:
    """Nutzervorgabe (Juli 2026, "wir haben gar kein closing asset nach dem
    letzten Satz"): prüft, ob ein Closing Shot geplant ist und ob er NICHT
    dasselbe Asset wie der letzte oder vorletzte Satz verwendet."""
    if not draft.sentence_items:
        return  # kein Inhalt -> ein Closing Shot ergibt (noch) keinen Sinn

    closing: ClosingVisualPlan = draft.closing_visual_plan
    if not closing.primary_asset_id and not closing.needs_supplement_asset:
        report.closing_shot_missing_count += 1
        report.issues.append(
            SentenceAssetReadinessIssue(
                sentence_id=CLOSING_SHOT_ISSUE_SENTENCE_ID,
                issue_type=ISSUE_TYPE_CLOSING_SHOT_MISSING,
                message=(
                    "Kein Closing Shot geplant — die Pause nach dem letzten Satz bleibt "
                    "visuell ungedeckt, falls das letzte Satz-Asset nicht bis dahin gehalten "
                    "werden kann."
                ),
            )
        )
        return

    recent_primary_ids = {
        sentence.primary_asset_id for sentence in draft.sentence_items[-2:] if sentence.primary_asset_id
    }
    if closing.primary_asset_id and closing.primary_asset_id in recent_primary_ids:
        report.closing_shot_reuse_conflict_count += 1
        report.issues.append(
            SentenceAssetReadinessIssue(
                sentence_id=CLOSING_SHOT_ISSUE_SENTENCE_ID,
                issue_type=ISSUE_TYPE_CLOSING_SHOT_REUSES_RECENT_SENTENCE,
                message=(
                    f"Closing Shot verwendet Asset '{closing.primary_asset_id}', das bereits "
                    "im letzten oder vorletzten Satz als primary_asset_id verwendet wurde."
                ),
            )
        )


def _sentence_alternative_count(sentence: SentenceItem, known_asset_ids: set[str]) -> int:
    """Anzahl DISTINKTER, im Inventory tatsächlich vorhandener Assets, die
    dieser Satz laut LLM ernsthaft in Betracht gezogen hat — Primary/
    Backup/Second-Backup zählen immer mit, unabhängig davon, ob sie auch
    in source_inventory_asset_ids_considered gelistet wurden (defensiv,
    falls das Feld unvollständig befüllt ist)."""
    considered = {
        asset_id
        for asset_id in sentence.source_inventory_asset_ids_considered
        if asset_id in known_asset_ids
    }
    if sentence.primary_asset_id:
        considered.add(sentence.primary_asset_id)
    considered.update(asset_id for asset_id in sentence.backup_asset_ids if asset_id in known_asset_ids)
    considered.update(
        asset_id for asset_id in sentence.second_backup_asset_ids if asset_id in known_asset_ids
    )
    return len(considered)


def _check_scarce_asset_allocation(
    draft: FolderVoiceoverDraft, report: FolderAssetReadinessReport, known_asset_ids: set[str]
) -> None:
    """Nutzervorgabe (Juli 2026): wenn mehrere Sätze DASSELBE Asset als
    primary_asset_id verwenden, sollte der Satz mit den WENIGSTEN echten
    Alternativen es behalten — ein Satz mit MEHR Alternativen, der dasselbe
    knappe Asset trotzdem nutzt, hätte stattdessen ausweichen oder
    supplementieren sollen. Reine Heuristik auf Basis von
    source_inventory_asset_ids_considered (siehe _sentence_alternative_count) —
    kein harter Blocker, da das LLM den Sätzen nicht immer vollständige
    considered-Listen mitgibt."""
    sentences_by_primary: dict[str, list[SentenceItem]] = defaultdict(list)
    for sentence in draft.sentence_items:
        if sentence.primary_asset_id:
            sentences_by_primary[sentence.primary_asset_id].append(sentence)

    for asset_id, sentences in sentences_by_primary.items():
        if len(sentences) < 2:
            continue
        alternative_counts = [
            (_sentence_alternative_count(sentence, known_asset_ids), sentence) for sentence in sentences
        ]
        min_alternatives = min(count for count, _ in alternative_counts)
        for count, sentence in alternative_counts:
            if count > min_alternatives:
                report.scarce_asset_conflict_count += 1
                report.issues.append(
                    SentenceAssetReadinessIssue(
                        sentence_id=sentence.sentence_id,
                        issue_type=ISSUE_TYPE_SCARCE_ASSET_ASSIGNED_TO_FLEXIBLE_SENTENCE,
                        message=(
                            f"Satz '{sentence.sentence_id}' nutzt das knappe Asset '{asset_id}' "
                            f"({count} betrachtete Alternative(n)), obwohl ein anderer Satz mit "
                            f"nur {min_alternatives} Alternative(n) auf dasselbe Asset angewiesen "
                            "ist. Bitte ein anderes Asset verwenden oder needs_supplement_asset setzen."
                        ),
                    )
                )


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
            ([sentence.primary_asset_id] if sentence.primary_asset_id else [])
            + list(sentence.backup_asset_ids)
            + list(sentence.second_backup_asset_ids)
        )
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

    _check_closing_shot(draft, report)
    _check_asset_allocation_across_folder(draft, report)
    _check_scarce_asset_allocation(draft, report, known_asset_ids)

    report.status = READINESS_STATUS_PASS if not report.issues else READINESS_STATUS_NEEDS_REVIEW
    return report
