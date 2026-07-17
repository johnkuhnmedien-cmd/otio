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
13. `docs/source_plans/*` (nachrangig; ggf. leer; überschreibt nichts Höheres)

Untergeordnet: `docs/ALPHA_EXECUTION_MANIFEST.md`.

## Repository-Lage

- Branch: `cursor/discovery-v2-integration` · PR `#69`
- Phase 7–8: **APPROVED** · Schema **14** · Fake Vision only
- Teststand 8D-Closeout: 2806 / 2787 / 18 / 1
- Nächster Produktschritt: Phase 9 (nicht begonnen)

## Verbindliche Kurzregeln

- MANUAL Alpha-Standard; CHECKPOINT vorbereitet (nicht fertig behaupten); AUTOMATIC post-alpha
- Hauptpipeline inkl. LLM-Pausenregie vor Python-Timing; Script Lock vor Voice
- Coverage vor Stock; Adobe-Reihenfolge und Stock-Eskalation verbindlich
- Humanity & Authenticity = eigener Review-Schritt
- Working Media = einzige OTIO-Medienquelle; Classic `_otio/` read-only
- Gateways zentral; OpenRouter/direkt konfigurierbar; kein Default/Aktiv behaupten
- Echter Vision-Provider gesperrt; Visual Observation ≠ Fakten-/Assetfreigabe
- KI-Timelines = `NEGATIVE_REFERENCE`

## Alpha-DoD (Auszug)

MANUAL-Hauptpfad, keine Originaländerung, Stale-Gates, versionierte Artefakte,
validiertes Working Media, Humanity Review, Feasibility, Freigabe, parsebares OTIO,
keine neuen Discovery-bedingten Testfehler.
