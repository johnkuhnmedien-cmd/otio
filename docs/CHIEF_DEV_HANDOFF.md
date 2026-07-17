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
- Phase 9–11: **abgeschlossen** (Fake)
- Phase 12 Visual Edit / Humanity / Feasibility / Repair: **abgeschlossen** (Fake)
- Registry-Schema: **19**
- Vision / Text / Stock / Voice: Fake-only
- Teststand: **2898 / 2879 / 18 / 1**
- **Aktueller Schritt: Phase-13-Planung**
- Plan Phase 12: `docs/source_plans/PHASE12_VISUAL_EDIT_HUMANITY_FEASIBILITY_REPAIR_PLAN.md`
- Plan Phase 13: `docs/source_plans/PHASE13_EDITORIAL_APPROVAL_OTIO_E2E_PLAN.md`
- Entscheidungen: D-10-001 … D-10-008; D-11-001 … D-11-011; D-12-001 … D-12-008
  (`docs/DECISIONS.md` unverändert in diesem Planungsauftrag)
- OTIO-Export: **noch nicht implementiert**
- Installierte Bibliothek (Planungsprüfung): `opentimelineio==0.18.1`

## Nächste erlaubte Aktion

Nach Freigabe dieses Phase-13-Plans:

→ **Phase-13-Implementierung** (Editorial Approval, Export Validation, OTIO, Reparse, Alpha-E2E)

Weiterhin gesperrt ohne eigene Gates:

- echte Stock-/Text-/Vision-/Voice-Provider
- proprietäre NLE-Exporte (Premiere / DaVinci / Final Cut)
- Cloud-Upload / Publishing

## Verbindliche Kurzregeln

- MANUAL Alpha-Standard
- `ready_for_editorial_review` ≠ Exportfreigabe
- Approval nur durch Menschen; Checkbox nie vorselektiert
- Working Media + aktuelle Narration-WAVs = einzige OTIO-Quellen
- Classic `_otio/` read-only; Discovery Export unter `_otio_v2/export/`
- KI-Timelines = `NEGATIVE_REFERENCE`
- Gateways zentral; keine stillen Provider

## Alpha-DoD (Auszug)

MANUAL-Hauptpfad, keine Originaländerung, Stale-Gates, versionierte Artefakte,
validiertes Working Media, Humanity Review, Feasibility, Nutzerfreigabe,
parsebares OTIO, keine neuen Discovery-bedingten Testfehler.
