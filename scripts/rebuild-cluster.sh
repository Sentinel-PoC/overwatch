#!/bin/bash
set -euo pipefail

# rebuild-cluster.sh — Full OKD 4.19 Overwatch Cluster Rebuild Orchestrator
#
# This script orchestrates a complete cluster rebuild from iac-control.
# It handles the full lifecycle from ignition generation through GitOps bootstrap.
#
# Usage:
#   ./rebuild-cluster.sh              # Full rebuild (will prompt for confirmation)
#   ./rebuild-cluster.sh --dry-run    # Validate all pre-flight checks without executing
#
# Prerequisites:
#   - Run from iac-control (192.168.12.210 / 10.0.0.1)
#   - Vault root token (prompted at start)
#   - SCOS artifacts present in ~/overwatch-repo/scos-artifacts/
#   - openshift-install binary at /usr/local/bin/openshift-install
#   - Proxmox API accessible

# ============================================================================
# Configuration
# ============================================================================
REPO_DIR="$HOME/overwatch-repo"
GITOPS_DIR="$HOME/overwatch-gitops"
SCRIPTS_DIR="$REPO_DIR/scripts"
ARTIFACTS_DIR="$REPO_DIR/scos-artifacts"
AUTH_DIR="$REPO_DIR/auth"
WEB_ROOT="/var/www/html"
LOG_FILE="/var/log/okd-rebuild.log"
KUBECONFIG="$AUTH_DIR/kubeconfig"

# Cluster config
CLUSTER_NAME="overwatch"
BASE_DOMAIN="haist.farm"
export API_VIP="10.0.0.1"
export BOOTSTRAP_IP="10.0.0.220"
export MASTER_IPS=("10.0.0.221" "10.0.0.222" "10.0.0.223")

# Infrastructure endpoints
# shellcheck disable=SC2034  # endpoint constants; used by Terraform env and documentation
VAULT_ADDR="https://192.168.12.206:8200"
PROXMOX_API="https://192.168.12.6:8006"
export MINIO_ENDPOINT="http://192.168.12.58:9000"
export GITLAB_URL="http://192.168.12.68"
CONFIG_SERVER="10.0.0.2"

# Tool paths
INSTALLER="/usr/local/bin/openshift-install"
OC="/usr/local/bin/oc"
TOFU="/usr/local/bin/tofu"

# State
DRY_RUN=false
VAULT_TOKEN=""
STEP_COUNT=0

# ============================================================================
# Logging
# ============================================================================
log() {
    local level="$1"; shift
    local msg
    msg="[$(date -u +%Y-%m-%dT%H:%M:%SZ)] [$level] $*"
    echo "$msg"
    echo "$msg" >> "$LOG_FILE" 2>/dev/null || true
}

info()  { log "INFO" "$@"; }
warn()  { log "WARN" "$@"; }
error() { log "ERROR" "$@"; }
step()  { ((STEP_COUNT++)); log "STEP" "=== Step $STEP_COUNT: $* ==="; }

# ============================================================================
# Argument Parsing
# ============================================================================
while [[ $# -gt 0 ]]; do
    case "$1" in
        --dry-run)
            DRY_RUN=true
            info "DRY RUN MODE — no changes will be made"
            shift
            ;;
        --help|-h)
            echo "Usage: $0 [--dry-run] [--help]"
            echo ""
            echo "Options:"
            echo "  --dry-run   Validate pre-flight checks without executing rebuild"
            echo "  --help      Show this help message"
            exit 0
            ;;
        *)
            error "Unknown option: $1"
            exit 1
            ;;
    esac
done

# ============================================================================
# Vault Authentication
# ============================================================================
step "Vault Authentication"

read -rsp "Enter Vault root token: " VAULT_TOKEN
echo ""

info "Validating Vault token against $VAULT_ADDR..."
VAULT_HEALTH=$(curl -sk -o /dev/null -w "%{http_code}" \
    -H "X-Vault-Token: $VAULT_TOKEN" \
    "$VAULT_ADDR/v1/sys/health" 2>/dev/null || echo "000")

if [[ "$VAULT_HEALTH" == "200" ]]; then
    info "Vault is healthy and token is valid"
elif [[ "$VAULT_HEALTH" == "000" ]]; then
    error "Cannot reach Vault at $VAULT_ADDR"
    exit 1
else
    error "Vault returned HTTP $VAULT_HEALTH — token may be invalid"
    exit 1
fi

export VAULT_ADDR VAULT_TOKEN

# Retrieve MinIO credentials from Vault for Terraform backend
info "Retrieving MinIO credentials from Vault..."
MINIO_CREDS=$(curl -sk -H "X-Vault-Token: $VAULT_TOKEN" \
    "$VAULT_ADDR/v1/secret/data/minio" 2>/dev/null)

if echo "$MINIO_CREDS" | jq -e '.data.data' &>/dev/null; then
    AWS_ACCESS_KEY_ID=$(echo "$MINIO_CREDS" | jq -r '.data.data.access_key')
    export AWS_ACCESS_KEY_ID
    AWS_SECRET_ACCESS_KEY=$(echo "$MINIO_CREDS" | jq -r '.data.data.secret_key')
    export AWS_SECRET_ACCESS_KEY
    info "MinIO credentials loaded from Vault"
else
    warn "Could not retrieve MinIO credentials — Terraform state operations may fail"
fi

# Retrieve Proxmox API token from Vault
info "Retrieving Proxmox API token from Vault..."
PVE_CREDS=$(curl -sk -H "X-Vault-Token: $VAULT_TOKEN" \
    "$VAULT_ADDR/v1/secret/data/proxmox" 2>/dev/null)

if echo "$PVE_CREDS" | jq -e '.data.data' &>/dev/null; then
    TF_VAR_proxmox_api_token=$(echo "$PVE_CREDS" | jq -r '.data.data.api_token')
    export TF_VAR_proxmox_api_token
    info "Proxmox API token loaded from Vault"
else
    error "Could not retrieve Proxmox credentials from Vault"
    exit 1
fi

# ============================================================================
# Pre-Flight Checks
# ============================================================================
step "Pre-Flight Checks"

PREFLIGHT_PASS=true

# Check we're on iac-control
info "Checking hostname..."
if [[ "$(hostname)" != "iac-control" ]]; then
    warn "Expected hostname 'iac-control', got '$(hostname)'"
fi

# Check required tools
for tool in "$INSTALLER" "$OC" "$TOFU" jq curl nginx haproxy; do
    if command -v "$tool" &>/dev/null || [[ -x "$tool" ]]; then
        info "Tool found: $tool"
    else
        error "Missing required tool: $tool"
        PREFLIGHT_PASS=false
    fi
done

# Check SCOS artifacts
for artifact in scos-kernel scos-initramfs.img scos-rootfs.img; do
    if [[ -f "$ARTIFACTS_DIR/$artifact" ]]; then
        info "Artifact present: $artifact ($(du -h "$ARTIFACTS_DIR/$artifact" | cut -f1))"
    else
        error "Missing SCOS artifact: $ARTIFACTS_DIR/$artifact"
        PREFLIGHT_PASS=false
    fi
done

# Check install-config.yaml
if [[ -f "$REPO_DIR/install-config.yaml" ]]; then
    info "install-config.yaml present"
else
    error "Missing: $REPO_DIR/install-config.yaml"
    PREFLIGHT_PASS=false
fi

# Check generate_ignition.sh
if [[ -x "$REPO_DIR/generate_ignition.sh" ]]; then
    info "generate_ignition.sh present and executable"
else
    error "Missing or not executable: $REPO_DIR/generate_ignition.sh"
    PREFLIGHT_PASS=false
fi

# Check nginx can serve artifacts
info "Checking nginx status..."
if systemctl is-active --quiet nginx 2>/dev/null; then
    info "nginx is running"
elif systemctl is-enabled --quiet nginx 2>/dev/null; then
    info "nginx is installed but not running (will start during rebuild)"
else
    warn "nginx service status unknown"
fi

# Check DNS resolution
info "Checking DNS resolution..."
for name in "api.${CLUSTER_NAME}.${BASE_DOMAIN}" "api-int.${CLUSTER_NAME}.${BASE_DOMAIN}"; do
    resolved=$(dig +short "$name" @127.0.0.1 2>/dev/null || true)
    if [[ -n "$resolved" ]]; then
        info "DNS OK: $name -> $resolved"
    else
        warn "DNS resolution failed for $name"
    fi
done

# Check Proxmox API
info "Checking Proxmox API..."
PVE_STATUS=$(curl -sk -o /dev/null -w "%{http_code}" "$PROXMOX_API/api2/json/" 2>/dev/null || echo "000")
if [[ "$PVE_STATUS" == "200" ]] || [[ "$PVE_STATUS" == "401" ]]; then
    info "Proxmox API reachable (HTTP $PVE_STATUS)"
else
    error "Proxmox API unreachable at $PROXMOX_API (HTTP $PVE_STATUS)"
    PREFLIGHT_PASS=false
fi

# Check Config Server
info "Checking Config Server (iPXE)..."
CS_STATUS=$(curl -s -o /dev/null -w "%{http_code}" "http://${CONFIG_SERVER}/" 2>/dev/null || echo "000")
if [[ "$CS_STATUS" == "200" ]]; then
    info "Config Server reachable at $CONFIG_SERVER"
else
    warn "Config Server unreachable (HTTP $CS_STATUS) — iPXE boot may fail"
fi

# Check HAProxy
info "Checking HAProxy status..."
if systemctl is-active --quiet haproxy 2>/dev/null; then
    info "HAProxy is running"
else
    error "HAProxy is not running"
    PREFLIGHT_PASS=false
fi

if [[ "$PREFLIGHT_PASS" == "false" ]]; then
    error "Pre-flight checks FAILED — resolve issues above before proceeding"
    exit 1
fi

info "All pre-flight checks PASSED"

# ============================================================================
# Dry Run Exit
# ============================================================================
if [[ "$DRY_RUN" == "true" ]]; then
    info "DRY RUN complete — all pre-flight checks passed"
    info "Remove --dry-run flag to execute the rebuild"
    exit 0
fi

# ============================================================================
# Confirmation
# ============================================================================
echo ""
echo "╔══════════════════════════════════════════════════════════════╗"
echo "║           OKD CLUSTER REBUILD — FINAL CONFIRMATION          ║"
echo "╠══════════════════════════════════════════════════════════════╣"
echo "║ This will DESTROY and REBUILD the entire Overwatch cluster. ║"
echo "║                                                              ║"
echo "║ Cluster: $CLUSTER_NAME.$BASE_DOMAIN                         ║"
echo "║ Nodes:   1 bootstrap + 3 masters                            ║"
echo "║ Method:  Terraform destroy + apply + ignition boot           ║"
echo "║                                                              ║"
echo "║ This action is IRREVERSIBLE.                                 ║"
echo "╚══════════════════════════════════════════════════════════════╝"
echo ""
read -rp "Type 'REBUILD' to confirm: " CONFIRM
if [[ "$CONFIRM" != "REBUILD" ]]; then
    info "Aborted by user"
    exit 0
fi

# ============================================================================
# Step: Backup Current State
# ============================================================================
step "Backup Current State"

BACKUP_DIR="$REPO_DIR/backups/$(date +%Y%m%d-%H%M%S)"
mkdir -p "$BACKUP_DIR"

# Backup current ignition files
if [[ -f "$REPO_DIR/bootstrap.ign" ]]; then
    cp "$REPO_DIR/bootstrap.ign" "$BACKUP_DIR/"
    cp "$REPO_DIR/master.ign" "$BACKUP_DIR/"
    cp "$REPO_DIR/worker.ign" "$BACKUP_DIR/"
    info "Backed up existing ignition files to $BACKUP_DIR"
fi

# Backup current kubeconfig
if [[ -f "$KUBECONFIG" ]]; then
    cp "$KUBECONFIG" "$BACKUP_DIR/kubeconfig"
    info "Backed up current kubeconfig"
fi

# Backup HAProxy config
sudo cp /etc/haproxy/haproxy.cfg "$BACKUP_DIR/haproxy.cfg"
info "Backed up HAProxy config"

# ============================================================================
# Step: Regenerate Ignition Configs
# ============================================================================
step "Regenerate Ignition Configs"

cd "$REPO_DIR"
info "Running generate_ignition.sh..."
bash "$REPO_DIR/generate_ignition.sh"

if [[ -f "$REPO_DIR/overwatch-gen/bootstrap.ign" ]]; then
    cp "$REPO_DIR/overwatch-gen/bootstrap.ign" "$REPO_DIR/"
    cp "$REPO_DIR/overwatch-gen/master.ign" "$REPO_DIR/"
    cp "$REPO_DIR/overwatch-gen/worker.ign" "$REPO_DIR/"
    info "Ignition files regenerated successfully"
else
    error "Ignition generation failed — no bootstrap.ign found"
    exit 1
fi

# Copy new kubeconfig
if [[ -f "$REPO_DIR/overwatch-gen/auth/kubeconfig" ]]; then
    mkdir -p "$AUTH_DIR"
    cp "$REPO_DIR/overwatch-gen/auth/kubeconfig" "$KUBECONFIG"
    info "New kubeconfig installed to $KUBECONFIG"
fi

# ============================================================================
# Step: Copy Ignition Files to Web Root
# ============================================================================
step "Copy Ignition Files to Web Root"

sudo mkdir -p "$WEB_ROOT/ignition"
sudo cp "$REPO_DIR/bootstrap.ign" "$WEB_ROOT/ignition/"
sudo cp "$REPO_DIR/master.ign" "$WEB_ROOT/ignition/"
sudo cp "$REPO_DIR/worker.ign" "$WEB_ROOT/ignition/"
sudo chmod 644 "$WEB_ROOT/ignition/"*.ign
info "Ignition files copied to $WEB_ROOT/ignition/"

# Ensure SCOS artifacts are in web root
sudo mkdir -p "$WEB_ROOT/scos"
sudo cp "$ARTIFACTS_DIR/scos-kernel" "$WEB_ROOT/scos/"
sudo cp "$ARTIFACTS_DIR/scos-initramfs.img" "$WEB_ROOT/scos/"
sudo cp "$ARTIFACTS_DIR/scos-rootfs.img" "$WEB_ROOT/scos/"
info "SCOS artifacts copied to $WEB_ROOT/scos/"

# ============================================================================
# Step: Start Nginx Artifact Server
# ============================================================================
step "Start Nginx Artifact Server"

if ! systemctl is-active --quiet nginx; then
    sudo systemctl start nginx
    info "nginx started"
else
    info "nginx already running"
fi

# Verify artifact serving
HTTP_CHECK=$(curl -s -o /dev/null -w "%{http_code}" "http://10.0.0.1:8080/ignition/bootstrap.ign" 2>/dev/null || echo "000")
if [[ "$HTTP_CHECK" == "200" ]]; then
    info "Artifact server verified — bootstrap.ign accessible at http://10.0.0.1:8080/ignition/bootstrap.ign"
else
    error "Artifact server check failed (HTTP $HTTP_CHECK)"
    exit 1
fi

# ============================================================================
# Step: Enable Bootstrap in HAProxy
# ============================================================================
step "Enable Bootstrap in HAProxy"

sudo bash "$SCRIPTS_DIR/toggle-haproxy-bootstrap.sh" enable
info "Bootstrap node enabled in HAProxy"

# ============================================================================
# Step: Terraform Destroy + Apply
# ============================================================================
step "Terraform Destroy + Apply"

cd "$REPO_DIR/infrastructure"
export KUBECONFIG="$AUTH_DIR/kubeconfig"

info "Running tofu init..."
$TOFU init -no-color

info "Destroying existing cluster VMs..."
$TOFU destroy -auto-approve -no-color 2>&1 | tee -a "$LOG_FILE"

info "Creating new cluster VMs..."
$TOFU apply -auto-approve -no-color 2>&1 | tee -a "$LOG_FILE"

info "Terraform apply complete — VMs created"

# ============================================================================
# Step: Wait for Bootstrap Complete
# ============================================================================
step "Wait for Bootstrap Complete"

cd "$REPO_DIR"
info "Waiting for bootstrap to complete (this may take 20-40 minutes)..."
$INSTALLER wait-for bootstrap-complete --dir="$REPO_DIR/overwatch-gen" --log-level=info 2>&1 | tee -a "$LOG_FILE"
info "Bootstrap complete!"

# ============================================================================
# Step: Disable Bootstrap in HAProxy
# ============================================================================
step "Disable Bootstrap in HAProxy"

sudo bash "$SCRIPTS_DIR/toggle-haproxy-bootstrap.sh" disable
info "Bootstrap node removed from HAProxy"

# ============================================================================
# Step: Approve CSRs
# ============================================================================
step "Approve CSRs"

info "Starting CSR auto-approval loop..."
bash "$SCRIPTS_DIR/approve-csrs.sh" --timeout 10 &
CSR_PID=$!

# ============================================================================
# Step: Wait for Cluster Operators
# ============================================================================
step "Wait for Cluster Operators Stable"

info "Waiting for install to complete (cluster operators stabilizing)..."
$INSTALLER wait-for install-complete --dir="$REPO_DIR/overwatch-gen" --log-level=info 2>&1 | tee -a "$LOG_FILE"
info "Cluster install complete!"

# Stop CSR approval loop
if kill -0 "$CSR_PID" 2>/dev/null; then
    kill "$CSR_PID" 2>/dev/null || true
    wait "$CSR_PID" 2>/dev/null || true
    info "CSR approval loop stopped"
fi

# ============================================================================
# Step: Stop Nginx
# ============================================================================
step "Stop Nginx Artifact Server"

sudo systemctl stop nginx
info "nginx stopped"

# ============================================================================
# Step: Apply Custom MachineConfigs
# ============================================================================
step "Apply Custom MachineConfigs"

export KUBECONFIG="$AUTH_DIR/kubeconfig"

info "Applying OVN sysctl buffer tuning..."
$OC apply -f "$REPO_DIR/manifests/machineconfigs/99-master-sysctl-ovs-buffer.yaml"
info "MachineConfig 99-master-sysctl-ovs-buffer applied"

info "Waiting for MachineConfigPool to finish rolling out..."
$OC wait mcp master --for=condition=Updated --timeout=600s 2>/dev/null || \
    warn "MCP rollout may still be in progress — check manually"

# ============================================================================
# Step: Install OpenShift GitOps Operator
# ============================================================================
step "Install OpenShift GitOps Operator"

info "Applying GitOps operator subscription..."
$OC apply -f "$GITOPS_DIR/argocd/install.yaml"

info "Waiting for GitOps operator to be ready..."
for i in $(seq 1 60); do
    if $OC get deployment openshift-gitops-server -n openshift-gitops &>/dev/null; then
        info "OpenShift GitOps operator is deployed"
        break
    fi
    sleep 10
    if [[ $i -eq 60 ]]; then
        warn "GitOps operator not ready after 10 minutes — continuing anyway"
    fi
done

# Wait for ArgoCD server to be available
$OC rollout status deployment/openshift-gitops-server -n openshift-gitops --timeout=300s 2>/dev/null || \
    warn "ArgoCD server rollout not complete"

# ============================================================================
# Step: Configure ArgoCD + Apply Applications
# ============================================================================
step "Configure ArgoCD and Apply Applications"

# Apply ArgoCD route for 208.haist.farm access
info "Applying ArgoCD route..."
$OC apply -f "$GITOPS_DIR/apps/argocd/route-208.yaml"

# Apply system infrastructure apps first
info "Applying NFS provisioner app..."
$OC apply -f "$GITOPS_DIR/clusters/overwatch/system/storage/nfs-app.yaml"

info "Applying static storage app..."
$OC apply -f "$GITOPS_DIR/clusters/overwatch/apps/static-storage-app.yaml"

# Wait for storage to be ready
sleep 30

# Apply ingress apps
info "Applying Pangolin internal app..."
$OC apply -f "$GITOPS_DIR/clusters/overwatch/system/ingress/pangolin-app.yaml"

info "Applying Newt tunnel app..."
$OC apply -f "$GITOPS_DIR/clusters/overwatch/system/ingress/newt-app.yaml"

# Apply workload apps
info "Applying Homepage app..."
$OC apply -f "$GITOPS_DIR/clusters/overwatch/apps/homepage-app.yaml"

info "Applying Jellyfin app..."
$OC apply -f "$GITOPS_DIR/clusters/overwatch/apps/jellyfin-app.yaml"

info "Applying Seedbox app..."
$OC apply -f "$GITOPS_DIR/clusters/overwatch/apps/seedbox-app.yaml"

# Apply monitoring
info "Applying Grafana app..."
$OC apply -f "$GITOPS_DIR/clusters/overwatch/apps/monitoring/grafana-app.yaml"
$OC apply -f "$GITOPS_DIR/clusters/overwatch/apps/monitoring/grafana-route-208.yaml"

# Apply console route
info "Applying Console route..."
$OC apply -f "$GITOPS_DIR/apps/console/route-208.yaml"

info "All ArgoCD applications applied"

# ============================================================================
# Step: Install Istio via Sail Operator
# ============================================================================
step "Install Istio (Sail Operator)"

info "Installing Sail Operator via OLM..."
cat <<SAILEOF | $OC apply -f -
apiVersion: operators.coreos.com/v1alpha1
kind: Subscription
metadata:
  name: sailoperator
  namespace: openshift-operators
spec:
  channel: stable
  installPlanApproval: Automatic
  name: sailoperator
  source: community-operators
  sourceNamespace: openshift-marketplace
SAILEOF

info "Waiting for Sail Operator to be ready..."
for i in $(seq 1 60); do
    if $OC get crd istios.sailoperator.io &>/dev/null; then
        info "Sail Operator CRDs available"
        break
    fi
    sleep 10
done

# Apply Istio CRs
info "Applying IstioCNI CR..."
$OC apply -f "$GITOPS_DIR/apps/istio-controlplane/istio-cni.yaml"

info "Applying Istio CR..."
$OC apply -f "$GITOPS_DIR/apps/istio-controlplane/istio.yaml"

# Wait for Istio to be ready
info "Waiting for istiod deployment..."
for i in $(seq 1 60); do
    if $OC rollout status deployment/istiod -n istio-system --timeout=10s &>/dev/null; then
        info "Istiod is ready"
        break
    fi
    sleep 10
done

# Apply mesh configuration
info "Applying mesh-config ArgoCD application..."
$OC apply -f "$GITOPS_DIR/clusters/overwatch/service-mesh/mesh-config-app.yaml"

# ============================================================================
# Step: Install Jaeger
# ============================================================================
step "Install Jaeger Operator"

info "Installing Jaeger Operator via OLM..."
$OC apply -f "$GITOPS_DIR/apps/jaeger/namespace-observability.yaml"
$OC apply -f "$GITOPS_DIR/apps/jaeger/jaeger-subscription.yaml"

info "Waiting for Jaeger CRD..."
for i in $(seq 1 30); do
    if $OC get crd jaegers.jaegertracing.io &>/dev/null; then
        info "Jaeger CRD available"
        break
    fi
    sleep 10
done

info "Applying Jaeger instance..."
$OC apply -f "$GITOPS_DIR/apps/jaeger/jaeger.yaml"

# ============================================================================
# Step: Install Kiali (Helm)
# ============================================================================
step "Install Kiali"

info "Installing Kiali via Helm..."
if command -v helm &>/dev/null; then
    helm repo add kiali https://kiali.org/helm-charts 2>/dev/null || true
    helm repo update 2>/dev/null || true
    helm install kiali kiali/kiali-server --version 2.21.0 --namespace istio-system \
        --set auth.strategy=anonymous \
        --set external_services.istio.root_namespace=istio-system \
        --set "external_services.prometheus.url=https://thanos-querier.openshift-monitoring.svc.cluster.local:9091" \
        --set "external_services.tracing.in_cluster_url=http://jaeger-query.observability.svc.cluster.local:16686" \
        --set "external_services.grafana.in_cluster_url=http://grafana.monitoring.svc.cluster.local:80" \
        2>&1 | tee -a "$LOG_FILE"
    info "Kiali installed"
else
    warn "Helm not found — install Kiali manually"
fi

# ============================================================================
# Step: Install Kyverno
# ============================================================================
step "Install Kyverno"

info "Installing Kyverno via Helm..."
if command -v helm &>/dev/null; then
    helm repo add kyverno https://kyverno.github.io/kyverno/ 2>/dev/null || true
    helm repo update 2>/dev/null || true
    helm install kyverno kyverno/kyverno --version 3.3.4 --namespace kyverno --create-namespace \
        2>&1 | tee -a "$LOG_FILE"
    info "Kyverno installed"

    # Apply cluster policies from overwatch-gitops if they exist
    if ls "$GITOPS_DIR/apps/kyverno-policies/"*.yaml &>/dev/null; then
        info "Applying Kyverno cluster policies..."
        $OC apply -f "$GITOPS_DIR/apps/kyverno-policies/"
    else
        warn "No Kyverno policies found in $GITOPS_DIR/apps/kyverno-policies/ — apply manually"
    fi
else
    warn "Helm not found — install Kyverno manually"
fi

# ============================================================================
# Step: Apply MachineConfig from GitOps
# ============================================================================
step "Apply MachineConfigs from GitOps"

if [[ -d "$GITOPS_DIR/clusters/overwatch/machineconfigs/" ]]; then
    info "Applying MachineConfigs from GitOps repo..."
    $OC apply -f "$GITOPS_DIR/clusters/overwatch/machineconfigs/"
fi

# ============================================================================
# Final Status
# ============================================================================
step "Final Status Check"

info "Checking cluster operator status..."
$OC get clusteroperators 2>&1 | tee -a "$LOG_FILE"

info "Checking node status..."
$OC get nodes 2>&1 | tee -a "$LOG_FILE"

info "Checking ArgoCD applications..."
$OC get applications -n openshift-gitops 2>&1 | tee -a "$LOG_FILE" || true

echo ""
echo "╔══════════════════════════════════════════════════════════════╗"
echo "║          OKD CLUSTER REBUILD COMPLETE                        ║"
echo "╠══════════════════════════════════════════════════════════════╣"
echo "║ Cluster:    $CLUSTER_NAME.$BASE_DOMAIN                      ║"
echo "║ Console:    https://console.208.haist.farm                   ║"
echo "║ ArgoCD:     https://argocd.208.haist.farm                    ║"
echo "║ API:        https://api.$CLUSTER_NAME.$BASE_DOMAIN:6443     ║"
echo "║ Kubeconfig: $KUBECONFIG                                      ║"
echo "║ Log:        $LOG_FILE                                        ║"
echo "╚══════════════════════════════════════════════════════════════╝"
echo ""
info "Rebuild script finished at $(date -u +%Y-%m-%dT%H:%M:%SZ)"
info "Full log available at: $LOG_FILE"

# Manual follow-up reminders
echo ""
echo "=== MANUAL FOLLOW-UP ==="
echo "1. Verify all ArgoCD apps are Synced/Healthy: oc get applications -n openshift-gitops"
echo "2. Check MachineConfigPool rollout: oc get mcp"
echo "3. Update Vault kubeconfig secret: vault kv put secret/iac-control/kubeconfig value=@$KUBECONFIG"
echo "4. Re-register Wazuh agents on nodes if needed"
echo "5. Verify Pangolin/Newt external access at *.208.haist.farm"
echo "6. Run compliance check: /home/ubuntu/sentinel-repo/scripts/compliance-check.sh"
