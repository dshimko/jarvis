# tflint-ignore-file: terraform_unused_required_providers
# infra/ itself is an empty root used only by `terraform test` (tests/*.tftest.hcl run the real
# roots through `module { source = ... }` with mocked providers). This block maps the provider
# names those roots use so the mocks resolve.
terraform {
  required_version = ">= 1.10"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.66"
    }
    archive = {
      source  = "hashicorp/archive"
      version = "~> 2.8"
    }
    tailscale = {
      source  = "tailscale/tailscale"
      version = "~> 0.29"
    }
  }
}
