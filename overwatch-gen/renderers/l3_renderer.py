"""
l3_renderer.py — L3 Network layer renderer.

Renders NetBox prefixes + Unifi firewall data into:
  architecture-vault/03-L3-network/INDEX.md    (Markdown table)
  architecture-vault/03-L3-network/network-topology.d2  (D2 diagram source)

render_netbox(data)   — called by l3_netbox collector layer
render_firewall(data) — called by l3_unifi_firewall collector layer

Both write to the same INDEX.md. When run independently, each produces
a partial view (firewall section empty for netbox run, prefix section
empty for firewall run). When both layers are run together (--all), the
last writer wins; use layer l3_combined if you want merged output.

Template: overwatch-gen/templates/l3_network.md.j2
          overwatch-gen/templates/l3.d2.j2
"""

import logging
import os
import subprocess
from pathlib import Path

logger = logging.getLogger(__name__)

_TEMPLATE_DIR = Path(__file__).parent.parent / "templates"
_VAULT_DIR = Path(__file__).parent.parent.parent / "architecture-vault"
_L3_DIR = _VAULT_DIR / "03-L3-network"

_MD_TEMPLATE = _TEMPLATE_DIR / "l3_network.md.j2"
_D2_TEMPLATE = _TEMPLATE_DIR / "l3.d2.j2"


def _dry_run() -> bool:
    return os.environ.get("OVERWATCH_GEN_DRY_RUN", "").lower() in ("1", "true", "yes")


def _run_d2(d2_path: Path, svg_path: Path) -> None:
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
        logger.warning("d2 binary not found — install d2 for SVG output. Skipping %s", svg_path)
    except subprocess.TimeoutExpired:
        logger.warning("d2 timed out rendering %s", d2_path)


def _write_output(context: dict) -> None:
    from overwatch_gen.lib.render import render_markdown, write_deterministic

    md_content = render_markdown(_MD_TEMPLATE, context)
    d2_content = render_markdown(_D2_TEMPLATE, context)

    if _dry_run():
        print("=== L3 Network INDEX.md ===")
        print(md_content)
        print("=== L3 network-topology.d2 ===")
        print(d2_content)
        return

    _L3_DIR.mkdir(parents=True, exist_ok=True)
    write_deterministic(_L3_DIR / "INDEX.md", md_content)

    d2_path = _L3_DIR / "network-topology.d2"
    write_deterministic(d2_path, d2_content)

    svg_dir = _VAULT_DIR / "99-diagrams-svg"
    svg_dir.mkdir(parents=True, exist_ok=True)
    _run_d2(d2_path, svg_dir / "l3-network-topology.svg")
    logger.info("L3 render complete: %s", _L3_DIR / "INDEX.md")


def render_combined(netbox: dict, unifi: dict) -> None:
    """
    Render L3 network layer combining NetBox and Unifi firewall data.

    netbox keys expected: prefixes, ip_addresses (may be empty dict if layer not run)
    unifi keys expected:  firewall_policies, firewall_zones (may be empty dict if not run)

    Writes INDEX.md and network-topology.d2 ONCE with all fields populated.
    This is the authoritative write — called by the l3_unifi_firewall orchestrator.
    """
    context = {
        "firewall_policies": unifi.get("firewall_policies", []),
        "firewall_zones": unifi.get("firewall_zones", []),
        "ip_addresses": netbox.get("ip_addresses", []),
        "prefixes": netbox.get("prefixes", []),
    }
    _write_output(context)


def render_netbox(data: dict) -> None:
    """
    Render L3 NetBox data (standalone, backwards-compatible wrapper).

    data keys expected: prefixes, ip_addresses
    Calls render_combined with empty firewall side.
    """
    render_combined(netbox=data, unifi={})


def render_firewall(data: dict) -> None:
    """
    Render L3 Unifi firewall data (standalone, backwards-compatible wrapper).

    data keys expected: firewall_policies, firewall_zones
    Calls render_combined with empty netbox side.
    """
    render_combined(netbox={}, unifi=data)
