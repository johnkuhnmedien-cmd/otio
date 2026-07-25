# LOCAL_LIVE_ADOBE_SMOKE — Benutzeraktion erforderlich

Cloud-Agenten haben keinen Zugriff auf das reale Adobe-Stock-Konto.

## Voraussetzungen

1. Lokaler Checkout auf `cursor/adobe-stock-research-import-3982` (PR #83)
2. Gültige Adobe-OAuth-Anmeldung in der App (bereits lizenziertes Video)
3. Bekannte Content-ID mit `state=purchased` (keine bewusste Neu-Lizenz)

## Ablauf

```bash
streamlit run app.py
# Route: adobe-stock-import
# OAuth-Konto prüfen → Projekt wählen → Import starten
```

## Pflichtprüfungen

- lokale Datei vorhanden, Dauer > 0
- kein `/Watermarked/`-Pfad
- `license_history == 0` in der Diagnose
- Content/License nicht unnötig wiederholt
- Request-Zähler dokumentieren
- Screenshot + Diagnose-JSON **redigiert** (Tokens, signierte URLs, PII entfernen)

## Status

`LOCAL_LIVE_ADOBE_SMOKE: USER_ACTION_REQUIRED`
