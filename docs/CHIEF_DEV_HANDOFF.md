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
- Chief-Dev-Status: **`APPROVED_WITH_CONDITIONS`**
- Releaseklasse: **interner MANUAL-/Fake-Alpha**
- Phase 7–13: **abgeschlossen** (Fake / MANUAL)
- Registry-Schema: **20**
- OTIO-Profil: `discovery-otio-export-v1` · OpenTimelineIO **0.18.1**
- Vision / Text / Stock / Voice: **Fake-only**
- Adobe: **UNKNOWN**
- NLE: **nur lokaler OTIO-Serialize/Reparse-Nachweis**
- Teststand: **2915 collected / 2896 passed / 18 failed / 1 skipped**
- Alpha-E2E: **bestanden** (lokal, Fake-only)
- Release-Readiness-Verify: **`ALPHA_READY_WITH_LIMITATIONS`**
- **Aktueller Schritt: Alpha-Release Closeout — keine neue Produktphase**
- Plan Phase 13: `docs/source_plans/PHASE13_EDITORIAL_APPROVAL_OTIO_E2E_PLAN.md`
- Entscheidungen: D-10-001 … D-10-008; D-11-001 … D-11-011; D-12-001 … D-12-008; D-13-001 … D-13-008

## Nächste erlaubte Aktivität

→ **Kontrollierter Alpha-Merge beziehungsweise interne Alpha-Erprobung**

Keine neue Produktphase freigegeben.

Weiterhin gesperrt ohne eigene Gates:

- echte Stock-/Text-/Vision-/Voice-Provider
- proprietäre NLE-Exporte (Premiere / DaVinci / Final Cut)
- Cloud-Upload / Publishing
- AUTOMATIC-Orchestrierung (Post-Alpha)

## Verbindliche Kurzregeln

- MANUAL Alpha-Standard
- Approval nur durch Menschen; Checkbox nie vorselektiert
- Export Validation vor jedem OTIO
- OTIO nur Working Media + aktuelle Narration-WAVs
- Completed erst nach Reparse + Semantik
- Classic `_otio/` read-only; Discovery Export unter `_otio_v2/export/`
- KI-Timelines = `NEGATIVE_REFERENCE`
- Gateways zentral; keine stillen Provider

## Alpha-DoD (Auszug)

MANUAL-Hauptpfad, keine Originaländerung, Stale-Gates, versionierte Artefakte,
validiertes Working Media, Humanity Review, Feasibility, Nutzerfreigabe,
parsebares OTIO mit Reparse, keine neuen Discovery-bedingten Testfehler.
