# Alpha — Visual Edit / Repair Rework Plan

**Auftrags-ID:** `DISCOVERY-V2-ALPHA-VISUAL-EDIT-REPAIR-REWORK-PLAN-001`
**Status:** PLANNING ONLY — keine Produkt- oder Testimplementierung
**Basis-HEAD:** `1e16916ee16f10fc7617d001093d46d67d412c6c`
**Branch:** `cursor/discovery-v2-integration` · PR `#69`
**Registry-Schema:** **20** (bleibt 20; keine Migration in diesem Plan)
**SoT-Rang:** `docs/source_plans/*` — nachrangig; überschreibt keine höheren Dokumente
**Vorgänger:** `PHASE12_VISUAL_EDIT_HUMANITY_FEASIBILITY_REPAIR_PLAN.md`

---

## 1. Ausgangslage

Im manuellen Alpha-Test (USA_v2 / Visual Edit) entsteht ein technisch ungültiger
Plan und eine nicht auflösbare Reparaturschleife:

| Beobachtung | Wert |
|---|---|
| Shots | 6 |
| Assignments | 6 |
| Verwendete Assets | **1** |
| Asset-ID (Beispiel) | `07681fe1-23e8-4996-aa5a-e5cb47daf1ed` |
| Asset-Verwendungen | 6 |
| Feasibility E3 | Asset reuse exceeds E3 |
| Feasibility E4 | Video source range reuse exceeds E4 |
| severity | `blocking` |
| blocks_phase_13 | `true` |
| deterministically_repairable | `false` |

Mehrere Feasibility Reports teilen denselben `plan_id`, denselben
`input_fingerprint`, dieselbe `assignment_count` und dieselbe `issue_count`.
Der Plan ändert sich nicht. „Reparaturen anwenden“ bleibt faktisch unwirksam
bzw. deaktiviert. Erneute Prüfung reproduziert denselben Fehler.

**Script-Lock-Realtest:** fachlich erfolgreich abgeschlossen (Identity Rework).
**End-to-End am Visual-Edit-/Repair-Schritt:** blockiert durch E3/E4-Schleife.
**R1.4:** weiterhin gesperrt.

---

## 2. Reproduktionsdaten (read-only)

Nutzerartefakte wurden ausschließlich read-only ausgewertet und sind **keine**
Implementierungsabhängigkeit:

- `latest_feasibility_report.json`
- `50b5d5fe-3f65-4751-a8f5-64e9879a12d6.json`
- `c91b197e-d76a-40f5-afec-ebd2dc05e7d9.json`
- `3b7d4143-37f7-4904-9579-fe84a6265523.json`
- `assets.sqlite3` (Nutzerregistry — unverändert)

Der spätere Implementierungsauftrag muss **kleine deterministische Fixtures**
unter `tests/` bzw. Test-Hilfsfabriken verwenden. Keine persönlichen Paths,
keine Nutzerregistry, keine Secrets.

**Minimal-Fixture-Idee (V1):**

```text
6 Shots, Narration Timeline fix
6 akzeptierte Observations + completed Working Media (distinct asset_ids)
FakeText / Planer-Input so verdrahtet, dass ohne Fix nur Asset[0] gewählt wird
→ Feasibility: E3 (count=6 > ASSET_REUSE_MAX=3)
→ bei Video + gleicher Tech-Shot-Range: zusätzlich E4 (≥90 % Overlap)
```

---

## 3. Untersuchte Produktbereiche

| Bereich | Primärdateien |
|---|---|
| Domain / Policy | `otio_app/discovery_v2/domain/visual_edit.py` |
| Fake Visual Edit Adapter | `otio_app/discovery_v2/adapters/text_fake.py` (`_visual_edit_plan`, `_editorial_repair_proposal`) |
| Text Gateway | `otio_app/discovery_v2/adapters/text_gateway.py` |
| Visual Edit Planer / Assignments | `otio_app/discovery_v2/application/visual_edit_plan_service.py` |
| Feasibility | `otio_app/discovery_v2/application/feasibility_service.py` |
| Repair Propose / Apply | `otio_app/discovery_v2/application/visual_edit_repair_service.py` |
| UI | `otio_app/discovery_v2/ui/visual_edit_page.py` |
| Review/Export-Gates | `evaluate_ready_for_editorial_review` in Feasibility; Review/Export-Seite |
| Persistence | `visual_edit_repository.py`, Schema-Tabellen in `asset_registry_database.py` |
| Bestehende Tests | `tests/test_discovery_v2_visual_edit_*.py`, `tests/test_discovery_v2_feasibility.py` |
| Classic / Without-VO | nur read-only; keine Änderungen vorgesehen |

Policy-Konstanten (bereits vorhanden):

- `ASSET_REUSE_MAX = 3` (E3)
- `SOURCE_RANGE_OVERLAP_RATIO_MAX = 0.90` (E4)

---

## 4. Root-Cause-Matrix

| Hypothese | Befund | Genauigkeit |
|---|---|---|
| **A** Planer ignoriert Asset-Diversität | **BESTÄTIGT** | Fake `_visual_edit_plan` bindet **jeden** Shot an `candidates[0]`. Bei `len(candidates) > 1` wird `shot_count=6` gewählt, aber weiterhin nur das erste Asset referenziert. Kein Ranking, keine Reuse-Penalty, keine globale Optimierung. |
| **B** E3 erst nach Planung | **BESTÄTIGT** | `ASSET_REUSE_MAX` wird nur in `feasibility_service` nach Planpersistenz geprüft. Weder Fake-Adapter noch `_resolve_assignment` zählen laufende Asset-Nutzung. |
| **C** E4 bei Source-Range ignoriert | **BESTÄTIGT** | Alle Video-Shots teilen denselben `candidate_technical_shot_id` (erster Tech-Shot). `_resolve_video_range` kennt keine planweite Occupancy-Registry; Bias allein reicht nicht gegen ≥90 %-Overlap bei gleicher Tech-Shot-Länge. E4 entsteht in Feasibility über `_overlap_ratio`. |
| **D** Repair Proposals nicht blockerbezogen | **BESTÄTIGT** | Deterministische Proposals nur bei `deterministically_repairable=True` (E6 Transitions). E3/E4 setzen `repairable=False`. Fake Repair erzeugt generisches `vary_first_local_motif` ohne Assignment-Ziel, ohne Zielasset, ohne erwartete E3/E4-Wirkung. |
| **E** Keine Reassignment-Operation | **BESTÄTIGT** | `apply_selected_repair_proposals` → `_copy_bundle_as_new_version` klont Assignments 1:1 (neue IDs, gleiche Assets/Ranges). Kein `replace_assignment_asset` / `replace_assignment_source_range`. |
| **F** Identischer Plan endlos geprüft | **BESTÄTIGT** | Keine Deduplizierung gleicher Feasibility-Input-/Issue-Signatur. Apply-Button verlangt `user_status=="selected"`, UI setzt nie `selected` → Apply bleibt disabled; Nutzer klickt erneut Feasibility/Propose → gleiche Artefakte. |

### 4.1 Genau Root Cause des Ein-Asset-Plans

**Primärursache (Planer / Fake Adapter):**

In `text_fake.py` `_visual_edit_plan`:

1. `candidate = candidates[0]`
2. Schleife über `shot_count` Shots schreibt für jedes Shot dieselbe
   `candidate_asset_id` / `candidate_working_media_id` /
   `candidate_observation_id` / `candidate_technical_shot_id`
3. `shot_count` steigt auf 6, sobald mehrere Candidates und ≥3 Sätze vorhanden
   sind — Diversität wird simuliert, aber nicht materialisiert

**Sekundärursache (Python Resolve):**

`_resolve_assignment` übernimmt die Intent-Kandidaten 1:1 ohne E3-Awareness und
ohne Alternativsuche in der Candidate-Map.

### 4.2 Genau Ursache der E4-Range-Wiederholung

1. Alle sechs Intents zeigen auf denselben Technical Shot.
2. `_resolve_video_range` berechnet In/Out nur aus Tech-Shot-Grenzen + Bias
   (`beginning`/`middle`/`end`) + Desired Duration — **ohne** bereits belegte
   Ranges anderer Assignments desselben Working Media.
3. Bei ähnlicher Desired Duration und begrenzter Tech-Shot-Länge überlappen die
   resultierenden Ranges ≥ 90 % → Feasibility E4.

---

## 5. E3-Vertrag (Asset Diversity)

### 5.1 Harte Regel (bestehend, verbindlich beibehalten)

```text
Für jedes asset_id im aktuellen Plan:
  count(assignments with that asset_id) ≤ ASSET_REUSE_MAX  (= 3)
sonst Feasibility blocking (E3).
```

- Quelle der Wahrheit: Domain-Konstante `ASSET_REUSE_MAX` in
  `domain/visual_edit.py` — **nicht** hart im UI codieren.
- Zählt Assignments des aktuellen Plan-Bundles (nicht historische Versionen).
- Bild und Video gleich: E3 ist asset_id-basiert, unabhängig von `media_kind`.

### 5.2 Planzeit-Verhalten (neu, V2)

```text
Assignment erzeugen
→ aktuelle Reuse-Zählung im Aufbau-Bundle berücksichtigen
→ wenn Zielasset bereits ASSET_REUSE_MAX mal genutzt:
     nächstes geeignetes Accepted+Working-Media-Candidate wählen
→ wenn kein geeignetes Ersatzasset:
     Coverage-/Feasibility-Blocker erzeugen (kein still ungültiger Plan)
```

Bevorzugt: **Vielfalt**, wenn mehrere geeignete Assets vorhanden sind
(Round-Robin / niedrigste aktuelle Nutzung / deterministische Sortierung nach
`asset_id`).

### 5.3 Zu wenig Assets

| Situation | Verhalten |
|---|---|
| ≥ shot_count geeignete Assets | Diversität anstreben; E3 einhalten |
| 2…ASSET_REUSE_MAX geeignete Assets, mehr Shots | Reuse bis Grenze erlaubt; darüber Blocker |
| genau 1 geeignetes Asset, > ASSET_REUSE_MAX Shots | **kein** still ungültiger Plan; sichtbarer Blocker `additional_coverage_required` (oder Feasibility E3 mit klarer Next Action) |
| absichtliche Wiederholung redaktionell gewünscht | nur über explizites Repair Proposal / Nutzerentscheid — nicht still; Humanity darf Motiv-Wiederholung warnen (`similar_motif_sequence`) |

### 5.4 Humanity & Authenticity

E3 ist technisch. Humanity bleibt separat: Motivläufe (`max_similar_motif_run`)
können trotz E3-konformer Vielfalt warnen. Rework ändert Humanity-Signale nicht,
außer dass Fake-Pläne nach Diversität weniger oft `similar_motif_sequence`
triggern.

---

## 6. E4-Vertrag (Source-Range Coordination)

### 6.1 Harte Regel (bestehend)

```text
Für Paare von Video-Assignments mit gleichem asset_id + working_media_id:
  overlap_ratio ≥ SOURCE_RANGE_OVERLAP_RATIO_MAX (0.90) → blocking E4
```

`overlap_ratio = overlap_duration / min(range_a, range_b)`.

### 6.2 Planzeit-Verhalten (neu, V2)

```text
Asset / Technical Shot wählen
→ verfügbare Technical Shots / Ranges laden
→ Occupancy-Registry des Planaufbaus konsultieren
→ gültige Range mit Overlap < 0.90 zu allen belegten Ranges wählen
→ Frames runden (bestehende seconds_to_frame_nearest)
→ Registry aktualisieren
```

### 6.3 Definitionen

| Begriff | Vertrag |
|---|---|
| Identische Range | start/end (nach Framerundung) gleich → E4 |
| Ähnliche Range | Overlap-Ratio ≥ 0.90 → E4 |
| Erlaubte Überlappung | Overlap-Ratio **&lt; 0.90** |
| Mindestabstand | kein zusätzlicher harter Gap in R1; Overlap-Regel reicht. Optional deferred: Mindest-Gap in Sekunden |
| Technical-Shot-Grenzen | Range muss innerhalb Tech-Shot `[start,end]` + Handle-Regeln bleiben |
| Zu kurzes Quellmaterial | kein gültiger nichtüberlappender Slot → Blocker (`source_range_out_of_bounds` oder Feasibility E4 + Next Action `manual_reassignment_required` / Coverage) |
| Bewusste Ausnahme | nur über explizites User-selected Proposal; nicht still |

### 6.4 Fehlerzuordnung

E4-Issues müssen `assignment_id` (und idealerweise `shot_id`) setzen — heute
wird oft nur `right.assignment_id` gesetzt und `shot_id=None`. Rework V1/V2:
beide Assignments der Verletzung referenzieren (Issue-Duplikat oder
`affected` in Details), damit Repair und UI zuordenbar sind.

---

## 7. Candidate- und Ranking-Vertrag

### 7.1 Zulässige Kandidaten

Nur:

- aktuelle akzeptierte Visual Observations
- validiertes **completed** Working Media
- aktuelles Projekt-Asset (kein Preview, Original, Temp, Quarantäne)

### 7.2 Ranking (Planer / Python-Guard)

Reihenfolge für Fake und Python-Guard (deterministisch):

1. Editorial Intent-Match (Fake darf redaktionell vorschlagen)
2. Niedrigste aktuelle Reuse-Zählung im Planaufbau (E3-aware)
3. Verfügbarkeit E4-fähiger Source-Range (Video)
4. Stabiler Tie-Break: `asset_id` aufsteigend, dann `observation_id`

Fake-Adapter muss bei mehreren Candidates **rotieren oder ranken**, nicht
`candidates[0]` für alle Shots.

Python darf technisch gleichwertige Alternativen nur dann still wählen, wenn
keine redaktionelle Differenz besteht (siehe §10 Option 2 — **nicht** R1-Default).

---

## 8. Planer-Härtung (V2)

### 8.1 Fake Adapter (`text_fake._visual_edit_plan`)

- Pro Shot eigenen Candidate aus der gerankten Liste wählen
- Laufende Reuse-Map `asset_id → count` führen; Stop bei `ASSET_REUSE_MAX`
- Video: diversifizieren über Technical Shots / Bias **und** Occupancy
- Wenn unmöglich: Payload mit expliziten Uncertainty-/Blocker-Hinweisen;
  Python setzt Feasibility-Blocker (kein Fake-Erfolg)

### 8.2 Python Resolve (`visual_edit_plan_service`)

- `_resolve_assignment` erhält planweite `reuse_counts` + `occupied_ranges`
- Bei E3-Verletzung des Intent-Assets: E3-konforme Alternative aus Candidate-Map
  nur wenn redaktionell als technisch gleichwertig markiert **oder** Intent
  bereits Alternativen liefert — sonst Issue statt stiller Fremdsubstitution
- `_resolve_video_range(..., occupied=...)` wählt nächsten freien Slot
  (Bias als Präferenz, Occupancy als harte Schranke)

### 8.3 Trennung Planerfehler vs. Repairfehler

| Schicht | Verantwortung |
|---|---|
| Planer (Fake + Resolve) | E3/E4 möglichst vermeiden; Diversität |
| Feasibility | E3/E4 final validieren; Issues erzeugen |
| Repair | Nur wenn Plan existiert und Blocker adressierbar; ausführbare Ops |
| UI | Wirksame Proposals auswählbar; Loop-Schutz; klare Next Actions |

---

## 9. Repair-Operationen

### 9.1 Bewertete Operationen

| Operation | R1 | Begründung |
|---|---|---|
| `replace_assignment_asset` | **ja** | Direkter Fix für E3 |
| `replace_assignment_source_range` | **ja** | Direkter Fix für E4 |
| `swap_assignment_assets` | deferred | Nützlich, aber komplexer; durch 2× replace abdeckbar |
| `shorten_assignment_source_range` | deferred | Kann E4 helfen; Risiko redaktioneller Längenänderung |
| `split_assignment` | deferred | Hohe Dramaturgie-Wirkung |
| `request_additional_coverage` | **ja (Terminalzustand)** | Wenn keine Alternative — kein Fake-Apply |

### 9.2 Minimaler sicherer R1-Umfang

Nur:

1. `replace_assignment_asset`
2. `replace_assignment_source_range`

Nur anwenden wenn:

- Zielasset gültiges completed Working Media besitzt
- aktuelle akzeptierte Observation vorliegt
- Asset zum Visual Intent passt (Proposal-Ebene; Fake liefert Match)
- keine neue E3-/E4-Verletzung entsteht (Validate-before-apply)
- neue Source-Range technisch gültig (Bounds, Frames, Handles)

---

## 10. Reassignment-Vertrag

### 10.1 Option 1 (bevorzugt, R1)

```text
Feasibility E3/E4
→ Repair Proposal mit ausführbarer Operation
→ Nutzer wählt Proposal (user_status=selected)
→ Python wendet Operation an
→ neue Planversion
→ neuer Plan-Fingerprint / Input-Fingerprint-Kontext
→ erneute Feasibility
```

`deterministically_repairable=false` für E3/E4 bleibt korrekt: die Wahl des
Ersatzassets ist redaktionell. Trotzdem muss ein **ausführbares** Proposal
existieren (nicht nur Text).

### 10.2 Option 2 (nur mit Domainbegründung — nicht R1-Default)

Rein technische Alternativen (z. B. gleiche Observation-Familie, identischer
Intent, nur anderer Tech-Shot-Slot ohne Motivwechsel) dürften deterministic
gewählt werden. **R1 verzichtet darauf**, um stille redaktionelle Substitution
zu vermeiden.

### 10.3 Proposal-Pflichtfelder (ausführbar)

Jedes wirksame Proposal muss maschinenlesbar enthalten:

| Feld | Inhalt |
|---|---|
| `repair_type` | `replace_assignment_asset` \| `replace_assignment_source_range` |
| `affected_ids` | mind. `assignment_id` (+ `shot_id` empfohlen) |
| Zielasset / Zielrange | siehe §16 Artefakt-Ops |
| `addresses_issue_ids` oder Issue-Signatur | mind. ein konkreter Blocker |
| `expected_effect` | menschenlesbar: E3/E4-Wirkung |
| `user_status` | `proposed` → Nutzer setzt `selected` |

Nicht zulässig:

- generische Texte ohne Operation
- Vorschläge ohne Assignment-ID
- Vorschläge ohne Zielasset/Zielrange
- auswählbare Vorschläge, die keinen Blocker adressieren

### 10.4 Apply-Semantik

```text
validate ops against current plan + candidates
→ copy bundle as new version (bestehende Versionierung)
→ apply ops to assignments in new bundle
→ supersede old plan
→ stale humanity + feasibility
→ persist RepairRun + RepairResult.changes (konkrete Diffs)
→ mark proposals applied
→ mark_current_plan(new)
→ optional auto-feasibility oder MANUAL „Feasibility prüfen“
```

Kein stilles Auto-Accept. Kein stilles Überschreiben ohne neue Version.
Alte Planversion bleibt historisch erhalten.

---

## 11. Loop-Schutz (V4)

### 11.1 Issue-Signatur

Kanonsich, sortiert, stabil:

```text
issue_signature = sha256(
  sorted(
    f"{error_code}|{shot_id or ''}|{assignment_id or ''}|{normalize(technical_details)}"
  )
)
```

### 11.2 Fingerprints

| Fingerprint | Rolle |
|---|---|
| Plan `input_fingerprint` | Eingabekontext (Lock, Timeline, Observations, …) |
| Plan-Inhalt-Fingerprint (neu oder aus Bundle) | Shots+Assignments+Transitions inhaltlich |
| Feasibility-Input-Fingerprint | = Plan-Inhalt (+ Timebase); Reports speichern heute `plan.input_fingerprint` |
| Repair-Input-Fingerprint | plan_id + plan_version + issue_signature + selected_proposal_ids |

### 11.3 Dedup-Regel

```text
gleicher plan_id
+ gleicher Plan-Inhalt-Fingerprint
+ gleicher Feasibility-Input-Fingerprint
+ gleiche Issue-Signatur
→ kein neuer „Fortschritt“
→ Outcome: repair_no_change
→ UI: „Keine Änderung am Plan erkannt. Die vorhandenen Reparaturvorschläge
       lösen die Blocker nicht.“
```

Feasibility-Runs dürfen historisch gespeichert werden, aber UI/Flash darf nicht
„Erfolg“ vortäuschen. Erneuter Buttonklick zeigt denselben Status.

### 11.4 Apply ohne inhaltliche Änderung

Wenn Apply nur klont (heutiger Bug) oder Ops no-op sind:

- Outcome `repair_no_change`
- keine Erfolgsmeldung „Repair angewendet“ ohne Diff
- Planversion optional nicht erhöhen, **oder** Version erhöhen aber Status
  `repair_no_change` + kein Current-Wechsel — **Bevorzugt:** keine neue Current
  Planversion bei No-Op (weniger Registry-Rauschen)

---

## 12. UI-Vertrag (V4)

### 12.1 Blocker-Darstellung

```text
Blocker E3:
Asset <id> wird in N Shots verwendet.
Erlaubt: ASSET_REUSE_MAX (Policy)

Vorschlag:
Shot K / Assignment A auf Asset B umstellen
Source-Range: 00:01:12–00:01:17 (falls Video)

Auswirkung:
- E3 verbessert (N → N-1 …)
- ggf. E4 für Assignment X entfällt

[ ] Vorschlag auswählen  → user_status=selected
```

Analog für E4 mit alternativer Range.

### 12.2 Buttons

| Aktion | Gate |
|---|---|
| Feasibility prüfen | Plan vorhanden; Loop-Hinweis bei identischer Signatur |
| Repair Proposals erzeugen | Plan + Feasibility fail; erzeugt nur blockerbezogene Ops |
| Proposal auswählen | Checkbox/Selector pro Proposal (heute fehlend) |
| Ausgewählte Reparaturen anwenden | mind. ein `selected` **und** Proposal adressiert offenen Blocker |

### 12.3 Endzustände (UI/Application Outcomes)

Bevorzugt als Application-Outcome / Meldung — **keine** neuen
`VISUAL_EDIT_ERROR_*`, solange bestehende Codes reichen:

| Outcome | Wann | Bestehender Anker |
|---|---|---|
| `repair_available` | ausführbare Proposals vorhanden | Proposal-Liste non-empty |
| `repair_selection_required` | Proposals da, keines selected | Apply disabled |
| `repair_applied` | Ops angewendet, Planinhalt geändert | heutige Erfolgsmeldung, geschärft |
| `repair_no_change` | identische Signatur / No-Op Apply | neu als Result-Flag/Message; Code `repair_conflict` oder dedizierte Message ohne neuen Error-Code falls möglich |
| `repair_not_possible` | keine Ops konstruierbar | Message + Next Action |
| `additional_coverage_required` | kein gültiges Ersatzasset | Next Action: weitere Assets analysieren |
| `manual_reassignment_required` | kein gültiger Source-Slot | Next Action: manuelle Neuzuordnung |

Falls ein eigener Error-Code nötig wird: in Implementierung gegen
`VISUAL_EDIT_ERROR_CODES` prüfen; nur bei Lücke erweitern (Domain, nicht Schema).

---

## 13. Planversionierung

Bereits vorhanden und beizubehalten:

- `plan_version` monoton pro Projekt
- Apply erzeugt neue `plan_id` + Version; alte Status `superseded`
- Humanity/Feasibility der alten Version → `stale`
- Current Pointer nur auf neue Version nach wirklichem Apply

Rework-Zusatz:

- Apply schreibt echte Assignment-Diffs in `RepairResult.changes`
- No-Op Apply ändert Current nicht (§11.4)

---

## 14. Fingerprints

| Artefakt | Heute | Rework |
|---|---|---|
| Visual Edit Plan `input_fingerprint` | aus Input-Kontext | unverändert als Input-Gate |
| Feasibility Report `input_fingerprint` | kopiert Plan-Input | zusätzlich Issue-Signatur in Metrics speichern |
| Repair Proposal | an Context-Fingerprint gebunden | Proposal-Ops an Issue-Signatur binden |
| Loop-Schutz | fehlt | Plan-Inhalt + Issue-Signatur (§11) |

Nach erfolgreichem Reassignment muss sich der **Plan-Inhalt** unterscheiden;
Input-Fingerprint kann gleich bleiben (gleiche Observations), daher ist
Inhaltssignatur für Loop-Schutz und „Fortschritt“ maßgeblich.

---

## 15. Schemaentscheidung

**`REGISTRY_SCHEMA_VERSION` bleibt 20.**

### 15.1 Bestehende Strukturen reichen für Kernfluss

Tabellen `visual_edit_plans`, `editorial_shots`, `shot_media_assignments`,
`feasibility_*`, `repair_proposals`, `repair_runs`, `repair_results` existieren
seit Schema 19 und sind unter 20 unverändert nutzbar.

### 15.2 Lücke: ausführbare Ops am Proposal

`RepairProposal` (`extra=forbid`) und Tabelle `repair_proposals` haben **keine**
Spalten für Zielasset / Zielrange / `addresses_issue_ids`.

**R1-Lösung ohne Schema-Bump (verbindlich bevorzugen):**

```text
repair_proposals-Zeile = Metadaten (type, affected_ids, description, …)
+ Sidecar-Artefakt unter _otio_v2/editing/repairs/
    repair_proposal_<proposal_id>.ops.json
```

Ops-JSON (Beispiel):

```json
{
  "proposal_id": "...",
  "operations": [
    {
      "op": "replace_assignment_asset",
      "assignment_id": "...",
      "shot_id": "...",
      "target_asset_id": "...",
      "target_working_media_id": "...",
      "target_observation_id": "...",
      "target_technical_shot_id": null,
      "target_source_in_seconds": null,
      "target_source_out_seconds": null,
      "addresses_issue_codes": ["E3"]
    }
  ],
  "issue_signature": "..."
}
```

Apply liest Sidecar; fehlt Sidecar → `repair_proposal_invalid`.

### 15.3 Schema-21-Pfad (nicht implementieren; nur dokumentiert)

Falls Sidecar später abgelehnt wird:

| Bedarf | Migration |
|---|---|
| Neue Spalte `operations_json TEXT` an `repair_proposals` | Schema 21 |
| Optional `addresses_issue_ids_json` | Schema 21 |
| Idempotenz | Migration nur ADD COLUMN IF NOT EXISTS; alte Rows `operations_json='[]'` |
| Rückwärtskompatibilität | Reader akzeptiert fehlende Ops als invalid proposal |
| Implementierungsschritt | **BLOCKED** bis Schema-21-Freigabe |

In R1: **kein** Schema-21; Sidecar-Pfad nutzen.

---

## 16. Fehlercodes

Vorhandene Codes weiterverwenden:

- `feasibility_blocking_issue` (E3/E4-Text in `technical_details`)
- `invalid_source_range` / `source_range_out_of_bounds`
- `invalid_asset_reference` / `invalid_observation_reference` / `invalid_working_media_reference`
- `repair_proposal_invalid` / `repair_conflict` / `repair_validation_failed`

Outcomes aus §12.3 sind Application-Status/Messages. Neue
`VISUAL_EDIT_ERROR_*` nur wenn zwingend — dann Domain-Tuple erweitern, Schema
unverändert.

---

## 17. Testmatrix

Neue Testdatei (Vorschlag):
`tests/test_discovery_v2_visual_edit_repair_rework.py`

Erweiterung bestehender Dateien wo sinnvoll:
`test_discovery_v2_visual_edit_plan.py`,
`test_discovery_v2_visual_edit_source_ranges.py`,
`test_discovery_v2_feasibility.py`,
`test_discovery_v2_visual_edit_repair.py`,
`test_discovery_v2_visual_edit_ui.py`.

### 17.1 Planer

| Node-ID (Vorschlag) | Assert |
|---|---|
| `test_planner_six_shots_six_assets_not_single_asset` | ≥2 distinct `asset_id` (ideal 6) |
| `test_planner_respects_e3_reuse_max_during_assign` | kein Asset > `ASSET_REUSE_MAX` |
| `test_planner_too_few_assets_explicit_blocker` | Blocker statt still invalid |
| `test_planner_deterministic_sort_stable` | zwei Läufe identisch |
| `test_planner_only_accepted_observations` | rejected/pending ausgeschlossen |
| `test_planner_only_completed_working_media` | Preview/Temp ausgeschlossen |

### 17.2 Source-Ranges

| Node-ID | Assert |
|---|---|
| `test_ranges_same_video_non_overlapping` | Overlap &lt; 0.90 |
| `test_ranges_identical_detected_as_e4` | Feasibility fail |
| `test_ranges_e4_considered_at_plan_time` | Resolve meidet Occupancy |
| `test_ranges_too_short_video_blocker` | klarer Blocker |
| `test_ranges_frame_rounding_stable` | frames konsistent |

### 17.3 Repair

| Node-ID | Assert |
|---|---|
| `test_repair_e3_emits_executable_reassignment` | Ops + Zielasset |
| `test_repair_e4_emits_executable_range_replace` | Ops + Zielrange |
| `test_repair_proposal_has_assignment_and_target` | Pflichtfelder |
| `test_repair_proposal_addresses_concrete_blocker` | Issue-Bezug |
| `test_repair_user_can_select_proposal` | `user_status=selected` |
| `test_repair_apply_creates_new_plan_version` | version+1, old superseded |
| `test_repair_apply_changes_plan_content_fingerprint` | Inhalt ≠ vorher |
| `test_repair_preserves_historical_plan` | alte Bundle lesbar |
| `test_repair_feasibility_uses_new_plan` | Report.plan_id = neu |

### 17.4 Loop-Schutz

| Node-ID | Assert |
|---|---|
| `test_loop_identical_repair_is_repair_no_change` | Outcome |
| `test_loop_identical_feasibility_not_progress` | UI/Service Flag |
| `test_loop_same_issue_signature_without_plan_change` | klare Blockade |
| `test_loop_ui_does_not_offer_endless_same_action` | Flash/Disable |

### 17.5 Keine Alternative

| Node-ID | Assert |
|---|---|
| `test_no_alt_asset_additional_coverage_required` | Outcome |
| `test_no_alt_range_manual_reassignment_required` | Outcome |
| `test_no_arbitrary_replacement_asset` | fremdes Asset verworfen |
| `test_no_preview_as_replacement` | Preview verworfen |

### 17.6 Isolation

| Node-ID | Assert |
|---|---|
| `test_schema_remains_20` | `REGISTRY_SCHEMA_VERSION == 20` |
| `test_no_classic_otio_write` | kein Write unter `_otio/` |
| Classic / Without-VO Suite | unveränderte Baseline-18-Fails; keine neuen |
| Fake-only | keine echten Provider |
| kein R1.4-Polling | keine Progress-Polling-Änderungen |
| UI-Render ohne Medien-I/O | bestehende UI-Verträge |

---

## 18. Smokes A–F (spätere Umsetzung)

| Smoke | Setup | Erwartung |
|---|---|---|
| **A Diversity** | 6 Shots, 6 geeignete Assets | mehrere Assets; E3 pass |
| **B Source Ranges** | 3 Shots absichtlich gleiches Video | 3 gültige Ranges, Overlap &lt; 0.90; E4 pass |
| **C E3-Reassignment** | Asset A ×6 | Feasibility E3 → Proposal ersetzt ≥2 Assignments durch B/C → neue Version → E3 pass |
| **D E4-Reassignment** | 3 überlappende Ranges | Proposal alternative Ranges → neue Version → E4 pass |
| **E keine Alternative** | 1 gültiges Asset | `additional_coverage_required`; keine Fake-Erfolgs-Schleife |
| **F Loop-Schutz** | identischer Plan + Issues + Repair ohne Änderung | `repair_no_change`; keine Erfolgs-Loop |

---

## 19. Implementierungsreihenfolge V1–V4

### V1 — Root Cause Fixtures & Vertragsprüfung

- Deterministische Mini-Fixtures für E3/E4-Konstellation
- Tests, die den **heutigen** Bug reproduzieren (rot) bzw. Verträge spezifizieren
- Issue-Signatur-Helfer + identische-Run-Erkennung (Service-seitig, noch ohne volle UI)
- **Exit:** reproduzierbarer Fail der Ein-Asset-/Overlap-Konstellation

### V2 — Planer-Härtung

- Fake Diversität + E3-aware Assignment
- E4-aware Source-Range + Occupancy-Registry
- Feasibility-Zuordnung E3/E4 zu Assignment/Shot schärfen
- **Exit:** Smoke A + B grün; Plan ohne Repair E3/E4-pass bei ausreichenden Assets

### V3 — Ausführbare Repairs

- Sidecar Ops-JSON (Schema 20)
- Fake Repair erzeugt blockerbezogene `replace_*` Proposals
- Apply führt Ops aus (nicht nur Clone)
- Validate-before-apply gegen E3/E4
- **Exit:** Smoke C + D + E grün

### V4 — Loop-Schutz und UI

- Selection-UI für Proposals
- `repair_no_change` / identische Signatur
- Blocker-Texte mit Policy-Wert
- Apply nur bei wirksamen selected Proposals
- **Exit:** Smoke F grün; manuelle Alpha-Schleife durchbrochen

**Regel:** Keine Stufe beginnen, bevor die vorherige getestet und grün ist.

---

## 20. Risiken und Einschränkungen

| Risiko | Mitigation |
|---|---|
| Fake-only Diversität ≠ echte LLM-Qualität | Python-Guards E3/E4 bleiben hart |
| Sidecar Ops vs. DB | klarer Pfad; Schema 21 BLOCKED |
| Redaktionelle Fehlzuordnung durch Auto-Alternative | Option 1; keine stille Fremdsubstitution |
| Baseline 18 Classic/Without-VO Fails | nicht „reparieren“; Isolationstests |
| Apply ohne Selection | UI Selection zwingend V4 |
| Humanity vs. E3 | getrennt halten |
| Zu aggressives Diversifizieren zerstört Intent | Ranking behält Intent-Match vor Reuse |

---

## 21. Deferred Items

- `swap_assignment_assets`, `shorten_assignment_source_range`, `split_assignment`
- Mindest-Gap zwischen Ranges (über Overlap hinaus)
- Schema-21 `operations_json` Spalte
- Echte Provider / LLM-Repair-Qualität
- R1.4 Progress-Polling
- Intake-Revision, Laienführung, Style References, Shared Working Media
- Automatische Re-Feasibility nach Apply (kann MANUAL bleiben)
- Option-2 deterministische technisch-äquivalente Reassignments

---

## 22. Abnahmekriterien dieses Plans

Der Plan ist abnahmefähig, weil er:

1. die Root Cause des Ein-Asset-Plans lokalisiert (`candidates[0]` × N),
2. E3 und E4 fachlich und codebezogen beschreibt,
3. Planerfehler und Repairfehler trennt,
4. konkrete Produktdateien und Services benennt,
5. einen ausführbaren Reassignment-Vertrag (Option 1 + Ops) definiert,
6. beliebige Assetersetzung verbietet,
7. Planversionierung und Fingerprints erhält bzw. schärft,
8. die identische Reparaturschleife adressiert (`repair_no_change`),
9. Schema 20 bewertet (Sidecar; Schema 21 nur BLOCKED-Fallback),
10. konkrete Tests und Smokes A–F enthält,
11. keine echten Provider oder Folgephasen aktiviert.

---

## 23. Nächste erlaubte Aktion nach Freigabe

→ **Visual Edit Rework V1** (Fixtures + Vertrags-/Repro-Tests)

Weiterhin gesperrt ohne eigenen Auftrag:

- V2–V4 vor V1-Abschluss
- R1.4 Progress-Polling
- echte Provider
- neue Produktphase
- Schema-21-Migration
- Classic / Without-VO Produktänderungen

---

## 24. Explizit nicht in diesem Auftrag

- Keine Produktcodedatei geändert
- Keine Testdatei geändert
- Keine Registry geändert
- Keine Reparatur implementiert
- Keine neue Produktphase begonnen
- `docs/DECISIONS.md` unverändert
