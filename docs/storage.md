# Storage

## Storage Architecture

The Overwatch cluster uses NFS-backed dynamic provisioning as its primary
storage backend, supplemented by static PersistentVolumes for specific
workloads.

## NFS Provisioner

The `nfs-subdir-external-provisioner` provides dynamic PersistentVolume
creation backed by NFS shares on vault-server (192.168.12.205).

### ArgoCD Application

The NFS provisioner is managed by ArgoCD as the `nfs-provisioner` application:

| Property | Value |
|----------|-------|
| Namespace | `nfs-provisioner` |
| Helm Chart | `nfs-subdir-external-provisioner` v4.0.18 |
| NFS Server | 192.168.12.205 |
| NFS Path | `/mnt/DATA/data` |
| StorageClass | `nfs-storage` (default) |
| Istio Sidecar | Disabled (`sidecar.istio.io/inject: "false"`) |
| Image | `registry.k8s.io/sig-storage/nfs-subdir-external-provisioner:v4.0.2` |

### Helm Values

```yaml
nfs:
  server: 192.168.12.205
  path: /mnt/DATA/data
storageClass:
  create: false       # StorageClass is created separately
rbac:
  create: false       # RBAC is created separately
podAnnotations:
  sidecar.istio.io/inject: "false"
resources:
  requests:
    cpu: 100m
    memory: 128Mi
  limits:
    cpu: 200m
    memory: 256Mi
```

The `storageClass.create: false` and `rbac.create: false` settings indicate
that these resources are managed outside the Helm chart -- likely via the
`static-storage` ArgoCD application or manual creation.

### StorageClass

```
NAME                    PROVISIONER                                   RECLAIMPOLICY   VOLUMEBINDINGMODE
nfs-storage (default)   cluster.local/nfs-provisioner-...             Delete          Immediate
```

`nfs-storage` is the default StorageClass. PVCs without an explicit
`storageClassName` will use NFS dynamic provisioning.

### NFS Network Path

NFS traffic from OKD nodes to vault-server is explicitly permitted in
iac-control's iptables FORWARD rules:

```
# NFS access to storage server (TCP + UDP)
-A FORWARD -d 192.168.12.205/32 -i ens19 -o eth0 \
  -p tcp -m multiport --dports 2049,111 -j ACCEPT
-A FORWARD -d 192.168.12.205/32 -i ens19 -o eth0 \
  -p udp -m multiport --dports 2049,111 -j ACCEPT
```

Note: This allows node-level NFS mounts. The NFS provisioner pod runs with
istio sidecar injection disabled and uses the node's network stack for NFS
operations.

## Static PersistentVolumes

The `static-storage` ArgoCD application manages pre-created PersistentVolumes
for workloads that require specific NFS paths:

| Property | Value |
|----------|-------|
| ArgoCD App | `static-storage` |
| Source | `overwatch-gitops/clusters/overwatch/system/static-storage/` |
| Destination Namespace | `default` |

Static PVs are typically used for:

- Media storage (Jellyfin, seedbox content)
- Database data directories (PostgreSQL instances for Keycloak, Harbor, DefectDojo, NetBox, Backstage, Matrix)
- Application configuration that must survive PVC recreation

### Common PV Pattern

Static PVs in this cluster typically follow this pattern:

```yaml
apiVersion: v1
kind: PersistentVolume
metadata:
  name: <app>-data-pv
spec:
  capacity:
    storage: <size>
  accessModes:
    - ReadWriteOnce    # or ReadWriteMany for shared media
  nfs:
    server: 192.168.12.205
    path: /mnt/DATA/data/<app-specific-path>
  persistentVolumeReclaimPolicy: Retain
  storageClassName: ""   # Empty to prevent dynamic binding
```

## PVC Patterns by Application

Based on the ArgoCD applications deployed in the cluster, the following
workloads use persistent storage:

| Application | Namespace | Storage Type | Notes |
|-------------|-----------|-------------|-------|
| Harbor | harbor | Static PVs | Registry data, PostgreSQL, Redis |
| Keycloak | keycloak | Dynamic (NFS) | PostgreSQL data |
| Grafana | monitoring | Dynamic (NFS) | Dashboard state |
| DefectDojo | defectdojo | Dynamic + Static | PostgreSQL, media uploads |
| NetBox | netbox | Dynamic (NFS) | PostgreSQL, media, Redis |
| Jellyfin | media | Static PVs | Media library (ReadWriteMany) |
| Seedbox | media | Static PVs | Download content |
| Matrix | matrix | Dynamic (NFS) | Synapse media, PostgreSQL |
| Backstage | backstage | Dynamic (NFS) | PostgreSQL |
| Haists Website | haists-website | Dynamic (NFS) | Application data |
| Overwatch Console | overwatch-console | Dynamic (NFS) | Application data |

## Troubleshooting

### PVC Stuck in Pending

1. Check the NFS provisioner pod is running:
   ```bash
   oc get pods -n nfs-provisioner
   ```

2. Check NFS server connectivity from a node:
   ```bash
   ssh -i ~/.ssh/okd_key core@10.0.0.221 \
     'showmount -e 192.168.12.205'
   ```

3. Check iptables FORWARD rules on iac-control allow NFS traffic:
   ```bash
   sudo iptables -L FORWARD -n -v | grep 2049
   ```

### Volume Mount Errors

If pods fail to mount NFS volumes:

- Verify the NFS export path exists on vault-server (192.168.12.205)
- Check that the node's CoreOS has NFS client utilities (they are included by default in SCOS)
- Ensure firewall rules permit ports 2049 and 111 (TCP/UDP)

### StatefulSet Volume Conflicts

Harbor PostgreSQL and other StatefulSets with immutable volume claim templates
may require ArgoCD force sync when storage settings change. Add the
appropriate `ignoreDifferences` to the ArgoCD Application:

```yaml
ignoreDifferences:
  - group: apps
    kind: StatefulSet
    jqPathExpressions:
      - .spec.volumeClaimTemplates
      - .spec.persistentVolumeClaimRetentionPolicy
```
