# Networking

## Network Architecture

The Overwatch cluster operates on an isolated internal network bridged to the
management VLAN through iac-control:

```
 Management VLAN (192.168.12.0/24)             Internet
        |                                          |
        | eth0                                     |
  [iac-control]--- ens19 (10.0.0.1/10.0.0.10) ---[vmbr1]
        |                                          |
        | NAT + Squid (:3128)            +---------+---------+
        | HAProxy (:6443,:22623,:80,:443)|         |         |
        | dnsmasq (DNS/DHCP/PXE)     [master-1] [master-2] [master-3]
        | keepalived VIP 10.0.0.1    10.0.0.221 10.0.0.222 10.0.0.223
        |
  [config-server (10.0.0.2)] -- keepalived BACKUP
```

### IP Addressing

| Network | CIDR | Purpose |
|---------|------|---------|
| Management VLAN | 192.168.12.0/24 | Infrastructure VMs, Proxmox hosts |
| Cluster Network | 10.0.0.0/24 | OKD node-to-node communication |
| Pod Network | 10.128.0.0/14 | OVN-Kubernetes pod CIDRs |
| Service Network | 172.30.0.0/16 | Kubernetes ClusterIP services |
| DHCP Range | 10.0.0.50 - 10.0.0.100 | Dynamic leases (unknown clients) |

### iac-control Interfaces

| Interface | IP Addresses | Purpose |
|-----------|-------------|---------|
| eth0 | 192.168.12.210/24 | Management network |
| ens19 | 10.0.0.10/24, 10.0.0.1/24 (VIP) | OKD cluster network + gateway |
| docker0 | 172.17.0.1/16 | Docker bridge |

## HAProxy Load Balancer

HAProxy on iac-control load-balances all cluster API and ingress traffic.
The configuration is managed by Ansible from the template
`sentinel-iac-work/ansible/roles/iac-control/templates/haproxy.cfg.j2`.

### Frontends and Backends

| Frontend | Port | Mode | Backend Servers | Purpose |
|----------|------|------|-----------------|---------|
| `okd4_api_frontend` | 6443 | TCP | master-1, master-2, master-3 | Kubernetes API server |
| `okd4_machine_config_frontend` | 22623 | TCP | master-1, master-2, master-3 | Machine Config Server |
| `okd4_http_ingress_frontend` | 80 | TCP | master-1, master-2, master-3 | HTTP ingress (OKD Router) |
| `okd4_https_ingress_frontend` | 443 | TCP | master-1, master-2, master-3 | HTTPS ingress (OKD Router) |
| `istio_gateway_http_frontend` | 8081 | TCP | master-1:31080, master-2:31080, master-3:31080 | Istio IngressGateway (NodePort) |
| `stats` | 9000 | HTTP | -- | HAProxy stats dashboard |

### HAProxy Configuration (key excerpts)

```
global
    log /dev/log local0
    chroot /var/lib/haproxy
    ssl-default-bind-options ssl-min-ver TLSv1.2 no-tls-tickets

defaults
    timeout connect 5000
    timeout client  50000
    timeout server  50000

# --- API Server (6443) ---
frontend okd4_api_frontend
    bind *:6443
    default_backend okd4_api_backend
    mode tcp

backend okd4_api_backend
    balance roundrobin
    mode tcp
    # server bootstrap 10.0.0.220:6443 check  # Disabled post-install
    server master1   10.0.0.221:6443 check
    server master2   10.0.0.222:6443 check
    server master3   10.0.0.223:6443 check

# --- Ingress HTTPS (443) ---
frontend okd4_https_ingress_frontend
    bind *:443
    default_backend okd4_https_ingress_backend
    mode tcp

backend okd4_https_ingress_backend
    balance roundrobin
    mode tcp
    server master1 10.0.0.221:443 check
    server master2 10.0.0.222:443 check
    server master3 10.0.0.223:443 check

# --- Istio IngressGateway (8081 -> NodePort 31080) ---
frontend istio_gateway_http_frontend
    bind *:8081
    default_backend istio_gateway_http_backend
    mode tcp

backend istio_gateway_http_backend
    balance roundrobin
    mode tcp
    server master1 10.0.0.221:31080 check
    server master2 10.0.0.222:31080 check
    server master3 10.0.0.223:31080 check
```

### HAProxy Access Control

HAProxy ports (80, 443, 6443, 22623) are restricted by iptables to:

- `192.168.12.168/32` -- pangolin-proxy (Traefik reverse proxy)
- `192.168.12.206/32` -- vault-server (ESO needs K8s API access)
- All traffic on the OKD interface (ens19 / 10.0.0.0/24)

All other sources are dropped and logged with `HAPROXY-BLOCK:` prefix.

## dnsmasq DNS / DHCP / PXE

dnsmasq on iac-control provides DNS resolution, DHCP, and PXE boot for the
cluster network. Configuration is managed by Ansible from the template
`sentinel-iac-work/ansible/roles/iac-control/templates/dnsmasq-overwatch.conf.j2`.

### DNS Records

```
# Static DNS (resolves to iac-control VIP / LB)
address=/api.overwatch.haist.farm/10.0.0.1
address=/api-int.overwatch.haist.farm/10.0.0.1
address=/.apps.overwatch.haist.farm/10.0.0.1
```

The wildcard `*.apps.overwatch.haist.farm` record resolves all OKD Routes
and Ingress traffic to iac-control, where HAProxy forwards it to the
cluster ingress controllers.

A second dnsmasq config file (`dnsmasq-sentinel-services.conf.j2`) resolves
internal service domains:

```
# Resolves *.208.haist.farm to pangolin-proxy
address=/.208.haist.farm/192.168.12.168

# External-facing domains resolved internally (avoid Cloudflare round-trip)
address=/auth.haist.farm/192.168.12.168
address=/gitlab.haist.farm/192.168.12.168
address=/matrix.haist.farm/192.168.12.168
```

### DHCP Static Leases

```
dhcp-range=10.0.0.50,10.0.0.100,12h

dhcp-host=bc:24:11:d8:cf:14,10.0.0.220,set:bootstrap
dhcp-host=BC:24:11:F1:90:7A,master-1,10.0.0.221,set:master
dhcp-host=BC:24:11:54:EF:CF,master-2,10.0.0.222,set:master
dhcp-host=BC:24:11:EC:8C:91,master-3,10.0.0.223,set:master
```

### PXE Boot

```
dhcp-match=set:ipxe,175
dhcp-boot=tag:bootstrap,tag:ipxe,http://10.0.0.1:8080/ignition/bootstrap.ipxe
dhcp-boot=tag:master,tag:ipxe,http://10.0.0.1:8080/ignition/master.ipxe
```

iPXE scripts and ignition files are served by nginx on port 8080.

### Upstream DNS Forwarding

```
server=1.1.1.1
server=8.8.8.8
```

## keepalived VIP

keepalived provides a floating VIP (10.0.0.1) for HA DNS/LB failover between
iac-control and config-server. Configuration from
`sentinel-iac-work/ansible/roles/iac-control/templates/keepalived.conf.j2`:

```
vrrp_script chk_haproxy {
    script "/usr/local/bin/check_haproxy.sh"
    interval 2
    weight -20
    fall 3
    rise 2
}

vrrp_instance OKD_VIP {
    state MASTER              # BACKUP on config-server
    interface ens19           # eth0 on config-server
    virtual_router_id 51
    priority 100              # 50 on config-server

    authentication {
        auth_type PASS
        auth_pass <from-vault>  # secret/infrastructure/vrrp
    }

    virtual_ipaddress {
        10.0.0.1/24
    }

    track_script {
        chk_haproxy
    }
}
```

If HAProxy on iac-control fails 3 consecutive health checks (6 seconds),
keepalived demotes its priority by 20 and config-server takes over the VIP.

## OVN-Kubernetes CNI

The cluster uses OVN-Kubernetes as the Container Network Interface:

- **Pod network:** 10.128.0.0/14 with /23 per node (510 pods per node)
- **Service network:** 172.30.0.0/16
- **Network policy:** Supported via OVN ACLs
- **Multicast:** Supported

### OVS Buffer Tuning

OVN's default netlink socket buffers (212 KB) caused
`OVNKubernetesNodeOVSOverflowKernel` alerts. A MachineConfig
(`99-master-sysctl-ovs-buffer`) is applied to all masters:

```
net.core.rmem_max = 16777216
net.core.rmem_default = 16777216
net.core.wmem_max = 16777216
net.core.wmem_default = 16777216
net.core.netdev_budget = 600
net.core.netdev_budget_usecs = 4000
net.core.netdev_max_backlog = 10000
net.core.somaxconn = 4096
```

## Squid Egress Proxy

Squid on iac-control provides transparent HTTP proxy with domain-based
allowlisting for cluster egress control. Configuration from
`sentinel-iac-work/ansible/roles/iac-control/templates/squid.conf.j2`.

### Key Configuration

```
# Transparent proxy on port 3128
http_port 3128 intercept

# OKD network egress filtering
acl okd_network src 10.0.0.0/24
acl allowed_domains dstdomain "/etc/squid/okd-egress-allowlist.txt"

# Allow OKD network to access allowed domains only
http_access allow okd_network allowed_domains

# Deny all other access
http_access deny all
```

### Allowed Domains

The egress allowlist (`/etc/squid/okd-egress-allowlist.txt`) permits:

| Domain Pattern | Purpose |
|----------------|---------|
| `.quay.io` | Container images |
| `.docker.io` | Container images |
| `.ghcr.io` | Container images (GitHub) |
| `.lscr.io` | LinuxServer container images |
| `.githubusercontent.com` | GitHub raw content |
| `.k8s.io` | Kubernetes resources |
| `.openshift.com`, `.okd.io` | OKD/OpenShift resources |
| `.pangolin.net` | Pangolin tunnel connectivity |
| `.letsencrypt.org`, `.digicert.com`, `.globalsign.com` | TLS certificate validation |
| `.ubuntu.com`, `.debian.org` | OS package updates |
| `.pool.ntp.org` | NTP time sync |
| `.github.com` | GitHub repositories |
| `.redhat.io` | Red Hat container images |
| `.akamaized.net`, `.fastly.net`, `.cloudfront.net`, `.amazonaws.com` | CDN for registries |
| `192.168.12.58` | MinIO (Terraform state) |
| `192.168.12.68` | GitLab (ArgoCD source) |

### Allowed Ports

| Port | Purpose |
|------|---------|
| 80 | HTTP |
| 443 | HTTPS |
| 587 | SMTP submission (Mailjet) |
| 8080 | HTTP alt (registries) |
| 8443 | HTTPS alt (registries) |
| 9443 | Webhook receivers |

## iptables / Firewall Rules

The iptables configuration on iac-control
(`sentinel-iac-work/ansible/roles/iac-control/templates/rules.v4.j2`)
controls all traffic between the cluster network and management VLAN.

### FORWARD Chain (Cluster Egress)

Traffic allowed from OKD cluster network (ens19) to management VLAN (eth0):

| Destination | Ports | Protocol | Purpose |
|-------------|-------|----------|---------|
| 1.1.1.1, 8.8.8.8 | 53 | UDP | DNS forwarding |
| Any | 123 | UDP | NTP |
| Any | 80, 443 | TCP | HTTP/HTTPS (image pulls, health checks) |
| Any | 587 | TCP | SMTP (Mailjet) |
| 192.168.12.205 (NFS) | 2049, 111 | TCP/UDP | NFS storage |
| 192.168.12.58 (MinIO) | 9000 | TCP | Object storage |
| 192.168.12.68 (GitLab) | 22, 80, 443 | TCP | ArgoCD sync, CI |
| Any | 21820, 51820 | UDP | WireGuard (seedbox VPN) |

All other egress is dropped and logged with `OKD-EGRESS-DENY:` prefix.

### NAT

```
# Management network traffic from cluster is allowed without NAT
-A PREROUTING -d 192.168.12.0/24 -i ens19 -j ACCEPT

# OKD cluster masquerading for internet-bound traffic
-A POSTROUTING -s 10.0.0.0/24 -o eth0 -j MASQUERADE
```

**Critical constraint:** Despite the PREROUTING ACCEPT rule, OKD pods cannot
initiate connections to 192.168.12.0/24 because the pod network (10.128.0.0/14)
is different from the node network (10.0.0.0/24). The NAT/FORWARD rules only
apply to node-level traffic. Pod traffic to mgmt VLAN addresses has no return
route.

## Traffic Flow Summary

### External User to OKD Service

```
User -> Cloudflare Tunnel / Tailscale
  -> pangolin-proxy (192.168.12.168) Traefik :443
    -> iac-control HAProxy :443 (HTTPS ingress)
      -> OKD Router on master nodes :443
        -> Pod (via OVN-Kubernetes)
```

### External User to Istio-Meshed Service

```
User -> pangolin-proxy Traefik
  -> iac-control HAProxy :8081 (Istio frontend)
    -> Istio IngressGateway NodePort :31080
      -> mTLS -> Pod sidecar -> Pod
```

### ArgoCD Sync

```
ArgoCD (in-cluster) -> GitLab (192.168.12.68:443)
  via iptables FORWARD rule allowing 192.168.12.68 on ports 22,80,443
```
