# network

VPC `10.60.0.0/24`, one public subnet, IGW, route table, `jarvis-instance-sg` with zero ingress
and the six egress rules of DESIGN.md section 2, the default SG adopted and emptied, and VPC flow
logs (ALL traffic) to an SSE-KMS S3 bucket that expires objects at 90 days.

Flow logs are plain-text without Hive partitions so objects land under `AWSLogs/<ACCT>/`, the
prefix the allowlisted delivery grant (DESIGN.md P21) covers.

Inputs: `account_id`, `aws_region`, `kms_key_arn`. Outputs: ids, `policies`, `security_groups`.
