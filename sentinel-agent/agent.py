#!/usr/bin/env python3
"""sentinel-agent — autonomous infrastructure remediation loop.

Runs as a systemd timer on iac-control every 5 minutes.
Polls Plane, Wazuh, and ArgoCD for signals, triages them,
executes scoped remediation, and logs everything.

Architecture: ~/overwatch/docs/autonomous-operations-architecture.md Section 4
"""

import argparse
import json
import logging
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import yaml

from models import Signal, SignalSource, Tier, ActionResult
from triage import rules_only_diagnosis
from research import emit_research_event
from lock import is_locked, read_lock_info


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

def load_config(config_path: str) -> dict:
    """Load and validate config.yaml."""
    path = Path(config_path)
    if not path.exists():
        raise FileNotFoundError(f"Config not found: {config_path}")

    with open(path) as f:
        config = yaml.safe_load(f)

    required_sections = ["agent", "vault", "plane", "wazuh", "argocd",
                         "gemini", "ntfy", "forgejo", "kubernetes"]
    missing = [s for s in required_sections if s not in config]
    if missing:
        raise ValueError(f"Config missing required sections: {missing}")

    return config


# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

def setup_logging(config: dict) -> logging.Logger:
    """Configure JSON structured logging."""
    log_dir = Path(config["agent"].get("log_dir", "/var/log/sentinel-agent"))
    log_dir.mkdir(parents=True, exist_ok=True)

    logger = logging.getLogger("sentinel-agent")
    logger.setLevel(getattr(logging, config["agent"].get("log_level", "INFO")))

    # JSON formatter
    class JsonFormatter(logging.Formatter):
        def format(self, record):
            entry = {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "level": record.levelname,
                "message": record.getMessage(),
                "module": record.module,
            }
            if hasattr(record, "signal_id"):
                entry["signal_id"] = record.signal_id
            if record.exc_info and record.exc_info[0]:
                entry["exception"] = self.formatException(record.exc_info)
            return json.dumps(entry)

    # File handler
    fh = logging.FileHandler(log_dir / "agent.log")
    fh.setFormatter(JsonFormatter())
    logger.addHandler(fh)

    # Stderr for systemd journal
    sh = logging.StreamHandler(sys.stderr)
    sh.setFormatter(JsonFormatter())
    logger.addHandler(sh)

    return logger


# ---------------------------------------------------------------------------
# Vault credentials
# ---------------------------------------------------------------------------

def get_vault_token(config: dict, log: logging.Logger) -> Optional[str]:
    """Authenticate to Vault via AppRole, return token."""
    import requests

    vault_cfg = config["vault"]
    addr = vault_cfg["addr"]
    mount = vault_cfg["approle_mount"]

    role_id_file = Path(vault_cfg["role_id_file"])
    secret_id_file = Path(vault_cfg["secret_id_file"])

    if not role_id_file.exists() or not secret_id_file.exists():
        log.error("Vault AppRole credential files missing")
        return None

    role_id = role_id_file.read_text().strip()
    secret_id = secret_id_file.read_text().strip()

    try:
        resp = requests.post(
            f"{addr}/v1/auth/{mount}/login",
            json={"role_id": role_id, "secret_id": secret_id},
            timeout=10,
            verify=False,  # internal CA
        )
        resp.raise_for_status()
        token = resp.json()["auth"]["client_token"]
        log.info("Vault AppRole login successful")
        return token
    except Exception as e:
        log.error(f"Vault AppRole login failed: {e}")
        return None


def get_secret(vault_addr: str, vault_token: str, path: str,
               log: logging.Logger, key: str = "") -> Optional[str]:
    """Read a secret from Vault KV v2.

    If key is specified, returns that specific key from the secret.
    Otherwise returns the first value (for single-key secrets).
    """
    import requests

    try:
        resp = requests.get(
            f"{vault_addr}/v1/{path}",
            headers={"X-Vault-Token": vault_token},
            timeout=10,
            verify=False,
        )
        resp.raise_for_status()
        data = resp.json()["data"]["data"]
        if key:
            return data.get(key)
        return list(data.values())[0]
    except Exception as e:
        log.error(f"Failed to read Vault secret {path}: {e}")
        return None


def load_secrets(config: dict, vault_token: str,
                 log: logging.Logger) -> dict:
    """Pull all required secrets from Vault.

    Config format: each entry is either a string (path) or a dict
    with 'path' and 'key' fields.
    """
    vault_addr = config["vault"]["addr"]
    secrets = {}
    for name, spec in config["vault"]["secrets"].items():
        if isinstance(spec, dict):
            path = spec["path"]
            key = spec.get("key", "")
        else:
            path = spec
            key = ""
        val = get_secret(vault_addr, vault_token, path, log, key=key)
        if val:
            secrets[name] = val
        else:
            log.warning(f"Secret {name} unavailable from {path}" +
                       (f" key={key}" if key else ""))
    return secrets


# ---------------------------------------------------------------------------
# Main cycle
# ---------------------------------------------------------------------------

def run_cycle(config: dict, secrets: dict, log: logging.Logger,
              dry_run: bool = False,
              cycle_count: int = 0) -> dict:
    """Execute one sentinel-agent cycle.

    Args:
        config: Loaded config.yaml dict.
        secrets: Credentials pulled from Vault.
        log: Logger instance.
        dry_run: If True, log actions without executing them.
        cycle_count: Monotonically increasing cycle number (from state file).
            Used by the per-source backoff tracker to compute skip windows.

    Returns cycle summary dict for logging.
    """
    from sources.plane import poll_plane
    from sources.wazuh import poll_wazuh
    from sources.argocd import poll_argocd
    from actions.tier2 import execute_tier2
    from actions.tier3 import execute_tier3
    from actions.escalate import escalate
    from verify.checks import verify_action
    from llm.router import get_diagnosis
    from state.backoff_tracker import (
        load_backoff_tracker, save_backoff_tracker,
        should_skip_for_backoff, record_tier2_success, record_tier2_failure,
    )

    cycle_start = time.monotonic()
    cycle_stats = {
        "signals_found": 0,
        "tier1_skipped": 0,
        "tier2_attempted": 0,
        "tier2_succeeded": 0,
        "tier2_backoff_skipped": 0,
        "tier3_attempted": 0,
        "tier3_succeeded": 0,
        "escalated": 0,
        "errors": 0,
    }

    # Track restart attempts per pod this cycle
    restart_counts: dict[str, int] = {}
    max_restarts = config.get("tier2", {}).get("max_restarts_per_pod", 3)

    # Load per-source backoff state (persists across cycles)
    backoff_tracker = load_backoff_tracker(config)

    # --- POLL ---
    signals: list[Signal] = []

    log.info("Polling input sources")
    signals.extend(poll_plane(config, secrets, log))
    signals.extend(poll_wazuh(config, secrets, log))
    signals.extend(poll_argocd(config, secrets, log))

    cycle_stats["signals_found"] = len(signals)
    log.info(f"Found {len(signals)} signals")

    if not signals:
        return cycle_stats

    # --- TRIAGE ---
    for signal in signals:
        # Try LLM diagnosis for Tier 2, fall back to rules-only
        if signal.tier is None:
            signal.tier = get_diagnosis(signal, config, secrets, log)

    # --- ACT ---
    for signal in signals:
        if signal.tier == Tier.SKIP:
            cycle_stats["tier1_skipped"] += 1
            log.info(f"SKIP (Tier 1): {signal.summary}")
            continue

        if signal.tier == Tier.OPERATIONAL:
            pod_key = signal.source_id

            # --- Per-source backoff gate ---
            skip, force_escalate = should_skip_for_backoff(
                backoff_tracker, pod_key, cycle_count, log
            )
            if force_escalate:
                log.warning(
                    f"Backoff: {pod_key} has exceeded consecutive failure "
                    f"threshold — converting to Tier.ESCALATE"
                )
                signal.tier = Tier.ESCALATE
                cycle_stats["escalated"] += 1
                if not dry_run:
                    escalate(signal, config, secrets, log)
                emit_research_event(config, "agent_escalated", {
                    "signal": signal.summary,
                    "reason": "backoff escalation threshold exceeded",
                    "source_id": pod_key,
                }, signal.plane_issue_id, log=log)
                continue

            if skip:
                cycle_stats["tier2_backoff_skipped"] += 1
                entry = backoff_tracker.get(pod_key, {})
                emit_research_event(config, "tier2_backoff_skipped", {
                    "source_id": pod_key,
                    "signal": signal.summary,
                    "failure_count": entry.get("failure_count", 0),
                    "consecutive_failures": entry.get("consecutive_failures", 0),
                    "next_eligible_cycle": entry.get("next_eligible_cycle", 0),
                    "current_cycle": cycle_count,
                    "skipped_this_cycle": True,
                }, signal.plane_issue_id, log=log)
                continue

            # --- Max per-cycle restart limit (existing guard) ---
            if restart_counts.get(pod_key, 0) >= max_restarts:
                log.warning(
                    f"Max restarts reached for {pod_key}, escalating"
                )
                signal.tier = Tier.ESCALATE
            else:
                cycle_stats["tier2_attempted"] += 1
                log.info(f"TIER 2: {signal.summary}")

                if dry_run:
                    log.info(f"DRY RUN: would execute tier2 for {signal.summary}")
                    result = ActionResult(
                        signal=signal,
                        action_taken="dry-run",
                        success=True,
                        evidence="dry run mode"
                    )
                else:
                    result = execute_tier2(signal, config, secrets, log)

                restart_counts[pod_key] = restart_counts.get(pod_key, 0) + 1

                if result.success:
                    cycle_stats["tier2_succeeded"] += 1
                    # Reset backoff on success
                    backoff_tracker = record_tier2_success(
                        backoff_tracker, pod_key
                    )
                    # Verify
                    if not dry_run:
                        verified = verify_action(result, config, secrets, log)
                        emit_research_event(config, "agent_fix_verified", {
                            "signal": signal.summary,
                            "action": result.action_taken,
                            "verified": verified,
                        }, signal.plane_issue_id, log=log)
                else:
                    cycle_stats["errors"] += 1
                    log.error(f"Tier 2 failed: {result.error}")
                    # Increment backoff counter on failure
                    if not dry_run:
                        backoff_tracker = record_tier2_failure(
                            backoff_tracker, pod_key, cycle_count, log
                        )

                emit_research_event(config, "agent_fix_attempted", {
                    "signal": signal.summary,
                    "tier": "2",
                    "action": result.action_taken,
                    "success": result.success,
                }, signal.plane_issue_id, log=log)

                # Persist updated backoff state after every Tier 2 attempt
                if not dry_run:
                    save_backoff_tracker(config, backoff_tracker, log)
                continue

        if signal.tier == Tier.GIT_CHANGE:
            cycle_stats["tier3_attempted"] += 1
            log.info(f"TIER 3: {signal.summary}")

            if dry_run:
                log.info(f"DRY RUN: would execute tier3 for {signal.summary}")
            else:
                result = execute_tier3(signal, config, secrets, log)
                if result.success:
                    cycle_stats["tier3_succeeded"] += 1
                else:
                    cycle_stats["errors"] += 1

                emit_research_event(config, "agent_fix_attempted", {
                    "signal": signal.summary,
                    "tier": "3",
                    "action": result.action_taken,
                    "success": result.success,
                }, signal.plane_issue_id, log=log)
            continue

        if signal.tier == Tier.ESCALATE:
            cycle_stats["escalated"] += 1
            log.info(f"ESCALATE: {signal.summary}")

            if not dry_run:
                escalate(signal, config, secrets, log)

            emit_research_event(config, "agent_escalated", {
                "signal": signal.summary,
                "reason": "outside authority or unclassified",
            }, signal.plane_issue_id, log=log)

    elapsed = time.monotonic() - cycle_start
    log.info(f"Cycle complete in {elapsed:.1f}s: {json.dumps(cycle_stats)}")

    return cycle_stats


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="sentinel-agent: autonomous infrastructure remediation"
    )
    parser.add_argument(
        "--config", default="/opt/sentinel-agent/config.yaml",
        help="Path to config.yaml"
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Log actions without executing them"
    )
    parser.add_argument(
        "--once", action="store_true",
        help="Run a single cycle then exit (default for systemd timer)"
    )
    args = parser.parse_args()

    # Load config
    try:
        config = load_config(args.config)
    except (FileNotFoundError, ValueError) as e:
        print(f"FATAL: {e}", file=sys.stderr)
        sys.exit(1)

    if args.dry_run:
        config["agent"]["dry_run"] = True

    # Setup logging
    log = setup_logging(config)
    log.info(f"sentinel-agent starting (dry_run={args.dry_run})")

    # Print config summary in dry-run mode
    if args.dry_run:
        log.info(f"Config loaded: sections={list(config.keys())}")
        log.info(f"Plane: {config['plane']['base_url']}")
        log.info(f"Wazuh: {config['wazuh']['api_url']}")
        log.info(f"ArgoCD: {config['argocd']['api_url']}")
        models = config['gemini'].get('models', [config['gemini'].get('model', '?')])
        log.info(f"Gemini: {models}")
        log.info(f"Vault: {config['vault']['addr']}")
        print("sentinel-agent dry-run config loaded successfully")
        sys.exit(0)

    # --- Maintenance-lock check (OPS-233) ---
    # Operator touches /var/run/sentinel-agent.halt before hands-on work.
    # Agent exits 0 cleanly — systemd timer fires again next interval.
    # Lock disappears on reboot (tmpfs) so no permanent halt risk.
    if is_locked():
        lock_info = read_lock_info(log)
        log.warning(
            "MAINTENANCE LOCK ACTIVE — skipping cycle. "
            f"Lock file: /var/run/sentinel-agent.halt "
            f"metadata={lock_info}"
        )
        # Emit research event so every locked cycle appears in the audit trail
        try:
            emit_research_event(
                config,
                "maintenance_lock_active",
                {
                    "lock_file": "/var/run/sentinel-agent.halt",
                    "metadata": lock_info,
                },
                narrative="Operator maintenance lock active — cycle skipped",
                log=log,
            )
        except Exception:
            pass  # research log failure must not prevent clean exit
        sys.exit(0)

    # Authenticate to Vault
    vault_token = get_vault_token(config, log)
    if not vault_token:
        log.error("Cannot authenticate to Vault — aborting cycle")
        # Fire ntfy priority 5 if possible
        try:
            from notify.ntfy import send_ntfy
            send_ntfy(
                config, "Vault authentication failed — sentinel-agent cannot start",
                priority=5
            )
        except Exception:
            pass
        sys.exit(1)

    # Pull secrets
    secrets = load_secrets(config, vault_token, log)
    if not secrets.get("plane_api_key"):
        log.error("Plane API key unavailable — cannot comment on issues")

    # Load and increment cycle counter (used by per-source backoff tracker)
    from state.backoff_tracker import load_cycle_count, save_cycle_count
    cycle_count = load_cycle_count(config)
    cycle_count += 1
    save_cycle_count(config, cycle_count, log)
    log.info(f"sentinel-agent cycle #{cycle_count}")

    # Run cycle
    try:
        stats = run_cycle(
            config, secrets, log,
            dry_run=config["agent"]["dry_run"],
            cycle_count=cycle_count,
        )
    except Exception as e:
        log.exception(f"Cycle failed with unhandled exception: {e}")
        sys.exit(1)

    log.info("sentinel-agent cycle complete")


if __name__ == "__main__":
    main()
