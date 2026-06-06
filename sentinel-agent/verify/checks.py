"""Post-action verification checks.

After every Tier 2/3 action, verify the fix actually worked.
The agent that fixes something is never the one that marks it
complete (JUDGE does that), but we still verify locally to
detect immediate failures.
"""

import logging
import subprocess
import time

import requests

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from models import ActionResult


def verify_action(result: ActionResult, config: dict, secrets: dict,
                  log: logging.Logger) -> bool:
    """Verify that an action's effect is visible.

    Returns True if the fix appears to have worked.
    """
    action = result.action_taken

    if action.startswith("restart-pod"):
        return _verify_pod_restart(result, config, log)

    if action.startswith("argocd-sync"):
        return _verify_argocd_sync(result, config, secrets, log)

    if action.startswith("force-delete-pod"):
        return _verify_pod_deleted(result, config, log)

    if action.startswith("vault-"):
        return _verify_vault_unsealed(config, log)

    if action.startswith("tier3-mr-created"):
        return True  # MR existence is the verification

    # Unknown action type — can't verify
    log.warning(f"No verification check for action: {action}")
    return False


def _verify_pod_restart(result: ActionResult, config: dict,
                        log: logging.Logger) -> bool:
    """Check that pod is Running after restart. Waits up to 60s."""
    raw = result.signal.raw_data
    namespace = raw.get("namespace", "")
    pod_prefix = raw.get("pod_name", "").rsplit("-", 1)[0]  # deployment prefix

    if not namespace or not pod_prefix:
        return False

    kubeconfig = config.get("kubernetes", {}).get("kubeconfig", "")
    kube_args = [f"--kubeconfig={kubeconfig}"] if kubeconfig else []

    # Wait for new pod to come up
    for attempt in range(6):  # 6 * 10s = 60s
        time.sleep(10)
        try:
            result_proc = subprocess.run(
                ["oc"] + kube_args + [
                    "get", "pods", "-n", namespace,
                    "-o", "jsonpath={range .items[*]}{.metadata.name} {.status.phase}\n{end}",
                ],
                capture_output=True, text=True, timeout=15,
            )
            if result_proc.returncode != 0:
                continue

            for line in result_proc.stdout.strip().split("\n"):
                parts = line.split()
                if len(parts) == 2 and parts[0].startswith(pod_prefix):
                    if parts[1] == "Running":
                        log.info(f"Verified: pod {parts[0]} is Running")
                        return True
        except Exception:
            continue

    log.warning(f"Pod restart verification timed out for {pod_prefix}")
    return False


def _verify_argocd_sync(result: ActionResult, config: dict,
                        secrets: dict, log: logging.Logger) -> bool:
    """Check ArgoCD app health after sync. Waits up to 90s."""
    raw = result.signal.raw_data
    app_name = raw.get("app_name", "")
    if not app_name:
        return False

    token = secrets.get("argocd_token")
    if not token:
        return False

    argocd_url = config["argocd"]["api_url"]

    for attempt in range(9):  # 9 * 10s = 90s
        time.sleep(10)
        try:
            resp = requests.get(
                f"{argocd_url}/applications/{app_name}",
                headers={"Authorization": f"Bearer {token}"},
                timeout=15,
                verify=False,
            )
            resp.raise_for_status()
            app = resp.json()
            health = app.get("status", {}).get("health", {}).get("status", "")
            sync = app.get("status", {}).get("sync", {}).get("status", "")

            if health == "Healthy" and sync == "Synced":
                log.info(f"Verified: ArgoCD {app_name} is Healthy+Synced")
                return True
        except Exception:
            continue

    log.warning(f"ArgoCD sync verification timed out for {app_name}")
    return False


def _verify_pod_deleted(result: ActionResult, config: dict,
                        log: logging.Logger) -> bool:
    """Verify a stuck pod is no longer present."""
    raw = result.signal.raw_data
    namespace = raw.get("namespace", "")
    pod_name = raw.get("pod_name", "")
    if not namespace or not pod_name:
        return False

    kubeconfig = config.get("kubernetes", {}).get("kubeconfig", "")
    kube_args = [f"--kubeconfig={kubeconfig}"] if kubeconfig else []

    time.sleep(5)
    try:
        proc = subprocess.run(
            ["oc"] + kube_args + ["get", "pod", pod_name, "-n", namespace],
            capture_output=True, text=True, timeout=15,
        )
        # Pod not found = success (it was deleted)
        if proc.returncode != 0 and "NotFound" in proc.stderr:
            log.info(f"Verified: pod {pod_name} deleted")
            return True
        return False
    except Exception:
        return False


def _verify_vault_unsealed(config: dict, log: logging.Logger) -> bool:
    """Check Vault seal status."""
    vault_addr = config["vault"]["addr"]
    try:
        resp = requests.get(
            f"{vault_addr}/v1/sys/seal-status",
            timeout=10,
            verify=False,
        )
        resp.raise_for_status()
        sealed = resp.json().get("sealed", True)
        if not sealed:
            log.info("Verified: Vault is unsealed")
            return True
        return False
    except Exception:
        return False
