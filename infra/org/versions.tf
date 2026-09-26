terraform {
  required_version = ">= 1.10"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.66"
    }
  }
}

# Management account (AWS_PROFILE set by the human or `make plan-org`; AD21). Organizations and
# the Identity Center instance (confirmed read-only: us-east-1) are served from us-east-1.
provider "aws" {
  region = var.identity_center_region

  default_tags {
    tags = {
      app   = "jarvis"
      env   = "prod"
      owner = "dushan"
    }
  }
}
