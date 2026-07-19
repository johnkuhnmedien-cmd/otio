# Phase 13 — Editorial Approval, OTIO Export und Alpha-E2E Plan

**Auftrags-ID:** `DISCOVERY-V2-PHASE13-EDITORIAL-APPROVAL-OTIO-E2E-PLAN-001`
**Status:** PLANNING ONLY — keine Produktimplementierung
**Basis-HEAD:** `9bcd3f72db2ed38e42563008e5628d6846cf8a7e`
**Registry-Ausgang:** Schema **19**
**Ziel-Schema (Implementierung):** Schema **20**
**Installierte OTIO-Bibliothek (geprüft):** `opentimelineio==0.18.1`
**SoT-Rang:** `docs/source_plans/*` — nachrangig; überschreibt keine höheren Dokumente

---

## 0. E15-Verifikation (Pflicht vor Planung)

| Feld | Befund |
|---|---|
| Application Service | `otio_app/discovery_v2/application/feasibility_service.py` |
| Gate-Funktion | `evaluate_ready_for_editorial_review(conn, *, project_id)` |
| Verhalten | Offene Findings mit `severity=="blocking"` und `user_status=="open"` → Return `False`, Planstatus `repair_required`; Ready nur ohne solche Findings und ohne Feasibility-Blocker / Planned Graphics |
| Relevante Tests | `tests/test_discovery_v2_humanity_review.py::test_smoke_e_humanity_signals_flag_generic_stock_and_similar_motif` (injiziert open blocking Finding → Ready verweigert); Hauptpfad A setzt Ready nur nach gültigen Reviews |
| Ergebnis | **CONFIRMED** |

Kein CONFLICT → Planung fortgesetzt.

---

## 1. Konfliktprüfung gegen höhere SoT und Phase 12

| Thema | Quelle | Plan-Konformität |
|---|---|---|
| Phase 13 = Editorial Review + Export Validation + OTIO + Reparse + E2E | MASTER_PLAN / ALPHA_EXECUTION | ja |
| Nutzerfreigabe vor Export | EDITORIAL_QUALITY / ALPHA_SCOPE | ja; nur Mensch erzeugt `approved` |
| Working Media = einzige OTIO-Medienquelle | MEDIA_LIFECYCLE | ja |
| Narration-Audio ≠ Originalmedien; Phase-11-WAVs | Phase-11-Plan / MEDIA | ja |
| Classic `_otio/` read-only; Discovery nur `_otio_v2` | CLASSIC / 00-core | ja; Export unter `export/` |
| KI-Timelines = NEGATIVE_REFERENCE | EDITORIAL_QUALITY | ja; §18 |
| MANUAL; kein Auto beim Rerun | ALPHA_SCOPE / 01-step | ja |
| Fake-only Provider | MODEL_ROUTING | ja; kein LLM für Approval |
| Phase 12 endet bei `ready_for_editorial_review` | D-12-008 / Phase-12-Plan | ja; Startbedingung |
| Kein proprietärer NLE-Export in Alpha | ALPHA_SCOPE / Auftrag | ja |

**Phase-12-Abgleich (Code Schema 19):**

- Current: `visual_edit_project_state` mit Plan-/Humanity-/Feasibility-/Repair-IDs.
- Ready-Gate prüft open blocking Humanity Findings (E15 CONFIRMED).
- Kein `opentimelineio`-Import unter `discovery_v2/` bisher.
- Classic `otio_exporter.py` nur NEGATIVE_REFERENCE-/read-only-Orientierung; **keine** Orchestrierungsreuse.
- Transitionstypen Phase 12: `cut|dissolve|fade|hold`.
- Narration-WAV: `narration/audio/<run_id>/<segment_id>.wav`.

Keine kritischen SoT-Konflikte. Offenes → §31 UNKNOWN.

---

## 2. Phase-13-Grenze

### Beginnt mit

Aktuellem Visual Edit Plan im Status exakt `ready_for_editorial_review`
(plus wirksamer Lock, completed Narration Timeline, aktuelle Humanity/Feasibility).

### Endet mit

Erfolgreichem OTIO-Export + semantischem Reparse und dokumentiertem
vollständigem lokalen Alpha-E2E-Smoke (MANUAL / Fake).

### In Scope

```text
ready_for_editorial_review Visual Edit Plan
→ finale Editorial Review (UI)
→ Export Validation
→ explizite Nutzer-Approval
→ OTIO-Export (opentimelineio 0.18.1)
→ OTIO-Reparse + semantischer Vergleich
→ versionierte Exporthistorie
→ Recovery, MANUAL-UI, Alpha-E2E-Smoke
```

### Explizit nicht in Phase 13

- Premiere- / DaVinci- / Final-Cut-Projektexport
- automatischer Upload, Cloud-Rendering, Veröffentlichung
- echte Provider; automatische Lizenzierung
- neue Phase nach Phase 13

---

## 3. Verbindliche Inputs

Phase-13-Start (Approval-Anzeige und jeder Exportschritt) prüft erneut:

1. Wirksamer Script Lock (`locked`, Fingerprint gültig)
2. Aktuelle Narration Timeline (`completed`, Current)
3. Aktueller Visual Edit Plan (Current-ID; Status `ready_for_editorial_review`)
4. Aktueller Humanity Review (`completed`, an Plan gebunden)
5. Aktueller Feasibility Report (`completed`, Assessment nicht `fail`)
6. Keine stale Review-/Repair-Kette (Fingerprints)
7. Keine open blocking Humanity Findings
8. Keine blocking Feasibility Issues
9. Keine unresolved Planned Graphics (`planned_graphic` ohne Working Media)
10. Nur aktuelle completed Working Media; gültige Source-Ranges
11. Keine relevanten aktiven Runs (Analysis/Editorial/Supplementation/Narration/Visual Edit/Export)

Historische Planversionen sind nie Current-Exportinput.
Fehler mindestens: `export_input_stale`, `editorial_approval_*`, `export_validation_*`.

---

## 4. Domainmodelle (Pydantic, `extra="forbid"`)

### 4.1 EditorialApproval

| Feld | Hinweis |
|---|---|
| `approval_id` | str |
| `project_id` | str |
| `visual_edit_plan_id` | str |
| `humanity_review_id` | str |
| `feasibility_report_id` | str |
| `script_lock_id` | str |
| `narration_timeline_id` | str |
| `input_fingerprint` | Approval-Fingerprint (§10) |
| `user_decision` | Literal `"approved"` \| `"rejected"` |
| `user_comment` | str |
| `accepted_visible_risks` | list[AcceptedExportRisk] |
| `confirmation_checked` | bool (muss `true` bei approve) |
| `status` | `pending` \| `approved` \| `rejected` \| `superseded` \| `invalidated` |
| `revision` | int ≥ 1 |
| `created_at` | datetime |

**Nur ein Mensch** erzeugt `approved` über Application Service + UI.
Kein Fake-/LLM-Adapter darf Approval erzeugen.

### 4.2 ExportValidationReport / ExportValidationIssue

Report: `report_id`, `approval_id`, `visual_edit_plan_id`, `input_fingerprint`,
`otio_profile_version`, `timebase`, `status` (`completed`\|`failed`\|`stale`\|`superseded`),
`issues`, `metrics`, `created_at`.

Issue: `issue_id`, `report_id`, `shot_id`/`assignment_id` nullable, `error_code`,
`severity` (`warning`\|`blocking`), `technical_details`, `blocks_export` bool.

### 4.3 OtioExportRun / OtioExportArtifact

Run: `run_id`, `project_id`, `approval_id`, `validation_report_id`,
`visual_edit_plan_id`, `export_profile_version`, `input_fingerprint`,
`output_relative_path`, `otio_sha256`, `status`
(`queued`\|`running`\|`completed`\|`failed`\|`interrupted`), Zeiten, `error_code`.

Artifact: `artifact_id`, `run_id`, `relative_path`, `byte_size`, `sha256`,
`otio_library_version`, `track_count`, `clip_count`, `total_duration_seconds`,
`total_frames`, `timebase`, `created_at`.

### 4.4 OtioReparseReport

`report_id`, `export_run_id`, `artifact_id`, `parseable`, `semantically_equivalent`,
`deviations`, Track-/Clip-/Duration-Kennzahlen, `status`, `created_at`.

Export-Run `completed` **nur** wenn Export **und** Reparse erfolgreich und
semantisch äquivalent.

---

## 5. Finale Editorial Review (MANUAL-UI)

Seite bevorzugt **Review & Export** (`discovery-review-export`).

Vor Approval anzeigen (persistierte Viewmodels):

- Script Lock, Narration Timeline
- Shotliste, Medienzuordnungen, Source-Ranges
- Humanity Findings, Feasibility Issues, Repair-Historie
- akzeptierte offene Risiken, Planned-Graphic-Status
- Asset-Wiederholungen, Satzgrenzen-Schnitte, Shotdauerverteilung
- geografische / Synthetic-Risiken
- erwartete OTIO-Struktur (Tracks, Clipzahlen-Schätzung)

**Keine automatische Approval.**

Explizit:

1. Checkbox (nie vorselektiert):
   „Ich habe den aktuellen Plan und alle sichtbaren Risiken geprüft.“
2. Button: „Finale Editorial-Freigabe erteilen“
3. Optional Reject-Button mit Kommentar

---

## 6. Approval-Fingerprint

Deterministisch (kanonisches JSON → SHA-256), mindestens:

- Project-ID
- Script-Lock-ID + Lock-Fingerprint
- Narration-Timeline-ID + Timeline-Fingerprint
- Visual-Edit-Plan-ID + Plan-Inhaltshash (Shots/Assignments/Transitions)
- Humanity-Review-ID + Findings-Fingerprint
- Feasibility-Report-ID + Issues-Fingerprint
- Repair-Historie (Run-/Proposal-IDs der Current-Kette)
- akzeptierte sichtbare Risiken
- Working-Media-IDs + Source-Range-Fingerprint
- Approval-Schemaversion (`editorial-approval-v1`)

Keine Abhängigkeit von `created_at`.

Bei Inputänderung nach Approval: Status → `invalidated`; Export blockiert
(`editorial_approval_invalidated` / `export_input_stale`).

---

## 7. Export Validation

Vor OTIO-Erzeugung deterministisch; kein Export bei `blocks_export=true`.

Prüfungen mindestens (§11 Auftrag) inkl.:

- aktuelle Approval `approved` + Fingerprint match
- aktueller Plan `ready_for_editorial_review` (nicht superseded/invalidated)
- aktuelle Humanity/Feasibility; keine open blocking Findings/Issues
- keine Planned Graphics ohne Working Media
- Shotdauern positiv; Timeline vollständig/lückenlos; Frames monoton; keine Überlappung
- rationale Timebase konsistent mit Narration Timeline
- Video: gültige Source-Ranges; Foto: Timeline-Dauer ohne Video-Range
- nur completed Working Media; relative Pfade unter `_otio_v2`
- keine Preview/Temp/Original/Quarantäne/Analysis-Frame/Candidate
- Narration-WAVs: aktuelle Phase-11-Segmente der Current Timeline
- Audio- und Videogesamtdauer innerhalb E6-Toleranz

---

## 8. OTIO-Struktur (Alpha, otio 0.18.1)

### 8.1 Timeline und Tracks (E1–E3)

| Element | Entscheidung |
|---|---|
| Timeline-Name | `discovery_v2_{project_id_short}_{plan_version}` |
| Video | genau **1** Track `V1` (`TrackKind.Video`) |
| Narration-Audio | genau **1** Track `A1` (`TrackKind.Audio`) |
| Weitere Tracks | Alpha: keine (keine Title-/Effekttracks) |

### 8.2 Video (E4–E7)

- Jeder Editorial Shot → genau ein `Clip` (oder `Gap` bei `intentional_visual_only`)
- Clip-Name: `shot_{ordinal}_{shot_id_short}`
- `source_range` aus Python-aufgelösten Source-In/Out (Video) bzw. Timeline-Dauer (Foto)
- Foto: `ExternalReference` auf completed Working-Media-Bild; Clip-Dauer = Timeline-Dauer;
  keine Ken-Burns-/Effektberechnung
- `intentional_visual_only`: `Gap` auf V1 mit Metadatum `discovery.visual_only=true`
- Shotreihenfolge = Editorial-Shot-Ordinale; harte Schnitte ohne OTIO-Transition bei `cut`

### 8.3 Audio / Narration (E8–E10)

- A1 spiegelt **validierte Narration Timeline** (keine Neu-Timingberechnung)
- Voice-Entries → `Clip` auf aktuellem WAV (`narration/audio/...`)
- Pause- und visual_only-Entries → `Gap` auf A1 (stabile OTIO-Darstellung)
- Keine Audio-Neugenerierung beim Export
- Σ A1-Dauer ≡ Narration `total_duration` (E6); Σ V1 ≡ dieselbe Toleranz

### 8.4 Übergänge (E11)

| Phase-12-Typ | OTIO-Abbildung |
|---|---|
| `cut` | kein `Transition`-Objekt (harter Schnitt) |
| `dissolve` | `otio.schema.Transition` Typ `SMPTE_Dissolve`, wenn Handles ≥ resolved duration/2 auf beiden Seiten; sonst **blockierend** (`export_blocking_issue` / unsupported) |
| `fade` | Alpha: **nur Metadatum** am To-Shot (`discovery.transition_intent=fade`, Dauer); kein proprietärer Fade-Effekt |
| `hold` | Metadatum am From-Shot; keine OTIO-Transition |

Unsupported / nicht darstellbare Dissolve → Validation **blockiert** (keine stille Daueränderung, keine negativen Handles).

### 8.5 Metadaten (E12)

Jedes relevante OTIO-Objekt trägt unter `metadata["discovery_v2"]` mindestens:

`project_id`, `visual_edit_plan_id`, `approval_id`, `shot_id`, `sentence_ids`,
`visual_beat_ids`, `visual_intent_ids`, `asset_id`, `working_media_id`,
`assignment_id`, `narration_entry_ids`, `voice_segment_id` (Audio),
`export_profile_version`.

### 8.6 Timebase (E13)

Identisch zur Narration-Timeline-Timebase (`fps_numerator`/`fps_denominator`).
`RationalTime` / `TimeRange` ausschließlich mit dieser Rate.
`timeline.global_start_time = 0`.

### 8.7 Medienreferenzen (E14)

- Persistenz in Registry/Manifest: **relative** Pfade unter `_otio_v2/...`
- In der OTIO-Datei: `ExternalReference.target_url` = **absolut aufgelöstes POSIX**
  aus Project Root (Resolve-kompatibel, analog Classic-Pfadstil) —
  **nur** nach Validation aus Working-Media- bzw. Narration-WAV-Relative
- Kein `file://`-Zwang; kein Fallback auf Original-/Preview-Pfade
- Keine Candidate-/Planned-Graphic-Referenz

---

## 9. OTIO-Reparse und semantischer Vergleich

Nach Serialisierung zwingend:

1. Datei mit `opentimelineio.adapters.read_from_file` öffnen
2. Timeline/Tracks/Clips/Gaps/Transitions zählen
3. Gesamtdauer und Timebase berechnen
4. Medien-`target_url` gegen Manifest-Working-Media-/WAV-Menge prüfen
5. Source-Ranges und Shotreihenfolge gegen Exportmanifest vergleichen
6. Metadaten-IDs vergleichen

### Semantische Gleichheit (E15 Toleranzen)

| Maß | Toleranz |
|---|---|
| Shot-/Clip-Anzahl V1 (Clips+Gaps) | exakt |
| Shot-ID-Reihenfolge in Metadaten | exakt |
| Source-In/Out Frames | exakt |
| Working-Media-/Asset-IDs | exakt |
| Voice-Segment-IDs auf A1 | exakt |
| Timebase num/den | exakt |
| Gesamtdauer Frames V1 und A1 | ≤ **1 Frame** Differenz zueinander und zum Narration-Total |
| Transition-Count (Dissolve) | exakt zur geplanten exportierten Menge |

`completed` nur bei parsebar **und** semantisch äquivalent.

---

## 10. NEGATIVE_REFERENCE

Hinterlegte KI-Timelines und Classic-OTIO-Orchestrierung sind ausschließlich
`NEGATIVE_REFERENCE`.

Verboten:

- strukturelle Nachahmung als Soll
- Qualitätsvergleich gegen KI-Timelines
- Übernahme von Shotlängen/Übergängen/Dramaturgie aus Negativbeispielen

Erlaubt: negative Regressionen („dieses Fehlermuster nicht wiederholen“).
Im Repo liegen derzeit **keine** `.otio`-Fixture-Dateien; Policy gilt dennoch.

Classic `otio_exporter.py`: read-only Orientierung für Bibliotheks-APIs;
**keine** Funktionsreuse der Cut-Plan-/Merge-Orchestrierung.

---

## 11. Schema 20 (ab Schema 19)

Keine Tabellen für proprietäre NLE-Exporte oder Cloud-Publishing.

### 11.1 `editorial_approvals`

Zweck: versionierte Nutzerfreigaben.
Spalten: Felder §4.1 + `relative_json_path`.
FK logisch zu Plan/Reviews/Lock/Timeline.
Unique: `approval_id`; `(project_id, revision)`.
Status: §4.1.
Append-only; neue Revision bei Re-Approval; alte `superseded`/`invalidated`.
Current: `export_project_state.current_editorial_approval_id`.
Stale: Input-Fingerprint-Mismatch → `invalidated`.
Warum jetzt: Exportfreigabe.

### 11.2 `editorial_approval_risks`

Zweck: akzeptierte sichtbare Risiken der Approval.
Spalten: `approval_id`, `risk_id`/`category`, `description`, `source_ref`.
PK `(approval_id, ordinal)` oder risk_id.
Append-only mit Approval.
Warum jetzt: Audit der Nutzerentscheidung.

### 11.3 `export_validation_reports` / `export_validation_issues`

Zweck: deterministische Pre-Export-Prüfung.
Current: `current_export_validation_report_id`.
Stale bei Approval-/Planänderung.
Warum jetzt: Gate vor OTIO.

### 11.4 `otio_export_runs` / `otio_export_artifacts`

Zweck: Exportläufe und Artefaktmetadaten.
Current: `current_otio_export_run_id`, `current_otio_artifact_id`.
Append-only; Konflikte bei Pfadkollision → `otio_artifact_conflict`.
Warum jetzt: versionierter Export.

### 11.5 `otio_reparse_reports`

Zweck: Pflicht-Reparse-Ergebnis.
Current: `current_reparse_report_id`.
Warum jetzt: semantische Exportwahrheit.

### 11.6 `export_project_state`

Spalten: `project_id` PK;
`current_editorial_approval_id`,
`current_export_validation_report_id`,
`current_otio_export_run_id`,
`current_otio_artifact_id`,
`current_reparse_report_id`,
`current_visual_edit_plan_id`,
`current_narration_timeline_id`,
`updated_at`.

Keine Current-Auswahl nach `created_at`.

Optional: `export_runs` Attempts analog Phase 12, wenn Worker asynchron —
mindestens Run-Tabelle `otio_export_runs` deckt Scopes ab.

---

## 12. Artefaktpfade

Nur unter `_otio_v2/export/`:

```text
export/approvals/
export/validation/
export/otio/<export_run_id>/timeline.otio
export/manifests/<export_run_id>/export_manifest.json
export/reparse/
export/runs/
export/reports/
export/temp/<run_id>/
export/latest_approval.json
export/latest_validation.json
export/latest_otio_export.json
export/latest_reparse.json
```

Regeln: relative Pfade; kein `_otio`; kein `..`; kein doppeltes `_otio_v2`;
atomare Publikation; abweichende bestehende Datei nicht überschreiben;
SQLite = Wahrheit.

---

## 13. Atomizität

1. **Approval:** Inputs erneut prüfen → Fingerprint → append-only persistieren;
   **kein** Auto-Export.
2. **Validation:** im Speicher berechnen → Temp-JSON → re-parse → transaktional persistieren.
3. **OTIO Export:** Temp-OTIO schreiben → readback parse → Manifest + Hash →
   semantischer Vergleich → erst dann atomar nach
   `export/otio/<run_id>/timeline.otio` publizieren → SQLite/Current transaktional.
4. Teilweise Datei nie als `completed` markieren.

---

## 14. Runs und Sperren

Scopes: `editorial_approval_only` · `export_validation_only` ·
`otio_export_only` · `otio_reparse_only`.

- Approval: synchroner Application-Pfad erlaubt (analog Script Lock).
- Validation: sync erlaubt; schwerer Export bevorzugt Worker.
- Maximal **ein** aktiver Export-/Validation-Run pro Projekt (geteiltes Gate).
- Gegenseitig sperren mit Analysis, Editorial, Supplementation, Narration, Visual Edit.
- Inputs während Validation/Export unveränderlich (Fingerprint).

Recovery: orphan → `failed`; Attempt → `interrupted` / `worker_interrupted`;
nur `export/temp/<run_id>/` bereinigen; publizierte valide Artefakte bleiben;
kein Gateway; kein Auto-Export; kein OTIO aus Streamlit-Rerun.

---

## 15. MANUAL-UI und UI-No-I/O

Bereiche: Editorial Review · Export Validation · OTIO Export · Reparse-Ergebnis.

Buttons (explizit):

1. Finale Editorial-Freigabe erteilen (mit Checkbox)
2. Export validieren
3. OTIO erzeugen (nur bei Approval + bestandener Validation)

Nach Export anzeigen: Pfad, Hash, Tracks, Clips, Dauer, Timebase,
Medienreferenzen, semantischer Vergleich, Reparse-Status.

**UI-No-I/O:** kein OTIO write/parse, kein Medien-/Audio-I/O, kein FFmpeg/ffprobe,
kein Hashing/stat, keine Validation/Approval/Jobstarts, keine direkte SQLite-Abfrage
beim Render — nur Services + Viewmodels.

---

## 16. Vollständiger Alpha-E2E-Smoke

Lokaler Temp-Projektpfad, Fake-only, kein Netzwerk:

```text
Projekt → Intake → Analyse → Observation Review → Brief → Narrative/Hooks
→ Script → Coverage → Gap → Script Lock → Fake Voice → Pause → Timing
→ Visual Edit Plan → Humanity → Feasibility → Repair → erneute Reviews
→ Editorial Approval → Export Validation → OTIO → Reparse
```

Nachweise: nur `_otio_v2`; kein `_otio`; Classic unverändert; Current-IDs;
Fingerprints; OTIO referenziert nur Working Media + aktuelle Narration-WAVs;
Reparse semantisch erfolgreich.

---

## 17. Fake-Smokes A–H

| ID | Inhalt |
|---|---|
| **A** | Ready Plan → Approval → Validation → OTIO → Reparse PASS |
| **B** | Inputänderung nach Anzeige blockiert Approval (Fingerprint) |
| **C** | Plan-/Reviewänderung nach Approval → invalidated; Export blockiert |
| **D** | Original/Preview/Temp/Quarantäne/Analysis Frame/Candidate blockiert |
| **E** | Planned Graphic blockiert Validation und Export |
| **F** | Shotanzahl, Reihenfolge, Ranges, Timebase, Dauer nach Reparse |
| **G** | Crash vor Publish / nach Temp → keine completed-Zeile, keine halbe finale Datei |
| **H** | UI doppel-render No-I/O; separater vollständiger Alpha-E2E |

---

## 18. Fehlercodes

Mindestens:

`editorial_approval_required` · `editorial_approval_rejected` ·
`editorial_approval_invalidated` · `editorial_approval_confirmation_required` ·
`editorial_approval_fingerprint_mismatch` ·
`export_validation_required` · `export_validation_failed` · `export_blocking_issue` ·
`invalid_export_media_reference` · `invalid_export_audio_reference` ·
`invalid_export_source_range` · `invalid_export_timebase` ·
`planned_graphic_not_exportable` ·
`otio_export_failed` · `otio_artifact_conflict` · `otio_serialize_failed` ·
`otio_reparse_failed` · `otio_semantic_mismatch` ·
`export_input_stale` · `export_run_already_active` ·
`visual_edit_run_already_active` · `narration_run_already_active` ·
`analysis_run_already_active` · `editorial_run_already_active` ·
`supplementation_run_already_active` ·
`export_registry_write_failed` · `export_artifact_write_failed` ·
`worker_interrupted` · `report_write_failed`

Keine Secrets, Vollpayloads oder unkontrollierte Absolutpfade in Fehlertexten.

---

## 19. Testplan (Gruppen)

1. Schema 19 → 20 + Idempotenz; keine NLE-/Cloud-Tabellen
2. Approval nur durch Nutzer; Checkbox Pflicht
3. Approval-Fingerprint + Invalidierung
4. Humanity/Feasibility Pflicht vor Approval
5. Export Validation vollständig
6. Nur Working Media + aktuelle Narration-WAVs
7. Preview/Original/Temp/Candidate abgelehnt
8. Planned Graphic blockiert
9. Rationale Timebase; V1/A1 Struktur
10. Fotos, Pausen (Gaps), Transitions laut E11
11. Metadatenidentitäten; Source-Ranges
12. Atomischer Export; Reparse; Semantik
13. Recovery; Current State; Stale-Matrix
14. UI-No-I/O
15. NEGATIVE_REFERENCE nicht als Vorlage
16. Kein proprietärer NLE-Export; kein echter Provider
17. Classic-/Without-VO-Regression; `_otio_v2`-Isolation
18. Smokes A–H + vollständiger Alpha-E2E

---

## 20. Entscheidungen (verbindlich für Implementierung)

| # | Thema | Entscheidung |
|---|---|---|
| E1 | Timeline-Name | `discovery_v2_{project_id_short}_{plan_version}` |
| E2 | Trackstruktur | genau `V1` Video + `A1` Narration-Audio |
| E3 | Exportprofilversion | `discovery-otio-export-v1` |
| E4 | Medienreferenztyp | `ExternalReference`; Registry relativ; OTIO absolut POSIX aus Project Root |
| E5 | Relative URL-Strategie | persistiere `_otio_v2/...` Relatives; resolve nur aus validated Working Media / Narration WAV |
| E6 | Dauer-Toleranz Export | \|V1−A1\| und \|Track−NarrationTotal\| ≤ **1 Frame** |
| E7 | Fotoabbildung | Clip + Still-ExternalReference; Dauer = Timeline-Dauer; keine Effekte |
| E8 | Intentional visual only | `Gap` auf V1 + Metadatum |
| E9 | Audio-Pausen | `Gap` auf A1; keine stille Clipfüllung |
| E10 | Keine Timing-Neuberechnung | Export liest fertige Narration Timeline / Shots |
| E11 | Transitions | cut=hart; dissolve=OTIO SMPTE_Dissolve wenn Handles reichen sonst block; fade/hold=Metadaten only |
| E12 | Metadatenschema | `metadata["discovery_v2"]` mit IDs laut §8.5 |
| E13 | Timebase | Narration-Timebase unverändert |
| E14 | Unsupported OTIO | blockieren in Validation; keine stille Umdeutung |
| E15 | Reparse-Toleranzen | §9 Tabelle |
| E16 | Approval-Risikoformat | `AcceptedExportRisk{risk_id, category, description, source_ref}` |
| E17 | Sperrmodell | max. 1 aktiver Export/Validation-Run; mutual exclusion mit Phase 8–12 Runs |
| E18 | Current State | `export_project_state` explizite IDs |
| E19 | Artefaktwurzel | `_otio_v2/export/` |
| E20 | OTIO-Dateiname | `export/otio/<export_run_id>/timeline.otio` |
| E21 | Approval sync | Application-Pfad sync; Export-Worker async oder sync-flag |
| E22 | Schema | 19 → **20** |
| E23 | Provider | keine echten Provider; kein LLM-Approval |
| E24 | Classic | keine Orchestrierungsreuse; `_otio/` unangetastet |

---

## 21. Modulvorschlag

```text
otio_app/discovery_v2/
  domain/export.py
  export_paths.py
  application/editorial_approval_service.py
  application/export_validation_service.py
  application/otio_export_service.py
  application/otio_reparse_service.py
  application/export_job_recovery.py
  adapters/export_job_launcher.py
  jobs/export_worker.py
  persistence/export_repository.py
  ui/review_export_page.py
```

Neuer OTIO-Builder nur unter `discovery_v2` (nicht Classic-Exporter importieren).

---

## 22. Implementierungsaufteilung (Makroauftrag)

```text
Schema 20 + Export Domain/State
+ Editorial Approval (Mensch only) + Fingerprint
+ Export Validation Engine
+ OTIO Builder (V1/A1, Gaps, Dissolve-regeln) + Manifest
+ Reparse + semantischer Vergleich
+ Runs/Launcher/Recovery + Sperren
+ Review & Export UI (MANUAL) + No-I/O
+ Tests + Smokes A–H + vollständiger Alpha-E2E
```

---

## 23. UNKNOWN-Punkte

- NLE-spezifisches Verhalten nach Import des Alpha-OTIO in Premiere/Resolve/FCP
  (nicht Alpha-Ziel)
- Vollständige OTIO-Effekt-/Freeze-Frame-Semantik jenseits Still-Clips
- Multi-Videotrack / Nested Compositions
- Cloud-Signaturen oder remote Media URLs
- Exakte Pixel-/Color-Pipeline in NLEs nach Import

Bibliotheksstand für diesen Plan geprüft: `opentimelineio==0.18.1` mit
`Timeline`, `Track`, `Clip`, `Gap`, `Transition`, `ExternalReference`, `Marker`.

---

## 24. DoD für späteren Implementierungsauftrag

- Schema 20; Smokes A–H + Alpha-E2E grün
- Approval nur mit Checkbox + Nutzeraktion
- Validation vor jedem OTIO
- OTIO nur Working Media + aktuelle Narration-WAVs
- Reparse semantisch äquivalent
- Stale invalidiert Approval/Export
- UI-No-I/O; kein Auto-Export
- kein Classic-`_otio`-Schreiben; kein proprietärer NLE-Export
- kein echter Provider
- Baseline-18 Failures unangetastet; keine neuen Discovery-Failures

---

## 25. Nächste erlaubte Aktion

Nach **Freigabe dieses Plans**:

→ Phase-13-**Implementierungs**-Makroauftrag gemäß §22.

Gesperrt: echte Provider, proprietäre NLE-Exporte, Cloud-Publish, Post-Alpha-Phasen.
