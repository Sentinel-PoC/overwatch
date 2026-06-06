"""Tier 3 actions — Git branch + PR workflow for manifest fixes.

Requires Claude API for frontier reasoning on K8s manifest changes.
Falls back to escalation if Claude is unavailable.
"""

import json
import logging
import subprocess
from pathlib import Path

import requests

import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from models import Signal, ActionResult, LLMResult
from llm.client import query_claude
from llm.prompt import load_system_prompt
from llm.router import _log_llm_decision


def execute_tier3(signal: Signal, config: dict, secrets: dict,
                  log: logging.Logger) -> ActionResult:
    """Execute a Tier 3 Git change via branch + PR.

    Workflow:
    1. Ask Claude for a diagnosis and fix
    2. Create branch in overwatch-gitops
    3. Apply the fix
    4. Commit and push
    5. Create Forgejo PR
    6. Comment on Plane issue
    7. Notify via ntfy
    """
    # Step 1: Get fix from Claude
    system_prompt = load_system_prompt()
    diagnosis_prompt = _build_fix_prompt(signal)

    llm_result = query_claude(
        diagnosis_prompt, system_prompt, config, secrets, log
    )

    # Log the Claude interaction for audit
    from triage import rules_only_diagnosis
    from models import Tier
    _log_llm_decision(config, signal, llm_result, diagnosis_prompt,
                      parsed_tier=Tier.GIT_CHANGE if llm_result.success else None,
                      rules_tier=Tier.GIT_CHANGE, log=log)

    if not llm_result.success or not llm_result.response_text:
        return ActionResult(
            signal=signal,
            action_taken="tier3-claude-diagnosis",
            success=False,
            error="Claude API unavailable — escalating",
        )

    # Parse the fix
    fix = _parse_fix_response(llm_result.response_text, log)
    if not fix:
        return ActionResult(
            signal=signal,
            action_taken="tier3-parse-fix",
            success=False,
            error="Could not parse Claude's fix recommendation",
        )

    # Step 2: Create branch
    issue_id = signal.source_id or "unknown"
    branch_name = _make_branch_name(issue_id, signal.summary)
    gitops_path = _get_gitops_path(config)

    if not gitops_path:
        return ActionResult(
            signal=signal,
            action_taken="tier3-find-repo",
            success=False,
            error="overwatch-gitops repo path not found",
        )

    success, output = _git_cmd(
        ["checkout", "-b", branch_name],
        cwd=gitops_path, log=log
    )
    if not success:
        return ActionResult(
            signal=signal,
            action_taken=f"tier3-create-branch {branch_name}",
            success=False,
            error=f"Failed to create branch: {output}",
        )

    # Step 3: Apply fix
    file_path = fix.get("file_path", "")
    file_content = fix.get("content", "")

    if not file_path or not file_content:
        _git_cmd(["checkout", "main"], cwd=gitops_path, log=log)
        return ActionResult(
            signal=signal,
            action_taken="tier3-apply-fix",
            success=False,
            error="Fix missing file_path or content",
        )

    target_file = Path(gitops_path) / file_path
    target_file.parent.mkdir(parents=True, exist_ok=True)
    target_file.write_text(file_content)

    # Step 4: Commit and push
    agent_id = config.get("agent", {}).get("id", "sentinel-agent")
    commit_msg = f"[{issue_id}] {fix.get('summary', signal.summary)}\n\nAgent: {agent_id}"

    _git_cmd(["add", file_path], cwd=gitops_path, log=log)
    success, output = _git_cmd(
        ["commit", "-m", commit_msg],
        cwd=gitops_path, log=log
    )
    if not success:
        _git_cmd(["checkout", "main"], cwd=gitops_path, log=log)
        return ActionResult(
            signal=signal,
            action_taken="tier3-commit",
            success=False,
            error=f"Commit failed: {output}",
        )

    success, output = _git_cmd(
        ["push", "-u", "origin", branch_name],
        cwd=gitops_path, log=log
    )
    if not success:
        _git_cmd(["checkout", "main"], cwd=gitops_path, log=log)
        return ActionResult(
            signal=signal,
            action_taken="tier3-push",
            success=False,
            error=f"Push failed: {output}",
        )

    # Step 5: Create Forgejo PR
    mr_url = _create_forgejo_pr(
        branch_name, issue_id, fix.get("summary", signal.summary),
        config, secrets, log
    )

    # Step 6: Comment on Plane issue
    if signal.plane_issue_id and mr_url:
        _comment_on_plane(
            signal.plane_issue_id, mr_url, fix.get("summary", ""),
            config, secrets, log
        )

    # Step 7: Notify
    from notify.ntfy import send_ntfy
    send_ntfy(
        config,
        f"[{issue_id}] PR created: {fix.get('summary', signal.summary)}\n{mr_url or 'MR creation failed'}",
        priority=3,
    )

    # Return to main
    _git_cmd(["checkout", "main"], cwd=gitops_path, log=log)

    return ActionResult(
        signal=signal,
        action_taken=f"tier3-mr-created branch={branch_name}",
        success=True,
        evidence=f"MR: {mr_url}" if mr_url else "Branch pushed, MR creation may have failed",
    )


def _build_fix_prompt(signal: Signal) -> str:
    """Build a prompt asking Claude for a specific manifest fix."""
    return f"""\
An infrastructure problem has been detected on the Overwatch Platform (OKD 4.19).

Signal: {signal.summary}
Source: {signal.source.value}
Raw data: {json.dumps(signal.raw_data, indent=2)}

Analyze this problem and provide a fix as JSON:
{{
  "summary": "one-line description of the fix",
  "file_path": "relative path in overwatch-gitops repo (e.g., apps/myapp/values.yaml)",
  "content": "complete file content with the fix applied",
  "rationale": "why this fix is correct"
}}

Important:
- The repo is overwatch-gitops with ArgoCD app-of-apps pattern
- Apps are under apps/ directory with Helm values
- Do NOT modify Kyverno policies, Vault config, or security tooling
- Provide the COMPLETE file content, not a diff
"""


def _parse_fix_response(response: str, log: logging.Logger) -> dict:
    """Parse Claude's fix response."""
    try:
        text = response.strip()
        if "```" in text:
            start = text.index("```") + 3
            if text[start:start + 4] == "json":
                start += 4
            end = text.index("```", start)
            text = text[start:end].strip()
        return json.loads(text)
    except (json.JSONDecodeError, ValueError) as e:
        log.warning(f"Failed to parse Claude fix response: {e}")
        return {}


def _make_branch_name(issue_id: str, summary: str) -> str:
    """Generate branch name: {PROJ}-{SEQ}-{short-desc}."""
    # Clean summary for branch name
    clean = summary.lower()
    clean = "".join(c if c.isalnum() or c in ("-", " ") else "" for c in clean)
    words = clean.split()[:4]
    short_desc = "-".join(words) if words else "fix"
    return f"{issue_id}-{short_desc}"


def _get_gitops_path(config: dict) -> str:
    """Find the overwatch-gitops repo path."""
    # On iac-control it's at ~/overwatch-gitops/
    candidates = [
        Path.home() / "overwatch-gitops",
        Path("/home/ubuntu/overwatch-gitops"),
        Path("/home/sentinel-agent/overwatch-gitops"),
    ]
    for p in candidates:
        if (p / ".git").exists():
            return str(p)
    return ""


def _git_cmd(args: list[str], cwd: str,
             log: logging.Logger) -> tuple[bool, str]:
    """Run a git command."""
    cmd = ["git"] + args
    log.info(f"Git: {' '.join(cmd)} (cwd={cwd})")
    try:
        result = subprocess.run(
            cmd, cwd=cwd,
            capture_output=True, text=True, timeout=30,
        )
        output = result.stdout + result.stderr
        return result.returncode == 0, output.strip()
    except Exception as e:
        return False, str(e)


def _create_forgejo_pr(branch: str, issue_id: str, title: str,
                       config: dict, secrets: dict,
                       log: logging.Logger) -> str:
    """Create a Forgejo pull request. Returns PR URL or empty string."""
    token = secrets.get("forgejo_token")
    if not token:
        log.error("No Forgejo token — cannot create PR")
        return ""

    forgejo_cfg = config["forgejo"]
    base_url = forgejo_cfg["base_url"]
    owner = forgejo_cfg["owner"]
    repo = forgejo_cfg["repos"]["overwatch_gitops"]

    try:
        resp = requests.post(
            f"{base_url}/api/v1/repos/{owner}/{repo}/pulls",
            headers={"Authorization": f"token {token}"},
            json={
                "head": branch,
                "base": "main",
                "title": f"[{issue_id}] {title}",
                "body": f"Relates to {issue_id}\n\n"
                        f"## Changes\n{title}\n\n"
                        f"## Created by\nsentinel-agent (autonomous)",
            },
            timeout=30,
        )
        resp.raise_for_status()
        return resp.json().get("html_url", "")
    except Exception as e:
        log.error(f"Forgejo PR creation failed: {e}")
        return ""


def _comment_on_plane(issue_id: str, mr_url: str, summary: str,
                      config: dict, secrets: dict,
                      log: logging.Logger):
    """Post a comment on a Plane issue with MR link."""
    api_key = secrets.get("plane_api_key")
    if not api_key:
        return

    plane_cfg = config["plane"]
    url = (f"{plane_cfg['base_url']}/workspaces/{plane_cfg['workspace_slug']}"
           f"/projects/{plane_cfg['project_id']}/issues/{issue_id}/comments/")

    try:
        requests.post(
            url,
            headers={
                "x-api-key": api_key,
                "Content-Type": "application/json",
            },
            json={
                "comment_html": f"<p><strong>CHANGE</strong> — sentinel-agent created MR</p>"
                               f"<p>Fix: {summary}</p>"
                               f"<p>MR: <a href=\"{mr_url}\">{mr_url}</a></p>"
                               f"<p>Status: ready for review (Jim merges)</p>",
            },
            timeout=15,
        )
    except Exception as e:
        log.warning(f"Failed to comment on Plane issue: {e}")
