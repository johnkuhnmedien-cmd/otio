# Progress — Discovery V2 / Alpha

## Aktueller Stand

**Script-Lock Current-State L4 Invalidierung umgesetzt — Schema weiterhin 20.**

- Chief-Dev-Status Alpha-Produktstand: **APPROVED** (Commit `1ac7fba`)
- Script-Lock Realtest: **erfolgreich**
- R1.1–R1.3: abgeschlossen
- Releaseklasse: **interner MANUAL-/Fake-Alpha**
- Schema: **20**
- Provider: **Fake-only**
- Branch: `cursor/discovery-v2-integration` · PR `#69`
- Visual Edit Rework **V1–V3**: implementiert
- **Coverage Stability C1 / C2 / C2-R1**: vollständig abgeschlossen / USA_v2 abgenommen
- Decisions: D-COVERAGE-STABILITY-001…008 · **D-SCRIPT-LOCK-CURRENT-001…006**
- C3-Plan: `docs/source_plans/ALPHA_COVERAGE_STABILITY_C3_GAP_IDENTITY_CARRY_FORWARD_PLAN.md`
- **C3.1 / C3.2 abgeschlossen** (`36367d2` / `47cfafd`)
- **C3.3 umgesetzt** (`7ba6468`): Exact Match Engine `coverage-gap-match-report-v1`
- **Fake-Alpha-Test an Narration-Grenze blockiert** (USA_v2): historischer Lock ≠ Effective Lock
- Script-Lock-Plan: `docs/source_plans/ALPHA_SCRIPT_LOCK_CURRENT_STATE_CONSISTENCY_PLAN.md` (`6a33d0f`)
- **L1 Root-Cause-Fixtures abgeschlossen** (`a492c54`)
- **L2 Effective-Lock-Resolver umgesetzt** (`5bca917`)
- **L3 Editorial-/Narration-Gate-Integration umgesetzt** (`6852e7c`)
- **L4 Current-State-Invalidierung umgesetzt** (`f1ddcb2`)
- **Structure-Finalization-Hotfix umgesetzt** (`aaf7696`): vollständige Struktur → `review_requested`
- Preview-Blocker-UI-Hotfix (`069cada`)
- **Structure-Persistence-Atomicity-Hotfix umgesetzt** (`bac1bbc`): Registry-Commit vor JSON-Publish; Preview fail-closed
- **Fake-Alpha weiterhin bis L5 pausiert**
- **Nächste erlaubte Aktion nach Chief-Dev-Freigabe: L5 USA_v2-Realtest**
- **L5, C3.4, C4, V4 und R1.4 gesperrt**
- Keine neue Produktphase · echte Provider gesperrt

## Phase-Status

| Phase | Status |
|---|---|
| 7–13 Produktpfad | freigegeben / Fake-Alpha |
| R1.1–R1.3 | abgeschlossen |
| Visual Edit Rework V1–V3 | abgeschlossen |
| Coverage Stability C1 | abgeschlossen (Reproduktion) |
| Coverage Stability C2 | vollständig abgeschlossen |
| Coverage Stability C2-R1 | vollständig abgeschlossen |
| Coverage Stability C3 Plan | dokumentiert |
| Coverage Stability C3.1 | abgeschlossen (Root-Cause-Fixtures) |
| Coverage Stability C3.2 | umgesetzt (Semantic Gap Identity) |
| Coverage Stability C3.3 | umgesetzt (Exact Match Engine) |
| Coverage Stability C3.4 | **gesperrt** |
| Script-Lock Current-State Plan | **dokumentiert** |
| Script-Lock L1 Fixtures | **abgeschlossen** (Root-Cause-Reproduktion) |
| Script-Lock L2 Effective Resolver | **umgesetzt** (read-only, fail-closed) |
| Script-Lock L3 UI/Gate Integration | **umgesetzt** (Editorial + Narration Gates) |
| Script-Lock L4 Current-State Invalidation | **umgesetzt** (atomare Pointer-Clears) |
| Script-Lock L5 | **gesperrt** bis Chief-Dev-Freigabe |
| Coverage Stability C4 | **gesperrt** |
| Visual Edit Rework V4 Loop/UI | **gesperrt** |
| R1.4 Job-UX / Progress-Polling | **gesperrt** |
| R1.5–R1.6 | gesperrt |

## Script-Lock Current-State Consistency — Kurzstand

- Fake-Alpha USA_v2 an Narration-Grenze blockiert
- **L1 abgeschlossen** — Fixtures A/B/C + Fixture-Nachweise (`a492c54`; UI-Deadlock-Erwartungen → L3-Produktvertrag)
- **L2 umgesetzt** — `resolve_effective_current_script_lock` read-only / fail-closed (`5bca917`)
  - Editorial `current_script_lock_id` Pflicht; kein latest-locked Fallback
  - Identity + kanonischer Fingerprint + `gap_id:risk_code` + `status=locked`
  - Narration-Pointer nur diagnostisch (`narration_script_lock_stale`)
  - Decisions: D-SCRIPT-LOCK-CURRENT-001 / D-SCRIPT-LOCK-CURRENT-002
- **L3 umgesetzt** — Editorial-/Narration-Gates (`6852e7c`)
  - `EditorialScriptLockGateState` / `NarrationGateState`
  - Current ≠ History; New-Lock unabhängig von Historie
  - Voice/Pause/Timing nur Effective-Lock-Artefakte; stale Narration-Pointer fail-closed
  - Decisions: D-SCRIPT-LOCK-CURRENT-003 / D-SCRIPT-LOCK-CURRENT-004
- **L4 umgesetzt** — atomare Current-State-Invalidierung (`f1ddcb2`)
  - `invalidate_current_script_lock_context` / `apply_script_lock_context_invalidation`
  - Fachliche Pfade: Script-Edit, Structure/Coverage, Observation, Risk, Hook
  - Neuer Lock = Editorial Current only; Narration bindet erst bei Voice-Start
  - Historische Locks/Voice/Pause/Timeline bleiben erhalten
  - Decisions: D-SCRIPT-LOCK-CURRENT-005 / D-SCRIPT-LOCK-CURRENT-006
- Domain: `otio_app/discovery_v2/domain/script_lock_current_state.py`
- Application: `script_lock_current_state_service.py`, `script_lock_current_state_mutation_service.py`,
  `editorial_script_lock_gate_service.py`, `narration_gate_service.py`
- Tests: L2 (34) · L3 (30) · L4 (`tests/test_discovery_v2_script_lock_current_state_l4.py`, 30 Node-IDs)
- Plan: `docs/source_plans/ALPHA_SCRIPT_LOCK_CURRENT_STATE_CONSISTENCY_PLAN.md`
- **Structure-Finalization-Hotfix** (`aaf7696`)
  - Root Cause: Structure-Runs schrieben `structure_pending` zurück (Fake + Worker)
  - Bei vollständiger Struktur: kanonisch `review_requested` (JSON + DB)
  - Unvollständig: fail-closed, Status bleibt `structure_pending`, sichtbarer Fehlercode
  - UI „Struktur aktualisieren“ sync → „Struktur aktualisiert.“ oder Fehlercode
  - Tests: `tests/test_discovery_v2_structure_finalization.py` (12 Node-IDs)
- **Structure-Persistence-Atomicity-Hotfix** (`bac1bbc`)
  - Root Cause: JSON `review_requested` vor Registry-Commit; FK auf `coverage_intent_results` → divergenter Stand + falscher Preview-Fingerprint
  - Neu: Registry-Replace → State → JSON-Publish → Commit; Snapshot-Restore bei Fehler
  - Preview: `registry_artifact_mismatch` / `editorial_script_identity_mismatch` / `active_script_pointer_missing`
  - Tests: `tests/test_discovery_v2_structure_persistence_atomicity.py` (12 Node-IDs)
- Nächster Schritt nach Freigabe: **L5 USA_v2-Realtest**
- Gesperrt: L5 bis Freigabe; C3.4, C4, V4, R1.4
- Fake-Alpha weiterhin bis L5 pausiert

## Coverage Stability C3.3 — Kurzstand

- Schema-Identifier: `coverage-gap-match-report-v1`
- Domain: `otio_app/discovery_v2/domain/coverage_gap_matching.py`
- Application: `otio_app/discovery_v2/application/coverage_gap_matching_service.py`
- Exact Match: gleicher Schema-Identifier + Semantic Key + vollständiger Identity-Payload
- Nur `exact_one_to_one` → `carry_forward_evaluation_allowed=true` (noch keine Übernahme)
- 1:N / N:1 / N:N / Kollision / Schema-Mismatch → fail-closed, kein Similarity Matching
- Stabiler Report-Fingerprint; Eingabereihenfolge irrelevant
- Decisions: D-COVERAGE-STABILITY-007, D-COVERAGE-STABILITY-008
- Tests: `tests/test_discovery_v2_coverage_stability_c3_3.py` (30 Node-IDs)
- Produktcommit: `7ba6468`

## Coverage Stability C3.2 — Kurzstand

- Schema-Identifier: `coverage-gap-semantic-key-v1`
- Domain: `otio_app/discovery_v2/domain/coverage_gap_identity.py`
- Application: `otio_app/discovery_v2/application/coverage_gap_identity_service.py`
- `gap_id` bleibt UUID4-Instanz; Semantic Key ist getrennt und wird nicht persistiert
- Key-Inhalte: `project_id` + Intent-Semantik + `coverage_level` / missing / risk_codes
- ausgeschlossen: Audit-/Gap-/Intent-IDs, Status, Decisions, Fingerprints, Pfade, `priority`
- Kollisionsschutz: gleicher Key + anderer Payload → `coverage_gap_semantic_key_collision`
- Decisions: D-COVERAGE-STABILITY-005, D-COVERAGE-STABILITY-006
- Tests: `tests/test_discovery_v2_coverage_stability_c3_2.py` (30 Node-IDs)
- Produktcommit: `47cfafd`

## Coverage Stability C3.1 — Kurzstand

- `gap_id` = `uuid4()`; neuer fachlicher Audit ⇒ Supersede + neue Gap-Instanzen
- Events / Candidate Decisions / `accepted_unresolved` bleiben an alter `gap_id`
- Script-Lock Keys `gap_id:risk_code` und Lock-Fingerprint ändern sich
- keine persistierte `semantic_gap_key` / `predecessor_gap_id`
- Match-Shape-Fixtures: 1:1, 1:N, N:1, kein Vorgänger
- Tests: `tests/test_discovery_v2_coverage_stability_c3_1.py` (12 Node-IDs)
- Fixtures: `tests/fixtures/coverage_stability_c3_1.py`

## USA_v2 Coverage-Reuse-Realtest — Abnahme

Projekt: **USA_v2** · Schema **20**

| Feld | Wert |
|---|---|
| Aktiver Audit | `c2b32d64-3961-53bf-ab85-391932a2bf43` |
| Letzter Run | `7bb2273b-96ca-4942-9924-6de34b29d471` |

Zweiter identischer Coverage-Aufruf:

- completed Current Audit **reused**
- kein neuer Run
- keine neue Audit-ID
- keine neuen Gap-IDs
- keine Gap-Statusverluste

Aktuelle Gaps:

| Gap-ID | Status |
|---|---|
| `094f0390-cfb2-41e7-a19f-65ca4d583fb0` | `resolved_by_graphic_plan` |
| `2c36238e-6a1e-44ee-ae00-0abf8f398acf` | `accepted_unresolved` |
| `95a924fe-2995-4da7-80ff-d172cca3221b` | `in_progress` / `user_decision` |

Offener UI-Befund (nicht C2-blockierend): Visual-Intent-ID wird teilweise als Gap-ID beschriftet.

## Coverage Stability C2-R1 — Kurzstand

- Audits ohne gespeicherten Canonical Fingerprint: **kein Reuse**
- Keine Rekonstruktion aus mutable Brief/Narrative/Script-Bundle
- Genau ein normaler Recompute → neuer Audit speichert Fingerprint
- Zweiter identischer Aufruf → Completed Reuse (kein Gateway/Worker/Gaps)
- Active-Run-Reuse unverändert
- Diagnosegrund: `legacy_audit_missing_canonical_fingerprint`
- Decision: D-COVERAGE-STABILITY-004

## Teststand

**3244 collected / 3225 passed / 18 failed / 1 skipped** (+12 Structure-Persistence-Atomicity-Tests; Baseline-18 unverändert)

18 bekannte Classic/Without-VO-Fehler und 1 VFR-Skip unverändert.

## Nächste erlaubte Aktivität

Nach Chief-Dev-Freigabe:

→ **L5 USA_v2-Realtest** (Script-Lock Current-State Consistency)

Weiter gesperrt: L5 bis Freigabe; C3.4, C4, V4, R1.4–R1.6,
echte Provider, neue Produktphase, Nutzerregistry-Reparatur.
Fake-Alpha weiterhin bis L5 pausiert.
