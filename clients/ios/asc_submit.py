#!/usr/bin/env python3
"""
MySecurePrint — Phase B: ASC-API Submit (nach testflight.sh-Upload).

Verwendung:
  export ASC_KEY_ID=Q77537L95P
  export ASC_ISSUER_ID=221f1947-cc3d-45a2-84d0-bb01d66de2b3
  python3 asc_submit.py [--version 1.0.0] [--wait-build]

Flow (siehe asc-ios-app-store-submit.md Memory):
  1. JWT (ES256) holen
  2. App via Bundle de.nimtz.mysecureprint finden -> APP_ID
  3. AppStoreVersion (1.0.0, platform=IOS) anlegen oder finden
  4. Localizations (de-DE) setzen aus APP_STORE_LISTING.md
  5. Auf neuesten Build mit processingState=VALID warten
  6. Build an Version anhaengen
  7. reviewSubmission anlegen (platform=IOS verifizieren!)
  8. reviewSubmissionItem anlegen (retry bei transient 500)
  9. reviewSubmission submit (attributes.submitted=true)
"""
from __future__ import annotations
import argparse, json, os, sys, time
from pathlib import Path
import jwt
import requests

KEY_ID = os.environ.get("ASC_KEY_ID", "Q77537L95P")
ISSUER_ID = os.environ.get("ASC_ISSUER_ID", "221f1947-cc3d-45a2-84d0-bb01d66de2b3")
P8_PATH = Path(os.path.expanduser(
    f"~/.appstoreconnect/private_keys/AuthKey_{KEY_ID}.p8"))
BUNDLE_ID = "de.nimtz.mysecureprint"
BASE = "https://api.appstoreconnect.apple.com"

# Listing-Texte (aus APP_STORE_LISTING.md). Limits: keywords ≤ 100, subtitle ≤ 30,
# promotional ≤ 170, description ≤ 4000.
LISTING_DE = {
    "name": "MySecurePrint",
    "subtitle": "Sicheres mobiles Drucken",
    "keywords": "secure print,mobile,nfc,druck,karte,mobiledruck,airprint,share,papercut,konica",
    "promotionalText": (
        "Mobile Druck-App fuer deinen selbst gehosteten mysecureprint-server. "
        "Mit Microsoft anmelden, NFC-Karten, Share-Sheet."
    ),
    "description": (
        "Mobiles, sicheres Drucken vom iPhone und iPad ueber die Printix-"
        "Cloud-API — endlich auch unterwegs, ohne Desktop-Client.\n\n"
        "MySecurePrint ist eine mobile iOS-Begleit-App fuer den Open-Source-"
        "Server mysecureprint-server. Der Server, den du selbst auf Linux, "
        "in Docker oder als Azure Web App betreibst, spricht im Hintergrund "
        "mit deinem Printix-Tenant ueber die offizielle Printix-Cloud-REST-"
        "API. Die App reicht deine Druckauftraege dorthin durch — Resultat: "
        "vollwertiges SecurePrint vom iPhone, mit denselben Queues, "
        "Berechtigungen und Audit-Logs wie auf dem PC.\n\n"
        "Was du damit machst:\n"
        "- PDFs oder Fotos direkt aus dem iOS-Share-Sheet an deine "
        "Printix-Anywhere- oder Direct-Queue senden — auch im Hintergrund.\n"
        "- Mit Microsoft-Konto via Entra OAuth + PKCE anmelden (oder mit "
        "lokalem Demo-Account).\n"
        "- Job-Status live verfolgen: bereit zur Abholung, fehlgeschlagen, "
        "in Verarbeitung.\n"
        "- Optional: UID deiner NFC-Firmenkarte registrieren (ISO 14443 / "
        "ISO 15693) zur Zuordnung am Drucker.\n\n"
        "Datenschutz und Sicherheit:\n"
        "- Tokens im iOS-Keychain (Access-Group, geteilt mit der "
        "Share-Extension).\n"
        "- Druckdateien gehen direkt an deinen eigenen Server — keine "
        "Drittanbieter, kein Analytics, keine Tracker.\n"
        "- Quelloffen: github.com/mnimtz/mysecureprint-server\n\n"
        "Rechtlicher Hinweis: MySecurePrint ist eine unabhaengige, nicht-"
        "kommerzielle Drittanbieter-App und steht in keiner Verbindung zu "
        "Druckerherstellern wie HP, Konica Minolta, Brother, Lexmark, "
        "PaperCut oder Tungsten Automation Corp. \"Printix\" ist eingetragenes "
        "Markenzeichen von Tungsten Automation Corp. und wird hier "
        "ausschliesslich zur Beschreibung der API-Kompatibilitaet genannt."
    ),
    "whatsNew": "Jobs können jetzt per Wisch (Swipe) aus der Job-Liste gelöscht werden. Verbesserte Statusanzeige und Stabilitäts-Fixes.",
    "supportUrl": "https://github.com/mnimtz/mysecureprint-server/issues",
    "marketingUrl": "https://github.com/mnimtz/mysecureprint-server",
    "privacyPolicyUrl": "https://printix-sp.azurewebsites.net/privacy",
}


def get_token() -> str:
    p8 = P8_PATH.read_text()
    now = int(time.time())
    return jwt.encode(
        {"iss": ISSUER_ID, "iat": now, "exp": now + 900,
         "aud": "appstoreconnect-v1"},
        p8, algorithm="ES256",
        headers={"kid": KEY_ID, "typ": "JWT"},
    )


def api(method: str, path: str, token: str, **kwargs):
    H = {"Authorization": f"Bearer {token}",
         "Content-Type": "application/json"}
    H.update(kwargs.pop("headers", {}))
    r = requests.request(method, BASE + path, headers=H, timeout=30, **kwargs)
    if not r.ok:
        print(f"  ✗ {method} {path} → HTTP {r.status_code}: {r.text[:500]}")
    return r


def find_app(token: str) -> str:
    r = api("GET", "/v1/apps", token,
            params={"filter[bundleId]": BUNDLE_ID,
                    "fields[apps]": "bundleId,name"})
    r.raise_for_status()
    for a in r.json().get("data", []):
        if a.get("attributes", {}).get("bundleId") == BUNDLE_ID:
            return a["id"]
    raise SystemExit(f"App mit Bundle {BUNDLE_ID} nicht in ASC gefunden.")


def find_or_create_version(token: str, app_id: str, version: str) -> str:
    # Existierende Versionen pruefen
    r = api("GET", f"/v1/apps/{app_id}/appStoreVersions", token,
            params={"filter[versionString]": version,
                    "fields[appStoreVersions]": "versionString,appStoreState,platform"})
    r.raise_for_status()
    for v in r.json().get("data", []):
        if v["attributes"].get("versionString") == version:
            print(f"  ✓ Version {version} existiert: {v['id']} state={v['attributes'].get('appStoreState')}")
            return v["id"]
    # Anlegen
    r = api("POST", "/v1/appStoreVersions", token, json={
        "data": {
            "type": "appStoreVersions",
            "attributes": {
                "platform": "IOS",
                "versionString": version,
                "releaseType": "AFTER_APPROVAL",
            },
            "relationships": {
                "app": {"data": {"type": "apps", "id": app_id}},
            },
        }
    })
    r.raise_for_status()
    vid = r.json()["data"]["id"]
    print(f"  ✓ Version {version} angelegt: {vid}")
    return vid


def set_localization(token: str, version_id: str, locale: str, data: dict) -> None:
    r = api("GET", f"/v1/appStoreVersions/{version_id}/appStoreVersionLocalizations",
            token)
    r.raise_for_status()
    loc_id = None
    for loc in r.json().get("data", []):
        if loc["attributes"].get("locale") == locale:
            loc_id = loc["id"]
            break
    if not loc_id:
        r = api("POST", "/v1/appStoreVersionLocalizations", token, json={
            "data": {
                "type": "appStoreVersionLocalizations",
                "attributes": {"locale": locale,
                               "description": data["description"]},
                "relationships": {
                    "appStoreVersion": {"data": {
                        "type": "appStoreVersions", "id": version_id}},
                },
            }
        })
        r.raise_for_status()
        loc_id = r.json()["data"]["id"]
        print(f"  ✓ Localization {locale} angelegt: {loc_id}")
    # PATCH attributes
    patch_attrs = {
        "description": data["description"],
        "keywords": data["keywords"],
        "promotionalText": data["promotionalText"],
        "supportUrl": data["supportUrl"],
        "marketingUrl": data["marketingUrl"],
    }
    if data.get("whatsNew"):
        patch_attrs["whatsNew"] = data["whatsNew"]
    r = api("PATCH", f"/v1/appStoreVersionLocalizations/{loc_id}", token, json={
        "data": {
            "type": "appStoreVersionLocalizations",
            "id": loc_id,
            "attributes": patch_attrs,
        }
    })
    if r.ok:
        print(f"  ✓ {locale} localization gepatched")


def wait_for_valid_build(token: str, app_id: str, version: str,
                          timeout_min: int = 30) -> str:
    """Wait for a VALID build whose marketing version (preReleaseVersion) matches."""
    deadline = time.time() + timeout_min * 60
    print(f"  ⏳ Warte auf VALID-Build marketing={version} (timeout {timeout_min} min)…")
    while time.time() < deadline:
        # filter[preReleaseVersion.version] matches the marketing version (CFBundleShortVersionString)
        r = api("GET", "/v1/builds", token, params={
            "filter[app]": app_id,
            "filter[preReleaseVersion.version]": version,
            "fields[builds]": "version,processingState,uploadedDate",
            "sort": "-uploadedDate", "limit": 5,
        })
        r.raise_for_status()
        for b in r.json().get("data", []):
            att = b["attributes"]
            bver = att.get("version", "")
            bstate = att.get("processingState", "")
            print(f"    Build {bver} state={bstate}")
            if bstate == "VALID":
                print(f"  ✓ build_id={b['id']}")
                return b["id"]
        time.sleep(30)
    raise SystemExit(f"Kein VALID-Build mit marketing={version} innerhalb Timeout.")


def attach_build(token: str, version_id: str, build_id: str) -> None:
    r = api("PATCH", f"/v1/appStoreVersions/{version_id}/relationships/build",
            token, json={"data": {"type": "builds", "id": build_id}})
    r.raise_for_status()
    print(f"  ✓ Build {build_id} an Version {version_id} angehaengt")


def submit_for_review(token: str, app_id: str, version_id: str) -> None:
    # 1. reviewSubmission anlegen (platform=IOS!)
    r = api("POST", "/v1/reviewSubmissions", token, json={
        "data": {
            "type": "reviewSubmissions",
            "attributes": {"platform": "IOS"},
            "relationships": {
                "app": {"data": {"type": "apps", "id": app_id}},
            },
        }
    })
    r.raise_for_status()
    sub_data = r.json()["data"]
    sub_id = sub_data["id"]
    plat = sub_data["attributes"].get("platform")
    if plat != "IOS":
        raise SystemExit(f"WARN: reviewSubmission platform={plat} (erwartet IOS) — abbrechen!")
    print(f"  ✓ reviewSubmission angelegt: {sub_id} platform={plat}")

    # 2. reviewSubmissionItem (mit retry bei transient 500)
    for attempt in range(3):
        r = api("POST", "/v1/reviewSubmissionItems", token, json={
            "data": {
                "type": "reviewSubmissionItems",
                "relationships": {
                    "reviewSubmission": {"data": {
                        "type": "reviewSubmissions", "id": sub_id}},
                    "appStoreVersion": {"data": {
                        "type": "appStoreVersions", "id": version_id}},
                },
            }
        })
        if r.ok:
            print(f"  ✓ reviewSubmissionItem (Versuch {attempt+1})")
            break
        if r.status_code == 500 and attempt < 2:
            print(f"  ⚠ transient 500, retry in 5s…")
            time.sleep(5)
            continue
        r.raise_for_status()

    # 3. Submit
    r = api("PATCH", f"/v1/reviewSubmissions/{sub_id}", token, json={
        "data": {
            "type": "reviewSubmissions", "id": sub_id,
            "attributes": {"submitted": True},
        }
    })
    r.raise_for_status()
    state = r.json()["data"]["attributes"].get("state")
    print(f"  ✓ reviewSubmission SUBMITTED — state={state}")
    print(f"  → ASC-Status: https://appstoreconnect.apple.com/apps/{app_id}/distribution")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--version", default="1.0.0")
    parser.add_argument("--wait-build", action="store_true",
                        help="Warte bis Build VALID ist (sonst Skip)")
    args = parser.parse_args()

    if not P8_PATH.exists():
        sys.exit(f".p8 fehlt: {P8_PATH}")

    print(f"▶ JWT…")
    token = get_token()

    print(f"▶ App suchen: bundle={BUNDLE_ID}…")
    app_id = find_app(token)
    print(f"  ✓ app_id={app_id}")

    print(f"▶ Version {args.version} anlegen/finden…")
    version_id = find_or_create_version(token, app_id, args.version)

    print(f"▶ Localization de-DE setzen…")
    set_localization(token, version_id, "de-DE", LISTING_DE)

    if args.wait_build:
        print(f"▶ Auf VALID-Build warten…")
        build_id = wait_for_valid_build(token, app_id, args.version)
        print(f"  ✓ build_id={build_id}")

        print(f"▶ Build attachen…")
        attach_build(token, version_id, build_id)

        print(f"▶ Review submitten…")
        submit_for_review(token, app_id, version_id)
    else:
        print(f"▶ Skip Build-Wait. Nach altool-Upload + 'VALID' erneut mit --wait-build laufen.")

    print(f"\n✓ Fertig.")


if __name__ == "__main__":
    main()
