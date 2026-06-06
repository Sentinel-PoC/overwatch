# Recovery Status - Sun Jan 25 20:55:00 UTC 2026

## Cluster Status: DEPLOYED & STABLE
- **Masters:** 3/3 Ready (master-1, master-2, master-3).
- **Console:** Accessible via Traefik Proxy (https://console-openshift-console.apps.overwatch.haist.farm).
- **Authentication:** OAuth integrated and working.

## Configuration Fixes (Manual)
- **DNS/DHCP:** Updated `dnsmasq` on `iac-control` to assign hostnames (`master-1`..`3`).
- **Proxy:** Updated `pangolin-proxy` Traefik config (`openshift.yml`) to use TCP Passthrough for `*.apps` to allow OpenShift Router to handle SNI correctly, while terminating API/Console for Cloudflare certs.
- **Node Identity:** Manually set hostnames on nodes to resolve `localhost` conflict.

## Critical Notes
- **Do NOT** run `tofu apply` blindly. The state is drifted.
- **Master 3** required manual `machine-config-daemon` execution to finish firstboot.
