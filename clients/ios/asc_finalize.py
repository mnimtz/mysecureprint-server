#!/usr/bin/env python3
"""Setzt alle automatisierbaren Pflicht-Felder fuer den App-Store-Review.

Macht NICHT:
- Screenshots (Upload via Transporter oder UI)
- App-Privacy-Questionnaire / appDataUsages (UI ist einfacher)
- Pricing (UI)
"""
import os, sys, json
sys.path.insert(0, os.path.dirname(__file__))
from asc_submit import get_token, api

APP_ID = "6785880823"
VERSION_ID = "cb41bd19-0b4a-47a3-ad0a-349519b1010c"
APP_INFO_ID = "18f728a3-e55a-404c-94e2-9540619326f2"
LOC_ID = "6699a155-5ea1-4106-bc2d-f3e7355c0180"

PRIVACY_URL = "https://printix-sp.azurewebsites.net/privacy"
COPYRIGHT = "2026 Marcus Nimtz"
DEMO_USER = "demo.apple"
DEMO_PASS = "Demo@2026"
DEMO_NOTES = (
    "Demo-Server (vorausgefuellt in App-Settings): "
    "https://printix-sp.azurewebsites.net\n\n"
    "Login: Tab 'Settings' oeffnen, Username 'demo.apple' und "
    "Passwort 'Demo@2026' eintragen (lokaler Demo-Account auf Server, "
    "NICHT Microsoft Entra). Server-URL ist vorausgefuellt.\n\n"
    "Hinweis: App ist Begleit-Client fuer selbst gehosteten "
    "printix-mcp Server (Open-Source). Druck via Share-Sheet "
    "(PDF/Foto teilen → MySecurePrint), Job-Status im 'Jobs'-Tab. "
    "NFC-Karten-Registrierung optional (benoetigt physische "
    "Mifare-Karte; nicht zwingend fuer Review).\n\n"
    "Bei Login-Problemen oder Fragen: marcus@nimtz.email"
)
PHONE = "+491722649706"

# Age Rating: alle „NONE"/„NOT_APPLICABLE" → 4+
AGE_RATING = {
    # Level enums
    "alcoholTobaccoOrDrugUseOrReferences": "NONE",
    "gamblingSimulated": "NONE",
    "medicalOrTreatmentInformation": "NONE",
    "profanityOrCrudeHumor": "NONE",
    "sexualContentGraphicAndNudity": "NONE",
    "sexualContentOrNudity": "NONE",
    "horrorOrFearThemes": "NONE",
    "matureOrSuggestiveThemes": "NONE",
    "violenceCartoonOrFantasy": "NONE",
    "violenceRealistic": "NONE",
    "violenceRealisticProlongedGraphicOrSadistic": "NONE",
    "gunsOrOtherWeapons": "NONE",
    # Booleans
    "messagingAndChat": False,
    "userGeneratedContent": False,
    "advertising": False,
    "gambling": False,
    "unrestrictedWebAccess": False,
    "lootBox": False,
    "parentalControls": False,
    "healthOrWellnessTopics": False,
    "ageAssurance": False,
    "contests": "NONE",
}


def patch_localization_privacy():
    print("▶ Localization: privacyPolicyUrl + privacyChoicesUrl…")
    r = api("PATCH", f"/v1/appInfoLocalizations/{LOC_ID}", get_token(), json={
        "data": {
            "type": "appInfoLocalizations",
            "id": LOC_ID,
            "attributes": {
                "privacyPolicyUrl": PRIVACY_URL,
            },
        }
    })
    print(" ", r.status_code, r.text[:200])


def set_app_content_rights():
    print("▶ App: contentRightsDeclaration=DOES_NOT_USE_THIRD_PARTY_CONTENT…")
    r = api("PATCH", f"/v1/apps/{APP_ID}", get_token(), json={
        "data": {
            "type": "apps",
            "id": APP_ID,
            "attributes": {
                "contentRightsDeclaration": "DOES_NOT_USE_THIRD_PARTY_CONTENT",
            },
        }
    })
    print(" ", r.status_code, r.text[:200])


def set_version_copyright():
    print("▶ Version: copyright…")
    r = api("PATCH", f"/v1/appStoreVersions/{VERSION_ID}", get_token(), json={
        "data": {
            "type": "appStoreVersions",
            "id": VERSION_ID,
            "attributes": {"copyright": COPYRIGHT},
        }
    })
    print(" ", r.status_code, r.text[:200])


def set_age_rating():
    print("▶ AgeRatingDeclaration: alles NONE → 4+…")
    # Belongs to the appInfo, not version. Try via /v1/appInfos/{id}/ageRatingDeclaration
    tok = get_token()
    # GET current
    r = api("GET", f"/v1/appInfos/{APP_INFO_ID}/ageRatingDeclaration", tok)
    print("  GET", r.status_code)
    arid = r.json().get("data", {}).get("id") if r.status_code == 200 else None
    if not arid:
        print("  Keine ARID gefunden — versuche via Version…")
        r = api("GET", f"/v1/appStoreVersions/{VERSION_ID}/ageRatingDeclaration", tok)
        print("  GET", r.status_code, r.text[:200])
        arid = r.json().get("data", {}).get("id") if r.status_code == 200 else None
    if not arid:
        print("  ✗ Kann ARID nicht ermitteln, ueberspringe.")
        return
    print(f"  ARID={arid}")
    r = api("PATCH", f"/v1/ageRatingDeclarations/{arid}", tok, json={
        "data": {
            "type": "ageRatingDeclarations",
            "id": arid,
            "attributes": AGE_RATING,
        }
    })
    print(" ", r.status_code, r.text[:400])


def create_review_detail():
    print("▶ AppStoreReviewDetail anlegen (Demo-Account)…")
    tok = get_token()
    r = api("GET", f"/v1/appStoreVersions/{VERSION_ID}/appStoreReviewDetail", tok)
    print("  GET", r.status_code)
    if r.status_code == 200 and r.json().get("data"):
        rid = r.json()["data"]["id"]
        print(f"  Existiert: {rid}, PATCH…")
        r = api("PATCH", f"/v1/appStoreReviewDetails/{rid}", tok, json={
            "data": {
                "type": "appStoreReviewDetails", "id": rid,
                "attributes": {
                    "contactFirstName": "Marcus",
                    "contactLastName": "Nimtz",
                    "contactEmail": "marcus@nimtz.email",
                    "contactPhone": PHONE,
                    "demoAccountName": DEMO_USER,
                    "demoAccountPassword": DEMO_PASS,
                    "demoAccountRequired": True,
                    "notes": DEMO_NOTES,
                },
            }
        })
    else:
        r = api("POST", "/v1/appStoreReviewDetails", tok, json={
            "data": {
                "type": "appStoreReviewDetails",
                "attributes": {
                    "contactFirstName": "Marcus",
                    "contactLastName": "Nimtz",
                    "contactEmail": "marcus@nimtz.email",
                    "contactPhone": PHONE,
                    "demoAccountName": DEMO_USER,
                    "demoAccountPassword": DEMO_PASS,
                    "demoAccountRequired": True,
                    "notes": DEMO_NOTES,
                },
                "relationships": {
                    "appStoreVersion": {
                        "data": {"type": "appStoreVersions", "id": VERSION_ID}
                    }
                }
            }
        })
    print(" ", r.status_code, r.text[:400])


def set_primary_category():
    print("▶ AppInfo: primaryCategory=BUSINESS…")
    tok = get_token()
    # category IDs sind die Strings selbst
    r = api("PATCH", f"/v1/appInfos/{APP_INFO_ID}", tok, json={
        "data": {
            "type": "appInfos",
            "id": APP_INFO_ID,
            "relationships": {
                "primaryCategory": {
                    "data": {"type": "appCategories", "id": "BUSINESS"}
                }
            }
        }
    })
    print(" ", r.status_code, r.text[:400])


if __name__ == "__main__":
    patch_localization_privacy()
    set_app_content_rights()
    set_version_copyright()
    set_age_rating()
    create_review_detail()
    set_primary_category()
    print("\n✓ Fertig. Verbleibende Schritte in ASC-UI:")
    print("  - Screenshots (iPhone 6.5\" + iPad 12.9\")")
    print("  - App Privacy Questionnaire (App Information → App Privacy)")
    print("  - Pricing (App Information → Pricing and Availability)")
