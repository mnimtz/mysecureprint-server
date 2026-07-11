"""Server-side Netzwerk-Topologie-Diagramm — v2 Redesign.

Nimmt einen `fetch_network_topology`-Baum von bi_client, filtert nach
User-Auswahl, rendert als inline-SVG mit modernen Karten in Tungsten-
Brand-Farben.

Design-Prinzipien:
- Jede Karte kompakt aber informativ mit Icon + zentriertem Text
- Drucker-Karten zeigen echte MFP-Illustration in Vendor-Farbe
- Workstation-Karten zeigen Typ-Icon (Laptop/Desktop/Server/Mobile)
- Keine Ueberlagerung — Layout-Padding stellt Abstand sicher
- Icons via explizite width/height auf <use> (Cross-Browser-safe)
- Skaliert fuer grosse Tenants (Canvas scrollt)
"""
from __future__ import annotations

import html
from typing import Optional

# ── Tungsten Brand Tokens ────────────────────────────────────────────
NAVY       = "#002854"
DEEP_NAVY  = "#00123B"
TUNGSTEN_BLUE = "#00A0FB"
LIGHT_BLUE = "#9DDDF9"
GREEN      = "#00EB86"
DARK_GREEN = "#016839"
BLACK      = "#231F20"
GRAY_MUTE  = "#8094AA"
GRAY_BORD  = "#D9DFE6"
GRAY_SUB   = "#E4E4E4"
BG_SUBTLE  = "#F5F7FA"

# ── Layout-Parameter (v2 großzügiger, keine Overlaps) ────────────────
LEAF_SLOT_W = 170     # Breite pro Leaf-Karte (Drucker/Workstation/User)
LEAF_H      = 130     # Höhe der Leaf-Karten
NET_H       = 60      # Höhe der Network-Pill
SITE_H      = 62      # Höhe der Site-Pill
ROOT_H      = 56
LEVEL_GAP   = 90      # Vertikaler Abstand zwischen Ebenen
TOP_PAD     = 30
LEFT_PAD    = 40
BOTTOM_PAD  = 40

# Y-Positionen der Ebenen (statisch berechnet)
Y_ROOT = TOP_PAD + ROOT_H // 2
Y_SITE = Y_ROOT + ROOT_H // 2 + LEVEL_GAP + SITE_H // 2
Y_NET  = Y_SITE + SITE_H // 2 + LEVEL_GAP + NET_H // 2
Y_LEAF = Y_NET + NET_H // 2 + LEVEL_GAP + LEAF_H // 2
Y_USER = Y_LEAF + LEAF_H // 2 + LEVEL_GAP + 40


# ── Vendor-Farbmapping ───────────────────────────────────────────────
_VENDOR_COLORS = {
    "hp":       "#0096d6",
    "hewlett":  "#0096d6",
    "brother":  "#5c2d91",
    "kyocera":  "#e30613",
    "ricoh":    "#c8102e",
    "canon":    "#bc0000",
    "epson":    "#004b93",
    "xerox":    "#00b6de",
    "lexmark":  "#e11b22",
    "oki":      "#005baa",
    "samsung":  "#1428a0",
    "konica":   "#0091d9",
    "smart":    NAVY,  # z.B. "Smart Tank" ohne HP-Prefix
}


def _vendor_color(vendor: str) -> str:
    v = (vendor or "").lower()
    for key, col in _VENDOR_COLORS.items():
        if key in v:
            return col
    return NAVY


# ── Icons (Symbole in <defs>) — mit Verwendung via explizitem width/height ──
def _svg_defs() -> str:
    return f'''
    <defs>
      <!-- Modern MFP: Body, Screen, Slot, Papierschacht -->
      <symbol id="ico-mfp" viewBox="0 0 100 100" preserveAspectRatio="xMidYMid meet">
        <!-- ADF Top -->
        <rect x="18" y="14" width="64" height="8" rx="2" fill="currentColor"/>
        <!-- Scanner strip -->
        <rect x="14" y="22" width="72" height="3" fill="{DEEP_NAVY}" opacity=".4"/>
        <!-- Body -->
        <rect x="10" y="25" width="80" height="52" rx="5" fill="currentColor"/>
        <!-- Screen -->
        <rect x="52" y="32" width="30" height="16" rx="2" fill="{TUNGSTEN_BLUE}"/>
        <rect x="55" y="35" width="4" height="4" rx="1" fill="{GREEN}"/>
        <rect x="61" y="35" width="4" height="4" rx="1" fill="#fff" opacity=".7"/>
        <!-- Paper slot -->
        <rect x="18" y="54" width="60" height="8" rx="1" fill="#fff"/>
        <rect x="20" y="56" width="56" height="1" fill="{GRAY_BORD}"/>
        <!-- Paper tray -->
        <rect x="14" y="66" width="72" height="14" rx="2" fill="{DEEP_NAVY}" opacity=".7"/>
        <rect x="42" y="72" width="16" height="3" rx="1" fill="{TUNGSTEN_BLUE}"/>
        <!-- LED strip left -->
        <rect x="12" y="34" width="2" height="30" rx="1" fill="{GREEN}"/>
        <!-- Base -->
        <rect x="12" y="80" width="76" height="3" rx="1" fill="{DEEP_NAVY}"/>
      </symbol>

      <!-- Laptop (Clamshell) -->
      <symbol id="ico-laptop" viewBox="0 0 100 100" preserveAspectRatio="xMidYMid meet">
        <path d="M 20 22 h 60 v 44 h -60 z" fill="currentColor"/>
        <rect x="24" y="26" width="52" height="34" fill="#0f172a"/>
        <rect x="28" y="30" width="44" height="24" fill="{TUNGSTEN_BLUE}" opacity=".55"/>
        <path d="M 8 70 h 84 v 4 l -6 6 h -72 l -6 -6 z" fill="#94a3b8"/>
        <rect x="46" y="72" width="8" height="1.5" rx="1" fill="#64748b"/>
      </symbol>

      <!-- Desktop Tower -->
      <symbol id="ico-desktop" viewBox="0 0 100 100" preserveAspectRatio="xMidYMid meet">
        <rect x="30" y="14" width="40" height="68" rx="3" fill="currentColor"/>
        <circle cx="50" cy="24" r="2" fill="{GREEN}"/>
        <rect x="36" y="34" width="28" height="2" fill="#334155"/>
        <rect x="36" y="40" width="28" height="2" fill="#334155"/>
        <rect x="36" y="46" width="28" height="2" fill="#334155"/>
        <rect x="36" y="60" width="28" height="10" rx="1" fill="#334155"/>
        <rect x="26" y="86" width="48" height="4" rx="1" fill="{DEEP_NAVY}"/>
      </symbol>

      <!-- Server (rack) -->
      <symbol id="ico-server" viewBox="0 0 100 100" preserveAspectRatio="xMidYMid meet">
        <rect x="18" y="14" width="64" height="16" rx="2" fill="currentColor"/>
        <circle cx="24" cy="22" r="1.5" fill="{GREEN}"/>
        <circle cx="30" cy="22" r="1.5" fill="{TUNGSTEN_BLUE}"/>
        <rect x="54" y="20" width="24" height="4" fill="#fff" opacity=".2"/>
        <rect x="18" y="34" width="64" height="16" rx="2" fill="currentColor"/>
        <circle cx="24" cy="42" r="1.5" fill="{GREEN}"/>
        <circle cx="30" cy="42" r="1.5" fill="{TUNGSTEN_BLUE}"/>
        <rect x="54" y="40" width="24" height="4" fill="#fff" opacity=".2"/>
        <rect x="18" y="54" width="64" height="16" rx="2" fill="currentColor"/>
        <circle cx="24" cy="62" r="1.5" fill="{GREEN}"/>
        <circle cx="30" cy="62" r="1.5" fill="{TUNGSTEN_BLUE}"/>
        <rect x="54" y="60" width="24" height="4" fill="#fff" opacity=".2"/>
        <rect x="18" y="74" width="64" height="10" rx="2" fill="#334155"/>
      </symbol>

      <!-- Mobile phone -->
      <symbol id="ico-mobile" viewBox="0 0 100 100" preserveAspectRatio="xMidYMid meet">
        <rect x="34" y="12" width="32" height="76" rx="5" fill="currentColor"/>
        <rect x="38" y="20" width="24" height="52" fill="#0f172a"/>
        <rect x="40" y="22" width="20" height="48" fill="{TUNGSTEN_BLUE}" opacity=".55"/>
        <circle cx="50" cy="80" r="2.5" fill="#334155"/>
      </symbol>

      <!-- User (generic) -->
      <symbol id="ico-user" viewBox="0 0 100 100" preserveAspectRatio="xMidYMid meet">
        <circle cx="50" cy="34" r="16" fill="currentColor"/>
        <path d="M 18 88 c 0 -18 14 -30 32 -30 s 32 12 32 30 z" fill="currentColor"/>
      </symbol>

      <!-- Site (building) — kleiner, mit Fenstern -->
      <symbol id="ico-site" viewBox="0 0 100 100" preserveAspectRatio="xMidYMid meet">
        <path d="M 14 84 v -56 l 36 -20 l 36 20 v 56 z" fill="currentColor"/>
        <rect x="26" y="46" width="10" height="10" fill="{LIGHT_BLUE}"/>
        <rect x="45" y="46" width="10" height="10" fill="{LIGHT_BLUE}"/>
        <rect x="64" y="46" width="10" height="10" fill="{LIGHT_BLUE}"/>
        <rect x="26" y="62" width="10" height="10" fill="{LIGHT_BLUE}"/>
        <rect x="45" y="62" width="10" height="18" fill="{TUNGSTEN_BLUE}"/>
        <rect x="64" y="62" width="10" height="10" fill="{LIGHT_BLUE}"/>
      </symbol>

      <!-- Network (WiFi cloud) -->
      <symbol id="ico-network" viewBox="0 0 100 100" preserveAspectRatio="xMidYMid meet">
        <path d="M 20 62 a 16 16 0 0 1 6 -30
                 a 20 20 0 0 1 38 -1
                 a 14 14 0 0 1 14 32 z" fill="currentColor"/>
        <circle cx="36" cy="52" r="2.5" fill="#fff"/>
        <circle cx="50" cy="52" r="2.5" fill="#fff"/>
        <circle cx="64" cy="52" r="2.5" fill="#fff"/>
      </symbol>
    </defs>
    '''


def _icon_for_workstation(ws: dict) -> str:
    t = (ws.get("type") or "").lower()
    os_str = (ws.get("os") or "").lower()
    if "server" in t or "server" in os_str:
        return "ico-server"
    if "laptop" in t or "mac" in os_str or "book" in os_str:
        return "ico-laptop"
    if "mobile" in t or "ios" in os_str or "android" in os_str:
        return "ico-mobile"
    return "ico-desktop"


def _ws_color(ws: dict) -> str:
    os_str = (ws.get("os") or "").lower()
    if "mac" in os_str or "darwin" in os_str:
        return "#94a3b8"
    if "linux" in os_str:
        return "#f59e0b"
    if "server" in os_str:
        return "#dc2626"
    return TUNGSTEN_BLUE


# ── Layout ──────────────────────────────────────────────────────────

def _layout_tree(nodes: list[dict], depth: int, x_start: int,
                 slot_width: int) -> tuple[int, dict]:
    """Bottom-up Slot-Layout. Jeder Leaf-Node bekommt slot_width, innere
    Knoten liegen zentriert ueber ihren Kindern. Returns (used_w, positions).
    Y-Werte werden ausserhalb per _y_for(depth) gesetzt.
    """
    if not nodes:
        return (0, {})
    positions: dict = {}
    x = x_start
    for node in nodes:
        children = node.get("_children") or []
        if not children:
            positions[node["id"]] = (x + slot_width // 2, 0)  # y wird spaeter gesetzt
            node["_depth"] = depth
            x += slot_width
        else:
            w, cp = _layout_tree(children, depth + 1, x, slot_width)
            positions.update(cp)
            first_x = cp[children[0]["id"]][0]
            last_x  = cp[children[-1]["id"]][0]
            positions[node["id"]] = ((first_x + last_x) // 2, 0)
            node["_depth"] = depth
            x += max(w, slot_width)
    return (x - x_start, positions)


def _assign_y(nodes: list[dict], positions: dict) -> None:
    """Setzt Y-Koordinate pro Node abhaengig von node.kind."""
    for n in nodes:
        kind = n["kind"]
        if kind == "root":
            y = Y_ROOT
        elif kind == "site":
            y = Y_SITE
        elif kind == "network":
            y = Y_NET
        elif kind in ("printer", "workstation"):
            y = Y_LEAF
        elif kind == "user":
            y = Y_USER
        else:
            y = 0
        x, _ = positions[n["id"]]
        positions[n["id"]] = (x, y)
        _assign_y(n.get("_children") or [], positions)


# ── Filter-Baum ─────────────────────────────────────────────────────
def _build_render_tree(topology: dict, filters: dict) -> list[dict]:
    sel_sites = set(filters.get("sites") or [])
    show_all_sites = filters.get("show_all_sites", True)
    show_printers = filters.get("show_printers", True)
    show_workstations = filters.get("show_workstations", True)
    show_users = filters.get("show_users", False)
    show_details = filters.get("show_details", False)
    show_netdetails = filters.get("show_netdetails", False)

    def _mkuser(u):
        return {"id": f"u:{u['id']}", "kind": "user",
                "label": u.get("name") or u.get("email") or "?",
                "sub": u.get("department") or "",
                "meta": u, "_children": [],
                "_details": show_details, "_netdetails": show_netdetails}

    def _mkws(w):
        kids = [_mkuser(u) for u in w.get("users", [])] if show_users else []
        return {"id": f"w:{w['id']}", "kind": "workstation",
                "label": w.get("name") or "?",
                "sub": (w.get("os") or "").strip(),
                "meta": w, "_children": kids,
                "_details": show_details, "_netdetails": show_netdetails}

    def _mkp(p):
        return {"id": f"p:{p['id']}", "kind": "printer",
                "label": p.get("name") or "?",
                "sub": (p.get("model") or p.get("vendor") or "").strip(),
                "meta": p, "_children": [],
                "_details": show_details, "_netdetails": show_netdetails}

    sites_out = []
    for site in topology.get("sites", []):
        if not show_all_sites and site["id"] not in sel_sites:
            continue
        nets = []
        for net in site.get("networks", []):
            pkids = ([_mkp(p) for p in net.get("printers", [])]
                     if show_printers else [])
            wkids = ([_mkws(w) for w in net.get("workstations", [])]
                     if show_workstations else [])
            children = pkids + wkids
            if not children:
                continue
            nets.append({
                "id": f"n:{net['id']}", "kind": "network",
                "label": net.get("name") or "?",
                "sub": "mobile" if net.get("mobile_print") else "",
                "meta": net, "_children": children,
            })
        if not nets:
            continue
        sites_out.append({
            "id": f"s:{site['id']}", "kind": "site",
            "label": site.get("name") or "?",
            "sub": site.get("type") or "",
            "meta": site, "_children": nets,
        })

    # Unassigned-Bucket: Drucker+Workstations ohne Site/Network-Zuordnung
    ua = topology.get("unassigned", {}) or {}
    ua_printers = ([_mkp(p) for p in ua.get("printers", [])]
                   if show_printers else [])
    ua_ws = ([_mkws(w) for w in ua.get("workstations", [])]
             if show_workstations else [])
    if ua_printers or ua_ws:
        sites_out.append({
            "id": "s:_unassigned", "kind": "site",
            "label": "Sonstige", "sub": "MIXED",
            "meta": {}, "_children": [{
                "id": "n:_unassigned", "kind": "network",
                "label": "Ungeordnete Geraete",
                "sub": "",
                "meta": {}, "_children": ua_printers + ua_ws,
            }],
        })

    if not sites_out:
        return []
    return [{"id": "root", "kind": "root", "label": "Tenant",
             "sub": "", "meta": {}, "_children": sites_out}]


# ── Rendering ───────────────────────────────────────────────────────

def _esc(s: str) -> str:
    return html.escape(str(s or ""), quote=True)


def _truncate(s: str, n: int) -> str:
    if not s or len(s) <= n:
        return s or ""
    return s[: n - 1] + "…"


def _render_node(node: dict, positions: dict) -> str:
    x, y = positions[node["id"]]
    kind = node["kind"]

    if kind == "root":
        return f'''
        <g transform="translate({x},{y})">
          <rect x="-70" y="-{ROOT_H//2}" width="140" height="{ROOT_H}" rx="10"
                fill="{DEEP_NAVY}"/>
          <text x="0" y="6" fill="#fff" text-anchor="middle"
                style="font-weight:700;font-size:14px;
                       font-family:'Red Hat Display',Arial,sans-serif;">Tenant</text>
        </g>'''

    if kind == "site":
        label = _truncate(node["label"], 22)
        sub = _truncate(node["sub"] or "Site", 20)
        return f'''
        <g transform="translate({x},{y})">
          <rect x="-90" y="-{SITE_H//2}" width="180" height="{SITE_H}" rx="12"
                fill="#fff" stroke="{NAVY}" stroke-width="2"/>
          <use href="#ico-site" x="-84" y="-14" width="26" height="26"
               color="{NAVY}"/>
          <text x="0" y="-4" fill="{NAVY}" text-anchor="middle"
                style="font-weight:700;font-size:13px;
                       font-family:'Red Hat Display',Arial,sans-serif;">
            {_esc(label)}
          </text>
          <text x="0" y="14" fill="{GRAY_MUTE}" text-anchor="middle"
                style="font-weight:500;font-size:10px;letter-spacing:.5px;
                       font-family:'Red Hat Display',Arial,sans-serif;">
            {_esc(sub.upper())}
          </text>
        </g>'''

    if kind == "network":
        label = _truncate(node["label"], 20)
        mobile_txt = "· mobile print" if node["sub"] == "mobile" else ""
        return f'''
        <g transform="translate({x},{y})">
          <rect x="-85" y="-{NET_H//2}" width="170" height="{NET_H}" rx="30"
                fill="{LIGHT_BLUE}" opacity=".25"
                stroke="{TUNGSTEN_BLUE}" stroke-width="1.5"/>
          <use href="#ico-network" x="-78" y="-12" width="24" height="24"
               color="{TUNGSTEN_BLUE}"/>
          <text x="0" y="-2" fill="{NAVY}" text-anchor="middle"
                style="font-weight:700;font-size:12px;
                       font-family:'Red Hat Display',Arial,sans-serif;">
            {_esc(label)}
          </text>
          <text x="0" y="14" fill="{GRAY_MUTE}" text-anchor="middle"
                style="font-weight:500;font-size:9px;letter-spacing:.5px;
                       font-family:'Red Hat Display',Arial,sans-serif;">
            NETZWERK {_esc(mobile_txt)}
          </text>
        </g>'''

    # v0.7.276: opts fuer Detail-Toggles
    details = node.get("_details", False)
    netdetails = node.get("_netdetails", False)
    # Karten-Breite/Hoehe: bei Details ON etwas groesser damit Zusatzzeilen passen
    card_w = LEAF_SLOT_W - 16
    card_h = LEAF_H
    if details or netdetails:
        card_h = LEAF_H + 34  # Platz fuer 2 Extra-Zeilen

    if kind == "printer":
        color = _vendor_color(node["meta"].get("vendor", ""))
        model = _truncate((node["meta"].get("model") or "").strip(),
                          40 if details else 22)
        name = _truncate(node["label"], 32 if details else 18)
        vendor = _truncate((node["meta"].get("vendor") or "").upper(), 20)
        location = _truncate((node["meta"].get("location") or "").strip(), 24)
        extra = ""
        if details and location:
            extra = f'''<text x="0" y="{card_h//2 - 8}" fill="{GRAY_MUTE}"
                              text-anchor="middle"
                              style="font-weight:500;font-size:9px;
                                     font-family:'Red Hat Display',Arial,sans-serif;">
                        📍 {_esc(location)}
                      </text>'''
        return f'''
        <g transform="translate({x},{y})">
          <rect x="-{card_w//2}" y="-{card_h//2}"
                width="{card_w}" height="{card_h}" rx="10"
                fill="#fff" stroke="{GRAY_BORD}" stroke-width="1"/>
          <use href="#ico-mfp" x="-32" y="-{card_h//2 - 6}"
               width="64" height="64" color="{color}"/>
          <text x="0" y="30" fill="{NAVY}" text-anchor="middle"
                style="font-weight:700;font-size:11px;
                       font-family:'Red Hat Display',Arial,sans-serif;">
            {_esc(name)}
          </text>
          <text x="0" y="44" fill="{GRAY_MUTE}" text-anchor="middle"
                style="font-weight:500;font-size:9px;
                       font-family:'Red Hat Display',Arial,sans-serif;">
            {_esc(model)}
          </text>
          <text x="0" y="56" fill="{color}" text-anchor="middle"
                style="font-weight:700;font-size:8px;letter-spacing:.5px;
                       font-family:'Red Hat Display',Arial,sans-serif;">
            {_esc(vendor)}
          </text>
          {extra}
        </g>'''

    if kind == "workstation":
        icon = _icon_for_workstation(node["meta"])
        col = _ws_color(node["meta"])
        name = _truncate(node["label"], 32 if details else 18)
        os_str = _truncate((node["meta"].get("os") or "").strip(),
                           40 if details else 20)
        ws_type = (node["meta"].get("type") or "").upper()
        ip = (node["meta"].get("ip") or "").strip()
        cv = (node["meta"].get("client_version") or "").strip()
        ssid = (node["meta"].get("ssid") or "").strip()
        # Netzwerk-Details-Zeile
        net_line = ""
        if netdetails and (ip or ssid):
            bits = []
            if ip:   bits.append(f"🌐 {ip}")
            if ssid: bits.append(f"📶 {ssid}")
            net_line = f'''<text x="0" y="{card_h//2 - 22}" fill="{TUNGSTEN_BLUE}"
                                text-anchor="middle"
                                style="font-weight:500;font-size:9px;font-family:monospace;">
                            {_esc(" · ".join(bits))}
                          </text>'''
        # Client-Version bei Details oder Netdetails
        cv_line = ""
        if (details or netdetails) and cv:
            cv_line = f'''<text x="0" y="{card_h//2 - 8}" fill="{GRAY_MUTE}"
                                text-anchor="middle"
                                style="font-weight:500;font-size:9px;
                                       font-family:'Red Hat Display',Arial,sans-serif;">
                            Client v{_esc(cv)}
                          </text>'''
        return f'''
        <g transform="translate({x},{y})">
          <rect x="-{card_w//2}" y="-{card_h//2}"
                width="{card_w}" height="{card_h}" rx="10"
                fill="#fff" stroke="{GRAY_BORD}" stroke-width="1"/>
          <use href="#{icon}" x="-32" y="-{card_h//2 - 8}"
               width="64" height="64" color="{col}"/>
          <text x="0" y="30" fill="{NAVY}" text-anchor="middle"
                style="font-weight:700;font-size:11px;
                       font-family:'Red Hat Display',Arial,sans-serif;">
            {_esc(name)}
          </text>
          <text x="0" y="44" fill="{GRAY_MUTE}" text-anchor="middle"
                style="font-weight:500;font-size:9px;
                       font-family:'Red Hat Display',Arial,sans-serif;">
            {_esc(os_str)}
          </text>
          <text x="0" y="56" fill="{col}" text-anchor="middle"
                style="font-weight:700;font-size:8px;letter-spacing:.5px;
                       font-family:'Red Hat Display',Arial,sans-serif;">
            {_esc(ws_type)}
          </text>
          {net_line}
          {cv_line}
        </g>'''

    if kind == "user":
        # Bei Details Karte größer machen und Email zeigen
        u_w = 180 if details else 140
        u_h = 60 if details else 48
        name = _truncate(node["label"], 32 if details else 20)
        dept = _truncate(node["sub"] or "User", 24 if details else 20)
        email = (node["meta"].get("email") or "").strip()
        email_line = ""
        if details and email:
            email_line = f'''<text x="10" y="20" fill="{DARK_GREEN}"
                                  text-anchor="middle"
                                  style="font-weight:500;font-size:8px;
                                         font-family:monospace;">
                              {_esc(_truncate(email, 30))}
                            </text>'''
        return f'''
        <g transform="translate({x},{y})">
          <rect x="-{u_w//2}" y="-{u_h//2}" width="{u_w}" height="{u_h}"
                rx="{u_h//2}" fill="{GREEN}" opacity=".18"
                stroke="{DARK_GREEN}" stroke-width="1" stroke-opacity=".4"/>
          <use href="#ico-user" x="-{u_w//2 - 6}" y="-16" width="32" height="32"
               color="{DARK_GREEN}"/>
          <text x="10" y="-4" fill="{NAVY}" text-anchor="middle"
                style="font-weight:700;font-size:10px;
                       font-family:'Red Hat Display',Arial,sans-serif;">
            {_esc(name)}
          </text>
          <text x="10" y="10" fill="{DARK_GREEN}" text-anchor="middle"
                style="font-weight:500;font-size:9px;
                       font-family:'Red Hat Display',Arial,sans-serif;">
            {_esc(dept)}
          </text>
          {email_line}
        </g>'''

    return ""


def _iter_edges(nodes: list[dict]):
    for n in nodes:
        for c in n.get("_children") or []:
            yield n, c
            yield from _iter_edges([c])


def _iter_nodes(nodes: list[dict]):
    for n in nodes:
        yield n
        yield from _iter_nodes(n.get("_children") or [])


def _node_bottom(kind: str, details: bool = False) -> int:
    if kind == "root": return ROOT_H // 2
    if kind == "site": return SITE_H // 2
    if kind == "network": return NET_H // 2
    if kind in ("printer", "workstation"):
        return (LEAF_H + 34) // 2 if details else LEAF_H // 2
    if kind == "user":
        return 30 if details else 24
    return 0


def _node_top(kind: str, details: bool = False) -> int:
    return _node_bottom(kind, details)


def render_svg(topology: dict, filters: dict) -> tuple[str, dict]:
    if not topology:
        return ("", {"empty": True})

    tree = _build_render_tree(topology, filters)
    if not tree:
        return ("", {"empty": True})

    # Bei Details ON: Slots etwas breiter machen fuer laengere Texte
    details_on = bool(filters.get("show_details") or filters.get("show_netdetails"))
    slot_w = 220 if details_on else LEAF_SLOT_W
    used_w, positions = _layout_tree(tree, 0, LEFT_PAD, slot_w)
    _assign_y(tree, positions)

    # Overall dimensions
    if not positions:
        return ("", {"empty": True})
    max_x = max(x for (x, _) in positions.values())
    max_y = max(y for (_, y) in positions.values())
    width = max_x + LEAF_SLOT_W // 2 + LEFT_PAD
    height = max_y + LEAF_H // 2 + BOTTOM_PAD

    # Edges (Bezier von Parent-Bottom zu Child-Top)
    edges = []
    for parent, child in _iter_edges(tree):
        px, py = positions[parent["id"]]
        cx, cy = positions[child["id"]]
        p_bottom = py + _node_bottom(parent["kind"], details_on)
        c_top    = cy - _node_top(child["kind"], details_on)
        mid_y = (p_bottom + c_top) // 2
        edges.append(
            f'<path d="M {px} {p_bottom} C {px} {mid_y}, {cx} {mid_y}, '
            f'{cx} {c_top}" fill="none" stroke="{GRAY_BORD}" '
            f'stroke-width="1.5" opacity=".7"/>'
        )

    nodes_svg = [_render_node(n, positions) for n in _iter_nodes(tree)]

    # Alle Nodes+Edges in einer Pan-Zoom-Ebene wrappen — JS ueber der Seite
    # transformiert diese Ebene fuer Zoom/Pan.
    svg = f'''
    <svg xmlns="http://www.w3.org/2000/svg"
         viewBox="0 0 {width} {height}"
         width="{width}" height="{height}"
         class="nm-svg"
         style="max-width: 100%; height: auto; display: block; margin: 0 auto;
                font-family: 'Red Hat Display', Arial, sans-serif;
                cursor: grab; touch-action: none; user-select: none;">
      {_svg_defs()}
      <g class="pan-zoom-layer" transform="translate(0,0) scale(1)">
        <g class="edges">{''.join(edges)}</g>
        <g class="nodes">{''.join(nodes_svg)}</g>
      </g>
    </svg>
    '''
    return (svg, {"empty": False, "width": width, "height": height})
