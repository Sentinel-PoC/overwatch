"""Poll Plane for issues assigned to sentinel-agent."""

import logging
from typing import Optional

import requests

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from models import Signal, SignalSource


def poll_plane(config: dict, secrets: dict, log: logging.Logger) -> list[Signal]:
    """Poll Plane API for issues labeled or assigned to sentinel-agent.

    Returns list of Signal objects for issues in Todo/In Progress states
    that are labeled 'sentinel-agent'.
    """
    api_key = secrets.get("plane_api_key")
    if not api_key:
        log.warning("No Plane API key — skipping Plane poll")
        return []

    plane_cfg = config["plane"]
    base_url = plane_cfg["base_url"]
    workspace = plane_cfg["workspace_slug"]
    project_id = plane_cfg["project_id"]

    url = f"{base_url}/workspaces/{workspace}/projects/{project_id}/issues/"

    try:
        resp = requests.get(
            url,
            headers={
                "x-api-key": api_key,
                "Content-Type": "application/json",
            },
            timeout=15,
        )
        resp.raise_for_status()
    except (requests.ConnectionError, requests.Timeout) as e:
        log.warning(f"Plane unreachable: {e}")
        return []
    except requests.HTTPError as e:
        log.error(f"Plane API error: {e}")
        return []

    data = resp.json()
    issues = data.get("results", data) if isinstance(data, dict) else data

    # Resolve label name → UUID for matching
    target_label = plane_cfg["assigned_label"]
    target_label_id = _resolve_label_id(base_url, workspace, project_id,
                                         api_key, target_label, log)

    # Resolve actionable state groups
    actionable_states = _resolve_actionable_states(base_url, workspace,
                                                    project_id, api_key, log)

    signals = []
    for issue in issues:
        # Filter: must have sentinel-agent label
        issue_labels = issue.get("labels", [])
        if target_label_id and target_label_id not in issue_labels:
            continue
        elif not target_label_id:
            # Fallback: check label_detail names
            label_names = [l.get("name", "") for l in issue.get("label_detail", [])
                          if isinstance(l, dict)]
            if target_label not in label_names:
                continue

        # Filter: must be in actionable state
        issue_state = issue.get("state", "")
        if actionable_states and issue_state not in actionable_states:
            continue
        state_name = actionable_states.get(issue_state, "unknown") if actionable_states else ""

        seq = issue.get("sequence_id", "?")
        signals.append(Signal(
            source=SignalSource.PLANE,
            source_id=f"OPS-{seq}",
            summary=f"Plane issue OPS-{seq}: {issue.get('name', 'untitled')}",
            severity=_priority_to_severity(issue.get("priority", "none")),
            raw_data={
                "issue_id": issue["id"],
                "sequence_id": seq,
                "name": issue.get("name", ""),
                "priority": issue.get("priority", "none"),
                "state": state_name,
                "labels": issue.get("labels", []),
                "description_stripped": issue.get("description_stripped", "")[:500],
            },
            plane_issue_id=issue["id"],
        ))

    log.info(f"Plane: found {len(signals)} actionable issues")
    return signals


def _resolve_label_id(base_url: str, workspace: str, project_id: str,
                      api_key: str, label_name: str,
                      log: logging.Logger) -> str:
    """Resolve label name to UUID. Returns empty string on failure."""
    try:
        resp = requests.get(
            f"{base_url}/workspaces/{workspace}/projects/{project_id}/labels/",
            headers={"x-api-key": api_key},
            timeout=10,
        )
        resp.raise_for_status()
        labels = resp.json().get("results", resp.json()) if isinstance(resp.json(), dict) else resp.json()
        for lbl in labels:
            if lbl.get("name", "").lower() == label_name.lower():
                return lbl["id"]
    except Exception as e:
        log.warning(f"Failed to resolve label '{label_name}': {e}")
    return ""


def _resolve_actionable_states(base_url: str, workspace: str,
                                project_id: str, api_key: str,
                                log: logging.Logger) -> dict:
    """Get state UUIDs for actionable states (Todo, In Progress).

    Returns dict: {state_uuid: state_name} for unstarted/started groups.
    """
    try:
        resp = requests.get(
            f"{base_url}/workspaces/{workspace}/projects/{project_id}/states/",
            headers={"x-api-key": api_key},
            timeout=10,
        )
        resp.raise_for_status()
        states = resp.json().get("results", resp.json()) if isinstance(resp.json(), dict) else resp.json()
        return {
            s["id"]: s["name"]
            for s in states
            if s.get("group") in ("unstarted", "started")
        }
    except Exception as e:
        log.warning(f"Failed to resolve states: {e}")
    return {}


def _priority_to_severity(priority: str) -> int:
    """Map Plane priority to numeric severity (Wazuh scale 0-15)."""
    return {
        "urgent": 14,
        "high": 10,
        "medium": 6,
        "low": 3,
        "none": 1,
    }.get(priority, 1)
