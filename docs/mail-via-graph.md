# System-Mails ueber Microsoft Graph (statt Resend)

**Seit:** v0.7.0
**Status:** stabil fuer Mobile-Invite / Welcome / GDPR-Export / Reports.
**Default-Provider bleibt Resend** — wer nichts umstellt, faehrt weiter wie bisher.

## Wann nutzen?

| Vorteil Graph                                  | Vorteil Resend                      |
|------------------------------------------------|-------------------------------------|
| keine externe Subscription, kein DNS-Setup     | unabhaengig von O365-Lizenz         |
| Absender = eigene Firmen-Domain ohne Aufwand   | einfacheres Spam-Verhalten          |
| Audit-Trail in Exchange Online (Sent Items)    | Bounce-Tracking out-of-the-box      |
| Tenant-Admins kennen das Permission-Modell     | unabhaengig vom Entra-Auto-Setup    |
| keine zusaetzliche Drittpartei im Datenfluss   |                                     |

Empfehlung: wer Entra-Auto-Setup ohnehin macht, sollte gleich beide
Permissions (`Mail.Send` und — vorbereitend — `Mail.Read`) mit
registrieren. Der Admin-Consent ist der lange Schritt; den moechte
man nicht ein zweites Mal durchklicken.

## Setup-Schritte

1. **Im Admin-UI:** `/admin/settings?section=entra` &rarr; Auto-Setup-
   Karte. Beim Einrichten der Entra-App die Checkbox
   *„Mail-Versand ueber dieses O365-Konto erlauben (Mail.Send)"* aktivieren.
   Optional auch *„Email-to-Print Gateway vorbereiten (Mail.Read)"* —
   das eigentliche Feature folgt in v0.8.0, aber die Permission ist
   dann schon mitregistriert.
2. **Admin-Consent erteilen** (Azure-Portal &rarr; *Entra ID* &rarr;
   *App-Registrierungen* &rarr; deine neue App &rarr; *API-Berechtigungen*
   &rarr; **„Grant admin consent for &lt;tenant&gt;"**). Ohne diesen Klick
   liefert Graph einen 403 zurueck.
3. **Service-Mailbox waehlen.** Best Practice: eine dedizierte
   Mailbox `noreply@firma.de` (kein Postfach eines echten Users —
   damit man den Zugriff sauber per Policy einschraenken kann).
4. **Application Access Policy in Exchange Online setzen** (WICHTIG):

   ```powershell
   Connect-ExchangeOnline
   New-ApplicationAccessPolicy `
       -AppId <client_id> `
       -PolicyScopeGroupId noreply@firma.de `
       -AccessRight RestrictAccess `
       -Description "MySecurePrint Mail.Send only on noreply mailbox"
   ```

   Ohne diese Policy kann die App-Identitaet von **jeder** Mailbox im
   Tenant aus senden — das ist eine bewusste Microsoft-Default-
   Schwaeche. Mit der Policy nur noch von der einen Service-Mailbox.

5. **Provider umschalten:** `/admin/settings?section=general` &rarr;
   Karte „Globales Mail-Fallback" &rarr; Provider-Dropdown auf
   *„Microsoft Graph"* + Service-Mailbox eintragen + Speichern.

## Fallback-Verhalten

Wenn Provider = Graph und der Versand fehlschlaegt (Token weg,
Mailbox down, Policy-Konflikt …), versucht der Server automatisch
Resend — falls dort ein API-Key + Absender konfiguriert sind. Im
Log erscheint dann eine `WARNING`-Zeile
`mail: Graph-Versand fehlgeschlagen (...) — Fallback auf Resend.`

Wer Resend bewusst nicht haben moechte, laesst den Resend-API-Key
leer; dann faellt der Versand komplett aus statt nach Resend zu
gehen.

## Troubleshooting

| HTTP-Code | Ursache                                    | Loesung                                        |
|-----------|--------------------------------------------|-----------------------------------------------|
| 401       | Token abgelehnt — Secret abgelaufen?       | Im Admin-UI Entra-Secret rotieren.            |
| 403       | Admin-Consent fehlt ODER Application      | Im Azure-Portal Consent erteilen, oder       |
|           | Access Policy verweigert diese Mailbox.   | Policy mit `Get-ApplicationAccessPolicy`     |
|           |                                            | pruefen.                                      |
| 404       | Sender-Mailbox existiert nicht im Tenant.  | UPN/Adresse pruefen — muss eine echte         |
|           |                                            | Exchange-Online-Mailbox sein, nicht z.B.     |
|           |                                            | nur ein Mail-Enabled User.                    |

## Email-to-Print (Vorbereitung)

Die `Mail.Read`-Application-Permission wird ab v0.7.0 mitregistriert,
das eigentliche Feature kommt aber erst in **v0.8.0**:

- Polling eines dedizierten Service-Postfachs
- Anhaenge eines authentifizierten Tenant-Users werden in dessen
  Namen an die Standard-Druckqueue geschickt
- Subject-Praefixe steuern Ziel-Queue / Optionen

Der Admin-Toggle `email_to_print_enabled` ist in v0.7.0 deaktiviert.
Wer schon jetzt die Permission registriert hat, kann in v0.8.0 ohne
zweiten Consent-Roundtrip aktivieren.
