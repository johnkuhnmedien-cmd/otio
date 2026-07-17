> **RECONSTRUCTED_BOOTSTRAP**
>
> - Dokumente wurden für dieses Repository neu konsolidiert.
> - Andere Projekte sind keine fachliche Quelle.
> - Übernommene Dokumentstrukturen besitzen keine normative Bedeutung.
> - Verbindlich ist ausschließlich der für Discovery V2 verifizierte Inhalt ab dem Bereinigungscommit.
> - Nicht belegte externe Details bleiben **UNKNOWN**.
> - Kein Anspruch auf wiedergefundene historische Originale.

# Editorial Quality — Discovery V2

## Grundsatz

LLM liefert redaktionelle Vorschläge; Python erzwingt Technik, Timing und Exportintegrität
(Handoff-Verantwortungsteilung). Alpha bleibt MANUAL — Manifest.

## Observation Review (implementiert, Phase 8D)

- `accepted` — Observation darf als editorial-ready Eingabe gelten (bei aktuellen Identities/Configs/gültigem Schema)
- `reanalyze_requested` — kein automatischer Model-Run
- `rejected` — nicht editorial-ready
- sonst `unreviewed`

Reviews sind append-only mit monotoner `review_revision` — Code / D-8D-005.

`accepted` bedeutet nicht Asset-Auswahl, Faktenfreigabe, Geo, Synthetic, Dramaturgie
oder Visual-Beat-Freigabe — D-8D-004.

## Geplante Gates (Manifest; nicht implementiert)

### Coverage Audit (Phase 9)

Coverage gegen Visual Beats/Intents und verfügbare editorial-ready Observations.
Details der Bewertungsmatrix: **UNKNOWN** bis Umsetzungsauftrag.

### Script Lock (Phase 10)

Script-Lock-Gate und Stale-Regeln vor Voice — Manifest.
Konkrete Stale-Trigger: **UNKNOWN** bis Umsetzungsauftrag.
Ohne gültigen Lock keine Voice-/Timing-Produktion — Manifest-Reihenfolge.

### Humanity & Authenticity Review (Phase 12)

Manuelles Qualitätsgate vor Freigabe — Manifest.
Bewertungskriterien im Detail: **UNKNOWN** bis Umsetzungsauftrag.

### Feasibility und Repair (Phase 12)

Python: deterministische Reparaturen; LLM: redaktionelle Reparaturvorschläge — Manifest.
Export erst nach bestandener Feasibility — Manifest-DoD.

### Editorial Review / Freigabe (Phase 13)

Nutzerfreigabe, Export Validation, Reparse — Manifest.

## Qualitätsverbote (Alpha)

- keine stillen Automationen (MANUAL)
- Shot/Satz nicht als stilles hartes 1:1 voraussetzen (Many-to-Many geplant in Phase 12)
- keine unvalidierten Timingdaten im Export
- keine OTIO-Medienreferenzen außerhalb completed Working Media
