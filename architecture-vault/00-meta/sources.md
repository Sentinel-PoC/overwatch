# Data Sources

## Live data sources queried by overwatch-gen

| Source | Access method | Layer(s) |
|--------|---------------|----------|
| Proxmox VE API | REST via `lib/vault_client` + requests | L1, L2 |
| Unifi Network API | REST (local controller) | L2, L3 |
| Kubernetes API | `kubernetes` Python client + kubeconfig | L4, L5, L7 |
| Vault KV | `lib/vault_client` (AppRole) | L5, L6 |
| OKD / OpenShift API | `oc` CLI + REST | L7 |
| TLS certificate scan | Direct TLS handshake via Python ssl | L6 |

## Credential paths in Vault

All credentials used by overwatch-gen are stored in Vault under the
`claude-automation` AppRole policy scope:

| Secret | Vault path |
|--------|------------|
| Plane API key | `secret/data/plane/api-key` |
| Proxmox API token | `secret/data/proxmox/api-token` |
| Unifi credentials | `secret/data/unifi/credentials` |
| Kubeconfig (OKD) | `secret/data/okd/kubeconfig` |

## Static sources

| Source | Description |
|--------|-------------|
| `infrastructure/` (overwatch repo) | Terraform/Ansible inventory — cross-referenced against live state |
| `sentinel-cache/` | Last-known-good snapshots from sentinel-agent |

## Freshness

Data is queried at generation time. See `generated-at.md` for the timestamp
of the most recent run.

Vault leases expire; if a run fails due to credential expiry, re-issue the
AppRole secret_id via the operator runbook.
