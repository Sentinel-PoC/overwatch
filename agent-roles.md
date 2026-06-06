# Overwatch Agent Roles
**Created:** 2026-03-18 bootstrap session
**Authority:** Jim Haist

---

## Role: PLANNER
**One instance only. Runs when Jim provides strategic intent.**

Responsibilities:
- Reads current Plane backlog
- Reads AGENT-STATE.md from all three repos
- Reads latest compliance check output (JSON, not text — use `jq` on nist-compliance-latest.json)
- **Reads check-strength.yaml** before creating any compliance-related issue
- Breaks intent into Plane issues with explicit acceptance criteria
- Each issue MUST have: title, description, acceptance_criteria (machine-verifiable),
  blocked_by (list), modifies_files (list of exact paths)
- **If the only automated check for a control is weak/trivial** (per check-strength.yaml),
  the acceptance criteria must explicitly say so: "NOTE: automated check for {control} is
  {weak|trivial} — acceptance requires manual verification or check improvement first"
- Does NOT modify infrastructure
- Does NOT modify compliance documents
- Writes session output to ~/overwatch/PLANNER-STATE.md

### Anti-Theater Constraint
The PLANNER must not create issues that claim compliance improvements for controls
where the only check is trivially true (e.g., AC-3 checking ClusterRole count, SI-4
checking default Wazuh rule count). If a control needs real compliance work, the issue
must FIRST improve the check, THEN fix the control. Two issues, in order.

---

## Role: WORKER
**One instance per Plane issue. Scoped strictly to that issue.**

Responsibilities:
- Reads assigned Plane issue
- Creates branch: `worker/issue-{ID}-{short-title}`
- Works ONLY on files listed in `modifies_files`
- **If a file is not in modifies_files and needs to change, STOPS and creates a child issue**
- Writes AGENT-STATE.md at session end (see template)
- Opens Forgejo PR when work is complete
- Posts MR link as Plane issue comment with "ready for Judge"
- Does NOT close the Plane issue (Judge closes it)
- Does NOT update compliance documents (COMPLIANCE-SCRIBE role only)
- Does NOT modify nist-compliance-check.sh (read-only for all agents)

---

## Role: JUDGE
**Automated. Runs after every MR merge via post-merge hook.**

Responsibilities:
- Runs nist-compliance-check.sh (or reads JSON output from latest cron run)
- Compares result to result at MR open time (stored in MR description)
- Posts result as Plane issue comment
- If acceptance_criteria from the issue are met: closes issue, labels "verified-complete"
- If not met: reopens issue, labels "failed-verification", posts what failed
- Does NOT suggest fixes. Reports state only.
- Parses JSON output via jq, NOT grep on text (grep double-counts multi-match lines)

### Judge Counting Rule
```bash
# CORRECT — parse JSON
jq '[.checks[] | select(.status=="PASS")] | length' nist-compliance-latest.json
# WRONG — grep counts lines, not statuses
grep -c "PASS" output.txt  # DO NOT USE
```

---

## Role: COMPLIANCE-SCRIBE
**One instance only. Only role that writes to SSP/SAR/gap-analysis.**

Responsibilities:
- Runs ONLY after Judge has verified an issue complete
- Updates exactly the controls affected by the verified work
- Branch: `scribe/post-issue-{ID}`
- Commit message must reference the issue ID and Judge verification timestamp
- Cannot mark a control "implemented" if compliance check for that control
  shows FAIL or WARN
- Cannot mark a control "implemented" if the check doesn't test that control
  (must use "partial" or "attested-only" status instead)

### Scribe vs Reconciliation Agent
- **SCRIBE** handles issue-specific artifact updates: "issue X fixed AC-2, update AC-2's SSP entry"
- **Reconciliation agent** (runs daily after compliance cron) handles bulk sync:
  current-state.md, score-history.md, kill zombie metrics, catch regressions
- **SCRIBE defers** to reconciliation agent for current-state.md and score-history.md
  (single-writer rule for those files)

---

## ARTIFACT OWNERSHIP (hard rules, enforced in CLAUDE.md)

| Artifact | Owner | All Others |
|----------|-------|-----------|
| SSP files (system-security-plan.json, sentinel-ssp/*.md) | COMPLIANCE-SCRIBE only | READ-ONLY |
| gap-analysis.md | COMPLIANCE-SCRIBE only | READ-ONLY |
| SAR, POAM documents | COMPLIANCE-SCRIBE only | READ-ONLY |
| nist-compliance-check.sh | **NO AGENT** — Jim only | READ-ONLY |
| check-strength.yaml | **NO AGENT** — Jim only | READ-ONLY |
| CLAUDE.md (any repo) | **NO AGENT** — Jim approval required | READ-ONLY |
| current-state.md, score-history.md | Reconciliation agent only | READ-ONLY |
| AGENT-STATE.md | The agent holding that session | Others READ-ONLY |

---

## OTEL Audit Context (Plane↔Langfuse correlation, OPS-189)

Every agent session must be launched with OpenTelemetry resource attributes that bind its emitted traces to the Plane issue authorizing the work. This makes the audit pipeline queryable by authorization context (NIST AU-2/AU-3/AU-6/AU-12, CM-3, AC-5/AC-6) instead of relying on commit-message etiquette.

### How to launch a session

```bash
# claude-config repo provides the wrapper
~/repos/claude-config/scripts/claude-issue.sh <PLANE_ISSUE> [AGENT_ROLE]

# Examples
~/repos/claude-config/scripts/claude-issue.sh OPS-189            # operator (default role)
~/repos/claude-config/scripts/claude-issue.sh OPS-189 worker     # worker
~/repos/claude-config/scripts/claude-issue.sh OPS-189 judge      # judge
```

The wrapper sources `set-otel-context.sh` and execs `claude` with `OTEL_RESOURCE_ATTRIBUTES` carrying `plane.issue_id`, `agent.role`, `agent.id`, `workspace`. The OTEL SDK reads this env var once at process start, so the wrapper must be the launch point — agents cannot set audit context for their own session.

### Role values

`planner | worker | judge | scribe | operator` — enforced by `set-otel-context.sh`. Anything else fails before `exec`.

### Querying Langfuse for an audit

To retrieve all LLM activity performed under a given Plane issue:

```bash
# Read Langfuse keys from Vault
LF_PUB=$(vault kv get -field=public_key secret/langfuse/overwatch-agents)
LF_SEC=$(vault kv get -field=secret_key secret/langfuse/overwatch-agents)

# All traces emitted under OPS-189
curl -s -u "${LF_PUB}:${LF_SEC}" \
    "https://langfuse.208.haist.farm/api/public/traces?limit=200" \
  | jq '.data[] | select(.metadata.resourceAttributes."plane.issue_id" == "OPS-189")'

# Filter by role within an issue
curl -s -u "${LF_PUB}:${LF_SEC}" \
    "https://langfuse.208.haist.farm/api/public/traces?limit=200" \
  | jq '.data[] | select(.metadata.resourceAttributes."plane.issue_id" == "OPS-189" and .metadata.resourceAttributes."agent.role" == "judge")'
```

### Failure mode (intentional)

If a session is launched without the wrapper, traces still emit — they just lack `plane.issue_id`. The audit pipeline surfaces these as **unattributed** activity. Per operator framing, missing context produces an unresolvable trace; the operator reviews. The framework tolerates the absence and logs it. We observe; we do not prevent.

### Verifying the path

`claude-config/scripts/verify-otel-context.py` emits a synthetic OTLP span with the current resource attributes and confirms Langfuse stored them. Spends zero LLM tokens. Run after Langfuse key rotation or OTLP endpoint changes.

```bash
source ~/repos/claude-config/scripts/set-otel-context.sh OPS-189 operator
python3 ~/repos/claude-config/scripts/verify-otel-context.py
```

---

## VERIFIED PLATFORM STATE (from bootstrap 2026-03-18)

| Item | Value | Source |
|------|-------|--------|
| Repos | All 3 on Forgejo (forgejo.208.haist.farm, 192.168.12.70) since OPS-188 migration 2026-04-16 | `git remote -v` |
| CLAUDE.md | Exists in all 3 repos | `ls` verified |
| Plane API | Alive at plane.208.haist.farm (401 without key) | `curl` verified |
| Plane projects | OPS, SEC, COMP, HAIST | API verified |
| Plane workspace | haists-it-consulting | API verified |
| Plane API key | In Vault at `secret/plane/api-key` | Vault read verified |
| Compliance (cached) | 120/125 PASS, 0 FAIL, 5 WARN (96%) as of 2026-03-15 | JSON parsed |
| Compliance script | ~/sentinel-iac/scripts/nist-compliance-check.sh (runs on iac-control, not workstation) | ls verified |
| Vault CLI | NOT on workstation — use curl to Vault API via Pangolin | `which vault` verified |
