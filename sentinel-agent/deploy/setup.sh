#!/usr/bin/env bash
# setup.sh — Deploy sentinel-agent to iac-control
# Run as root on iac-control (or via Ansible)
#
# Usage: sudo bash setup.sh
#
# Prerequisites:
#   - Vault unsealed with AppRole auth enabled
#   - Python 3.11+ available
#   - This script must be run from the sentinel-agent/deploy/ directory

set -euo pipefail

AGENT_DIR="/opt/sentinel-agent"
LOG_DIR="/var/log/sentinel-agent"
AGENT_USER="sentinel-agent"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
SOURCE_DIR="$(dirname "$SCRIPT_DIR")"

echo "=== sentinel-agent deployment ==="
echo "Source: $SOURCE_DIR"
echo "Target: $AGENT_DIR"

# 1. Create system user
if ! id "$AGENT_USER" &>/dev/null; then
    useradd --system --home-dir "$AGENT_DIR" --shell /usr/sbin/nologin "$AGENT_USER"
    echo "Created user: $AGENT_USER"
else
    echo "User exists: $AGENT_USER"
fi

# 2. Create directories
mkdir -p "$AGENT_DIR" "$LOG_DIR"
chown "$AGENT_USER:$AGENT_USER" "$LOG_DIR"

# 3. Copy agent code
echo "Copying agent files..."
rsync -a --exclude='deploy/' --exclude='tests/' --exclude='__pycache__/' \
    "$SOURCE_DIR/" "$AGENT_DIR/"
chown -R "$AGENT_USER:$AGENT_USER" "$AGENT_DIR"

# 4. Create Python venv
echo "Creating Python venv..."
python3 -m venv "$AGENT_DIR/.venv"
"$AGENT_DIR/.venv/bin/pip" install -q -r "$AGENT_DIR/requirements.txt"
echo "Installed: $("$AGENT_DIR/.venv/bin/pip" list --format=freeze | wc -l) packages"

# 5. Install systemd units
echo "Installing systemd units..."
cp "$SCRIPT_DIR/sentinel-agent.service" /etc/systemd/system/
cp "$SCRIPT_DIR/sentinel-agent.timer" /etc/systemd/system/
systemctl daemon-reload

# 6. Vault AppRole setup instructions
echo ""
echo "=== MANUAL STEPS REQUIRED ==="
echo ""
echo "1. Create Vault policy and AppRole:"
echo "   vault policy write sentinel-agent-policy $SCRIPT_DIR/vault-policy.hcl"
echo "   vault write auth/approle/role/sentinel-agent \\"
echo "     token_policies=sentinel-agent-policy \\"
echo "     token_ttl=5m token_max_ttl=10m secret_id_ttl=0"
echo ""
echo "2. Get role_id and secret_id:"
echo "   vault read auth/approle/role/sentinel-agent/role-id"
echo "   vault write -f auth/approle/role/sentinel-agent/secret-id"
echo ""
echo "3. Write credential files:"
echo "   echo '<role_id>' > $AGENT_DIR/.vault-role-id"
echo "   echo '<secret_id>' > $AGENT_DIR/.vault-secret-id"
echo "   chown $AGENT_USER:$AGENT_USER $AGENT_DIR/.vault-role-id $AGENT_DIR/.vault-secret-id"
echo "   chmod 400 $AGENT_DIR/.vault-role-id $AGENT_DIR/.vault-secret-id"
echo ""
echo "4. Copy kubeconfig for sentinel-agent:"
echo "   # IMPORTANT: use /opt/sentinel-agent/.kube/ not /home/sentinel-agent/.kube/"
echo "   # ProtectHome=true in the systemd service blocks /home/* access (OPS-215)"
echo "   mkdir -p $AGENT_DIR/.kube"
echo "   cp /home/ubuntu/.kube/config $AGENT_DIR/.kube/config"
echo "   chown -R $AGENT_USER:$AGENT_USER $AGENT_DIR/.kube"
echo "   chmod 700 $AGENT_DIR/.kube"
echo "   chmod 600 $AGENT_DIR/.kube/config"
echo ""
echo "5. Test dry-run:"
echo "   sudo -u $AGENT_USER $AGENT_DIR/.venv/bin/python3 $AGENT_DIR/agent.py --config $AGENT_DIR/config.yaml --dry-run"
echo ""
echo "6. Enable and start timer:"
echo "   systemctl enable sentinel-agent.timer"
echo "   systemctl start sentinel-agent.timer"
echo ""
echo "=== Deployment complete (pending manual steps) ==="
