#!/bin/bash
set -euo pipefail

# replace-node.sh — Replace a single OKD master node without full cluster rebuild
#
# CRITICAL: This script is for replacing ONE node in a running cluster.
# It extracts the MCS CA from the live cluster (not from generate_ignition.sh)
# because regenerating ignition creates a NEW PKI that won't match the running cluster.
#
# Usage:
#   ./replace-node.sh --node master-1              # Replace master-1 (interactive)
#   ./replace-node.sh --node master-2 --dry-run    # Dry-run for master-2
#   ./replace-node.sh --list                        # Show node map
#
# Prerequisites:
#   - Running OKD cluster with at least 2 healthy masters
#   - KUBECONFIG with cluster-admin access
#   - Proxmox API access (for VM destroy/create)
#   - Vault root token (for Proxmox credentials)

# ============================================================================
# Configuration
# ============================================================================
REPO_DIR="$HOME/overwatch-repo"
SCRIPTS_DIR="$REPO_DIR/scripts"
AUTH_DIR="$REPO_DIR/auth"
GEN_DIR="$REPO_DIR/overwatch-gen"
WEB_ROOT="/var/www/html"
LOG_FILE="/var/log/okd-rebuild.log"
KUBECONFIG="${KUBECONFIG:-$AUTH_DIR/kubeconfig}"
export KUBECONFIG

CLUSTER_NAME="overwatch"
BASE_DOMAIN="haist.farm"
MCS_URL="https://api-int.${CLUSTER_NAME}.${BASE_DOMAIN}:22623/config/master"

VAULT_ADDR="https://192.168.12.206:8200"
TOFU="/usr/local/bin/tofu"
OC="/usr/local/bin/oc"

# Node map: name -> MAC, IP, Terraform resource, Proxmox node
declare -A NODE_MAC NODE_IP NODE_TF_RESOURCE NODE_PVE_HOST NODE_VMID
NODE_MAC[master-1]="BC:24:11:F1:90:7A"
NODE_MAC[master-2]="BC:24:11:54:EF:CF"
NODE_MAC[master-3]="BC:24:11:EC:8C:91"

NODE_IP[master-1]="10.0.0.221"
NODE_IP[master-2]="10.0.0.222"
NODE_IP[master-3]="10.0.0.223"

NODE_TF_RESOURCE[master-1]="proxmox_virtual_environment_vm.overwatch_node_1"
NODE_TF_RESOURCE[master-2]="proxmox_virtual_environment_vm.overwatch_node_2"
NODE_TF_RESOURCE[master-3]="proxmox_virtual_environment_vm.overwatch_node_3"

NODE_PVE_HOST[master-1]="pve"
NODE_PVE_HOST[master-2]="208-pve2"
NODE_PVE_HOST[master-3]="208-pve2"

NODE_VMID[master-1]="211"
NODE_VMID[master-2]="212"
NODE_VMID[master-3]="213"

DRY_RUN=false
TARGET_NODE=""
VAULT_TOKEN=""

# ============================================================================
# Logging
# ============================================================================
log() {
    local level="$1"; shift
    local msg
    msg="[$(date -u +%Y-%m-%dT%H:%M:%SZ)] [replace-node] [$level] $*"
    echo "$msg"
    echo "$msg" >> "$LOG_FILE" 2>/dev/null || true
}

info()  { log "INFO" "$@"; }
warn()  { log "WARN" "$@"; }
error() { log "ERROR" "$@"; }
step()  { log "STEP" "=== $* ==="; }

# ============================================================================
# Argument Parsing
# ============================================================================
show_usage() {
    echo "Usage: $0 --node <master-1|master-2|master-3> [--dry-run]"
    echo "       $0 --list"
    echo ""
    echo "Options:"
    echo "  --node <name>   Target node to replace"
    echo "  --dry-run       Validate pre-flight checks only"
    echo "  --list          Show node map and exit"
    echo "  --help          Show this help"
}

list_nodes() {
    echo "╔══════════════════════════════════════════════════════════════════════════╗"
    echo "║ Node        MAC                 IP           VM ID  Proxmox Host        ║"
    echo "╠══════════════════════════════════════════════════════════════════════════╣"
    printf "║ %-11s %-19s %-12s %-6s %-19s ║\n" "master-1" "${NODE_MAC[master-1]}" "${NODE_IP[master-1]}" "${NODE_VMID[master-1]}" "${NODE_PVE_HOST[master-1]}"
    printf "║ %-11s %-19s %-12s %-6s %-19s ║\n" "master-2" "${NODE_MAC[master-2]}" "${NODE_IP[master-2]}" "${NODE_VMID[master-2]}" "${NODE_PVE_HOST[master-2]}"
    printf "║ %-11s %-19s %-12s %-6s %-19s ║\n" "master-3" "${NODE_MAC[master-3]}" "${NODE_IP[master-3]}" "${NODE_VMID[master-3]}" "${NODE_PVE_HOST[master-3]}"
    echo "╚══════════════════════════════════════════════════════════════════════════╝"
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --node)
            TARGET_NODE="$2"
            shift 2
            ;;
        --dry-run)
            DRY_RUN=true
            shift
            ;;
        --list)
            list_nodes
            exit 0
            ;;
        --help|-h)
            show_usage
            exit 0
            ;;
        *)
            error "Unknown option: $1"
            show_usage
            exit 1
            ;;
    esac
done

if [[ -z "$TARGET_NODE" ]]; then
    error "No target node specified"
    show_usage
    exit 1
fi

if [[ -z "${NODE_MAC[$TARGET_NODE]:-}" ]]; then
    error "Unknown node: $TARGET_NODE (valid: master-1, master-2, master-3)"
    exit 1
fi

info "Target node: $TARGET_NODE (IP: ${NODE_IP[$TARGET_NODE]}, MAC: ${NODE_MAC[$TARGET_NODE]})"

# ============================================================================
# Vault Authentication
# ============================================================================
step "Vault Authentication"

read -rsp "Enter Vault root token: " VAULT_TOKEN
echo ""

VAULT_HEALTH=$(curl -sk -o /dev/null -w "%{http_code}" \
    -H "X-Vault-Token: $VAULT_TOKEN" \
    "$VAULT_ADDR/v1/sys/health" 2>/dev/null || echo "000")

if [[ "$VAULT_HEALTH" != "200" ]]; then
    error "Vault health check failed (HTTP $VAULT_HEALTH)"
    exit 1
fi
info "Vault token validated"

export VAULT_ADDR VAULT_TOKEN

# Retrieve Proxmox + MinIO credentials
MINIO_CREDS=$(curl -sk -H "X-Vault-Token: $VAULT_TOKEN" \
    "$VAULT_ADDR/v1/secret/data/minio" 2>/dev/null)
AWS_ACCESS_KEY_ID=$(echo "$MINIO_CREDS" | jq -r '.data.data.access_key')
export AWS_ACCESS_KEY_ID
AWS_SECRET_ACCESS_KEY=$(echo "$MINIO_CREDS" | jq -r '.data.data.secret_key')
export AWS_SECRET_ACCESS_KEY

PVE_CREDS=$(curl -sk -H "X-Vault-Token: $VAULT_TOKEN" \
    "$VAULT_ADDR/v1/secret/data/proxmox" 2>/dev/null)
TF_VAR_proxmox_api_token=$(echo "$PVE_CREDS" | jq -r '.data.data.api_token')
export TF_VAR_proxmox_api_token

info "Infrastructure credentials loaded from Vault"

# ============================================================================
# Pre-Flight Checks
# ============================================================================
step "Pre-Flight Checks"

PREFLIGHT_PASS=true

# 1. Cluster connectivity
if ! $OC whoami &>/dev/null; then
    error "Cannot connect to cluster — check KUBECONFIG=$KUBECONFIG"
    PREFLIGHT_PASS=false
else
    info "Cluster connection OK ($(${OC} whoami))"
fi

# 2. Check cluster health — at least 2 masters must be Ready
READY_MASTERS=$($OC get nodes -l node-role.kubernetes.io/master= --no-headers 2>/dev/null | grep -c " Ready" || true)
info "Ready master nodes: $READY_MASTERS/3"
if [[ "$READY_MASTERS" -lt 2 ]]; then
    error "Need at least 2 healthy masters to replace a node (found $READY_MASTERS)"
    PREFLIGHT_PASS=false
fi

# 3. Check etcd health
ETCD_HEALTH=$($OC get etcd cluster -o jsonpath='{.status.conditions[?(@.type=="EtcdMembersAvailable")].status}' 2>/dev/null || echo "Unknown")
info "etcd members available: $ETCD_HEALTH"
if [[ "$ETCD_HEALTH" != "True" ]]; then
    warn "etcd health is not True — proceed with caution"
fi

# 4. Check MCS is reachable
MCS_HEALTH=$(curl -sk -o /dev/null -w "%{http_code}" "https://api-int.${CLUSTER_NAME}.${BASE_DOMAIN}:22623/healthz" 2>/dev/null || echo "000")
info "MCS health endpoint: HTTP $MCS_HEALTH"

# 5. Verify we can extract MCS CA
if $OC get configmap machine-config-server-ca -n openshift-machine-config-operator &>/dev/null; then
    info "MCS CA configmap accessible"
else
    error "Cannot read machine-config-server-ca configmap"
    PREFLIGHT_PASS=false
fi

# 6. Check Terraform state
cd "$REPO_DIR/infrastructure"
if $TOFU init -no-color &>/dev/null; then
    info "Terraform initialized"
else
    error "Terraform init failed"
    PREFLIGHT_PASS=false
fi

# 7. Check nginx
if command -v nginx &>/dev/null; then
    info "nginx available"
else
    error "nginx not found"
    PREFLIGHT_PASS=false
fi

if [[ "$PREFLIGHT_PASS" == "false" ]]; then
    error "Pre-flight checks FAILED"
    exit 1
fi
info "All pre-flight checks PASSED"

if [[ "$DRY_RUN" == "true" ]]; then
    info "DRY RUN complete — all checks passed for replacing $TARGET_NODE"
    exit 0
fi

# ============================================================================
# Confirmation
# ============================================================================
echo ""
echo "╔══════════════════════════════════════════════════════════════╗"
echo "║       SINGLE NODE REPLACEMENT — FINAL CONFIRMATION          ║"
echo "╠══════════════════════════════════════════════════════════════╣"
echo "║ Node:     $TARGET_NODE"
echo "║ IP:       ${NODE_IP[$TARGET_NODE]}"
echo "║ MAC:      ${NODE_MAC[$TARGET_NODE]}"
echo "║ VM ID:    ${NODE_VMID[$TARGET_NODE]}"
echo "║ Proxmox:  ${NODE_PVE_HOST[$TARGET_NODE]}"
echo "║                                                              ║"
echo "║ This will DESTROY and RECREATE this VM.                      ║"
echo "║ The running cluster must have 2 other healthy masters.       ║"
echo "╚══════════════════════════════════════════════════════════════╝"
echo ""
read -rp "Type '$TARGET_NODE' to confirm: " CONFIRM
if [[ "$CONFIRM" != "$TARGET_NODE" ]]; then
    info "Aborted by user"
    exit 0
fi

# ============================================================================
# Step: Extract MCS CA from Running Cluster
# ============================================================================
step "Extract MCS CA from Running Cluster"

MCS_CA_PEM=$($OC get configmap machine-config-server-ca \
    -n openshift-machine-config-operator \
    -o jsonpath='{.data.ca-bundle\.crt}')

if [[ -z "$MCS_CA_PEM" ]]; then
    error "Failed to extract MCS CA"
    exit 1
fi

# Base64-encode CA for ignition format
MCS_CA_B64=$(echo "$MCS_CA_PEM" | base64 -w0)
info "MCS CA extracted and base64-encoded (${#MCS_CA_B64} chars)"

# ============================================================================
# Step: Generate NMState Config for Target Node
# ============================================================================
step "Generate NMState Config"

TARGET_IP="${NODE_IP[$TARGET_NODE]}"
NMSTATE_YAML=$(cat <<NMEOF
interfaces:
  - name: ens18
    type: ethernet
    state: up
    ipv4:
      enabled: true
      address:
        - ip: ${TARGET_IP}
          prefix-length: 24
      dhcp: false
    ipv6:
      enabled: false
dns-resolver:
  config:
    server:
      - 10.0.0.1
routes:
  config:
    - destination: 0.0.0.0/0
      next-hop-address: 10.0.0.1
      next-hop-interface: ens18
NMEOF
)

# Gzip and base64-encode for ignition
NMSTATE_GZ_B64=$(echo "$NMSTATE_YAML" | gzip | base64 -w0)
info "NMState config generated for ${TARGET_IP}"

# ============================================================================
# Step: Build Pointer Ignition File
# ============================================================================
step "Build Pointer Ignition File"

TARGET_MAC_LOWER=$(echo "${NODE_MAC[$TARGET_NODE]}" | tr '[:upper:]' '[:lower:]')
IGN_FILE="$GEN_DIR/master-${TARGET_MAC_LOWER}.ign"

# Build the pointer ignition that references the live cluster's MCS
cat > "$IGN_FILE" << IGNEOF
{
  "ignition": {
    "config": {
      "merge": [
        {
          "source": "${MCS_URL}"
        }
      ]
    },
    "security": {
      "tls": {
        "certificateAuthorities": [
          {
            "source": "data:text/plain;charset=utf-8;base64,${MCS_CA_B64}"
          }
        ]
      }
    },
    "version": "3.2.0"
  },
  "storage": {
    "files": [
      {
        "path": "/etc/nmstate/ens18.yml",
        "contents": {
          "compression": "gzip",
          "source": "data:;base64,${NMSTATE_GZ_B64}"
        },
        "mode": 420
      }
    ]
  },
  "systemd": {
    "units": [
      {
        "contents": "[Unit]\nDescription=QEMU Guest Agent\nAfter=network-online.target\nWants=network-online.target\n\n[Service]\nTimeoutStartSec=0\nExecStartPre=-/usr/bin/podman stop qemu-guest-agent\nExecStartPre=-/usr/bin/podman rm qemu-guest-agent\nExecStartPre=/usr/bin/podman pull docker.io/linuxkit/qemu-ga:v0.8\nExecStart=/usr/bin/podman run --name qemu-guest-agent --rm --privileged --net=host -v /dev:/dev docker.io/linuxkit/qemu-ga:v0.8 /usr/bin/qemu-ga -m virtio-serial -p /dev/virtio-ports/org.qemu.guest_agent.0\nRestart=always\nRestartSec=5\n\n[Install]\nWantedBy=multi-user.target\n",
        "enabled": true,
        "name": "qemu-guest-agent.service"
      }
    ]
  },
  "passwd": {
    "users": []
  }
}
IGNEOF

info "Pointer ignition written to $IGN_FILE"

# Also write a generic master.ign that the iPXE fallback can use
# (Config Server serves MAC-specific files; this is a fallback)
cp "$IGN_FILE" "$GEN_DIR/master.ign"
info "Also copied as master.ign fallback"

# ============================================================================
# Step: Copy Ignition to Web Root
# ============================================================================
step "Copy Ignition to Web Root"

sudo mkdir -p "$WEB_ROOT/ignition"
sudo cp "$IGN_FILE" "$WEB_ROOT/ignition/"
sudo cp "$GEN_DIR/master.ign" "$WEB_ROOT/ignition/master.ign"
sudo chmod 644 "$WEB_ROOT/ignition/"*.ign
info "Ignition files copied to $WEB_ROOT/ignition/"

# ============================================================================
# Step: Start Nginx
# ============================================================================
step "Start Nginx"

if ! systemctl is-active --quiet nginx; then
    sudo systemctl start nginx
fi

HTTP_CHECK=$(curl -s -o /dev/null -w "%{http_code}" "http://10.0.0.1:8080/ignition/master.ign" 2>/dev/null || echo "000")
if [[ "$HTTP_CHECK" == "200" ]]; then
    info "Nginx serving ignition files on port 8080"
else
    error "Nginx check failed (HTTP $HTTP_CHECK)"
    exit 1
fi

# ============================================================================
# Step: Remove Old Node from Cluster (if still present)
# ============================================================================
step "Remove Old Node from Cluster"

# Get the OKD node name for this IP
OKD_NODE_NAME=$($OC get nodes -o wide --no-headers 2>/dev/null | grep "${TARGET_IP}" | awk '{print $1}' || true)

if [[ -n "$OKD_NODE_NAME" ]]; then
    info "Found cluster node: $OKD_NODE_NAME — cordoning and draining..."
    $OC adm cordon "$OKD_NODE_NAME" 2>/dev/null || true
    $OC adm drain "$OKD_NODE_NAME" --ignore-daemonsets --delete-emptydir-data --force --timeout=120s 2>/dev/null || \
        warn "Drain timed out or partially failed — continuing"
    $OC delete node "$OKD_NODE_NAME" 2>/dev/null || true
    info "Node $OKD_NODE_NAME removed from cluster"
else
    warn "Node with IP $TARGET_IP not found in cluster — may already be removed"
fi

# Remove stale etcd member if present
info "Checking for stale etcd member..."
ETCD_POD=$($OC get pods -n openshift-etcd -l app=etcd --no-headers 2>/dev/null | grep Running | head -1 | awk '{print $1}' || true)
if [[ -n "$ETCD_POD" ]]; then
    # List etcd members and find the one matching our target IP
    ETCD_MEMBERS=$($OC exec -n openshift-etcd "$ETCD_POD" -c etcd -- etcdctl member list -w table 2>/dev/null || true)
    STALE_MEMBER_ID=$(echo "$ETCD_MEMBERS" | grep "${TARGET_IP}" | awk -F'|' '{print $2}' | tr -d ' ' || true)
    if [[ -n "$STALE_MEMBER_ID" ]]; then
        info "Removing stale etcd member $STALE_MEMBER_ID..."
        $OC exec -n openshift-etcd "$ETCD_POD" -c etcd -- etcdctl member remove "$STALE_MEMBER_ID" 2>/dev/null || \
            warn "Failed to remove etcd member — may need manual intervention"
    else
        info "No stale etcd member found for ${TARGET_IP}"
    fi
fi

# ============================================================================
# Step: Terraform Destroy + Apply (Single Node)
# ============================================================================
step "Terraform Destroy + Apply ($TARGET_NODE)"

cd "$REPO_DIR/infrastructure"

TF_RESOURCE="${NODE_TF_RESOURCE[$TARGET_NODE]}"

info "Destroying $TF_RESOURCE..."
$TOFU destroy -target="$TF_RESOURCE" -auto-approve -no-color 2>&1 | tee -a "$LOG_FILE"

info "Recreating $TF_RESOURCE..."
$TOFU apply -target="$TF_RESOURCE" -auto-approve -no-color 2>&1 | tee -a "$LOG_FILE"

info "VM recreated — node will PXE boot and pull ignition from MCS"

# ============================================================================
# Step: Approve CSRs
# ============================================================================
step "Approve CSRs"

info "Starting CSR auto-approval (the new node will generate CSRs)..."
bash "$SCRIPTS_DIR/approve-csrs.sh" --timeout 15 &
CSR_PID=$!

# ============================================================================
# Step: Wait for Node to Join
# ============================================================================
step "Wait for Node to Join Cluster"

info "Waiting for node with IP $TARGET_IP to appear in the cluster..."
MAX_WAIT=900  # 15 minutes
ELAPSED=0
while [[ $ELAPSED -lt $MAX_WAIT ]]; do
    NEW_NODE=$($OC get nodes -o wide --no-headers 2>/dev/null | grep "${TARGET_IP}" | awk '{print $1}' || true)
    if [[ -n "$NEW_NODE" ]]; then
        info "Node appeared: $NEW_NODE"
        break
    fi
    sleep 30
    ((ELAPSED+=30))
    info "Waiting... ($ELAPSED/${MAX_WAIT}s)"
done

if [[ -z "${NEW_NODE:-}" ]]; then
    error "Node did not join cluster within ${MAX_WAIT}s"
    warn "Check: console logs on Proxmox, MCS logs, CSRs"
    # Don't exit — let CSR loop continue, operator can check
fi

# Wait for Ready state
if [[ -n "${NEW_NODE:-}" ]]; then
    info "Waiting for node $NEW_NODE to become Ready..."
    $OC wait "node/$NEW_NODE" --for=condition=Ready --timeout=600s 2>/dev/null || \
        warn "Node not Ready after 10 minutes — may still be configuring"
fi

# Stop CSR approval loop
if kill -0 "$CSR_PID" 2>/dev/null; then
    kill "$CSR_PID" 2>/dev/null || true
    wait "$CSR_PID" 2>/dev/null || true
fi

# ============================================================================
# Step: Wait for Cluster Operators to Stabilize
# ============================================================================
step "Wait for Cluster Operators"

info "Waiting for cluster operators to stabilize after node replacement..."
DEGRADED_OPS=1
for i in $(seq 1 30); do
    DEGRADED_OPS=$($OC get clusteroperators --no-headers 2>/dev/null | grep -cE "True\s+True|True\s+\S+\s+True" || true)
    TOTAL_OPS=$($OC get clusteroperators --no-headers 2>/dev/null | wc -l || echo 0)
    if [[ "$DEGRADED_OPS" -eq 0 ]] 2>/dev/null; then
        info "All $TOTAL_OPS cluster operators healthy"
        break
    fi
    info "Operators still settling ($i/30)..."
    sleep 30
done

# ============================================================================
# Step: Stop Nginx
# ============================================================================
step "Stop Nginx"

sudo systemctl stop nginx
info "nginx stopped"

# ============================================================================
# Final Status
# ============================================================================
step "Final Status"

echo ""
$OC get nodes -o wide 2>&1
echo ""
$OC get clusteroperators 2>&1 | head -15
echo ""

echo "╔══════════════════════════════════════════════════════════════╗"
echo "║           NODE REPLACEMENT COMPLETE                          ║"
echo "╠══════════════════════════════════════════════════════════════╣"
echo "║ Node:      $TARGET_NODE"
echo "║ IP:        ${TARGET_IP}"
echo "║ Log:       $LOG_FILE"
echo "╚══════════════════════════════════════════════════════════════╝"
echo ""
echo "=== MANUAL FOLLOW-UP ==="
echo "1. Verify node is Ready: oc get nodes"
echo "2. Check MachineConfigPool: oc get mcp (should show UPDATED=True)"
echo "3. Verify etcd: oc get etcd cluster -o jsonpath='{.status.conditions}' | jq"
echo "4. Check cluster operators: oc get co"
echo "5. Re-register Wazuh agent if needed"
echo "6. Verify Istio sidecar injection on new node pods"
