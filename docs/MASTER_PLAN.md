> **RECONSTRUCTED_BOOTSTRAP**
>
> - Dokumente wurden für dieses Repository neu konsolidiert.
> - Andere Projekte sind keine fachliche Quelle.
> - Übernommene Dokumentstrukturen besitzen keine normative Bedeutung.
> - Verbindlich ist ausschließlich der für Discovery V2 verifizierte Inhalt ab dem Bereinigungscommit.
> - Nicht belegte externe Details bleiben **UNKNOWN**.
> - Kein Anspruch auf wiedergefundene historische Originale.

# Master Plan — Discovery V2

Produktpipeline und Phasenrahmen für Discovery V2 in diesem Repository.
Untergeordnet gegenüber `.cursor/rules/*` und `docs/DECISIONS.md`.

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

## Implementierter Vorlauf (Phasen 7–8)

Belegt durch Code, Tests und akzeptierten Handoff:

```text
Projekt anlegen (discovery_v2)
→ Inventory (read-only)
→ Auswahl bestätigen
→ Asset Registry
→ Technische Validierung
→ Media Intake (Copy / Remux / Transcode / TIFF→PNG)
→ Analysis Contracts + Eligibility
→ Shot-/Frame-Prepare
→ Fake Vision Model Analysis
→ Observation Review / Editorial-Ready-Gate
```

Eingabe in Phase 9: nur editorial-ready Visual Observations
(`accepted`, aktuelle Identity/Config, gültiges Schema) — Code / D-8D-004.

## Geplante Alpha-Produktpipeline (Phasen 9–13)

Reihenfolge laut D-8D-001 und `docs/ALPHA_EXECUTION_MANIFEST.md`
(noch nicht implementiert):

```text
Editorial Core (Brief, Script, Visual Beats/Intents)
→ Coverage Audit
→ Stock-Supplementation (providernentral; Adobe OAuth UNKNOWN)
→ Script Lock
→ Voice (Fake zuerst; optional ElevenLabs hinter Gate)
→ LLM-Pausenfunktion
→ Python-Timingauflösung
→ Visual Edit Plan
→ Humanity & Authenticity Review
→ Feasibility und Repair
→ Editorial Review / Freigabe
→ OTIO-Export (+ Validation / Reparse)
```

Verbindliche Reihenfolgen innerhalb dieses Plans:

- Coverage vor Stock-Supplementation
- Script Lock vor Voice
- LLM-Pausenfunktion vor Python-Timingauflösung
- Humanity Review und Feasibility vor Freigabe/Export

## Makrophasen

| Phase | Inhalt | Status |
|---|---|---|
| 7 | Media Intake | APPROVED (Handoff) |
| 8 | Assetanalyse (8A–8D) | APPROVED (Handoff) |
| 9 | Editorial Core und Coverage | nächster erlaubter Produktschritt; nicht begonnen |
| 10 | Supplementation und Script Lock | geplant |
| 11 | Voice, Pausen und Timing | geplant |
| 12 | Visual Edit Plan und Quality | geplant |
| 13 | Review und OTIO | geplant |

Ausführungsbündelung: `docs/ALPHA_EXECUTION_MANIFEST.md` (untergeordnet, D-8D-002).

### Phase 9 — Mindestumfang (Manifest)

Project Brief, Narrative Plan, Hook-Varianten, Script Draft, Claims, Sätze,
Visual Beats, Visual Intents, Coverage Audit; noch kein Script Lock.

### Phase 10 — Mindestumfang (Manifest)

Coverage-Gaps, Eskalationsreihenfolge, providerneutrale Supplementation,
Script-Lock-Gate, Stale-Regeln. Adobe OAuth: **UNKNOWN**.

### Phase 11 — Mindestumfang (Manifest)

Fake Voice zuerst; optional ElevenLabs hinter Provider-Gate; LLM-Pausenfunktion;
Python-Timingauflösung; Narration Timeline.

### Phase 12 — Mindestumfang (Manifest)

Konkrete Shot-Instanzen; Satz/Shot Many-to-Many; Humanity & Authenticity Review;
Feasibility; deterministische und redaktionelle Repairs.

### Phase 13 — Mindestumfang (Manifest)

Editorial Review; Export Validation; OTIO Export; Reparse; Alpha-E2E-Smoke (MANUAL).

## Nicht-Ziele

- Classic-/Without-VO-Refactor
- stille Provideraktivierung
- AUTOMATIC-Orchestrierung im Alpha
- historische Originaldokumente als wiedergefunden darstellen
