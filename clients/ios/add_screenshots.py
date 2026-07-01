"""Fuegt Screenshots zu existierenden Sets hinzu (zusaetzlich, nicht ersetzen)."""
import os, sys, hashlib, requests
sys.path.insert(0, os.path.dirname(__file__))
from asc_submit import get_token, api

LOC_ID = "7a8bfda6-1d84-43d9-8001-345c529d8ad0"
ADDITIONS = [
    ("APP_IPHONE_65", "build/screenshots/iphone_upload.png"),
    ("APP_IPAD_PRO_3GEN_129", "build/screenshots/ipad_upload.png"),
]


def main():
    tok = get_token()
    r = api("GET", f"/v1/appStoreVersionLocalizations/{LOC_ID}/appScreenshotSets", tok)
    sets = {s["attributes"]["screenshotDisplayType"]: s["id"] for s in r.json()["data"]}

    for display_type, path in ADDITIONS:
        sid = sets[display_type]
        data = open(path, "rb").read()
        fn = os.path.basename(path)
        r = api("POST", "/v1/appScreenshots", tok, json={
            "data": {"type": "appScreenshots",
                     "attributes": {"fileName": fn, "fileSize": len(data)},
                     "relationships": {"appScreenshotSet": {
                         "data": {"type": "appScreenshotSets", "id": sid}}}}})
        if r.status_code not in (200, 201):
            print(f"  ✗ {display_type}: {r.status_code} {r.text[:300]}")
            continue
        d = r.json()["data"]
        shot_id = d["id"]
        for op in d["attributes"]["uploadOperations"]:
            chunk = data[op["offset"]:op["offset"] + op["length"]]
            headers = {h["name"]: h["value"] for h in op.get("requestHeaders", [])}
            requests.request(op["method"], op["url"], data=chunk, headers=headers).raise_for_status()
        md5 = hashlib.md5(data).hexdigest()
        api("PATCH", f"/v1/appScreenshots/{shot_id}", tok, json={
            "data": {"type": "appScreenshots", "id": shot_id,
                     "attributes": {"uploaded": True, "sourceFileChecksum": md5}}}).raise_for_status()
        print(f"  ✓ Added {display_type}: {fn}")


if __name__ == "__main__":
    main()
