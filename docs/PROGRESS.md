# Progress — Discovery V2 / Alpha

## Aktueller Stand

**R1.2 State-/Routing-Stabilisierung abgeschlossen — Schema weiterhin 20.**

- Chief-Dev-Status Alpha-Produktstand: **APPROVED** (Commit `1ac7fba`)
- R1.1 Blocker-Fix: abgeschlossen (`f3e015b`)
- R1.2 State/Routing: **abgeschlossen** (Produktcommit `4c6bfd9`)
- Releaseklasse: **interner MANUAL-/Fake-Alpha**
- Schema: **20** (keine Schema-21-Migration)
- Provider: **Fake-only**
- Adobe: **UNKNOWN**
- NLE: nur lokaler OTIO-Serialize/Reparse
- Branch: `cursor/discovery-v2-integration` · PR `#69`
- Reload/Deep-Link: `?project_id=<uuid>&page=<slug>` + Streamlit `url_path`
- Post-Mutation: kontrollierter `st.rerun()` + frisches Application-Viewmodel
- **Nächster erlaubter Schritt nach Freigabe: R1.3**
- R1.4–R1.6 weiterhin gesperrt
- Keine neue Produktphase
- Echte Provider weiterhin gesperrt

## Phase-Status

| Phase | Status |
|---|---|
| 7–13 Produktpfad | freigegeben / Fake-Alpha |
| Alpha Release Closeout | dokumentiert (`1ac7fba`) |
| UX Workflow Stabilization R1 Plan | dokumentiert (`cac5e76`) |
| R1.1 Coverage / Script Lock Blocker | abgeschlossen (`f3e015b`) |
| R1.2 State / Routing | **abgeschlossen** (`4c6bfd9`) |
| R1.3 Review / Analyse-Queue | nächster erlaubter Schritt (nach Freigabe) |
| R1.4–R1.6 | gesperrt |

## Teststand

**2962 collected / 2943 passed / 18 failed / 1 skipped** (Ziel nach Vollsuite-Nachlauf)

Vergleich zur R1.1-Baseline **2935 / 2916 / 18 / 1**:
- +~27 R1.2-Tests, gleiche 18 Classic/Without-VO Baseline-Fehler
- 1 bekannter VFR-Skip unverändert

## Bekannte Einschränkungen / UNKNOWN

- Fake-only Provider
- Adobe OAuth / Lizenz / Auto-Download: **UNKNOWN**
- Keine proprietären NLE-Exporte
- Analyse-Queue / Batch-Review / Progress-Polling: R1.3–R1.6
- Style References / Shared Working Media: deferred

## Nächste erlaubte Aktivität

Nach Freigabe:

→ **R1.3** (Analyse-Queue / Observation-Review-Mengen)

Noch gesperrt: R1.4–R1.6, echte Provider, Style References,
Shared Working Media, neue Produktphase.
