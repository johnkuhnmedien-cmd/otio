# Progress — Discovery V2 / Alpha

## Aktueller Stand

**Phase 11 — Voice, Pausenregie und Timing ist freigegeben / abgeschlossen (Fake Voice + MANUAL).**

Contract Hardening R1 angewendet (Schema **18**).

**Phase 12 — Visual Edit / Humanity / Feasibility / Repair: in Planung.**

Plan: `docs/source_plans/PHASE12_VISUAL_EDIT_HUMANITY_FEASIBILITY_REPAIR_PLAN.md`

- Schema: **18** (Phase-12-Zielschema **19**, noch nicht migriert)
- Fake Voice aktiv (`provider=fake`, `fake-neutral-v1`)
- Pause Direction über DiscoveryTextGateway (`pause_direction`)
- Resolved Narration Timeline (rationale Timebases)
- Echte Stock-/Adobe-/Text-/Vision-/Voice-Provider (inkl. ElevenLabs): gesperrt
- Branch: `cursor/discovery-v2-integration` · PR `#69`

## Phase-Status

| Phase | Status |
|---|---|
| 7 Media Intake | abgeschlossen |
| 8A–8D Assetanalyse | formal abgeschlossen |
| SoT Bootstrap | freigegeben |
| 9 Editorial Core / Coverage | freigegeben / abgeschlossen (Fake) |
| 10 Supplementation / Script Lock | freigegeben / abgeschlossen (Fake) |
| 11 Voice / Pause / Timing | **freigegeben / abgeschlossen (Fake)** + Contract Hardening R1 |
| 12 Visual Edit / Humanity / Feasibility / Repair | **in Planung** (keine Produktimplementierung) |
| 13 Review / OTIO | gesperrt |

## Teststand (nach Phase 11 Contract Hardening R1)

- Baseline 8D: 2806 / 2787 / 18 / 1
- Nach Phase 9: 2831 / 2812 / 18 / 1
- Nach Phase 10: 2850 / 2831 / 18 / 1
- Nach Phase 11: 2877 / 2858 / 18 / 1
- Nach Contract Hardening R1: **2883 collected / 2864 passed / 18 failed / 1 skipped**

18 bekannte Baseline-Fehler unverändert; 1 bekannter VFR-Skip unverändert;
keine neuen Fehler oder Skips (+6 Hardening-Tests, alle grün).

## Nächster erlaubter Schritt

Nach Freigabe dieses Phase-12-Plans: **Phase-12-Implementierung**
(Visual Edit Plan, Humanity, Feasibility, Repair — Fake only).

Gesperrt: ElevenLabs und echte Voiceprovider, echte Stock/Adobe,
echte Text-/Vision-Provider, Phase 13, OTIO-Export/Reparse/NLE.
