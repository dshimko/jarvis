# Partial S3 backend: bucket, key and region come from backend.hcl at init
# (terraform init -backend-config=backend.hcl). Native lockfile, no DynamoDB.
terraform {
  backend "s3" {
    use_lockfile = true
  }
}
