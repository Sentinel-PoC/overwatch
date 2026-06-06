# Methodology

## Audit approach

The Overwatch L1-L7 architecture audit uses a **live-data, code-driven** approach.
Rather than manually documenting the architecture once and letting it drift,
`overwatch-gen` queries authoritative sources each run and regenerates the vault.

This means:
- The vault always reflects current state (as of last run timestamp)
- Discrepancies between expected and actual state are surfaced as gaps
- The audit trail is in git history on the `vault-autogen` branch

## OSI layer scope

| Layer | Scope |
|-------|-------|
| L1 Physical | Physical hosts, Proxmox nodes, NIC inventory, power |
| L2 Data Link | VLANs, Unifi switch config, MAC tables, spanning tree |
| L3 Network | IP subnets, routing, firewall rules (Unifi/OPNsense) |
| L4 Transport | Open ports, service listeners, TLS endpoints |
| L5 Session | Active sessions, Vault leases, K8s service accounts |
| L6 Presentation | TLS certificates, cipher suites, encoding config |
| L7 Application | Services, deployments, ingress rules, API endpoints |

## Determinism contract

All generated files (except `00-meta/generated-at.md`) must be byte-identical
across runs when the underlying platform state has not changed.

This is enforced by:
1. `lib/determinism.sorted_json()` — JSON with sorted keys, 2-space indent
2. `lib/render.write_deterministic()` — no-op if content unchanged
3. Stable node ordering in canvas files (sorted by node id)
4. No embedded timestamps in generated content (only in `generated-at.md`)

## Verification

Run `git diff vault-autogen` after a regeneration run to see what changed.
Only `generated-at.md` should change between runs on a stable platform.
Any other diff indicates a platform state change — investigate before merging.
