# MySecurePrint — iOS Client

iOS-Companion zum Open-Source-Server **printix-mcp**. Teilt sich mit dem
macOS-Client den Netzwerk-Layer via Swift-Package `PrintixSendCore`
(unter `macos-client/`).

> **Hinweis / Disclaimer:** MySecurePrint ist eine unabhaengige
> Drittanbieter-App. Sie ist **NICHT** entwickelt, unterstuetzt oder
> autorisiert von Tungsten Automation Corp. ("Printix" ist eine
> eingetragene Marke der Tungsten Automation Corp.), HP Inc., Konica
> Minolta, Brother, Lexmark, PaperCut oder einem anderen
> Druckerhersteller bzw. Print-Management-Anbieter. MySecurePrint
> spricht ausschliesslich den vom Nutzer selbst betriebenen
> `printix-mcp`-Server an.

## Aufbau

```
ios-client/
├── MySecurePrint.xcodeproj/            Xcode-Projekt
├── MySecurePrint/                      Haupt-App-Target — Bundle: de.nimtz.mysecureprint
│   ├── MySecurePrintApp.swift          @main
│   ├── ContentView.swift               Router: Setup→Login vs. Tabs
│   ├── SetupView.swift                 Server-URL-Eingabe
│   ├── LoginView.swift                 Password + Entra Auth-Code (PKCE)
│   ├── TargetsView.swift               Druck-Ziele auswählen
│   ├── UploadView.swift                Datei wählen + senden
│   ├── SettingsStore.swift             App-Group-UserDefaults-Wrapper
│   ├── KeychainTokenStore.swift        Bearer-Token in Keychain-Access-Group
│   ├── ApiClientFactory.swift          PrintixSendCore-Client-Factory
│   ├── PrivacyInfo.xcprivacy           Apple-Privacy-Manifest
│   └── MySecurePrint.entitlements
├── MySecurePrintShare/                 Share-Extension — Bundle: de.nimtz.mysecureprint.share
│   ├── ShareViewController.swift       PDF/Bild → Background-URLSession-Upload
│   ├── MySecurePrintShare.entitlements
│   ├── PrivacyInfo.xcprivacy
│   └── Info.plist
└── MySecurePrint-Info.plist
```

> Folder/file-Rebrand: *Printix MobilePrint* → *MySecurePrint* (cosmetic, App-Store-Polish).
> Bundle-ID + Display-Name wurden bereits in Commit `4e34497` umgestellt.

## Identitaeten

| Schluessel | Wert |
|---|---|
| App-Anzeigename | MySecurePrint |
| Haupt-Bundle-ID | `de.nimtz.mysecureprint` |
| Share-Extension-Bundle-ID | `de.nimtz.mysecureprint.share` |
| Custom-URL-Scheme (OAuth) | `mysecureprint://oauth/callback` |
| App-Group | `group.de.nimtz.mysecureprint` |
| Keychain-Service | `de.nimtz.mysecureprint` |
| Keychain-Access-Group | `$(AppIdentifierPrefix)group.de.nimtz.mysecureprint` |
| Marketing-Version | 1.0.0 |
| iOS-Deployment-Target | 17.0 |

## OAuth-Redirect-URI

Beim Entra-Login (Authorization-Code + PKCE) verwendet der iOS-Client
`mysecureprint://oauth/callback` als Custom-URL-Scheme-Redirect.
Server-seitig (printix-mcp 7.7.7+) muss dieser Wert in der Entra
App-Registration unter **Authentication -> Mobile and desktop
applications -> Add URI** hinterlegt sein. Der frueher genutzte
Wert `printixmobileprint://oauth/callback` wird vom Server
weiterhin akzeptiert (transitional), sodass aeltere Builds nicht
abrupt abreissen.

## Lizenz / Markenrechte

Siehe Wurzel-`LICENSE`. "Printix" ist eine eingetragene Marke der
Tungsten Automation Corp. Die App nennt den Server-Namen `printix-mcp`
ausschliesslich in nominativ-fair-use-Manier zur Beschreibung der
Kompatibilitaet, ohne Markenrechte zu beanspruchen.
