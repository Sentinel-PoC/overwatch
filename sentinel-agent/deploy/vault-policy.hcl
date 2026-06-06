# Vault policy for sentinel-agent AppRole
# Path: sentinel-agent-policy
#
# Grants read-only access to secrets needed for the agent cycle.
# Create with:
#   vault policy write sentinel-agent-policy vault-policy.hcl
#   vault write auth/approle/role/sentinel-agent \
#     token_policies="sentinel-agent-policy" \
#     token_ttl=5m \
#     token_max_ttl=10m \
#     secret_id_ttl=0 \
#     token_num_uses=0

# Plane API key
path "secret/data/plane/api-key" {
  capabilities = ["read"]
}

# Forgejo token (for PR creation)
path "secret/data/forgejo" {
  capabilities = ["read"]
}

# Wazuh API password
path "secret/data/wazuh" {
  capabilities = ["read"]
}

# ArgoCD token
path "secret/data/argocd" {
  capabilities = ["read"]
}

# Claude API key (for Tier 3)
path "secret/data/claude" {
  capabilities = ["read"]
}

# Self-check: agent can read its own policy
path "sys/policies/acl/sentinel-agent-policy" {
  capabilities = ["read"]
}

# Token self-lookup (agent checks its own TTL)
path "auth/token/lookup-self" {
  capabilities = ["read"]
}
