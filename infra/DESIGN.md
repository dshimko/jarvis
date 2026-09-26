# Jarvis on AWS: design

Re-targeted 2026-09-25 (PLAN.md AD34 to AD36).

Phase 1 deliverable. It is a design only; there is no code here. Inputs: `infra/PLAN.md` (AD1 to
AD21, and AD22 to AD30 in PLAN 3.1, which answer this design's first-round questions),
`infra/BRIEF.md`, `README.md`, `PORTING.md`, `jarvis/{modes,api,brain}.py`.
Where this file tightens the plan it says "(tighter)". Disagreements are listed in section 13.

Notation. `ACCT` is the `jarvis-prod` account id (created by `org/`). The management account
is `080109295043`, organization `o-lcwqey40mr`, profile `sparko` (confirmed read-only). Region is `var.aws_region`, default `us-east-1`,
and it is written literally below. `<secret:jarvis/work>`, `<key:jarvis>`, `<role:jarvis-instance>`
and similar are exact ARNs that Terraform resolves from resource attributes. They are never
wildcards. Every resource carries `app=jarvis`, `env=prod`, `owner=dushan` (AD20).

## 1. Resource inventory

### bootstrap/ (run once, `AWS_PROFILE=jarvis-prod`, JarvisAdmin)

| Resource | Name | Purpose |
|---|---|---|
| `aws_kms_key` + `aws_kms_alias` | `alias/jarvis-tfstate` | Encrypts the state bucket. Rotation on, 30-day deletion window |
| `aws_s3_bucket` | `jarvis-tfstate-ACCT` | Terraform state for `envs/prod` and `bootstrap` (native lockfile, `use_lockfile = true`) |
| versioning, SSE, PAB, ownership, lifecycle | same | Versioned; SSE-KMS `alias/jarvis-tfstate`, bucket key on; all four PAB flags; `BucketOwnerEnforced`; noncurrent versions expire at 90 days |
| `aws_s3_bucket_policy` | same | TLS only, listed principals only, right KMS key only (section 3.9) |
| `aws_s3_account_public_access_block` | account | Account-wide S3 public access block |

### modules/network

| Resource | Name | Purpose |
|---|---|---|
| `aws_vpc` | `jarvis-vpc` | `10.60.0.0/24`, DNS support and hostnames on, no IPv6 |
| `aws_subnet` | `jarvis-public-a` | `10.60.0.0/26` in `us-east-1a` (variable), `map_public_ip_on_launch = false` |
| `aws_internet_gateway` | `jarvis-igw` | Egress path, no NAT gateway |
| `aws_route_table` + association | `jarvis-public-rt` | `0.0.0.0/0 -> igw`, local route |
| `aws_security_group` | `jarvis-instance-sg` | Zero ingress rules. Egress per section 2 |
| `aws_vpc_security_group_egress_rule` x6 | `jarvis-egress-*` | One resource per rule (section 2) |
| `aws_default_security_group` | default | Adopted and stripped of all rules |
| `aws_s3_bucket` (+ versioning off, SSE, PAB, ownership, lifecycle, policy) | `jarvis-flowlogs-ACCT` | Flow log destination. SSE-KMS `<key:jarvis>`. Objects expire at 90 days |
| `aws_flow_log` | `jarvis-vpc-flowlog` | VPC-level, `traffic_type = ALL`, S3, 600 s aggregation, hive partitions |

### modules/compute

| Resource | Name | Purpose |
|---|---|---|
| `aws_kms_key` + `aws_kms_alias` | `alias/jarvis` | One CMK for EBS, secrets, logs, SNS, artifacts, flow logs, backup vault. Rotation on |
| `aws_ebs_encryption_by_default` + `aws_ebs_default_kms_key` | account | Any new volume is encrypted with `alias/jarvis` |
| `aws_iam_role` | `jarvis-instance` | Instance role (section 3.1) |
| `aws_iam_role_policy_attachment` | AmazonSSMManagedInstanceCore | Session Manager and Run Command |
| `aws_iam_role_policy` x3 | `jarvis-secrets`, `jarvis-artifacts`, `jarvis-telemetry` | Inline policies (section 3.2) |
| `aws_iam_instance_profile` | `jarvis-instance` | Binds the role |
| `aws_network_interface` | `jarvis-eni` | Primary ENI in `jarvis-public-a` with `jarvis-instance-sg`; exists before the instance |
| `aws_eip` | `jarvis-eip` | Elastic IP bound to `jarvis-eni` (AD29). Stable source IP for the role's `aws:SourceIp` Deny |
| `aws_launch_template` | `jarvis` | AMI, IMDS options, root volume, `jarvis-eni` as device 0, user_data |
| `aws_instance` | `jarvis` | t4g.medium, `credit_specification = standard` (tighter), termination protection on |
| `aws_cloudwatch_metric_alarm` | `jarvis-auto-recover` | `StatusCheckFailed_System` -> `arn:aws:automate:us-east-1:ec2:recover` + SNS |

AMI: `data "aws_ami"`, owner `099720109477` (Canonical), name
`ubuntu/images/hvm-ssd-gp3/ubuntu-noble-24.04-<arch>-server-*`, arch from `var.instance_type`
(AD20). The instance has `lifecycle { ignore_changes = [ami, user_data, launch_template] }` so a
new AMI or a new bootstrap tarball never replaces the instance. `ops/aws/` changes ship with a
release (AD18). Variable `instance_enabled` (default `true`): the first apply runs with `false`,
the human populates the value secrets, and the second apply sets `true` (AD30).

Launch template: IMDS `http_endpoint = enabled`, `http_tokens = required`, hop limit 1,
`instance_metadata_tags` and `http_protocol_ipv6` disabled. Root `/dev/sda1`: 30 GiB gp3, 3000
IOPS, 125 MiB/s, `<key:jarvis>`, `delete_on_termination = false` (tighter), tag `backup=jarvis`.
It attaches `jarvis-eni` as device 0, so the EIP is in place before the first packet; no
auto-assigned public IP, no key pair. `auto_recovery = default`, `disable_api_termination = true`.

### modules/secrets

| Resource | Name | Purpose |
|---|---|---|
| `aws_secretsmanager_secret` | `jarvis/work` | Work env (AD7) |
| `aws_secretsmanager_secret` | `jarvis/personal` | Personal env (AD7) |
| `aws_secretsmanager_secret` | `jarvis/shared` | Keys allowed in both modes (AD7) |
| `aws_secretsmanager_secret` | `jarvis/tailscale` | Tailscale auth key (AD7) |
| `aws_secretsmanager_secret` | `jarvis/work/api-token` | Work API token (AD6) |
| `aws_secretsmanager_secret` | `jarvis/personal/api-token` | Personal API token (AD6) |

All six: `kms_key_id = <key:jarvis>`, `recovery_window_in_days = 7`, no
`aws_secretsmanager_secret_version`, no rotation Lambda, no resource policy (identity policies
decide access).

### modules/artifacts

| Resource | Name | Purpose |
|---|---|---|
| `aws_s3_bucket` | `jarvis-artifacts-ACCT` | `releases/<sha>/`, `releases/DEPLOYED`, `bootstrap/<sha256>/` |
| versioning, SSE-KMS (`<key:jarvis>`, bucket key on), PAB, `BucketOwnerEnforced` | same | Standard hardening |
| `aws_s3_bucket_lifecycle_configuration` | same | Noncurrent versions expire at 30 days; `bootstrap/` current objects kept |
| `aws_s3_bucket_policy` | same | Section 3.8 |
| `aws_s3_object` | `bootstrap/<sha256>/bootstrap.tar.gz` | From `data "archive_file"` over `ops/aws/` (AD18) |

### modules/backup

| Resource | Name | Purpose |
|---|---|---|
| `aws_backup_vault` | `jarvis-backup` | Recovery points, `kms_key_arn = <key:jarvis>` |
| `aws_backup_plan` | `jarvis-ebs` | Rule `daily`: `var.backup_daily_schedule` default `cron(0 8 * * ? *)`, delete after 14 days. Rule `weekly`: `var.backup_weekly_schedule` default `cron(0 9 ? * SUN *)`, delete after 56 days. Start window 60 min, completion 360 min (AD30) |
| `aws_iam_role` | `jarvis-backup` | Service role (section 3.10) |
| `aws_iam_role_policy_attachment` x2 | AWSBackupServiceRolePolicyForBackup, ...ForRestores | AWS managed |
| `aws_backup_selection` | `jarvis-root-volume` | `StringEquals aws:ResourceTag/backup = jarvis` on EBS volumes |

### modules/observability

| Resource | Name | Purpose |
|---|---|---|
| `aws_cloudwatch_log_group` x3 | `/jarvis/work`, `/jarvis/personal`, `/jarvis/cloud-init` | 30-day retention, `<key:jarvis>` (AD2) |
| `aws_cloudwatch_log_metric_filter` x4 | section 7 | Heartbeat x2, OfwWatchErrors, OfwWatchDisabled |
| `aws_cloudwatch_metric_alarm` x6 | section 7 | Heartbeats, OFW, disk, instance status |
| `aws_sns_topic` | `jarvis-alerts` | `kms_master_key_id = <key:jarvis>` |
| `aws_sns_topic_policy` | same | Section 3.11 |
| `aws_sns_topic_subscription` | email | `var.alert_email`: required, no default (AD22). The human clicks the confirmation link |
| `aws_budgets_budget` | `jarvis-monthly` | $60 COST, monthly, ACTUAL 50/80/100 % -> SNS |

### modules/ssm

| Resource | Name | Purpose |
|---|---|---|
| `aws_ssm_document` x4 | `jarvis-deploy`, `jarvis-restart`, `jarvis-secrets-sync`, `jarvis-status` | Command docs from `ops/aws/ssm/<name>.sh` (AD19) |
| `aws_ssm_document` | `SSM-SessionManagerRunShell` | Session preferences: no S3 or CloudWatch session logging (rule 0.4), `idleSessionTimeout = 20`, `runAsEnabled = false` |

### modules/tailscale_acl (opt-in, tailscale provider, AD24)

| Resource | Name | Purpose |
|---|---|---|
| `tailscale_acl` | tailnet ACL | Created only when `manage_tailscale_acl = true` (default `false`). Applies `infra/modules/tailscale_acl/policy.hujson`, a human-owned full-tailnet policy seeded as in section 10.5. No auth keys, no devices in state |

### org/ (management account, `AWS_PROFILE=management`)

| Resource | Name | Purpose |
|---|---|---|
| `aws_organizations_organizational_unit` | `jarvis` | Deletable OU under the organization root that holds the account and carries the attached SCPs (AD35) |
| `aws_organizations_account` | `jarvis-prod` | The account, inside the `jarvis` OU. `close_on_deletion = true` (AD35): `terraform destroy` here closes it (AWS's 90-day recovery window) |
| `aws_organizations_policy` + attachment | `jarvis-region-deny` | SCP 1 (section 11). Attached to the `jarvis` OU, gated by `var.attach_region_scp` default `false`. Before any attachment, `org/` checks read-only (`data "aws_organizations_organization"`) that the `SERVICE_CONTROL_POLICY` type is enabled, and fails the plan if not |
| `aws_organizations_policy` + attachment | `jarvis-org-guard` | SCP 2 (section 11), always attached to the `jarvis` OU |
| `aws_organizations_policy` + attachment | `jarvis-guardrail` | SCP 3 (section 11.3). Attached to the `jarvis` OU, gated by `var.attach_guardrail_scp` default `false` (AD27) |
| `aws_ssoadmin_permission_set` x3 | `JarvisClient`, `JarvisOperator`, `JarvisAdmin` | Section 3.12. All `aws_ssoadmin_*` resources exist only when `var.create_permission_sets = true` (default `false`, AD25); the runbook gives the manual alternative |
| `aws_ssoadmin_permission_set_inline_policy` x2 | Client, Operator | Section 3.12 |
| `aws_ssoadmin_managed_policy_attachment` | Admin -> AdministratorAccess | Terraform applies only |
| `aws_ssoadmin_account_assignment` x3 | human user -> Operator, Admin; workstation user -> Client | The workstation has its own Identity Center user holding only JarvisClient (AD25) |

No IAM users, access keys, key pairs, inbound rules, NAT, VPC endpoints, Lambda, or CloudTrail
resources are created.

## 2. Network

| Item | Value |
|---|---|
| VPC | `10.60.0.0/24`, one AZ. Does not overlap Tailscale CGNAT `100.64.0.0/10` |
| Subnet | `10.60.0.0/26`, public, route `0.0.0.0/0 -> jarvis-igw` |
| VPC resolver | `10.60.0.2` (VPC base + 2), via the default DHCP option set |
| Public IPv4 | Elastic IP `jarvis-eip` on `jarvis-eni` (AD29). Stable across stop/start. The instance role's Deny pins every non-service call to it |
| Inbound | None. SG has zero ingress rules; the default SG is emptied |
| IMDS | v2 only, hop limit 1, plus the root-only iptables rule (section 6.5) |
| Flow logs | VPC, ALL traffic, to `s3://jarvis-flowlogs-ACCT/AWSLogs/ACCT/...`, 90-day expiry |

Egress rules on `jarvis-instance-sg`, one resource each:

| # | Proto | Port | Destination | Why |
|---|---|---|---|---|
| E1 | TCP | 443 | `0.0.0.0/0` | AWS APIs, Anthropic, Slack Socket Mode, Telegram long poll, MCP servers, Google, Tailscale control and DERP, NodeSource, Syncthing and CloudWatch agent packages |
| E2 | TCP | 80 | `0.0.0.0/0` | Package mirrors only (`*.ec2.ports.ubuntu.com` serves arm64 over HTTP; apt verifies signatures). Nothing else is expected on 80 |
| E3 | UDP | 41641 | `0.0.0.0/0` | Tailscale WireGuard to peers that listen on the default port |
| E4 | UDP | 3478 | `0.0.0.0/0` | Tailscale STUN (NAT discovery) |
| E5 | UDP | 53 | `10.60.0.2/32` | DNS to the VPC resolver only |
| E6 | TCP | 53 | `10.60.0.2/32` | DNS over TCP (large answers) to the VPC resolver only |

Notes:
- SGs do not filter traffic to the Amazon resolver, IMDS, or Time Sync (`169.254.169.123`, the
  chrony default). E5 and E6 document intent; IMDS protection is iptables; UDP 123 out is closed.
- No inbound rule means peers cannot open a path to the instance; the instance must dial out.
  AD24 expects a direct path in the common case and accepts DERP relay over 443 as the fallback
  (still end-to-end encrypted). See C1.
- No VPC endpoints. The AD29 `aws:SourceIp` Deny assumes AWS API calls leave through the IGW with
  the EIP as source. Adding an endpoint later means revisiting that Deny.
- Trivy will flag E1 to E4 (egress to `0.0.0.0/0`). Each gets an inline `trivy:ignore` with a
  one-line reason that points to this section. There is no launch-template public IP flag any
  more (the EIP replaces it).

## 3. IAM policies in full

The full JSON for every policy (instance role trust and inline policies, both key policies, the
flow log, artifacts, and state bucket policies, the Backup role, the SNS topic policy, the three
permission sets, and the SCP documents), each statement with its `Sid` and a one-line "why", is in
[`infra/DESIGN-IAM.md`](DESIGN-IAM.md). It moved there to keep this file under 900 lines. Every
statement that uses a wildcard is listed in section 4 with its exact condition.

**AD6 deviation.** Brief 2 gives the instance role only `GetSecretValue`. Brief 4.3 requires the
root `jarvis-secrets` service to copy each daemon's token into `jarvis/<mode>/api-token` so the
Windows client can read it with the human's SSO profile. A copy into Secrets Manager is a
`PutSecretValue` call, and on a CMK-encrypted secret that call makes Secrets Manager call
`kms:GenerateDataKey` and `kms:Decrypt` as the caller (Phase 2 amendment K1: `kms:Decrypt` added;
EncryptApiTokens has an exact key ARN, so it has no section 4 row). Without these two statements brief 4.3 cannot work. Both are
scoped to exactly the two token secret ARNs (and the KMS one by encryption context); the role
cannot write the four value secrets and cannot read the token secrets. "Changed" is decided from a
local hash, not by reading the secret back.

Narrowings against the brief: `kms:Decrypt` is conditioned on `kms:ViaService` and the
encryption context rather than granted on the bare key; no `ec2:Describe*` is granted (the
CloudWatch agent takes `InstanceId` from IMDS); and the AD29 Deny makes the role's credentials
useless anywhere but the instance's EIP.

## 4. IAM wildcard allowlist

The Phase 2 test (`infra/tests/iam_wildcards.tftest.hcl`) fails on any statement with `*` or `?`
in `Resource` or `NotResource` that is not in this list. It matches `(policy, Sid, Effect, action
set, condition)`, where a condition is `(operator, key, exact value)` as written below.
`<...>` placeholders are exact ARNs and never contain `*`; `<sso:Set>` is the one exception and is
defined in DESIGN-IAM.md. Wildcards in condition values are not resource wildcards.

Test harness rules (tighter):
- Every policy is a `jsonencode()` local, never `data "aws_iam_policy_document"` (mock providers
  randomise its `json`, so the test would pass vacuously).
- The tests run with `create_permission_sets = true`, so the permission-set policies are checked.
- `no_ingress.tftest.hcl` fails on any `aws_security_group` `ingress` block, `aws_security_group_rule`
  with `type = "ingress"`, `aws_vpc_security_group_ingress_rule`, or `aws_default_security_group`
  `ingress`. It also fails on any `aws_key_pair`, any `key_name` argument, `aws_iam_user`,
  `aws_iam_access_key`, or `aws_secretsmanager_secret_version`.

### 4.1 `Resource: "*"`

| # | Policy | Sid | Effect | Actions | Required condition (operator, key, value) |
|---|---|---|---|---|---|
| W1 | instance `jarvis-telemetry` | PutMetricDataJarvisNamespace | Allow | `cloudwatch:PutMetricData` | `StringEquals`, `cloudwatch:namespace`, `Jarvis` |
| W2 | JarvisOperator | SsmAndEc2ReadOnly | Allow | `ssm:DescribeInstanceInformation, ssm:GetCommandInvocation, ssm:ListCommandInvocations, ssm:ListCommands, ssm:DescribeSessions, ec2:DescribeInstances` | `StringEquals`, `aws:RequestedRegion`, `us-east-1` |
| W3 | key `alias/jarvis` | AccountRootAdmin | Allow | `kms:*` | none; principal exactly `arn:aws:iam::ACCT:root` |
| W4 | key `alias/jarvis` | CloudWatchLogsUse | Allow | `kms:Encrypt, kms:Decrypt, kms:ReEncrypt*, kms:GenerateDataKey*, kms:DescribeKey` | `ArnLike`, `kms:EncryptionContext:aws:logs:arn`, `arn:aws:logs:us-east-1:ACCT:log-group:/jarvis/*` |
| W5 | key `alias/jarvis` | FlowLogDeliveryUse | Allow | same as W4 | `StringEquals`, `aws:SourceAccount`, `ACCT`; `ArnLike`, `aws:SourceArn`, `arn:aws:logs:us-east-1:ACCT:*` |
| W6 | key `alias/jarvis` | AlarmsAndBudgetsToSns | Allow | `kms:Decrypt, kms:GenerateDataKey*` | `StringEquals`, `aws:SourceAccount`, `ACCT`; principals exactly `cloudwatch.amazonaws.com`, `budgets.amazonaws.com` |
| W7 | key `alias/jarvis` | BackupRoleUse | Allow | `kms:Decrypt, kms:DescribeKey, kms:GenerateDataKeyWithoutPlaintext, kms:ReEncrypt*` | `StringEquals`, `kms:ViaService`, `["ec2.us-east-1.amazonaws.com", "backup.us-east-1.amazonaws.com"]`; principal exactly `<role:jarvis-backup>` |
| W8 | key `alias/jarvis` | BackupRoleGrants | Allow | `kms:CreateGrant` | `Bool`, `kms:GrantIsForAWSResource`, `true` |
| W9 | key `alias/jarvis-tfstate` | AccountRootAdmin | Allow | `kms:*` | none; principal exactly account root |
| W10 | SCP `jarvis-region-deny` | DenyOutsideHomeRegion | Deny | `NotAction` = the S1 list | `StringNotEquals`, `aws:RequestedRegion`, `["us-east-1"]` |
| W11 | SCP `jarvis-org-guard` | DenyLeaveOrganization | Deny | `organizations:LeaveOrganization` | none |
| W12 | SCP `jarvis-org-guard` | DenyCloudTrailTampering | Deny | the S2 list (8 actions) | none |
| W13 | instance `jarvis-secrets` | DenyOffInstance | Deny | `*` | `NotIpAddress`, `aws:SourceIp`, `<eip>/32`; `Bool`, `aws:ViaAWSService`, `false` |
| W14 | instance `jarvis-artifacts` | DenyOffInstance | Deny | `*` | same as W13 |
| W15 | instance `jarvis-telemetry` | DenyOffInstance | Deny | `*` | same as W13 |
| W16 | SCP `jarvis-guardrail` | DenyLongLivedCredsAndIngress | Deny | the five S3 actions | none |

In a key policy, `"Resource": "*"` means "this key", so W3 to W9 cannot be narrowed. W13 to W15
must be `Deny` with `Action: "*"`; an `Allow` with `Action: "*"` anywhere fails the test.

### 4.2 ARN patterns containing `*` or `?`

`B` is the bucket named in the Policy column.

| # | Policy | Sid | Effect | Actions | Resource pattern(s) | Required condition (operator, key, value) |
|---|---|---|---|---|---|---|
| P1 | instance `jarvis-artifacts` | ReadArtifacts | Allow | `s3:GetObject` | `jarvis-artifacts-ACCT/releases/*`, `.../bootstrap/*` | none |
| P2 | instance `jarvis-telemetry` | WriteJarvisLogs | Allow | `logs:CreateLogStream, logs:PutLogEvents, logs:DescribeLogStreams` | `log-group:/jarvis/work:*`, `/jarvis/personal:*`, `/jarvis/cloud-init:*` | none |
| P3 | JarvisClient | ReadTokenSecrets | Allow | `secretsmanager:GetSecretValue` | `secret:jarvis/work/api-token-??????`, `secret:jarvis/personal/api-token-??????` | none |
| P4 | JarvisClient | DecryptTokenSecrets | Allow | `kms:Decrypt` | `arn:aws:kms:us-east-1:ACCT:key/*` | `ForAnyValue:StringEquals`, `kms:ResourceAliases`, `alias/jarvis`; `StringEquals`, `kms:ViaService`, `secretsmanager.us-east-1.amazonaws.com`; `StringLike`, `kms:EncryptionContext:SecretARN`, the two P3 ARNs |
| P5 | JarvisOperator | SessionAndCommandOnJarvisInstance | Allow | `ssm:StartSession, ssm:SendCommand` | `arn:aws:ec2:us-east-1:ACCT:instance/*` | `StringEquals`, `aws:ResourceTag/app`, `jarvis`; `BoolIfExists`, `ssm:SessionDocumentAccessCheck`, `true` |
| P6 | JarvisOperator | OwnSessionsOnly | Allow | `ssm:TerminateSession, ssm:ResumeSession` | `arn:aws:ssm:us-east-1:ACCT:session/*` | none (self-restriction dropped, see DESIGN-IAM.md 3.12) |
| P7 | JarvisOperator | WriteReleases | Allow | `s3:PutObject, s3:GetObject` | `jarvis-artifacts-ACCT/releases/*` | none |
| P8 | JarvisOperator | PutValueSecrets | Allow | `secretsmanager:PutSecretValue, secretsmanager:DescribeSecret` | `secret:jarvis/{work,personal,shared,tailscale}-??????` (4 ARNs) | none |
| P9 | JarvisOperator | JarvisKeyViaServices | Allow | `kms:GenerateDataKey, kms:Decrypt` | `arn:aws:kms:us-east-1:ACCT:key/*` | `ForAnyValue:StringEquals`, `kms:ResourceAliases`, `alias/jarvis`; `StringEquals`, `kms:ViaService`, `["s3.us-east-1.amazonaws.com", "secretsmanager.us-east-1.amazonaws.com"]` |
| P10 | artifacts bucket | DenyInsecureTransport | Deny | `s3:*` | `B`, `B/*` | `Bool`, `aws:SecureTransport`, `false` |
| P11 | artifacts bucket | DenyUnlistedPrincipals | Deny | `s3:*` | `B`, `B/*` | `ArnNotLike`, `aws:PrincipalArn`, `<allowed>` (4 ARNs) |
| P12 | artifacts bucket | DenyInstanceWrites | Deny | `s3:PutObject, s3:DeleteObject, s3:DeleteObjectVersion, s3:PutObjectAcl` | `B/*` | none; principal exactly `<role:jarvis-instance>` |
| P13 | artifacts bucket | DenyWrongKmsKey | Deny | `s3:PutObject` | `B/*` | `Null`, `s3:x-amz-server-side-encryption-aws-kms-key-id`, `false`; `StringNotEquals`, `s3:x-amz-server-side-encryption-aws-kms-key-id`, `<key:jarvis>` (full ARN). No-header PutObject and UploadPart are allowed |
| P14 | artifacts bucket | DenyNonKmsEncryption | Deny | `s3:PutObject` | `B/*` | `Null`, `s3:x-amz-server-side-encryption`, `false`; `StringNotEquals`, `s3:x-amz-server-side-encryption`, `aws:kms` |
| P15 | artifacts bucket | DenyKmsWithoutKeyId | Deny | `s3:PutObject` | `B/*` | `StringEquals`, `s3:x-amz-server-side-encryption`, `aws:kms`; `Null`, `s3:x-amz-server-side-encryption-aws-kms-key-id`, `true` |
| P16 | state bucket | DenyInsecureTransport | Deny | `s3:*` | `B`, `B/*` | `Bool`, `aws:SecureTransport`, `false` |
| P17 | state bucket | DenyUnlistedPrincipals | Deny | `s3:*` | `B`, `B/*` | `ArnNotLike`, `aws:PrincipalArn`, `<admins>` (2 ARNs) |
| P18 | state bucket | DenyWrongKmsKey | Deny | `s3:PutObject` | `B/*` | `Null`, `s3:x-amz-server-side-encryption-aws-kms-key-id`, `false`; `StringNotEquals`, `s3:x-amz-server-side-encryption-aws-kms-key-id`, `<key:jarvis-tfstate>` (full ARN). No-header state/lock writes and UploadPart are allowed |
| P19 | state bucket | DenyNonKmsEncryption | Deny | `s3:PutObject` | `B/*` | as P14 |
| P20 | state bucket | DenyKmsWithoutKeyId | Deny | `s3:PutObject` | `B/*` | as P15 |
| P21 | flow log bucket | AWSLogDeliveryWrite | Allow | `s3:PutObject` | `jarvis-flowlogs-ACCT/AWSLogs/ACCT/*` | `StringEquals`, `aws:SourceAccount`, `ACCT`; `StringEquals`, `s3:x-amz-acl`, `bucket-owner-full-control`; `ArnLike`, `aws:SourceArn`, `arn:aws:logs:us-east-1:ACCT:*`; principal exactly `delivery.logs.amazonaws.com` |
| P22 | flow log bucket | DenyInsecureTransport | Deny | `s3:*` | `B`, `B/*` | `Bool`, `aws:SecureTransport`, `false` |

`Principal: "*"` appears only in Deny statements (P10, P11, P13 to P20, P22, and SNS
DenyInsecurePublish). The test also fails on any `Allow` with `Principal: "*"`.

### 4.3 AWS managed policy attachments (the only allowed set)

| Attached to | Policy ARN |
|---|---|
| `jarvis-instance` | `arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore` |
| `jarvis-backup` | `arn:aws:iam::aws:policy/service-role/AWSBackupServiceRolePolicyForBackup` |
| `jarvis-backup` | `arn:aws:iam::aws:policy/service-role/AWSBackupServiceRolePolicyForRestores` |
| `JarvisAdmin` | `arn:aws:iam::aws:policy/AdministratorAccess` |

These contain AWS-authored wildcards. The test asserts that this exact set is attached and
nothing else.

**Totals: 16 `Resource: "*"` statements (W1 to W16) and 22 pattern statements (P1 to P22), 38 in
all.**

## 5. Secrets

| Secret | Shape (AD7) | Read by | Written by |
|---|---|---|---|
| `jarvis/work` | `{"SLACK_BOT_TOKEN": "...", "SLACK_APP_TOKEN": "...", "SLACK_OWNER_USER_ID": "...", "SLACK_MCP_URL": "...", "SLACK_WORK_TOKEN": "...", "GMAIL_MCP_URL": "...", "GMAIL_WORK_TOKEN": "...", "JEV_API_URL": "...", "JEV_API_KEY": "..."}`, optional `"ANTHROPIC_API_KEY"` | root `jarvis-secrets sync` | human, JarvisOperator `put-secret-value` |
| `jarvis/personal` | `{"TELEGRAM_BOT_TOKEN": "...", "TELEGRAM_OWNER_CHAT_ID": "...", "GMAIL_MCP_URL": "...", "GMAIL_PERSONAL_TOKEN": "...", "OFW_MCP_URL": "...", "OFW_MCP_TOKEN": "...", "GMAIL_WATCH_CLIENT_SECRETS": "<client secrets JSON as a string>", "JEV_API_URL": "...", "JEV_API_KEY": "..."}`, optional `"ANTHROPIC_API_KEY"` | root `jarvis-secrets sync` | human |
| `jarvis/shared` | `{}` by default (AD23). May hold `JEV_API_URL` / `JEV_API_KEY` if one Jev key serves both modes, then remove them from the mode secrets | root `jarvis-secrets sync` (merged into both) | human |
| `jarvis/tailscale` | `{"authkey": "tskey-auth-..."}` | root, cloud-init only | human |
| `jarvis/work/api-token` | plain string, 64 lowercase hex (AD16 reads `SecretString` as text) | JarvisClient | root `publish-token work` |
| `jarvis/personal/api-token` | plain string, 64 lowercase hex | JarvisClient | root `publish-token personal` |

Key names come from `env/*.env.example`. Claude Code signs in per user with `claude login` through
`scripts/oauth-login.sh` (credentials in that user's `~/.claude`); a per-mode `ANTHROPIC_API_KEY`
in the mode secret is the supported alternative (already in `PASSTHROUGH`) (AD23). Any value that
is identical in both mode secrets fails `shared_violations` unless its key is in `jarvis/shared`.
That includes `GMAIL_MCP_URL` if both modes use the same URL: move it to `jarvis/shared` or the
sync writes nothing, by design. (`JEV_*` keys are exempt only in the old local check; AD7 has no
such exemption.) The value secrets hold only strings;
`jarvis-secrets` rejects non-string values and keys outside `^[A-Z][A-Z0-9_]{0,63}$`, and reports
key names, never values.

**Sync rules.**
1. Fetch `work`, `personal`, `shared` as root. A secret with no version yet
   (`ResourceNotFoundException`) is "unpopulated": log `secrets_unpopulated` with the name and
   exit 1.
2. `merged_<mode> = shared | <mode>`, where a mode key that also exists in `shared` is a
   violation.
3. `shared_violations(work, personal, shared_keys)`: any violation means write nothing and
   exit 1 (AD7).
4. Privilege-drop write (tighter). Root never opens a path inside a mode user's home. It pipes the
   `KEY=value` text to `runuser -u jarvis-<mode> -- /opt/jarvis/libexec/jarvis-write-env`, which
   writes `~/.jarvis/env.tmp` (O_CREAT|O_EXCL|O_NOFOLLOW, 0600), fsyncs, and renames it to
   `~/.jarvis/env`. A symlink planted by the mode user can then only hit files that user already
   owns.
5. `publish-token <mode>` reads the token the same way (`runuser -u jarvis-<mode> -- cat
   ~/.jarvis/api_token`), so a symlink to the other home fails on permissions. It accepts only
   `^[0-9a-f]{64}\n?$`, waits up to 30 s for the file, and compares sha256 with
   `/var/lib/jarvis-secrets/<mode>.token.sha256`. It calls `PutSecretValue` only on a change, and
   rewrites the local hash only after `PutSecretValue` succeeds, so a failed publish is retried on
   the next start. Errors log `error_class` only.

**Rotation.**

| Secret | Procedure |
|---|---|
| `jarvis/work`, `jarvis/personal`, `jarvis/shared` | Revoke or reissue at the provider. `aws secretsmanager put-secret-value --secret-id jarvis/<name> --secret-string file:///dev/stdin` (value from a password manager pipe, never argv or shell history). Then `make secrets-sync`: the SSM doc runs `jarvis-secrets sync` and restarts only the modes whose env changed. A `shared` change restarts both |
| `jarvis/tailscale` | Used only at first boot. Create a one-off, pre-authorised, tagged (`tag:jarvis`), 1-day-expiry key in the admin console just before provisioning. After the join, overwrite with `{"authkey": ""}`. Node re-auth: new key, then a JarvisOperator shell as root runs `tailscale up --force-reauth --auth-key=file:/run/jarvis/ts-authkey` with the same tmpfs handling |
| `jarvis/<mode>/api-token` | SSM `jarvis-restart Mode=<mode>` with `Rotate=true`: `runuser -u jarvis-<mode> -- rm -f -- ~/.jarvis/api_token` (AD31, never `rm` as root), restart `jarvis@<mode>`. The daemon creates a new token (O_EXCL); `ExecStartPost` publishes it; the client gets 401 and refetches (AD16) |
| Claude and MCP OAuth tokens | Not in Secrets Manager. They live in `~/.claude*` per user on EBS. Rotate by revoking at the provider and rerunning `scripts/oauth-login.sh <mode>` |

**Tailscale key tmpfs handling (cloud-init).**
1. `umask 077; install -d -m 0700 -o root -g root /run/jarvis` (`/run` is tmpfs).
2. `aws secretsmanager get-secret-value --secret-id jarvis/tailscale --query SecretString --output text | jq -r .authkey > /run/jarvis/ts-authkey`.
   The value travels by pipe only. It is never in argv, an env var, or a shell variable. `set -x`
   is forbidden in `bootstrap.sh`.
3. An empty key means retry every 30 s for up to 30 minutes, then log `tailscale_key_missing`
   and exit 1.
4. `tailscale up --auth-key=file:/run/jarvis/ts-authkey --advertise-tags=tag:jarvis --hostname=jarvis --ssh=false --accept-dns=false`
   (MagicDNS on the box is not needed, and egress 53 goes only to the VPC resolver). Precondition
   (AD24): the tailnet policy from 10.5 is applied and verified before this first boot.
5. `shred -u /run/jarvis/ts-authkey` in a `trap ... EXIT`, so it runs on failure too.

## 6. Instance layout and boot sequence

### 6.1 Users (AD17)

| User | uid/gid | Home | Shell | Groups |
|---|---|---|---|---|
| `jarvis-work` | 2001 | `/home/jarvis-work` 0700 | `/usr/sbin/nologin` | own group only |
| `jarvis-personal` | 2002 | `/home/jarvis-personal` 0700 | `/usr/sbin/nologin` | own group only |
| `jarvis-build` | system uid | none | `/usr/sbin/nologin` | own group only; runs the on-box tests (8.3) |
| `ssm-user` | created by the SSM agent | | bash | sudo (SSM default) |

No mode user is in `adm`, `systemd-journal`, `sudo`, `docker`, or `procadm`, and none has a
password. `/etc/cron.allow` and `/etc/at.allow` contain `root` only. Group `procadm` has no
members; it is the `/proc` `gid=` exemption for root tools (AD32).

**AD31 rule (root never follows a mode user's paths).** Any root tool that reads, writes,
deletes, or lists anything under `/home/jarvis-<mode>` does that step as the user:
`runuser -u jarvis-<mode> -- <cmd>`. A symlink planted by a compromised mode can then reach only
files that mode already owns. Anything that must stay root opens with `O_NOFOLLOW` and refuses
symlinks and non-regular files. Applied in: `jarvis-secrets sync` (write-env) and
`publish-token` (section 5), token rotation (section 5), `jarvis-syncthing-render` (runs entirely
as the user, 6.3), `jarvis-status` outbox counts (8.2), `make sync-agents` (rsync as the user,
8.3), and the bootstrap Claude install (6.4).

### 6.2 Directories

| Path | Owner, mode | Content |
|---|---|---|
| `/opt/jarvis/releases/<sha>/` | root 0755 | Unpacked release, `.venv/` inside, `.complete` marker |
| `/opt/jarvis/current` | root symlink | -> `releases/<sha>` |
| `/opt/jarvis/bin/` | root 0755, each file root 0700 | Root-run tools only: `jarvis-secrets`, `jarvis-deploy`, `jarvis-status`, `jarvis-logfilter`, `post-boot-assert`, `jarvis-imds-guard` |
| `/opt/jarvis/libexec/` | root 0755, each file root 0755 | Tools a mode user executes: `jarvis-write-env`, `jarvis-vault-commit`, `jarvis-syncthing-render` |
| `/opt/jarvis/bootstrap/<sha256>/` | root 0700 | Unpacked `ops/aws/` from the bootstrap tarball |
| `/home/jarvis-<mode>/vault/` | user 0700 | Live vault, git repo (AD17) |
| `/home/jarvis-<mode>/.jarvis/` | user 0700 | `env` 0600, `api_token` 0600, `tone_flags.json`, `sends.log`, and (personal) `gmail_watch_token.json`, `ofw_watch_seen.json` |
| `/home/jarvis-<mode>/.claude/`, `.claude.json` | user 0700/0600 | Claude Code and MCP OAuth state |
| `/home/jarvis-<mode>/.local/bin/claude` | user | Claude Code native install, per user |
| `/home/jarvis-<mode>/.local/state/syncthing/` | user 0700 | `config.xml`, `cert.pem`, `key.pem` |
| `/var/log/jarvis/` | root:adm 0750 | `<mode>.jsonl` 0640 root:adm, logrotate daily, keep 7, `copytruncate` |
| `/var/lib/jarvis-logexport/<mode>.cursor` | root 0600 | journald cursor |
| `/var/lib/jarvis-secrets/<mode>.token.sha256` | root 0600 | Last published token hash |
| `/run/jarvis/` | root 0700, tmpfs | Transient `ts-authkey`, deploy header file |
| `/etc/iptables/rules.v4`, `rules.v6` | root 0640 | Section 6.5 |
| `/etc/jarvis/instance.env` | root 0644 | Bucket name, region, secret names (no secrets) |
| `/etc/fstab` `/proc` line | root | `proc /proc proc defaults,hidepid=invisible,gid=2100 0 0` (numeric gid of `procadm`, AD32). Drop-ins `SupplementaryGroups=procadm` for `systemd-logind` and `polkit` (if present) |

### 6.3 systemd units

```
netfilter-persistent.service            (Before=network-pre.target; loads rules.v4/v6)
  -> jarvis-imds-guard.service          (oneshot, RemainAfterExit, After=netfilter-persistent)
       -> jarvis-secrets.service        (oneshot, RemainAfterExit, root; Requires+After=imds-guard; Wants+After=network-online.target)
            -> jarvis@work.service      (User=jarvis-work; Requires+After=jarvis-secrets; Wants+After=tailscaled)
            |    ExecStartPost=-+/opt/jarvis/bin/jarvis-secrets publish-token work
            -> jarvis@personal.service  (same, personal)
       -> syncthing@jarvis-work.service      (User=jarvis-work; Requires=imds-guard; Wants+After=tailscaled;
       -> syncthing@jarvis-personal.service   ExecStartPre=/opt/jarvis/libexec/jarvis-syncthing-render %i, no +)
       -> jarvis-vault-commit@work.timer     (OnCalendar=*:0/10, Persistent=true) -> jarvis-vault-commit@work.service (User=jarvis-work,
                                             ExecStart=/opt/jarvis/libexec/jarvis-vault-commit)
       -> jarvis-vault-commit@personal.timer -> jarvis-vault-commit@personal.service
tailscaled.service                      (package unit)
jarvis-logexport@work.service           (root; Before=jarvis@work; journalctl -f -o cat -u jarvis@work
jarvis-logexport@personal.service        --cursor-file=/var/lib/jarvis-logexport/work.cursor
                                          | /opt/jarvis/bin/jarvis-logfilter work >> /var/log/jarvis/work.jsonl)
amazon-cloudwatch-agent.service         (root; tails /var/log/jarvis/*.jsonl and cloud-init-output.log)
```

Rules:
- Every unit that runs as a mode user has `Requires=jarvis-imds-guard.service` and `After=` it. No
  mode-user process starts unless the IMDS rule is verified.
- `jarvis@.service` (owned by app-engineer, `ops/jarvis@.service`): `Environment=JARVIS_DEPLOYMENT=aws`,
  `ExecStart=/opt/jarvis/current/.venv/bin/python -m jarvis.main --mode %i`,
  `WorkingDirectory=/opt/jarvis/current`, `Restart=always`, `RestartSec=10`,
  `StartLimitIntervalSec=0`, `UMask=0077`, `Environment=LANG=C.UTF-8 PATH=/home/jarvis-%i/.local/bin:/usr/local/bin:/usr/bin:/bin`.
  Hardening (tighter; also on `syncthing@` and `jarvis-vault-commit@`): `ProtectProc=invisible`,
  `ProcSubset=pid`, `ProtectHome=tmpfs`, `BindPaths=/home/jarvis-%i`, `PrivateTmp=yes`,
  `NoNewPrivileges=yes`, `ProtectSystem=strict`, `ReadWritePaths=/home/jarvis-%i`,
  `PrivateDevices=yes`, `CapabilityBoundingSet=`, `RestrictSUIDSGID=yes`, `ProtectKernelTunables=yes`,
  `ProtectControlGroups=yes`, `RestrictAddressFamilies=AF_UNIX AF_INET AF_INET6 AF_NETLINK`,
  `LimitCORE=0`. `claude -p <prompt>` puts the prompt in argv (`brain.build_ask_cmd`), so without
  `ProtectProc` the other uid reads it from `/proc/<pid>/cmdline`; interactive `sudo -u` shells sit
  outside any unit, hence also `hidepid=invisible,gid=procadm` on `/proc` (AD32).
- A failed `jarvis-secrets` is a dependency failure that `Restart=always` does not retry; the
  daemons stay down and the heartbeat alarms fire (intended). `systemctl restart jarvis-secrets`
  restarts both daemons (`Requires=` propagates), so `jarvis-secrets-sync` runs the tool directly
  and restarts only the changed modes.
- `ExecStartPost=-+`: `+` runs it as root, `-` keeps the daemon up if publishing fails. Failure
  logs `token_publish_failed` into the unit journal, which ships to `/jarvis/<mode>`.
- `jarvis-syncthing-render` runs entirely as the mode user (AD31; `tailscale ip -4` works
  unprivileged). It rewrites the listen addresses on every start (`tcp://<ip>:22000`,
  `quic://<ip>:22000` for work; 22001 for personal). It exits non-zero if the output is empty or
  not inside `100.64.0.0/10`. Because it is `ExecStartPre=`, Syncthing never starts without a
  rendered config, so it never falls back to a default listen on `0.0.0.0` (AD8);
  `Restart=on-failure`, `RestartSec=10` retries until Tailscale is up. It also enforces the AD8
  flags, a GUI user and password (random, stored only as a bcrypt hash in that user's
  `config.xml`), and, for the workstation device, `introducer=false` and
  `autoAcceptFolders=false`.
- `jarvis-logfilter <mode>` (root, AD2 tightened) forwards only JSON-object lines, dropping
  tracebacks, library prints, and systemd messages, and re-applies the AD11 key deletion as a second
  layer. Every 300 s with drops it writes `{"event":"nonjson_dropped","mode":"<mode>","count":N}`.
- `jarvis-vault-commit@` runs `git -c core.hooksPath=/dev/null -c core.fsmonitor=false add -A`,
  then commits only if `git diff --cached --quiet` fails. Author is `jarvis-<mode>
  <jarvis-<mode>@localhost>`. It never pushes.

### 6.4 AD18 bootstrap flow

1. Terraform: `data "archive_file"` over `ops/aws/` (output `tar.gz`; see C10) ->
   `aws_s3_object` at `bootstrap/<output_sha256>/bootstrap.tar.gz`. The user_data template gets
   bucket, key, and sha256.
2. user_data (< 16 KB, `set -euo pipefail`, no `set -x`, every `aws` call with `--region
   us-east-1`): apt install the brief 3.1 package list,
   awscli v2 from the official zip (signature checked with the AWS CLI public key), and the
   CloudWatch agent `.deb` for the architecture.
3. `aws s3 cp s3://.../bootstrap.tar.gz /opt/jarvis/bootstrap/`, `echo "<sha256>  bootstrap.tar.gz" | sha256sum -c`,
   extract, and run `bootstrap.sh`.
4. `bootstrap.sh`, in this order (tighter than brief 3, which puts users before iptables):
   a. Install `rules.v4` and `rules.v6`, `netfilter-persistent reload`, then `jarvis-imds-guard
      --rules-only` (`iptables -C`, `ip6tables -C`, root reaches IMDS; the users do not exist yet).
   b. Create users, `procadm` (gid 2100), directories, cron/at allow lists, the `/proc` fstab
      line (`mount -o remount /proc`), then the full `jarvis-imds-guard`. `systemctl mask ssh.service ssh.socket` and `apt-get purge
      ec2-instance-connect` (AD24: nothing may listen on 22).
   c. Tailscale join (section 5).
   d. Install units, the CloudWatch agent config, and Syncthing templates; run `daemon-reload`.
   e. Per user: install Claude Code (native) as that user (`runuser`, AD31).
   e2. `unattended-upgrades`, security origin only, `Automatic-Reboot "true"`,
      `Automatic-Reboot-Time` from `var.auto_reboot_time_utc` (default `09:30`, after the backup
      windows; AD30). Rendered into user_data as a plain value.
   f. Read `releases/DEPLOYED`. A 403 or a 404 both mean "no release yet" and are not errors.
      If a sha is present, run `jarvis-deploy <sha>` (same code path as SSM). A deploy failure is
      logged (`first_deploy_failed`) and bootstrap continues.
   g. Enable and start everything. Run `post-boot-assert`. The script exits non-zero at the end
      if step f or the assert failed, so the failure shows in cloud-init output and
      `/jarvis/cloud-init`.
5. Later boots do not run user_data. systemd brings up section 6.3 from the persisted units.

### 6.5 iptables

`/etc/iptables/rules.v4` (filter table, OUTPUT chain, in order):
```
-A OUTPUT -d 169.254.169.254/32 -m owner --uid-owner 0 -j ACCEPT
-A OUTPUT -d 169.254.169.254/32 -j REJECT --reject-with icmp-port-unreachable
-A OUTPUT -o lo -p tcp -m multiport --dports 8781,8782 -m owner --uid-owner 0 -j ACCEPT
-A OUTPUT -o lo -p tcp -m multiport --dports 8781,8782 -j REJECT --reject-with tcp-reset
-A OUTPUT -o lo -p tcp --dport 8384 -m owner --uid-owner 0 -j ACCEPT
-A OUTPUT -o lo -p tcp --dport 8384 -m owner --uid-owner 2001 -j ACCEPT
-A OUTPUT -o lo -p tcp --dport 8384 -j REJECT --reject-with tcp-reset
-A OUTPUT -o lo -p tcp --dport 8385 -m owner --uid-owner 0 -j ACCEPT
-A OUTPUT -o lo -p tcp --dport 8385 -m owner --uid-owner 2002 -j ACCEPT
-A OUTPUT -o lo -p tcp --dport 8385 -j REJECT --reject-with tcp-reset
```
`rules.v6`: the same IMDS pair for `fd00:ec2::254/128` (IPv6 IMDS is disabled; defense in depth).
- Lines 1-2 (brief 1): only root (SSM agent, CloudWatch agent as root, cloud-init, `jarvis-secrets`) reaches IMDS.
- Lines 3-4 (tighter): no mode user can open a local connection to either API port; only root's
  health check can. Tailnet clients arrive on `tailscale0` (INPUT); local connections to the
  box's own Tailscale IP go out through `lo`.
- Lines 5-10 (tighter): each Syncthing GUI is reachable only by its own user and root; the other
  user could otherwise add a device and export the vault over egress 443.

**Boot-time check** (full mode: after bootstrap step 4b and at every boot via
`jarvis-imds-guard.service`, oneshot; `--rules-only` runs only step 2 plus the root call):
1. Assert `id -u jarvis-work` is `2001` and `id -u jarvis-personal` is `2002`; the rules match
   on those uids. A mismatch fails the guard.
2. `iptables -C` for lines 1 and 2, `ip6tables -C` for the v6 pair.
3. Functional test: `runuser -u jarvis-<mode> -- curl -s -m 2 -X PUT http://169.254.169.254/latest/api/token -H 'X-aws-ec2-metadata-token-ttl-seconds: 30'`
   must exit with code `7` (connection refused, from the REJECT) for each user. Any other code,
   including success or a timeout, fails. The same call as root must succeed.
4. On failure: one `netfilter-persistent reload` and a recheck. If it still fails: `logger -p
   auth.crit "jarvis-imds-guard: IMDS rule missing"`, exit 1. Every mode-user unit stays down;
   the heartbeat alarms fire within 15 minutes.

`post-boot-assert` (brief 9.2) checks: cross-home reads fail both ways; IMDS unreachable for both
users; `ss -ltnp` shows nothing on `0.0.0.0` or `[::]` for 8781, 8782, 22000, 22001, 8384, 8385,
and no listener at all on 22; `ssh.service` masked; the EIP associated;
`sudo -u jarvis-work ps -e` shows no `jarvis-personal` process and vice versa (AD32);
APIs bound to the Tailscale IP; Syncthing `relaysEnabled=false` and
`globalAnnounceEnabled=false`; GUI ports rejected for the other uid; `/proc` of the other daemon
invisible from inside its unit.

## 7. Observability

### 7.1 Log groups (AD2)

| Group | Source file | Retention | KMS |
|---|---|---|---|
| `/jarvis/work` | `/var/log/jarvis/work.jsonl` | 30 days | `<key:jarvis>` |
| `/jarvis/personal` | `/var/log/jarvis/personal.jsonl` | 30 days | `<key:jarvis>` |
| `/jarvis/cloud-init` | `/var/log/cloud-init-output.log` | 30 days | `<key:jarvis>` |

**Off-box content controls (AD2 and AD11 tightened).** The AD11 key filter alone is not enough:
`httpx` logs `HTTP Request: POST https://api.telegram.org/bot<TOKEN>/getUpdates` at INFO, and
`log.exception` tracebacks can echo MCP args or bodies. Three layers, each with a test:
1. `logsetup` pins `httpx`, `httpcore`, `telegram`, `slack_bolt`, `slack_sdk`, `mcp`, `urllib3`,
   and `googleapiclient` to WARNING. Test: an INFO record from each is dropped.
2. The JSON formatter passes `msg` through `redact()` (the daemon's own secrets). Under the aws
   profile it never emits `exc_text`, `exc_info`, `stack_info`, or a traceback, only
   `error_class`. `log.exception` in paths that touch MCP args or bodies becomes `log.error` with
   `error_class=type(e).__name__`. Tests: a token in `msg` is redacted; an exception record has
   `error_class` and no traceback text.
3. `jarvis-logfilter` (6.3) forwards only JSON-object lines. Test: mixed input yields only the JSON
   lines plus a `nonjson_dropped` count.
`bootstrap.sh` prints no secrets and no vault content, and at first boot no personal content
exists.

### 7.2 Metric filters

| Filter | Log group | Pattern | Transformation |
|---|---|---|---|
| `jarvis-heartbeat-work` | `/jarvis/work` | `{ ($.event = "heartbeat") && ($.mode = "work") }` | ns `Jarvis`, name `Heartbeat`, value `1`, unit `Count`, dimensions `{Mode = $.mode}`, no default |
| `jarvis-heartbeat-personal` | `/jarvis/personal` | `{ ($.event = "heartbeat") && ($.mode = "personal") }` | same |
| `jarvis-ofw-watch-errors` | `/jarvis/personal` | `{ $.event = "ofw_watch_error" }` | ns `Jarvis`, name `OfwWatchErrors`, value `1`, default `0`, no dimensions |
| `jarvis-ofw-watch-disabled` | `/jarvis/personal` | `{ $.event = "ofw_watch_disabled" }` | ns `Jarvis`, name `OfwWatchDisabled`, value `1`, default `0` (tighter; not in AD1) |

A filter with dimensions cannot have a default value. That is intended: no heartbeat must mean
missing data.

### 7.3 Alarms

All alarms send to `jarvis-alerts` on ALARM and OK. A dead CloudWatch agent stops log shipping, so
the heartbeat alarms also cover agent failure.

| Alarm | Metric | Stat | Period | Eval / datapoints | Condition | Missing data | Extra action |
|---|---|---|---|---|---|---|---|
| `jarvis-heartbeat-work` | `Jarvis/Heartbeat` Mode=work | SampleCount | 300 | 3 / 3 | `< 1` | breaching | |
| `jarvis-heartbeat-personal` | `Jarvis/Heartbeat` Mode=personal | SampleCount | 300 | 3 / 3 | `< 1` | breaching | |
| `jarvis-ofw-watch-errors` | `Jarvis/OfwWatchErrors` | Sum | 300 | 3 / 3 | `> 0` | notBreaching | |
| `jarvis-ofw-watch-disabled` | `Jarvis/OfwWatchDisabled` | Sum | 300 | 1 / 1 | `> 0` | notBreaching | |
| `jarvis-disk-used` | `Jarvis/disk_used_percent` InstanceId, path=`/`, fstype=`ext4` | Maximum | 300 | 2 / 2 | `> 80` | missing | |
| `jarvis-status-instance` | `AWS/EC2 StatusCheckFailed_Instance` InstanceId | Maximum | 60 | 3 / 3 | `>= 1` | missing | |
| `jarvis-auto-recover` (compute) | `AWS/EC2 StatusCheckFailed_System` InstanceId | Maximum | 60 | 2 / 2 | `>= 1` | missing | `arn:aws:automate:us-east-1:ec2:recover` |

### 7.4 CloudWatch agent config outline (`ops/aws/cloudwatch-agent.json`)

- `agent`: `run_as_user: root`, `omit_hostname: true`, `metrics_collection_interval: 300`.
- `metrics`: `namespace: Jarvis`, `append_dimensions: {InstanceId: ${aws:InstanceId}}`, `disk`
  with `resources: ["/"]`, `measurement: ["used_percent"]`, `drop_device: true`. The metric is
  `disk_used_percent`, with dimensions exactly `InstanceId`, `path`, `fstype`.
- `logs`: `force_flush_interval: 15`; `collect_list` of the three files in 7.1, each to its group
  with `log_stream_name: {instance_id}`.
- No `retention_in_days` (Terraform owns it), no `procstat`, `mem`, or journald collection.

### 7.5 SNS and budget

- `jarvis-alerts`, CMK-encrypted, one email subscription to `var.alert_email` (required, no
  default, AD22). No alert arrives until the human clicks the confirmation link; the runbook says
  so.
- `jarvis-monthly`: `COST`, `MONTHLY`, limit `60 USD`, notifications `ACTUAL` `GREATER_THAN`
  50, 80, and 100 `PERCENTAGE` -> SNS topic.

## 8. Deploy and rollback

### 8.1 Release layout

```
s3://jarvis-artifacts-ACCT/releases/<git-sha>/jarvis-<git-sha>.tar.gz
s3://jarvis-artifacts-ACCT/releases/<git-sha>/jarvis-<git-sha>.tar.gz.sha256   ("<hex>  jarvis-<sha>.tar.gz")
s3://jarvis-artifacts-ACCT/releases/DEPLOYED                                    (text: last successful sha)
```
The tarball has `jarvis/`, `tests/`, `requirements*.txt` (hash-pinned), `pytest.ini`,
`config.yaml`, `config.aws.yaml`, `config.local.yaml`, `mcp/`, `ops/`, `vaults/` (templates
only), and `VERSION`. It never includes `env/*.env`, `.git`, `.venv`, or live vaults. `make
release` refuses a dirty tree (G1).

### 8.2 SSM document contract (AD19)

All four: `schemaVersion 2.2`, one `aws:runShellScript` step, `timeoutSeconds` 1800 (deploy) or
300 (others), runs as root. Output goes only to the SSM API (no S3 or CloudWatch output). SSM
stores stdout, so it leaves the instance: output is metadata only (rule 0.4).

| Document | Parameters | Behavior |
|---|---|---|
| `jarvis-deploy` | `Sha`: String, `allowedPattern ^[0-9a-f]{40}$` | Runs `/opt/jarvis/bin/jarvis-deploy "$SHA"` after revalidating the pattern in the script |
| `jarvis-restart` | `Mode`: String, `allowedValues [work, personal]`; `Rotate`: String, `allowedValues [false, true]`, default `false` | Restarts `jarvis@<mode>`; `Rotate=true` first removes the token as the user (section 5, AD31) |
| `jarvis-secrets-sync` | none | `jarvis-secrets sync`, restarts only changed modes, prints changed mode names |
| `jarvis-status` | none | `jarvis-status`: unit states, last heartbeat time, pending outbox counts (counted as each user via `runuser`, AD31), disk %, Tailscale `BackendState`. Counts and states only |

Phase 2 ships the four as stubs that `exit 1` with "not implemented" (AD19).

### 8.3 `jarvis-deploy <sha>` steps

1. `flock /run/jarvis-deploy.lock`.
2. Download the tarball and `.sha256` to `/opt/jarvis/releases/.staging-<sha>/` and run
   `sha256sum -c`. A mismatch exits 1 and touches nothing.
3. Unpack to `/opt/jarvis/releases/<sha>/` (root 0755). If `.complete` exists, skip steps 3 to 5.
4. `python3.12 -m venv .venv`, then `pip install --require-hashes -r requirements.txt`.
5. Tests as `jarvis-build`, never root: `h=$(mktemp -d /tmp/jarvis-test.XXXXXX)` (0700), `chown
   jarvis-build:jarvis-build "$h"`, `trap 'rm -rf -- "$h"' EXIT`, then `runuser -u jarvis-build --
   env HOME="$h" JARVIS_DEPLOYMENT=local .venv/bin/pytest -q` (`JARVIS_RUN_CLAUDE_TESTS` unset).
   A failure exits 1 and `current` is unchanged; on success touch `.complete`.
6. `prev=$(readlink /opt/jarvis/current)`.
7. Install `ops/aws/` units and tools from the release, then `systemctl daemon-reload`.
8. Atomic switch: `ln -sfn releases/<sha> /opt/jarvis/current.new && mv -T /opt/jarvis/current.new /opt/jarvis/current`.
9. `systemctl restart jarvis@work jarvis@personal`.
10. Health check per mode, every 2 s for up to 60 s:
    `curl -sf --max-time 5 -H @/run/jarvis/hdr-<mode> http://<tailscale ip -4>:<port>/health`.
    The header file (0600 root, tmpfs) holds `Authorization: Bearer <token>` and is deleted in
    a trap, so the token never appears in argv. Pass means HTTP 200, `ok == true`,
    `mode == <mode>`, and `deployment == "aws"` (AD14).
11. Pass: print `deployed <sha>`, prune to the 5 newest releases (never `current` or `prev`),
    exit 0. The Makefile then writes `releases/DEPLOYED`.
12. **Rollback condition**: any failure in steps 7 to 10. Point `current` back at `prev`,
    reinstall `prev`'s `ops/aws/` units, `daemon-reload`, restart both, rerun step 10, print
    `rolled_back <sha> -> <prev>`, exit 1. With no `prev` (first deploy): stop both daemons,
    exit 1. The live vaults are never touched.

`make sync-agents MODE=<mode>` uses an interactive SSM session (`AWS-StartInteractiveCommand`).
With `runAsEnabled = false`, SSM sessions run as `ssm-user`, and root tools run through `sudo`
inside the session; this is the resolution of AD17's "runAs" wording. The tool shows the template
diff, asks for confirmation, and applies it with `runuser -u jarvis-<mode> -- rsync` (AD31).
Session logging is off (section 1, modules/ssm). Every `aws` call in the Makefile and scripts
passes `--region us-east-1` (11.1).

## 9. Cost estimate (us-east-1, on-demand, 730 h/month)

Prices are us-east-1 public list prices as of this writing, from knowledge. None were looked up
for this document, so there are no page citations. Verify at https://aws.amazon.com/pricing/
before relying on the totals.

| Line item | Unit price | Quantity | Monthly |
|---|---|---|---|
| EC2 t4g.medium, Linux, on-demand | $0.0336/h | 730 h | $24.53 |
| t4g CPU credits | $0.04/vCPU-h surplus | 0 (`standard` mode, no surplus billing) | $0.00 |
| EBS gp3 root | $0.08/GB-month (3000 IOPS, 125 MB/s included) | 30 GB | $2.40 |
| EBS snapshots via AWS Backup (warm) | $0.05/GB-month | ~50 GB (upper estimate: ~12 GB used, 14 daily + 8 weekly incrementals) | $2.50 |
| Public IPv4: Elastic IP on a running instance (AD29) | $0.005/h | 730 h | $3.65 |
| CloudWatch Logs ingestion | $0.50/GB | 1 GB (upper estimate; heartbeats are ~15 MB) | $0.50 |
| CloudWatch Logs storage | $0.03/GB-month | 1 GB | $0.03 |
| CloudWatch custom metrics | $0.30/metric-month | 5 (Heartbeat x2, OfwWatchErrors, OfwWatchDisabled, disk_used_percent) | $1.50 |
| CloudWatch alarms (standard) | $0.10/alarm-month | 7 | $0.70 |
| Free at this volume | EC2 status check metrics; SSM Session Manager, Run Command, documents; AWS Backup for EBS (storage above only); SNS email (first 1,000); Budgets without actions; data transfer out (first 100 GB/month, < 10 GB used); IGW, VPC, SG, IAM, Identity Center | | $0.00 |
| Secrets Manager | $0.40/secret-month (+$0.05/10k calls) | 6 secrets, <1k calls | $2.40 |
| KMS customer managed keys | $1.00/key-month (+$0.03/10k requests) | 2 keys, <10k requests | $2.03 |
| S3 Standard (artifacts, state, flow logs) | $0.023/GB-month, $0.005/1k PUT | ~2 GB, ~8k PUT | $0.10 |
| VPC flow logs to S3 (vended logs) | $0.25/GB delivered | ~1 GB (upper estimate) | $0.25 |
| **Total** | | | **$40.59** |

Against the $60 budget: **$40.59, which is 68 %, leaving $19.41 of headroom.** The EIP replaces
the auto-assigned address at the same price, so the total is unchanged. The 50 % budget alert
($30) fires every month by design, because the brief fixes the thresholds and the steady-state
bill is above $30. Free tier credits, if any, only lower this. Tax is excluded.

What could push it over: CPU credit mode switched to `unlimited` under sustained load (worst case
about +$47), a log loop (ingestion at $0.50/GB), or snapshot churn from rebuilding the venv on
every deploy.

Top two levers if it ever exceeds $60:
1. **Compute.** A 1-year Compute Savings Plan or reserved instance for t4g.medium (roughly 35 to
   40 % off, about -$9), or `t4g.small` ($0.0168/h, -$12.26) if memory allows. Keep `standard`
   credits.
2. **Growth lines.** Cut snapshot retention (weekly 8 -> 4) and cap log volume (drop `info` events
   except heartbeat). Logs and snapshots are the only lines that grow with use.

## 10. Threat model

Assets: the two mode secret sets, per-mode OAuth tokens (Claude, MCP, Gmail read-only), the two
vaults, the two API tokens, the instance role, and the human's SSO session.

### 10.1 Compromised `jarvis-work` process (code execution as uid 2001)

| Target | Reachable? | Why |
|---|---|---|
| `/home/jarvis-personal` (vault, `.jarvis/env`, `api_token`, `.claude`, `.claude.json`, Syncthing keys) | No | Home 0700. Inside the unit, `ProtectHome=tmpfs` hides it entirely |
| Personal daemon env (`/proc/<pid>/environ`) | No | Different uid. `ProtectProc=invisible` also hides the pid |
| Personal `claude -p` argv (prompts) | No, with `ProtectProc=invisible` | Without it, `/proc/<pid>/cmdline` is world-readable. That is why the directive is required |
| IMDS / instance role credentials | No | iptables uid-0-only rule, verified at boot; units need the guard. Even if credentials leaked, the AD29 Deny rejects any call not from the EIP |
| Secrets Manager, S3, CloudWatch | No | No credentials. No env var or file holds AWS keys |
| Personal API `100.x:8782` | No | iptables `lo` rule rejects non-root; bearer token also required and unreadable |
| Personal Syncthing GUI `127.0.0.1:8385` | No | iptables uid rule plus GUI password |
| Personal Syncthing sync port `:22001` | Connects, but is refused | TLS device-id authentication; only the workstation's device id is accepted |
| journald (system journal) | No | Not in `adm` or `systemd-journal` |
| `/var/log/jarvis/*.jsonl` | No | 0640 root:adm |
| `/opt/jarvis/current` (code, `config*.yaml`, `mcp/*.json`) | Read | No secrets there; MCP configs are `${VAR}` placeholders only |
| Own vault, own env, own OAuth tokens, own API token | Yes | Same uid. Compromising a mode user means compromising that mode |
| Internet egress 443/80 | Yes | Can exfiltrate its own mode's data. There is no domain filter (would need a proxy; not in the brief) |
| Tailnet | Limited | Only what the ACL grants `tag:jarvis` as a source: nothing (10.5). tailscaled LocalAPI gives a non-operator user read-only status; `serve`, `funnel`, and `up` are refused |
| Outbox sends | Drafts only | It can write `outbox/<id>.md`, but a send needs the human's hash-bound approval. The executor is in the daemon, which it controls, so a code-execution compromise can call its own MCP write tools directly. Residual, bounded to work tools |
| Root | Only via a local privilege escalation | Mitigation: security-only unattended upgrades with a 09:30 UTC reboot (AD30), no sudo, no SUID tools added |

### 10.2 Compromised `jarvis-personal` process

Symmetric to 10.1 with work and personal swapped (API 8781, GUI 8384). Personal-only items: it
holds the Telegram bot token, OFW MCP token, Gmail personal MCP token, and the Gmail watcher token
(scope `gmail.readonly`, so it cannot send or modify mail). It cannot reach the work Slack tokens
or Atlassian/ClickUp OAuth. The OFW send path still needs the human's hash-checked approval
unless the attacker has code execution in the daemon, which is residual as in 10.1.

### 10.3 Compromised Windows workstation (same user as the human)

- Tailnet access to `jarvis:8781`, `:8782`, `:22000`, `:22001`, granted by the ACL.
- API tokens: held in the client's memory, which is readable by the same user. The attacker can
  call both APIs, including `/outbox` (which returns the hashes) and `/approve`. They can send
  any pending draft and ask agents to draft new ones. Bounded by `daily_write_cap` (work 20,
  personal 5) and the tone gate. Treat this as full send-as-human.
- Vaults: both are on the workstation through Syncthing, so they can be read directly. Writes
  sync to the server. Planted `.claude/settings*.json` with risky keys make the brain refuse
  (`brain.refusal`). `.git/` is not synced (AD8), so hooks cannot be planted. Prompt injection
  through notes remains.
- AWS: a cached SSO token can mint credentials for every permission set assigned to that user.
  The workstation therefore signs in as its own Identity Center user that holds only JarvisClient
  (AD25). A stolen workstation token reads the two API tokens and nothing else. If the human's
  own user (Operator, Admin) is ever signed in on the workstation, the chain is
  `ssm:StartSession` -> `ssm-user` -> sudo -> root -> both modes, or the whole account. Controls:
  MFA on every sign-in, short sessions, and operator work from a different device.
- Syncthing device key (`%LOCALAPPDATA%\Syncthing\key.pem`): stealing it lets any tailnet device
  that the ACL admits impersonate the workstation's Syncthing device to both server instances.

### 10.4 Stolen API token

- Without tailnet membership: useless (Tailscale-IP bind, no inbound SG rules).
- With a tailnet device the ACL admits: full API for that one mode (10.3), nothing else. Rotate
  with `jarvis-restart Mode=<mode> Rotate=true`. Tokens never appear in logs, argv, S3, or chat.

### 10.5 Tailscale ACL (`policy.hujson`, applied only with `manage_tailscale_acl = true`)

A tailnet on the default allow-all policy exposes every port on the instance, including 22.
Applying this policy (by Terraform or by pasting `policy.hujson` into the admin console) is
therefore a runbook precondition before the first boot (AD24). Verification: the file carries a
`tests` block that Tailscale evaluates on save (workstation group accepted on 8781, 8782,
22000/22001; `tag:jarvis` to the workstation denied; any source to `tag:jarvis:22` denied). After
the join, the runbook checks from a non-workstation device that `jarvis:8781` and `jarvis:22`
are unreachable. The instance also has nothing on 22 (6.4, `post-boot-assert`).
`tagOwners: {"tag:jarvis": ["autogroup:admin"]}`. One grant: `src: ["group:<workstation group>"]`
(name to confirm, section 12),
`dst: ["tag:jarvis"]`, `ip: ["tcp:8781","tcp:8782","tcp:22000","udp:22000","tcp:22001","udp:22001"]`.
No grant has `tag:jarvis` as the source, so the server cannot open connections to the
workstation. The workstation's Syncthing dials `tcp://jarvis:22000` / `22001`; the server holds
the workstation device with address `dynamic`, `introducer=false`, `autoAcceptFolders=false`, and
discovery off, so it never dials. Tailscale SSH is off (`--ssh=false`, no `ssh` block).

### 10.6 What the SCPs stop, and what they do not

| Stops | Does not stop |
|---|---|
| Any regional API call outside us-east-1 by any principal in `jarvis-prod`, including admin, except the exempt global services (which include `kms:*`, AD28) | Anything in us-east-1 by JarvisAdmin; KMS keys created in other regions |
| `organizations:LeaveOrganization` | Actions in the management account (SCPs never apply there) |
| Stopping, deleting, or reconfiguring CloudTrail trails and event data stores in the account (defense in depth) | The organization trail `management-events`, which lives in the management account and already records `jarvis-prod` |
| With `attach_guardrail_scp`: IAM users, access keys, EC2 key pairs, SG ingress (section 11.3) | Those, while the flag is `false` (the default) |

### 10.7 Residual risk, stated plainly

- **Root on the instance** has everything: both env files, both OAuth token sets, both vaults,
  and the instance role. Root is reachable by anyone with JarvisOperator (SSM shell ->
  `ssm-user` sudo), and by a kernel or local privilege escalation.
- **The human's SSO session** is the key to root and to the account. MFA and session length are
  the controls.
- **EBS snapshots** hold everything root holds, under the CMK. Anyone who can restore them and use
  the key (JarvisAdmin) can read them.
- **Syncthing device keys** on the workstation and on the server are long-lived. There is no
  revocation other than removing the device id on the other side.
- **Egress** is port-based, not domain-based. A compromised mode can send its own data anywhere
  over 443.
- **Providers** (Anthropic, Slack, Google, Telegram, OFW, Tailscale control plane) see content
  or metadata by design.
- **SSM Run Command output** is stored by AWS. That is why the documents print metadata only.
- **The `sparko` management profile** is a long-lived IAM user access key, used only to plan,
  apply, and later destroy `org/` (AD36) -- the management account holds no state bucket, no
  artifacts, and no operational access of its own. It can nonetheless assume
  `OrganizationAccountAccessRole` into `jarvis-prod`, which is admin there, and from there
  `ssm:StartSession` gives root on the instance: this is a latent AWS Organizations capability
  that Terraform's own usage discipline does not remove, and SCPs do not bind the management
  account either. The runbook's first step recommends moving management-account access to
  Identity Center (and deactivating the key) before the first apply; that is the human's call.

## 11. SCPs

Full SCP JSON: [DESIGN-IAM.md section S](DESIGN-IAM.md). `org/` checks read-only that the SCP
policy type is enabled before any attachment (section 1).

### 11.1 `jarvis-region-deny` (S1, attached to `jarvis-prod` only with `attach_region_scp`)

One Deny with `NotAction` and `StringNotEquals aws:RequestedRegion = us-east-1`. Source (AD28): the
brief's list (IAM, STS, Organizations, Support, CloudFront, Budgets, Route 53, KMS) merged with the
AWS Control Tower region-deny control's `NotAction` list. S1 is that list as the architect knows
it. Control Tower revises it, so Phase 2 diffs it against the current documentation. Unused items
stay in; each exempts only calls never made here. The exempt services are global, home-region, or
served from us-east-1 (known cases below). `acm:*` and `kms:*` cover CloudFront certificates and
Route 53 DNSSEC keys, which must live in us-east-1.

The cost of `kms:*`: a principal in this account can create and use KMS keys in any region. That
is accepted under AD28. Only JarvisAdmin can create keys; the instance role cannot (no
`kms:CreateKey`, plus the AD29 Deny).

Consequence: every CLI caller passes `--region us-east-1` explicitly: the Windows client
(`get-secret-value`), `user_data` and `bootstrap.sh`, the Makefile, and `scripts/*`. A default
region from a profile or the environment is not relied on.

**Warning: region-deny SCPs have broken real services before.** An SCP evaluates the region of
the API request, not the region of the resource, so services that call another region internally
or whose APIs live in us-east-1 fail with an `AccessDenied` that looks like an IAM bug. Known
cases: ACM certificates for CloudFront (us-east-1 only); Route 53 DNSSEC keys in KMS (us-east-1
only); the STS global endpoint, which signs as us-east-1 and breaks older SDK, CLI, and Terraform
paths unless `sts:*` is exempt; the Billing, Cost Explorer, Budgets, CUR, Support, Health, and
Trusted Advisor APIs and consoles (us-east-1); S3 `ListAllMyBuckets` / `GetBucketLocation` and
the S3 console; Identity Center admin calls made from the member account. Control Tower's own
list has been revised as new cases were found. Hence the dry run below before attaching.

Dry run before attaching (AD28). Both steps are read-only and create nothing:
1. `terraform plan` in `org/` with `attach_region_scp = false` (the default). Review the rendered
   JSON and diff the `NotAction` list against the current Control Tower documentation.
2. `aws accessanalyzer validate-policy --policy-type SERVICE_CONTROL_POLICY --policy-document
   file://<rendered>.json`. It must return no `ERROR` or `SECURITY_WARNING` findings.

Then `attach_region_scp = true` in a planned window, with a smoke check (`terraform plan` of
`envs/prod`, `make status`, a client token fetch). Rollback is setting `attach_region_scp = false`
and re-applying `org/` (with the `sparko` profile, from the management account) -- the SCP attaches
to, and detaches from, the `jarvis` OU, not the management account itself.

### 11.2 `jarvis-org-guard` (S2, always attached)

- DenyLeaveOrganization: the account cannot escape the SCPs.
- DenyCloudTrailTampering: nobody in the account, admin included, can stop, delete, or reconfigure
  a trail or event data store. An organization trail already exists: `management-events` in the
  management account `080109295043`, multi-region, home region us-east-1. It records
  `jarvis-prod`, so `jarvis-prod` needs no trail of its own (AD26 resolved) and this deny is
  defense in depth. The runbook says so.

### 11.3 `jarvis-guardrail` (S3, attached only with `attach_guardrail_scp = true`, AD27)

- DenyLongLivedCredsAndIngress: denies `iam:CreateUser`, `iam:CreateAccessKey`,
  `ec2:CreateKeyPair`, `ec2:ImportKeyPair`, `ec2:AuthorizeSecurityGroupIngress`. It enforces brief
  rule 0.3 at the account boundary, so even JarvisAdmin cannot create IAM users, access keys, or
  key pairs, or open an inbound rule. Default `false`; the runbook recommends turning it on after
  the first successful apply. `aws_vpc_security_group_egress_rule` uses
  `AuthorizeSecurityGroupEgress`, which is unaffected.

## 12. Assumptions and open questions

Assumptions: Ubuntu 24.04 arm64 has everything the brief lists (else `instance_type =
"t3.medium"`), and the human's Identity Center users have MFA.

AD22 to AD30 answered the first-round questions (folded in above). The human still confirms:

| # | Item | Where it is used |
|---|---|---|
| U1 | The value of `alert_email` (required, no default) | 1 (observability), 7.5 |
| U2 | The Tailscale group name for the workstation's user or device (placeholder `group:<workstation group>`) | 10.5, `policy.hujson` |
| U3 | Whether to turn on each optional flag after its dry run: `attach_region_scp`, `attach_guardrail_scp`, `create_permission_sets` (and `manage_tailscale_acl`, the fourth opt-in) | 1 (org/, tailscale_acl), 11 |

## 13. Concerns

| # | Concern | Recommendation (design follows the decision regardless) |
|---|---|---|
| C1 | AD24 expects a direct path because egress UDP 41641 is open. That holds when the peer listens on 41641 or can reach the instance's EIP. A peer behind NAT that maps to a random port cannot be reached, because egress allows UDP 41641/3478 only, so that pair uses DERP | Accepted by AD24 (DERP is end-to-end encrypted and fine for this traffic). If latency matters, widen egress UDP to 1024-65535 (still zero inbound). Not done here |
| C3 | Brief 1 egress rule 53 and the SG in general do not filter traffic to the Amazon resolver, IMDS, or Time Sync | Kept as documentation. IMDS protection relies entirely on iptables plus the guard (6.5) |
| C4 | All irreplaceable state (OAuth tokens, vault git history) is on the root volume. A replaced instance starts empty and must be restored from a snapshot | `delete_on_termination = false` and termination protection (done). Later, consider a separate `/home` data volume (+$0.08/GB) so the instance can be replaced without a restore |
| C6 | AD8 does not protect each Syncthing GUI from the other mode user on loopback | iptables uid rules and a GUI password (6.3, 6.5) |
| C7 | AD2 `journalctl -f` loses or duplicates lines when logexport restarts | `--cursor-file` (6.3) |
| C9 | AD17's shared `/opt/jarvis/current` is readable by both users. It holds no secrets today; any future secret-bearing file there would break isolation | Keep secrets out of the release (8.1). `jarvis-status` asserts no `*.env` exists under `current` |
| C10 | AD18 names `bootstrap.tar.gz`. Only recent `hashicorp/archive` versions support `type = "tar.gz"` | terraform-engineer checks the pinned version. If unsupported, use `zip` (unzip is in the package list) and keep the same sha256 path scheme |
| C11 | AD12 needs zero HIGH/CRITICAL from trivy. Egress to `0.0.0.0/0` is flagged by design | Inline `trivy:ignore:<ID>` with a reason pointing to section 2. The security-reviewer accepts each by id |
| C12 | Rule 0.4 covers logs. SSM Run Command output and Session Manager logging also leave the instance | Session logging off (1, ssm), and command output is metadata only (8.2) |
| C13 | Brief 2 lists the budget but not credit mode. `unlimited` (the t4g default) can add about $47 under sustained CPU and break the budget | `credit_specification = standard` (done) |
| C14 | AD30 says "populate the six secrets" before the second apply. Only the four value secrets are human-written; the two token secrets are written by the instance (AD6), and the operator cannot write them (3.12) | Runbook: populate `jarvis/work`, `jarvis/personal`, `jarvis/shared` (may be `{}`), and `jarvis/tailscale` |
| C15 | AD29's Deny covers every call made with the role, including the SSM agent's calls under the managed policy. Anything that changes the egress source IP (a NAT, a VPC endpoint, IPv6 egress) breaks SSM, logs, and secrets at once | Keep the network in section 2 unchanged unless the Deny is revisited in the same change. Post-boot-assert checks that the EIP is associated |
