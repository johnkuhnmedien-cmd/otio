# Progress — Discovery V2 / Alpha

## Aktueller Stand

**Script-Lock Identity Rework abgeschlossen — Schema weiterhin 20.**

- Chief-Dev-Status Alpha-Produktstand: **APPROVED** (Commit `1ac7fba`)
- R1.1 Coverage-/Accept-Blocker: abgeschlossen (`f3e015b`)
- R1.1 Script-Lock Identity Rework: **abgeschlossen** (`8f4b9aa`)
- R1.2 State/Routing: abgeschlossen (`4c6bfd9`)
- R1.3 Review/Analyse: abgeschlossen (`45a5b4f`, `8b4c2ad`)
- Releaseklasse: **interner MANUAL-/Fake-Alpha**
- Schema: **20**
- Provider: **Fake-only**
- Branch: `cursor/discovery-v2-integration` · PR `#69`
- Root Cause Script Lock: Fingerprint wurde bei `accepted_unresolved` erst nach
  UI-Risikobestätigungen berechnet → zirkulärer Blocker („Kein Fingerprint“)
- Fix: fachlicher Preview-Fingerprint unabhängig von Checkboxen; kanonischer
  Risikoschlüssel `gap_id:risk_code`
- Manueller Realtest (USA_v2) muss anschließend wiederholt werden
- R1.3 Acceptance Evidence weiterhin offen
- **Nächster erlaubter Schritt nach Freigabe: R1.4**
- R1.4–R1.6 weiterhin gesperrt (kein Polling)
- Keine neue Produktphase · echte Provider gesperrt

## Phase-Status

| Phase | Status |
|---|---|
| 7–13 Produktpfad | freigegeben / Fake-Alpha |
| R1.1 Coverage / Accept | abgeschlossen (`f3e015b`) |
| R1.1 Script-Lock Identity | **abgeschlossen** (`8f4b9aa`) |
| R1.2 State / Routing | abgeschlossen (`4c6bfd9`) |
| R1.3 Review / Analyse-Queue | abgeschlossen (`45a5b4f`) |
| R1.4 Job-UX / Progress-Polling | nächster erlaubter Schritt (nach Freigabe) |
| R1.5–R1.6 | gesperrt |

## Teststand

**2988 collected / 2969 passed / 18 failed / 1 skipped** (~271s)

Vergleich zur R1.3-Baseline **2976 / 2957 / 18 / 1**:
- +12 Tests (Script-Lock Identity), alle grün
- 18 bekannte Classic/Without-VO Baseline-Fehler unverändert
- 1 bekannter VFR-Skip unverändert

## Nächste erlaubte Aktivität

Nach Freigabe und manuellem Realtest-Script-Lock:

→ **R1.4** (Job-UX / Progress-Polling)

Noch gesperrt: R1.5–R1.6, echte Provider, Style References,
Shared Working Media, neue Produktphase.
