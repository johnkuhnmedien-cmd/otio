> **RECONSTRUCTED_BOOTSTRAP**
>
> - Dokumente wurden für dieses Repository neu konsolidiert.
> - Andere Projekte sind keine fachliche Quelle.
> - Übernommene Dokumentstrukturen besitzen keine normative Bedeutung.
> - Verbindlich ist ausschließlich der für Discovery V2 verifizierte Inhalt ab dem Bereinigungscommit.
> - Nicht belegte externe Details bleiben **UNKNOWN**.
> - Kein Anspruch auf wiedergefundene historische Originale.

# Chief Dev Handoff — Discovery V2

Kurzlage für technische Führung. Operative Details: `DISCOVERY_V2_HANDOFF.md`.

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
13. `docs/source_plans/*` (reserviert; derzeit ohne normative Inhalte)

Untergeordnet: `docs/ALPHA_EXECUTION_MANIFEST.md`. Operativer Handoff: `DISCOVERY_V2_HANDOFF.md`.

## Repository-Lage

- Branch: `cursor/discovery-v2-integration`
- PR: `#69`
- Phase 7 Media Intake: **APPROVED**
- Phase 8 Assetanalyse (8A–8D): **APPROVED**
- Registry-Schema: **14**
- Vision: Fake-only (`provider=fake`); echte Provider gesperrt
- Teststand Phase-8D-Closeout: 2806 collected / 2787 passed / 18 failed / 1 skipped
- Nächster erlaubter Produktschritt: **Phase 9** (nur mit eigenem Auftrag)
- Phase 9 nicht begonnen

## Architektur (Kurz)

UI → Application → Domain → Adapters → Persistence; SQLite + versionierte JSON unter
`_otio_v2/`; Classic `_otio/` read-only; Analyse auf completed Working Media.

## Führungsregeln

- Bootstrap-Dokumente sind für dieses Repository neu konsolidiert; andere Projekte sind keine SoT.
- Bei Konflikt: Regeln → DECISIONS → MASTER_PLAN → …
- Provider-Gates getrennt; keine Baseline-Fremdreparaturen ohne Auftrag.
- Adobe OAuth, produktive Vision-Modell-IDs und nicht implementierte Editorial-Details: **UNKNOWN**.

## Alpha-DoD (Manifest, Auszug)

MANUAL-Hauptpfad, keine Originaländerung, Stale-Gates, versionierte Artefakte,
validiertes Working Media, Humanity Review, Feasibility, Nutzerfreigabe,
parsebares OTIO, keine neuen Discovery-bedingten Testfehler.
