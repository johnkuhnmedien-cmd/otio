# Progress — Discovery V2 / Alpha

## Aktueller Stand

**Coverage-Idempotenz-Planung aktiv — Schema weiterhin 20.**

- Chief-Dev-Status Alpha-Produktstand: **APPROVED** (Commit `1ac7fba`)
- Script-Lock Realtest: **erfolgreich**
- R1.1–R1.3: abgeschlossen
- Releaseklasse: **interner MANUAL-/Fake-Alpha**
- Schema: **20**
- Provider: **Fake-only**
- Branch: `cursor/discovery-v2-integration` · PR `#69`
- Visual Edit Rework **V1–V3**: implementiert (`70ffe6e` / `0fba7bd` / `f1b982a`)
- V2-Realtest: sechs unterschiedliche Assets
- **V3-Realtest:** ehrlicher `additional_coverage_required`-Fall erreicht
- **End-to-End derzeit blockiert** durch Coverage-Reset bei äquivalentem Audit
  (neue Audit-ID → Gaps superseded → Entscheidungen verloren → Script Lock offen)
- Plan: `docs/source_plans/ALPHA_COVERAGE_IDEMPOTENCY_CARRY_FORWARD_PLAN.md`
- **Nächster erlaubter Schritt nach Freigabe: Coverage Stability C1**
- **C2–C4, V4 und R1.4 gesperrt** bis Freigabe
- Keine neue Produktphase · echte Provider gesperrt

## Phase-Status

| Phase | Status |
|---|---|
| 7–13 Produktpfad | freigegeben / Fake-Alpha |
| R1.1–R1.3 | abgeschlossen |
| Visual Edit Rework V1–V3 | abgeschlossen |
| Coverage Idempotency Plan | **dokumentiert** |
| Coverage Stability C1–C4 | nächster erlaubter Schritt (nach Freigabe: C1) |
| Visual Edit Rework V4 Loop/UI | **gesperrt** |
| R1.4 Job-UX / Progress-Polling | **gesperrt** |
| R1.5–R1.6 | gesperrt |

## Teststand

**3011 collected / 2992 passed / 18 failed / 1 skipped** (~293s)

Baseline unverändert seit V3. 18 bekannte Classic/Without-VO-Fehler und 1 VFR-Skip
nicht Gegenstand dieses Planungsauftrags.

## Nächste erlaubte Aktivität

Nach Freigabe:

→ **Coverage Stability C1** (Fixtures und reproduzierbarer Root-Cause-Test)

Danach C2 (Canonical Input / Dedup) → C3 (Gap Identity / Carry-Forward) → C4
(Atomarität / UI).

Noch gesperrt: V4, R1.4–R1.6, echte Provider, neue Produktphase,
Nutzerregistry-Reparatur.
