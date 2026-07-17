> **RECONSTRUCTED_BOOTSTRAP**
>
> - erstellt, weil nach vollständiger Repository- und Dateisystemsuche kein historisches Original auffindbar war
> - gilt ab dem Bootstrap-Commit als Repositoryvertrag
> - erhebt keinen Anspruch, den exakten Wortlaut früherer, nicht auffindbarer Dokumente wiederzugeben
> - basiert auf akzeptiertem Handoff, bestehendem Code, Tests, dokumentierten Architekturentscheidungen und Audit-Referenzen
> - ungeklärte externe API-, OAuth-, Lizenz- und Providerdetails bleiben **UNKNOWN**

# Master Plan — Discovery V2

Produktpipeline und Phasenrahmen für Discovery V2. Untergeordnet gegenüber `.cursor/rules/*` und `docs/DECISIONS.md`.

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

## Vollständige Produktpipeline

```text
Projekt und Assets
→ Skript und Visual Beats
→ Coverage Audit
→ Stock-Supplementation
→ Script Lock
→ ElevenLabs
→ LLM-Pausenregie
→ Python-Timingauflösung
→ Visual Edit Plan
→ Humanity Review
→ Feasibility und Repair
→ Freigabe
→ OTIO-Export
```

## Implementierter Vorlauf (Phasen 1–8, Stand Bootstrap)

Bereits im Repository realisiert und freigegeben:

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

Nur **editorial-ready** Visual Observations (accepted, aktuell, gültig) sind Eingabe für Phase 9.

## Makrophasen (Alpha)

| Phase | Inhalt | Status-Rahmen |
|---|---|---|
| 7 | Media Intake | abgeschlossen |
| 8 | Assetanalyse (Contracts → Prepare → Fake Vision → Review) | abgeschlossen |
| 9 | Editorial Core und Coverage | nächster erlaubter Produktschritt |
| 10 | Supplementation und Script Lock | geplant |
| 11 | Voice, Pausen und Timing | geplant |
| 12 | Visual Edit Plan und Quality | geplant |
| 13 | Review und OTIO | geplant |

Ausführungsbündelung: `docs/ALPHA_EXECUTION_MANIFEST.md` (untergeordnet).

### Phase 9 — Editorial Core und Coverage

Project Brief, Narrative Plan, Hook-Varianten, Script Draft, Claims, Sätze, Visual Beats, Visual Intents, Coverage Audit. Noch kein Script Lock.

### Phase 10 — Supplementation und Script Lock

Coverage-Gaps, Eskalationsreihenfolge, providerneutrale Supplementation, Script-Lock-Gate, Stale-Regeln. Adobe OAuth bleibt **UNKNOWN** bis eigenes Gate.

### Phase 11 — Voice, Pausen und Timing

Fake Voice zuerst; optional ElevenLabs hinter Provider-Gate; LLM-Pausenfunktion; Python-Timingauflösung; Narration Timeline.

### Phase 12 — Visual Edit Plan und Quality

Konkrete Shot-Instanzen; Satz/Shot Many-to-Many; Humanity & Authenticity Review; Feasibility; deterministische und redaktionelle Repairs.

### Phase 13 — Review und OTIO

Editorial Review; Export Validation; OTIO Export; Reparse; kompletter Alpha-End-to-End-Smoke (MANUAL).

## Nicht-Ziele dieses Plans

- Classic-/Without-VO-Refactor
- stille Provideraktivierung
- AUTOMATIC-Orchestrierung im Alpha
- Behauptung, historische Originaldokumente wiederherzustellen
