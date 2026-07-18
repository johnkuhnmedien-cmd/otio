# Progress — Discovery V2 / Alpha

## Aktueller Stand

**Visual Edit Rework V1 (Fixtures) abgeschlossen — Schema weiterhin 20.**

- Chief-Dev-Status Alpha-Produktstand: **APPROVED** (Commit `1ac7fba`)
- Script-Lock Realtest: **erfolgreich**
- R1.1–R1.3: abgeschlossen
- Releaseklasse: **interner MANUAL-/Fake-Alpha**
- Schema: **20**
- Provider: **Fake-only**
- Branch: `cursor/discovery-v2-integration` · PR `#69`
- Plan: `docs/source_plans/ALPHA_VISUAL_EDIT_REPAIR_REWORK_PLAN.md`
- Visual Edit Rework **V1 Fixtures**: **abgeschlossen** (`70ffe6e`)
- Reproduktion: Fake-Planer nutzt `candidates[0]` trotz 6 gültiger Assets →
  E3 (`ASSET_REUSE_MAX=3`) + E4 (`SOURCE_RANGE_OVERLAP_RATIO_MAX=0.90`);
  Repair `vary_first_local_motif` ohne ausführbare Reassignment-Ops;
  wiederholte Feasibility → gleiche Issue-Signatur
- **Nächster erlaubter Schritt nach Freigabe: Visual Edit Rework V2**
  (Planer-Härtung: Diversität, E3-/E4-aware Assignment)
- **V3/V4 und R1.4 weiterhin gesperrt**
- Keine neue Produktphase · echte Provider gesperrt

## Phase-Status

| Phase | Status |
|---|---|
| 7–13 Produktpfad | freigegeben / Fake-Alpha |
| R1.1–R1.3 | abgeschlossen |
| Visual Edit Repair Rework Plan | dokumentiert |
| Visual Edit Rework V1 Fixtures | **abgeschlossen** (`70ffe6e`) |
| Visual Edit Rework V2 Planer | nächster erlaubter Schritt (nach Freigabe) |
| Visual Edit Rework V3–V4 | **gesperrt** |
| R1.4 Job-UX / Progress-Polling | **gesperrt** |
| R1.5–R1.6 | gesperrt |

## Reproduzierende Node-IDs (V1)

- `tests/test_discovery_v2_visual_edit_rework_v1.py::test_fixture_has_multiple_valid_assets_but_fake_plan_uses_first_asset_only`
- `tests/test_discovery_v2_visual_edit_rework_v1.py::test_reproduced_plan_fails_e3_asset_reuse`
- `tests/test_discovery_v2_visual_edit_rework_v1.py::test_reproduced_plan_fails_e4_source_range_overlap`
- `tests/test_discovery_v2_visual_edit_rework_v1.py::test_current_repair_proposal_has_no_executable_reassignment`
- `tests/test_discovery_v2_visual_edit_rework_v1.py::test_repeated_feasibility_of_unchanged_plan_has_same_issue_signature`
- `tests/test_discovery_v2_visual_edit_rework_v1.py::test_reproduction_uses_no_gateway_and_no_media_io`

Policy: `otio_app/discovery_v2/domain/visual_edit.py` —
`ASSET_REUSE_MAX`, `SOURCE_RANGE_OVERLAP_RATIO_MAX`.

## Teststand

**2994 collected / 2975 passed / 18 failed / 1 skipped** (~270s)

Vergleich zur Baseline **2988 / 2969 / 18 / 1**:
- +6 V1-Reproduktionstests, alle grün
- 18 bekannte Classic/Without-VO Baseline-Fehler unverändert
- 1 bekannter VFR-Skip unverändert
- 0 xfailed / 0 xpassed

## Nächste erlaubte Aktivität

Nach Freigabe:

→ **Visual Edit Rework V2** (Planer-Härtung)

Noch gesperrt: V3 Repairs, V4 Loop/UI, R1.4–R1.6, echte Provider,
Style References, Shared Working Media, neue Produktphase.
