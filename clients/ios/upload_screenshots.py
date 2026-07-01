"""Upload Screenshots zu ASC.

Apple Screenshot-Upload ist 3-stufig:
 1) Set anlegen (POST /v1/appScreenshotSets)
 2) Reservation (POST /v1/appScreenshots → liefert upload-URL)
 3) Binary PUT zur upload-URL (Apple's S3) + PATCH uploaded=true
"""
import os, sys, hashlib, json, requests
sys.path.insert(0, os.path.dirname(__file__))
from asc_submit import get_token, api

LOC_ID = "7a8bfda6-1d84-43d9-8001-345c529d8ad0"  # appStoreVersionLocalization de-DE

SHOTS_DIR = "build/screenshots"

# (displayType, file)
SETS = [
    ("APP_IPHONE_65", [
        "iphone_01_login.png", "iphone_02_jobs.png", "iphone_03_share.png"]),
    ("APP_IPAD_PRO_3GEN_129", [
        "ipad_01_login.png", "ipad_02_jobs.png", "ipad_03_share.png"]),
]


def create_set(token, display_type):
    # Existiert bereits?
    r = api("GET", f"/v1/appStoreVersionLocalizations/{LOC_ID}/appScreenshotSets",
            token, params={"fields[appScreenshotSets]": "screenshotDisplayType"})
    r.raise_for_status()
    for s in r.json().get("data", []):
        if s["attributes"]["screenshotDisplayType"] == display_type:
            print(f"  Set {display_type} existiert: {s['id']}")
            return s["id"]
    r = api("POST", "/v1/appScreenshotSets", token, json={
        "data": {
            "type": "appScreenshotSets",
            "attributes": {"screenshotDisplayType": display_type},
            "relationships": {
                "appStoreVersionLocalization": {
                    "data": {"type": "appStoreVersionLocalizations", "id": LOC_ID}
                }
            }
        }
    })
    r.raise_for_status()
    sid = r.json()["data"]["id"]
    print(f"  Set {display_type} angelegt: {sid}")
    return sid


def upload_image(token, set_id, file_path):
    data = open(file_path, "rb").read()
    file_size = len(data)
    file_name = os.path.basename(file_path)
    # 1) Reservation
    r = api("POST", "/v1/appScreenshots", token, json={
        "data": {
            "type": "appScreenshots",
            "attributes": {
                "fileName": file_name,
                "fileSize": file_size,
            },
            "relationships": {
                "appScreenshotSet": {
                    "data": {"type": "appScreenshotSets", "id": set_id}
                }
            }
        }
    })
    r.raise_for_status()
    d = r.json()["data"]
    shot_id = d["id"]
    ops = d["attributes"]["uploadOperations"]
    # 2) PUT binary (es kann mehrere Chunks geben)
    for op in ops:
        url = op["url"]
        method = op["method"]
        offset = op["offset"]
        length = op["length"]
        chunk = data[offset:offset + length]
        headers = {h["name"]: h["value"] for h in op.get("requestHeaders", [])}
        rr = requests.request(method, url, data=chunk, headers=headers)
        rr.raise_for_status()
    # 3) uploaded=true + checksum
    md5 = hashlib.md5(data).hexdigest()
    r = api("PATCH", f"/v1/appScreenshots/{shot_id}", token, json={
        "data": {
            "type": "appScreenshots",
            "id": shot_id,
            "attributes": {"uploaded": True, "sourceFileChecksum": md5},
        }
    })
    r.raise_for_status()
    print(f"    ✓ {file_name} ({file_size} bytes)")


def main():
    tok = get_token()
    for display_type, files in SETS:
        print(f"\n▶ {display_type}")
        sid = create_set(tok, display_type)
        for fn in files:
            p = os.path.join(SHOTS_DIR, fn)
            upload_image(tok, sid, p)


if __name__ == "__main__":
    main()
