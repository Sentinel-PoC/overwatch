"""
l7_renderer.py — L7 Application layer renderer.

Renders Traefik IngressRoutes + Istio VirtualServices + OKD Routes into:
  architecture-vault/07-L7-application/INDEX.md     (app routing table — BSides centerpiece)
  architecture-vault/07-L7-application/tls-termination.d2  (TLS boundary D2 diagram)

Template: overwatch-gen/templates/l7_application.md.j2
          overwatch-gen/templates/l7.d2.j2

The app-routing table is the headline visual:
  hostname | source | TLS issuer | Traefik middleware chain | Istio VS | destination service | AuthzPolicy
"""

import logging
import os
import subprocess
from pathlib import Path

logger = logging.getLogger(__name__)

_TEMPLATE_DIR = Path(__file__).parent.parent / "templates"
_VAULT_DIR = Path(__file__).parent.parent.parent / "architecture-vault"
_L7_DIR = _VAULT_DIR / "07-L7-application"

_MD_TEMPLATE = _TEMPLATE_DIR / "l7_application.md.j2"
_D2_TEMPLATE = _TEMPLATE_DIR / "l7.d2.j2"


def _dry_run() -> bool:
    return os.environ.get("OVERWATCH_GEN_DRY_RUN", "").lower() in ("1", "true", "yes")


def _render_md(context: dict) -> str:
    from overwatch_gen.lib.render import render_markdown
    return render_markdown(_MD_TEMPLATE, context)


def _render_d2(context: dict) -> str:
    from overwatch_gen.lib.render import render_markdown
    return render_markdown(_D2_TEMPLATE, context)


def _run_d2(d2_path: Path, svg_path: Path) -> None:
    """Run d2 binary to produce SVG. Logs warning if d2 not installed."""
    try:
        result = subprocess.run(
            ["d2", str(d2_path), str(svg_path)],
            capture_output=True,
            text=True,
            timeout=60,
        )
        if result.returncode != 0:
            logger.warning("d2 render failed for %s: %s", d2_path, result.stderr[:300])
        else:
            logger.info("d2: rendered %s", svg_path)
    except FileNotFoundError:
        logger.warning(
            "d2 binary not found — install d2 for SVG output. Skipping %s", svg_path
        )
    except subprocess.TimeoutExpired:
        logger.warning("d2 timed out rendering %s", d2_path)


def _build_app_routing_table(
    traefik_data: dict,
    istio_data: dict,
    okd_data: dict,
) -> list[dict]:
    """
    Build the unified application routing table.

    One row per public hostname. Columns:
      hostname            — public-facing hostname
      source              — "IngressRoute", "OKD Route"
      source_name         — CR name (namespace/name)
      tls_issuer          — cert issuer name
      middleware_chain    — ordered middleware names (Traefik only)
      istio_vs            — Istio VirtualService name if any, else ""
      authz_policy        — AuthorizationPolicy name if any, else ""
      destination_service — backend service name + port
    """
    rows = []

    # Build Istio lookup: host -> VS name, authz policies
    istio_vs_by_host: dict[str, str] = {}
    for vs in istio_data.get("virtual_services", []):
        for host in vs.get("hosts", []):
            istio_vs_by_host[host] = f"{vs['namespace']}/{vs['name']}"

    authz_by_ns: dict[str, list[str]] = {}
    for ap in istio_data.get("authorization_policies", []):
        ns = ap.get("namespace", "")
        authz_by_ns.setdefault(ns, []).append(ap["name"])

    # Traefik IngressRoute rows
    for route in traefik_data.get("ingress_routes", []):
        host = route.get("host", "")
        ns = route.get("namespace", "")
        svc = route.get("service", "")
        port = route.get("service_port", 0)
        dest = f"{svc}:{port}" if port else svc

        authz_policies = authz_by_ns.get(ns, [])
        authz_name = ", ".join(authz_policies) if authz_policies else ""

        rows.append({
            "hostname": host,
            "source": "IngressRoute",
            "source_name": f"{ns}/{route.get('name', '')}",
            "tls_issuer": route.get("tls_issuer", ""),
            "middleware_chain": " → ".join(route.get("middlewares", [])),
            "istio_vs": istio_vs_by_host.get(host, ""),
            "authz_policy": authz_name,
            "destination_service": dest,
        })

    # OKD Route rows
    for route in okd_data.get("routes", []):
        host = route.get("host", "")
        ns = route.get("namespace", "")
        dest = route.get("destination_service", "")
        port = route.get("destination_port", 0)
        dest_full = f"{dest}:{port}" if port else dest

        term = route.get("tls_termination", "none")
        tls_label = f"OKD/{term}" if term != "none" else "none"

        rows.append({
            "hostname": host,
            "source": "OKD Route",
            "source_name": f"{ns}/{route.get('name', '')}",
            "tls_issuer": tls_label,
            "middleware_chain": "",
            "istio_vs": "",
            "authz_policy": "",
            "destination_service": dest_full,
        })

    # Sort: hostname alphabetically
    rows.sort(key=lambda r: r.get("hostname", ""))
    return rows


def _build_tls_boundaries(
    traefik_data: dict, istio_data: dict
) -> list[dict]:
    """
    Extract TLS boundary info for the d2 diagram.

    Returns list of boundary segments:
      segment — (from_node, to_node, label, is_mtls)
    """
    boundaries = []

    # Static outer boundary: internet -> Traefik
    boundaries.append({
        "from": "internet",
        "to": "traefik",
        "label": "TLS (Traefik terminates)",
        "is_mtls": False,
    })

    # Check if Istio ingress gateway is in use (any VS has gateway ref)
    has_istio_gw = any(
        vs.get("gateways")
        for vs in istio_data.get("virtual_services", [])
    )

    if has_istio_gw:
        boundaries.append({
            "from": "traefik",
            "to": "istio_ingress_gw",
            "label": "mTLS (Istio)",
            "is_mtls": True,
        })
        boundaries.append({
            "from": "istio_ingress_gw",
            "to": "sidecar",
            "label": "mTLS (ISTIO_MUTUAL)",
            "is_mtls": True,
        })
        boundaries.append({
            "from": "sidecar",
            "to": "workload",
            "label": "plaintext (pod-local)",
            "is_mtls": False,
        })
    else:
        boundaries.append({
            "from": "traefik",
            "to": "workload",
            "label": "plaintext (cluster-internal)",
            "is_mtls": False,
        })

    return boundaries


def _build_l7_context(
    traefik_data: dict,
    istio_data: dict,
    okd_data: dict,
) -> dict:
    """Build Jinja2 context for L7 templates."""
    warnings = []
    for data, name in [(traefik_data, "Traefik"), (istio_data, "Istio"), (okd_data, "OKD Routes")]:
        if data.get("_warning"):
            warnings.append(f"{name}: {data['_warning']}")

    routing_table = _build_app_routing_table(traefik_data, istio_data, okd_data)
    tls_boundaries = _build_tls_boundaries(traefik_data, istio_data)

    middlewares = traefik_data.get("middlewares", [])

    return {
        "routing_table": routing_table,
        "tls_boundaries": tls_boundaries,
        "middlewares": middlewares,
        "virtual_services": istio_data.get("virtual_services", []),
        "destination_rules": istio_data.get("destination_rules", []),
        "authorization_policies": istio_data.get("authorization_policies", []),
        "okd_routes": okd_data.get("routes", []),
        "warnings": warnings,
        "route_count": len(routing_table),
        "has_istio": bool(istio_data.get("virtual_services")),
        "has_okd": bool(okd_data.get("routes")),
        "has_traefik": bool(traefik_data.get("ingress_routes")),
    }


def render(traefik_data: dict, istio_data: dict, okd_data: dict) -> None:
    """
    Render L7 Application layer page.

    Args:
        traefik_data: Output of l7_traefik.collect()
        istio_data:   Output of l7_istio.collect()
        okd_data:     Output of l7_okd_routes.collect()

    Writes to architecture-vault/07-L7-application/INDEX.md and tls-termination.d2.
    """
    context = _build_l7_context(traefik_data, istio_data, okd_data)

    md_content = _render_md(context)
    d2_content = _render_d2(context)

    if _dry_run():
        print("=== L7 INDEX.md ===")
        print(md_content)
        print("=== L7 tls-termination.d2 ===")
        print(d2_content)
        return

    from overwatch_gen.lib.render import write_deterministic

    _L7_DIR.mkdir(parents=True, exist_ok=True)
    md_path = _L7_DIR / "INDEX.md"
    d2_path = _L7_DIR / "tls-termination.d2"
    svg_path = _L7_DIR / "tls-termination.svg"

    wrote_md = write_deterministic(md_path, md_content)
    wrote_d2 = write_deterministic(d2_path, d2_content)

    if wrote_md:
        logger.info("L7: wrote %s", md_path)
    if wrote_d2:
        logger.info("L7: wrote %s", d2_path)
        _run_d2(d2_path, svg_path)
