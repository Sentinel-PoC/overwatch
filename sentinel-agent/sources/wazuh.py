"""Poll Wazuh Manager API + Wazuh Indexer for infrastructure signals.

Two data sources:

1. **Manager API** (port 55000): Agent health, daemon status.
   - Disconnected agents → restart them (Tier 2)
   - Stopped daemons → escalate

2. **Indexer API** (port 9200, OpenSearch): Real security alerts.
   - High-severity alerts (rule.level >= threshold) → triage via rules/LLM
   - Without Indexer polling, sentinel-agent is blind to actual attacks.

The Indexer uses basic auth (admin:password from Vault).
The Manager uses JWT token auth.
"""

import logging
import re
from datetime import datetime, timezone, timedelta

import requests
import urllib3

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from models import Signal, SignalSource


def poll_wazuh(config: dict, secrets: dict, log: logging.Logger) -> list[Signal]:
    """Poll Wazuh Manager API + Indexer for health and alert signals."""
    signals = []

    # --- Manager API: agent health + daemon status ---
    signals.extend(_poll_manager(config, secrets, log))

    # --- Indexer API: real security alerts ---
    signals.extend(_poll_indexer(config, secrets, log))

    log.info(f"Wazuh: found {len(signals)} total signals "
             f"(manager + indexer)")
    return signals


def _poll_manager(config: dict, secrets: dict,
                  log: logging.Logger) -> list[Signal]:
    """Poll Wazuh Manager API for agent health issues."""
    wazuh_cfg = config["wazuh"]
    api_url = wazuh_cfg["api_url"]
    api_user = wazuh_cfg["api_user"]
    api_password = secrets.get("wazuh_password")

    if not api_password:
        log.warning("No Wazuh password — skipping Wazuh Manager poll")
        return []

    # Authenticate
    try:
        auth_resp = requests.post(
            f"{api_url}/security/user/authenticate",
            auth=(api_user, api_password),
            timeout=15,
            verify=False,
        )
        auth_resp.raise_for_status()
        token = auth_resp.json().get("data", {}).get("token")
        if not token:
            log.error("Wazuh auth returned no token")
            return []
    except (requests.ConnectionError, requests.Timeout) as e:
        log.warning(f"Wazuh Manager unreachable: {e}")
        return []
    except requests.HTTPError as e:
        log.error(f"Wazuh Manager auth failed: {e}")
        return []

    headers = {"Authorization": f"Bearer {token}"}
    signals = []

    # Check for disconnected agents
    signals.extend(_check_agent_health(api_url, headers, log))

    # Check manager status
    expected_stopped = set(wazuh_cfg.get("expected_stopped_daemons", []))
    signals.extend(_check_manager_health(api_url, headers, log, expected_stopped))

    log.info(f"Wazuh Manager: found {len(signals)} issues")
    return signals


def _check_agent_health(api_url: str, headers: dict,
                        log: logging.Logger) -> list[Signal]:
    """Find disconnected or never-connected agents."""
    signals = []
    try:
        resp = requests.get(
            f"{api_url}/agents",
            headers=headers,
            params={"limit": 100, "select": "id,name,status,lastKeepAlive,ip"},
            timeout=15,
            verify=False,
        )
        resp.raise_for_status()
    except Exception as e:
        log.warning(f"Wazuh agents endpoint failed: {e}")
        return []

    agents = resp.json().get("data", {}).get("affected_items", [])

    for agent in agents:
        agent_id = agent.get("id", "000")
        if agent_id == "000":
            continue  # Skip manager itself

        status = agent.get("status", "").lower()
        name = agent.get("name", "unknown")

        if status == "disconnected":
            signals.append(Signal(
                source=SignalSource.WAZUH,
                source_id=f"wazuh-agent-{agent_id}",
                summary=f"Wazuh agent {name} (ID {agent_id}) is disconnected",
                severity=8,
                raw_data={
                    "rule_id": "502",  # maps to triage rules
                    "agent_id": agent_id,
                    "agent_name": name,
                    "agent_status": status,
                    "last_keepalive": agent.get("lastKeepAlive", ""),
                    "ip": agent.get("ip", ""),
                },
            ))
        elif status == "never_connected":
            signals.append(Signal(
                source=SignalSource.WAZUH,
                source_id=f"wazuh-agent-{agent_id}",
                summary=f"Wazuh agent {name} (ID {agent_id}) never connected",
                severity=6,
                raw_data={
                    "rule_id": "504",
                    "agent_id": agent_id,
                    "agent_name": name,
                    "agent_status": status,
                },
            ))

    return signals


def _check_manager_health(api_url: str, headers: dict,
                          log: logging.Logger,
                          expected_stopped: set = None) -> list[Signal]:
    """Check Wazuh manager status."""
    if expected_stopped is None:
        expected_stopped = set()

    signals = []
    try:
        resp = requests.get(
            f"{api_url}/manager/status",
            headers=headers,
            timeout=15,
            verify=False,
        )
        resp.raise_for_status()
    except Exception as e:
        log.warning(f"Wazuh manager status failed: {e}")
        return [Signal(
            source=SignalSource.WAZUH,
            source_id="wazuh-manager",
            summary="Wazuh manager status endpoint unreachable",
            severity=12,
            raw_data={"error": str(e)},
        )]

    daemons = resp.json().get("data", {}).get("affected_items", [{}])[0]
    # Filter out expected stopped daemons and wazuh-clusterd (single-node)
    always_ignore = {"wazuh-clusterd"}
    stopped = [name for name, status in daemons.items()
               if status == "stopped"
               and name not in always_ignore
               and name not in expected_stopped]

    if stopped:
        signals.append(Signal(
            source=SignalSource.WAZUH,
            source_id="wazuh-manager-daemons",
            summary=f"Wazuh manager has stopped daemons: {', '.join(stopped)}",
            severity=10,
            raw_data={
                "stopped_daemons": stopped,
                "all_daemons": daemons,
            },
        ))

    return signals


# ---------------------------------------------------------------------------
# Kernel OOM classification (OPS-986)
# ---------------------------------------------------------------------------

def _classify_kernel_oom(full_log: str) -> tuple[bool, str]:
    """Distinguish cgroup-contained OOM from host-level OOM.

    Parses a kernel log line associated with Wazuh rule 5108.

    Returns:
        (is_cgroup_oom, cgroup_path)
        is_cgroup_oom -- True if the OOM is contained within a cgroup/LXC
        cgroup_path   -- value of oom_memcg= (empty string if not found)

    Cgroup OOM indicators
    ---------------------
    - ``constraint=CONSTRAINT_MEMCG`` — explicit cgroup constraint in the
      oom-kill kernel line
    - ``Memory cgroup out of memory:`` — cgroup kill from memory.oom_group
    - ``oom_memcg=`` present anywhere in the line

    Host OOM indicators
    -------------------
    - ``constraint=CONSTRAINT_NONE`` — kernel exhausted global memory
    - No cgroup markers present

    When the log is ambiguous, the function returns ``False`` (i.e., keeps
    the high-priority escalation path) to avoid silently dropping a real
    host-level OOM event.
    """
    if "constraint=CONSTRAINT_MEMCG" in full_log or "oom_memcg=" in full_log:
        m = re.search(r"oom_memcg=([^,\s]+)", full_log)
        cgroup_path = m.group(1).rstrip(",") if m else ""
        return True, cgroup_path

    if "Memory cgroup out of memory" in full_log:
        m = re.search(r"oom_memcg=([^,\s]+)", full_log)
        cgroup_path = m.group(1).rstrip(",") if m else ""
        return True, cgroup_path

    # constraint=CONSTRAINT_NONE or no cgroup marker → treat as host OOM
    return False, ""


# ---------------------------------------------------------------------------
# Wazuh Indexer (OpenSearch) — real security alerts
# ---------------------------------------------------------------------------

def _poll_indexer(config: dict, secrets: dict,
                  log: logging.Logger) -> list[Signal]:
    """Poll Wazuh Indexer (OpenSearch) for recent high-severity alerts.

    The Indexer stores all Wazuh alerts in daily indices matching
    wazuh-alerts-*. We query for alerts from the last cycle interval
    (default 5 minutes) with rule.level >= severity_threshold.

    Deduplication: alerts are keyed by rule.id + agent.name to avoid
    generating multiple signals for the same recurring alert within
    one cycle.
    """
    indexer_cfg = config.get("wazuh_indexer", {})
    indexer_url = indexer_cfg.get("api_url", "")

    if not indexer_url:
        log.info("Wazuh Indexer not configured — skipping alert polling")
        return []

    # Credentials: separate from Manager API
    indexer_user = indexer_cfg.get("api_user", "admin")
    indexer_password = secrets.get("wazuh_indexer_password")

    if not indexer_password:
        # Fall back to Manager password if Indexer password not separate
        indexer_password = secrets.get("wazuh_password")

    if not indexer_password:
        log.warning("No Wazuh Indexer password — skipping Indexer poll")
        return []

    wazuh_cfg = config["wazuh"]
    severity_threshold = wazuh_cfg.get("severity_threshold", 8)
    max_alerts = wazuh_cfg.get("max_alerts", 50)

    # Time window: last cycle interval (default 5 min)
    cycle_sec = config.get("agent", {}).get("cycle_interval_sec", 300)
    now = datetime.now(timezone.utc)
    time_from = (now - timedelta(seconds=cycle_sec)).isoformat()

    # Index pattern — Wazuh uses daily indices
    index_pattern = indexer_cfg.get("index_pattern", "wazuh-alerts-*")

    # Excluded rule IDs — rules that generate noise and aren't actionable
    excluded_rule_ids = set(str(r) for r in indexer_cfg.get(
        "excluded_rule_ids", []))

    # Build OpenSearch query
    query = {
        "size": max_alerts,
        "sort": [{"timestamp": {"order": "desc"}}],
        "query": {
            "bool": {
                "must": [
                    {"range": {"rule.level": {"gte": severity_threshold}}},
                    {"range": {"timestamp": {"gte": time_from}}},
                ],
            },
        },
        "_source": [
            "rule.id", "rule.level", "rule.description", "rule.groups",
            "agent.id", "agent.name", "agent.ip",
            "data", "timestamp", "full_log",
        ],
    }

    # Add exclusion filter if we have excluded rules
    if excluded_rule_ids:
        query["query"]["bool"]["must_not"] = [
            {"terms": {"rule.id": list(excluded_rule_ids)}}
        ]

    try:
        resp = requests.post(
            f"{indexer_url}/{index_pattern}/_search",
            auth=(indexer_user, indexer_password),
            json=query,
            timeout=15,
            verify=False,
        )
        resp.raise_for_status()
    except (requests.ConnectionError, requests.Timeout) as e:
        log.warning(f"Wazuh Indexer unreachable: {e}")
        return []
    except requests.HTTPError as e:
        log.error(f"Wazuh Indexer query failed: {e}")
        return []

    data = resp.json()
    hits = data.get("hits", {}).get("hits", [])
    total = data.get("hits", {}).get("total", {})
    total_count = total.get("value", 0) if isinstance(total, dict) else total

    log.info(f"Wazuh Indexer: {len(hits)} alerts returned "
             f"(total matching: {total_count}, "
             f"threshold: level>={severity_threshold})")

    if not hits:
        return []

    # Deduplicate by rule_id + agent_name within this cycle
    seen: dict[str, dict] = {}  # dedup_key → best (highest level) hit

    for hit in hits:
        source = hit.get("_source", {})
        rule = source.get("rule", {})
        agent = source.get("agent", {})

        rule_id = str(rule.get("id", "0"))
        rule_level = int(rule.get("level", 0))
        rule_desc = rule.get("description", "")
        rule_groups = rule.get("groups", [])

        agent_id = str(agent.get("id", "000"))
        agent_name = agent.get("name", "unknown")

        # Dedup key: same rule on same agent = one signal
        dedup_key = f"{rule_id}:{agent_name}"

        if dedup_key in seen:
            # Keep the highest severity instance
            if rule_level <= seen[dedup_key]["rule_level"]:
                continue

        seen[dedup_key] = {
            "rule_id": rule_id,
            "rule_level": rule_level,
            "rule_description": rule_desc,
            "rule_groups": rule_groups,
            "agent_id": agent_id,
            "agent_name": agent_name,
            "agent_ip": agent.get("ip", ""),
            "timestamp": source.get("timestamp", ""),
            "full_log": (source.get("full_log", "") or "")[:500],
            "data": source.get("data", {}),
        }

    # Convert to signals
    signals = []
    for dedup_key, alert in seen.items():
        rule_id = alert["rule_id"]
        rule_level = alert["rule_level"]
        agent_name = alert["agent_name"]
        rule_desc = alert["rule_description"]

        summary = (f"Wazuh alert: [{rule_id}] {rule_desc} "
                   f"(level {rule_level}, agent: {agent_name})")

        # OPS-986: Kernel OOM rule — distinguish cgroup-contained from host-level.
        # rule 5108 fires on both; cgroup OOM is benign (LXC/container limit hit),
        # host OOM means the physical node is genuinely memory-starved.
        oom_meta: dict = {}
        if rule_id == "5108":
            is_cgroup_oom, cgroup_path = _classify_kernel_oom(
                alert["full_log"]
            )
            if is_cgroup_oom:
                rule_level = 4  # downgrade: contained OOM is low-severity
                cgroup_label = f", cgroup: {cgroup_path}" if cgroup_path else ""
                summary = (
                    f"Wazuh alert: [{rule_id}] cgroup OOM (contained) — "
                    f"agent: {agent_name}{cgroup_label}"
                )
                oom_meta = {
                    "is_cgroup_oom": True,
                    "oom_cgroup_path": cgroup_path,
                    "categories": ["cat-observability"],
                }
            else:
                oom_meta = {"is_cgroup_oom": False}

        signals.append(Signal(
            source=SignalSource.WAZUH,
            source_id=f"wazuh-alert-{rule_id}-{agent_name}",
            summary=summary,
            severity=rule_level,
            raw_data={
                "rule_id": rule_id,
                "rule_level": rule_level,
                "rule_description": rule_desc,
                "rule_groups": alert["rule_groups"],
                "agent_id": alert["agent_id"],
                "agent_name": agent_name,
                "agent_ip": alert["agent_ip"],
                "timestamp": alert["timestamp"],
                "full_log": alert["full_log"],
                "data": alert["data"],
                "source_type": "indexer",
                **oom_meta,
            },
        ))

    log.info(f"Wazuh Indexer: {len(signals)} unique alert signals "
             f"(from {len(hits)} raw alerts)")
    return signals
