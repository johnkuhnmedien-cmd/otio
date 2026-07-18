# Progress — Discovery V2 / Alpha

## Aktueller Stand

**R1.1 Coverage-/Script-Lock-Blocker behoben — Schema weiterhin 20.**

- Chief-Dev-Status Alpha-Produktstand: **APPROVED** (Commit `1ac7fba`)
- R1.1 Blocker-Fix: **abgeschlossen** (Produktcommit `f3e015b`)
- Releaseklasse: **interner MANUAL-/Fake-Alpha**
- Schema: **20** (keine Schema-21-Migration)
- Provider: **Fake-only**
- Adobe: **UNKNOWN**
- NLE: nur lokaler OTIO-Serialize/Reparse
- Branch: `cursor/discovery-v2-integration` · PR `#69`
- Interne Alpha-Erprobung: Coverage-Partial-Pfad + Script Lock wieder gangbar
  (`exact_match_not_verified` → sichtbares Risiko → `accepted_unresolved` → Lock)
- Root Cause `editorial_registry_write_failed`: FakeText Coverage-Audit-IDs
  ohne `run_id` → UNIQUE-Konflikt bei Wiederanlauf (siehe DECISIONS / Handoff)
- **Nächster erlaubter Schritt nach Freigabe: R1.2**
- R1.3–R1.6 weiterhin gesperrt
- Keine neue Produktphase
- Echte Provider weiterhin gesperrt

## Phase-Status

| Phase | Status |
|---|---|
| 7–13 Produktpfad | freigegeben / Fake-Alpha |
| Alpha Release Closeout | dokumentiert (`1ac7fba`) |
| UX Workflow Stabilization R1 Plan | dokumentiert (`cac5e76`) |
| R1.1 Coverage / Script Lock Blocker | **abgeschlossen** (`f3e015b`) |
| R1.2 Stale Viewmodels / Reload | nächster erlaubter Schritt (nach Freigabe) |
| R1.3–R1.6 | gesperrt |

## Teststand

**2935 collected / 2916 passed / 18 failed / 1 skipped**

Vergleich zur R1-Plan-Baseline **2915 / 2896 / 18 / 1**:
- +20 Tests (R1.1 Smokes/Regressionen), alle grün
- 18 bekannte Classic/Without-VO Baseline-Fehler unverändert
- 1 bekannter VFR-Skip unverändert

## Bekannte Einschränkungen / UNKNOWN

- Fake-only Provider
- Adobe OAuth / Lizenz / Auto-Download: **UNKNOWN**
- Keine proprietären NLE-Exporte
- Stale Viewmodels / Reload / Progress / Batch-Review: R1.2–R1.6
- Style References / Shared Working Media: deferred

## Nächste erlaubte Aktivität

Nach Freigabe:

→ **R1.2** (Stale Viewmodels / Reload / Projektkontext)

Noch gesperrt: R1.3–R1.6, echte Provider, Style References,
Shared Working Media, neue Produktphase.
