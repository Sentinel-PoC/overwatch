"""Rules-only signal triage — no LLM dependency.

Deterministic pattern matching for known infrastructure problems.
Handles common cases, escalates everything else.
"""

from models import Signal, SignalSource, Tier


# Wazuh Indexer rule groups that are informational / low-risk
# These fire frequently and don't need immediate action
_SKIP_RULE_GROUPS = {
    "syslog", "pam", "local_syslog", "sshd",
    "authentication_success", "gdpr_IV_32.2",
}

# Wazuh rules that indicate brute force / auth failure (Tier 2: block via CrowdSec)
_BRUTE_FORCE_RULE_IDS = {
    "5710", "5503", "5551", "5712", "5720",  # SSH brute force variants
    "60204",  # web auth brute force
    "5758",   # multiple auth failures
    "5763",   # multiple authentication failures
}

# Wazuh rules that indicate potential intrusion (always escalate)
_INTRUSION_RULE_IDS = {
    "510",    # host-based anomaly detection
    "550",    # integrity check failure
    "551",    # integrity check modified file
    "552",    # integrity check new file
    "553",    # integrity check deleted file
    "554",    # integrity check: file permission/owner changed
    "100002", # Suricata alert
    "100003", # Suricata alert (high)
    "87101",  # vulnerability detected (critical)
    "87104",  # vulnerability detected (high)
    "92652",  # Docker container started
    "92657",  # Docker: new image pulled
}


def rules_only_diagnosis(signal: Signal) -> Tier:
    """Classify signal using deterministic pattern matching.

    This is the fallback when Gemini/LLM is unreachable. It handles
    the common cases and escalates anything it can't classify.
    """
    summary_lower = signal.summary.lower()
    raw = signal.raw_data

    # ArgoCD signals
    if signal.source == SignalSource.ARGOCD:
        health = raw.get("health_status", "").lower()
        sync = raw.get("sync_status", "").lower()
        sync_error = raw.get("sync_error", False)

        # OutOfSync with no error → ArgoCD auto-sync handles it
        if sync == "outofsync" and not sync_error:
            return Tier.SKIP

        # Degraded or Missing → operational fix
        if health in ("degraded", "missing"):
            return Tier.OPERATIONAL

        # SyncFailed → might need manifest fix
        if sync_error:
            return Tier.GIT_CHANGE

    # Wazuh signals (Manager API + Indexer alerts)
    if signal.source == SignalSource.WAZUH:
        rule_id = str(raw.get("rule_id", ""))
        rule_groups = set(raw.get("rule_groups", []))
        is_indexer = raw.get("source_type") == "indexer"

        # Agent disconnected (Manager API signals)
        if rule_id in ("502", "503", "504"):
            return Tier.OPERATIONAL

        # --- Indexer alert classification ---

        # OPS-986: Kernel OOM (rule 5108) — cgroup-contained vs host-level.
        # wazuh.py annotates the signal with is_cgroup_oom after parsing the
        # full_log for constraint=CONSTRAINT_MEMCG / oom_memcg= markers.
        if rule_id == "5108":
            if raw.get("is_cgroup_oom"):
                # OOM was contained within a cgroup/LXC — no host action needed.
                # The host node had free memory; this is a container resource
                # limit event, observable but not operationally urgent.
                return Tier.SKIP
            # constraint=CONSTRAINT_NONE or ambiguous — treat as host OOM.
            # Physical node is genuinely memory-starved; escalate immediately.
            return Tier.ESCALATE

        # Known intrusion indicators → always escalate
        if rule_id in _INTRUSION_RULE_IDS:
            return Tier.ESCALATE

        # Brute force / repeated auth failures → Tier 2 (CrowdSec handles)
        if rule_id in _BRUTE_FORCE_RULE_IDS:
            return Tier.OPERATIONAL

        # File integrity monitoring (FIM) changes
        if "syscheck" in rule_groups or "ossec" in rule_groups:
            if signal.severity >= 10:
                return Tier.ESCALATE  # high-sev FIM = potential compromise
            return Tier.SKIP  # low-sev FIM = normal config changes

        # Vulnerability detection alerts
        if "vulnerability-detector" in rule_groups:
            if signal.severity >= 12:
                return Tier.ESCALATE  # critical vuln
            return Tier.SKIP  # informational vuln

        # Informational rule groups → skip
        if rule_groups and rule_groups.issubset(_SKIP_RULE_GROUPS):
            return Tier.SKIP

        # High severity security alerts → escalate
        if signal.severity >= 12:
            return Tier.ESCALATE

        # Medium severity from Indexer with no known pattern → let LLM triage
        if is_indexer and signal.severity >= 8:
            return Tier.ESCALATE

    # Kubernetes patterns (from Plane issues or ArgoCD detail)
    if "crashloopbackoff" in summary_lower:
        return Tier.OPERATIONAL
    if "imagepullbackoff" in summary_lower:
        return Tier.OPERATIONAL
    if "oomkilled" in summary_lower:
        return Tier.GIT_CHANGE  # likely needs resource limit change
    if "pending" in summary_lower and "pvc" in summary_lower:
        return Tier.ESCALATE  # PVC issues need human review

    # Vault sealed
    if "vault" in summary_lower and "sealed" in summary_lower:
        return Tier.OPERATIONAL

    # Default: can't classify → escalate
    return Tier.ESCALATE
