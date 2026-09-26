# Partial S3 backend in the management account: bucket, key, region from backend.hcl at init.
terraform {
  backend "s3" {
    use_lockfile = true
  }
}
