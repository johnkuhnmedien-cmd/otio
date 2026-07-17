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
- Registry-Schema: **15**
- Vision / Text Editorial: Fake-only
- Teststand: **2831 / 2812 / 18 / 1**
- **Aktueller Schritt: Phase-10-Planung**
- Plan: `docs/source_plans/PHASE10_SUPPLEMENTATION_SCRIPT_LOCK_PLAN.md`
- Phase-10-Produktimplementierung: **nicht begonnen**

## Nächste erlaubte Aktion

Nach Freigabe des Phase-10-Plans:

→ **Phase-10-Implementierung** (Supplementation Fake + Script Lock)

Weiterhin gesperrt ohne eigene Gates:

- echte Stockprovider und Adobe OAuth / Lizenz / Auto-Download
- echte Text-/Vision-Provider
- Phase 11+ (Voice, Pausenregie, Timing, …)
- OTIO Export

## Verbindliche Kurzregeln

- MANUAL Alpha-Standard; CHECKPOINT vorbereitet; AUTOMATIC post-alpha
- Script Lock vor Voice; Coverage vor Stock; LLM-Pausenregie vor Timing
- Stock-Eskalation und Adobe-Medienfolge verbindlich; Preview ≠ Working Media
- Humanity & Authenticity = eigener späterer Review-Schritt
- Working Media = einzige OTIO-Medienquelle; Classic `_otio/` read-only
- Gateways zentral; kein Default/Aktiv für reale Provider behaupten
- Visual Observation ≠ Fakten-/Assetfreigabe
- KI-Timelines = `NEGATIVE_REFERENCE`

## Alpha-DoD (Auszug)

MANUAL-Hauptpfad, keine Originaländerung, Stale-Gates, versionierte Artefakte,
validiertes Working Media, Humanity Review, Feasibility, Freigabe, parsebares OTIO,
keine neuen Discovery-bedingten Testfehler.
