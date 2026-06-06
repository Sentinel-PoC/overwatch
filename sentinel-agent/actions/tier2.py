"""Tier 2 operational actions — autonomous fixes, no code changes.

Authority (from architecture doc Section 4.4):
  Autonomous (no approval):
    - Restart pods (max 3 per pod per cycle)
    - Force-sync ArgoCD apps with no SyncError
    - Restart Wazuh agents
    - Unseal Vault
    - Scale deployments to declared replica count
    - Clear old Jobs/CronJobs

  With notification (proceed after 15 min if no response):
    - Restart dnsmasq/haproxy/squid on iac-control
    - Delete stuck Terminating pods

  NEVER:
    - Delete PVCs, PVs, namespaces, CRDs
    - Modify Kyverno, Vault policies, firewall rules
    - Disable security tooling
"""

import logging
import subprocess
from typing import Optional

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from models import Signal, SignalSource, ActionResult
from operator_activity import recent_operator_activity
from research import emit_research_event


def execute_tier2(signal: Signal, config: dict, secrets: dict,
                  log: logging.Logger) -> ActionResult:
    """Execute a Tier 2 operational fix based on signal type.

    Returns None-equivalent ActionResult (success=False, action_taken='change_freeze')
    when operator activity is detected within the configured window.  Caller
    should treat this as a skip, not an error.
    """
    # --- Change-freeze guard (OPS-234) ---
    freeze_cfg = config.get("change_freeze", {})
    window = freeze_cfg.get("change_freeze_seconds", 900)  # 15 min default
    is_active, evidence = recent_operator_activity(window_seconds=window, log=log)
    if is_active:
        log.warning(
            f"CHANGE FREEZE — Tier 2 skipped for '{signal.summary}': "
            f"operator activity {evidence.get('seconds_ago', '?')}s ago "
            f"(window={window}s)"
        )
        emit_research_event(
            config,
            "tier2_change_freeze_skip",
            {
                "signal": signal.summary,
                "reason": "change_freeze_operator_active",
                "last_activity_at_utc": evidence.get("last_activity_at_utc"),
                "seconds_ago": evidence.get("seconds_ago"),
                "window_seconds": window,
                "source": evidence.get("source"),
            },
            signal.plane_issue_id,
            narrative=(
                f"Tier 2 frozen: operator active {evidence.get('seconds_ago')}s ago. "
                f"Will retry next cycle."
            ),
            log=log,
        )
        return ActionResult(
            signal=signal,
            action_taken="change_freeze",
            success=False,
            error=(
                f"Tier 2 frozen: operator active {evidence.get('seconds_ago')}s ago "
                f"(window={window}s, source={evidence.get('source')})"
            ),
        )

    summary_lower = signal.summary.lower()
    raw = signal.raw_data

    # Route to specific handler
    if signal.source == SignalSource.ARGOCD:
        return _handle_argocd(signal, config, secrets, log)

    if signal.source == SignalSource.WAZUH:
        return _handle_wazuh(signal, config, secrets, log)

    if "crashloopbackoff" in summary_lower:
        return _restart_pod(signal, config, log)

    if "imagepullbackoff" in summary_lower:
        return _restart_pod(signal, config, log)

    if "vault" in summary_lower and "sealed" in summary_lower:
        return _unseal_vault(signal, config, secrets, log)

    if "terminating" in summary_lower:
        return _force_delete_pod(signal, config, log)

    # Generic: try pod restart if we have pod info
    if raw.get("pod_name") and raw.get("namespace"):
        return _restart_pod(signal, config, log)

    return ActionResult(
        signal=signal,
        action_taken="none",
        success=False,
        error=f"No Tier 2 handler for signal: {signal.summary}",
    )


def _run_kubectl(args: list[str], config: dict,
                 log: logging.Logger) -> tuple[bool, str]:
    """Run a kubectl/oc command, return (success, output)."""
    kubeconfig = config.get("kubernetes", {}).get("kubeconfig", "")
    cmd = ["oc"] + args
    if kubeconfig:
        cmd = ["oc", f"--kubeconfig={kubeconfig}"] + args

    log.info(f"Executing: {' '.join(cmd)}")
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=60,
        )
        output = result.stdout + result.stderr
        if result.returncode == 0:
            return True, output.strip()
        else:
            return False, output.strip()
    except subprocess.TimeoutExpired:
        return False, "Command timed out after 60s"
    except FileNotFoundError:
        return False, "oc/kubectl not found"


def _restart_pod(signal: Signal, config: dict,
                 log: logging.Logger) -> ActionResult:
    """Delete a pod to trigger restart by its controller."""
    raw = signal.raw_data
    namespace = raw.get("namespace", "")
    pod_name = raw.get("pod_name", "")

    # Try to extract from ArgoCD or Plane data if not directly available
    if not pod_name and "app_name" in raw:
        # For ArgoCD signals, we need to find the failing pod
        app_name = raw["app_name"]
        namespace = raw.get("namespace", app_name)
        success, output = _run_kubectl(
            ["get", "pods", "-n", namespace, "--field-selector=status.phase!=Running",
             "-o", "jsonpath={.items[0].metadata.name}"],
            config, log
        )
        if success and output:
            pod_name = output
        else:
            return ActionResult(
                signal=signal,
                action_taken="find-failing-pod",
                success=False,
                error=f"Could not find failing pod in {namespace}: {output}",
            )

    if not pod_name or not namespace:
        return ActionResult(
            signal=signal,
            action_taken="restart-pod",
            success=False,
            error="Missing pod_name or namespace in signal data",
        )

    success, output = _run_kubectl(
        ["delete", "pod", pod_name, "-n", namespace],
        config, log
    )

    return ActionResult(
        signal=signal,
        action_taken=f"restart-pod {namespace}/{pod_name}",
        success=success,
        evidence=output if success else "",
        error=output if not success else "",
    )


def _handle_argocd(signal: Signal, config: dict, secrets: dict,
                   log: logging.Logger) -> ActionResult:
    """Handle ArgoCD signals — force sync or restart pods."""
    import requests

    raw = signal.raw_data
    app_name = raw.get("app_name", "")
    health_status = raw.get("health_status", "").lower()
    sync_error = raw.get("sync_error", False)

    if not app_name:
        return ActionResult(
            signal=signal, action_taken="none", success=False,
            error="No app_name in ArgoCD signal",
        )

    # If Degraded/Missing/stuck Progressing without sync error → try force sync
    is_stuck = raw.get("stuck", False)
    if (health_status in ("degraded", "missing", "progressing") and not sync_error
            and (health_status != "progressing" or is_stuck)):
        argocd_cfg = config["argocd"]
        token = secrets.get("argocd_token")
        if not token:
            # Try session auth
            from sources.argocd import _get_argocd_session_token
            token = _get_argocd_session_token(argocd_cfg, secrets, log)
        if not token:
            return ActionResult(
                signal=signal, action_taken="argocd-sync", success=False,
                error="No ArgoCD token available",
            )

        try:
            resp = requests.post(
                f"{argocd_cfg['api_url']}/applications/{app_name}/sync",
                headers={"Authorization": f"Bearer {token}"},
                json={"prune": False},
                timeout=30,
                verify=False,
            )
            resp.raise_for_status()
            return ActionResult(
                signal=signal,
                action_taken=f"argocd-sync {app_name}",
                success=True,
                evidence=f"Sync triggered for {app_name}",
            )
        except Exception as e:
            return ActionResult(
                signal=signal,
                action_taken=f"argocd-sync {app_name}",
                success=False,
                error=str(e),
            )

    # Fallback: try restarting pods in the app's namespace
    return _restart_pod(signal, config, log)


def _handle_wazuh(signal: Signal, config: dict, secrets: dict,
                  log: logging.Logger) -> ActionResult:
    """Handle Wazuh signals — restart agents, acknowledge alerts."""
    raw = signal.raw_data
    rule_id = str(raw.get("rule_id", ""))
    agent_name = raw.get("agent_name", "")

    # Agent disconnected — restart via SSH
    if rule_id in ("502", "503", "504"):
        # Map agent name to SSH target
        # This would use the environment reference for SSH access
        log.info(f"Wazuh agent {agent_name} disconnected — restart needed")
        return ActionResult(
            signal=signal,
            action_taken=f"wazuh-restart-agent {agent_name}",
            success=False,
            error="SSH-based Wazuh agent restart not yet implemented",
        )

    # For other Wazuh alerts, just log and return
    return ActionResult(
        signal=signal,
        action_taken=f"wazuh-alert-logged rule={rule_id}",
        success=True,
        evidence=f"Alert logged: {signal.summary}",
    )


def _unseal_vault(signal: Signal, config: dict, secrets: dict,
                  log: logging.Logger) -> ActionResult:
    """Attempt to unseal Vault."""
    import requests

    vault_addr = config["vault"]["addr"]

    # Check current seal status
    try:
        resp = requests.get(
            f"{vault_addr}/v1/sys/seal-status",
            timeout=10,
            verify=False,
        )
        resp.raise_for_status()
        status = resp.json()
        if not status.get("sealed", True):
            return ActionResult(
                signal=signal,
                action_taken="vault-check-seal",
                success=True,
                evidence="Vault is already unsealed",
            )
    except Exception as e:
        return ActionResult(
            signal=signal,
            action_taken="vault-check-seal",
            success=False,
            error=f"Cannot reach Vault: {e}",
        )

    # Vault is sealed — we need unseal keys from Vault (chicken-and-egg)
    # This requires the transit auto-unseal or manual intervention
    return ActionResult(
        signal=signal,
        action_taken="vault-unseal",
        success=False,
        error="Vault is sealed — requires manual unseal or transit auto-unseal. "
              "Escalating to operator.",
    )


def _force_delete_pod(signal: Signal, config: dict,
                      log: logging.Logger) -> ActionResult:
    """Force-delete a stuck Terminating pod."""
    raw = signal.raw_data
    namespace = raw.get("namespace", "")
    pod_name = raw.get("pod_name", "")

    if not pod_name or not namespace:
        return ActionResult(
            signal=signal, action_taken="force-delete-pod", success=False,
            error="Missing pod_name or namespace",
        )

    success, output = _run_kubectl(
        ["delete", "pod", pod_name, "-n", namespace,
         "--grace-period=0", "--force"],
        config, log
    )

    return ActionResult(
        signal=signal,
        action_taken=f"force-delete-pod {namespace}/{pod_name}",
        success=success,
        evidence=output if success else "",
        error=output if not success else "",
    )
