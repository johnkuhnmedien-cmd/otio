# ALPHA — Coverage Stability C3: Gap Identity & Carry-Forward

**Plan-ID:** `DISCOVERY-V2-ALPHA-COVERAGE-STABILITY-C3-GAP-IDENTITY-CARRY-FORWARD-PLAN-001`  
**Status:** Planung (keine Implementierung)  
**Schema-Ziel:** `REGISTRY_SCHEMA_VERSION = 20` (keine Migration in C3)  
**Branch / PR:** `cursor/discovery-v2-integration` · `#69`  
**Verwandt:** `ALPHA_COVERAGE_IDEMPOTENCY_CARRY_FORWARD_PLAN.md` (C1–C4 Roadmap); C2/C2-R1 abgenommen

---

## 1. Ausgangslage

### 1.1 Bestätigter Produktstand

| Baustein | Stand |
|---|---|
| C1 Coverage Reset Reproduktion | abgeschlossen |
| C2 Canonical Coverage Input + Completed/Active Reuse | abgenommen (USA_v2) |
| C2-R1 Legacy Fail-Closed | abgenommen |
| Schema | **20** |
| Testbaseline | 3043 / 3024 / 18 / 1 |

C2-Vertrag (nicht erneut lösen):

```
identischer Canonical Coverage Input
→ bestehenden Current Audit wiederverwenden
→ gleiche Gaps / Gap-IDs
→ kein Carry-Forward erforderlich
```

### 1.2 C3-Problemraum

C3 betrifft **ausschließlich**:

```
fachlich neuer Canonical Coverage Input
→ neuer Coverage Audit
→ neue Coverage Results
→ prüfen, ob einzelne neue Gaps semantische Nachfolger früherer Gaps sind
→ ausgewählte Historie konservativ und auditierbar fortführen oder referenzieren
```

### 1.3 Reales Referenzprojekt (read-only, keine Fixture-Kopie)

**USA_v2** (nicht als Testfixture verändern/kopieren):

| Feld | Wert |
|---|---|
| Audit | `c2b32d64-3961-53bf-ab85-391932a2bf43` |
| Run | `7bb2273b-96ca-4942-9924-6de34b29d471` |
| Gap `094f0390-cfb2-41e7-a19f-65ca4d583fb0` | `resolved_by_graphic_plan` |
| Gap `2c36238e-6a1e-44ee-ae00-0abf8f398acf` | `accepted_unresolved` |
| Gap `95a924fe-2995-4da7-80ff-d172cca3221b` | `in_progress` / `user_decision` |

Offener UI-Befund (nicht C3-Scope): Visual-Intent-ID wird teilweise als Gap-ID beschriftet → **C4 / separate UI-Korrektur**.

---

## 2. Abgrenzung zu C2 und C4

| Thema | Eigentümer |
|---|---|
| Identischer Canonical Input → Audit-/Gap-Reuse | **C2** (fertig) |
| Legacy-Audit ohne Fingerprint fail-closed | **C2-R1** (fertig) |
| Semantische Gap-Identität über Audits | **C3** (dieser Plan) |
| Carry-Forward ausgewählter Gap-Historie | **C3** |
| Atomare Supersede-/Current-Umschaltung, UI-Beschriftung | **C4** (gesperrt) |
| Visual Edit V4, R1.4 Polling | gesperrt |

C3 darf:

- C2-Reuse-Pfad **nicht** ersetzen oder verwässern
- bei identischem Canonical Input **keinen** Carry-Forward ausführen
- keine Schema-21-Migration ohne separate Freigabe implementieren
- keine UI-Gap-Beschriftung „reparieren“

---

## 3. Root Cause der aktuellen Gap-Identität

Belegt durch konkrete Dateien/Funktionen — keine Vermutung.

### 3.1 `gap_id`-Erzeugung

| | |
|---|---|
| Generator | `otio_app/discovery_v2/persistence/supplementation_repository.py` → `new_gap_id()` |
| Formel | `str(uuid4())` — **zufällig**, nicht deterministisch, nicht audit-/intent-stabil |
| Aufruf | `coverage_gap_service.materialize_gaps_from_current_coverage` setzt `gap_id=repo.new_gap_id()` |

### 3.2 Gap-Zeile ↔ Audit / Intent

Domain: `CoverageGap` in `otio_app/discovery_v2/domain/supplementation.py`.

| Feld | Quelle beim Materialisieren |
|---|---|
| `coverage_audit_id` | aktiver Audit (`editorial_project_state.active_coverage_audit_id`) |
| `script_id`, `script_version` | Audit |
| `visual_intent_id` | `CoverageIntentResult.visual_intent_id` |
| `coverage_level`, `risk_flags`, `missing_properties` | Result + `merge_gap_risk_flags` / `_split_coverage` |
| `gap_version` | `next_gap_version(project_id, visual_intent_id)` = `MAX+1` je Intent |
| `status`, Escalation, Decisions | initial / nach Nutzeraktionen |

DB: `UNIQUE (project_id, visual_intent_id, gap_version)` — Versionierung pro Intent, **keine** Vorgänger-FK.

### 3.3 Was bei jedem neuen Audit neu entsteht

Ablauf in `materialize_gaps_from_current_coverage`:

1. Wenn `list_gaps_for_audit(current_audit)` bereits Zeilen hat → early return (audit-idempotent).
2. Sonst in einer Transaktion:
   - `supersede_gaps_not_in_audit(project_id, coverage_audit_id)` — alle Gaps mit **anderem** Audit → `SUPERSEDED` + Event
   - für jedes nicht-voll-covered Result: **neue** UUID4-`gap_id`, neues `gap_version`, `MATERIALIZED`-Event

Folge: Nutzerentscheidungen, Eskalation, Candidate Decisions und Risk Keys der alten `gap_id` gelten **nicht** mehr als Current.

### 3.4 Gap Events und Candidate Decisions

| Artefakt | Persistenz | Schlüssel |
|---|---|---|
| `GapEvent` | `coverage_gap_events`, append-only (`append_gap_event`) | `event_id` (UUID4), FK `gap_id` |
| `CandidateDecision` | `stock_candidate_decisions`, append-only Revisions | `decision_id`; unique `(candidate_id, revision)`; Feld `gap_id` |
| Graphic Plan | `GraphicPlan` mit `gap_id` + `visual_intent_id` | `graphic_plan_id` |
| Eskalationshistorie | `current_escalation_step` + `prior_attempt_summaries` + Events | an `gap_id` |

### 3.5 Script-Lock-Risikoschlüssel

| | |
|---|---|
| Funktion | `make_lock_risk_confirmation_key(gap_id, risk_code)` |
| Format | `{gap_id}:{risk_code}` (`LOCK_RISK_CONFIRMATION_SEPARATOR = ":"`) |
| Quelle für Lock | `persisted_accepted_lock_risk_keys` nur aus `ACCEPTED_UNRESOLVED`-Gaps |
| Lock-Fingerprint | `script_lock_fingerprint(...)` inkl. `coverage_audit_id` + `coverage_gap_fingerprint` (enthält **`gap_id`**) |

`coverage_gap_fingerprint` in `domain/supplementation.py` ist **kein** stabiler semantischer Gap-Key (enthält `coverage_audit_id` + `gap_id`).

### 3.6 Vorgänger-/Nachfolger heute

**Nicht vorhanden** in Runtime-Code:

- kein `predecessor_gap_id` / `successor_gap_id`
- kein persistierter semantischer Gap-Key
- kein Carry-Forward-Report

Einziger Querschnitt: `gap_version` pro `(project_id, visual_intent_id)` — ohne Entscheidungsübertragung.

### 3.7 Current vs. Historical Queries

| API | Verhalten |
|---|---|
| `list_gaps_for_audit` | Gaps eines Audits (Materialize-Idempotenz, Script Lock) |
| `list_current_gaps` | `include_superseded=False` — **nicht** auf Active-Audit gefiltert |
| `list_coverage_gaps(include_superseded=True)` | volle Historie |

---

## 4. Gap Semantic Identity versus Gap Instance

| Konzept | Bedeutung |
|---|---|
| **Gap Semantic Identity** | fachliche Problemidentität (Intent-Semantik + Coverage-Result-Signatur) |
| **Gap Instance** | konkretes Auftreten in einem Audit (`gap_id` + `coverage_audit_id` + `gap_version`) |

C2 arbeitet auf **Instance**-Ebene (gleiche Audit-ID → gleiche Instances).  
C3 verknüpft **Semantic Identity** über verschiedene Instances.

---

## 5. Untersuchte Architekturvarianten

### Option A — Neue IDs + nur abgeleiteter semantischer Schlüssel

Neue `gap_id` pro Audit; Schlüssel nur zur Laufzeit; Verknüpfung ohne persistierte Vorgängerbeziehung.

- Pro: Schema 20 freundlich  
- Contra: Historie schwer auditierbar; UI/Lock brauchen expliziten Report

### Option B — Dieselbe `gap_id` über Audits weiterverwenden

- Contra (entscheidend): Auditzuordnung geht verloren oder vermischt sich; Events/Candidates/FK an eine Instanz gebunden; Current-/Historical-Semantik bricht; widerspricht UUID4-Erzeugung und `coverage_audit_id`-Bindung

**Abgelehnt.**

### Option C — Neue `gap_id` + explizite Vorgängerbeziehung + Report (**empfohlen**)

```
neuer Audit
→ neue gap_id (Instance bleibt auditgebunden)
→ semantic_gap_key (abgeleitet, versioniert)
→ optional predecessor_gap_id nur im Carry-Forward-Report / Sidecar
→ coverage-gap-carry-forward-report-v1 (append-only)
```

- Pro: Instance-Historie bleibt append-only; Auditzuordnung klar; Fail-closed bei Mehrdeutigkeit; Schema 20 ohne Spalte möglich  
- Contra: Match-Engine und Matrix müssen konservativ sein

**Empfehlung: Option C.**

---

## 6. Empfohlener semantischer Schlüssel

### 6.1 Schema `coverage-gap-semantic-key-v1`

Abgeleiteter, sortierungsstabiler SHA-256 über kanonisches JSON:

```
coverage_gap_semantic_key = sha256(stable_json({
  "schema_version": "coverage-gap-semantic-key-v1",
  "project_id": ...,
  "structure_fingerprint": ...,          # script_structure_fingerprint(bundle)
  "script_content_sha256": ...,          # ScriptDraft.content_sha256
  "visual_intent": {                     # semantischer Intent-Fingerprint
    "desired_motif", "action", "setting",
    "geographic_requirements",
    "authenticity_requirements": sorted(...),
    "allowed_media_kinds": sorted(...),
    "priority"
  },
  "coverage_result": {
    "coverage_level",
    "missing_properties": sorted(...),
    "risk_flags": sorted(...),
  }
}))
```

### 6.2 Rollen der Komponenten

| Komponente | Rolle |
|---|---|
| `project_id` | stabile Gap-Identität |
| `structure_fingerprint` + `script_content_sha256` | stabile Gap-Identität (Script-/Strukturkontext) |
| Visual-Intent-Felder (ohne `visual_intent_id`) | stabile Gap-Identität |
| `coverage_level` + sorted missing/risks | stabile Gap-Identität **und** Carry-Forward-Sicherheitsprüfung |
| sortierte matched Asset-Identitäten | **optional** nur Carry-Forward-Sicherheitsprüfung / Match-Hinweis — **nicht** Teil des Basis-Keys (sonst Key-Drift bei Beobachtungsänderungen ohne Intent-Änderung) |
| Canonical Coverage Input Fingerprint (gesamt) | **nicht** Teil des Gap-Keys (zu grob; blockiert sinnvolle Teilmatches) — dient nur der Stufe-0-Abgrenzung (C2) |

### 6.3 Explizit ausgeschlossen

`coverage_audit_id`, `gap_id`, `run_id`, Timestamps, UI-State, zufällige UUIDs, Artefaktpfade, `visual_intent_id` (technisch, nicht semantisch — Intent-IDs können bei Structure-Refresh unter gleicher Semantik neu sein; Semantik steckt in Motiv/Action/Setting/…).

### 6.4 Normalisierungsvertrag

- UTF-8, `sort_keys=True`, kompakte JSON-Separatoren (wie C2 Fingerprint)
- Listen: sortiert, dedupliziert
- `None` / leere Strings kanonisch normalisieren
- Enums als stabile `.value`-Strings
- zentrale Domainfunktion (später), keine UI-Stringbildung

### 6.5 Kollisionsschutz

Bei gleichem Semantic Key für **zwei verschiedene neue Gaps im selben Target-Audit**:

→ `coverage_gap_semantic_key_collision`  
→ kein automatisches Carry-Forward für betroffene Keys  
→ fail-closed

---

## 7. Match-Klassen

| Klasse | Bedingung | Carry-Forward |
|---|---|---|
| **Stufe 0** | Canonical Input identisch | C2-Reuse — **kein C3** |
| **Stufe 1 — Exact 1:1** | genau ein Predecessor mit gleichem Semantic Key + identischer Result-/Risk-/Missing-Signatur | ausgewählte Felder laut Matrix (automatisch oder mit Bestätigung) |
| **Stufe 2 — Similar** | Intent-Semantik nahe, Result/Risk abweichen | nur **Referenz** auf Predecessor; keine automatische Entscheidung |
| **Stufe 3 — New / Ambiguous** | kein Match, 1:N, N:1, Score-Gleichstand | kein Carry-Forward |

### Kardinalität

| Fall | Regel |
|---|---|
| 1 → 1 | einziger Kandidat für automatisches Carry-Forward |
| 1 → N | kein Auto-Transfer; Historie höchstens referenzieren |
| N → 1 | kein Auto-Transfer widersprüchlicher Entscheidungen |
| Mehrdeutig (gleicher Score / mehrere Exact-Kandidaten) | `coverage_gap_predecessor_ambiguous` → fail-closed |

---

## 8. Carry-Forward-Matrix

| Information | automatisch übernehmen | nur referenzieren | niemals übernehmen |
|---|---|---|---|
| Eskalationshistorie (`prior_attempt_summaries` / Events) | — | ja (Stufe 1–2) | still überschreiben alter Events |
| aktuelle Eskalationsstufe | nur Stufe 1 + unveränderte Result-Signatur + nicht widersprüchlich | sonst | bei Risk-/Missing-Änderung |
| frühere Suchanfragen | — | ja | als aktive Suche neu starten |
| abgelehnte Candidates | Stufe 1 wenn Asset+Working-Media+Intent+Key unverändert | sonst | bei stale Working Media |
| akzeptierte Candidates | — | ja | als Coverage-Lösung bei geändertem Result/Risk |
| Candidate-Validierungen | — | ja | blind als „gültig“ |
| `accepted_unresolved` | nur Stufe 1 + exakte Risk-/Missing-/Geo-/Auth-Signatur; **erneute Lock-Bestätigung trotzdem nötig** | Signatur gleich, aber Lock neu | bei jedem Risk-/Missing-/Geo-/Auth-Delta |
| `resolved_by_graphic_plan` | nur wenn gleicher Semantic Key + Graphic Plan existiert + Inhalt/Ziel unverändert + keine neuen Risiken | Plan geändert / unsicher | Plan stale / gelöscht |
| Fotoentscheidung | — | ja | blind |
| Kartenentscheidung | — | ja | blind |
| Nutzerkommentar | — | ja (Anzeige) | als neue Entscheidung |
| Script-Lock-Bestätigung | — | historisch | still auf neuen Audit |
| Risk Acceptance (`gap_id:risk_code`) | — | historisch | unter neuer `gap_id` still gültig |
| Gap Events | — | unverändert historisch | mutieren / umhängen |

Default bei Unsicherheit: **niemals automatisch übernehmen**.

---

## 9. Sichere Invalidierung

Automatisches Carry-Forward **blockieren**, wenn sich gegenüber dem Predecessor ändert:

- Scriptinhalt (`content_sha256`) oder Scriptversion (als Instanzkontext)
- Strukturfingerprint
- Visual-Intent-Semantik (Motiv/Action/Setting/Geo/Auth/Media)
- Coverage Level
- Missing Properties (Mengenvergleich sortiert)
- Risk Set
- geografisches / Authentizitätsrisiko
- Observation Set (wenn Match-Assets Teil der Sicherheitsprüfung sind)
- Assetgültigkeit / Working Media
- Provider-/Prompt-/Schema-Vertrag, falls Result-Vergleich unsicher

Geänderter Canonical Coverage Input allein ⇒ **nicht** „jeder Gap unvergleichbar“, aber **komponentenweise** Prüfung Pflicht.  
Keine globale Entscheidung blind auf den neuen Audit übertragen.

Fehlercode-Familie: `coverage_gap_carry_forward_unsafe`, `coverage_gap_risk_signature_mismatch`, …

---

## 10. Candidate Decisions

| Entscheidung | Regel |
|---|---|
| Abgelehnt | nur behalten/referenzieren wenn Candidate-Asset-Identität, Working Media gültig, Visual-Intent-Semantik + Gap-Semantic-Key identisch, Ablehnungsgrund anwendbar; sonst `coverage_gap_candidate_stale` |
| Akzeptiert | **nicht** automatisch als Coverage-Lösung bei geändertem Coverage Result oder Risk Set |
| Validierungen | nur Referenz |

---

## 11. Graphic- / Foto- / Kartenentscheidungen

### `resolved_by_graphic_plan`

Nur Kandidat zur Fortführung, wenn **alle** gelten:

1. Semantic Key Exact Match (Stufe 1)
2. Graphic Plan zu Predecessor existiert und ist ladbar
3. Plan-Inhalt (`description`, `required_data`, `geographic_scope`) und Zielbezug (`visual_intent` Semantik) unverändert
4. keine neuen Risiken / Missing Properties

Sonst: `coverage_gap_graphic_plan_stale` → kein Auto-Transfer; höchstens Referenz.

### Foto / Karte

Keine automatische Übernahme als Lösung; Eskalationsstufe höchstens unter Stufe-1-Bedingungen; sonst nur Referenz.

---

## 12. `accepted_unresolved`-Vertrag

Mögliche Auto-Übernahme des Gap-**Status**/der accepted risks nur bei:

- gleicher Visual-Intent-Semantik (Key)
- identischem Coverage Result (Level)
- identischem Risk Set
- identischen Missing Properties
- unverändertem geografischem Risiko
- unverändertem Authentizitätsrisiko

**Zusätzlich (konservativ):** auch bei vollständiger Gleichheit bleibt eine **erneute Nutzerbestätigung im Script-Lock** erforderlich (neue `gap_id` ⇒ neue `gap_id:risk_code`-Keys; neuer Lock-Fingerprint). Vorbefüllung der Gap-Entscheidung ist erlaubt; stille Lock-Gültigkeit ist **verboten**.

---

## 13. Script-Lock-Vertrag

| Regel | Vertrag |
|---|---|
| Alt | Risk Keys = `gap_id:risk_code` |
| Neu fachlicher Audit | **immer** neuer `script_lock_fingerprint` nötig |
| Alte Lock-Bestätigung | historisch; **niemals** still gültig für neuen Audit |
| Vorbefüllung | sichere Gap-Entscheidungen dürfen sichtbar vorausgefüllt werden |
| Invalidierung | jede Änderung an Coverage-/Gap-/Claim-/Observation-Fingerprint-Komponenten wie heute in `get_effective_script_lock` |

Nicht zulässig:

```
neue gap_id → alte Lock-Bestätigung still als gültig
```

Bevorzugt:

```
neuer fachlicher Audit
→ neuer Script-Lock-Fingerprint
→ Nutzer bestätigt den neuen Stand
```

---

## 14. Auditierbarkeit und Report

### 14.1 Sidecar `coverage-gap-carry-forward-report-v1`

Versioniertes JSON unter `_otio_v2/` (Pfadvertrag in C3.4 festlegen), append-only je Target-Audit (oder je Materialize-Lauf mit Idempotenz-Key).

Vorgeschlagene Felder:

| Feld | Inhalt |
|---|---|
| `schema_version` | `coverage-gap-carry-forward-report-v1` |
| `source_audit_id` / `target_audit_id` | Auditbindung |
| `source_gap_id` / `target_gap_id` | Instance-Bindung |
| `semantic_gap_key` | abgeleiteter Key |
| `match_class` | `exact_1to1` / `similar` / `none` / `ambiguous` / `one_to_many` / `many_to_one` |
| `match_reasons` | explizite Gründe (kein undurchsichtiger Score-Zwang) |
| `carried_fields` / `referenced_fields` / `invalidated_fields` | Matrix-Ergebnis |
| `decision` | `carried` / `referenced` / `skipped` / `blocked` |
| `reason_codes` | Fehler-/Policy-Codes |
| `created_at` | Report-Timestamp (**nicht** im Semantic Key) |

### 14.2 Historie

- Alte Gap-Instanzen und Events bleiben unverändert (append-only).
- Keine stille Mutation / kein Umhängen von Events auf neue `gap_id`.
- Carry-Forward erzeugt ggf. **neue** Events auf der Target-Instance (`user_decision_recorded` o. ä.) mit Verweis auf Report.

---

## 15. Idempotenz

| Anforderung | Regel |
|---|---|
| Erneutes Materialize desselben Target-Audits | early return wie heute; kein zweites Carry-Forward |
| Erneuter Carry-Forward-Lauf | Report-Idempotenz-Key (`target_audit_id` + `target_gap_id` + `semantic_gap_key`) → no-op wenn bereits published |
| Fehler | kein partieller Carry-Forward → `coverage_gap_carry_forward_publish_failed`; Target-Gaps ohne übernommene Entscheidungen belassen |

---

## 16. Fehlerfälle

| Code | Bedeutung |
|---|---|
| `coverage_gap_semantic_identity_unavailable` | Key nicht berechenbar (Bundle/Result unvollständig) |
| `coverage_gap_semantic_key_collision` | gleicher Key für >1 Target-Gap |
| `coverage_gap_predecessor_ambiguous` | 1:N / N:1 / Score-Gleichstand |
| `coverage_gap_carry_forward_unsafe` | Sicherheitsprüfung fehlgeschlagen |
| `coverage_gap_risk_signature_mismatch` | Risk/Missing/Geo/Auth weichen ab |
| `coverage_gap_candidate_stale` | Candidate/Working Media ungültig |
| `coverage_gap_graphic_plan_stale` | Graphic Plan nicht fortführbar |
| `coverage_gap_decision_conflict` | widersprüchliche Predecessor-Entscheidungen |
| `coverage_gap_carry_forward_publish_failed` | Report/Persistenz fehlgeschlagen |

Bestehende Codes (`coverage_gap_accept_*`, `script_lock_*`, …) weiterverwenden wo passend.  
Bei Unsicherheit: **kein** automatisches Carry-Forward.

---

## 17. Schemaentscheidung

**Vorgabe: Schema bleibt 20.**

| Mittel | C3-Nutzung |
|---|---|
| Abgeleiteter semantischer Schlüssel | ja (Domainfunktion) |
| Bestehende Gap-JSON / Registry-Zeilen | ja (Instance bleibt) |
| Versioniertes Carry-Forward-Sidecar unter `_otio_v2/` | ja (empfohlen) |
| Bestehende Gap Events / Candidate Decisions | ja (historisch + Referenz) |
| Current-/Historical-Abfragen | ja; ggf. Join über Report |
| Neue SQLite-Spalte/Tabelle | **nicht** in C3 ohne Freigabe |

Falls später eine Spalte `semantic_gap_key` / `predecessor_gap_id` zwingend würde:

| | |
|---|---|
| Status | **BLOCKED** für C3-Implementierung bis Schema-Freigabe |
| Dokumentieren | Migrationspfad, Rückwärtskompatibilität, Idempotenz |
| Interim | Sidecar + abgeleiteter Key ausreichend für Alpha |

**C3-Plan-Bewertung:** mit Schema 20 **machbar** (Option C + Sidecar). Keine Migration in diesem Plan.

---

## 18. Testmatrix (für spätere Implementierung)

Vorgeschlagene Datei: `tests/test_discovery_v2_coverage_stability_c3.py`  
Fixtures: Erweiterung von `tests/fixtures/coverage_stability_c1.py` oder `…_c3.py` (Temp-Projekte; **keine** USA_v2-Registry).

### 18.1 Semantischer Schlüssel

| Node-ID (geplant) | Aussage |
|---|---|
| `tests/test_discovery_v2_coverage_stability_c3.py::test_c3_identical_gap_semantics_share_key` | gleiche Semantik → gleicher Key |
| `…::test_c3_gap_id_does_not_affect_semantic_key` | unterschiedliche `gap_id` → Key gleich |
| `…::test_c3_coverage_audit_id_does_not_affect_semantic_key` | unterschiedliche Audit-ID → Key gleich |
| `…::test_c3_visual_intent_change_changes_semantic_key` | Intent-Semantik ändert Key |
| `…::test_c3_risk_set_change_changes_semantic_key` | Risk Set ändert Key |
| `…::test_c3_missing_properties_change_changes_semantic_key` | Missing Properties ändern Key |
| `…::test_c3_sorted_set_fields_are_order_stable` | Sortierung stabil |
| `…::test_c3_semantic_key_excludes_timestamps_and_paths` | keine Timestamps/Pfade |

### 18.2 Match-Klassen

| Node-ID | Aussage |
|---|---|
| `…::test_c3_exact_one_to_one_match` | 1:1 Exact |
| `…::test_c3_one_to_many_does_not_auto_carry` | 1:N fail-closed |
| `…::test_c3_many_to_one_does_not_auto_carry` | N:1 fail-closed |
| `…::test_c3_ambiguous_predecessor_rejected` | Mehrdeutigkeit |
| `…::test_c3_no_predecessor_creates_normal_new_gap` | Stufe 3 |

### 18.3 Entscheidungen

| Node-ID | Aussage |
|---|---|
| `…::test_c3_escalation_history_referenced_or_carried_safely` | Eskalation |
| `…::test_c3_accepted_unresolved_only_with_exact_risk_signature` | accepted_unresolved |
| `…::test_c3_changed_risk_invalidates_accepted_unresolved` | Risk-Delta |
| `…::test_c3_valid_graphic_plan_detected_as_carry_candidate` | Graphic Plan OK |
| `…::test_c3_changed_graphic_plan_not_carried` | Graphic Plan stale |
| `…::test_c3_rejected_candidate_kept_only_if_asset_and_gap_unchanged` | Candidate reject |
| `…::test_c3_invalid_working_media_invalidates_candidate_decision` | Working Media |

### 18.4 Script Lock

| Node-ID | Aussage |
|---|---|
| `…::test_c3_new_audit_requires_new_lock_fingerprint` | neuer Fingerprint |
| `…::test_c3_old_lock_confirmation_not_silently_reused` | keine stille Lock-Reuse |
| `…::test_c3_safely_prefilled_gap_decisions_remain_visible` | Vorbefüllung sichtbar |
| `…::test_c3_changed_risk_signature_blocks_lock` | Risk blockiert Lock |

### 18.5 Auditierbarkeit / Isolation

| Node-ID | Aussage |
|---|---|
| `…::test_c3_carry_forward_report_is_versioned` | Report v1 |
| `…::test_c3_predecessor_successor_link_is_auditable` | Nachvollziehbarkeit |
| `…::test_c3_old_gap_events_remain_immutable` | append-only |
| `…::test_c3_carry_forward_is_idempotent` | Idempotenz |
| `…::test_c3_failed_publish_leaves_no_partial_carry` | kein Partial |
| `…::test_c3_schema_remains_20_and_no_classic_write` | Schema / Classic |
| `…::test_c3_no_real_provider_and_no_c4_v4_r14` | Isolation |

Bestehende Suiten grün halten:  
`test_discovery_v2_coverage_gaps.py`, `…_script_lock.py`, `…_script_lock_identity_r1.py`, `…_stock_candidate_decisions.py`, `…_coverage_stability_c1.py`, `…_c2.py`, Supplementation-Tests.

---

## 19. Smokes A–F (spätere Node-IDs)

| Smoke | Node-ID | Erwartung |
|---|---|---|
| **A** Exact Nachfolger | `…::test_c3_smoke_a_exact_semantic_successor_carries_allowed_history` | neuer Audit + 1:1 + identische Signatur → erlaubte Historie nachvollziehbar |
| **B** Neues Risiko | `…::test_c3_smoke_b_new_authenticity_risk_blocks_old_acceptance` | gleicher Intent + neues Auth-Risiko → keine alte Risk Acceptance |
| **C** Gap-Aufteilung | `…::test_c3_smoke_c_gap_split_no_auto_carry_history_reference_ok` | 1→2 → kein Auto-CF; Referenz möglich |
| **D** Graphic Plan | `…::test_c3_smoke_d_graphic_plan_safe_carry_candidate` | unveränderter Plan → sicherer Fortführungs-Kandidat |
| **E** Candidate ungültig | `…::test_c3_smoke_e_stale_working_media_blocks_candidate` | Working Media ungültig → Decision nicht übernommen |
| **F** Script Lock | `…::test_c3_smoke_f_carried_gaps_still_require_new_lock_fingerprint` | Gap-CF möglich → Lock-Fingerprint trotzdem neu |

---

## 20. Implementierungsreihenfolge C3.1–C3.4

### C3.1 — Root-Cause-Fixtures (nächster erlaubter Implementierungsschritt nach Planfreigabe)

- Gap-ID UUID4 + Event-Bindung reproduzieren
- Supersede bei neuem Audit ohne Reuse belegen
- Script-Lock-Auswirkung neuer `gap_id` belegen
- Fixtures: 1:1, 1:N, N:1, ambiguous
- **keine** Produktlogik für Carry-Forward

### C3.2 — Semantic Gap Identity

- Domainmodell `coverage-gap-semantic-key-v1`
- kanonische Normalisierung + Kollisionsschutz
- reine Ableitung; Persistenz nur wenn nötig über Sidecar-Freigabe
- keine SQLite-Migration

### C3.3 — Match Engine und Carry-Forward-Matrix

- Exact 1:1 Matching
- Matrix aus §8
- Mehrdeutigkeit fail-closed
- Integration **nach** erfolgreicher Materialisierung neuer Gaps (nicht C4-Atomik vorziehen)

### C3.4 — Report, Idempotenz und Integration

- `coverage-gap-carry-forward-report-v1`
- append-only Historie
- Idempotenz / Publish-Fehlerpfad
- Script-Lock-Vorbefüllung ohne stille Lock-Reuse

**C4 bleibt ausgeschlossen** (Supersede-Atomik, Current-Umschaltung, UI-Beschriftung).

---

## 21. Risiken und Einschränkungen

| Risiko | Mitigation |
|---|---|
| Intent-ID wechselt bei Structure-Refresh trotz ähnlicher Semantik | Semantic Key ohne `visual_intent_id` |
| Structure Bundle unter gleicher `script_id` mutierbar (C2-R1 Evidenz) | Structure-/Script-Hashes im Key; kein Legacy-Reconstruct |
| Zu aggressives Carry-Forward | Matrix fail-closed; Lock immer neu |
| `list_current_gaps` nicht audit-gefiltert | C3 darf Current-Semantik nicht still ändern (C4) |
| Schema-Druck | Sidecar zuerst; Spalte = BLOCKED |
| USA_v2 als Fixture missbraucht | verboten; nur Temp-Projekte |

---

## 22. Deferred Items

- C4 Supersede-Atomizität / Current-Gap-Umschaltung
- UI-Korrektur Gap- vs. Visual-Intent-Beschriftung
- Persistierte SQLite-Spalte `semantic_gap_key` / `predecessor_gap_id` (Schema ≥21)
- Automatische Risikoannahme ohne Lock-Bestätigung
- Visual Edit V4, R1.4 Polling
- Echte Provider
- Nutzerregistry-Reparatur

---

## 23. Abnahmekriterien des Plans

Der Plan ist abnahmefähig, wenn er:

- [x] C2-Reuse klar von C3-Carry-Forward trennt
- [x] aktuelle Gap-ID-Erzeugung konkret belegt (`new_gap_id` / UUID4 / Materialize)
- [x] Gap Semantic Identity und Gap Instance trennt
- [x] Architekturvariante empfiehlt (Option C)
- [x] vollständige Carry-Forward-Matrix enthält
- [x] `accepted_unresolved`, Graphic Plans, Candidate Decisions einzeln behandelt
- [x] 1:N und N:1 fail-closed behandelt
- [x] Script-Lock-Neubestätigung konservativ regelt
- [x] versionierten Audit-Report vorsieht
- [x] Schema 20 belastbar bewertet (machbar ohne Migration)
- [x] konkrete Tests und Smokes enthält
- [x] C4 und UI-Beschriftung nicht vorzieht

---

## 24. Nächste erlaubte Aktion nach Planfreigabe

→ **C3.1 Root-Cause-Fixtures** (nur Tests/Fixtures; keine Carry-Forward-Produktlogik)

Danach C3.2 → C3.3 → C3.4 nach jeweiligen Freigaben.  
C4 / V4 / R1.4 bleiben gesperrt.
