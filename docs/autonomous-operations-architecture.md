# Overwatch Autonomous Operations Architecture

**Version:** 1.1
**Date:** 2026-03-18
**Author:** Jim Haist / Haist IT Consulting
**Platform:** Overwatch Platform — Project Sentinel (208.haist.farm)

---

## 1. Purpose

This document defines how AI agents autonomously build, maintain, and
verify the Overwatch Platform with minimal human intervention. It
unifies three subsystems into a single operational architecture:

1. **Sentinel Agent** — fixes infrastructure (pods, services, nodes)
2. **Compliance Reconciliation** — fixes documentation (SSP, SAR, POAM)
3. **Multi-Agent Coordination** — governs who writes what, when, and how

The goal is a platform where AI agents do the work, deterministic tools
verify the work, and Jim reviews the audit trail — not every action.

---

## 2. Design Principles

### 2.1 Ground Truth Flows Downhill

```
Real infrastructure state
        │
        ▼  measured by
Deterministic compliance check (nist-compliance-check.sh)
        │
        ▼  consumed by
All compliance artifacts (SSP, SAR, POAM, state files)
```

No agent asserts compliance status. The script measures it, agents
update artifacts to match. If the script and an artifact disagree,
the artifact is wrong.

### 2.2 Agents Cannot Verify Their Own Work

The agent that fixes something is never the agent that marks it
complete. This is enforced by role separation:

- WORKER fixes infrastructure → JUDGE verifies → SCRIBE updates docs
- Sentinel-agent fixes a pod → compliance check measures the result →
  reconciliation agent updates the SSP

### 2.3 Artifact Ownership Is Exclusive

Every file has exactly one role authorized to write it. Two agents
writing to the same file is the root cause of the SSP overwrite
(commit bf9f8df) and the zombie 64-65% metric. Ownership is enforced
in CLAUDE.md, CI checks, and the CODEOWNERS equivalent.

### 2.4 Weak Evidence Cannot Create Strong Claims

A compliance check that merely verifies a file exists cannot upgrade
an SSP control to "implemented." The check-strength registry
classifies every check, and the reconciliation agent respects it.

---

## 3. System Overview

```
┌─────────────────────────────────────────────────────────────────┐
│                    HUMAN LAYER (Jim)                             │
│  Reviews PRs · Merges · Provides strategic intent · Overrides   │
└──────────┬──────────────────────────┬───────────────────────────┘
           │                          │
     ntfy pager                 Plane replies
           │                          │
┌──────────┴──────────────────────────┴───────────────────────────┐
│                 COORDINATION LAYER                               │
│                                                                  │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌────────────────┐  │
│  │ PLANNER  │  │ WORKER   │  │  JUDGE   │  │ COMPLIANCE-    │  │
│  │          │  │ (N inst) │  │          │  │ SCRIBE         │  │
│  └────┬─────┘  └────┬─────┘  └────┬─────┘  └───────┬────────┘  │
│       │             │             │                 │            │
│       │     Plane issues    judge-verify.sh   SSP/SAR/POAM      │
│       │             │             │                 │            │
└───────┼─────────────┼─────────────┼─────────────────┼───────────┘
        │             │             │                 │
┌───────┼─────────────┼─────────────┼─────────────────┼───────────┐
│       │        AUTOMATION LAYER   │                 │            │
│       │             │             │                 │            │
│  ┌────┴─────┐  ┌────┴─────┐  ┌───┴────────┐  ┌────┴─────────┐ │
│  │ sentinel │  │ ArgoCD   │  │ compliance │  │ reconcile    │ │
│  │ -agent   │  │ auto-    │  │ check cron │  │ agent        │ │
│  │ (5 min)  │  │ sync     │  │ (daily)    │  │ (post-check) │ │
│  └────┬─────┘  └────┬─────┘  └───┬────────┘  └──────────────┘ │
│       │             │             │                              │
└───────┼─────────────┼─────────────┼─────────────────────────────┘
        │             │             │
┌───────┼─────────────┼─────────────┼─────────────────────────────┐
│       ▼             ▼             ▼                              │
│              INFRASTRUCTURE LAYER                                │
│                                                                  │
│  OKD 4.19 · Vault · Wazuh · Forgejo · Plane · ArgoCD            │
│  iac-control · 3 Proxmox hypervisors · NFS · MinIO               │
│                                                                  │
└─────────────────────────────────────────────────────────────────┘
```

---

## 4. Subsystem 1: Sentinel Agent (Infrastructure)

### 4.1 Function

Detects and fixes infrastructure problems. Runs as a systemd timer
on iac-control, polling every 5 minutes. Monitors Plane issues,
Wazuh alerts, and ArgoCD health. Executes scoped remediation within
its authority, escalates everything else via Plane comments + ntfy.

### 4.2 Input Sources

| Source   | API                                   | What It Detects                        |
|----------|---------------------------------------|----------------------------------------|
| Plane    | https://plane.208.haist.farm/api/v1   | Issues assigned to agent or labeled sentinel-agent |
| Wazuh    | https://wazuh.208.haist.farm:55000    | Unacknowledged alerts above severity threshold |
| ArgoCD   | https://argocd.208.haist.farm/api/v1  | Apps with Degraded/Missing/OutOfSync+SyncFailed |

### 4.3 Action Tiers

| Tier | Description | Authority | Example |
|------|-------------|-----------|---------|
| 1 | Drift from Git state, no sync error | ArgoCD handles automatically | OutOfSync app → auto-sync |
| 2 | Operational fix, no code change | Autonomous | CrashLoopBackOff → restart pod |
| 3 | Requires Git change | PR only, Jim merges | Manifest fix → branch + PR |
| ESCALATE | Outside authority or unclear | Plane comment + ntfy | PVC deletion, Kyverno change |

### 4.4 Authority Model

**Autonomous (no approval):**
- Restart pods (max 3 attempts per pod per cycle)
- Force-sync ArgoCD apps with no SyncError
- Restart Wazuh agents
- Unseal Vault (if credentials available)
- Scale deployments to declared replica count
- Clear old Jobs/CronJobs
- Run diagnostics, comment on Plane issues
- Create child issues for discovered problems

**With notification (proceed after 15 min if no response):**
- Create Forgejo branch + PR for overwatch-gitops fixes
- Restart dnsmasq/haproxy/squid on iac-control
- Delete stuck Terminating pods
- Update non-security Helm values

**Requires Jim's explicit approval on Plane:**
- Delete PVCs or PVs
- Modify Kyverno ClusterPolicies
- Change iptables/firewall rules
- Modify Vault policies/auth/secrets
- Change ArgoCD app definitions
- Touch sentinel-iac repo (Ansible/Terraform)
- Merge own PRs

**Never:**
- Delete namespaces or CRDs
- Disable Wazuh agents/rules
- Expose new services externally
- Create or modify Secrets directly (Vault/ExternalSecrets only)
- Bypass Git

### 4.5 Communication

**ntfy** is the pager — one-way, gets Jim's attention.
**Plane** is the conversation — two-way, async.

1. Agent comments on Plane issue with diagnosis + proposed action
2. Fires ntfy push with issue link
3. Jim reads Plane comment, replies
4. Next agent cycle picks up Jim's reply

### 4.6 LLM Routing

| Tier | Primary | Fallback | Rationale |
|------|---------|----------|-----------|
| Triage/classification | Rules-based (no LLM) | — | Pattern match on Wazuh rule IDs, ArgoCD status, Plane labels |
| Tier 2 (operational) | Gemini API (free tier — `gemini-2.5-flash` default, multi-model fallback for quota spreading) | Rules-only (no LLM) | Free-tier quota (~60 RPD across models) removes local-compute dependency; decisions logged to `llm-decisions.jsonl` |
| Tier 3 (Git changes) | Claude API (pay-per-token) | Escalate to Jim | Correct K8s manifests need frontier reasoning |

> **Doc-to-code note (OPS-199, 2026-04-17):** the original design specified
> Ollama on the workstation (`qwen2.5:14b`, ROCm) for Tier 2, falling back to
> rules-only if the workstation was off. The deployed agent uses Gemini API
> instead. The substitution is intentional — it removes the
> workstation-on + ROCm-healthy prerequisite and uses the free-tier quota
> that is sufficient for Tier 2 volume — but the doc lagged the code until
> HAIST-27 Phase 1 caught the drift. Below reflects what is actually running.

**Tier 2 fallback behavior:** The sentinel-agent calls Gemini from
iac-control. Multiple Gemini models are tried in order when 429 (rate
limited) comes back, spreading the ~60-RPD free-tier quota across
models before falling back to rules. If all Gemini models are
exhausted or the network is unreachable, the agent falls back to
rules-only diagnosis. The exact code path (lightly paraphrased):

```python
def query_gemini(prompt, config, secrets, log):
    """Try each configured model in order; fall back on 429."""
    models = config["gemini"].get(
        "models", [config["gemini"].get("model", "gemini-2.5-flash")]
    )
    for model in models:
        result = _try_gemini_model(prompt, model, ...)  # hard 30s timeout
        if result.success:
            return result
        # 429 or other error — try the next model
    # All models exhausted — caller falls back to rules-only
    return LLMResult(success=False, ...)

def get_diagnosis(signal, config, secrets, log):
    rules_tier = rules_only_diagnosis(signal)
    if rules_tier in (Tier.OPERATIONAL, Tier.ESCALATE):
        llm = query_gemini(_build_prompt(signal), config, secrets, log)
        if llm.success:
            return _parse_llm_tier(llm.response_text, log)
        log.warning("Gemini unavailable, using rules-only")
        return rules_tier
    # Tier 3 goes to Claude, not Gemini (see `query_claude` in llm/client.py)
    ...
```

Rules-only Tier 2 handles the common cases without LLM reasoning:
CrashLoopBackOff → restart pod, Wazuh agent offline → restart agent,
ArgoCD OutOfSync no error → force sync. Anything the rules can't
classify gets escalated instead of diagnosed by LLM.

This means Tier 2 degrades gracefully when Gemini is rate-limited or
unreachable — it handles known patterns and escalates unknowns, rather
than failing silently or blocking. Every Gemini call (success or
failure) is appended to `/var/log/sentinel-agent/llm-decisions.jsonl`
with prompt, response, parsed tier, rules-tier, and whether the LLM
agreed with rules — this is the audit trail used to measure LLM
usefulness over time.

### 4.7 Deployment

```
/opt/sentinel-agent/
├── agent.py              # Main loop
├── config.yaml           # API endpoints, Vault refs, thresholds
├── check-strength.yaml   # Weak check registry (shared with reconcile)
├── PROMPT.md             # System prompt (loaded at runtime)
├── sources/
│   ├── plane.py          # Poll Plane API
│   ├── wazuh.py          # Poll Wazuh API
│   └── argocd.py         # Poll ArgoCD API
├── actions/
│   ├── tier2.py          # Operational fixes
│   ├── tier3.py          # Git branch → PR workflow
│   └── escalate.py       # Plane comment + ntfy
├── llm/
│   ├── client.py         # Ollama / Claude API abstraction
│   ├── prompt.py         # System prompt loader
│   └── router.py         # Tier → model routing
├── notify/
│   └── ntfy.py           # ntfy push client
├── verify/
│   └── checks.py         # Post-action verification
└── tests/
```

**systemd:** `sentinel-agent.timer` → every 5 minutes, hardened with
NoNewPrivileges, ProtectSystem=strict, PrivateTmp.

**Credentials:** Vault AppRole, 5-min TTL tokens, scoped read-only
policy. Agent pulls its own creds each cycle.

---

## 5. Subsystem 2: Compliance Reconciliation (Documentation)

### 5.1 Function

Keeps compliance artifacts consistent with deterministic check
results. Runs automatically after every compliance check. Eliminates
zombie metrics, catches SSP overwrites, prevents multi-source
contradiction.

### 5.2 What It IS vs What It IS NOT

| IS | IS NOT |
|----|--------|
| Reconciliation engine | Auditor or advisor |
| Updates artifacts to match measured reality | Interprets controls or assesses risk |
| Kills zombie metrics | Generates new compliance claims |
| Enforces notation consistency | Modifies the compliance check script |

### 5.3 Trigger

Separate systemd service with hard dependency on the compliance check.
NOT ExecStartPost — a reconcile crash in ExecStartPost would corrupt
the compliance check's exit code, making it look like the check itself
failed.

```ini
# /etc/systemd/system/compliance-reconcile.service
[Unit]
Description=Compliance Artifact Reconciliation
After=nist-compliance-check.service
Requires=nist-compliance-check.service

[Service]
Type=oneshot
User=sentinel-agent
ExecStart=/opt/sentinel-agent/.venv/bin/python compliance_reconcile.py
Environment=VAULT_ADDR=https://vault.208.haist.farm:8200
StandardOutput=append:/var/log/sentinel-agent/reconcile.log
StandardError=append:/var/log/sentinel-agent/reconcile.log
TimeoutStartSec=600

[Install]
WantedBy=nist-compliance-check.service
```

The compliance check runs, exits cleanly with its own exit code, then
systemd starts the reconcile service. If reconcile fails, the check
result is already written and unaffected.

### 5.4 Reconciliation Pipeline

```
nist-compliance-latest.json (ground truth)
        │
        ├─→ Compare against OSCAL SSP
        │     Update implementation-status per control
        │     Weak PASS → "partial" (not "implemented")
        │     Strong PASS → "implemented"
        │     FAIL/WARN → downgrade to "partial"
        │     Gap language detected → downgrade to "partial"
        │
        ├─→ Regenerate OSCAL SAR
        │     Merge with manual findings (no automated check)
        │
        ├─→ Update OSCAL POAM
        │     FAIL → PASS: complete item with date
        │     PASS → FAIL: open/reopen item
        │
        ├─→ Overwrite current-state.md
        │     ONLY these metrics, nothing else:
        │       - Check result: PASS/TOTAL (STATUS)
        │       - Pass rate: X%
        │       - Control coverage: CHECKED/APPLICABLE
        │       - Strong evidence: STRONG/APPLICABLE
        │       - Last check: TIMESTAMP
        │     Kill ALL zombie metrics (64-65%, ~176-180, etc.)
        │
        ├─→ Append to score-history.md (if numbers changed)
        │
        ├─→ trestle validate -a (revert if invalid)
        │
        └─→ Git commit + push to compliance-vault
```

### 5.5 The Weak Check Registry

Shared file: `/opt/sentinel-agent/check-strength.yaml`

Used by BOTH the reconciliation agent and the PLANNER role.

| Category | Count | Can Upgrade to "implemented"? |
|----------|-------|-------------------------------|
| Strong (runtime API probe) | ~40 | Yes |
| Moderate (config inspection) | ~30 | Yes |
| Weak (file existence, trivially true, policy grep, SCA proxy) | ~55 | No — "partial" max |

Known trivially-true checks (always pass, evidence nothing):
- CA-2 compliance_timer (script checks its own timer)
- CM-7 unnecessary_services (telnetd/rsh never installed)
- IA-8 non_org_users (only root has UID 0 — always true)
- AU-11 log_retention (>=3 logrotate configs — every Linux)
- AC-3 okd_rbac (>50 ClusterRoles — OKD ships hundreds)
- SI-4 wazuh_rules_loaded (>=4000 rules — default is ~4500)
- SC-39 process_isolation (>=5 SCCs — OpenShift ships ~20)

Known misleading check:
- IA-2(1) mfa_configured — hard-codes "OTP enabled" without
  verifying OTP is actually configured. Checks Keycloak realm only.

### 5.6 Regression Detection

If today's FAIL count exceeds yesterday's by >= 5:
- Flag regression in current-state.md
- Create/update POAM items
- ntfy priority 4: "[COMPLIANCE] Regression detected"

---

## 6. Subsystem 3: Multi-Agent Coordination

### 6.1 Roles

| Role | Instances | Trigger | Writes To |
|------|-----------|---------|-----------|
| PLANNER | 1 | Jim provides strategic intent | Plane issues, PLANNER-STATE.md |
| WORKER | N (1 per issue) | Plane issue assigned | Files in modifies_files only, AGENT-STATE.md |
| JUDGE | 1 | Post-PR-merge hook | Plane comments, judge results |
| COMPLIANCE-SCRIBE | 1 | After Judge verifies | SSP, SAR, POAM, gap-analysis.md |

### 6.2 Artifact Ownership Matrix

| Artifact | Owner | All Others |
|----------|-------|------------|
| OSCAL SSP (system-security-plan.json) | COMPLIANCE-SCRIBE | Read-only |
| OSCAL SAR (assessment-results.json) | COMPLIANCE-SCRIBE + reconcile agent | Read-only |
| OSCAL POAM | COMPLIANCE-SCRIBE + reconcile agent | Read-only |
| md_ssp/*.md | COMPLIANCE-SCRIBE | Read-only |
| gap-analysis.md | COMPLIANCE-SCRIBE | Read-only |
| current-state.md | Reconcile agent (daily bulk) | Read-only |
| nist-score-history.md | Reconcile agent | Read-only |
| nist-compliance-check.sh | Nobody (Jim only, via normal PR) | Read-only |
| CLAUDE.md (any repo) | Nobody (Jim only) | Read-only |
| overwatch-gitops manifests | WORKER (scoped to issue) | Read-only |
| sentinel-iac playbooks | WORKER (scoped to issue, Jim approval) | Read-only |
| AGENT-STATE.md | Current session agent | Previous version read-only |

### 6.3 SCRIBE vs Reconcile Agent — Division of Labor

| Concern | COMPLIANCE-SCRIBE | Reconcile Agent |
|---------|-------------------|-----------------|
| Trigger | After Judge verifies an issue | After daily compliance check |
| Scope | Controls affected by the verified issue | All controls with checks |
| SSP updates | Issue-specific status changes | Bulk status sync |
| current-state.md | Does not touch | Sole writer |
| score-history.md | Does not touch | Sole writer |
| Gap language scan | Does not do | Scans all "implemented" controls |
| Zombie metric cleanup | Does not do | Kills on every run |

The SCRIBE handles precision (this issue fixed AC-2, update AC-2).
The reconcile agent handles consistency (everything matches the
script output, nothing stale persists).

If both want to update the same control's SSP status, the reconcile
agent wins — it runs daily and has ground truth. The SCRIBE's changes
get validated or overridden on the next reconcile cycle.

### 6.4 Work Lifecycle

```
Jim provides strategic intent
        │
        ▼
┌──────────────┐
│   PLANNER    │  Reads Plane backlog, compliance state, check-strength.yaml
│              │  Creates Plane issues with:
│              │    - acceptance_criteria (machine-verifiable)
│              │    - blocked_by (dependencies)
│              │    - modifies_files (exact paths)
│              │    - check_strength (from registry — honest about what
│              │      the check actually verifies)
└──────┬───────┘
       │ assigns to sentinel-agent (Tier 2 ops)
       │ assigns to WORKER (Tier 3 Git changes)
       ▼
┌──────────────┐
│   WORKER     │  Creates branch: worker/issue-{ID}-{title}
│   or         │  Modifies ONLY files in modifies_files
│   sentinel-  │  If needs other files → creates child issue, STOPS
│   agent      │  Opens PR when complete
│              │  Writes AGENT-STATE.md
│              │  Comments on Plane: "ready for Judge"
└──────┬───────┘
       │ PR merged (Jim or auto-merge for low-risk)
       ▼
┌──────────────┐
│    JUDGE     │  Runs judge-verify.sh
│              │  Compares check results to baseline
│              │  Posts result as Plane comment
│              │  If acceptance_criteria met → closes issue
│              │  If not → reopens, labels "failed-verification"
└──────┬───────┘
       │ issue closed with "verified-complete"
       ▼
┌──────────────┐
│  COMPLIANCE- │  Updates SSP for affected controls
│  SCRIBE      │  Cannot mark "implemented" if check shows FAIL/WARN
│              │  Cannot mark "implemented" if no strong check exists
│              │  Branch: scribe/post-issue-{ID}
│              │  Commit references issue ID + Judge timestamp
└──────────────┘
       │
       │ (next daily cycle)
       ▼
┌──────────────┐
│  RECONCILE   │  Syncs ALL artifacts with latest check results
│  AGENT       │  Catches anything SCRIBE missed or got wrong
│              │  Kills zombie metrics
│              │  Detects regressions
│              │  One commit, structured message
└──────────────┘
```

### 6.5 Assertion Rules (All Agents)

- Do not write "implemented", "complete", "fixed", or "resolved"
  unless you ran a verification command and saw passing output
  this session
- If you cannot verify, write "UNVERIFIED — requires Judge run"
- A wrong answer that says UNVERIFIED is better than a confident
  wrong answer

### 6.6 Blocker Protocol

When an agent hits a problem outside its scope:
1. Do not loop. Do not try to fix things outside your scope.
2. Create a child issue in Plane with: what, where, which role
3. Comment on your issue: "BLOCKED — child issue {ID} created"
4. Write AGENT-STATE.md with blocked status
5. Stop cleanly.

---

## 7. Communication Architecture

### 7.1 Channels

| Channel | Direction | Purpose |
|---------|-----------|---------|
| Plane | Two-way (async) | All agent ↔ human communication. Issues, comments, approvals. |
| ntfy | One-way (agent → Jim) | Pager. Gets attention. Links to Plane issue. |
| Git PRs | One-way (agent → review) | All code/config/doc changes. |
| AGENT-STATE.md | One-way (agent → next agent) | Session handoff. What was done, what's blocked, what's next. |

### 7.2 ntfy Priority Levels

| Priority | Meaning | Examples |
|----------|---------|---------|
| 5 (urgent) | Platform at risk | Vault sealed, multiple services down, security alert from internal IP |
| 4 (high) | Needs attention soon | Fix failed, PR needs review, compliance regression >= 5 checks |
| 3 (default) | Informational action | Fix applied, new issue discovered, routine escalation |
| 2 (low) | Heartbeat | Cycle clean (max once/hour) |

### 7.3 Plane Issue Template (Created by PLANNER)

```markdown
## Title: [FAMILY-CONTROL] Short description

## Description
What's wrong, what's the expected state, what evidence exists.

## Acceptance Criteria
- [ ] `nist-compliance-check.sh` check `{check_name}` returns PASS
- [ ] ArgoCD app `{app}` shows Synced + Healthy
- [ ] (other machine-verifiable conditions)

## Check Strength
Check `{check_name}` is classified as: {strong|moderate|weak}
If weak: this fix improves infrastructure but cannot upgrade SSP
status to "implemented" without a stronger check.

## Modifies Files
- overwatch-gitops/apps/{app}/values.yaml
- (exact paths — WORKER cannot touch anything else)

## Blocked By
- (other issue IDs, or "none")

## Assigned To
- {sentinel-agent | WORKER}
```

---

## 8. Bootstrap Sequence

### 8.1 One-Time Setup (Jim runs once)

The bootstrap prompt (separate document) executes these phases:

| Phase | What | Produces |
|-------|------|---------|
| 0 | Honest inventory — what actually exists right now | /tmp/bootstrap-inventory.md |
| 1 | Agent role definitions | ~/overwatch/agent-roles.md |
| 2 | AGENT-STATE template | ~/overwatch/AGENT-STATE-TEMPLATE.md |
| 3 | CLAUDE.md updates in all repos | Coordination rules appended |
| 4 | Judge verification script | ~/overwatch/scripts/judge-verify.sh |
| 5 | Validation report | ~/overwatch/BOOTSTRAP-RESULT.md |

### 8.2 Bootstrap Preconditions

**Hard stops (bootstrap aborts if these fail):**
- Vault unsealed — the entire auth chain depends on Vault. Agents
  pull credentials from Vault via AppRole. If Vault is sealed,
  sentinel-agent is blind, Tier 2 has no API tokens, the compliance
  check can't reach Wazuh, and no agent can authenticate to Forgejo
  or Plane. Unseal Vault before running bootstrap. There is no
  workaround.

**Soft preconditions (bootstrap records and continues):**
- Forgejo repos accessible from iac-control
- Plane CE reachable (bootstrap creates role definitions without
  Plane, but cannot verify issue creation)
- nist-compliance-check.sh runnable (bootstrap records current state
  if available, marks UNVERIFIED if not)

If a soft precondition fails, bootstrap records what failed and why,
marks affected outputs as UNVERIFIED, and continues. It does not
assert success for things it couldn't verify.

### 8.3 Post-Bootstrap: First PLANNER Run

After bootstrap, Jim pastes the PLANNER prompt (generated as the
last step of Phase 5). The PLANNER:
1. Reads BOOTSTRAP-RESULT.md
2. Reads latest compliance check output
3. Reads check-strength.yaml
4. Creates Plane issues for the next sprint
5. Assigns operational issues to sentinel-agent
6. Assigns Git-change issues to WORKER

From this point, the system runs autonomously with Jim reviewing
PRs and responding to ntfy notifications.

---

## 9. Graduated Trust Model

| Phase | Duration | Sentinel Agent | WORKER PRs | Auto-merge | SCRIBE |
|-------|----------|---------------|-----------|------------|--------|
| 1 | 2 weeks | Tier 2 only | No | No | Manual only |
| 2 | 2 weeks | Tier 2 + 3 (PR) | Yes (Jim merges) | No | After Judge |
| 3 | Ongoing | Full authority | Yes (Jim merges) | Low-risk only | After Judge |
| 4 | Earned | Full authority | Yes | CI-gated auto | After Judge |

Phase 4 is the thesis fully realized: agents maintain infrastructure
through Git, CI gates validate, compliance framework measures,
JUDGE verifies, SCRIBE updates docs, reconcile agent enforces
consistency, and Jim reviews the audit trail.

---

## 10. Failure Modes and Mitigations

### 10.1 Failures This Architecture Prevents

| Failure Mode | Root Cause (from 2026-03-18 audit) | Prevention |
|---|---|---|
| Zombie metrics | Stale 64-65% in current-state.md for 5+ weeks | Reconcile agent overwrites every cycle |
| SSP overwrite | Agent bf9f8df destroyed status-honesty fix 26 min later | COMPLIANCE-SCRIBE exclusive ownership, WORKER cannot touch SSP |
| Multi-source contradiction | OSCAL says 28, markdown says 185, gap analysis says 100 | Single pipeline from check results to all artifacts |
| Trivially-true inflation | 10 checks always pass, inflate rate | check-strength.yaml prevents weak PASS from upgrading to "implemented" |
| Gap language persists | 9 "implemented" controls admit gaps in their own description | Reconcile agent scans and downgrades on every run |
| Self-verified work | Agents close their own issues claiming "fixed" | JUDGE closes issues; agents say "ready for Judge" |
| WARN loophole | 5 persistent WARNs don't affect COMPLIANT status | Reconcile agent maps WARN → "partial" in SSP |
| Script bug propagation | post-session.sh grep mismatch produced false zeros for a month | Judge uses JSON output (jq), not text grep |

### 10.2 Failures This Architecture Does NOT Prevent

| Risk | Why | Mitigation |
|---|---|---|
| LLM generates wrong manifest | Frontier models still make mistakes | CI gates (Kyverno Enforce, Trivy, Gitleaks) catch most; Jim reviews PRs |
| Second-order cascading failure | Correct change breaks unmodeled dependency (Harbor/PostgreSQL incident) | Wazuh + ArgoCD health monitoring detect post-merge; JUDGE catches regressions |
| Vault sealed during agent cycle | Hypervisor reboot, no auto-unseal | sentinel-agent detects and fires ntfy priority 5 immediately |
| Check script has a bug | Script is ground truth — if it's wrong, everything downstream is wrong | Jim is sole modifier of the script; no agent touches it |
| PLANNER creates bad issues | Acceptance criteria reference wrong checks | PLANNER reads check-strength.yaml; Jim reviews issue backlog |

---

## 11. BSides Angle

This architecture is the experiment. The data it produces is the talk.

### 11.1 Metrics Generated Automatically

All metrics below are emitted as structured events to
`research-log.jsonl` (see Section 13) and published weekly to the
research site.

| Metric | Source | Frequency |
|--------|--------|-----------|
| Compliance pass rate over time | score-history.md | Daily |
| Agent action success/failure rate | sentinel-agent log | Every 5 min |
| Time from regression to detection | reconcile agent log | Daily |
| Time from detection to fix | Plane issue timestamps | Per issue |
| PRs generated vs merged vs rejected | Forgejo API | Per PR |
| Check strength distribution | check-strength.yaml | Static (manual updates) |
| JUDGE pass/fail rate | judge-verify.sh output | Per issue |
| Zombie metrics killed | reconcile agent log | Daily |
| SSP overwrites detected and corrected | reconcile agent log | Daily |
| Ollama fallback frequency | sentinel-agent log | Per cycle |

### 11.2 The Core Thesis, Restated

AI agents can build and maintain real infrastructure. But they cannot
reliably verify their own work. The 64% vs 24-26% compliance theater
gap — discovered on this platform, documented in the 2026-03-18
audit — proves that agent self-assessment diverges from deterministic
measurement.

This architecture's response: don't fix the agents, fix the system.
Separate the builder from the verifier. Make the deterministic script
the single source of truth. Let agents propose, let tools verify, let
docs auto-reconcile. The human reviews the trail, not every step.

Nobody else is publicly running this experiment on real hardware with
a formal compliance framework measuring the results.

### 11.3 What Changed Because of the Audit

| Before (pre-2026-03-18) | After (this architecture) |
|---|---|
| Any agent writes to any file | Exclusive artifact ownership |
| Agents close their own issues | JUDGE closes issues |
| Compliance metrics persist indefinitely | Reconcile agent overwrites every 24h |
| Weak checks inflate compliance rate | check-strength.yaml prevents inflation |
| SSP overwrite goes undetected for weeks | Next reconcile cycle corrects it |
| "64-65%" lives in 15+ files | Zombie metrics killed automatically |
| Agent work sessions have no handoff | AGENT-STATE.md required at session end |

---

## 12. Deployment Checklist

### 12.1 Prerequisites

- [ ] **HARD STOP** — Vault unsealed with AppRole configured for sentinel-agent
- [ ] Plane CE accessible with API token in Vault
- [ ] Forgejo repos accessible from iac-control (sentinel-iac, overwatch, overwatch-gitops, compliance-vault)
- [ ] ntfy deployed (self-hosted or ntfy.sh)
- [ ] nist-compliance-check.sh running on daily cron
- [ ] Python 3.11+ on iac-control with venv
- [ ] Ollama running on workstation (6800 XT / ROCm) — optional, Tier 2 degrades to rules-only
- [ ] Claude API key (for Tier 3 only — optional for Phase 1)
- [ ] Research site repo created (haist.farm or dedicated)

### 12.2 Deployment Order

| Step | What | Where |
|------|------|-------|
| 1 | **Unseal Vault** — everything depends on this | Vault |
| 2 | Run bootstrap prompt (Phase 0-5) | iac-control via Claude Code |
| 3 | Create Vault AppRole + scoped policy | Vault |
| 4 | Create sentinel-agent system user | iac-control |
| 5 | Deploy sentinel-agent + check-strength.yaml to /opt/sentinel-agent | iac-control |
| 6 | Enable sentinel-agent.timer (start at 15 min intervals) | systemd |
| 7 | Deploy compliance-reconcile.service (After=nist-compliance-check) | systemd |
| 8 | Create Plane project, agent user, labels | Plane CE |
| 9 | Create research-log.jsonl and research-log-cursor.txt | /var/log/sentinel-agent/ |
| 10 | Deploy research-publisher weekly timer | systemd |
| 11 | Run PLANNER prompt to populate first sprint | Claude Code |
| 12 | Monitor for 2 weeks (Phase 1 trust) | Jim reviews all PRs |

### 12.3 Labels (Plane)

| Label | Meaning |
|-------|---------|
| sentinel-agent | Assigned to infrastructure agent |
| agent-created | Issue discovered by an agent |
| agent-generated | PR created by an agent |
| verified-complete | Closed by JUDGE after verification |
| failed-verification | JUDGE found acceptance criteria not met |
| blocked | Waiting on dependency |
| waiting-on-human | Needs Jim's input |

---

## 13. Research Data Pipeline

### 13.1 Problem

The architecture generates metrics (Section 11.1) but has no pipeline
for turning operational data into publishable research artifacts. The
gap between "platform that produces data" and "research operation that
publishes findings" is bridged here.

### 13.2 Research Event Model

Every significant platform event is a potential research data point.
The reconcile agent and sentinel-agent both emit structured events to
a shared append-only log.

**File:** `/var/log/sentinel-agent/research-log.jsonl`

Each line is a JSON object with a fixed schema:

```json
{
  "timestamp": "2026-03-18T06:05:12Z",
  "event_type": "compliance_reconcile",
  "source": "reconcile-agent",
  "data": {
    "pass_count": 120,
    "fail_count": 0,
    "warn_count": 5,
    "total_checks": 125,
    "coverage_pct": 53.5,
    "strong_evidence_pct": 31.0,
    "ssp_updates": 3,
    "zombie_metrics_killed": 0,
    "regression": false
  },
  "plane_issue": null,
  "narrative": "Daily reconciliation — 3 SSP controls updated (ac-8: planned→implemented, sc-10: planned→partial, si-2: implemented→partial)"
}
```

### 13.3 Event Types

| event_type | Source | When | Key Data |
|------------|--------|------|----------|
| `compliance_reconcile` | Reconcile agent | Daily after check | Pass/fail/warn counts, SSP changes, regressions |
| `compliance_regression` | Reconcile agent | When fail count increases >= 5 | Which checks regressed, delta from previous |
| `agent_fix_attempted` | Sentinel agent | Every Tier 2/3 action | Signal type, diagnosis, action taken, success/failure |
| `agent_fix_verified` | Sentinel agent | After post-action verification | Same as above + verification result |
| `agent_escalated` | Sentinel agent | When escalating to Jim | What, why, what was tried |
| `judge_verdict` | JUDGE | After every PR merge | Issue ID, baseline vs current, pass/fail delta, verdict |
| `scribe_update` | COMPLIANCE-SCRIBE | After Judge verification | Controls updated, old → new status |
| `ssp_overwrite_detected` | Reconcile agent | When SSP status doesn't match last reconcile | Which controls changed, which commit |
| `zombie_metric_killed` | Reconcile agent | When a stale metric is removed | Which metric, which file, how old |
| `ollama_fallback` | Sentinel agent | When Ollama unreachable, rules-only used | Which signal, rules-only result |
| `trust_phase_change` | Manual (Jim) | When graduating trust level | Old phase, new phase, justification |

### 13.4 Who Writes Events

| Agent | Writes Events | Does NOT Write |
|-------|--------------|----------------|
| Sentinel agent | agent_fix_*, agent_escalated, ollama_fallback | compliance_*, judge_*, scribe_* |
| Reconcile agent | compliance_*, ssp_overwrite_*, zombie_* | agent_*, judge_*, scribe_* |
| JUDGE | judge_verdict | Everything else |
| COMPLIANCE-SCRIBE | scribe_update | Everything else |

Each agent appends to the same file. JSONL is append-only and
survives concurrent writes (each line is a single atomic write
if kept under the pipe buffer size).

### 13.5 Research Site Publisher

A separate weekly agent reads research-log.jsonl and produces
a markdown post for the research site repo.

**Trigger:** Weekly systemd timer (Sunday 08:00)

**Process:**

```
research-log.jsonl (accumulated events)
        │
        ▼
┌─────────────────────────────────────────────┐
│  RESEARCH PUBLISHER (weekly)                 │
│                                              │
│  1. Read events since last publish           │
│  2. Aggregate into summary statistics        │
│  3. Identify notable events (regressions,    │
│     trust phase changes, SSP overwrites,     │
│     zombie kills)                            │
│  4. Generate markdown post with:             │
│     - Week summary (pass rate trend,         │
│       agent actions, fixes, escalations)     │
│     - Notable events with context            │
│     - Charts (compliance trend, agent        │
│       action distribution)                   │
│     - Raw data tables                        │
│  5. Commit to research site repo             │
│  6. Update research-log-cursor.txt           │
│     (tracks last published event timestamp)  │
└─────────────────────────────────────────────┘
        │
        ▼
  research site repo (haist.farm or dedicated)
        │
        ▼
  Static site build (Hugo / Jekyll / plain md)
```

**Post template:**

```markdown
---
title: "Week of {DATE}: {HEADLINE}"
date: {DATE}
tags: [overwatch, compliance, autonomous-ops]
---

## Platform State

- Compliance: {PASS}/{TOTAL} ({RATE}%) — {UP/DOWN/STABLE} from last week
- Strong evidence coverage: {STRONG}%
- Agent actions this week: {COUNT} ({SUCCESS} successful, {FAIL} failed, {ESCALATE} escalated)

## Notable Events

{For each notable event: what happened, what the agent did, what it means}

## Compliance Trend

{Inline chart or table: daily pass rates for the week}

## Agent Activity

{Table: actions by type, success rate, avg time to fix}

## Raw Data

<details>
<summary>Full event log ({COUNT} events)</summary>

{JSONL events for the week, formatted}

</details>
```

### 13.6 What This Produces for BSides

The weekly posts are the longitudinal dataset. Over the weeks between
now and BSides Fort Wayne (June 6), the research site accumulates:

- Compliance trend from bootstrap to steady state
- Agent success/failure rates improving (or not) over time
- Concrete examples of compliance theater caught and corrected
  (zombie metrics, SSP overwrites, weak check inflation)
- Trust phase graduations with before/after metrics
- The gap between "what agents claim" and "what tools measure"
  tracked week over week

The talk becomes: "Here's what happened when we let AI agents run
real infrastructure for N weeks. Here's the data. Here's what broke.
Here's what the compliance framework caught that the agents didn't."

The data exists because the architecture produces it as a side
effect of normal operation — not because someone ran an audit.

---

## Document Control

| Version | Date | Changes |
|---------|------|---------|
| 1.0 | 2026-03-18 | Initial architecture — unified sentinel-agent, compliance-reconcile, multi-agent coordination |
| 1.1 | 2026-03-18 | Fixed: LLM routing fallback for workstation offline, reconcile trigger (separate timer not ExecStartPost), Vault as hard stop for bootstrap, added research data pipeline (Section 13) |
