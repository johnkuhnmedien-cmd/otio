> **RECONSTRUCTED_BOOTSTRAP**
>
> - Dokumente wurden für dieses Repository neu konsolidiert.
> - Andere Projekte sind keine normative Discovery-V2-Quelle.
> - Gelöschter GPT-Wissensstand ist keine Repositoryquelle.
> - Verbindlich ist der für Discovery V2 geprüfte Inhalt ab den Bootstrap- und Korrekturcommits.
> - Der Bootstrap beansprucht keine historische Wortlauttreue.
> - Nicht belegte externe Details bleiben **UNKNOWN**.

# Chief Dev Handoff — Discovery V2

Operative Details: `DISCOVERY_V2_HANDOFF.md`.

## Source-of-Truth-Reihenfolge

1. `.cursor/rules/00-core-architecture.mdc`
2. `.cursor/rules/01-step-discipline.mdc`
3. `docs/DECISIONS.md`
4. `docs/MASTER_PLAN.md`
5. `docs/ALPHA_SCOPE.md`
6. `docs/PIPELINE_SPEC.md`
7. `docs/MEDIA_LIFECYCLE.md`
8. `docs/EDITORIAL_QUALITY.md`
9. `docs/MODEL_ROUTING.md`
10. `docs/CLASSIC_MIGRATION_CONTRACT.md`
11. `docs/PROGRESS.md`
12. `docs/CHIEF_DEV_HANDOFF.md`
13. `docs/source_plans/*` (nachrangig; überschreibt nichts Höheres)

Untergeordnet: `docs/ALPHA_EXECUTION_MANIFEST.md`.

## Repository-Lage

- Branch: `cursor/discovery-v2-integration` · PR `#69`
- Phase 7–8: **APPROVED**
- Phase 9 Editorial Core / Coverage: **abgeschlossen** (Fake Text)
- Phase 10 Supplementation / Script Lock: **abgeschlossen** (Fake Stock)
- Phase 11 Voice / Pause / Timing: **vollständig abgeschlossen** (Fake Voice, Schema 18)
- Phase 12 Visual Edit / Humanity / Feasibility / Repair: **abgeschlossen** (Fake, Schema **19**)
- Registry-Schema: **19**
- Vision / Text Editorial / Stock / Voice: Fake-only
- Teststand: **2898 / 2879 / 18 / 1**
- **Aktueller Schritt: Phase 12 abgeschlossen; Phase 13 noch nicht begonnen**
- Plan Phase 12: `docs/source_plans/PHASE12_VISUAL_EDIT_HUMANITY_FEASIBILITY_REPAIR_PLAN.md`
- Entscheidungen: D-10-001 … D-10-008; D-11-001 … D-11-011; D-12-001 … D-12-008

## Nächste erlaubte Aktion

Nach Freigabe:

→ **Phase 13 planen** (Editorial Review, Export Validation, OTIO, Reparse)

Weiterhin gesperrt ohne eigene Gates:

- echte Stockprovider und Adobe OAuth / Lizenz / Auto-Download
- echte Text-/Vision-Provider
- ElevenLabs und echte Voiceprovider
- Phase-13-Produkt (finale Approval, Export Validation, OTIO, Reparse, NLE)

## Verbindliche Kurzregeln

- MANUAL Alpha-Standard; CHECKPOINT vorbereitet; AUTOMATIC post-alpha
- Script Lock vor Voice; Coverage vor Stock; LLM-Pausenregie vor Timing
- Visual Edit erst nach validierter Narration Timeline
- Humanity & Authenticity = eigener Review-Schritt vor Ready/Export
- Feasibility deterministisch; Repairs versionieren Pläne
- `ready_for_editorial_review` ≠ Exportfreigabe
- Working Media = einzige OTIO-Medienquelle; Classic `_otio/` read-only
- Gateways zentral; kein Default/Aktiv für reale Provider behaupten
- Visual Observation ≠ Fakten-/Assetfreigabe
- KI-Timelines = `NEGATIVE_REFERENCE`
- Sentence ≠ Beat ≠ Intent ≠ Editorial Shot ≠ Technical Shot ≠ Asset

## Alpha-DoD (Auszug)

MANUAL-Hauptpfad, keine Originaländerung, Stale-Gates, versionierte Artefakte,
validiertes Working Media, Humanity Review, Feasibility, Freigabe, parsebares OTIO,
keine neuen Discovery-bedingten Testfehler.
