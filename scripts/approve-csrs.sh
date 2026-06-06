#!/bin/bash
set -euo pipefail

# approve-csrs.sh — Automatically approve pending CSRs during OKD cluster bootstrap
#
# Usage:
#   ./approve-csrs.sh              # Run until no pending CSRs for 5 minutes
#   ./approve-csrs.sh --once       # Approve current pending CSRs and exit
#   ./approve-csrs.sh --timeout 10 # Custom timeout in minutes (default: 5)

KUBECONFIG="${KUBECONFIG:-$HOME/overwatch-repo/auth/kubeconfig}"
export KUBECONFIG

TIMEOUT_MINUTES="${2:-5}"
LOG_FILE="/var/log/okd-rebuild.log"
INTERVAL=30
MAX_IDLE=$((TIMEOUT_MINUTES * 60 / INTERVAL))

log() {
    local msg
    msg="[$(date -u +%Y-%m-%dT%H:%M:%SZ)] [csr-approver] $*"
    echo "$msg"
    echo "$msg" >> "$LOG_FILE" 2>/dev/null || true
}

approve_pending() {
    local pending
    pending=$(oc get csr 2>/dev/null | grep -c Pending || true)

    if [[ "$pending" -gt 0 ]]; then
        log "Found $pending pending CSR(s) — approving..."
        oc get csr -o name 2>/dev/null | while read -r csr; do
            local status
            status=$(oc get "$csr" -o 'jsonpath={.status.conditions[0].type}' 2>/dev/null || echo "Pending")
            if [[ "$status" != "Approved" ]]; then
                if oc adm certificate approve "$csr" 2>/dev/null; then
                    log "Approved: $csr"
                else
                    log "Failed to approve: $csr"
                fi
            fi
        done
        return 0
    else
        return 1
    fi
}

# Check connectivity
if ! oc whoami &>/dev/null; then
    log "ERROR: Cannot connect to cluster. Check KUBECONFIG=$KUBECONFIG"
    exit 1
fi

log "Starting CSR auto-approval (timeout: ${TIMEOUT_MINUTES}m, interval: ${INTERVAL}s)"

case "${1:-}" in
    --once)
        approve_pending || log "No pending CSRs found"
        exit 0
        ;;
    --timeout)
        # timeout value is in $2, already captured above
        ;;
    "")
        # default loop mode
        ;;
    *)
        echo "Usage: $0 [--once | --timeout <minutes>]"
        exit 1
        ;;
esac

idle_count=0
total_approved=0

while true; do
    if approve_pending; then
        idle_count=0
        ((total_approved++))
    else
        ((idle_count++))
        if [[ $idle_count -ge $MAX_IDLE ]]; then
            log "No pending CSRs for ${TIMEOUT_MINUTES} minutes — exiting (total rounds: $total_approved)"
            break
        fi
        log "No pending CSRs (idle $idle_count/$MAX_IDLE) — waiting ${INTERVAL}s..."
    fi
    sleep "$INTERVAL"
done

log "CSR auto-approval complete"
