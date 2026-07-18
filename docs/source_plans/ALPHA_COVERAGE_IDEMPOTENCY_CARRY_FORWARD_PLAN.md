# Alpha — Coverage Idempotency & Carry-Forward Plan

**Auftrags-ID:** `DISCOVERY-V2-ALPHA-COVERAGE-IDEMPOTENCY-CARRY-FORWARD-PLAN-001`  
**Status:** PLANNING ONLY — keine Produkt- oder Testimplementierung  
**Basis-HEAD:** `8e9a02c88057e5cc6f0ebc2ce7c193ec3d56621e`  
**Branch:** `cursor/discovery-v2-integration` · PR `#69`  
**Registry-Schema:** **20** (bleibt 20; keine Migration in diesem Plan)  
**SoT-Rang:** `docs/source_plans/*` — nachrangig; überschreibt keine höheren Dokumente  
**Vorgänger:** `PHASE9_EDITORIAL_CORE_COVERAGE_PLAN.md`, `PHASE10_SUPPLEMENTATION_SCRIPT_LOCK_PLAN.md`, R1.1/R1.3

---

## 1. Ausgangslage

Im manuellen Alpha-Test (Editorial / Coverage / Script Lock) gehen bei einem
erneuten Coverage-Lauf mit **fachlich identischen Inputs** Gap-Fortschritt und
Nutzerentscheidungen verloren:

| Beobachtung | Wirkung |
|---|---|
| Coverage-Risiken entschieden / Gaps eskaliert | Fortschritt vorhanden |
| Coverage erneut berechnet (manuell oder nach Review) | neuer completed Audit |
| Gleicher Observation-/Script-/Modellstand | trotzdem **neue Audit-ID** |
| Alte Gaps | `status = superseded` |
| Neue Gaps für dieselben Visual Intents | neue Gap-IDs, `in_progress`, `local_deeper_review`, `user_decision = NULL` |
| Script Lock | zeigt erneut offene Gaps |

**Korrekte Invalidierung** bei geändertem Script/Intent/Observation/Risk bleibt
erforderlich. Der Fehler ist der Reset bei **exakt gleichen** Coverage Inputs.

**Visual Edit Rework V1–V3:** implementiert. V3-Realtest erreicht ehrlichen
`additional_coverage_required`. End-to-End ist derzeit durch diesen
Coverage-Reset blockiert.

**R1.4 / V4:** weiterhin gesperrt. Keine Registry-Reparatur am Nutzerprojekt.

---

## 2. Reproduzierter Registry-Zustand (read-only)

Nutzerartefakte wurden ausschließlich read-only ausgewertet und sind **keine**
Implementierungsabhängigkeit. Spätere Fixtures müssen synthetisch und
deterministisch unter `tests/` entstehen.

### 2.1 Editorial-Stand

| Feld | Wert |
|---|---|
| `project_id` | `4e364f0c-9a6d-462c-b336-df9314f585ca` |
| `active_script_id` | `0fa95aec-d26a-4bfe-9b1b-c996d480ef5f` |
| `script_version` | `2` |
| `active_coverage_audit_id` | `211d6cee-3bca-50a5-b339-6457844b9dbe` |
| `observation_fingerprint` | `c2d08cb1a6b8a4a5ce4cc944e77962003c5ca64defcc75e6a9fbb00e357d0a8d` |

### 2.2 Zwei fachlich gleichwertige Audits

| | Audit A | Audit B (Current) |
|---|---|---|
| `coverage_audit_id` | `969e015d-a89d-5c57-a238-b75342a38197` | `211d6cee-3bca-50a5-b339-6457844b9dbe` |
| `project_id` | gleich | gleich |
| `script_id` / `script_version` | gleich / 2 | gleich / 2 |
| `brief_version` | gleich | gleich |
| `narrative_plan_id` | gleich | gleich |
| `input_observation_fingerprint` | gleich | gleich |
| provider / model / gateway / prompt / schema | gleich | gleich |
| `status` | `completed` | `completed` |

### 2.3 Gap-Reset

Audit A Gaps (teilweise eskaliert, u. a. `user_decision` / `photo`):

- Visual Intent `202ea69d-db61-54b7-a3dd-5ea9cd7ea0fd`
- Visual Intent `72e412e3-24e6-5caf-a2ce-ceb6c04e146b`
- Visual Intent `760e427f-84de-5acb-9a52-ee8fcc582e3b`

Nach Audit B: alte Gaps `superseded`; neue Gap-IDs

- `3a1365b0-45ab-49fe-8340-2eb731301497`
- `cea60e7f-fb90-4051-bc8b-6a0c62262afb`
- `dcd0bacb-deab-4ecb-a5a0-d4c512a1b14a`

mit `status = in_progress`, `current_escalation_step = local_deeper_review`,
`user_decision = NULL`.

---

## 3. Korrekte Invalidierung versus fehlerhafter Reset

| Fall | Erwartung |
|---|---|
| Anderes Script / Script-Inhalt / Structure | neuer Audit erlaubt; alte Decisions **nicht** blind übernehmen |
| Anderer Visual Intent / Intent-Semantik | neuer Gap; keine Fremdübernahme |
| Anderer Observation Fingerprint | Neuberechnung; alte Gap-Entscheidungen invalid |
| Erweitertes / geändertes Risk Set | keine alte Risikoannahme |
| **Identischer kanonischer Coverage Input** | **kein** neuer fachlicher Audit; **keine** neuen Gap-IDs; Fortschritt erhalten |

Entscheidungen aus Script Version 1 dürfen nicht automatisch auf einen
inhaltlich neuen Script-Version-2-Stand übertragen werden.

---

## 4. Untersuchte Produktbereiche

| Bereich | Primärdateien |
|---|---|
| Fake Coverage Adapter | `otio_app/discovery_v2/adapters/text_fake.py` (`_coverage`) |
| Coverage Worker | `otio_app/discovery_v2/jobs/editorial_worker.py` (`_process_coverage`) |
| Coverage Start | `application/editorial_service.py` (`start_coverage_run`) |
| Gap Materialisierung | `application/coverage_gap_service.py` |
| Gap Supersede | `persistence/supplementation_repository.py` (`supersede_gaps_not_in_audit`) |
| Auto-Revalidation | `application/coverage_revalidation_service.py` |
| Observation Review Trigger | `application/observation_review_service.py` |
| Script Lock Gate | `application/script_lock_service.py` |
| Domain Fingerprints | `domain/editorial.py`, `domain/supplementation.py` |
| UI | `ui/editorial_page.py` |
| Bestehende Tests | `tests/test_discovery_v2_alpha_r1_1_blockers.py`, `…_r1_3_review_analysis.py`, Coverage-/Script-Lock-Tests |

Classic / Without-VO: nur read-only.

---

## 5. Root-Cause-Matrix

| Hypothese | Befund | Genauigkeit |
|---|---|---|
| **A** Kein kanonischer Coverage-Input-Fingerprint | **BESTÄTIGT** | Persistiert wird nur `input_observation_fingerprint` (= Observation-Set-Hash). Kein zentraler Hash über Script-Inhalt, Structure, Intent-Semantik, Brief/Narrative-Identität und Provider-/Prompt-/Schema-Vertrag. |
| **B** Audit-ID enthält Run-Identität; Reuse-Lookup fehlt | **BESTÄTIGT** | Fake: `audit_id = uuid5(project, script_id, input_fingerprint, **run_id**)`. Worker reuse nur bei exakter `coverage_audit_id`-Trefferquote → neuer `run_id` ⇒ neuer Audit. R1.1 wollte UNIQUE-Kollisionen vermeiden; fachliche Idempotenz fehlt. |
| **C** Gap-Materialisierung supersedet zu früh | **BESTÄTIGT** | `materialize_gaps_from_current_coverage`: wenn Active Audit noch keine Gaps hat → `supersede_gaps_not_in_audit` **vor** semantischem Vergleich / Carry-Forward → sofort neue UUID4-Gaps. |
| **D** Gap-Identität hängt an Audit-ID | **TEILWEISE** | `gap_id` ist UUID4 (nicht aus Audit abgeleitet). Reuse ist aber **audit-scoped** (`list_gaps_for_audit`); neuer Audit ⇒ neue Gaps. Lock-Risiken sind `gap_id:risk_code`. |
| **E** Auto + manuell deduplizieren nicht gemeinsam | **TEILWEISE** | Beide rufen `start_coverage_run` auf und teilen Active-Run-Schutz. Nach `completed` fehlt Input-Dedup → zweiter gleichwertiger Run möglich. |
| **F** Rerun / Doppelklick | **TEILWEISE** | Active-Run und Launcher blockieren Parallelstarts. Nach Completion erzeugen erneuter Button / Auto+Manuell einen zweiten completed Audit. |

### 5.1 Exakte Root Cause der äquivalenten Audits

1. R1.1: Fake inkludiert `run_id` in der Audit-UUID5, damit wiederholte Runs nicht
   auf `coverage_audits.coverage_audit_id` UNIQUE kollidieren
   (`text_fake.py`, Kommentar + `_id(..., request.run_id)`).
2. Jeder Coverage-Run erhält eine neue Editorial-`run_id`.
3. `_process_coverage` prüft Reuse nur via
   `get_coverage_audit(coverage_audit_id=audit.coverage_audit_id)`.
4. Lookup miss → neuer completed Audit wird persistiert und Current gesetzt —
   obwohl Script/Observation/Provider-Felder identisch sind.

Technische Run-Identität und fachliche Audit-Äquivalenz sind nicht getrennt.

### 5.2 Exakte Root Cause der neuen Gap-IDs

1. Current zeigt auf Audit B (neue ID).
2. `materialize_gaps_from_current_coverage` findet keine Gaps für Audit B.
3. `supersede_gaps_not_in_audit` markiert alle Gaps mit anderer `coverage_audit_id`
   als `superseded`.
4. Neue Gaps: `gap_id = uuid4()`, Eskalation zurück auf Startsequenz,
   `user_decision = NULL`.
5. Events/Candidate Decisions/accepted_unresolved hängen an alten `gap_id`s →
   für den Current-Stand unsichtbar.
6. Script Lock prüft Gaps des Active Audits → offene Gaps erneut.

### 5.3 Automatische versus manuelle Trigger

| Trigger | Pfad |
|---|---|
| Manuell | UI „Coverage prüfen“ → `start_coverage_run` |
| Auto nach Accepted Review | `observation_review_service` → `revalidate_coverage_after_accepted_reviews` → `start_coverage_run` |
| Script-Lock Preview | materialisiert Gaps; startet keinen Coverage-Run |
| Recovery/Retry | neuer Editorial Run möglich |

Gemeinsam: Active-Run-Gate. Fehlend: completed-equivalent Dedup vor Gateway.

---

## 6. Canonical Coverage Input

### 6.1 Neuer zentraler Fingerprint (C2)

`canonical_coverage_input_fingerprint` = stabiler SHA-256 über sortierte,
kanonische JSON-Payload mit mindestens:

| Bestandteil | Quelle (heute) |
|---|---|
| `project_id` | Run / State |
| Brief identity/content | Active Brief + content hash |
| Narrative identity/content | Active Narrative Plan + content hash |
| Selected hook | Active selected hook identity |
| Script identity + version | `script_id`, `script_version` |
| Script content fingerprint | `script_drafts.content_sha256` / Bundle-Hash |
| Structure fingerprint | Structure/Sentences/Claims/Beats Bundle |
| Visual-intent semantic fingerprint | Intent-IDs + gewünschte Motive/Constraints (sortiert) |
| Observation fingerprint | bestehendes `compute_observation_set_fingerprint` |
| Provider / model / gateway / prompt / response schema | Text-Config / Request |

**Nicht** Bestandteil: UI-Felder, Streamlit-Session, Editorial `run_id`,
zufällige Attempt-IDs, Timestamps.

### 6.2 Trennung Run vs. Audit

| Identität | Rolle |
|---|---|
| `editorial_run_id` | technische Ausführung (UUID, darf neu sein) |
| `coverage_audit_id` | persistierte Audit-Zeile (darf technisch neu sein, wenn nötig) |
| `canonical_coverage_input_fingerprint` | **fachliche** Äquivalenz / Dedup / Reuse |

R1.1-UNIQUE-Schutz bleibt: technische Audit-IDs dürfen run-eindeutig bleiben.
**Reuse** erfolgt über Lookup auf den kanonischen Fingerprint / Äquivalenzfelder,
nicht über Kollision derselben Audit-ID.

---

## 7. Audit-Idempotenzvertrag

### Fall A — identische fachliche Inputs

```
gleicher canonical_coverage_input_fingerprint
+ completed Current (oder completed equivalent) Audit vorhanden
```

**Erwartung:**

- bestehenden Audit wiederverwenden (Current bleibt / wird bestätigt)
- bestehende Gap-IDs erhalten
- Gap Events, Eskalation, Candidate Decisions, User Decisions,
  `accepted_unresolved` erhalten
- **kein** Supersede
- **kein** neuer Gatewayaufruf
- Run höchstens als `reused` / completed-with-reuse protokollieren

### Fall B — aktiver Run mit gleichem Dedup-Key

`queued`/`running` mit gleichem Key → keinen zweiten Run; bestehende Run-ID
zurückgeben (`editorial_run_already_active` oder semantisches Reuse-Resultat).

### Fall C — letzter Run fehlgeschlagen, gültiger Current Audit

- Current Audit und Gaps nicht beschädigen (bestehender R1.1-Vertrag)
- expliziter Retry darf neuen technischen Run erzeugen
- Current bleibt bis erfolgreichem Abschluss unverändert
- bei Retry mit **identischem** Input und gültigem Current: Fall A bevorzugen
  (kein Gateway, kein neuer Audit)

### Fall D — fachlich geänderte Inputs

Bei geändertem Script-Inhalt, Structure, Visual Intent, Observation Fingerprint
oder Provider-/Prompt-/Schema-Vertrag:

- neuer Audit erlaubt
- User Decisions **nicht** blind übernehmen
- Carry-Forward nur nach Matrix §9

---

## 8. Run-Deduplication

### 8.1 Gemeinsamer Key

```
coverage_run_dedup_key =
  project_id
  + canonical_coverage_input_fingerprint
  + scope(=editorial_coverage_only)
  + mode
```

`mode ∈ {normal, retry_failed, force_recompute}`

Gilt für:

- manuellen Coverage-Button
- automatische Revalidation nach Observation Review
- Supplement-Revalidation (falls Coverage startet)
- Recovery / Retry
- kontrollierten UI-Rerun

### 8.2 force_recompute

In Alpha **nicht** still durch normale UI-Aktionen. Nur expliziter,
gesonderter Vertrag (später / deferred), sonst immer `normal`.

### 8.3 Lookup vor Gateway

Vor `DiscoveryTextGateway.generate` für Coverage:

1. Active Run mit gleichem Dedup-Key? → zurückgeben  
2. Completed equivalent Audit (Schema-20-Felder / kanonischer FP)? → reuse, kein Gateway  
3. Sonst: neuer technischer Run + Gateway

Bestehende `find_completed_editorial_attempt` ist ungenutzt und kann als
Anker geprüft werden; primär reicht Lookup auf `coverage_audits` + Script-Hashes.

---

## 9. Stabiler Gap-Schlüssel

### 9.1 Semantischer Key (C3)

```
coverage_gap_key = sha256(
  project_id
  + script_or_structure_fingerprint
  + visual_intent_semantic_fingerprint
  + coverage_result_signature   # level + sorted missing_properties + sorted risk_flags
  + matched_asset_identity_signature  # optional, sortiert
)
```

Anforderungen:

- zentral erzeugt, sortierungsstabil
- **nicht** aus `coverage_audit_id` allein
- **nicht** aus zufälliger `gap_id`
- keine UI-Stringbildung
- geänderte Risiken / Missing Properties ⇒ anderer Schlüssel

### 9.2 Schema-20-Bewertung

| Option | Bewertung |
|---|---|
| Key nur zur Laufzeit ableiten | **bevorzugt für C2 Reuse-Pfad**: bei identischem Input Gaps des reused Audits behalten — kein Key persistieren nötig |
| Key in Gap-Zeile (neue Spalte) | Schema 21 → **BLOCKED** ohne Freigabe |
| Key in Sidecar JSON unter `_otio_v2/` | möglich für Carry-Forward bei zwingend neuem Audit; versioniert |

**R1-Empfehlung:** C2 macht Carry-Forward in den meisten Alpha-Fällen überflüssig
(Reuse). C3 implementiert den Key zuerst als **abgeleitete Laufzeitfunktion**;
persistiertes Sidecar nur wenn ein neuer Audit trotz gleicher Intent-Signatur
technisch nötig ist.

Bestehende `coverage_gap_fingerprint` (Lock) enthält `coverage_audit_id` +
`gap_id` und ist **kein** stabiler semantischer Gap-Key — nicht wiederverwenden
für Carry-Forward-Identität.

---

## 10. Carry-Forward-Matrix

### 10.1 Identischer Coverage Input (bevorzugt)

Keine Kopie nötig → bestehende Gaps des reused Audits **direkt** wiederverwenden.

### 10.2 Neuer Audit mit semantisch identischem Gap

Nur wenn neuer Audit zwingend (Fall D / technische Notwendigkeit):

| Darf übernommen werden (konservativ) | Darf **nicht** automatisch |
|---|---|
| Eskalationshistorie / `prior_attempt_summaries` bei gleichem `coverage_gap_key` | Entscheidung für anderen Visual Intent |
| Abgelehnte Stock Candidates, wenn Candidate+Intent+Key unverändert | Entscheidung nach geändertem Script-Inhalt |
| `user_decision` / `accepted_unresolved` nur bei **vollständiger** Gap-Signatur-Gleichheit inkl. Risk Set | Entscheidung bei erweitertem/geändertem Risk Set |
| | Entscheidung bei geändertem geo-/Authentizitätsrisiko |
| | Entscheidung für ungültig gewordenes Asset |
| | Lock-Bestätigung eines alten Fingerprints / alter `gap_id` |

Default bei Unsicherheit: **nicht übernehmen**, neuen Gap mit klarer UI-Erklärung.

---

## 11. Supersede-Vertrag

Gaps dürfen erst `superseded` werden, wenn **alle** gelten:

1. fachlich **neuer** Audit erfolgreich erzeugt (nicht äquivalent)
2. Intent Results validiert
3. neue Gaps vollständig persistiert (falls nötig)
4. Artefakte veröffentlicht
5. Current Audit atomar umschaltbar

Bei Fehler:

- alter Audit bleibt Current
- alte Gaps bleiben aktuell
- keine teilweise Supersede-Kette

Bei identischem Input: **kein Supersede**.

Historische Gaps/Events bleiben unverändert erhalten (append-only Historie).

---

## 12. Atomarität und Recovery

| Grenze | Vertrag |
|---|---|
| Vor Gateway (Reuse) | kein neuer Audit, Current unverändert |
| Nach Gateway, vor Persist | Current unverändert (R1.1 bereits) |
| Gap-Persist scheitert | kein Current-Wechsel; keine Supersede der Alten |
| Erfolgreicher neuer Audit | Current + Gaps + Pointer in einer IMMEDIATE-Transaktion |
| Recovery | gültiger Current nie durch historischen Failed-Run blockiert |

Keine verwaiste `active_coverage_audit_id`.

---

## 13. UI-Vertrag (minimal)

### Reuse

> Coverage ist bereits aktuell.  
> Bestehender Audit und Gap-Entscheidungen wurden wiederverwendet.

Nicht: „neuer Audit erzeugt“.

### Recompute

> Coverage wurde neu berechnet, weil sich `<konkreter Input>` geändert hat.  
> Frühere Gap-Entscheidungen gelten für den alten Stand.

Anzeigen (gekürzt):

- aktueller Audit
- Coverage-Input-Fingerprint
- Grund Reuse vs. Neuberechnung
- Anzahl erhaltener Gap-Entscheidungen
- Anzahl neuer/geänderter Gaps

Keine allgemeine UX-Revision.

---

## 14. Fehlercodes

Bestehende Codes bevorzugen:

| Bedarf | Bevorzugter Anker |
|---|---|
| Aktiver Run | `editorial_run_already_active` |
| Current-Update | `coverage_current_state_update_failed` |
| Artifact/Persist | `coverage_artifact_publish_failed` / `coverage_audit_persist_failed` |
| Gap fehlt/stale | `coverage_gap_missing` / `coverage_gap_stale` |

Falls zwingend nötig (Domain-Tuple, **kein** Schema-Bump):

- `coverage_equivalent_audit_found` (Application-Outcome / Message OK ohne neuen Code)
- `coverage_gap_identity_conflict`
- `coverage_gap_carry_forward_unsafe`
- `coverage_supersede_failed`
- `coverage_input_fingerprint_mismatch`

Kein stilles Wegwerfen von Entscheidungen.

---

## 15. Schemaentscheidung

**`REGISTRY_SCHEMA_VERSION` bleibt 20.**

Ausreichend für C2 (primärer Fix):

- `coverage_audits` Spalten (project/script/version/brief/narrative/obs-fp/provider/…)
- `script_drafts.content_sha256` / Bundle-Hashes
- Gap Events, Candidate Decisions, existing gap rows
- JSON-Artefakte unter `_otio_v2/`
- abgeleiteter kanonischer Fingerprint zur Laufzeit
- Run-Report-Felder (`reused: true`)

**Nicht nötig für Reuse-Pfad:** neue Spalte an `coverage_gaps`.

Falls später persistierter `coverage_gap_key` oder Unique-Index auf
Äquivalenz-Audit gewünscht wird:

| Bedarf | Migration |
|---|---|
| Spalte `canonical_input_fingerprint` an `coverage_audits` | Schema 21 |
| Spalte `coverage_gap_key` an `coverage_gaps` | Schema 21 |
| Unique Index equivalent audits | Schema 21 |

→ Umsetzungsschritt als **BLOCKED** markieren bis Schema-21-Freigabe; Sidecar
oder Laufzeit-Ableitung bevorzugen.

---

## 16. Testmatrix (Vorschlag)

Neue Datei: `tests/test_discovery_v2_coverage_idempotency_c1.py` (C1)  
Folge: `…_c2.py`, `…_c3.py`, `…_c4.py` oder eine Suite mit Markern.

### 16.1 Audit-Idempotenz

| Node-ID (Vorschlag) | Assert |
|---|---|
| `test_identical_coverage_reuses_current_audit` | gleiche `coverage_audit_id` |
| `test_identical_second_call_skips_gateway` | Fake/Gateway Call Count = 0 |
| `test_identical_second_call_keeps_gap_ids` | Gap-ID-Set unverändert |
| `test_active_identical_run_is_reused` | gleiche `run_id` |
| `test_ui_rerun_does_not_start_second_coverage_run` | kein zweiter Start |
| `test_auto_and_manual_share_dedup_key` | genau ein Run |

### 16.2 Gap-Erhalt

| Node-ID | Assert |
|---|---|
| `test_escalation_preserved_on_identical_recompute` | `current_escalation_step` |
| `test_candidate_decisions_preserved` | Revisionsstand |
| `test_accepted_unresolved_preserved` | Status + risks |
| `test_gap_events_unchanged` | Event-IDs |
| `test_script_lock_risk_keys_stable` | `gap_id:risk_code` |
| `test_no_unnecessary_supersede` | keine neuen SUPERSEDED Events |

### 16.3 Sichere Invalidierung

| Node-ID | Assert |
|---|---|
| `test_script_content_change_creates_new_audit` | neue Audit-ID |
| `test_visual_intent_change_creates_new_gap` | neuer Gap |
| `test_changed_risk_set_does_not_carry_accept` | keine Übernahme |
| `test_observation_fingerprint_change_recomputation` | klarer Recompute |
| `test_old_lock_confirmation_not_transferred` | Preview blockiert |

### 16.4 Carry Forward

| Node-ID | Assert |
|---|---|
| `test_semantic_identical_gap_may_carry_progress` | nur bei zwingendem neuem Audit |
| `test_different_gap_signature_blocks_carry_forward` | blockiert |
| `test_changed_target_assets_invalidate_candidate_decisions` | invalid |
| `test_unchanged_rejected_candidates_may_persist` | Vertrag |

### 16.5 Atomarität / Isolation

| Node-ID | Assert |
|---|---|
| `test_gap_persist_failure_keeps_old_current` | alter Audit/Gaps |
| `test_failed_new_gaps_do_not_supersede_old` | keine Supersede |
| `test_successful_new_audit_atomic_current` | Current atomar |
| `test_schema_remains_20` | Schema 20 |
| `test_no_otio_write_classic_untouched` | Isolation |

---

## 17. Pflicht-Smokes (spätere Umsetzung)

| Smoke | Node-ID (Vorschlag) | Erwartung |
|---|---|---|
| **A** Identischer Aufruf | `test_smoke_a_identical_coverage_reuses_audit_and_gaps` | Audit reused, gleiche Gap-ID, gleiche Eskalation |
| **B** accepted_unresolved | `test_smoke_b_accepted_unresolved_survives_identical_recompute` | Status bleibt; Lock möglich |
| **C** Auto + manuell | `test_smoke_c_auto_plus_manual_single_run` | genau ein Run |
| **D** Scriptänderung | `test_smoke_d_script_change_new_audit_no_blind_decision` | neuer Audit; Hinweis |
| **E** Risk Set | `test_smoke_e_expanded_risk_set_no_old_accept` | keine Übernahme |
| **F** atomarer Fehler | `test_smoke_f_gap_persist_failure_preserves_current` | alter Current bleibt |

---

## 18. Implementierungsreihenfolge C1–C4

| Schritt | Inhalt | Gate |
|---|---|---|
| **C1** Fixtures & Root Cause | Reproduktionsfixture (zwei äquivalente Audits → Gap-Reset); Auto/Manuell; Supersede-Zeitpunkt; Tests rot → grün nach Fixes | Freigabe |
| **C2** Canonical Input & Run Dedup | Fingerprint; gemeinsamer Dedup-Key; Reuse completed Audit; kein Gateway bei Identität | nach C1 |
| **C3** Gap Identity & Carry-Forward | stabiler Gap-Key; Matrix; konservative Invalidierung | nach C2 |
| **C4** Atomarität & UI-Status | Supersede erst nach Publikation; Reuse-/Recompute-Flash; Recovery | nach C3 |

Kein späterer Schritt vor getestetem und freigegebenem Vorgänger.
**V4 und R1.4 nicht vorziehen.**

---

## 19. Risiken und Einschränkungen

- R1.1-Test `test_r1_repeated_coverage_run_no_longer_unique_collision` prüft nur
  „keine UNIQUE-Kollision“, nicht Reuse — muss um Idempotenz-Asserts ergänzt
  oder durch C2-Tests ersetzt/geschärft werden.
- Fake Audit-ID mit `run_id` darf technisch bleiben; Lookup muss äquivalenten
  completed Audit finden **bevor** Gateway oder Current-Wechsel.
- Lock-Keys bleiben `gap_id:risk_code` — daher ist Gap-ID-Erhalt (C2) kritischer
  als Carry-Forward über neue IDs.
- Keine Nutzerregistry-Reparatur; Alpha-Projekte nach Fix neu coverage-laufen
  lassen bzw. bei Identität reuse.

---

## 20. Deferred Items

- `force_recompute` UI
- Schema-21 Spalten/Indexes
- V4 Loop-/UI-Schutz
- R1.4 Progress-Polling
- allgemeine Laienführungsrevision
- echte Provider
- Style References / Shared Working Media
- vidIQ

---

## 21. Abnahmekriterien dieses Plans

Der Plan ist abnahmefähig, weil er:

1. die beiden gleichwertigen Audits konkret erklärt,
2. technische Run-Identität und fachliche Audit-Identität trennt,
3. identische Coverage-Aufrufe idempotent macht (Vertrag Fall A),
4. Gap-IDs und Entscheidungen bei identischem Input erhält,
5. keine Decisions über geänderte Inputs hinweg blind übernimmt,
6. einen stabilen Gap-Schlüssel definiert,
7. automatische und manuelle Trigger gemeinsam dedupliziert,
8. Supersede- und Current-State-Atomarität festlegt,
9. Schema 20 bewertet (Reuse ohne Migration),
10. konkrete Tests und Smokes A–F enthält,
11. keine Registry-Reparatur am Nutzerprojekt verlangt,
12. V4 und R1.4 nicht vorzieht.

---

## 22. Nächste erlaubte Aktion nach Freigabe

→ **Coverage Stability C1** (Fixtures + reproduzierbarer Root-Cause-Test)

Danach C2–C4. **V4 und R1.4 bleiben gesperrt.**
