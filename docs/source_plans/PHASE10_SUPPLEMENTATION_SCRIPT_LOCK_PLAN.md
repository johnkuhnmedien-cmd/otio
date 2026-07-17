# Phase 10 — Supplementation und Script Lock Plan

**Auftrags-ID:** `DISCOVERY-V2-PHASE10-SUPPLEMENTATION-SCRIPT-LOCK-PLAN-001`
**Status:** PLANNING ONLY — keine Produktimplementierung
**Basis-HEAD:** `b68c8028f032d47b0071d77dbc3a4879ee29ebf1`
**Registry-Ausgang:** Schema **15**
**SoT-Rang:** `docs/source_plans/*` — nachrangig; überschreibt keine höheren Dokumente

---

## 0. Konfliktprüfung gegen höhere SoT und Phase 9

| Thema | Quelle | Plan-Konformität |
|---|---|---|
| Coverage vor Stock; Lock vor Voice | MASTER_PLAN / PIPELINE_SPEC | ja |
| Eskalationsreihenfolge exakt | PIPELINE_SPEC / MEDIA_LIFECYCLE | ja |
| Adobe-Kette; OAuth UNKNOWN | PIPELINE / MEDIA / MODEL_ROUTING | ja |
| Preview ≠ Working Media / OTIO | MEDIA_LIFECYCLE | ja |
| Phase 9 endet nach Coverage | D-9-002 | ja; Phase 10 startet danach |
| Fake zuerst; keine echten Provider | MODEL_ROUTING / CLASSIC | ja |
| `_otio_v2` only; Classic read-only | 00-core / CLASSIC | ja |
| MANUAL; kein Auto-Lock/Rerun | ALPHA_SCOPE / 01-step | ja |
| Observation ≠ Fakten-/Assetfreigabe | D-8D-004 / D-9-006 | ja |
| `locked` nur auf ScriptLock, nicht ScriptDraft | D-9-002 / Phase-9-Code | ja |
| Source Group ≠ Kapitel | D-9-004 | ja (Regression nachholen) |

**Phase-9-Abgleich (Code-Stand Schema 15):**

- Editorial Project State trägt `active_brief_id`, `active_narrative_plan_id`,
  `selected_hook_id`, `active_script_id`, `active_coverage_audit_id`,
  `observation_fingerprint` (explizite Current-IDs).
- `ScriptDraftStatus` kennt kein `locked`; `structure_pending` ist im aktuellen
  Enum nicht vorhanden — Lock-Gate prüft **vollständige Struktur**
  (Sentences/Claims/Beats/Intents vorhanden) und behandelt unstrukturierte
  User-Edits analog blockierend. Optional in Implementierung:
  `structure_pending` nachziehen oder Äquivalenzregel dokumentieren.
- Coverage-Ergebnisse trennen in Implementierung idealerweise
  `coverage_level` und `risk_flags` (Phase-9-Auftrag); falls Code Level+Risiken
  noch in einem Enum mischt, Phase 10 normalisiert beim Gap-Aufbau.

Keine kritischen SoT-Konflikte. Offenes → §22.

---

## 1. Phase-10-Grenze

### Beginnt mit

Terminale oder offene Coverage-Ergebnisse des aktuellen Coverage Audits
(Phase 9), gebunden an aktuellen Script-/Brief-/Observation-Fingerprint.

### Endet mit

Gültigem **Script Lock** (`status=locked`) nach expliziter Nutzerbestätigung.

### In Scope

```text
Coverage Gaps / Gap-Entscheidungen
→ Eskalation (lokal → Foto → Suche → Satzrevision → erneut suchen → Grafik → Nutzer)
→ providerneutrale Supplementation (FakeStockSearchAdapter)
→ manuelle Kandidatenprüfung / Preview-Lebenszyklus / Dubletten
→ Annahme oder Ablehnung
→ optionaler Nutzerimport akzeptierter Originale
→ Media Intake (bestehend)
→ erneute Validierung / Assetanalyse / Observation Review
→ erneuter Coverage Audit
→ Script-Lock-Gate + Lock-Historie / Stale
```

### Explizit nicht in Phase 10

- echte Adobe-Suche, Adobe OAuth, Lizenzierung, echter Stockdownload
- Voice / ElevenLabs / Pausenregie / Timing
- Visual Edit Plan / OTIO
- Phase 11+

---

## 2. Verbindliche Eskalationsreihenfolge

```text
lokal tiefer prüfen
→ Foto
→ bessere Suche
→ Satz gezielt umformulieren
→ erneut suchen
→ Karte oder Grafik
→ Nutzerentscheidung
```

**Kein beliebiges Ersatzasset.**

Pro Coverage Gap persistieren:

| Feld | Inhalt |
|---|---|
| `current_escalation_step` | einer der sieben Schritte |
| `prior_attempts` | geordnete Historie (Attempt-IDs / Summaries) |
| `user_decision` | letzte Entscheidung oder null |
| `outcome` | Ergebnis des aktuellen Schritts |
| `visual_intent_id` | Bezug |
| `script_id` / `script_version` | Bezug |
| `open_risks` | Risikoflags / akzeptierte Unsicherheiten |

Schrittwechsel nur durch explizite Nutzeraktion (MANUAL).

---

## 3. Adobe- und Stock-Grenze

### Verbindliche Medienreihenfolge

```text
Bestand → Suche → Preview → Validierung → Dublettenprüfung
→ Akzeptanz → OAuth-Prüfung → Lizenzierung → Originaldownload
→ Media Intake → Registry
```

### In Phase 10 implementierbar (Fake/Alpha)

| Schritt | Plan |
|---|---|
| Bestand | bestehende Assets / lokale Vertiefung |
| Suche | `FakeStockSearchAdapter` + strukturierte lokale Testkandidaten |
| Preview | Preview-Metadaten / nichtproduktiver Preview-Bereich |
| Validierung / Dubletten | Metadaten- und Hash-/Identitätsregeln ohne Original ggf. eingeschränkt |
| Akzeptanz | manuell `accepted_for_import` / `rejected` / `needs_review` |
| OAuth / Lizenz / Download | **blockierend UNKNOWN** — Lock bleibt blockiert ohne akzeptiertes Original via Intake |
| Original | **manueller Nutzerimport** eines akzeptierten Testoriginals |
| Intake → Registry | bestehender Media-Intake-Pfad (Copy/Remux/Transcode/Convert) |

### UNKNOWN (nicht spekulieren)

- konkrete Adobe-OAuth-Variante
- Adobe-Endpunkte
- Lizenzmodell, Preis-/Kontoverhalten
- automatische Originaldownloads

---

## 4. Domainmodelle (Pydantic-Vorschlag)

Alle LLM-/Adapter-Antworten: `extra="forbid"`. IDs stabil (UUID).

### 4.1 CoverageGap

| Feld | Hinweis |
|---|---|
| `gap_id` | str |
| `project_id` | str |
| `script_id` / `script_version` | Bindung |
| `coverage_audit_id` | str |
| `visual_intent_id` | str |
| `coverage_level` | `covered` \| `partially_covered` \| `not_covered` |
| `missing_properties` | list[str] |
| `risks` / `risk_flags` | list (geo/generic/repetition/synthetic/user_decision) |
| `escalation_status` / `current_escalation_step` | Enum der 7 Schritte |
| `prior_attempt_summaries` | list |
| `status` | siehe unten |
| `gap_version` | int |
| `created_at` | datetime |

**Status:** `open` · `in_progress` · `resolved_with_local_asset` ·
`resolved_with_supplement` · `resolved_by_script_revision` ·
`resolved_by_graphic_plan` · `accepted_unresolved` ·
`user_decision_required` · `superseded`

Gaps entstehen aus nicht-terminalen bzw. risikobehafteten
`CoverageIntentResult`s; `covered` ohne offene Risiken erzeugt keinen offenen Gap
(oder sofort `resolved_*` nur nach Nutzerbestätigung lokaler Kandidaten — E-Entscheidung).

### 4.2 SupplementationRequest

| Feld | Hinweis |
|---|---|
| `request_id` | str |
| `gap_id` | str |
| Motiv / Aktion / Setting | aus Visual Intent |
| geografische / Authentizitätsanforderungen | |
| `allowed_media_kinds` | |
| `search_version` | int |
| `status` | `draft` \| `searching` \| `awaiting_decision` \| `import_pending` \| `completed` \| `cancelled` \| `stale` |

### 4.3 SearchAttempt

| Feld | Hinweis |
|---|---|
| `attempt_id` | str |
| `request_id` | str |
| `query_text` / `search_strategy` | str |
| `provider` | `fake` (Alpha) |
| `adapter_version` | str |
| `attempt_number` | int |
| `result_count` | int |
| `status` | `completed` \| `failed` \| `interrupted` |
| `error_code` / sanitized message | |
| `created_at` | |

### 4.4 StockCandidate

| Feld | Hinweis |
|---|---|
| `candidate_id` | str |
| `attempt_id` | str |
| `provider_candidate_id` | str |
| `preview_ref` | relative nichtproduktive Preview-Referenz oder null |
| Beschreibung, Medientyp, sichtbare Metadaten | |
| geografische Hinweise | ohne Gewissheitsbehauptung |
| `license_status` | immer `unknown` im Fake |
| `duplicate_status` | `unknown` \| `possible_duplicate` \| `not_duplicate` |
| `user_status` | `proposed` \| `accepted_for_import` \| `rejected` \| `needs_review` |

### 4.5 CandidateDecision (append-only)

`decision_id`, `candidate_id`, `revision`, Entscheidung, Begründung,
Nutzerhinweis, Zeitpunkt. Bestehende Zeilen nicht überschreiben.

### 4.6 GraphicPlan

`graphic_plan_id`, `visual_intent_id`, `gap_id`, Beschreibung, benötigte Daten,
geografischer Scope, `user_status` (`proposed` \| `accepted` \| `rejected`).
**Keine Grafikgenerierung** in Phase 10.

### 4.7 ScriptLock

| Feld | Hinweis |
|---|---|
| `lock_id` | str |
| `project_id` | str |
| `script_id` (+ version) | |
| `project_brief_id` | |
| `narrative_plan_id` | |
| `selected_hook_id` | |
| `coverage_audit_id` | |
| `observation_set_fingerprint` | |
| `script_hash` | |
| `structure_fingerprint` | Hash über Sentences/Claims/Beats/Intents |
| `coverage_fingerprint` | Hash über Audit + Gap-Entscheidungen |
| `accepted_open_risks` | list (explizit bestätigt) |
| `claim_decision_snapshot` | exakter Zustand |
| `user_confirmation` | bool + Timestamp + Fingerprint-Bestätigung |
| `lock_version` | int |
| `status` | `locked` \| `superseded` \| `invalidated` |
| `created_at` | |

**`locked` ausschließlich hier — nie auf `ScriptDraft`.**

### 4.8 ClaimDecision (Phase 10)

Append-only, gebunden an `script_id` + `claim_id` + Claim-Inhaltshash:

`confirmed` · `rejected` · `accepted_as_uncertain` · `revision_required`

Keine Modell-/Fake-Erzeugung von Nutzerbestätigungen.

---

## 5. Script-Lock-Gate

Lock nur wenn **alle** gelten:

1. aktueller aktiver Project Brief
2. aktueller Narrative Plan
3. Hook `selected`
4. aktueller Script Draft vollständig strukturiert (keine offenen Structure-Lücken;
   nicht äquivalent zu „nur Text ohne Sentences/Beats“)
5. aktueller Coverage Audit vorhanden und nicht stale
6. alle Visual Intents besitzen terminale Coverage-/Gap-Entscheidungen
7. offene Gaps: `resolved_*` oder ausdrücklich `accepted_unresolved`
8. unsichere Claims: `confirmed` / `rejected` / `accepted_as_uncertain`
9. keine stale Observation im Lock-Input-Fingerprint
10. keine stale Brief-/Hook-/Script-/Coverage-Version
11. keine laufenden Analysis-, Editorial- oder Supplementation-Runs
12. Nutzer bestätigt den konkreten **Lock-Fingerprint** (Checkbox nicht vorangekreuzt)

Kein automatischer Lock. Kein Lock durch Streamlit-Rerun.

---

## 6. Lock-Historie und Invalidierung

- Script Lock ist **unveränderlich** (kein In-place-Edit).
- Alter Lock bei Invalidierung: `invalidated` (Historie bleibt).
- Neue Sperrung = neuer Lock (+ ggf. `lock_version`).

### Invalidiert durch

- neue aktive Project-Brief-Version
- andere Hook-Auswahl
- neue Script-Version / neue Scriptstruktur
- neue Claimentscheidung
- neue Visual Beats oder Visual Intents
- neuer Coverage Audit
- Änderung einer terminalen Gap-Entscheidung
- neue oder stale Observation, wenn Teil des Lock-Inputs
- neue Supplementation-Ausgabe, die Coverage verändert

---

## 7. Supplementation und Media Intake

```text
akzeptierter Kandidat (Metadaten)
→ OAuth-/Lizenzprüfung erforderlich (UNKNOWN → blockiert Auto-Download)
→ akzeptiertes Original (manueller Import im Alpha)
→ normaler Media Intake
→ Working Media (completed)
→ technische Validierung
→ Assetanalyse (Prepare → Fake Vision)
→ Observation Review (accepted)
→ erneuter Coverage Audit
→ Gap-Status aktualisieren
```

### Alpha-/Fake-Regeln

- Preview ist **niemals** Working Media und keine OTIO-Quelle
- kein Shortcut Candidate → Editorial Asset
- keine Sonderregistrierung außerhalb Asset Registry
- akzeptierte ungenutzte Assets werden nicht automatisch gelöscht
- Intake-Services unverändert wiederverwenden:
  Selection/Import-Muster → Validation → Intake-Plan →
  `start_copy_intake` / remux / transcode / image_convert

---

## 8. Providerneutraler Fake-Pfad

```text
UI / Application (supplementation_*_service)
  → StockSearchGateway
      → FakeStockSearchAdapter   (verpflichtend zuerst)
      → (später) echte Adapter    (separates Gate; deaktiviert)
```

**FakeStockSearchAdapter:**

- kein Netzwerk, keine echten Stockanbieter
- deterministische Kandidatenmetadaten
- keine Lizenzbehauptung (`license_status=unknown`)
- keine geografische Gewissheit
- keine automatischen Downloads / keine Produktionsdateien
- simulierbare Fehler (timeout, invalid, duplicate flood, …)

Kein stiller Fallback auf Fake für später konfigurierte echte Provider.
Unbekannter/deaktivierter Provider → `supplementation_provider_unavailable` /
`supplementation_gateway_unconfigured`.

---

## 9. Persistenz und Schema (15 → **16**)

### Prinzip

- SQLite = interne Wahrheit; explizite Current-IDs in Project State erweitern
- Editorial-Runs (`editorial_runs`) **nicht** mit Supplementation-Semantik vermischen
- Eigene Supplementation-Run-/Attempt-Tabellen **oder** parallele
  `supplementation_runs` / `supplementation_attempts` (Empfehlung: **eigene Tabellen**)

### Tabellen (minimal)

| Tabelle | Zweck | Unique / Historie | Warum jetzt |
|---|---|---|---|
| `coverage_gaps` | Gap-Kopf + Eskalation | `(project_id, visual_intent_id, gap_version)`; Current über Project State oder `status!=superseded` | Gap-Arbeitsgegenstand |
| `supplementation_requests` | Suchauftrag je Gap | `request_id` PK; FK gap | Suchvertrag |
| `stock_search_attempts` | Suchversuche | Attempt-Nummer je Request | Audit/Retry |
| `stock_candidates` | Kandidaten | `candidate_id`; Unique provider+attempt | Entscheidungen |
| `stock_candidate_decisions` | append-only | `(candidate_id, revision)` | Historie |
| `graphic_plans` | Karte/Grafik-Plan | `graphic_plan_id` | Eskalationsstufe |
| `claim_decisions` | append-only Claim-Entscheidungen | `(claim_id, script_id, revision)` | Lock-Voraussetzung |
| `script_locks` | Lock-Kopf | `lock_id`; Current `status=locked` max. 1/project | Gate |
| `script_lock_risks` | akzeptierte offene Risiken am Lock | `(lock_id, risk_key)` | Transparenz |
| `supplementation_runs` | Job-Orchestrierung | analog editorial_runs | Sperren/Recovery |
| `supplementation_attempts` | Cache/Retry | Cache-Key-Index | Fake-Gateway |

### Project State Erweiterung (Schema 16)

Zusätzliche Current-IDs (keine `created_at`-Heuristik):

- `current_gap_set_fingerprint` (optional)
- `current_supplementation_request_id`
- `current_script_lock_id`
- behalten: Brief/Narrative/Hook/Script/Coverage/Observation-Fingerprint

### Nicht anlegen

Voice-, Pause-, Timing-, Edit-Plan-, OTIO-Tabellen.

---

## 10. Artefaktpfade

Unter `_otio_v2/editorial/`:

```text
editorial/gaps/<gap_id>.json
editorial/supplementation/requests/<request_id>.json
editorial/supplementation/searches/<attempt_id>.json
editorial/supplementation/candidates/<candidate_id>.json
editorial/graphics/<graphic_plan_id>.json
editorial/script_locks/<lock_id>.json
editorial/claim_decisions/<decision_id>.json   # oder gebündelt je script
editorial/runs/supplementation/<run_id>.json
editorial/temp/<run_id>/
editorial/latest_script_lock.json              # Pointer, nicht alleinige Wahrheit
```

**Preview-Dateien:** nicht unkontrolliert in JSON-Dirs.
Vorschlag: `_otio_v2/editorial/supplementation/previews/<attempt_id>/…`
(nichtproduktiv; nie Working Media; nie OTIO-Quelle).

Regeln: relative Pfade; kein `_otio`; kein `..`; atomare JSON-Publikation;
SQLite Wahrheit.

---

## 11. Claims und Nutzerentscheidungen

- UI erlaubt `confirmed` / `rejected` / `accepted_as_uncertain` / `revision_required`
- append-only; gebunden an exakte Script- und Claimversion (Inhaltshash)
- neue Claim-Version erbt **keine** alte Bestätigung
- Fake/LLM erzeugt keine Nutzerbestätigung
- Lock speichert Snapshot aller Claimentscheidungen

---

## 12. Satzrevision wegen Coverage

Eskalationsstufe „Satz gezielt umformulieren“:

1. strukturierter Vorschlag oder Nutzeredit (Fake: kein echter LLM-Repair nötig)
2. **neue** Script-Version (nie stilles Edit des aktuellen/gelockten Scripts)
3. Structure erneut erzeugen (expliziter Structure-Run)
4. Coverage erneut
5. Gap-/Suchhistorie bleibt; alter Coverage Audit → stale
6. kein Auto-Lock

---

## 13. MANUAL-UI-Workflow

Erweiterung der Seite **Editorial** (oder Unterbereich „Coverage / Lock“):

### Coverage Gaps

Anzeigen: Visual Intent, fehlende Properties, Risiken, lokale Kandidaten,
aktueller Eskalationsschritt, bisherige Versuche, empfohlene nächste Aktion.

### Lokale Vertiefung

Button: **Lokale Assets erneut prüfen** — explizit; kein Scan beim Rendering.

### Supplementation

Button: **Ergänzungskandidaten suchen** — nur Fake-Adapter.
Kandidatenkarte: Previewstatus, Herkunft, Medienart, Beschreibung, Risiken,
Dublettenhinweis, Lizenzstatus `unknown`, Nutzerentscheidung.

### Grafik / Karte

Button: GraphicPlan anlegen — **keine** Medienerzeugung.

### Script Lock

Vor Lock anzeigen: Scriptversion, Hook, Satzanzahl, Claimstatus, Beats/Intents,
Coverage-Zähler, akzeptierte offene Risiken, Observation-/Coverage-/Lock-Fingerprint.

Checkbox + Button (nicht vorangekreuzt):

**Skript für Voice und Timing sperren**

---

## 14. Jobs und Sperren

| Regel | Plan |
|---|---|
| Aktive Supplementation | max. 1 Run/Projekt |
| Supplementation aktiv | blockiert Editorial- und Analysis-Starts (Inputs könnten sich ändern) |
| Analysis/Editorial aktiv | blockiert Supplementation |
| Script Lock | nur ohne aktive relevante Runs; kurzer Application-Transaction OK |
| Orphan | Run/Attempt → failed/interrupted; nur eigener Temp; kein Gateway |
| Kein Auto | kein Rerun-Search, kein Auto-Intake, kein Auto-Lock |

### Scope-Namen

- `supplementation_local_review_only`
- `supplementation_search_only`
- `supplementation_candidate_validation_only`
- `script_lock_only` (optional; Lock darf synchron in Application laufen)

Launcher: `SupplementationJobLauncher` (Analog Editorial/Analysis) oder Erweiterung
mit **getrennten** Scope-Namespaces — Semantik nicht mit `editorial_*` vermischen.

---

## 15. Fehlercodes

| Code |
|---|
| `coverage_gap_missing` |
| `coverage_gap_stale` |
| `supplementation_request_invalid` |
| `supplementation_gateway_unconfigured` |
| `supplementation_provider_unavailable` |
| `supplementation_response_invalid` |
| `invalid_stock_candidate` |
| `stock_candidate_duplicate` |
| `stock_candidate_preview_missing` |
| `stock_candidate_not_accepted` |
| `stock_license_unknown` |
| `stock_oauth_unknown` |
| `supplementation_retry_exhausted` |
| `claim_decision_required` |
| `claim_decision_stale` |
| `script_lock_requirements_not_met` |
| `script_lock_confirmation_required` |
| `script_lock_fingerprint_mismatch` |
| `script_lock_conflict` |
| `script_lock_invalidated` |
| `supplementation_run_already_active` |
| `analysis_run_already_active` |
| `editorial_run_already_active` |
| `supplementation_artifact_conflict` |
| `supplementation_registry_write_failed` |
| `supplementation_artifact_write_failed` |
| `worker_interrupted` |
| `report_write_failed` |

Keine Secrets/vollständigen Payloads in Meldungen.

---

## 16. Testplan (Gruppen)

1. Schema 15 → 16 + Datenhalt
2. Coverage-Gap-Erzeugung aus Audit
3. Eskalationsreihenfolge (keine Sprünge ohne Historie)
4. lokale Vertiefungsprüfung
5. Fake Stock Gateway; kein Netzwerk; keine Lizenzbehauptung
6. Kandidatenentscheidung append-only
7. Dublettenprüfung
8. Preview ≠ Working Media / ≠ OTIO
9. akzeptiertes Original → normaler Intake
10. erneute Assetanalyse + Coverage
11. GraphicPlan ohne Medienerzeugung
12. Satzrevision → neue Script-Version
13. Claimentscheidungen
14. Script-Lock-Gate + Fingerprint
15. Lock-Invalidierung
16. kein Auto-Lock
17. keine Voice-/Timingfunktion
18. Recovery und Runs / gegenseitige Sperren
19. UI-No-I/O
20. Classic-/Without-VO-Regression
21. `_otio_v2`-Isolation
22. keine Phase-11-Funktion

### Nachgeholte Phase-9-Regressionen (dauerhaft)

- Source Groups erzeugen keine Kapitel oder Beats
- stale Observation gelangt nicht in Coverage oder Lock

---

## 17. Fake-End-to-End-Smokes

| ID | Inhalt |
|---|---|
| **A** | Gap durch lokale Vertiefung gelöst; kein Stock-Run; Coverage terminal; Script Lock OK |
| **B** | Fake-Suche → Kandidat → Metadaten akzeptiert; `license_status=unknown`; Lock blockiert ohne Original-Intake |
| **C** | manueller Originalimport → Intake → WM → Analyse → Review → Coverage → Gap gelöst |
| **D** | Satzrevision → neue Script-Version; alte bleibt; neuer Audit; kein Auto-Lock |
| **E** | `accepted_unresolved` Risiko im Lock; Lock nur nach expliziter Bestätigung |
| **F** | stale Observation ausgeschlossen; keine historische Freigabe |
| **G** | Lock OK → neue Script-Version → alter Lock `invalidated`; Voice nicht implementiert |
| **H** | Orphan-Supplementation; Temp clean; kein Provideraufruf bei Recovery/Render; kein Auto-Lock |

---

## 18. Implementierungsaufteilung

### Bevorzugter Makroauftrag

```text
Schema 16 + Gap-/Claim-/Lock-Domain
+ StockSearchGateway + FakeStockSearchAdapter
+ Gap-Service + Eskalation
+ Supplementation Search/Decision Services
+ GraphicPlan (ohne Media)
+ Intake-Anbindung (manueller Import)
+ Re-Coverage-Orchestrierung
+ Script-Lock-Gate
+ UI-Erweiterung Editorial
+ Recovery + gegenseitige Sperren
+ Tests + Smokes A–H
+ Phase-9-Regressionen (Source Group / stale Obs)
```

### Separates Gate (nie im Makro still aktivieren)

Echte Stockprovider / Adobe OAuth / Lizenz / Auto-Download.

Unterteilung nur bei Sicherheitsgrund (z. B. Schema+Gaps isoliert reviewen).

---

## 19. Modulvorschlag

```text
otio_app/discovery_v2/
  domain/supplementation.py          # Gaps, Candidates, Lock, ClaimDecision
  adapters/stock_config.py
  adapters/stock_fake.py
  adapters/stock_gateway.py
  adapters/supplementation_job_launcher.py
  application/coverage_gap_service.py
  application/supplementation_service.py
  application/script_lock_service.py
  application/supplementation_job_recovery.py
  persistence/supplementation_repository.py
  supplementation_paths.py           # oder Erweiterung editorial_paths
  jobs/supplementation_worker.py
  ui/editorial_page.py               # erweitern
```

---

## 20. Entscheidungen (zur Freigabe im Implementierungsauftrag)

| # | Thema | Planvorschlag |
|---|---|---|
| E1 | Stock-Gateway | neu, Fake zuerst; kein Classic-Adobe-Orchestrierungs-Reuse |
| E2 | Schema | 15 → **16**; eigene Supplementation-Run-Tabellen |
| E3 | Gap aus `covered` | kein offener Gap ohne Risiko; Risiken → Gap/`user_decision_required` |
| E4 | Kandidatenlimit/Suche | max. **10** Results/Attempt; UI zeigt ≤10; >10 vom Adapter = invalid |
| E5 | Suchversuche/Gap | max. **5** SearchAttempts je Gap-Version |
| E6 | Preview-Ort | `_otio_v2/editorial/supplementation/previews/` |
| E7 | Dublette ohne Original | Metadaten-Fingerprint + optionaler Preview-Hash; sonst `unknown` |
| E8 | Lock-Ausführung | synchron in Application (`script_lock_only` ohne schweren Worker) |
| E9 | `structure_pending` | Lock blockiert unstrukturierte Scripts; Enum-Nachzug optional |
| E10 | Coverage Level vs Risks | Gap-Modell trennt Level und `risk_flags` |
| E11 | `accepted_unresolved` | erlaubt Lock nur mit expliziter Risiko-Checkbox je Risiko |
| E12 | Parallele Gaps | ja; ein Supplementation-Run bearbeitet ausgewählte Gap-IDs |

---

## 21. UNKNOWN-Punkte

- echte Stockprovider, Adobe OAuth, Lizenz-/Kostenmodell, Auto-Download
- Preview-CDN/MIME-Details jenseits lokaler Fake-Fixtures
- Dublettenidentität über Provider hinweg ohne Datei
- produktive Suchstrategien / Ranking
- Mehrbenutzer / Queue

---

## 22. DoD für späteren Implementierungsauftrag

- Schema 16 + Fake-Smokes A–H grün
- Eskalationsreihenfolge erzwungen
- Preview nie Working Media
- Lock nur nach Fingerprint-Bestätigung; Invalidierung historisch
- keine echten Provider/OAuth/Lizenz
- keine Phase-11-Funktionen
- UI-No-I/O; Classic/Without-VO unverändert
- Vollsuite: keine neuen Discovery-bedingten Failures; Baseline-18 unangetastet
- nachgeholte Phase-9-Regressionen (Source Group, stale Obs) grün

---

## 23. Nächste erlaubte Aktion

Nach **Freigabe dieses Plans**:

→ Phase-10-**Implementierungs**-Makroauftrag gemäß §18.

Gesperrt: echte Stock/Adobe, Phase 11+, Voice, Timing, OTIO.
