# Progress — Discovery V2 / Alpha

## Aktueller Stand

**Coverage Stability C2 abgeschlossen — Schema weiterhin 20.**

- Chief-Dev-Status Alpha-Produktstand: **APPROVED** (Commit `1ac7fba`)
- Script-Lock Realtest: **erfolgreich**
- R1.1–R1.3: abgeschlossen
- Releaseklasse: **interner MANUAL-/Fake-Alpha**
- Schema: **20**
- Provider: **Fake-only**
- Branch: `cursor/discovery-v2-integration` · PR `#69`
- Visual Edit Rework **V1–V3**: implementiert
- Coverage Idempotency Plan: dokumentiert
- **Coverage Stability C1**: Reproduktion (`94b6e0f`)
- **Coverage Stability C2**: Canonical Input + Active-/Completed-Reuse (`7e8db48`)
- Decisions: D-COVERAGE-STABILITY-001…003
- **Manueller Reuse-Test erforderlich** (temporäres Projekt; keine USA_v2-Registry)
- Plan: `docs/source_plans/ALPHA_COVERAGE_IDEMPOTENCY_CARRY_FORWARD_PLAN.md`
- **Nächster erlaubter Schritt nach Freigabe: Coverage Stability C3**
- **C4, V4 und R1.4 gesperrt** bis Freigabe
- Keine neue Produktphase · echte Provider gesperrt

## Phase-Status

| Phase | Status |
|---|---|
| 7–13 Produktpfad | freigegeben / Fake-Alpha |
| R1.1–R1.3 | abgeschlossen |
| Visual Edit Rework V1–V3 | abgeschlossen |
| Coverage Idempotency Plan | dokumentiert |
| Coverage Stability C1 | abgeschlossen (Reproduktion) |
| Coverage Stability C2 | **abgeschlossen** (Canonical Reuse) |
| Coverage Stability C3–C4 | nächster erlaubter Schritt (nach Freigabe: C3) |
| Visual Edit Rework V4 Loop/UI | **gesperrt** |
| R1.4 Job-UX / Progress-Polling | **gesperrt** |
| R1.5–R1.6 | gesperrt |

## Coverage Stability C2 — Kurzstand

- Canonical Input Schema: `coverage-input-v1`
- Fingerprint: SHA-256 über sortierungsstabiles JSON (ohne run/audit/gap/timestamps/paths)
- Dedup-Key: `project_id|fingerprint|editorial_coverage_only|mode`
- Completed Current Audit Reuse → kein Gateway, kein Worker, keine neuen Gaps
- Active equivalent Run Reuse → dieselbe Run-ID, kein zweiter Worker
- Neue Audits speichern `canonical_coverage_input_fingerprint` im JSON-Artefakt
- Legacy: sichere Rekonstruktion oder kein Reuse
- `force_recompute` nur explizit; normale UI nutzt `normal`

## Teststand

**3034 collected / 3015 passed / 18 failed / 1 skipped** (~320s)

Baseline C1 (3017/2998) +17 C2-Tests. 18 bekannte Classic/Without-VO-Fehler
und 1 VFR-Skip unverändert.

## Nächste erlaubte Aktivität

Nach Freigabe:

→ **Coverage Stability C3** (Gap Identity / Carry-Forward)

Danach C4 (Atomarität / UI).

Noch gesperrt: V4, R1.4–R1.6, echte Provider, neue Produktphase,
Nutzerregistry-Reparatur.
