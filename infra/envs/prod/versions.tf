terraform {
  required_version = ">= 1.10"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.66"
    }
    tailscale = {
      source  = "tailscale/tailscale"
      version = "~> 0.29"
    }
  }
}

# Credentials come from AWS_PROFILE (jarvis-prod, JarvisAdmin); no profile names in code (AD21).
provider "aws" {
  region              = var.aws_region
  allowed_account_ids = [var.expected_account_id]

  default_tags {
    tags = {
      app   = "jarvis"
      env   = "prod"
      owner = "dushan"
    }
  }
}

# The tailscale provider is configured even when it manages nothing, and fails without
# credentials. While the ACL is not managed it gets an inert placeholder (no API call is made);
# when it is, the key comes from TAILSCALE_API_KEY in the environment.
provider "tailscale" {
  api_key = var.manage_tailscale_acl ? null : "unused-acl-not-managed"
}
