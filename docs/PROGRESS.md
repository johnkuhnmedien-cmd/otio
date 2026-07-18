# Progress — Discovery V2 / Alpha

## Aktueller Stand

**Visual Edit Rework V2 (Planer-Härtung) abgeschlossen — Schema weiterhin 20.**

- Chief-Dev-Status Alpha-Produktstand: **APPROVED** (Commit `1ac7fba`)
- Script-Lock Realtest: **erfolgreich**
- R1.1–R1.3: abgeschlossen
- Releaseklasse: **interner MANUAL-/Fake-Alpha**
- Schema: **20**
- Provider: **Fake-only**
- Branch: `cursor/discovery-v2-integration` · PR `#69`
- Plan: `docs/source_plans/ALPHA_VISUAL_EDIT_REPAIR_REWORK_PLAN.md`
- Visual Edit Rework **V1 Fixtures**: abgeschlossen (`70ffe6e`)
- Visual Edit Rework **V2 Planer**: **abgeschlossen** (`0fba7bd`)
- E3/E4-Policies unverändert: `ASSET_REUSE_MAX`, `SOURCE_RANGE_OVERLAP_RATIO_MAX`
- Fake liefert `ranked_candidates`; Python wählt E3-/E4-konform
- Ungültiger neuer Plan wird nicht Current
- Decisions: D-VE-REWORK-001…003
- **Manueller Realtest erforderlich** (USA_v2: neuen Visual Edit Plan erzeugen)
- **Nächster erlaubter Schritt nach Freigabe: Visual Edit Rework V3**
  (ausführbare Reassignment-Repairs)
- **V4 und R1.4 weiterhin gesperrt**
- Keine neue Produktphase · echte Provider gesperrt

## Phase-Status

| Phase | Status |
|---|---|
| 7–13 Produktpfad | freigegeben / Fake-Alpha |
| R1.1–R1.3 | abgeschlossen |
| Visual Edit Rework V1 Fixtures | abgeschlossen (`70ffe6e`) |
| Visual Edit Rework V2 Planer | **abgeschlossen** (`0fba7bd`) |
| Visual Edit Rework V3 Repairs | nächster erlaubter Schritt (nach Freigabe) |
| Visual Edit Rework V4 Loop/UI | **gesperrt** |
| R1.4 Job-UX / Progress-Polling | **gesperrt** |
| R1.5–R1.6 | gesperrt |

## Teststand

**3001 collected / 2982 passed / 18 failed / 1 skipped** (~278s)

Vergleich zur V1-Baseline **2994 / 2975 / 18 / 1**:
- +7 V2-Tests, alle grün
- 18 bekannte Classic/Without-VO Baseline-Fehler unverändert
- 1 bekannter VFR-Skip unverändert

## Nächste erlaubte Aktivität

Nach Freigabe und manuellem Realtest:

→ **Visual Edit Rework V3** (ausführbare Asset-/Range-Reassignment-Repairs)

Noch gesperrt: V4 Loop/UI, R1.4–R1.6, echte Provider, neue Produktphase.
