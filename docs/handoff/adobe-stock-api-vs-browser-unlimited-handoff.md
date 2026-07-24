# Adobe Stock — API vs. Browser-Unlimited (Dev-Handoff)

Stand: Branch `cursor/adobe-stock-research-import-3982` (PR #83)  
Seite in der App: **Adobe Stock Import** (`adobe-stock-import`)

---

## 1. Problem klar benannt

**Problemname:** *Stock-API Video-Entitlement Mismatch*  
(Browser-Unlimited ≠ OAuth/Stock-API-Entitlement)

### Symptom
- Auf **stock.adobe.com** kann der Nutzer Videos mit seinem **Unlimited**-Abo lizenzieren/downloaden.
- Über unsere App (`Content/License` mit OAuth-Token) schlagen viele Video-Downloads fehl mit:
  - `purchase_details.state = cancelled`
  - `size = Comp`
  - Download-URL unter `/Rest/Libraries/Watermarked/...` (Wasserzeichen, keine Vollversion)
  - `available_entitlement.full_entitlement_quota` oft nur  
    `{ "cct_pro_unlimited_images": "unlimited" }`
  - Video-`quota = 0`

### Was es **nicht** ist
- kein generelles „zu viele Anfragen“ / Rate-Limit als Hauptursache
- kein fehlendes Unlimited-Abo im Browser
- kein reiner UI-Bug in Streamlit

### Was es **ist**
Die **Stock Licensing API** sieht für das aktuelle OAuth-Token bei Videos oft **kein Video-Unlimited**, obwohl der Browser mit demselben menschlichen Nutzer Unlimited nutzt.

Bekannte Mitursachen / Prüfpunkte:
1. **OAuth-App war „In Development“** → in Adobe Developer Console **Push to Production** (bereits gemacht; danach OAuth neu einloggen).
2. **Falsche Adobe-ID** beim OAuth (Privat vs. Team / andere E-Mail).
3. **Creative Cloud Pro/Plus**: Adobe dokumentiert, dass Unlimited-Pläne für die Stock-API oft **nur mit Freigabe** nutzbar sind (`stockapis@adobe.com`). Browser ≠ API.
4. **Nebenbei lokal:** Zielordner unter iCloud Drive + „iCloud-Speicher ist voll“ kann Downloads zusätzlich zerstören — Ziel besser lokal außerhalb iCloud.

### Teilweise erfolgreiche Downloads
Erklären sich typischerweise so:
- Asset war schon lizenziert → **LicenseHistory** / Content/Info liefert Voll-URL, oder
- einzelne Assets unter 600 MB als Video_4K, oder
- lokale Schreibfehler (iCloud) vs. echte API-`cancelled`-Fehler vermischen sich in der Wahrnehmung.

---

## 2. Diagnose-Funktion in der App

### UI
Pfad: **Adobe Stock Import** → Bereich **Adobe-Anmeldung (OAuth)**

1. Nach Login wird angezeigt:  
   `OAuth-Konto: {email} · sub={sub}`  
   → muss mit dem Konto auf stock.adobe.com übereinstimmen.

2. Expander:  
   **„API-Konto / Entitlement prüfen (wenn Browser-Unlimited geht, API aber nicht)“**

3. Optional: Content-ID eines Videos eintragen (z. B. gerade im Browser lizenziert).

4. Button:  
   **„Member/Profile + License-Status abfragen“**

### Was der Button abfragt
| Anzeige | Adobe-Endpoint / Logik |
|---|---|
| Member/Profile **Standard** | `GET …/Member/Profile?license=Standard` (+ optional `content_id`) |
| Member/Profile **Video_HD** | `GET …/Member/Profile?license=Video_HD` (+ optional `content_id`) |
| Content/Info **Video_4K** | `Content/Info` purchase_details |
| Content/Info **Video_HD** | `Content/Info` purchase_details |
| LicenseHistory-Treffer | `Member/LicenseHistory` Suche nach Content-ID; nur Voll-URL (kein `/Watermarked/`) |

### Code-Stellen
| Rolle | Datei |
|---|---|
| UI Diagnose-Expander + Button | `otio_app/ui/adobe_oauth_panel.py` (`render_adobe_oauth_panel`) |
| OAuth E-Mail/sub aus JWT (unverifiziert, nur Anzeige) | `otio_app/services/adobe_stock_oauth.py` → `decode_access_token_claims` |
| Member/Profile-Zusammenfassung | `otio_app/services/supplement_sources/adobe_stock.py` → `_summarize_member_profile` |
| Heuristik „API sieht kein Video“ | dieselbe Datei → `entitlement_lacks_video`, `probe_video_entitlement` |
| Import nutzt History zuerst bei Videos | `otio_app/services/adobe_research_import.py` → `_license_and_download_to_path` |
| Kurz-Warnung auf Import-Seite | `otio_app/ui/adobe_research_import_page.py` |

### Erwartete Lesart der JSON-Antwort

**API sieht Video-Rechte (gut):**
- `full_entitlement_quota` enthält Video-/Credits-Keys oder Unlimited jenseits nur `…_images`
- Video-`quota` nicht dauerhaft `0` bei `license=Video_HD`
- nach Browser-Lizenz: Content/Info `state=purchased` **oder** LicenseHistory mit URL  
  `…/Rest/Libraries/Download/{id}/…` (nicht `…/Watermarked/…`)

**API sieht kein Video-Unlimited (Mismatch bestätigt):**
- nur `cct_pro_unlimited_images: unlimited`
- Video-`quota: 0`
- Content/License (Import-Fehler) → `state=cancelled`, `size=Comp`

---

## 3. Aktuelles App-Verhalten beim Import (kurz)

Strikt sequenziell pro Asset:
1. Pause  
2. **LicenseHistory zuerst** (bereits lizenzierte Clips)  
3. Content/Info → Content/License (`Video_4K`, Fallback `Video_HD`)  
4. Download als `.part` → Rename auf `{Kapitel}_Asset_NN.ext`  
5. Pause → nächstes Asset  

Zusätzlich: 4K nur wenn Datei ≤ 600 MB, sonst HD.

Fehlertext bei API-Mismatch: Konstante `VIDEO_ENTITLEMENT_HINT` in  
`otio_app/services/supplement_sources/adobe_stock.py`  
(benennt bewusst: Browser-Unlimited kann da sein, API-Token sieht es trotzdem nicht).

---

## 4. Was der Dev bitte prüfen / entscheiden soll

1. Diagnose-JSON (Video_HD Profile + Content/Info + History) für 1–2 Content-IDs sichern.  
2. OAuth-`email`/`sub` vs. stock.adobe.com-Konto vergleichen.  
3. Adobe Developer Console: Credential wirklich **In Production**? Stock API am OAuth Web App Credential?  
4. Mit Adobe klären: Ist CC Pro/Plus Unlimited für **diese** API-Integration freigeschaltet? (`stockapis@adobe.com`)  
5. Lokal: Download-Ziel **nicht** iCloud, wenn Speicher voll.

---

## 5. Minimaler Repro für den Dev

1. App starten, Adobe Stock Import öffnen, OAuth einloggen.  
2. Expander „API-Konto / Entitlement prüfen“.  
3. Content-ID eines Videos, das im Browser mit Unlimited geht.  
4. Button drücken, JSON kopieren.  
5. Parallel: denselben Clip im Browser lizenzieren → erneut Diagnose → History/Info prüfen.

Wenn Profile nur `cct_pro_unlimited_images` + Video-quota 0 zeigt, ist der Mismatch **bewiesen** — dann ist es kein OTIO-Download-Bug, sondern Adobe-API-Entitlement für dieses Token/diese Integration.
