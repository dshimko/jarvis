# bootstrap

Run once, before `envs/prod`, with `AWS_PROFILE=jarvis-prod` (JarvisAdmin): `make plan-bootstrap`,
review `plan.txt`, then the human applies. Local state (this root creates the state bucket).

Creates `alias/jarvis-tfstate` (rotation on), `jarvis-tfstate-<ACCT>` (versioned, SSE-KMS with a
bucket key, public access blocked, `BucketOwnerEnforced`, noncurrent versions expire at 90 days,
`prevent_destroy`), the bucket policy of DESIGN-IAM.md 3.9, and the account-wide S3 public
access block.

The bucket policy admits only the `JarvisAdmin` SSO role and `OrganizationAccountAccessRole`,
so apply this as one of them. Then copy `envs/prod/backend.hcl.example` to `backend.hcl` with the
`state_bucket` output.
