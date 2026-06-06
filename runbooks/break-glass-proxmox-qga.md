# Break-Glass Recovery: Proxmox Console + QEMU Guest Agent

**Issue:** OPS-262  
**Last updated:** 2026-04-20  
**Verified:** qga active on all running critical VMs as of 2026-04-20 (see `qga-audit-2026-04-20.md`)

---

## Purpose

This runbook documents the operator-controlled break-glass path for recovering access to any VM in the overwatch cluster when normal access channels are unavailable. The mechanism is the QEMU Guest Agent (`qemu-guest-agent`) accessed via the Proxmox API using a pre-positioned API token stored in Vault (or an offline backup).

This is **Option D** confirmed by the operator: Proxmox console + qga is the designated break-glass path.

---

## When to Use This

Use this procedure when **normal access is unavailable**:

- Vault is sealed and cannot be unsealed via standard path
- SSH CA is offline (cert signing unavailable, no signed cert to present)
- Workstation has lost network connectivity to the infrastructure network
- `sudo` or `sudoers` on a VM is broken, locking out privileged access
- SSH keys or authorized_keys on a VM have been corrupted or removed
- `sshd` on a VM is stopped or misconfigured
- OKD node is NotReady and SSH is unreachable
- Any scenario where the Proxmox API is reachable but the VM's SSH layer is not

**Do NOT use this as a routine access method.** Every use of `guest-exec` via this path generates a Wazuh alert (see Wazuh decoder `proxmox-qga-exec.xml`). Break-glass events must be reviewed.

---

## Prerequisites

### Checklist
- [ ] You have Proxmox web UI access OR Proxmox API access from your workstation
- [ ] Operator confirms Proxmox API tokens with shell-exec scope are stored in a sealed offline location (USB drive, printed, or air-gapped system)
- [ ] You know the target VM's VMID and node (see VMID table below)
- [ ] You have noted this event in Plane (create an OPS issue if one does not exist)

### Proxmox API Token Location

The Proxmox API token is stored at:

```
Vault path: secret/proxmox
Fields:     api_token_id       (format: user@realm!TokenName)
            api_token_secret   (UUID)
```

**If Vault is sealed:** retrieve the token from the offline backup (operator-held USB or printed credential sheet). Do not attempt to unseal Vault using this break-glass path — unseal Vault separately first if possible.

Token format for API calls:
```
Authorization: PVEAPIToken=<api_token_id>=<api_token_secret>
```

---

## VMID Quick Reference

| VMID | Name | Node | Role |
|------|------|------|------|
| 107 | pangolin-proxy | pve | Reverse proxy / Pangolin |
| 111 | wazuh | 208-pve2 | SIEM |
| 200 | iac-control | pve | Automation / Ansible control |
| 201 | gitlab-server | 208-pve2 | GitLab SCM |
| 205 | vault-server | 208-pve2 | HashiCorp Vault |
| 210 | overwatch-bootstrap | pve | OKD bootstrap node |
| 211 | overwatch-node-1 | pve | OKD control plane |
| 212 | overwatch-node-2 | 208-pve2 | OKD control plane |
| 213 | overwatch-node-3 | 208-pve2 | OKD control plane |
| 300 | config-server | pve | Platform config / DNS |

**Node IP mapping:**
- `pve` → 192.168.12.6
- `208-pve2` → 192.168.12.56
- `pve3` → 192.168.12.57

---

## Step-by-Step: Execute a Command via qga

### Step 1: Set environment variables

```bash
export PVE_HOST="192.168.12.6"        # or .56 / .57 depending on node
export PVE_PORT="8006"
export TOKEN_ID="root@pam!Claudette"   # from Vault secret/proxmox.api_token_id
export TOKEN_SECRET="<uuid>"           # from Vault secret/proxmox.api_token_secret
export AUTH="PVEAPIToken=${TOKEN_ID}=${TOKEN_SECRET}"
export VMID="200"                      # target VM
export NODE="pve"                      # target node name
```

### Step 2: Verify qga is active on the target VM

```bash
curl -sk \
  -H "Authorization: ${AUTH}" \
  "https://${PVE_HOST}:${PVE_PORT}/api2/json/nodes/${NODE}/qemu/${VMID}/agent/info" \
  | python3 -c "import json,sys; d=json.load(sys.stdin); print('QGA OK, version:', d['data']['result']['version'])"
```

Expected output: `QGA OK, version: 8.2.2` (or similar). If this fails, qga is not running — see Troubleshooting below.

### Step 3: Submit the command

Use `pvesh create` (on a Proxmox node shell) OR the API directly.

#### Via Proxmox API (from workstation, recommended):

```bash
# Submit command (returns a PID)
RESULT=$(curl -sk \
  -X POST \
  -H "Authorization: ${AUTH}" \
  -H "Content-Type: application/json" \
  -d '{"command": "id", "input-data": ""}' \
  "https://${PVE_HOST}:${PVE_PORT}/api2/json/nodes/${NODE}/qemu/${VMID}/agent/exec")

echo "${RESULT}" | python3 -m json.tool
PID=$(echo "${RESULT}" | python3 -c "import json,sys; print(json.load(sys.stdin)['data']['pid'])")
echo "Command PID: ${PID}"
```

#### Retrieve output:

```bash
# Wait 1-2 seconds, then retrieve
sleep 2
curl -sk \
  -H "Authorization: ${AUTH}" \
  "https://${PVE_HOST}:${PVE_PORT}/api2/json/nodes/${NODE}/qemu/${VMID}/agent/exec-status?pid=${PID}" \
  | python3 -c "
import json, sys, base64
d = json.load(sys.stdin)['data']
print('Exit code:', d.get('exitcode'))
if d.get('out-data'):
    print('STDOUT:', base64.b64decode(d['out-data']).decode())
if d.get('err-data'):
    print('STDERR:', base64.b64decode(d['err-data']).decode())
"
```

#### Via pvesh (if you have shell on a Proxmox node):

```bash
# SSH to Proxmox node first
ssh root@192.168.12.6

# Submit command via pvesh
pvesh create /nodes/${NODE}/qemu/${VMID}/agent/exec \
  --command id \
  --input-data ""

# Retrieve result (use PID from above response)
pvesh get /nodes/${NODE}/qemu/${VMID}/agent/exec-status --pid <PID>
```

---

## Common Recovery Actions

### 1. Restore sudoers (broken sudo on a VM)

```bash
# Command to write correct sudoers file
COMMAND='bash -c "echo \"ubuntu ALL=(ALL) NOPASSWD:ALL\" > /etc/sudoers.d/ubuntu && chmod 440 /etc/sudoers.d/ubuntu"'

curl -sk \
  -X POST \
  -H "Authorization: ${AUTH}" \
  -H "Content-Type: application/json" \
  -d "{\"command\": \"bash\", \"input-data\": \"-c 'echo ubuntu ALL=(ALL) NOPASSWD:ALL > /etc/sudoers.d/ubuntu && chmod 440 /etc/sudoers.d/ubuntu'\"}" \
  "https://${PVE_HOST}:${PVE_PORT}/api2/json/nodes/${NODE}/qemu/${VMID}/agent/exec"
```

**Alternative: use the Proxmox built-in API helper:**

```bash
# Proxmox 8.x supports guest-exec with command array
curl -sk \
  -X POST \
  -H "Authorization: ${AUTH}" \
  -H "Content-Type: application/json" \
  -d '{"command":"bash","input-data":"-c","args":["-c","echo ubuntu ALL=(ALL) NOPASSWD:ALL > /etc/sudoers.d/ubuntu"]}' \
  "https://${PVE_HOST}:${PVE_PORT}/api2/json/nodes/${NODE}/qemu/${VMID}/agent/exec"
```

### 2. Re-add SSH authorized key

```bash
TARGET_KEY="ssh-ed25519 AAAA... operator@workstation"
TARGET_USER="ubuntu"

# Get PID
curl -sk \
  -X POST \
  -H "Authorization: ${AUTH}" \
  -H "Content-Type: application/json" \
  -d "{\"command\": \"bash\", \"input-data\": \"-c 'mkdir -p /home/${TARGET_USER}/.ssh && echo ${TARGET_KEY} >> /home/${TARGET_USER}/.ssh/authorized_keys && chown -R ${TARGET_USER}:${TARGET_USER} /home/${TARGET_USER}/.ssh && chmod 700 /home/${TARGET_USER}/.ssh && chmod 600 /home/${TARGET_USER}/.ssh/authorized_keys'\"}" \
  "https://${PVE_HOST}:${PVE_PORT}/api2/json/nodes/${NODE}/qemu/${VMID}/agent/exec"
```

**Preferred: use qga's native SSH key management (qga 5.1+):**

```bash
curl -sk \
  -X POST \
  -H "Authorization: ${AUTH}" \
  -H "Content-Type: application/json" \
  -d "{\"username\": \"ubuntu\", \"key\": \"${TARGET_KEY}\"}" \
  "https://${PVE_HOST}:${PVE_PORT}/api2/json/nodes/${NODE}/qemu/${VMID}/agent/ssh-add-authorized-keys"
```

### 3. Restart sshd

```bash
curl -sk \
  -X POST \
  -H "Authorization: ${AUTH}" \
  -H "Content-Type: application/json" \
  -d '{"command": "systemctl", "input-data": "restart ssh"}' \
  "https://${PVE_HOST}:${PVE_PORT}/api2/json/nodes/${NODE}/qemu/${VMID}/agent/exec"
```

### 4. Restart Vault container (on vault-server, VMID 205)

```bash
# Check vault status first
curl -sk \
  -X POST \
  -H "Authorization: ${AUTH}" \
  -H "Content-Type: application/json" \
  -d '{"command": "systemctl", "input-data": "status vault"}' \
  "https://192.168.12.56:${PVE_PORT}/api2/json/nodes/208-pve2/qemu/205/agent/exec"

# Restart vault service
curl -sk \
  -X POST \
  -H "Authorization: ${AUTH}" \
  -H "Content-Type: application/json" \
  -d '{"command": "systemctl", "input-data": "restart vault"}' \
  "https://192.168.12.56:${PVE_PORT}/api2/json/nodes/208-pve2/qemu/205/agent/exec"
```

### 5. Re-mint SSH certificate (when SSH CA is offline)

If the SSH CA is offline but iac-control (VMID 200) is reachable via qga:

```bash
# 1. Check if Vault agent on iac-control is running
curl -sk \
  -X POST \
  -H "Authorization: ${AUTH}" \
  -H "Content-Type: application/json" \
  -d '{"command": "systemctl", "input-data": "status vault-agent"}' \
  "https://${PVE_HOST}:${PVE_PORT}/api2/json/nodes/pve/qemu/200/agent/exec"

# 2. If Vault is sealed, unseal it first (requires unseal keys — operator-held)
# 3. Once Vault is unsealed, restart vault-agent on iac-control via qga
# 4. Then SSH normally using the re-issued cert
```

### 6. Reset file permissions (emergency)

```bash
curl -sk \
  -X POST \
  -H "Authorization: ${AUTH}" \
  -H "Content-Type: application/json" \
  -d '{"command": "bash", "input-data": "-c \"chmod 755 /home/ubuntu && chmod 700 /home/ubuntu/.ssh && chmod 600 /home/ubuntu/.ssh/authorized_keys\""}' \
  "https://${PVE_HOST}:${PVE_PORT}/api2/json/nodes/${NODE}/qemu/${VMID}/agent/exec"
```

---

## Troubleshooting

### qga not responding ("error 500" or no data)

1. Log into Proxmox web UI: `https://192.168.12.6:8006`
2. Navigate to the VM → Console
3. Check if `qemu-guest-agent` service is running inside the VM:
   ```
   systemctl status qemu-guest-agent
   ```
4. If stopped: `systemctl start qemu-guest-agent`
5. If not installed: `apt install qemu-guest-agent` and `systemctl enable --now qemu-guest-agent`
6. Re-run audit script: `python3 scripts/audit_qga.py`

### VM is stopped

Start the VM first:

```bash
curl -sk \
  -X POST \
  -H "Authorization: ${AUTH}" \
  "https://${PVE_HOST}:${PVE_PORT}/api2/json/nodes/${NODE}/qemu/${VMID}/status/start"
```

Then wait 30-60 seconds for boot before attempting qga commands.

### Proxmox API returns 401 Unauthorized

The API token may have expired or been rotated. Retrieve the current token from:
1. Vault: `vault kv get secret/proxmox` (if Vault is unsealed)
2. Offline backup (operator-held)

---

## Post-Recovery Steps

After any break-glass event:

1. **Create a Plane issue** (or update the existing one) with:
   - Timestamp of break-glass use
   - Which VM was targeted
   - What commands were executed
   - Why normal access was unavailable

2. **Review Wazuh alert** — the Wazuh decoder (`proxmox-qga-exec.xml`) fires an alert level 12 on every `POST /nodes/*/qemu/*/agent/exec` call. Review the alert to confirm the action matches the operator's stated intent.

3. **Rotate credentials if needed** — if break-glass was triggered by a credential compromise, rotate SSH keys, re-issue certs, and update Vault secrets before resuming normal operations.

4. **Re-run qga audit** — confirm all VMs are still in a healthy state:
   ```bash
   python3 scripts/audit_qga.py
   ```

5. **Close the Plane issue** with VERIFICATION note after confirming recovery.

---

## NIST 800-53 Control Mapping

| Control | How This Runbook Supports It |
|---------|------------------------------|
| **CP-10** (System Recovery and Reconstitution) | Provides tested operator procedure for recovering VM access after failure |
| **CP-10(2)** (Transaction Recovery) | Defines recovery order and per-service commands |
| **AC-5** (Separation of Duties) | Proxmox API token is held separately from SSH CA; Wazuh alerts on every exec |
| **AC-17** (Remote Access) | Break-glass path is documented and audited, not a silent backdoor |
| **AU-6** (Audit Record Review) | Every qga exec call generates a Wazuh alert (level 12) via decoder |
| **AU-12** (Audit Record Generation) | Proxmox API logs + Wazuh decoder create immutable audit trail |
| **IR-4** (Incident Handling) | Runbook is the incident response procedure for access loss |
| **IR-10** (Integrated Information Security Analysis Team) | Post-recovery steps require Plane issue creation and Wazuh review |

---

## Sealed Proxmox Token Confirmation

Before relying on this break-glass path in a real emergency, the operator must confirm:

- [ ] Proxmox API tokens with VM shell-exec scope (`root@pam!Claudette` or equivalent) are stored in a **sealed offline location** (USB drive, printed credential sheet, or air-gapped system)
- [ ] At least one operator (not only the primary) knows the location of this offline backup
- [ ] The offline token has been tested against the Proxmox API within the last 90 days
- [ ] The token value in Vault (`secret/proxmox`) matches the offline backup

**This checklist is an operator affirmation.** It is not code-enforced. The operator must verify this manually and update this document with the confirmation date.

**Last operator confirmation:** UNVERIFIED — requires operator sign-off.

---

## Related Documents

- `runbooks/qga-audit-2026-04-20.md` — live audit showing qga status on all cluster VMs
- `runbooks/wazuh-decoders/proxmox-qga-exec.xml` — Wazuh decoder that fires on every qga exec
- `scripts/audit_qga.py` — reproducible audit script
- Vault path: `secret/proxmox` — API token storage
