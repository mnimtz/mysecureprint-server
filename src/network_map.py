"""Server-side Netzwerk-Topologie-Diagramm.

Nimmt einen `fetch_network_topology`-Baum von bi_client, filtert nach
User-Auswahl (welche Sites, ob Drucker/Workstations/Users) und rendert
das Ganze als inline-SVG. Layout ist ein einfacher bottom-up-tidy-tree:
Leaves bekommen einen Slot fester Breite, innere Knoten sitzen zentriert
ueber ihren Kindern.

Icons sind minimalistische Inline-SVGs — Drucker sind pro Hersteller
farb-differenziert (vendor-aware), Workstations nach OS/Typ. User sind
generisch.
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

# ── Layout-Parameter ─────────────────────────────────────────────────
SLOT_W = 130          # px pro Leaf
LEVEL_H = 140         # px zwischen Ebenen
TOP_PAD = 60
LEFT_PAD = 40
NODE_W = 100          # Karten-Breite
NODE_H = 90           # Karten-Hoehe fuer Drucker/Workstation
NODE_H_USER = 46
SITE_H = 44           # Kleinere Karten fuer Sites+Networks
NET_H = 44


# ── Vendor-Farbmapping fuer Drucker-Icons ────────────────────────────
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
}


def _vendor_color(vendor: str) -> str:
    v = (vendor or "").lower()
    for key, col in _VENDOR_COLORS.items():
        if key in v:
            return col
    return NAVY


# ── Icons als Inline-SVG-Symbols ─────────────────────────────────────
def _svg_defs() -> str:
    """Alle Icon-Definitionen fuer <use>-Referenzen."""
    return f'''
    <defs>
      <!-- Drucker (Multifunktions-MFP) mit Scanner-Deckel + Bedienpanel -->
      <symbol id="ico-mfp" viewBox="0 0 60 60">
        <rect x="6" y="6" width="48" height="6" rx="1" fill="#334155"/>
        <rect x="10" y="12" width="40" height="12" rx="1" fill="#94a3b8" opacity=".5"/>
        <rect x="6" y="24" width="48" height="26" rx="2" fill="currentColor"/>
        <rect x="38" y="27" width="12" height="8" rx="1" fill="#1e293b"/>
        <rect x="40" y="29" width="8" height="4" fill="{TUNGSTEN_BLUE}" opacity=".9"/>
        <rect x="10" y="38" width="26" height="10" rx="1" fill="#334155"/>
        <rect x="10" y="34" width="30" height="3" fill="#f8fafc"/>
        <rect x="6" y="50" width="48" height="4" rx="1" fill="#334155"/>
      </symbol>
      <!-- Reiner Printer (kein Scanner) -->
      <symbol id="ico-printer" viewBox="0 0 60 60">
        <rect x="10" y="16" width="40" height="4" fill="#334155"/>
        <rect x="6" y="20" width="48" height="26" rx="2" fill="currentColor"/>
        <rect x="42" y="24" width="8" height="4" rx="1" fill="{TUNGSTEN_BLUE}"/>
        <rect x="14" y="32" width="32" height="10" rx="1" fill="#f8fafc"/>
      </symbol>
      <!-- Desktop-Tower -->
      <symbol id="ico-desktop" viewBox="0 0 60 60">
        <rect x="18" y="8" width="24" height="40" rx="2" fill="currentColor"/>
        <circle cx="30" cy="16" r="1.5" fill="{GREEN}"/>
        <rect x="22" y="22" width="16" height="2" fill="#334155"/>
        <rect x="22" y="26" width="16" height="2" fill="#334155"/>
        <rect x="14" y="52" width="32" height="4" rx="1" fill="#334155"/>
      </symbol>
      <!-- Laptop / MacBook (Clamshell) -->
      <symbol id="ico-laptop" viewBox="0 0 60 60">
        <path d="M 10 14 h 40 v 26 h -40 z" fill="currentColor"/>
        <rect x="13" y="17" width="34" height="20" fill="#0f172a"/>
        <rect x="15" y="19" width="30" height="16" fill="{TUNGSTEN_BLUE}" opacity=".4"/>
        <path d="M 4 44 h 52 v 4 l -4 4 h -44 l -4 -4 z" fill="#94a3b8"/>
      </symbol>
      <!-- Server (Rack-Style, gestapelt) -->
      <symbol id="ico-server" viewBox="0 0 60 60">
        <rect x="10" y="8" width="40" height="12" rx="1" fill="currentColor"/>
        <circle cx="14" cy="14" r="1" fill="{GREEN}"/>
        <circle cx="18" cy="14" r="1" fill="{TUNGSTEN_BLUE}"/>
        <rect x="10" y="22" width="40" height="12" rx="1" fill="currentColor"/>
        <circle cx="14" cy="28" r="1" fill="{GREEN}"/>
        <circle cx="18" cy="28" r="1" fill="{TUNGSTEN_BLUE}"/>
        <rect x="10" y="36" width="40" height="12" rx="1" fill="currentColor"/>
        <circle cx="14" cy="42" r="1" fill="{GREEN}"/>
        <circle cx="18" cy="42" r="1" fill="{TUNGSTEN_BLUE}"/>
        <rect x="10" y="50" width="40" height="4" rx="1" fill="#334155"/>
      </symbol>
      <!-- Mobile -->
      <symbol id="ico-mobile" viewBox="0 0 60 60">
        <rect x="18" y="6" width="24" height="48" rx="4" fill="currentColor"/>
        <rect x="21" y="12" width="18" height="30" fill="#0f172a"/>
        <rect x="22" y="13" width="16" height="28" fill="{TUNGSTEN_BLUE}" opacity=".5"/>
        <circle cx="30" cy="48" r="2" fill="#334155"/>
      </symbol>
      <!-- User (generisch) -->
      <symbol id="ico-user" viewBox="0 0 60 60">
        <circle cx="30" cy="22" r="10" fill="currentColor"/>
        <path d="M 12 52 c 0 -10 8 -16 18 -16 s 18 6 18 16 z" fill="currentColor"/>
      </symbol>
      <!-- Site (Gebaeude) -->
      <symbol id="ico-site" viewBox="0 0 60 60">
        <path d="M 8 52 v -32 l 22 -12 l 22 12 v 32 z" fill="currentColor"/>
        <rect x="16" y="28" width="6" height="6" fill="{LIGHT_BLUE}"/>
        <rect x="27" y="28" width="6" height="6" fill="{LIGHT_BLUE}"/>
        <rect x="38" y="28" width="6" height="6" fill="{LIGHT_BLUE}"/>
        <rect x="16" y="40" width="6" height="6" fill="{LIGHT_BLUE}"/>
        <rect x="27" y="40" width="6" height="10" fill="{TUNGSTEN_BLUE}"/>
        <rect x="38" y="40" width="6" height="6" fill="{LIGHT_BLUE}"/>
      </symbol>
      <!-- Network (Cloud / SSID) -->
      <symbol id="ico-network" viewBox="0 0 60 60">
        <path d="M 12 40 a 10 10 0 0 1 4 -19 a 12 12 0 0 1 23 -1 a 8 8 0 0 1 9 20 z"
              fill="currentColor"/>
        <circle cx="22" cy="34" r="1.5" fill="#f8fafc"/>
        <circle cx="30" cy="34" r="1.5" fill="#f8fafc"/>
        <circle cx="38" cy="34" r="1.5" fill="#f8fafc"/>
      </symbol>
    </defs>
    '''


def _icon_for_printer(vendor: str, model: str) -> str:
    """Wahl zwischen mfp und printer-only anhand Modell-Heuristik."""
    m = (model or "").lower()
    if any(k in m for k in ("mfp", "mfc", "taskalfa", "aficio", "workcentre",
                             "colorjet mfp", "imagerunner", "smart tank",
                             "workforce", "printix2me")):
        return "ico-mfp"
    return "ico-mfp"  # Default MFP — die meisten Firmen-Drucker sind MFPs


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


# ── Layout ──────────────────────────────────────────────────────────

def _layout_tree(nodes: list[dict], level: int, x_start: int,
                 slot_width: int, level_height: int) -> tuple[int, dict]:
    """Rechnet Positionen. Jeder Node hat 'id' + optional 'children'.
    Returns (used_width, {id: (x, y)}).
    """
    if not nodes:
        return (0, {})
    positions = {}
    x = x_start
    for node in nodes:
        children = node.get("_children") or []
        if not children:
            positions[node["id"]] = (x + slot_width // 2, level * level_height + TOP_PAD)
            x += slot_width
        else:
            w, cp = _layout_tree(children, level + 1, x, slot_width, level_height)
            positions.update(cp)
            first = cp[children[0]["id"]][0]
            last = cp[children[-1]["id"]][0]
            positions[node["id"]] = ((first + last) // 2, level * level_height + TOP_PAD)
            x += max(w, slot_width)
    return (x - x_start, positions)


def _build_render_tree(topology: dict, filters: dict) -> list[dict]:
    """Nimmt den topology-Baum und die Filter, gibt Nodes zurueck die
    _layout_tree versteht. Jeder Node: {id, kind, label, sub, meta,
    _children[]}.
    """
    sel_sites = set(filters.get("sites") or [])
    show_all_sites = filters.get("show_all_sites", True)
    show_printers = filters.get("show_printers", True)
    show_workstations = filters.get("show_workstations", True)
    show_users = filters.get("show_users", False)

    def _mkuser(u: dict) -> dict:
        return {
            "id":    f"u:{u['id']}",
            "kind":  "user",
            "label": u.get("name") or u.get("email") or "?",
            "sub":   u.get("department") or "",
            "meta":  u,
            "_children": [],
        }

    def _mkws(w: dict) -> dict:
        kids = [_mkuser(u) for u in w.get("users", [])] if show_users else []
        return {
            "id":    f"w:{w['id']}",
            "kind":  "workstation",
            "label": w.get("name") or "?",
            "sub":   (w.get("os") or "").strip(),
            "meta":  w,
            "_children": kids,
        }

    def _mkprinter(p: dict) -> dict:
        return {
            "id":    f"p:{p['id']}",
            "kind":  "printer",
            "label": p.get("name") or "?",
            "sub":   p.get("vendor") or p.get("model") or "",
            "meta":  p,
            "_children": [],
        }

    result_sites = []
    for site in topology.get("sites", []):
        if not show_all_sites and site["id"] not in sel_sites:
            continue
        net_children = []
        for net in site.get("networks", []):
            printer_kids = ([_mkprinter(p) for p in net.get("printers", [])]
                            if show_printers else [])
            ws_kids = ([_mkws(w) for w in net.get("workstations", [])]
                       if show_workstations else [])
            children = printer_kids + ws_kids
            if not children:
                continue
            net_children.append({
                "id":    f"n:{net['id']}",
                "kind":  "network",
                "label": net.get("name") or "?",
                "sub":   "mobile" if net.get("mobile_print") else "",
                "meta":  net,
                "_children": children,
            })
        if not net_children:
            continue
        result_sites.append({
            "id":    f"s:{site['id']}",
            "kind":  "site",
            "label": site.get("name") or "?",
            "sub":   site.get("type") or "",
            "meta":  site,
            "_children": net_children,
        })

    # Root-Wrapper — falls > 1 Site, sonst direkt die Site als Root
    if not result_sites:
        return []
    return [{
        "id":    "root",
        "kind":  "root",
        "label": "Tenant",
        "sub":   "",
        "meta":  {},
        "_children": result_sites,
    }]


# ── SVG-Rendering ────────────────────────────────────────────────────

def _esc(s: str) -> str:
    return html.escape(str(s or ""), quote=True)


def _truncate(s: str, n: int) -> str:
    if len(s) <= n:
        return s
    return s[: n - 1] + "…"


def _render_node(node: dict, positions: dict) -> str:
    x, y = positions[node["id"]]
    kind = node["kind"]
    label = _truncate(node["label"], 18)
    sub = _truncate(node["sub"], 22)

    if kind == "root":
        return f'''
        <g class="node root" transform="translate({x},{y})">
          <rect x="-70" y="-24" width="140" height="48" rx="10"
                fill="{DEEP_NAVY}"/>
          <text x="0" y="0" fill="#fff" text-anchor="middle"
                dominant-baseline="middle"
                style="font-weight:700;font-size:14px;">Tenant</text>
          <text x="0" y="14" fill="{LIGHT_BLUE}" text-anchor="middle"
                dominant-baseline="middle"
                style="font-weight:500;font-size:11px;">
            {_esc(node["sub"])}
          </text>
        </g>'''

    if kind == "site":
        return f'''
        <g class="node site" transform="translate({x},{y})">
          <rect x="-70" y="-22" width="140" height="44" rx="8"
                fill="#fff" stroke="{NAVY}" stroke-width="2"/>
          <g transform="translate(-58,-14) scale(0.45)" color="{NAVY}">
            <use href="#ico-site"/>
          </g>
          <text x="-20" y="-4" fill="{NAVY}"
                style="font-weight:700;font-size:12px;">
            {_esc(label)}
          </text>
          <text x="-20" y="10" fill="{GRAY_MUTE}"
                style="font-weight:500;font-size:10px;">
            {_esc(sub or "Site")}
          </text>
        </g>'''

    if kind == "network":
        return f'''
        <g class="node network" transform="translate({x},{y})">
          <rect x="-65" y="-22" width="130" height="44" rx="8"
                fill="{LIGHT_BLUE}" opacity=".2"
                stroke="{TUNGSTEN_BLUE}" stroke-width="1.5"/>
          <g transform="translate(-56,-14) scale(0.42)" color="{TUNGSTEN_BLUE}">
            <use href="#ico-network"/>
          </g>
          <text x="-22" y="-4" fill="{NAVY}"
                style="font-weight:700;font-size:11px;">
            {_esc(label)}
          </text>
          <text x="-22" y="9" fill="{GRAY_MUTE}"
                style="font-weight:500;font-size:9px;">
            {_esc("Netzwerk" + (" · mobile" if sub == "mobile" else ""))}
          </text>
        </g>'''

    if kind == "printer":
        vendor_color = _vendor_color(node["meta"].get("vendor", ""))
        icon = _icon_for_printer(node["meta"].get("vendor", ""),
                                 node["meta"].get("model", ""))
        return f'''
        <g class="node printer" transform="translate({x},{y})">
          <rect x="-55" y="-45" width="110" height="90" rx="10"
                fill="#fff" stroke="{GRAY_BORD}" stroke-width="1"/>
          <g transform="translate(-25,-40) scale(0.85)" color="{vendor_color}">
            <use href="#ico-mfp"/>
          </g>
          <text x="0" y="24" fill="{NAVY}" text-anchor="middle"
                style="font-weight:700;font-size:11px;">
            {_esc(label)}
          </text>
          <text x="0" y="38" fill="{GRAY_MUTE}" text-anchor="middle"
                style="font-weight:500;font-size:9px;">
            {_esc(sub)}
          </text>
        </g>'''

    if kind == "workstation":
        icon = _icon_for_workstation(node["meta"])
        # Farbe: nach OS
        os_str = (node["meta"].get("os") or "").lower()
        if "mac" in os_str or "darwin" in os_str:
            wcol = "#94a3b8"
        elif "linux" in os_str:
            wcol = "#f59e0b"
        else:
            wcol = TUNGSTEN_BLUE
        return f'''
        <g class="node workstation" transform="translate({x},{y})">
          <rect x="-55" y="-45" width="110" height="90" rx="10"
                fill="#fff" stroke="{GRAY_BORD}" stroke-width="1"/>
          <g transform="translate(-25,-40) scale(0.85)" color="{wcol}">
            <use href="#{icon}"/>
          </g>
          <text x="0" y="24" fill="{NAVY}" text-anchor="middle"
                style="font-weight:700;font-size:11px;">
            {_esc(label)}
          </text>
          <text x="0" y="38" fill="{GRAY_MUTE}" text-anchor="middle"
                style="font-weight:500;font-size:9px;">
            {_esc(sub)}
          </text>
        </g>'''

    if kind == "user":
        return f'''
        <g class="node user" transform="translate({x},{y})">
          <rect x="-52" y="-22" width="104" height="44" rx="22"
                fill="{GREEN}" opacity=".15"/>
          <g transform="translate(-45,-14) scale(0.45)" color="{DARK_GREEN}">
            <use href="#ico-user"/>
          </g>
          <text x="-14" y="-4" fill="{NAVY}"
                style="font-weight:700;font-size:10px;">
            {_esc(label)}
          </text>
          <text x="-14" y="9" fill="{GRAY_MUTE}"
                style="font-weight:500;font-size:9px;">
            {_esc(sub or "User")}
          </text>
        </g>'''

    return ""


def _iter_edges(nodes: list[dict]):
    """Yields (parent, child) fuer alle Kind-Kanten."""
    for n in nodes:
        for c in n.get("_children") or []:
            yield n, c
            yield from _iter_edges([c])


def _iter_nodes(nodes: list[dict]):
    for n in nodes:
        yield n
        yield from _iter_nodes(n.get("_children") or [])


def render_svg(topology: dict, filters: dict) -> tuple[str, dict]:
    """Rendert die vollständige SVG. Returns (svg_str, stats)."""
    if not topology:
        return ("", {"empty": True})

    tree = _build_render_tree(topology, filters)
    if not tree:
        return ("", {"empty": True})

    used_w, positions = _layout_tree(tree, 0, LEFT_PAD, SLOT_W, LEVEL_H)

    # Hoehe = max_level * LEVEL_H + Bottom-Padding
    max_y = max(y for (_, y) in positions.values()) if positions else 0
    height = max_y + 80

    # Edges zuerst rendern (unter den Nodes)
    edges = []
    for parent, child in _iter_edges(tree):
        px, py = positions[parent["id"]]
        cx, cy = positions[child["id"]]
        # Curved Bezier von parent-unten zu child-oben
        mid_y = (py + cy) // 2
        edges.append(
            f'<path d="M {px} {py + 22} C {px} {mid_y}, {cx} {mid_y}, '
            f'{cx} {cy - 22}" fill="none" stroke="{GRAY_BORD}" '
            f'stroke-width="1.5"/>'
        )

    nodes_svg = [_render_node(n, positions) for n in _iter_nodes(tree)]

    stats = {
        "empty": False,
        "width": used_w + LEFT_PAD,
        "height": height,
    }

    svg = f'''
    <svg xmlns="http://www.w3.org/2000/svg"
         viewBox="0 0 {stats["width"]} {stats["height"]}"
         style="width: 100%; height: auto; font-family: 'Red Hat Display', Arial, sans-serif;">
      {_svg_defs()}
      <g class="edges">{''.join(edges)}</g>
      <g class="nodes">{''.join(nodes_svg)}</g>
    </svg>
    '''
    return (svg, stats)
