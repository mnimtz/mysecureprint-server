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
from fastapi.responses import HTMLResponse, RedirectResponse
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

    @app.get("/admin/network-map", response_class=HTMLResponse)
    async def netmap_page(request: Request):
        user = _admin_or_login(request)
        if not user:
            return RedirectResponse("/login", status_code=302)
        import sys, os, asyncio
        src_dir = os.path.dirname(os.path.dirname(__file__))
        if src_dir not in sys.path:
            sys.path.insert(0, src_dir)
        from bi_client import fetch_network_topology
        from network_map import render_svg

        tenant = _load_tenant(user)
        bi_configured = tenant and all([
            (tenant.get("sql_server")   or "").strip(),
            (tenant.get("sql_database") or "").strip(),
            (tenant.get("sql_username") or "").strip(),
            (tenant.get("sql_password") or "").strip(),
        ])
        topology = None
        if bi_configured:
            topology = await asyncio.to_thread(fetch_network_topology, tenant)

        # Filter aus Query-Params
        qp = request.query_params
        sel_sites_raw = (qp.get("sites") or "").strip()
        sel_sites = [s for s in sel_sites_raw.split(",") if s] if sel_sites_raw else []
        show_all_sites = not sel_sites
        show_printers = qp.get("printers", "1") != "0"
        show_workstations = qp.get("workstations", "1") != "0"
        show_users = qp.get("users", "0") == "1"

        filters = {
            "sites": sel_sites,
            "show_all_sites": show_all_sites,
            "show_printers": show_printers,
            "show_workstations": show_workstations,
            "show_users": show_users,
        }

        svg, stats = render_svg(topology, filters) if topology else ("", {"empty": True})

        return templates.TemplateResponse("admin_network_map.html", {
            "request": request, "user": user,
            "bi_configured": bi_configured,
            "topology": topology,
            "svg": svg, "stats": stats,
            "filters": filters,
            "active_page": "admin_network_map",
            **t_ctx(request),
        })
