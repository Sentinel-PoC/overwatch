terraform {
  required_version = ">= 1.6.0"
  backend "s3" {
    bucket = "terraform-state"
    key    = "overwatch/terraform.tfstate"
    region = "us-east-1"

    # MinIO configuration
    # Credentials via AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY env vars
    endpoints = {
      s3 = "http://192.168.12.58:9000"
    }

    skip_credentials_validation = true
    skip_metadata_api_check     = true
    skip_region_validation      = true
    skip_requesting_account_id  = true
    use_path_style              = true
  }
  required_providers {
    proxmox = {
      source  = "bpg/proxmox"
      version = "0.70.0"
    }
  }
}

provider "proxmox" {
  endpoint  = var.proxmox_endpoint
  api_token = var.proxmox_api_token
  insecure  = true
}
