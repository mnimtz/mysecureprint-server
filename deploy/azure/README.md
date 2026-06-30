# MySecurePrint Server — One-Click Azure Deploy

[![Deploy to Azure](https://aka.ms/deploytoazurebutton)](https://portal.azure.com/#create/Microsoft.Template/uri/https%3A%2F%2Fraw.githubusercontent.com%2Fmnimtz%2Fmysecureprint-server%2Fmain%2Fdeploy%2Fazure%2Fazuredeploy.json)
[![Visualize Resources](https://raw.githubusercontent.com/maxtyler/azure-quickstart-templates/master/1-CONTRIBUTION-GUIDE/images/visualizebutton.png)](http://armviz.io/#/?load=https%3A%2F%2Fraw.githubusercontent.com%2Fmnimtz%2Fmysecureprint-server%2Fmain%2Fdeploy%2Fazure%2Fazuredeploy.json)

Klick auf den **Deploy to Azure**-Button öffnet das Azure-Portal mit
einem vorausgefüllten Formular. Du brauchst:

- Einen Azure-Account (kostenloses 200-$-Test-Guthaben reicht für 6+ Monate)
- ~30 Sekunden zum Ausfüllen (App-Name + Region + Pricing-Tier)
- ~5 Minuten Wartezeit bis Deploy fertig

## Was wird angelegt

| Resource | Zweck | Kosten (Region West-Europe) |
|---|---|---|
| App Service Plan (Linux) | Container-Host | B1 ≈ 10 €/Monat (Standard) · F1 = 0 € (Free, Limits) |
| App Service (Web App for Containers) | Pulled `ghcr.io/mnimtz/mysecureprint-server:latest` | inkl. |
| Storage Account + File Share `printix-data` | persistente SQLite-DB unter /data | < 1 €/Monat |
| Blob Container `mysecureprint-backups` | Auto-Backups (opt-in) | < 1 €/Monat |

Insgesamt ~ **12 €/Monat** bei B1 für einen produktiven Tenant mit 50-200 Usern.
Mit F1 (Free) zum Testen, schläft aber nach 20 Min Idle ein.

## Nach dem Deploy

1. Browser zu `https://<dein-app-name>.azurewebsites.net`
2. Erst-Setup: Admin-Account anlegen, dann Printix-Credentials eintragen
3. Optional: EntraID-SSO + Mail-Versand (Resend oder Microsoft Graph) konfigurieren
4. iOS-App via TestFlight installieren + QR-Code aus dem Mitarbeiter-Portal scannen

## Button-Snippets zum Verteilen

### Markdown (für GitHub-READMEs)

```markdown
[![Deploy to Azure](https://aka.ms/deploytoazurebutton)](https://portal.azure.com/#create/Microsoft.Template/uri/https%3A%2F%2Fraw.githubusercontent.com%2Fmnimtz%2Fmysecureprint-server%2Fmain%2Fdeploy%2Fazure%2Fazuredeploy.json)
```

### HTML (für Email-Footer, Blogs)

```html
<a href="https://portal.azure.com/#create/Microsoft.Template/uri/https%3A%2F%2Fraw.githubusercontent.com%2Fmnimtz%2Fmysecureprint-server%2Fmain%2Fdeploy%2Fazure%2Fazuredeploy.json">
  <img src="https://aka.ms/deploytoazurebutton" alt="Deploy to Azure">
</a>
```

### Plain-Text (für SMS / Slack-DM)

```
Deploy MySecurePrint to Azure (one-click):
https://aka.ms/deploytoazure?uri=https://raw.githubusercontent.com/mnimtz/mysecureprint-server/main/deploy/azure/azuredeploy.json
```

## Alternative: Azure CLI

Wer's lieber per Skript macht:

```bash
az group create --name mysecureprint-rg --location westeurope

az deployment group create \
  --resource-group mysecureprint-rg \
  --template-uri https://raw.githubusercontent.com/mnimtz/mysecureprint-server/main/deploy/azure/azuredeploy.json \
  --parameters appName=my-secure-print sku=B1
```

## ARM-Template visualisieren

[ARM Visualizer](http://armviz.io/#/?load=https%3A%2F%2Fraw.githubusercontent.com%2Fmnimtz%2Fmysecureprint-server%2Fmain%2Fdeploy%2Fazure%2Fazuredeploy.json)
zeigt das Resource-Diagramm.

## Troubleshooting

- **„Image-Pull fehlgeschlagen"** — `ghcr.io/mnimtz/mysecureprint-server` muss public sein (ist es). Falls Azure trotzdem 503 zeigt, im App Service → Container-Settings → Image-Source aktualisieren.
- **„App startet nicht"** — Check Logs unter Azure Portal → App Service → Log stream. Häufigste Ursache: `/data`-Mount noch nicht ready beim Boot (siehe v0.6.8 Diagnose-Marker).
- **„HTTPS-Zertifikat-Warnung"** — Azure stellt das automatisch via `*.azurewebsites.net`. Eigene Domain → App Service → Custom domains.
