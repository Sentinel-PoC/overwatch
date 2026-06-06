"""
l4_ufw.py — UFW (Uncomplicated Firewall) host rules collector.

Live mode: NOT IMPLEMENTED — UFW data requires SSH access to each host and
ansible-gathered facts.  Live mode raises NotImplementedError and logs a
clear message directing operator to use fixture mode.

Fixture mode: ARCH_AUDIT_USE_FIXTURES=1 loads fixtures/ufw_sample.json.

Returns a dict with key:
  hosts   list  — per-host UFW rule tables, each with:
    hostname  str   — host FQDN or short name
    status    str   — "active" | "inactive"
    rules     list  — rule records sorted by rule_num:
      rule_num  int    — UFW rule number
      action    str    — e.g. "ALLOW IN", "DENY IN"
      from      str    — source address/range/Anywhere
      to        str    — destination address/Anywhere
      protocol  str    — tcp / udp / any
      port      str    — port number or "" if not specified
      comment   str    — UFW rule comment

Registers layer "l4_ufw".
"""

import json
import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)

_FIXTURE_SAMPLE = (
    Path(__file__).parent.parent / "fixtures" / "ufw_sample.json"
)


def _use_fixtures() -> bool:
    return os.environ.get("ARCH_AUDIT_USE_FIXTURES", "").lower() in ("1", "true", "yes")


def _load_fixture() -> dict:
    if not _FIXTURE_SAMPLE.exists():
        raise FileNotFoundError(
            f"UFW fixture not found: {_FIXTURE_SAMPLE}."
        )
    with open(_FIXTURE_SAMPLE, encoding="utf-8") as fh:
        return json.load(fh)


def _normalize_rule(rule: dict) -> dict:
    """Normalize a single UFW rule record."""
    return {
        "action": rule.get("action", ""),
        "comment": rule.get("comment", ""),
        "from": rule.get("from", "Anywhere"),
        "port": str(rule.get("port", "")),
        "protocol": rule.get("protocol", "any"),
        "rule_num": int(rule.get("rule_num", 0)),
        "to": rule.get("to", "Anywhere"),
    }


def _normalize(raw: dict) -> dict:
    """Normalize raw fixture data into stable sorted structure."""
    hosts = []
    for host in raw.get("hosts", []):
        rules = sorted(
            [_normalize_rule(r) for r in host.get("rules", [])],
            key=lambda r: r["rule_num"],
        )
        hosts.append({
            "hostname": host.get("hostname", ""),
            "rules": rules,
            "status": host.get("status", "unknown"),
        })

    hosts = sorted(hosts, key=lambda h: h["hostname"])
    return {"hosts": hosts}


def collect() -> dict:
    """
    Collect UFW host firewall rules.

    Live mode is not implemented — use ARCH_AUDIT_USE_FIXTURES=1.

    Returns dict with key: hosts (list of per-host rule tables).
    Sorted by hostname for deterministic output.
    """
    if _use_fixtures():
        logger.info("UFW: loading fixture from %s", _FIXTURE_SAMPLE)
        raw = _load_fixture()
        return _normalize(raw)

    raise NotImplementedError(
        "UFW live collection requires SSH + ansible facts. "
        "Set ARCH_AUDIT_USE_FIXTURES=1 to use fixture data. "
        "To add live support, implement SSH-based 'ufw status numbered' parsing "
        "or consume ansible gathered_facts from inventory."
    )


# --- Registry wiring ---
from overwatch_gen.lib import registry  # noqa: E402
from overwatch_gen.renderers import l4_renderer  # noqa: E402

registry.register_layer("l4_ufw", collect, l4_renderer.render_ufw)
