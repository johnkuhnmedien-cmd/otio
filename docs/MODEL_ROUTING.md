> **RECONSTRUCTED_BOOTSTRAP**
>
> - Dokumente wurden für dieses Repository neu konsolidiert.
> - Andere Projekte sind keine normative Discovery-V2-Quelle.
> - Gelöschter GPT-Wissensstand ist keine Repositoryquelle.
> - Verbindlich ist der für Discovery V2 geprüfte Inhalt ab den Bootstrap- und Korrekturcommits.
> - Der Bootstrap beansprucht keine historische Wortlauttreue.
> - Nicht belegte externe Details bleiben **UNKNOWN**.

# Model Routing — Discovery V2

## Zentrale Gateways

- Alle Text- und Vision-Modellaufrufe laufen über zentrale Gateways
- OpenRouter und direkte Anbieter sind grundsätzlich konfigurierbar
- Keine hart codierten Modelle in Domain- oder Fachmodulen
- Keine stillen Providerfallbacks

Nicht behaupten:

- OpenRouter sei aktuell Default
- ein realer Provider sei bereits aktiviert
- bestimmte Vision-Capabilities seien bereits nachgewiesen

## Discovery Vision (aktueller Stand)

```text
Application → Discovery Vision Gateway → FakeVisionAdapter
```

| Einstellung | Wert |
|---|---|
| Provider | `fake` |
| Model | `fake-vision-v1` |
| Enabled | `true` |
| Reale Provider | gesperrt / deaktiviert bis Freigabegate |

Consent pro Model-Run; Cache über Identity-/Config-Versionen und Frame-Hashes.
Konkrete reale Modell-IDs und Vision-Capabilities: **UNKNOWN** bis Gate.

## Provider-Gates

| Gate | Status |
|---|---|
| Vision (nicht-fake) | gesperrt |
| Text-LLM Editorial | Phase 9+; konfigurierbar, kein Default behaupten |
| ElevenLabs / Voice | gesperrt bis Phase 11; Fake Voice zuerst |
| Adobe Stock / OAuth-Variante | **UNKNOWN** / später |

## Voice und Pausenregie (geplant)

- keine finale Voice vor Script Lock
- nach Voice: LLM-Pausenregie, danach Python-Timingauflösung

## Verbote

- Secret-Leak
- ungefragte Uploads
- Vision über Text-only-Classic-Client als Discovery-Gateway
- Model-Start durch Streamlit-Rerun ohne Consent
