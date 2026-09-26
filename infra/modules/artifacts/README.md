# artifacts

`jarvis-artifacts-<ACCT>`: versioned, SSE-KMS with the jarvis CMK and a bucket key, all four
public access block flags, `BucketOwnerEnforced`, noncurrent versions expire at 30 days, and the
bucket policy of DESIGN-IAM.md 3.8 (with the PLAN 3.2 `DenyWrongKmsKey` form: `Null` plus
`StringNotEquals`, so header-less uploads and `UploadPart` pass).

It also packs `ops/aws/` with `archive_file` (`tar.gz`, supported by hashicorp/archive 2.8) and
uploads it to `bootstrap/<sha256>/bootstrap.tar.gz` (AD18). Uploads that name a key must send
the full key ARN.
