# Agent State — {REPO_NAME}

<!-- ============================================================
     AGENT-STATE.md IS A CUMULATIVE APPEND-ONLY JOURNAL (OPS-599)
     ============================================================
     ALWAYS read the existing AGENT-STATE.md (if any) and PREPEND
     your session entry AT THE TOP, below the filename heading.
     NEVER replace or truncate prior content — the accumulated
     history is load-bearing for conflict resolution and audit.

     If AGENT-STATE.md does not yet exist: create it from this
     template (this IS the first entry, no prior content to keep).
     ============================================================ -->

**Written by:** {ROLE} — session ending {TIMESTAMP}
**Plane issue:** {ISSUE_ID} — {ISSUE_URL}
**Branch:** {BRANCH_NAME}
act_chain: "human={OPERATOR} orchestrator={TEAM_OR_SESSION} executing={AGENT_NAME} action=state-write resource={REPO}/AGENT-STATE.md"

<!-- Act-Chain field required (OPS-927). Emit on ALL THREE surfaces: Plane comments, git commit footer, and here.
     Schema: ~/sentinel-cache/conventions/act-chain-schema.md
     Example: act_chain: "human=jim orchestrator=backlog-2026-05-25 executing=worker-927-agentstate-actchain action=state-write resource=overwatch/AGENT-STATE.md" -->

## What was completed this session
(list with commit hashes — no assertions without hashes)

## What is IN PROGRESS but not done
(list with exact reason why not done)

## What is BLOCKED
(list with exact blocker — not "needs more work", the specific thing)

## Files modified
(exact paths, not globs)

## What next agent should do FIRST
(one sentence, specific, actionable)

## Compliance state at session end
Run: `python3 -c "import json; d=json.load(open('$HOME/sentinel-cache/config-cache/nist-compliance-latest.json')); checks=d['checks']; p=sum(1 for c in checks if c['status']=='PASS'); f=sum(1 for c in checks if c['status']=='FAIL'); w=sum(1 for c in checks if c['status']=='WARN'); print(f'{p} pass, {f} fail, {w} warn of {len(checks)} ({d[\"timestamp\"]}')"`
Result: {PASS_COUNT} pass, {FAIL_COUNT} fail, {WARN_COUNT} warn
Timestamp of check: {CHECK_TIMESTAMP}
Verified: {YES/NO — did you actually run this}
