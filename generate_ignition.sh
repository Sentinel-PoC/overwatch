#!/bin/bash
set -e
rm -rf overwatch-gen
mkdir overwatch-gen
cp install-config.yaml overwatch-gen/

# Create Manifests
/usr/local/bin/openshift-install create manifests --dir=overwatch-gen

# Inject MachineConfig for Masters
cat <<YAML > overwatch-gen/openshift/99-master-qemu-guest-agent.yaml
apiVersion: machineconfiguration.openshift.io/v1
kind: MachineConfig
metadata:
  labels:
    machineconfiguration.openshift.io/role: master
  name: 99-master-qemu-guest-agent
spec:
  config:
    ignition:
      version: 3.2.0
      systemd:
        units:
          - name: install-qemu-guest-agent.service
            enabled: true
            contents: |
              [Unit]
              Description=Install QEMU Guest Agent
              Wants=network-online.target
              After=network-online.target
              Before=zincati.service
              ConditionPathExists=!/var/lib/install-qemu-guest-agent.stamp

              [Service]
              Type=oneshot
              RemainAfterExit=yes
              ExecStart=/bin/sh -c "rpm-ostree install --allow-inactive --assumeyes qemu-guest-agent && touch /var/lib/install-qemu-guest-agent.stamp"

              [Install]
              WantedBy=multi-user.target
          - name: enable-qemu-guest-agent.service
            enabled: true
            contents: |
              [Unit]
              Description=Enable QEMU Guest Agent
              After=network-online.target

              [Service]
              Type=oneshot
              ExecStart=/bin/systemctl enable --now qemu-guest-agent.service
              RemainAfterExit=yes

              [Install]
              WantedBy=multi-user.target
YAML

# Create Ignition Configs
/usr/local/bin/openshift-install create ignition-configs --dir=overwatch-gen

echo 'Ignition files generated in overwatch-gen/'
ls -lh overwatch-gen/*.ign
