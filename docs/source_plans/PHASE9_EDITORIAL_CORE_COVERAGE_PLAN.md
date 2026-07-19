# Phase 9 — Editorial Core und Coverage Plan

**Auftrags-ID:** `DISCOVERY-V2-PHASE9-EDITORIAL-CORE-COVERAGE-PLAN-001`
**Status:** PLANNING ONLY — keine Produktimplementierung
**Basis-HEAD:** `15e7d943e4c7b2b6e67920e99371e6f253719442`
**Registry-Ausgang:** Schema **14**
**SoT-Rang:** `docs/source_plans/*` — nachrangig; überschreibt keine höheren Dokumente

---

## 0. Konfliktprüfung gegen höhere SoT

| Thema | Höhere Quelle | Plan-Konformität |
|---|---|---|
| Phase 9 endet nach Coverage | MASTER_PLAN / Manifest | ja |
| kein Script Lock / Stock / Voice | MASTER_PLAN / ALPHA_SCOPE | ja |
| nur editorial-ready Observations | D-8D-004 / Handoff | ja |
| Fake Text zuerst; echte Provider Gate | MODEL_ROUTING / D-8D-003 Analog | ja |
| UI → Application → Domain | 00-core-architecture | ja |
| `_otio_v2` only; Classic read-only | CLASSIC_MIGRATION / paths | ja |
| MANUAL; kein Auto-Rerun-Start | ALPHA_SCOPE / 01-step | ja |
| NEGATIVE_REFERENCE | 00-core / EDITORIAL_QUALITY | ja |
| Sentence ≠ Beat ≠ Intent ≠ Asset | EDITORIAL_QUALITY / MASTER | ja |

Keine kritischen ungelösten Konflikte. Offene Punkte → Abschnitt 24 (UNKNOWN/Entscheidungen).

---

## 1. Phase-9-Grenze

### In Scope

```text
Project Brief
→ Narrative Plan
→ Hook-Varianten
→ Script Draft
→ Claims und Sätze
→ Visual Beats
→ Visual Intents
→ Coverage Audit
```

### Explizit nicht in Phase 9

- Stock-Supplementation / Adobe-Suche / Lizenzierung
- Script Lock
- Voice / ElevenLabs
- LLM-Pausenregie / Python-Timing
- konkrete Shot-Timeline / Visual Edit Plan
- OTIO Export
- Phase 10+

Ein Phase-9-Skript ist ein **versionierter Entwurf** (`draft` / `review_requested` /
`user_edited` / `superseded`). Status **`locked` existiert nicht**.

---

## 2. Domainmodelle (Pydantic-Vorschlag)

Alle Modelle: `BaseModel`, versionierte `schema_version`, wo sinnvoll
`ConfigDict(extra="forbid")` für LLM-Antworten. IDs als stabile Strings (UUID).

### 2.1 ProjectBrief

| Feld | Typ / Hinweis |
|---|---|
| `project_brief_id` | str |
| `project_id` | str |
| `language` | str |
| `topic` | str |
| `target_audience` | str |
| `desired_duration_seconds` | int \| null |
| `tone` | str |
| `geographic_frame` | str \| null |
| `must_include` | list[str] |
| `must_exclude` | list[str] |
| `user_notes` | str \| null |
| `brief_version` | int (monoton je project_id) |
| `content_sha256` | str |
| `status` | `draft` \| `active` \| `superseded` |
| `created_at` | datetime |
| `supersedes_brief_id` | str \| null |

### 2.2 NarrativePlan

| Feld | Typ / Hinweis |
|---|---|
| `narrative_plan_id` | str |
| `project_id` | str |
| `project_brief_id` / `brief_version` | Bindung |
| `central_question` | str |
| `editorial_thesis` | str |
| `hook_strategy` | str |
| `narrative_roles` | list[str] \| strukturierte Rollen |
| `arc` | str (Spannungsbogen) |
| `transition_logic` | str |
| `ending_function` | str |
| `uncertainties` | list[str] |
| `input_observation_ids` | list[str] (editorial-ready Snapshot) |
| `prompt_version` / `gateway_version` / `model_identifier` / `provider` | str |
| `status` | `draft` \| `active` \| `stale` \| `superseded` |
| `created_at` | datetime |

### 2.3 HookVariant

| Feld | Typ / Hinweis |
|---|---|
| `hook_id` | str |
| `narrative_plan_id` | str |
| `hook_text` | str |
| `hook_type` | str |
| `intended_effect` | str |
| `risks` | list[str] |
| `local_evidence_refs` | list[str] (observation_ids o. ä.) |
| `user_status` | `proposed` \| `selected` \| `rejected` |
| `created_at` | datetime |

Mehrere Varianten bleiben parallel historisch erhalten; Auswahl ändert nur
`user_status`, löscht keine anderen Hooks.

### 2.4 ScriptDraft

| Feld | Typ / Hinweis |
|---|---|
| `script_id` | str |
| `script_version` | int |
| `project_id` | str |
| `language` | str |
| `full_text` | str |
| `sentence_order` | list[str] (sentence_ids) |
| `narrative_plan_id` | str |
| `selected_hook_id` | str \| null |
| `project_brief_id` / `brief_version` | Bindung |
| `prompt_version` / `gateway_version` / `model_identifier` / `provider` | str |
| `source_kind` | `llm` \| `user_edit` |
| `supersedes_script_id` | str \| null |
| `content_sha256` | str |
| `status` | `draft` \| `review_requested` \| `user_edited` \| `superseded` |
| `created_at` | datetime |

**Kein** Status `locked`.

### 2.5 Sentence

| Feld | Typ / Hinweis |
|---|---|
| `sentence_id` | str |
| `script_id` | str |
| `ordinal` | int |
| `text` | str |
| `narrative_function` | str |
| `claim_ids` | list[str] |
| `visual_beat_ids` | list[str] (optional, Many-to-Many) |
| keine finalen Zeitwerte | — |
| keine Shot-Source-Ranges | — |

### 2.6 Claim

| Feld | Typ / Hinweis |
|---|---|
| `claim_id` | str |
| `script_id` | str |
| `statement` | str |
| `claim_type` | str |
| `confidence` | float \| enum |
| `evidence_refs` | list[{kind, id}] — Observation/Asset/UserNote |
| `user_note` | str \| null |
| `status` | `supported` \| `uncertain` \| `user_confirmation_required` \| `unsupported` |

Visual Observations dürfen Claims **unterstützen**, gelten aber nicht automatisch
als Faktenbeweis.

### 2.7 VisualBeat

Eigenständiger redaktioneller Abschnitt. **Kein** Technical Shot.

| Feld | Typ / Hinweis |
|---|---|
| `visual_beat_id` | str |
| `script_id` | str |
| `function` | str |
| `description` | str |
| `sentence_ids` | list[str] (Many-to-Many) |
| `rhythm_function` | str |
| `continuity_requirements` | list[str] |
| `intended_duration_hint_seconds` | float \| null — Absicht, keine finale Auflösung |

### 2.8 VisualIntent

| Feld | Typ / Hinweis |
|---|---|
| `visual_intent_id` | str |
| `visual_beat_id` | str |
| `desired_motif` | str |
| `action` | str |
| `setting` | str |
| `geographic_requirements` | str \| null |
| `authenticity_requirements` | list[str] |
| `allowed_media_kinds` | list[`video`\|`image`\|…] |
| `priority` | int \| enum |
| keine konkrete Assetbindung | — |
| keine Source-Range | — |

### 2.9 CoverageAudit / CoverageIntentResult

Audit-Kopf:

| Feld | Typ / Hinweis |
|---|---|
| `coverage_audit_id` | str |
| `project_id` | str |
| `script_id` / `script_version` | Bindung |
| `brief_version` / `narrative_plan_id` | Bindung |
| `input_observation_fingerprint` | str (hash der accepted Observation-IDs+sha) |
| `status` | `completed` \| `failed` \| `stale` |
| `created_at` | datetime |
| `prompt_version` / `gateway_version` / … | str |

Pro Visual Intent (`CoverageIntentResult`):

| Feld | Typ / Hinweis |
|---|---|
| `visual_intent_id` | str |
| `coverage_status` | `covered` \| `partially_covered` \| `not_covered` \| `geographically_uncertain` \| `too_generic` \| `repetition_risk` \| `possible_synthetic_risk` \| `user_decision_required` |
| `candidate_asset_ids` | list[str] — nur bestehende Projekt-Assets |
| `accepted_observation_ids` | list[str] |
| `rationale` | str |
| `confidence` | float |
| `missing_properties` | list[str] |
| `recommended_next_action` | str — z. B. Hinweis auf Phase-10-Eskalation; **keine** Stock-Suche |

---

## 3. Modelltrennung (verbindlich)

| Trennung | Regel |
|---|---|
| Source Group ≠ Kapitel | Ordner/`source_group` erzeugt kein Kapitel und keinen Beat |
| Technical Shot ≠ Editorial Shot | Phase-8-Shots bleiben technisch; keine automatische Beatbildung |
| Sentence ≠ Visual Beat | Many-to-Many; keine mechanische Beatbildung aus Satzgrenzen |
| Visual Beat ≠ Visual Intent | Intent ist Motivwunsch ohne Assetbindung |
| Visual Intent ≠ Asset | Coverage bindet Kandidaten erst im Audit |
| Claim ≠ Visual Observation | Observation stützt, beweist nicht |
| Coverage-Ergebnis ≠ Script Lock | Audit schließt Phase 9; Lock ist Phase 10 |

---

## 4. Eingaben

### Zulässig

- aktuelle Projektidentität (`discovery_v2`)
- aktueller/activer Project Brief
- aktuelle **editorial-ready** Visual Observations
  (`list_editorial_ready_observations`: accepted + aktuelle Analysis Identity +
  aktuelle Vision-Config-Versionen + gültiges Schema + frame fingerprint)
- aktuelle Working-Media- / Analysis-Identitäten (nur Metadaten/IDs)
- Nutzerparameter (Brief-Felder, Hook-Auswahl, Script-Edits)
- zentrale Discovery-Textmodellkonfiguration (Fake zuerst)

### Unzulässig

- `unreviewed` / `rejected` / `reanalyze_requested` Observations
- historische oder stale Observations
- Datei-/Ordnernamen als visuelle Evidenz
- Originalmedienpfade
- Analysis Frames als Produktionsassets
- `NEGATIVE_REFERENCE`-Timelines als positive Vorlage

---

## 5. Text-Gateway — Entscheidung im Plan

### Entscheidung (Planvorschlag zur Freigabe im Implementierungsauftrag)

**Discovery-spezifischen Text-Gateway etablieren** neben dem Vision-Gateway und
neben Classic `plan_llm_client` — nicht `plan_llm_client` multimodal/fachlich
aufblasen.

```text
UI / Application (editorial_*_service)
  → DiscoveryTextGateway
      → FakeTextAdapter          (verpflichtend zuerst, vollständiger Pfad)
      → (später) ProviderAdapter  (separates Gate; deaktiviert)
```

### Begründung

- eigene Prompt-/Response-Schema-Versionen und Audit-Records
- Editorial-Inputvertrag (Brief + accepted Observations)
- Trennung von Classic-Orchestrierung (CLASSIC_MIGRATION_CONTRACT)
- spiegelt erfolgreiches Phase-8-Vision-Muster

### Wiederverwendbar aus Classic (nur Infrastruktur-Ideen)

- Key-Laden / Provider-Präfixe / Settings-Labels
- Timeout-/Retry-Ideen

### Konfiguration

| Knob | Plan |
|---|---|
| `provider` | `fake` (Alpha-Default für Implementierung) |
| `model_identifier` | `fake-text-v1` (kein hartes Fachmodell) |
| `gateway_version` | `discovery-text-gateway-v1` |
| `prompt_version` | je Use-Case, z. B. `editorial-narrative-v1` |
| `response_schema_version` | je Use-Case, z. B. `narrative-plan-v1` |
| Retries | begrenzt (Analog Vision: max 2) |
| fehlende Config | `editorial_gateway_unconfigured` / `editorial_model_unavailable` |
| stiller Fallback | **verboten** |
| Cache-Identität | project + brief_version + input fingerprints + provider/model/gateway/prompt/schema + request_kind |
| Consent | Fake: kein externer Consent; später `editorial_consent_required` wenn extern |

Erster Implementierungsauftrag: **nur Fake-Textpfad**. Reale Textprovider = separates Gate.

---

## 6. LLM- vs. Python-Verantwortung

### LLM (bzw. Fake-Adapter) darf planen

- Narrative Plan, Hook-Varianten, Script Draft
- Satzfunktionen, Claims, Visual Beats, Visual Intents
- Coverage-Begründungen / redaktionelle Unsicherheiten
- Pausen-/Hook-Strategie auf redaktioneller Ebene (noch keine Timingauflösung)

### Python muss übernehmen

- Identitäten, Versionsbindung, Ordnungen (Ordinals)
- Schema-Validierung (`extra="forbid"` wo Gateway-Antwort)
- Cache, Statusübergänge, Stale-Erkennung
- Persistenz (SQLite Wahrheit + atomare JSON)
- deterministische Coverage-Zähler
- Validierung von Asset-/Observation-/Sentence-/Beat-/Intent-Referenzen
- Job-Mutual-Exclusion, Orphan-Recovery

---

## 7. Persistenz- und Schemavorschlag (ab Schema 14 → **15**)

**Prinzip:** SQLite = interne Wahrheit; große strukturierte LLM-Payloads als
versionierte JSON unter `_otio_v2/editorial/`; Registry speichert IDs, Status,
Versionen, Hashes, relative Pfade, FKs.

Keine vorsorglichen Phase-10+-Tabellen.

### 7.1 `editorial_runs`

| | |
|---|---|
| Zweck | Orchestrierung eines Editorial-Jobs (narrative/script/coverage) |
| Spalten (Kern) | `run_id`, `project_id`, `scope`, `status`, `brief_id`, `brief_version`, `script_id`, `error_code`, `relative_report_path`, `created_at`, `started_at`, `finished_at`, `schema_version` |
| FK | project via app; brief optional |
| Unique | `run_id` PK |
| Status | `queued` \| `running` \| `completed` \| `failed` \| `interrupted` |
| Historie | append-only Runs |
| Stale | n/a (Run ist Ereignis) |
| JSON | Run-Report unter `editorial/runs/<run_id>.json` |
| Warum jetzt | Mutual exclusion, Recovery, Audit analog Phase 8 |

Scopes (Vorschlag):

- `editorial_narrative_only`
- `editorial_script_only`
- `editorial_coverage_only`

### 7.2 `editorial_llm_attempts`

| | |
|---|---|
| Zweck | Cache/Historie je Gateway-Aufruf |
| Spalten | `attempt_id`, `run_id`, `project_id`, `request_kind`, `provider`, `model_identifier`, `gateway_version`, `prompt_version`, `response_schema_version`, `input_fingerprint`, `status`, `relative_json_path`, `error_code`, `created_at` |
| Unique | sinnvoller Cache-Key-Index über Identity-Felder |
| Status | `completed` \| `failed` \| `reused` \| `interrupted` |
| Warum jetzt | Retry/Cache/Audit wie `model_analysis_attempts` |

### 7.3 `project_briefs`

| | |
|---|---|
| Zweck | Brief-Versionen |
| Spalten | IDs, `brief_version`, `status`, `content_sha256`, `relative_json_path`, `supersedes_brief_id`, timestamps, Kernfelder optional denormalisiert (`language`, `topic`) |
| Unique | `(project_id, brief_version)` |
| Historie | neue Version bei Edit; alte `superseded` |
| JSON | vollständiger Brief |
| Warum jetzt | Wurzel aller Phase-9-Abhängigkeiten |

### 7.4 `narrative_plans`

| | |
|---|---|
| Zweck | Narrative-Plan-Versionen |
| Spalten | IDs, `brief_id`, `brief_version`, `status`, model/prompt versions, `input_observation_fingerprint`, `relative_json_path`, timestamps |
| Historie | append/supersede |
| JSON | vollständiger Plan inkl. Uncertainties |
| Warum jetzt | Eingang für Hooks/Script |

### 7.5 `hook_variants`

| | |
|---|---|
| Zweck | parallele Hook-Historie + Nutzerauswahl |
| Spalten | `hook_id`, `narrative_plan_id`, `user_status`, `relative_json_path` oder Inline-Text, timestamps |
| Unique | `hook_id`; max. ein `selected` je `narrative_plan_id` (partieller Unique / App-Rule) |
| Historie | Varianten bleiben; Statuswechsel |
| Warum jetzt | MANUAL Hook-Auswahl |

### 7.6 `script_drafts`

| | |
|---|---|
| Zweck | Skriptversionen |
| Spalten | `script_id`, `script_version`, `project_id`, `status`, `source_kind`, Bindungen (brief/narrative/hook), model versions, `content_sha256`, `relative_json_path`, `supersedes_script_id` |
| Unique | `(project_id, script_version)` und/oder `script_id` PK |
| Status | ohne `locked` |
| JSON | full_text + Metadaten |
| Warum jetzt | Kernartefakt Phase 9 |

### 7.7 `script_sentences`

| | |
|---|---|
| Zweck | geordnete Sätze je Script |
| Spalten | `sentence_id`, `script_id`, `ordinal`, `text`, `narrative_function`, optional JSON für claim/beat refs |
| Unique | `(script_id, ordinal)`, `sentence_id` |
| Historie | gehören zu Script-Version; bei Edit neue Script-Version + neue/abgeleitete Sentence-IDs |
| Warum jetzt | Referenzen für Claims/Beats |

### 7.8 `script_claims`

| | |
|---|---|
| Zweck | Claims je Script |
| Spalten | `claim_id`, `script_id`, `status`, `relative_json_path` oder strukturierte Spalten + evidence JSON |
| Warum jetzt | Evidenz-/Unsicherheitsmodell |

### 7.9 `visual_beats` / `visual_intents`

| | |
|---|---|
| Zweck | redaktionelle Beats/Intents |
| Spalten | IDs, `script_id` / `visual_beat_id`, Priorität, `relative_json_path` |
| Join | `visual_beat_sentences(visual_beat_id, sentence_id)` Many-to-Many |
| Warum jetzt | Coverage-Eingang; Domaintrennung erzwingen |

### 7.10 `coverage_audits` / `coverage_intent_results`

| | |
|---|---|
| Zweck | Audit-Kopf + je Intent Ergebnis |
| Spalten | Audit: Bindungen, fingerprints, status, report path; Results: intent_id, coverage_status, confidence, candidate/observation JSON, rationale path/text |
| Unique | `coverage_audit_id`; `(coverage_audit_id, visual_intent_id)` |
| Warum jetzt | Phase-9-Ende; keine Stock-Aktion |

### 7.11 `editorial_user_decisions` (optional schlank)

| | |
|---|---|
| Zweck | append-only Nutzerentscheidungen (Hook select/reject, Brief activate, …) |
| Spalten | `decision_id`, `project_id`, `decision_type`, `target_id`, `payload_json`, `created_at` |
| Warum jetzt | Auditierbarkeit MANUAL; kann teilweise in Entity-Status aufgehen — **Entscheidung im Implementierungsauftrag**, ob separate Tabelle nötig ist |

### JSON vs. Normalisierung

- **Normalisieren:** IDs, Status, Versionen, FKs, Ordinals, fingerprints, relative paths, coverage_status, hook user_status
- **JSON-Artefakt:** vollständige LLM-Antworten, Brief-Volltextfelder, Narrative-Details, Script full_text, Coverage-Rationales

---

## 8. Artefaktpfade

Wurzel: `_otio_v2/editorial/`

```text
editorial/briefs/<brief_id>.json
editorial/narrative_plans/<narrative_plan_id>.json
editorial/hooks/<narrative_plan_id>/<hook_id>.json
editorial/scripts/<script_id>.json
editorial/scripts/<script_id>.diff.md          # optional, generiert
editorial/coverage/<coverage_audit_id>.json
editorial/runs/<run_id>.json
editorial/attempts/<attempt_id>.json
editorial/latest_brief.json                    # Pointer
editorial/latest_narrative_plan.json
editorial/latest_script.json
editorial/latest_coverage.json
editorial/temp/<run_id>/...
```

Regeln (Analog `analysis_paths` / inventory stores):

- nur relative Pfade unter `editorial/` persistieren
- kein `_otio/`, keine Absoluten, kein `..`
- keine Working-Media- oder Analysis-Artefakte ändern/überschreiben
- atomare Veröffentlichung (`mkstemp` / `*.tmp` + `os.replace` + fsync wo etabliert)
- SQLite bleibt interne Wahrheit; Pointer sind Komfort, nicht alleinige Wahrheit

---

## 9. Stale-Matrix

| Änderung | Stale | Nicht stale |
|---|---|---|
| Project Brief geändert (neue Version) | Narrative Plan, Hooks, Script Draft, Claims, Visual Beats, Visual Intents, Coverage Audit | Media Intake, Technical Shots, Representative Frames, Visual Observations + Reviews |
| Akzeptierte Observation geändert / Identity nicht mehr editorial-ready | Narrative Plan (wenn Obs als Input), Script Draft, Visual Intents, Coverage Audit; Claims mit Obs-Evidenz | Brief (Inhalt), Media/Prepare-Artefakte |
| Script Draft geändert (neue Version) | abgeleitete Sentences/Claims/Beats/Intents der alten Version (superseded); Coverage Audit | Brief, Narrative Plan, Hooks (bis Nutzer neu generiert), Phase-8-Artefakte |
| Modell-/Prompt-/Schema-Version geändert | neue historische Ausgabe; alte nicht überschreiben | bestehende historische Artefakte bleiben |
| Hook-Auswahl gewechselt | Script Draft + abhängige Coverage (wenn Script an Hook gebunden) | Brief, Narrative Plan, andere Hook-Varianten |

Noch nicht vorhanden → **nicht implementieren / nicht stale-markieren:** Voice, Timing, Edit Plan, Script Lock, Stock.

Stale-Erkennung: Application vergleicht aktuelle Brief-/Script-/Observation-Fingerprints
mit in Artefakten gespeicherten Bindungen; UI zeigt `stale` und blockiert Folgeaktionen
bis Neugenerierung.

---

## 10. MANUAL-Workflow

1. Project Brief anlegen oder bearbeiten (explizit speichern → neue Version).
2. Narrative-Generierung **ausdrücklich** starten (Button → Job).
3. Hook-Varianten anzeigen (mind. 3 im Fake-Pfad).
4. Nutzer wählt einen Hook oder bearbeitet/lehnt ab.
5. Script Draft **ausdrücklich** erzeugen.
6. Nutzer kann Skripttext bearbeiten → neue Script-Version (`user_edited`).
7. Claims, Sätze, Visual Beats, Visual Intents anzeigen (aus Script-Run oder Folgeparse).
8. Coverage Audit **ausdrücklich** starten.
9. Coverage-Lücken/Risiken + empfohlene nächste Aktion (Phase-10-Hinweis) anzeigen.
10. Nutzer prüft Phase-9-Ergebnis; **kein** automatisches Script Lock.

Kein automatischer Start durch Streamlit-Rerun. Keine automatische Stock-Suche.

---

## 11. Nutzerbearbeitungen

- Jede Nutzerbearbeitung erzeugt eine **neue Version** (Brief oder Script).
- LLM-Ausgabe wird nicht still überschrieben; originale Modellantwort bleibt historisch
  (`source_kind=llm`, Attempt-JSON erhalten).
- Geänderte Sätze: neue Script-Version; Sentence-IDs neu oder stabil mit
  `supersedes`/`derived_from` — **Implementierungsentscheidung:** bevorzugtes Modell =
  neue IDs je Script-Version + Mapping-Tabelle optional; Ordinals neu aufbauen.
- Abhängige Artefakte werden stale (Matrix §9).
- Versionsdiff in UI (Text-Diff + Metadaten: version, source_kind, timestamps).
- Kein Merge nach reinem Textvergleich ohne Identitätsregeln.

---

## 12. Coverage Audit — zwei Ebenen

### 12.1 Redaktionelle Bewertung (FakeText / später LLM)

Motivpassung, Setting, Aktion, lokale Spezifität, geografische Unsicherheit,
generisches Stockrisiko, Motivwiederholung, mögliche Synthetic-Hinweise.

### 12.2 Deterministische Prüfung (Python, verpflichtend)

- referenzierte `asset_id` existiert im Projekt
- accepted Observation existiert und ist aktuell editorial-ready
- Asset gehört zum Projekt; Working-Media-/Analysis-Identität aktuell
- keine historische Observation
- keine doppelte Kandidatenreferenz je Intent (innerhalb eines Audits)
- Coverage-Zähler (Anzahlen je `coverage_status`)
- nur erlaubte terminale Statuswerte

**Keine** Stock-Kandidaten erzeugen. `recommended_next_action` darf auf Phase-10-
Eskalationsschritte verweisen (Text), ohne sie auszuführen.

---

## 13. Fehlercodes

| Code | Bedeutung |
|---|---|
| `project_brief_missing` | kein aktiver Brief |
| `project_brief_invalid` | Schema/Validierung |
| `editorial_gateway_unconfigured` | Text-Gateway/Config fehlt |
| `editorial_model_unavailable` | Adapter/Provider nicht nutzbar |
| `editorial_consent_required` | später bei externem Textprovider |
| `editorial_input_stale` | Brief/Obs/Script-Bindung veraltet |
| `editorial_response_invalid` | Antwort nicht parsebar |
| `editorial_response_schema_mismatch` | Schema-Version/Felder |
| `invalid_observation_reference` | nicht accepted/aktuell |
| `invalid_asset_reference` | unbekanntes/fremdes Asset |
| `invalid_sentence_reference` | |
| `invalid_visual_beat_reference` | |
| `invalid_visual_intent_reference` | |
| `coverage_audit_invalid` | |
| `editorial_retry_exhausted` | |
| `editorial_artifact_conflict` | Ziel existiert / Hash-Konflikt |
| `editorial_registry_write_failed` | |
| `editorial_artifact_write_failed` | |
| `worker_interrupted` | Orphan-Recovery |
| `report_write_failed` | |

---

## 14. Jobs und Recovery

| Thema | Plan |
|---|---|
| Launcher | `EditorialJobLauncher` (Analog Analysis) — ein Thread-Slot je Projekt |
| Scopes | `editorial_narrative_only`, `editorial_script_only`, `editorial_coverage_only` |
| Aktive Runs | maximal **ein** aktiver Editorial-Run pro Projekt (alle Scopes gegenseitig) |
| Parallelität Assetanalyse | **erlaubt**, sofern keine gemeinsamen Schreibziele; Empfehlung: Application-Gate warnt bei aktivem Model-Analysis-Run, blockiert nicht hart — **Entscheidung §24** |
| Orphan-Recovery | DB queued/running + Launcher inaktiv → `failed`/`interrupted` + `worker_interrupted` |
| Temp-Cleanup | nur `editorial/temp/<run_id>/` des eigenen Runs |
| Veröffentlichte JSON | bleiben erhalten |
| Mid-Request-Fortsetzung | nein |
| Neustart | idempotent über Attempt-Cache / Fingerprints |
| Fehler | Asset-/Intent-Einzelfehler vs. Run-Fail analog Phase 8 (Coverage: Run completed mit Intent-Ergebnissen; Gateway-Fail → Run failed) |

Worker: `otio_app/discovery_v2/jobs/editorial_*_worker.py` (ein Worker mit Scope-Switch
oder drei schlanke Worker — Implementierungsdetail, ein Launcher).

---

## 15. UI-Plan

**Neue Discovery-Seite** empfohlen: z. B. Navigationstitel **„Editorial“** /
**„Redaktion“** unter `otio_app/discovery_v2/ui/editorial_page.py`.

Keine Fachlogik in Streamlit; nur Application-Services; Buttons starten Jobs.

### Anzeigen

- Project Brief (Form + Version)
- Narrative Plan
- Hook-Varianten + Auswahl
- Script Draft + Versionsdiff
- Claims, Sätze, Visual Beats, Visual Intents
- Coverage-Status je Intent + Zähler
- lokale Kandidaten-Asset-IDs (Metadaten aus Registry-Views)
- akzeptierte Observation-Referenzen
- Unsicherheiten / Stale-Banner
- Runhistorie

### Beim Rendering verboten

- Gateway-/API-Aufruf
- Medien-I/O, Hashing, FFmpeg
- automatischer Jobstart
- direkte SQLite-Abfrage

---

## 16. Fake-End-to-End-Smokes (A–H)

| ID | Inhalt |
|---|---|
| **A** | Brief → Fake Narrative → 3 Hooks → Auswahl → Fake Script → Sentences/Claims/Beats/Intents → Coverage |
| **B** | Nutzer editiert Skript → neue Version; alte bleibt; Abhängige stale; erneuter Coverage nötig |
| **C** | unreviewed Observation darf nicht als Editorial-Input dienen |
| **D** | stale Observation blockiert/ausschließt Coverage-Input |
| **E** | `not_covered` ohne Stock-Suche; `recommended_next_action` für Phase 10 sichtbar |
| **F** | Source Group erzeugt weder Kapitel noch Beat allein |
| **G** | Orphan-Editorial-Run → controlled fail; veröffentlichte Artefakte bleiben |
| **H** | UI-No-I/O: kein Gateway/Jobstart beim Rendering |

---

## 17. Testplan (Gruppen / Node-Ideen)

1. Schema-Migration 14→15 + Historie (Brief/Script supersede)
2. Domaintrennung (keine Kapitel aus Source Group; Shot≠Beat)
3. Gateway + FakeTextAdapter; keine Providerimporte in UI/Domain
4. keine hart codierten Modelle in Fachmodulen
5. Project-Brief-Versionierung / Stale-Kaskade
6. Hook-Auswahl (ein selected; andere bleiben)
7. Script-Versionierung + Nutzerbearbeitung
8. Satz-/Claim-/Beat-/Intent-Referenzintegrität
9. nur accepted+aktuelle Observations als Analysequelle
10. Coverage-Kategorien + deterministische Validierung
11. keine Stock-Suche / keine Phase-10-Side-Effects
12. Stale-Matrix-Fälle
13. Cache + Retry + `editorial_retry_exhausted`
14. Orphan-Recovery
15. UI-No-I/O
16. Classic-/Without-VO-Navigation unverändert
17. `_otio_v2`-Isolation / kein `_otio`-Write
18. Fake-E2E Smokes A–H

Keine übergroße kombinatorische Matrix; gezielte Nodes + E2E A–H.

---

## 18. Implementierungsaufteilung

### Bevorzugte Bündelung — ein großer Phase-9-Auftrag

```text
Contracts + Schema 15
+ Fake Text Gateway
+ Project Brief Service/UI
+ Narrative / Hook / Script (Fake)
+ Sentences / Claims / Beats / Intents Persistenz
+ Coverage Audit (redaktionell Fake + deterministisch)
+ MANUAL Editorial UI
+ Recovery
+ Tests + Fake-E2E A–H
```

### Nur bei Sicherheitsgrund unterteilen

| Schnitt | Grund |
|---|---|
| 9A Contracts+Schema+Brief (ohne LLM) | falls Schema-Review isoliert gefordert |
| 9B Fake Gateway + Narrative/Hook/Script | LLM-Pfad |
| 9C Coverage + UI-Feinschliff | Audit-Gate |

**Default:** ein Makroauftrag.
**Echter Textprovider:** immer separates Gate nach Phase-9-Fake-APPROVED.

---

## 19. Modulvorschlag (Implementierungsskizze)

```text
otio_app/discovery_v2/
  domain/editorial.py                 # Pydantic-Verträge
  domain/editorial_errors.py          # Fehlercodes
  adapters/text_gateway.py
  adapters/text_fake.py
  adapters/text_config.py
  adapters/editorial_job_launcher.py
  application/project_brief_service.py
  application/narrative_service.py
  application/script_service.py
  application/coverage_service.py
  application/editorial_job_recovery.py
  persistence/editorial_repository.py
  editorial_paths.py
  jobs/editorial_worker.py
  ui/editorial_page.py
```

Routing/Navigation: additiv Discovery-Seitenliste erweitern; Classic unverändert.

---

## 20. Entscheidungen (zur Freigabe im Implementierungsauftrag)

| # | Thema | Planvorschlag |
|---|---|---|
| E1 | Text-Gateway | neuer DiscoveryTextGateway + FakeTextAdapter |
| E2 | Schema | 14 → **15**; Tabellen §7 |
| E3 | Fake-Ausgabeformat | strikte JSON-Objekte je `response_schema_version`; `extra=forbid` |
| E4 | Hook-Anzahl Fake | genau 3 Varianten |
| E5 | Script+Beats | ein Script-Run liefert Sentences/Claims/Beats/Intents zusammen (ein Attempt-Kind `script_bundle`) |
| E6 | Sentence-IDs bei User-Edit | neue IDs je neuer Script-Version |
| E7 | Coverage-Kandidatenlimit | max. **5** Asset-IDs je Intent (deterministisch gekappt) |
| E8 | Parallelität Analyse | warnen, nicht hart blockieren |
| E9 | UI | neue Seite „Editorial“ |
| E10 | `editorial_user_decisions` | zunächst **ohne** separate Tabelle; Decisions in Entity-Status + Run-Reports; bei Bedarf nachziehen |
| E11 | Kapitelmodell | **nicht** in Phase 9 |
| E12 | Sprache | Brief.`language` steuert Ausgabe; Mehrsprachigkeit jenseits Projekt-Sprache = UNKNOWN/out of scope Alpha |

---

## 21. UNKNOWN-Punkte

- produktive Text-Modell-IDs und Token-/Kostenlimits für reale Provider
- ob externer Textprovider Consent analog Vision braucht (Codepfad vorbereiten)
- Coverage-Konfidenz-Schwellwerte für UI-Ampeln (Fake kann feste Werte liefern)
- Claim-Evidenzgewichtungen jenseits Status-Enums
- exakte Diff-Darstellung (Inline vs. Side-by-Side) — UX-Detail
- Adobe/Stock — bewusst Phase 10; OAuth-Variante UNKNOWN
- Mehrsprachigkeit / Übersetzungsflows

---

## 22. DoD für späteren Implementierungsauftrag

- Schema 15 + Fake-Text-E2E A–H grün
- nur `_otio_v2/editorial/` Writes
- keine Phase-10/Voice/Lock/OTIO-Funktionen
- keine echten Provideraufrufe
- UI-No-I/O
- Classic/Without-VO unverändert
- Vollsuite: keine neuen Discovery-bedingten Failures; Baseline-18 unangetastet ohne Auftrag
- Handoff/Progress/Manifest nach Implementierung aktualisieren; Decisions nur bei echten Fachfreigaben

---

## 23. Nächste erlaubte Aktion

Nach **Freigabe dieses Plans** (`APPROVED` / `APPROVED_WITH_CONDITIONS`):

→ Phase-9-**Implementierungs**-Makroauftrag gemäß §18.

Gesperrt bis eigene Gates: echte Textprovider, Phase 10+, Voice, OTIO.
