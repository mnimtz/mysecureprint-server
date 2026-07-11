"""Admin-Route fuer den Netzwerk-Topologie-Plan.

GET  /admin/network-map       — Diagramm mit Filter-Sidebar
POST-Query-Params:
  sites=id1,id2,...  (leer = alle)
  printers=1|0
  workstations=1|0
  users=1|0
"""
from __future__ import annotations

import logging
from typing import Optional

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

logger = logging.getLogger(__name__)


def register_network_map_routes(app: FastAPI,
                                templates: Jinja2Templates,
                                get_session_user,
                                t_ctx) -> None:

    def _admin_or_login(request: Request):
        u = get_session_user(request)
        return u if (u and u.get("is_admin")) else None

    def _load_tenant(user: dict) -> Optional[dict]:
        import sys, os
        src_dir = os.path.dirname(os.path.dirname(__file__))
        if src_dir not in sys.path:
            sys.path.insert(0, src_dir)
        from db import get_tenant_full_by_user_id, _resolve_tenant_owner_for
        owner = _resolve_tenant_owner_for(user["id"]) or user["id"]
        return get_tenant_full_by_user_id(owner)

    def _parse_filters(request: Request) -> dict:
        qp = request.query_params
        sel_sites_raw = (qp.get("sites") or "").strip()
        sel_sites = [s for s in sel_sites_raw.split(",") if s] if sel_sites_raw else []
        return {
            "sites": sel_sites,
            "show_all_sites": not sel_sites,
            "show_printers":     qp.get("printers", "1") != "0",
            "show_workstations": qp.get("workstations", "1") != "0",
            "show_users":        qp.get("users", "0") == "1",
            "show_details":      qp.get("details", "0") == "1",
            "show_netdetails":   qp.get("netdetails", "0") == "1",
        }

    @app.get("/admin/network-map", response_class=HTMLResponse)
    async def netmap_page(request: Request):
        """v0.7.277: rendert nur die Seiten-Huelle. Die BI-DB-Query kann
        beim ersten Zugriff 15-30s dauern (Azure Auto-Pause) — statt den
        Browser so lange warten zu lassen, laden wir das SVG asynchron
        via /admin/network-map/data.
        Wenn das Topology-Ergebnis im Cache liegt (10 min TTL nach
        v0.7.276), kann der Client instant rendern.
        """
        user = _admin_or_login(request)
        if not user:
            return RedirectResponse("/login", status_code=302)
        import sys, os
        src_dir = os.path.dirname(os.path.dirname(__file__))
        if src_dir not in sys.path:
            sys.path.insert(0, src_dir)
        tenant = _load_tenant(user)
        bi_configured = tenant and all([
            (tenant.get("sql_server")   or "").strip(),
            (tenant.get("sql_database") or "").strip(),
            (tenant.get("sql_username") or "").strip(),
            (tenant.get("sql_password") or "").strip(),
        ])

        filters = _parse_filters(request)
        # Site-Liste nur wenn Cache verfuegbar — sonst leeres Skelett
        # (Client haengt sich vor dem ersten Rendern die Site-Liste dazu)
        topology_cached = None
        if bi_configured:
            try:
                from bi_client import _TOPOLOGY_CACHE  # noqa: WPS437
                tk = str(tenant.get("printix_tenant_id") or tenant.get("id") or "")
                entry = _TOPOLOGY_CACHE.get(tk)
                if entry:
                    topology_cached = entry[1]
            except Exception:
                topology_cached = None

        return templates.TemplateResponse("admin_network_map.html", {
            "request": request, "user": user,
            "bi_configured": bi_configured,
            "topology": topology_cached,
            "svg": "", "stats": {},
            "filters": filters,
            "async_load": bi_configured,
            "active_page": "admin_network_map",
            **t_ctx(request),
        })

    @app.get("/admin/network-map/data", response_class=JSONResponse)
    async def netmap_data(request: Request):
        """Liefert das gerenderte SVG + Statistik als JSON. Wird vom
        Frontend nach dem Laden der Huelle via XHR aufgerufen."""
        user = _admin_or_login(request)
        if not user:
            return JSONResponse({"error": "auth"}, status_code=403)
        import sys, os, asyncio
        src_dir = os.path.dirname(os.path.dirname(__file__))
        if src_dir not in sys.path:
            sys.path.insert(0, src_dir)
        from bi_client import fetch_network_topology
        from network_map import render_svg

        tenant = _load_tenant(user)
        if not tenant:
            return JSONResponse({"error": "no_tenant"}, status_code=404)
        bi_configured = all([
            (tenant.get("sql_server")   or "").strip(),
            (tenant.get("sql_database") or "").strip(),
            (tenant.get("sql_username") or "").strip(),
            (tenant.get("sql_password") or "").strip(),
        ])
        if not bi_configured:
            return JSONResponse({"error": "no_creds"}, status_code=400)

        force = request.query_params.get("refresh") == "1"
        try:
            topology = await asyncio.to_thread(
                fetch_network_topology, tenant, force)
        except Exception as e:  # noqa: BLE001
            return JSONResponse({"error": str(e)[:200]}, status_code=502)
        if topology is None:
            return JSONResponse({"error": "bi_unreachable"}, status_code=503)

        filters = _parse_filters(request)
        svg, stats = render_svg(topology, filters)
        # Auch die Site-Liste zurueckliefern damit das Frontend die Sidebar
        # nachtraeglich befuellen kann (falls initial ohne Cache geladen)
        sites_meta = [{"id": s["id"], "name": s["name"]}
                      for s in (topology.get("sites") or [])]
        return JSONResponse({
            "svg": svg, "stats": stats,
            "counts": topology.get("counts", {}),
            "sites": sites_meta,
        })
