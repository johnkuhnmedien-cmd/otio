> **RECONSTRUCTED_BOOTSTRAP**
>
> - erstellt, weil nach vollständiger Repository- und Dateisystemsuche kein historisches Original auffindbar war
> - gilt ab dem Bootstrap-Commit als Repositoryvertrag
> - erhebt keinen Anspruch, den exakten Wortlaut früherer, nicht auffindbarer Dokumente wiederzugeben
> - basiert auf akzeptiertem Handoff, bestehendem Code, Tests, dokumentierten Architekturentscheidungen und Audit-Referenzen
> - ungeklärte externe API-, OAuth-, Lizenz- und Providerdetails bleiben **UNKNOWN**

# Chief Dev Handoff — Discovery V2

Kurzlage für technische Führung. Arbeitsdetails und Commit-Historie: `DISCOVERY_V2_HANDOFF.md`.

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
13. `docs/source_plans/*`

Untergeordnet: `docs/ALPHA_EXECUTION_MANIFEST.md`. Operativer Handoff: `DISCOVERY_V2_HANDOFF.md`.

## Repository-Lage (Bootstrap)

- Branch: `cursor/discovery-v2-integration`
- PR: `#69`
- Phase 7 Media Intake: **APPROVED**
- Phase 8 Assetanalyse (8A–8D): **APPROVED**
- Registry-Schema: **14**
- Vision: Fake-only (`provider=fake`); echte Provider gesperrt
- Nächster erlaubter Produktschritt: **Phase 9** (nur mit eigenem Auftrag)
- Keine Phase 9 in diesem Bootstrap-Auftrag

## Architektur in einem Satz

Modularer Streamlit-Monolith: UI → Application → Domain → Adapters → Persistence; SQLite + versionierte JSON unter `_otio_v2/`; Classic `_otio/` read-only; nur completed Working Media für Produktion/Export.

## Sofortige Führungsentscheidungen

- Diese Dokumente sind **rekonstruierte Bootstrap-SoT**, keine wiedergefundenen Originale.
- Bei Konflikt: Regeln → DECISIONS → MASTER_PLAN → …; Manifest darf nicht still überschreiben.
- Provider-Gates getrennt halten; keine Baseline-Fremdreparaturen ohne Auftrag.
- Adobe OAuth / Lizenzdetails / nicht vorhandene Vision-Payloads: **UNKNOWN**.

## Definition of Done (Alpha, Auszug)

MANUAL-Hauptpfad, keine Originaländerung, Stale-Gates, versionierte Artefakte, validiertes Working Media, Humanity Review, Feasibility, Nutzerfreigabe, parsebares OTIO, keine neuen Discovery-bedingten Testfehler.
