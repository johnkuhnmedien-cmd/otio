# Progress — Discovery V2 / Alpha

## Aktueller Stand

**Interner MANUAL-/Fake-Alpha getestet — End-to-End am Coverage-/Script-Lock-Gate blockiert.**

- Chief-Dev-Status Alpha-Produktstand: **APPROVED** (Commit `1ac7fba`)
- Releaseklasse: **interner MANUAL-/Fake-Alpha**
- Schema: **20**
- Provider: **Fake-only**
- Adobe: **UNKNOWN**
- NLE: nur lokaler OTIO-Serialize/Reparse
- Branch: `cursor/discovery-v2-integration` · PR `#69`
- Interne Alpha-Erprobung: **blockiert** am Coverage-/Script-Lock-Gate
  (`partially_covered` + `exact_match_not_verified` + leere `risk_flags`
  → Accept-Button disabled → kein Script Lock)
- **R1-Planung aktiv:**
  `docs/source_plans/ALPHA_UX_WORKFLOW_STABILIZATION_R1_PLAN.md`
- Keine neue Produktphase
- Echte Provider weiterhin gesperrt

## Phase-Status

| Phase | Status |
|---|---|
| 7–13 Produktpfad | freigegeben / Fake-Alpha |
| Alpha Release Closeout | dokumentiert (`1ac7fba`) |
| Interne Alpha-Erprobung | blockiert (Coverage / Script Lock) |
| UX Workflow Stabilization R1 | **Planung** (kein Implementierungsstart) |

## Teststand (Baseline unverändert)

**2915 collected / 2896 passed / 18 failed / 1 skipped**

18 bekannte Baseline-Fehler unverändert; 1 bekannter VFR-Skip unverändert.
Keine R1-Produktänderungen in diesem Planungsstand.

## Bekannte Einschränkungen / UNKNOWN

- Fake-only Provider
- Adobe OAuth / Lizenz / Auto-Download: **UNKNOWN**
- Keine proprietären NLE-Exporte
- Coverage-Partial-Pfad ohne sichtbares `risk_flags` blockiert Lock (R1.1)
- Stale Viewmodels / Reload / Progress / Batch-Review: R1.2–R1.6
- Style References / Shared Working Media: deferred

## Nächste erlaubte Aktivität

Nach Freigabe des R1-Plans:

→ **R1.1 Blocker-Implementierung**
  (Coverage `accepted_unresolved`, Registry-Write, Lock-Gate, Fingerprint-UX)

Noch gesperrt: R1.2–R1.6, echte Provider, Style References,
Shared Working Media, neue Produktphase.
