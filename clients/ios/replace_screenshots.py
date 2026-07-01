"""Loescht alle existierenden Screenshots und uploadet die echten."""
import os, sys, hashlib, requests
sys.path.insert(0, os.path.dirname(__file__))
from asc_submit import get_token, api

LOC_ID = "7a8bfda6-1d84-43d9-8001-345c529d8ad0"

REPLACEMENTS = [
    ("APP_IPHONE_65", "build/screenshots/iphone_real.png"),
    ("APP_IPAD_PRO_3GEN_129", "build/screenshots/ipad_real.png"),
]


def main():
    tok = get_token()
    # Get all sets
    r = api("GET", f"/v1/appStoreVersionLocalizations/{LOC_ID}/appScreenshotSets", tok,
            params={"include": "appScreenshots"})
    r.raise_for_status()
    sets = {s["attributes"]["screenshotDisplayType"]: s["id"] for s in r.json()["data"]}

    for display_type, path in REPLACEMENTS:
        sid = sets.get(display_type)
        if sid:
            # Get screenshots in this set
            r = api("GET", f"/v1/appScreenshotSets/{sid}/appScreenshots", tok)
            for sh in r.json().get("data", []):
                rr = api("DELETE", f"/v1/appScreenshots/{sh['id']}", tok)
                print(f"  ✗ Deleted {sh['id']}: {rr.status_code}")

        if not sid:
            r = api("POST", "/v1/appScreenshotSets", tok, json={
                "data": {"type": "appScreenshotSets",
                         "attributes": {"screenshotDisplayType": display_type},
                         "relationships": {"appStoreVersionLocalization": {
                             "data": {"type": "appStoreVersionLocalizations", "id": LOC_ID}}}}})
            sid = r.json()["data"]["id"]
            print(f"  ✓ Created set {display_type}: {sid}")

        # Upload new screenshot
        data = open(path, "rb").read()
        fn = os.path.basename(path)
        r = api("POST", "/v1/appScreenshots", tok, json={
            "data": {"type": "appScreenshots",
                     "attributes": {"fileName": fn, "fileSize": len(data)},
                     "relationships": {"appScreenshotSet": {
                         "data": {"type": "appScreenshotSets", "id": sid}}}}})
        r.raise_for_status()
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
        print(f"  ✓ Uploaded {display_type}: {fn}")


if __name__ == "__main__":
    main()
