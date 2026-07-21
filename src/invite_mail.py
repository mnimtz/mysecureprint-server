"""Einladungs-Mail fuer neu angelegte Benutzer (Admin/Bulk-Invite).

Rendert Betreff + HTML-Body fuer die "temp password + Login-Link"-Mail,
die beim Anlegen eines Users versendet wird (siehe web/app.py:
POST /admin/users/new und POST /admin/users/bulk-import).

Kein invite_mail.py existierte im Repo obwohl beide Call-Sites es
importieren -> ModuleNotFoundError bei jeder Einladung. Dieses Modul
schliesst die Luecke, im gleichen Stil wie toner_alerts.render_alert_email
(Tungsten-Branding, inline-CSS fuer maximale Email-Client-Kompatibilitaet).
"""

from __future__ import annotations

NAVY = "#002854"
DEEP_NAVY = "#00123B"
ACCENT = "#00A0FB"

_STRINGS: dict[str, dict[str, str]] = {
    "de": {
        "subject": "Ihr Zugang zu {product}",
        "greeting": "Hallo {full_name},",
        "intro": "für Sie wurde ein Benutzerkonto bei {product} angelegt. Mit den folgenden Zugangsdaten können Sie sich anmelden:",
        "username_label": "Benutzername",
        "password_label": "Temporäres Passwort",
        "cta": "Jetzt anmelden",
        "security_note": "Aus Sicherheitsgründen empfehlen wir, das Passwort nach der ersten Anmeldung zu ändern.",
        "footer": "Diese E-Mail wurde automatisch generiert.",
    },
    "en": {
        "subject": "Your access to {product}",
        "greeting": "Hi {full_name},",
        "intro": "a user account was created for you on {product}. You can sign in with the following credentials:",
        "username_label": "Username",
        "password_label": "Temporary password",
        "cta": "Sign in now",
        "security_note": "For security reasons, we recommend changing the password after your first sign-in.",
        "footer": "This email was generated automatically.",
    },
    "fr": {
        "subject": "Votre accès à {product}",
        "greeting": "Bonjour {full_name},",
        "intro": "un compte utilisateur a été créé pour vous sur {product}. Vous pouvez vous connecter avec les identifiants suivants :",
        "username_label": "Nom d'utilisateur",
        "password_label": "Mot de passe temporaire",
        "cta": "Se connecter",
        "security_note": "Pour des raisons de sécurité, nous recommandons de changer le mot de passe après la première connexion.",
        "footer": "Cet e-mail a été généré automatiquement.",
    },
    "it": {
        "subject": "Il tuo accesso a {product}",
        "greeting": "Ciao {full_name},",
        "intro": "è stato creato un account utente per te su {product}. Puoi accedere con le seguenti credenziali:",
        "username_label": "Nome utente",
        "password_label": "Password temporanea",
        "cta": "Accedi ora",
        "security_note": "Per motivi di sicurezza, ti consigliamo di cambiare la password dopo il primo accesso.",
        "footer": "Questa email è stata generata automaticamente.",
    },
    "es": {
        "subject": "Su acceso a {product}",
        "greeting": "Hola {full_name},",
        "intro": "se ha creado una cuenta de usuario para usted en {product}. Puede iniciar sesión con las siguientes credenciales:",
        "username_label": "Nombre de usuario",
        "password_label": "Contraseña temporal",
        "cta": "Iniciar sesión",
        "security_note": "Por razones de seguridad, recomendamos cambiar la contraseña después del primer inicio de sesión.",
        "footer": "Este correo se generó automáticamente.",
    },
    "nl": {
        "subject": "Uw toegang tot {product}",
        "greeting": "Hallo {full_name},",
        "intro": "er is een gebruikersaccount voor u aangemaakt bij {product}. U kunt inloggen met de volgende gegevens:",
        "username_label": "Gebruikersnaam",
        "password_label": "Tijdelijk wachtwoord",
        "cta": "Nu inloggen",
        "security_note": "Om veiligheidsredenen raden we aan het wachtwoord na de eerste keer inloggen te wijzigen.",
        "footer": "Deze e-mail is automatisch gegenereerd.",
    },
    "no": {
        "subject": "Din tilgang til {product}",
        "greeting": "Hei {full_name},",
        "intro": "en brukerkonto er opprettet for deg hos {product}. Du kan logge inn med følgende innloggingsinformasjon:",
        "username_label": "Brukernavn",
        "password_label": "Midlertidig passord",
        "cta": "Logg inn nå",
        "security_note": "Av sikkerhetsgrunner anbefaler vi å endre passordet etter første innlogging.",
        "footer": "Denne e-posten ble generert automatisk.",
    },
    "sv": {
        "subject": "Din åtkomst till {product}",
        "greeting": "Hej {full_name},",
        "intro": "ett användarkonto har skapats åt dig hos {product}. Du kan logga in med följande uppgifter:",
        "username_label": "Användarnamn",
        "password_label": "Tillfälligt lösenord",
        "cta": "Logga in nu",
        "security_note": "Av säkerhetsskäl rekommenderar vi att du byter lösenord efter första inloggningen.",
        "footer": "Detta e-postmeddelande genererades automatiskt.",
    },
}
# nb (Bokmål) teilt sich Text mit no
_STRINGS["nb"] = _STRINGS["no"]

_PRODUCT_NAME = "MySecurePrint"


def _t(lang: str) -> dict[str, str]:
    return _STRINGS.get((lang or "en").lower(), _STRINGS["en"])


def render_invitation_email(lang: str, full_name: str, username: str,
                             password: str, login_url: str) -> tuple[str, str]:
    """Rendert (subject, html_body) fuer die Einladungs-Mail.

    Args:
        lang: Sprachcode (de/en/fr/it/es/nl/no/nb/sv), Fallback en
        full_name: Anzeigename des eingeladenen Users
        username: Login-Benutzername
        password: Temporaeres Klartext-Passwort (nur einmalig per Mail)
        login_url: Absolute URL zur Login-Seite
    """
    s = _t(lang)
    display_name = (full_name or username or "").strip() or username

    subject = s["subject"].format(product=_PRODUCT_NAME)
    greeting = s["greeting"].format(full_name=display_name)
    intro = s["intro"].format(product=_PRODUCT_NAME)

    html_body = f"""\
<div style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Arial, sans-serif;
            color: #231F20; max-width: 480px; margin: 0 auto;">
  <div style="background: {DEEP_NAVY}; padding: 20px 24px; border-radius: 12px 12px 0 0;">
    <span style="color: #fff; font-weight: 700; font-size: 16px;">{_PRODUCT_NAME}</span>
  </div>
  <div style="background: #fff; border: 1px solid #D9DFE6; border-top: none;
              border-radius: 0 0 12px 12px; padding: 28px 24px;">
    <p style="margin: 0 0 12px 0; font-size: 15px;">{greeting}</p>
    <p style="margin: 0 0 20px 0; font-size: 14px; color: #444; line-height: 1.5;">{intro}</p>

    <table style="border-collapse: collapse; font-size: 14px; width: 100%;
                  background: #F5F7FA; border-radius: 8px; margin-bottom: 20px;">
      <tr>
        <td style="padding: 12px 16px 4px 16px; color: #8094AA; font-size: 12px;
                   text-transform: uppercase; letter-spacing: .04em;">{s["username_label"]}</td>
      </tr>
      <tr>
        <td style="padding: 0 16px 12px 16px; font-weight: 700; font-family: monospace; font-size: 15px;">{username}</td>
      </tr>
      <tr>
        <td style="padding: 4px 16px 4px 16px; color: #8094AA; font-size: 12px;
                   text-transform: uppercase; letter-spacing: .04em;">{s["password_label"]}</td>
      </tr>
      <tr>
        <td style="padding: 0 16px 12px 16px; font-weight: 700; font-family: monospace; font-size: 15px;">{password}</td>
      </tr>
    </table>

    <div style="text-align: center; margin: 24px 0;">
      <a href="{login_url}" style="background: {ACCENT}; color: #fff; text-decoration: none;
         padding: 12px 28px; border-radius: 8px; font-weight: 700; font-size: 14px;
         display: inline-block;">{s["cta"]}</a>
    </div>

    <p style="margin: 20px 0 0 0; font-size: 12px; color: #8094AA; line-height: 1.5;">{s["security_note"]}</p>
  </div>
  <p style="text-align: center; font-size: 11px; color: #A0A0A0; margin-top: 16px;">{s["footer"]}</p>
</div>
"""
    return subject, html_body
