# Config Server (LXC Container)

# Node 0: Bootstrap
resource "proxmox_virtual_environment_vm" "overwatch_bootstrap" {
  node_name = "pve"
  vm_id = 210 + var.vm_id_offset
  name = "overwatch-bootstrap${var.env_suffix}"

  agent {
    enabled = true
  }

  cpu {
    cores = 4
    type  = "host"
  }

  memory {
    dedicated = 16384
  }

  network_device {
    bridge = "vmbr1"
    mac_address = var.env_suffix == "" ? "BC:24:11:D8:CF:14" : null
  }

  disk {
    datastore_id = "local-lvm"
    interface    = "scsi0"
    size         = 120
    file_format  = "raw"
  }

  boot_order = ["scsi0", "net0"]

  operating_system {
    type = "l26"
  }

  lifecycle {
    prevent_destroy = true
    ignore_changes  = [
      agent,
      cpu,
      serial_device,
    ]
  }
}

# Node 1: Master 1
resource "proxmox_virtual_environment_vm" "overwatch_node_1" {
  node_name = "pve"
  vm_id = 211 + var.vm_id_offset
  name = "overwatch-node-1${var.env_suffix}"

  agent {
    enabled = true
  }

  cpu {
    cores = 12
    type  = "host"
  }

  memory {
    dedicated = 32000
  }

  network_device {
    bridge = "vmbr1"
    mac_address = var.env_suffix == "" ? "BC:24:11:F1:90:7A" : null
  }

  disk {
    datastore_id = "local-lvm"
    interface    = "scsi0"
    size         = 120
    file_format  = "raw"
  }

  boot_order = ["scsi0", "net0"]

  operating_system {
    type = "l26"
  }

  lifecycle {
    prevent_destroy = true
    ignore_changes  = [
      agent,
      cpu,
      serial_device,
    ]
  }
}

# Node 2: Master 2
resource "proxmox_virtual_environment_vm" "overwatch_node_2" {
  node_name = "208-pve2"
  vm_id = 212 + var.vm_id_offset
  name = "overwatch-node-2${var.env_suffix}"

  agent {
    enabled = true
  }

  cpu {
    cores = 12
    type  = "host"
  }

  memory {
    dedicated = 32000
  }

  network_device {
    bridge = "vmbr1"
    mac_address = var.env_suffix == "" ? "BC:24:11:54:EF:CF" : null
  }

  disk {
    datastore_id = "local-lvm"
    interface    = "scsi0"
    size         = 120
    file_format  = "raw"
  }

  boot_order = ["scsi0", "net0"]

  operating_system {
    type = "l26"
  }

  lifecycle {
    prevent_destroy = true
    ignore_changes  = [
      agent,
      cpu,
      serial_device,
    ]
  }
}

# Node 3: Master 3
resource "proxmox_virtual_environment_vm" "overwatch_node_3" {
  node_name = "208-pve2"
  vm_id = 213 + var.vm_id_offset
  name = "overwatch-node-3${var.env_suffix}"

  agent {
    enabled = true
  }

  cpu {
    cores = 12
    type  = "host"
  }

  memory {
    dedicated = 32000
  }

  network_device {
    bridge = "vmbr1"
    mac_address = var.env_suffix == "" ? "BC:24:11:EC:8C:91" : null
  }

  disk {
    datastore_id = "local-lvm"
    interface    = "scsi0"
    size         = 120
    file_format  = "raw"
  }

  boot_order = ["scsi0", "net0"]

  operating_system {
    type = "l26"
  }

  lifecycle {
    prevent_destroy = true
    ignore_changes  = [
      agent,
      cpu,
      serial_device,
    ]
  }
}
