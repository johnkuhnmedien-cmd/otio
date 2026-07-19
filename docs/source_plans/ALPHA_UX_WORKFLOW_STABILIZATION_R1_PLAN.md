# Alpha UX Workflow Stabilization R1 — Implementierungsplan

**Auftrags-ID:** `DISCOVERY-V2-ALPHA-UX-WORKFLOW-STABILIZATION-R1-PLAN-001`
**Status:** PLANNING ONLY — keine Produktimplementierung in diesem Auftrag
**Basis-HEAD:** `1ac7fba2bf2c1a7f0ae783a81c82495e2c7c600e`
**Branch / PR:** `cursor/discovery-v2-integration` · `#69`
**Registry-Schema (Ausgang):** **20** (starke Präferenz: Schema bleibt 20)
**SoT-Rang:** `docs/source_plans/*` — nachrangig; überschreibt keine höheren Dokumente
**Releaseklasse:** interner MANUAL-/Fake-Alpha · Provider: Fake-only

---

## 0. Konfliktprüfung gegen höhere SoT

| Thema | Quelle | Plan-Konformität |
|---|---|---|
| MANUAL Alpha-Standard | ALPHA_SCOPE / 01-step | ja; geführte Buttons, keine stillen Gates |
| Fake-only; keine echten Provider | MODEL_ROUTING | ja; R1 Fake-only |
| Observation ≠ redaktionelle Freigabe | D-8D-004 / EDITORIAL_QUALITY | ja; Batch nur mit expliziter Nutzerbestätigung |
| Coverage vor Stock; Lock vor Voice | PIPELINE / MASTER | ja |
| `accepted_unresolved` nur mit sichtbarem Risiko + Bestätigung | PHASE10 E11 | ja; Fake-Partial-Coverage-Lücke schließen |
| Preview ≠ Working Media | MEDIA_LIFECYCLE | ja; Candidate-Accept ≠ Asset |
| Adobe OAuth / Lizenz UNKNOWN | D-10-008 | ja; keine Behauptung |
| Classic `_otio/` read-only; V2 nur `_otio_v2` | CLASSIC / 00-core | ja |
| UI → Application → Domain → Persistence | 00-core | ja; kein SQLite aus Streamlit |
| UI-No-I/O | 01-step / Phase-13 | ja; Polling nur Viewmodels |
| Keine neue Produktphase | PROGRESS / HANDOFF | ja; Alpha-Revision, kein Phase 14 |
| Style References / Shared WM | Auftrag §17–18 | deferred; nur Abgrenzung |

**Keine kritischen SoT-Konflikte.** Offene Punkte → §22 Deferred Items.

---

## 1. Ausgangslage

Discovery V2 Phasen 7–13 sind als interner MANUAL-/Fake-Alpha freigegeben
(`APPROVED` / Closeout `1ac7fba`). Die interne Alpha-Erprobung am realen
Medienprojekt ist am **Coverage-/Script-Lock-Gate** blockiert.

Zusätzlich blockieren UX-Lücken den Nichtentwickler-Pfad:

- Viewmodels bleiben nach Mutationen veraltet (Cmd+R nötig)
- Reload kann Projekt/Seite verlieren
- Jobs ohne sichtbares Polling
- Analyse-/Review-Mengen nicht bedienbar
- Media Intake ohne Speicher-Preflight und mit unübersichtlichen Ergebnislisten

Persönliche Registry-Pfade (z. B. Nutzer-`assets.sqlite3`) dienen nur optionaler
read-only Diagnose und sind **keine** Implementierungsabhängigkeit.
Umsetzung und Tests nutzen kleine deterministische Fixtures.

---

## 2. Bestätigte Reproduktionen

### R-A — Coverage-/Script-Lock-Blocker

| Feld | Wert |
|---|---|
| Coverage Level | `partially_covered` |
| Gap Status | `in_progress` |
| Escalation | `user_decision` |
| `missing_properties` | `["exact_match_not_verified"]` |
| `risk_flags` | `[]` |
| Stock Candidates | 3 Fake, Decisions alle `rejected` |
| UI | Button „Risiko unaufgelöst akzeptieren“ **disabled** |
| Folge | Gap nicht terminal → Script Lock blockiert → `lock_fingerprint=None` |

### R-B — `editorial_registry_write_failed`

Mehrere Coverage-Runs enden mit `editorial_registry_write_failed`
(Catch-all im Editorial-Worker bei Nicht-Gateway-Exceptions).

### R-C — Fingerprint-UX

Lock-UI verlangt freie Texteingabe des Fingerprints, ohne den aktuellen
Preview-Wert als bestätigbaren Stand anzuzeigen.

### R-D — Stale Viewmodel

Erfolgreiche Editorial-/Review-Aktionen speichern, lassen den nächsten Button
aber deaktiviert, bis manueller Reload.

### R-E — Reload / Routing

Browser-Reload kann auf „Neues Projekt“ / falschen Modus führen; Project-ID
nur in `st.session_state`.

### R-F — Job-Fortschritt

Intake/Prepare/Analyse-Fortschritt nur nach manuellem Reload; kein Polling.

### R-G — Analyse-Limits

Multiselect default = alle Assets → `analysis_frame_limit_exceeded`;
kein assetweiser Queue-Modus.

### R-H — Observation Review / Claims

Einzelbestätigung unbrauchbar bei großen Mengen; Claim-UI zeigt nur Modellstatus.

### R-I — Intake Speicher / Listen

`OSError: [Errno 28] No space left on device` erst nach vielen Kopien;
Ergebnislisten nicht kollabiert.

---

## 3. Scope und Ausschlüsse

### In Scope (R1 nach Freigabe, gestaffelt R1.1–R1.6)

- Coverage Gap `accepted_unresolved` für Fake-Partial-Coverage-Pfad
- Härtung Coverage-Registry-Writes / Fehlerbericht / Wiederanlauf
- Script-Lock-Gate-Texte + Fingerprint-Bestätigungs-UX
- Post-Mutation-Refresh + Routing-/Deep-Link-Vertrag
- Analyse-Queue (Fake, 1 Asset gleichzeitig)
- Batch Observation Review + Claim-Statusanzeige
- Automatische Re-Coverage nach gültigem Supplement-/Import-Pfad (ohne Review-Gates zu entfernen)
- gemeinsame Progress-/Polling-Komponente
- Intake Speicher-Preflight + eingeklappte Ergebnislisten
- Button-Erklärungen, Zieldauer-Presets, geführte Hauptaktionen

### Explizit nicht in R1

- echte Vision-/Text-/Stock-/Voice-Provider
- Adobe OAuth / Lizenz / Auto-Download
- Style References (Folgeentscheidung)
- Projektfamilien / Shared Working Media (Folgeentscheidung)
- proprietäre NLE-Exporte / Publishing / Cloud / Multi-User
- AUTOMATIC-Orchestrierung
- Schema-21-Implementierung in diesem Planungsauftrag
- manuelle Registry-Reparatur am Nutzerprojekt
- Classic / Without-VO Fachänderungen
- neue Produktphase (kein Phase 14)

---

## 4. Root-Cause-Matrix

| Befund | Root Cause / Untersuchungsaufgabe | Primäre Dateien | Änderung | Tests |
|---|---|---|---|---|
| R-A Accept-Button disabled | UI: `disabled=not gap.risk_flags`; Fake Coverage setzt nur `missing_properties=["exact_match_not_verified"]`, `_split_coverage(PARTIALLY_COVERED)→risk_flags=[]` | `ui/editorial_page.py`, `adapters/text_fake.py`, `application/coverage_gap_service.py`, `domain/supplementation.py` | Domain: sichtbares Risiko aus Partial-Coverage materialisieren; UI: Accept bei `user_decision` + terminaler Candidate-Lage ermöglichen | neue Script-Lock-/Gap-Tests + Smoke A |
| R-A Gap bleibt `in_progress` | `user_decision` Escalation ≠ terminal; Rejected Candidates terminieren Gap nicht | `coverage_gap_service.escalate_gap`, `record_candidate_decision` | `accept_gap_unresolved` als terminaler Pfad; danach Lock-Preview neu | Smoke A |
| R-A Fingerprint leer | `_build_preview` setzt Fingerprint nur ohne Blocker; `coverage_gap_open:*` | `script_lock_service._build_preview` | nach terminalem Gap erneut berechnen (on-demand bleibt) | Smoke A |
| R-B `editorial_registry_write_failed` | Catch-all in `jobs/editorial_worker.py` maskiert FK/UNIQUE/FS/State-Fehler in `_process_coverage` / `insert_coverage_audit` | `editorial_worker.py`, `editorial_repository.py`, `editorial_service.py` | RCA in R1.1: Transaktion atomar; Current-ID nur nach erfolgreichem Insert; typisierte Fehler; kein Partial Current | Registry-Write-Tests |
| R-C Fingerprint-Eingabe | `st.text_input("Zu bestaetigender Lock-Fingerprint")` ohne Preview-Wert | `ui/editorial_page.py`, `script_lock_service.preview_script_lock` | Checkbox bestätigt serverseitigen Preview-Fingerprint; kein Freitext | Lock-UX-Tests + Mismatch |
| R-D Stale VM | Editorial/Analysis/Narration/Export UI ohne `st.rerun` nach Mutation | `editorial_page.py`, `asset_analysis_page.py`, … | kontrollierter Rerun + Flash-Message; View neu laden | Smoke C |
| R-E Reload | Project nur `session_state["active_project_id"]`; Mode-Fallback `WITH_VOICEOVER` | `ui/navigation.py`, `ui/routing.py`, `discovery_v2/ui/overview.py` | Query-Param-/Route-Vertrag | Smoke D |
| R-F Progress | kein `st.fragment`/Polling; Caption + manueller Reload | alle Job-UIs | gemeinsame Progress-Komponente 2–5s | Smoke E |
| R-G Frame-Limit | Multiselect default=all; Limits 24/96 Frames | `vision_config.py`, `model_analysis_service.py`, `asset_analysis_page.py` | assetweise Queue; Fake: 1 Asset parallel | Smoke F |
| R-H Reviews | nur Einzel-Review; Claim-Decisions nicht gerendert | `observation_review` UI/Service, `editorial_page` Claims | Batch + Statusspalten | Batch-Tests |
| R-I Intake | Copy/Remux ohne Disk-Preflight; flache Result-Listen | `media_intake_page.py`, copy/remux services | Preflight + Summary/Expander | Smoke G/H |

---

## 5. R1.1 — Blocker (erste erlaubte Implementierung nach Freigabe)

### 5.1 Coverage Gap `accepted_unresolved` für Partial Coverage

**Verbindliches Ziel**

```text
user_decision
+ keine offenen Candidate-Pfade (alle rejected / keine pending)
+ sichtbares bestätigbares Risiko
+ ausdrückliche Nutzerbestätigung
→ accepted_unresolved
→ Gap terminal
→ Script-Lock-Gate erneut berechnen (preview)
```

**Domainentscheidung (Schema 20, keine neue Enum-Spalte nötig)**

1. Bei Materialisierung / Eskalation auf `user_decision`:
   wenn `coverage_level == partially_covered` **und** `risk_flags` leer **und**
   `missing_properties` nicht leer (mind. `exact_match_not_verified`):
   → `risk_flags` um `CoverageRiskFlag.USER_DECISION_REQUIRED` ergänzen
   (oder äquivalent: dediziertes sichtbares Risiko aus Missing-Property ableiten,
   weiterhin als `CoverageRiskFlag`).
2. `missing_properties` bleiben erhalten (Diagnose).
3. UI-Button „Risiko unaufgelöst akzeptieren“:
   enabled wenn Gap an Escalation `user_decision` **und**
   (`risk_flags` nicht leer) **und** keine nicht-terminalen Candidate-Pfade offen.
4. `accept_gap_unresolved(confirmed_risks=…)` unverändert append-only
   (`coverage_gap_events`, Status `accepted_unresolved`).
5. Kein Registry-SQL aus der UI.
6. Nach Erfolg: View neu laden + `preview_script_lock` zeigt aktualisierte Blocker/Fingerprint.

**Dateien**

- `otio_app/discovery_v2/domain/supplementation.py` (Regeln/Hilfen)
- `otio_app/discovery_v2/application/coverage_gap_service.py` (`_split_coverage` / escalate / accept)
- `otio_app/discovery_v2/adapters/text_fake.py` (konsistente Fake-Emission; optional Risk mitsenden)
- `otio_app/discovery_v2/ui/editorial_page.py` (Button-Enablement + Copy)
- `otio_app/discovery_v2/application/script_lock_service.py` (Gate-Texte)

### 5.2 `editorial_registry_write_failed` — RCA + Härtung

**Verbindliche RCA-Checkliste (Implementierungsauftrag)**

| Thema | Prüfen |
|---|---|
| Transaktionsgrenzen | `BEGIN IMMEDIATE` um Audit+Results+Current-State |
| Foreign Keys | `coverage_intent_results.visual_intent_id`, Script/Plan-IDs |
| Current-State | `active_coverage_audit_id` erst nach erfolgreichem Insert |
| Artefaktpublikation | JSON unter `_otio_v2` vor/after Commit-Reihenfolge |
| Wiederholung | Idempotenz / keine Doppel-Audits bei Retry |
| Parallelität | UI Doppelklick / überlappende Runs |
| Supplementation-Konflikt | Gap-Materialisierung vs. neuer Audit |
| Recovery | nach Disk-full / Worker-Interrupt: Run `failed`, Current bleibt gültig |

**Ziel**

- kein teilweise persistierter Coverage-Zustand
- keine verwaisten Current-IDs
- gültiger vorheriger Audit bleibt bei Schreibfehler Current
- verständlicher Fehlerbericht (Underlying Cause + `editorial_registry_write_failed` als Wrapper nur wenn nötig)
- expliziter Wiederanlauf ohne Duplikate

**Dateien:** `jobs/editorial_worker.py`, `persistence/editorial_repository.py`,
`application/editorial_service.py`

### 5.3 Script-Lock-Gate verständlich

`preview_script_lock` liefert strukturierte Blocker; UI rendert Checklist:

```text
Script Lock noch nicht möglich:
✓ aktuelles Script
✓ Struktur aktuell
✓ Claims entschieden
✗ 2 Coverage Gaps noch offen
✗ 1 Coverage-Run fehlgeschlagen
```

Mapping aus bestehenden Blocker-Tokens (`coverage_gap_open:*`,
`claim_decision_required:*`, `observation_fingerprint_stale`, …).

### 5.4 Fingerprint-UX (Sicherheit erhalten)

**Ersetzen**

```text
[Freitext Fingerprint]
```

**Durch**

```text
Aktueller Lock-Stand
Fingerprint: <erste 12 Zeichen>   [Details anzeigen → voller Hash]
☐ Ich bestätige genau diesen aktuellen Stand.
[Skript für Voice und Timing sperren]
```

**Vertrag**

- `preview_script_lock()` liefert den serverseitigen Fingerprint (nur wenn Gate ok)
- Checkbox bestätigt genau diesen Wert → `create_script_lock(..., confirmed_fingerprint=preview.lock_fingerprint)`
- Mutation zwischen Render und Klick → `script_lock_fingerprint_mismatch`
- kein frei editierbares Feld
- Fingerprint technisch vollständig erhalten
- Risiko-Checkboxes für `accepted_unresolved` bleiben (E11)

**Dateien:** `ui/editorial_page.py`, ggf. kleines Viewmodel-Feld in
`script_lock_service.ScriptLockPreview`

---

## 6. R1.2 — State und Routing

### 6.1 Post-Mutation-Refresh

**Vertrag**

```text
Aktion erfolgreich
→ Application Result
→ Flash-Message in session_state (ein Folgerender)
→ st.rerun()
→ Viewmodel frisch laden
→ nächster Schritt sichtbar
```

**Verbindlich**

- kein Jobneustart durch Rerun
- keine doppelte Mutation (Buttons nur auf Click)
- keine Gatewayaufrufe allein durch Rendering
- Erfolgsmeldung ≥ 1 Folgerender sichtbar

**Betroffen:** Brief, Narrative, Hook, Script, Struktur, Coverage, Claims,
Gap-Eskalation, Candidate Decision, Observation Review, (analog Narration/
Visual/Export wo sinnvoll).

**Muster:** Helper `discovery_ui_flash_and_rerun(message)` in
`otio_app/discovery_v2/ui/` (nur UI-Orchestrierung, keine Fachlogik).

### 6.2 Routing- / Reload-Vertrag

| Zustand | Persistenz |
|---|---|
| Project-ID | URL query `project_id` **und** session_state (URL gewinnt bei Reload) |
| Project Mode | aus persistiertem Project-Record (nicht geraten) |
| Discovery-Seite | Streamlit `st.Page` / `url_path` (bereits vorhanden) |

**Ziel**

```text
Reload auf /discovery-editorial?project_id=<uuid>
→ gleiches Projekt
→ gleiche Seite
→ aktueller persistierter Registry-Zustand
```

**Regeln**

- keine fachliche Wahrheit ausschließlich in `st.session_state`
- unbekannte/ungültige Project-ID → verständliche Projektauswahl, kein stiller Mode-Wechsel auf Classic ohne Hinweis
- Deep Link ohne ID → Projektauswahl auf derselben Seite
- Session-Neustart: URL reicht zur Wiederherstellung

**Dateien:** `otio_app/ui/routing.py`, `otio_app/ui/navigation.py`,
`otio_app/discovery_v2/ui/overview.py` (+ Seiten-Selector)

---

## 7. R1.3 — Review und Analyse

### 7.1 Analyse-Queue und Frame-Vertrag

**Ablauf**

```text
Asset
→ alle persistierten Representative Frames des Assets
→ assetgebundene Analyseeinheit
→ Limits prüfen
→ Fake Vision
→ Observation persistieren
→ nächstes Asset
```

**Fake-Alpha-Limits (R1)**

| Limit | Wert | Verhalten bei Überschreitung |
|---|---|---|
| Assets gleichzeitig | **1** | Queue sequenziell |
| Frames pro Asset | bestehend `MAX_FRAMES_PER_VIDEO` (24) | Asset überspringen/fail mit klarem Code; kein stilles Truncaten ohne Report |
| Bytes pro Frame / Run | `MAX_FRAME_BYTES` / `MAX_RUN_BYTES` | wie heute, UI erklärt |
| Assets pro Run | UI-Queue; Default = alle vorbereiteten, aber sequentiell | kein All-Frames-Burst |
| Frames pro Run | Summe über Queue; bei Limit: Batch in mehreren Runs oder Stop mit Fortschritt | dokumentieren in Implementierung; Prefer: mehrere Runs automatisch |

**Teilbatching innerhalb eines Assets:** nur wenn Frames/Bytes eines Assets die
Limits sprengen — dann geordnete Teilfenster + **eine** zusammengeführte
Observation pro Asset (oder fail-closed mit `analysis_frame_limit_exceeded`
und UI-Hinweis). Bevorzugt fail-closed mit Erklärung, wenn Fake-Adapter keine
Merge-Semantik hat; Merge nur wenn Domainvertrag existiert.

**Idempotenz:** Wiederanlauf reused Cache; keine doppelte aktuelle Observation
für dieselbe Analysis-Identity.

**UI**

```text
Asset A: 4 Representative Frames
Asset B: 1 Representative Frame
…
[Vorbereitete Assets analysieren]
```

Hauptbutton Assetanalyse (geführt): **Analyse vorbereiten und ausführen**
→ Prepare → Queue → Fake Analysis; Stopp bei Observation Review.

**Keine echte Visionintegration.**

### 7.2 Batch Observation Review

- Mehrfachauswahl, „Alle sichtbaren auswählen“
- Filter: unreviewed / geringe Sicherheit / geografisches Risiko /
  möglich synthetisch / technische Warnung
- Batch: akzeptieren / ablehnen / neu analysieren
- explizite Bestätigung mit Anzahl
- Fortschritt `geprüft / gesamt`
- Einzelansicht für riskante Fälle
- append-only Review Decisions
- **kein** stilles Auto-Accept durch das Modell

### 7.3 Claim Decisions — Anzeige

Trennung in UI:

| Spalte | Quelle |
|---|---|
| Modellstatus | Claim aus Script/Coverage |
| Nutzerentscheidung | letzte `claim_decisions` |
| Aktuell entschieden | ja/nein |

Zusätzlich: Batch für unkritische Claims; Einzelentscheidung für Konflikte;
Idempotenz gegen Doppelklick (gleiche Decision nicht doppelt sichtbar als „neu“,
technisch weiterhin append-only mit Revision ok, UI zeigt nur Current).

### 7.4 Supplementation-Revalidation

**Gewünschter Ablauf (technisch automatisierbar nach Nutzerentscheidungen)**

```text
Gap → lokal/supplement → Preview/Dublette
→ Nutzer: „Kandidat für Beschaffung vormerken“ (Rename von „Für Import akzeptieren“)
→ manuelles Original verfügbar
→ Media Intake → Working Media → Prepare → Fake Vision
→ Observation Review (Batch ok)
→ Coverage automatisch erneut
→ Gap bei Match → resolved_with_supplement
```

**UI-Klartext bei Candidate-Accept**

- noch keine Mediendatei
- noch kein Working Media
- noch keine Lizenz bestätigt

**Nicht zulässig:** Modell ohne Review als editorial-ready; Fake-Kandidat als Asset;
Lizenz/OAuth behaupten; Preview als Working Media.

---

## 8. R1.4 — Job-UX (Progress / Polling)

### Gemeinsame Komponente

`otio_app/discovery_v2/ui/components/run_progress.py` (Name frei, Ort unter UI)

**Status:** `queued` | `running` | `completed` | `completed_with_errors` |
`failed` | `interrupted`

**Anzeige:** Fortschrittsbalken, bearbeitet/gesamt, erfolgreich, reused,
übersprungen, fehlgeschlagen, aktuelles Asset/Schritt, Laufzeit, grobe ETA
(nur wenn Samples belastbar), Run-ID, Fehlercode.

**Polling:** alle **2–5 Sekunden** (Streamlit fragment / controlled rerun)

**Vertrag**

- liest ausschließlich Application-Run-Viewmodels
- kein Medien-I/O, Gateway, Jobstart, kein direkter SQLite-Zugriff aus Streamlit
- stoppt bei terminalem Status
- Seitenwechsel stoppt nicht den Worker

**Mindestens verdrahten:** technische Prüfung, Copy/Remux/Transcode/Image Intake,
Analysevorbereitung, Modellanalyse, Narration, Visual Edit, Export.

---

## 9. R1.5 — Intake-UX

### 9.1 Speicher-Preflight

Vor Copy/Remux/Transcode:

| Kennzahl | Regel |
|---|---|
| Quellgröße | Summe selektierter Quellen |
| geschätzte Working-Media-Größe | Copy≈Quelle; Remux≈Quelle; Transcode konservativ × Faktor (Vorschlag **1.3**, konfigurierbar im Service) |
| Temp-Spitze | max(Quelle, Ziel) + Reserve |
| freier Speicher | `shutil.disk_usage` auf Zielvolume |
| Sicherheitsreserve | Vorschlag **2 GiB** oder 10% der Schätzung (größerer Wert) |
| unbekanntes Ziel | konservative Obergrenze + Blocker „Schätzung unsicher“ |

Startblocker bei klar unzureichendem Speicher.

**Fehlercode:** `insufficient_working_media_storage`
(bestehendes `insufficient_disk_space` wo bereits genutzt — angleichen/aliasen,
keine Doppelbedeutung ohne Mapping).

Erneute Prüfung unmittelbar vor Runstart im Application/Worker.
**Keine** automatische Löschung von Originalen oder Working Media.

### 9.2 Ergebnislisten einklappen

Summary immer sichtbar; darunter Expander:

- Fehler/Warnungen (**expanded** wenn count>0)
- Neu / Reused / Übersprungen / Technische Details (**collapsed**)

Filter + „erste 20 + nachladen“; lange Pfade nicht im Hauptbereich;
JSON/CSV-Exportbericht bleibt.

---

## 10. R1.6 — Laienführung

### 10.1 Geführte Hauptaktionen

| Seite | Hauptbutton | Automatisch | Stopp / manuell |
|---|---|---|---|
| Assetanalyse | Analyse vorbereiten und ausführen | Prepare → Queue → Fake Analysis | Observation Review |
| Editorial | Editorial vorbereiten | Narrative → (Hook stop) → Script → Struktur → Coverage | Hook, Claim-Konflikte, Gap-Risiken, Lock |
| Narration | Narration erzeugen | Fake Voice → Pause → Timing | menschliche Gates / Lock |

**Weiterhin zwingend manuell:** Hook, Observation-Risiken, Claim-Konflikte,
unaufgelöstes Coverage-Risiko, Script Lock, Humanity-Blocker, finale Approval.

### 10.2 Button-Erklärungen

Jeder wichtige Button: Was / Daten / extern? / rückgängig? / danach möglich?
Tooltips für: Working Media, Technical Shot, Representative Frame, Coverage,
Gap, Candidate, Observation, Claim, Script Lock, Fingerprint, Feasibility, Reparse.

### 10.3 Zieldauer

Presets: 5 / 10 / 15 / 20 Min + Benutzerdefiniert; intern Sekunden.
Anzeige: `600 Sekunden = 10 Minuten`. Domainfeld bleibt
`desired_duration_seconds`. Optional spätere Verschiebung in Projekteinstellungen
— R1: verständliche Brief-UI reicht.

---

## 11. Architektur

```text
UI (Streamlit, No-I/O)
  → Application Services / Viewmodels
    → Domain
      → Adapters (Fake-only)
      → Persistence (Registry unter _otio_v2)
```

- keine Fachlogik in Streamlit
- keine direkten SQLite-Abfragen aus Streamlit
- keine schweren Jobs in normalen Reruns
- Polling nur Viewmodels
- externe Dienste nur Adapter; Fake-only; kein stiller Fallback
- keine `_otio`-Writes; Classic/Without-VO unverändert
- keine neuen Pakete ohne Freigabe

---

## 12. Schemaentscheidung

**Entscheidung: Schema bleibt 20**, sofern Implementierung bestätigt:

| Bedarf | Schema-20-Lösung |
|---|---|
| Partial-Coverage-Risiko | `risk_flags` JSON / bestehende Gap-Spalten |
| Lock-Preview UX | Viewmodel only |
| Routing | URL/session, keine DB |
| Progress | bestehende Run-Tabellen + Viewmodels |
| Batch Reviews | bestehende `visual_observation_reviews` / `claim_decisions` append-only |
| Claim-Status UI | Join letzte Decision — Viewmodel |
| Speicher-Preflight | Application calculation |
| Intake-Summary | Viewmodel over run items |

**Schema 21 nur vorschlagen**, wenn zwingend z. B.:

- persistente Analyse-Queue-Jobs mit eigener Tabelle nötig und Runs nicht reichen
- durable Flash/UI-state fälschlich in DB modelliert werden müsste (sollte nicht)

Falls Schema 21: exakte DDL, Migration 20→21 idempotent, Begründung —
**nicht** in diesem Planungsauftrag implementieren.

---

## 13. Fehlercodes (bestehend + geplant)

| Code | Nutzung |
|---|---|
| `editorial_registry_write_failed` | Wrapper; Underlying Cause loggen/anzeigen |
| `script_lock_requirements_not_met` | Gate nicht erfüllt |
| `script_lock_confirmation_required` | Checkbox fehlt |
| `script_lock_fingerprint_mismatch` | Stand mutiert / falsche Bestätigung |
| `script_lock_invalidated` | Stale Lock |
| `analysis_frame_limit_exceeded` | Queue/Limits — UI erklärt Maximum |
| `insufficient_working_media_storage` | **neu oder Alias** Preflight |
| `insufficient_disk_space` | bestehend Prepare/Transcode/Image — angleichen |
| Supplementation/Claim-Codes | unverändert nutzen |

Keine Secrets in Fehlermeldungen.

---

## 14. UI-Verträge (Kurz)

1. **Mutation → Rerun → frisches Viewmodel**
2. **Reload → project_id + page stabil**
3. **Polling → read-only Viewmodels, 2–5s, stop terminal**
4. **Lock → sichtbarer Fingerprint + Checkbox, kein Freitext**
5. **Gap Accept → nur Application Service, append-only**
6. **Batch Review → explizite Bestätigung mit Anzahl**
7. **Candidate Accept → Beschaffungsvormerkung, kein Asset**
8. **No-I/O beim Render** (bestehende Tests erweitern)

---

## 15. Testmatrix (Vorschlag Node-IDs / Dateien)

Neue/erweiterte Testdateien (Namen vorschlagend):

- `tests/test_discovery_v2_alpha_r1_coverage_lock.py`
- `tests/test_discovery_v2_alpha_r1_state_routing.py`
- `tests/test_discovery_v2_alpha_r1_progress.py`
- `tests/test_discovery_v2_alpha_r1_analysis_queue.py`
- `tests/test_discovery_v2_alpha_r1_batch_review.py`
- `tests/test_discovery_v2_alpha_r1_intake_ux.py`
- Erweiterung bestehender: `test_discovery_v2_script_lock.py`,
  `test_discovery_v2_coverage_gaps.py`, `test_discovery_v2_editorial_ui.py`,
  `test_discovery_v2_observation_review.py`, `test_discovery_v2_copy_intake.py`

| Regression | Vorgeschlagene Node-ID |
|---|---|
| Gap Accept möglich bei exact_match path | `::test_r1_user_decision_exact_match_allows_accept_unresolved` |
| Accept → terminal + lock preview | `::test_r1_accept_unresolved_makes_gap_terminal_and_exposes_fingerprint` |
| Coverage write atomar | `::test_r1_coverage_registry_write_is_atomic_on_failure` |
| Lock-Gate Texte | `::test_r1_lock_gate_lists_missing_requirements` |
| Fingerprint Checkbox | `::test_r1_lock_uses_server_fingerprint_without_text_input` |
| Fingerprint Mismatch | `::test_r1_lock_fingerprint_mismatch_on_stale_preview` |
| Mutation Rerun | `::test_r1_brief_save_refreshes_viewmodel` |
| Rerun kein Jobstart | `::test_r1_rerun_does_not_restart_jobs` |
| Reload Projekt/Route | `::test_r1_reload_keeps_project_id_and_route` |
| Deep Link | `::test_r1_deep_link_selects_project_or_prompts` |
| Polling read-only | `::test_r1_progress_polling_reads_viewmodel_only` |
| Polling stop terminal | `::test_r1_progress_polling_stops_when_terminal` |
| Assetweise Frames | `::test_r1_analysis_keeps_frames_per_asset_together` |
| Sequentiell | `::test_r1_analysis_processes_assets_sequentially` |
| Limits | `::test_r1_analysis_respects_run_limits` |
| Keine Doppel-Observation | `::test_r1_analysis_restart_is_idempotent` |
| UI Frames/Asset | `::test_r1_analysis_ui_lists_frames_per_asset` |
| Batch Observation | `::test_r1_batch_observation_review_append_only` |
| Claim Status UI | `::test_r1_claim_decision_shows_user_status` |
| Speicher Block | `::test_r1_intake_blocks_when_storage_insufficient` |
| Speicher OK | `::test_r1_intake_allows_start_when_storage_sufficient` |
| Reuse WM | `::test_r1_intake_restart_reuses_working_media` |
| Listen Collapse | `::test_r1_intake_results_collapse_successes` |
| UI-No-I/O Doppelrender | bestehende + `::test_r1_*_ui_double_render_no_io` |
| Isolation | `_otio`-Write-Negativtests / keine Provider |

Baseline 18 Failures / 1 VFR-Skip bleiben unberührt.

---

## 16. Smokes A–H (spätere Umsetzung)

| Smoke | Kurzpfad |
|---|---|
| **A** Coverage-Blocker | `partially_covered` → `user_decision` → Candidates rejected → Risiko bestätigen → `accepted_unresolved` → Fingerprint sichtbar → Script Lock ok |
| **B** Supplementation-Revalidation | Gap → Fake Candidate → manueller Original-Fixture → Intake → WM → Prepare → Fake Vision → Batch Review → Re-Coverage → `resolved_with_supplement` |
| **C** State Refresh | Brief speichern → Narrative aktiv; Hook → Script aktiv; Claim → Nutzerstatus sichtbar; kein manueller Reload |
| **D** Routing | `/discovery-editorial` Reload → gleiches Projekt + Seite |
| **E** Progress | Prepare running → Fortschritt aktualisiert → kein Jobneustart |
| **F** Analyse-Queue | 3 Assets + Frames → sequentiell → 1 Observation/Asset |
| **G** Speicher-Preflight | zu wenig Speicher → kein Start + Bedarfstext |
| **H** Intake-Ergebnisse / UI | Copy done → Summary; Erfolge collapsed; Fehler priorisiert; Doppelrender No-I/O |

---

## 17. Implementierungsreihenfolge

1. **R1.1 Blocker** — Coverage Accept, Registry-Write, Lock-Gate, Fingerprint-UX
2. **R1.2 State/Routing** — Rerun, Viewmodels, Deep Link
3. **R1.3 Review/Analyse** — Queue, Batch Observation, Claim-Status, Re-Coverage
4. **R1.4 Job-UX** — Progress + Polling
5. **R1.5 Intake-UX** — Preflight + Collapse
6. **R1.6 Laienführung** — Erklärungen, Begriffe, Zieldauer, geführte Buttons

Nach Planfreigabe ist **nur R1.1** der nächste erlaubte Implementierungsauftrag,
bis R1.2–R1.6 einzeln freigegeben werden.

---

## 18. Migrations- / Kompatibilitätsrisiken

| Risiko | Mitigation |
|---|---|
| Bestehende Gaps ohne `risk_flags` | Lazy-Normalisierung beim Lesen/Eskalation oder einmaliger Application-Repair-Pfad (kein Nutzer-SQL) |
| Lock-Fingerprint-Änderung | Fingerprint-Input ändert sich nur wenn Gate-Inputs sich ändern; UX ändert Bestätigungsmechanismus, nicht Hash-Formel ohne Decision |
| Query-Param Routing | Fallback auf session_state; Classic-Routen unberührt |
| Polling Last | nur Viewmodel-Reads; Intervall 2–5s; stop terminal |
| Baseline 18 | nicht anfassen |
| Nutzerregistry Disk-full | Preflight; keine Auto-Deletes |

---

## 19. Abnahmekriterien dieses Plans

- [x] Jeder bestätigte Befund → Root Cause / Untersuchungsaufgabe
- [x] Coverage-/Lock-Blocker reproduzierbar beschrieben
- [x] Keine manuellen Registry-Eingriffe
- [x] Fingerprint-Sicherheit erhalten
- [x] Konkrete Dateien/Services genannt
- [x] UI/Application/Domain/Persistence getrennt
- [x] Routingvertrag
- [x] No-I/O-Pollingvertrag
- [x] Batch Reviews mit ausdrücklicher Bestätigung
- [x] Analyse assetweise / limitkonform
- [x] Speicher-Preflight + Intake-Summary
- [x] Schemaauswirkung bewertet (20 präferiert)
- [x] Tests + Smokes A–H
- [x] Keine echten Provider / keine neue Produktphase

---

## 20. Deferred Items (nicht R1-Implementierung)

### D1 — Style References (Folgeentscheidung)

Mögliche Felder: Tonalität, Beispieltext, Satzlänge, Verbote, Dramaturgie,
Referenzskript als Text. Vorher klären: Persistenz, Versionierung, Fingerprints,
Datenschutz, Prompt-Injection, Einfluss auf Script Lock.
**R1:** höchstens Erweiterungspunkt „Brief/Style-Profile später“ — keine Felder.

### D2 — Projektfamilien / Shared Working Media (Folgeentscheidung)

Gleiche Medienwurzel + Sprachvarianten → gemeinsame Registry/WM-Identitäten,
getrennte Editorial/Narration/Export. Prüfen: Familien-ID, keine Cross-Delete,
keine stillen Pfade.
**R1:** keine Architekturänderung.

### D3 — Echte Provider / Adobe / NLE / AUTOMATIC

Weiterhin gesperrt.

---

## 21. Referenz — untersuchte Produktbereiche

| Bereich | Einstieg |
|---|---|
| Routing / Mode | `otio_app/ui/routing.py`, `navigation.py` |
| Editorial UI | `discovery_v2/ui/editorial_page.py` |
| Analysis UI | `discovery_v2/ui/asset_analysis_page.py` |
| Intake UI | `discovery_v2/ui/media_intake_page.py` |
| Coverage Gaps | `application/coverage_gap_service.py` |
| Script Lock | `application/script_lock_service.py` |
| Editorial Worker | `jobs/editorial_worker.py` |
| Fake Coverage | `adapters/text_fake.py` |
| Domain Risks | `domain/supplementation.py` |
| Vision Limits | `adapters/vision_config.py` |
| Observation Review | Observation-Review Application/UI |
| Bestehende No-I/O-Tests | `tests/test_discovery_v2_*_ui.py`, Observation/Analysis UI tests |

---

*Ende Plan — PLANNING ONLY.*
