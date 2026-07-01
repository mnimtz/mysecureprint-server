"""Generiert minimal-saubere Branding-Screenshots in den exakten Dimensionen."""
import os
from PIL import Image, ImageDraw, ImageFont

OUT = "build/screenshots"
os.makedirs(OUT, exist_ok=True)

# (slot, width, height, filename)
SHOTS = [
    ("iphone65", 1290, 2796, "iphone_01_login.png"),
    ("iphone65", 1290, 2796, "iphone_02_jobs.png"),
    ("iphone65", 1290, 2796, "iphone_03_share.png"),
    ("ipadPro129", 2048, 2732, "ipad_01_login.png"),
    ("ipadPro129", 2048, 2732, "ipad_02_jobs.png"),
    ("ipadPro129", 2048, 2732, "ipad_03_share.png"),
]

CAPTIONS = {
    "iphone_01_login.png":  ("Sicher anmelden", "Microsoft Entra OAuth + PKCE"),
    "iphone_02_jobs.png":   ("Deine Druck-Jobs", "Status, Queue, Fehler — alles auf einen Blick"),
    "iphone_03_share.png":  ("Direkt aus Share-Sheet", "PDF oder Foto teilen → MySecurePrint"),
    "ipad_01_login.png":    ("Sicher anmelden", "Microsoft Entra OAuth + PKCE"),
    "ipad_02_jobs.png":     ("Deine Druck-Jobs", "Status, Queue, Fehler — alles auf einen Blick"),
    "ipad_03_share.png":    ("Direkt aus Share-Sheet", "PDF oder Foto teilen → MySecurePrint"),
}

# Try to load a system font.
def font(size):
    for p in ["/System/Library/Fonts/SFNS.ttf",
              "/System/Library/Fonts/Helvetica.ttc",
              "/System/Library/Fonts/Avenir.ttc"]:
        if os.path.exists(p):
            try:
                return ImageFont.truetype(p, size)
            except Exception:
                continue
    return ImageFont.load_default()


def draw_shot(w, h, title, sub, path):
    # Vertical gradient background (blue → light)
    img = Image.new("RGB", (w, h), (240, 244, 250))
    draw = ImageDraw.Draw(img)
    for y in range(h):
        t = y / h
        r = int(20 + (240 - 20) * t)
        g = int(60 + (244 - 60) * t)
        b = int(150 + (250 - 150) * t)
        draw.line([(0, y), (w, y)], fill=(r, g, b))

    # Branding bar top
    bar_h = int(h * 0.18)
    draw.rectangle([0, 0, w, bar_h], fill=(15, 40, 110))

    # App name
    name_f = font(int(w * 0.075))
    bbox = draw.textbbox((0, 0), "MySecurePrint", font=name_f)
    tw = bbox[2] - bbox[0]
    draw.text(((w - tw) // 2, int(bar_h * 0.4) - (bbox[3] - bbox[1]) // 2),
              "MySecurePrint", fill="white", font=name_f)

    # Title (center-ish)
    title_f = font(int(w * 0.065))
    bbox = draw.textbbox((0, 0), title, font=title_f)
    tw = bbox[2] - bbox[0]
    draw.text(((w - tw) // 2, int(h * 0.42)), title, fill=(15, 25, 55), font=title_f)

    # Subtitle
    sub_f = font(int(w * 0.032))
    # wrap manually if too long
    lines = []
    words = sub.split(" ")
    cur = ""
    for word in words:
        test = (cur + " " + word).strip()
        bb = draw.textbbox((0, 0), test, font=sub_f)
        if bb[2] - bb[0] > w * 0.85 and cur:
            lines.append(cur); cur = word
        else:
            cur = test
    if cur:
        lines.append(cur)
    y = int(h * 0.52)
    for ln in lines:
        bb = draw.textbbox((0, 0), ln, font=sub_f)
        draw.text(((w - (bb[2] - bb[0])) // 2, y), ln, fill=(40, 50, 90), font=sub_f)
        y += int((bb[3] - bb[1]) * 1.4)

    # Footer
    foot_f = font(int(w * 0.022))
    foot = "Open-Source · Self-Hosted · Keychain-Token · Keine Tracker"
    bb = draw.textbbox((0, 0), foot, font=foot_f)
    draw.text(((w - (bb[2] - bb[0])) // 2, int(h * 0.92)), foot,
              fill=(15, 40, 110), font=foot_f)

    img.save(path, "PNG", optimize=True)
    print(f"  ✓ {path} ({w}x{h})")


for slot, w, h, fn in SHOTS:
    title, sub = CAPTIONS[fn]
    draw_shot(w, h, title, sub, os.path.join(OUT, fn))
print("\nFertig:", OUT)
