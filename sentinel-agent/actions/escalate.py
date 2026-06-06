"""Escalation actions — Plane comment + ntfy notification.

Used when a signal is outside sentinel-agent's authority or
cannot be classified by rules/LLM.

Deduplication strategy (3 layers):
1. Persistent tracker: state/escalation-tracker.json maps source_ids
   to Plane issue UUIDs across cycles — fastest, no API call needed.
2. Plane API search: if tracker has no entry, search all open
   [sentinel-agent] issues by summary keyword overlap.
3. Create new issue: only if both layers find nothing.
"""

import logging

import requests

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from models import Signal, ActionResult
from notify.ntfy import send_ntfy
from state.escalation_tracker import (
    load_tracker, save_tracker, get_existing_issue, record_escalation,
)


def escalate(signal: Signal, config: dict, secrets: dict,
             log: logging.Logger) -> ActionResult:
    """Escalate a signal via Plane comment + ntfy push.

    1. Check persistent tracker for existing issue (no API call)
    2. Fall back to Plane API search for open matching issues
    3. Create new issue only if no match found
    4. Fire ntfy push
    """
    tracker = load_tracker(config)

    # Build escalation message
    message = _build_escalation_message(signal)

    # Post to Plane — dedup: check tracker, then API, then create
    plane_success = False
    if signal.plane_issue_id:
        plane_success = _comment_on_plane(
            signal.plane_issue_id, message, config, secrets, log
        )
    else:
        # Layer 1: persistent tracker
        tracked_id = get_existing_issue(tracker, signal.source_id)
        if tracked_id:
            log.info(f"Dedup (tracker): reusing issue {tracked_id[:8]}... "
                     f"for {signal.source_id}")
            plane_success = _comment_on_plane(
                tracked_id, message, config, secrets, log
            )
            signal.plane_issue_id = tracked_id
        else:
            # Layer 2: Plane API search
            existing_id = _find_existing_escalation(
                signal, config, secrets, log
            )
            if existing_id:
                log.info(f"Dedup (API): found existing issue {existing_id[:8]}...")
                plane_success = _comment_on_plane(
                    existing_id, message, config, secrets, log
                )
                signal.plane_issue_id = existing_id
            else:
                # Layer 3: create new issue
                issue_id = _create_plane_issue(
                    signal, message, config, secrets, log
                )
                if issue_id:
                    signal.plane_issue_id = issue_id
                    plane_success = True

    # Update tracker with current escalation
    if signal.plane_issue_id:
        record_escalation(tracker, signal.source_id, signal.plane_issue_id)
        save_tracker(config, tracker, log)

    # Fire ntfy — only for NEW issues (first escalation), not repeats
    escalation_count = tracker.get(signal.source_id, {}).get(
        "escalation_count", 1)
    if escalation_count <= 1:
        ntfy_priority = _signal_to_ntfy_priority(signal)
        issue_ref = signal.source_id or signal.plane_issue_id or "unknown"
        ntfy_msg = f"[ESCALATE] {issue_ref}: {signal.summary}"
        send_ntfy(config, ntfy_msg, priority=ntfy_priority)
    else:
        ntfy_priority = _signal_to_ntfy_priority(signal)
        log.info(f"Repeat escalation #{escalation_count} for "
                 f"{signal.source_id} — suppressing ntfy")

    return ActionResult(
        signal=signal,
        action_taken=f"escalated priority={ntfy_priority}",
        success=plane_success,
        evidence=f"Plane comment posted, ntfy priority {ntfy_priority} sent",
        error="" if plane_success else "Plane comment failed",
    )


def _build_escalation_message(signal: Signal) -> str:
    """Build a human-readable escalation message."""
    return (
        f"<p><strong>ESCALATION</strong> — sentinel-agent needs operator input</p>"
        f"<p><strong>Signal:</strong> {signal.summary}</p>"
        f"<p><strong>Source:</strong> {signal.source.value} "
        f"(ID: {signal.source_id})</p>"
        f"<p><strong>Severity:</strong> {signal.severity}</p>"
        f"<p><strong>Why escalating:</strong> Outside autonomous authority "
        f"or unclassified signal pattern</p>"
        f"<p><strong>Raw data (excerpt):</strong></p>"
        f"<pre>{_safe_json(signal.raw_data)}</pre>"
        f"<p><strong>Recommended:</strong> Review and assign to appropriate "
        f"agent/role or handle manually.</p>"
    )


def _safe_json(data: dict, max_len: int = 500) -> str:
    """JSON-serialize with truncation for HTML display."""
    import json
    text = json.dumps(data, indent=2)
    if len(text) > max_len:
        text = text[:max_len] + "\n... (truncated)"
    return text


def _comment_on_plane(issue_id: str, comment_html: str,
                      config: dict, secrets: dict,
                      log: logging.Logger) -> bool:
    """Post a comment on an existing Plane issue."""
    api_key = secrets.get("plane_api_key")
    if not api_key:
        log.warning("No Plane API key for escalation comment")
        return False

    plane_cfg = config["plane"]
    url = (f"{plane_cfg['base_url']}/workspaces/{plane_cfg['workspace_slug']}"
           f"/projects/{plane_cfg['project_id']}/issues/{issue_id}/comments/")

    try:
        resp = requests.post(
            url,
            headers={
                "x-api-key": api_key,
                "Content-Type": "application/json",
            },
            json={"comment_html": comment_html},
            timeout=15,
        )
        resp.raise_for_status()
        log.info(f"Escalation comment posted to issue {issue_id}")
        return True
    except Exception as e:
        log.error(f"Failed to post escalation comment: {e}")
        return False


def _find_existing_escalation(signal: Signal, config: dict, secrets: dict,
                               log: logging.Logger) -> str:
    """Search Plane for an open issue matching this signal.

    Returns issue UUID if a matching open issue exists, regardless of age.
    An issue is a match if it was created by sentinel-agent and covers
    the same signal (matched by summary keywords).
    """
    api_key = secrets.get("plane_api_key")
    if not api_key:
        return ""

    plane_cfg = config["plane"]
    url = (f"{plane_cfg['base_url']}/workspaces/{plane_cfg['workspace_slug']}"
           f"/projects/{plane_cfg['project_id']}/issues/")

    try:
        resp = requests.get(
            url,
            headers={
                "x-api-key": api_key,
                "Content-Type": "application/json",
            },
            params={"search": "sentinel-agent"},
            timeout=15,
        )
        resp.raise_for_status()
    except Exception as e:
        log.warning(f"Dedup search failed: {e}")
        return ""

    data = resp.json()
    results = data.get("results", data) if isinstance(data, dict) else data

    for issue in results:
        name = issue.get("name", "")
        if "[sentinel-agent]" not in name:
            continue

        # Match by key words from the signal summary — extract the
        # meaningful parts (agent name, ID, status) and check overlap
        if not _summaries_match(signal.summary, name):
            continue

        # Only match open issues (backlog, unstarted, started)
        state_detail = issue.get("state_detail", {})
        state_group = ""
        if isinstance(state_detail, dict):
            state_group = state_detail.get("group", "")

        if state_group in ("completed", "cancelled"):
            continue

        log.info(f"Dedup match: OPS-{issue.get('sequence_id')} ({issue['id'][:8]}...)")
        return issue["id"]

    return ""


def _summaries_match(signal_summary: str, issue_name: str) -> bool:
    """Check if a signal summary matches an issue name.

    Extracts meaningful words (entity names, statuses) and requires
    sufficient overlap in BOTH directions. Filters out bracketed tags,
    parenthetical noise, label prefixes, and pure-numeric tokens to
    focus on the distinguishing parts (e.g. agent names, service names).
    """
    import re
    stop_words = {"the", "and", "for", "are", "from", "has", "was",
                  "been", "is", "not", "alert", "level", "agent"}

    def key_words(text: str) -> set:
        # Remove bracketed tags, parenthetical groups, and common prefixes
        cleaned = re.sub(r'\[.*?\]', '', text)
        cleaned = re.sub(r'\(.*?\)', '', cleaned)
        cleaned = re.sub(r'(?i)^(wazuh alert:|sentinel-agent)\s*', '', cleaned.strip())
        return {w for w in cleaned.lower().split()
                if len(w) >= 3 and w not in stop_words
                and not w.isdigit()}

    signal_words = key_words(signal_summary)
    issue_words = key_words(issue_name)

    if not signal_words or not issue_words:
        return False

    overlap = signal_words & issue_words
    # Use the smaller set as denominator so asymmetric lengths don't
    # cause false negatives (signal summaries are often longer than titles)
    smaller = min(len(signal_words), len(issue_words))
    return len(overlap) / smaller >= 0.5


def _create_plane_issue(signal: Signal, description: str,
                        config: dict, secrets: dict,
                        log: logging.Logger) -> str:
    """Create a new Plane issue for an untracked signal. Returns issue UUID."""
    api_key = secrets.get("plane_api_key")
    if not api_key:
        return ""

    plane_cfg = config["plane"]
    url = (f"{plane_cfg['base_url']}/workspaces/{plane_cfg['workspace_slug']}"
           f"/projects/{plane_cfg['project_id']}/issues/")

    priority = "high" if signal.severity >= 10 else "medium"

    try:
        resp = requests.post(
            url,
            headers={
                "x-api-key": api_key,
                "Content-Type": "application/json",
            },
            json={
                "name": f"[sentinel-agent] {signal.summary[:100]}",
                "description_html": description,
                "priority": priority,
            },
            timeout=15,
        )
        resp.raise_for_status()
        issue_id = resp.json().get("id", "")
        seq = resp.json().get("sequence_id", "?")
        log.info(f"Created Plane issue OPS-{seq} for escalation")
        return issue_id
    except Exception as e:
        log.error(f"Failed to create Plane issue: {e}")
        return ""


def _signal_to_ntfy_priority(signal: Signal) -> int:
    """Map signal to ntfy priority level.

    5 (urgent): Vault sealed, multiple services down, security from internal IP
    4 (high): Fix failed, compliance regression >= 5
    3 (default): Routine escalation, new issue
    2 (low): Heartbeat
    """
    if signal.severity >= 14:
        return 5
    if signal.severity >= 10:
        return 4
    if signal.severity >= 6:
        return 3
    return 2
