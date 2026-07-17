> **RECONSTRUCTED_BOOTSTRAP**
>
> - Dokumente wurden für dieses Repository neu konsolidiert.
> - Andere Projekte sind keine fachliche Quelle.
> - Übernommene Dokumentstrukturen besitzen keine normative Bedeutung.
> - Verbindlich ist ausschließlich der für Discovery V2 verifizierte Inhalt ab dem Bereinigungscommit.
> - Nicht belegte externe Details bleiben **UNKNOWN**.
> - Kein Anspruch auf wiedergefundene historische Originale.

# Model Routing — Discovery V2

## Discovery Vision (implementiert)

Aufrufpfad — Code Phase 8C:

```text
UI / Application (model_analysis_service)
  → Discovery Vision Gateway
      → FakeVisionAdapter
```

| Einstellung | Wert | Beleg |
|---|---|---|
| Provider | `fake` | `vision_config.py` |
| Model | `fake-vision-v1` | `vision_config.py` |
| Enabled | `true` | `vision_config.py` |
| Echte Provider | gesperrt | D-8D-003 / Handoff |

- Consent pro Model-Run — Code / Tests
- Cache über Identity-, Config-Versionen und Frame-Hash-Fingerprint — Code / Tests
- Kein HTTP/SDK in Discovery Vision — Tests / Handoff

## Text-LLM und Voice (geplant)

- Text-LLM für Phase 9+: über bestehende konfigurierbare Infrastruktur nutzbar, aber nur hinter Discovery-Application und ohne hart codierte Modelle in Discovery-Fachmodulen — Manifest / Handoff
- Konkrete Default-Modell-IDs für Discovery-Editorial: **UNKNOWN**
- Voice: Fake Voice zuerst; optional ElevenLabs hinter Phase-11-Gate — Manifest
- Adobe Stock / OAuth: **UNKNOWN** / später — Manifest

## Provider-Gates

| Gate | Status |
|---|---|
| Vision (nicht-fake) | gesperrt |
| Text-LLM Discovery Editorial | Phase 9+; Details UNKNOWN |
| ElevenLabs | gesperrt bis Phase 11 |
| Adobe Stock | UNKNOWN |

Keine stillen Provideraktivierungen — Manifest.

## Verbote

- hart codierte Modell-IDs in Discovery-Domain-/Application-Fachmodulen
- Secret-Leak in Logs/Artefakten/Fehlermeldungen
- ungefragte Uploads
- Vision über Text-only-Classic-Client als Discovery-Gateway
- Model-Start durch Streamlit-Rerun ohne Consent

## LLM- vs. Python-Zuständigkeit

Siehe `00-core-architecture.mdc`.
