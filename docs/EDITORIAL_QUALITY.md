> **RECONSTRUCTED_BOOTSTRAP**
>
> - erstellt, weil nach vollständiger Repository- und Dateisystemsuche kein historisches Original auffindbar war
> - gilt ab dem Bootstrap-Commit als Repositoryvertrag
> - erhebt keinen Anspruch, den exakten Wortlaut früherer, nicht auffindbarer Dokumente wiederzugeben
> - basiert auf akzeptiertem Handoff, bestehendem Code, Tests, dokumentierten Architekturentscheidungen und Audit-Referenzen
> - ungeklärte externe API-, OAuth-, Lizenz- und Providerdetails bleiben **UNKNOWN**

# Editorial Quality — Discovery V2

Redaktionelle Qualitätsgates und Verantwortungsgrenzen.

## Grundsatz

LLM schlägt redaktionelle Strukturen vor; Python erzwingt technische Machbarkeit, Zeiten und Exportintegrität. Keine Phase überspringt MANUAL-Freigaben im Alpha.

## Observation Review (implementiert)

Entscheidungen:

- `accepted` — Observation darf als editorial-ready Eingabe angeboten werden (bei aktuellen Identities/Configs/gültigem Schema)
- `reanalyze_requested` — keine Auto-Analyse; manueller neuer Model-Run erforderlich
- `rejected` — nicht editorial-ready
- sonst `unreviewed`

Reviews sind append-only mit monotoner `review_revision`.

`accepted` bedeutet **nicht**: Asset-Auswahl, Faktenfreigabe, Geo, Synthetic, Dramaturgie oder Visual-Beat-Freigabe.

## Geplante redaktionelle Gates

### Coverage Audit (Phase 9)

- Visual Beats / Intents gegen verfügbare editorial-ready Observations und Working Media
- Gaps dokumentieren; kein stilles Auffüllen ohne Phase 10

### Script Lock (Phase 10)

- Skript und abhängige Beats werden eingefroren
- Stale bei Identity-/Coverage-/Supplement-Änderungen
- ohne gültigen Lock keine Voice-/Timing-Produktion

### Humanity & Authenticity Review (Phase 12)

- manuelle Prüfung auf Authentizität, Würde, irreführende Synthetik/Kontexte
- Blocker für Freigabe bei unresolved Findings

### Feasibility und Repair (Phase 12)

- Python: deterministische Timing-/Range-/Machbarkeitsreparaturen
- LLM: redaktionelle Reparaturvorschläge
- Export erst nach bestandener Feasibility

### Editorial Review / Freigabe (Phase 13)

- Nutzerfreigabe vor OTIO-Export
- Export Validation + Reparse

## Qualitätsverbote

- Shot ≠ Satz als hartes 1:1 (Many-to-Many in Phase 12)
- keine stillen Automationen (MANUAL)
- keine unvalidierten Timingdaten im Export
- keine OTIO-Pfade außerhalb completed Working Media
