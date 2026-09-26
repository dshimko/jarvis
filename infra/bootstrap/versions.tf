# Local state by design: this root creates the state bucket. Run once with
# AWS_PROFILE=jarvis-prod (JarvisAdmin). Keep terraform.tfstate out of git (it holds no secrets).
terraform {
  required_version = ">= 1.10"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.66"
    }
  }
}

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
