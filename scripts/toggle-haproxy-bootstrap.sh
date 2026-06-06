#!/bin/bash
set -euo pipefail

# toggle-haproxy-bootstrap.sh — Enable or disable bootstrap backend in HAProxy
# Used during OKD cluster rebuild to add/remove bootstrap node from LB
#
# Usage:
#   ./toggle-haproxy-bootstrap.sh enable   # Uncomment bootstrap server lines
#   ./toggle-haproxy-bootstrap.sh disable  # Comment out bootstrap server lines
#   ./toggle-haproxy-bootstrap.sh status   # Show current state

HAPROXY_CFG="/etc/haproxy/haproxy.cfg"
BOOTSTRAP_IP="10.0.0.220"
LOG_FILE="/var/log/okd-rebuild.log"

log() {
    local msg
    msg="[$(date -u +%Y-%m-%dT%H:%M:%SZ)] [haproxy-toggle] $*"
    echo "$msg"
    echo "$msg" >> "$LOG_FILE" 2>/dev/null || true
}

check_root() {
    if [[ $EUID -ne 0 ]]; then
        echo "ERROR: This script must be run as root (use sudo)"
        exit 1
    fi
}

status() {
    echo "=== HAProxy Bootstrap Status ==="
    if grep -qP "^\s+server bootstrap\s+" "$HAPROXY_CFG" 2>/dev/null; then
        echo "Bootstrap is ENABLED (active in load balancer)"
        grep "server bootstrap" "$HAPROXY_CFG"
    elif grep -qP "^\s+#\s*server bootstrap\s+" "$HAPROXY_CFG" 2>/dev/null; then
        echo "Bootstrap is DISABLED (commented out)"
        grep "bootstrap" "$HAPROXY_CFG"
    else
        echo "Bootstrap entries NOT FOUND in $HAPROXY_CFG"
        echo "Will need to add them."
    fi
}

enable_bootstrap() {
    check_root
    log "Enabling bootstrap node ($BOOTSTRAP_IP) in HAProxy..."

    # Check if already enabled
    if grep -qP "^\s+server bootstrap\s+" "$HAPROXY_CFG" 2>/dev/null; then
        log "Bootstrap is already enabled"
        return 0
    fi

    # Backup current config
    cp "$HAPROXY_CFG" "${HAPROXY_CFG}.bak.$(date +%s)"

    # Check if commented-out bootstrap lines exist
    if grep -qP "^\s+#\s*server bootstrap\s+" "$HAPROXY_CFG"; then
        # Uncomment existing lines
        sed -i -E 's/^(\s+)#\s*(server bootstrap)/\1\2/' "$HAPROXY_CFG"
        log "Uncommented existing bootstrap entries"
    else
        # Add bootstrap entries to API and MCS backends
        sed -i "/^backend okd4_api_backend/,/^$/{
            /mode tcp/a\\    server bootstrap ${BOOTSTRAP_IP}:6443 check
        }" "$HAPROXY_CFG"
        sed -i "/^backend okd4_machine_config_backend/,/^$/{
            /mode tcp/a\\    server bootstrap ${BOOTSTRAP_IP}:22623 check
        }" "$HAPROXY_CFG"
        log "Added new bootstrap entries to API and MCS backends"
    fi

    # Validate and reload
    if haproxy -c -f "$HAPROXY_CFG" 2>/dev/null; then
        systemctl reload haproxy
        log "HAProxy config valid — reloaded successfully"
    else
        log "ERROR: HAProxy config validation failed! Restoring backup..."
        cp "${HAPROXY_CFG}.bak."* "$HAPROXY_CFG" 2>/dev/null
        systemctl reload haproxy
        exit 1
    fi

    status
}

disable_bootstrap() {
    check_root
    log "Disabling bootstrap node ($BOOTSTRAP_IP) in HAProxy..."

    # Check if already disabled
    if ! grep -qP "^\s+server bootstrap\s+" "$HAPROXY_CFG" 2>/dev/null; then
        log "Bootstrap is already disabled (or not present)"
        return 0
    fi

    # Backup current config
    cp "$HAPROXY_CFG" "${HAPROXY_CFG}.bak.$(date +%s)"

    # Comment out bootstrap lines
    sed -i -E 's/^(\s+)(server bootstrap)/\1# \2/' "$HAPROXY_CFG"
    log "Commented out bootstrap entries"

    # Validate and reload
    if haproxy -c -f "$HAPROXY_CFG" 2>/dev/null; then
        systemctl reload haproxy
        log "HAProxy config valid — reloaded successfully"
    else
        log "ERROR: HAProxy config validation failed! Restoring backup..."
        cp "${HAPROXY_CFG}.bak."* "$HAPROXY_CFG" 2>/dev/null
        systemctl reload haproxy
        exit 1
    fi

    status
}

case "${1:-}" in
    enable)
        enable_bootstrap
        ;;
    disable)
        disable_bootstrap
        ;;
    status)
        status
        ;;
    *)
        echo "Usage: $0 {enable|disable|status}"
        echo ""
        echo "  enable   - Add bootstrap node to HAProxy backends (API + MCS)"
        echo "  disable  - Remove bootstrap node from HAProxy backends"
        echo "  status   - Show current bootstrap state"
        exit 1
        ;;
esac
