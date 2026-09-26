# Jarvis on AWS: IAM policies in full

Companion to `infra/DESIGN.md` (section 3). Moved here to keep DESIGN.md under 900 lines. Same
notation: `ACCT` is the `jarvis-prod` account id; `<secret:...>`, `<key:...>`, `<role:...>`,
`<topic:...>` are exact ARNs resolved by Terraform, never wildcards. The wildcard allowlist is
DESIGN.md section 4.

Every statement has a `Sid` and a one-line "why". The Phase 2 wildcard test matches on `Sid`.
Every policy is built from a `jsonencode()` local, never `data "aws_iam_policy_document"`: mock
providers randomise that data source's `json`, which would make the policy tests vacuous.

`<sso:Set>` below is `arn:aws:iam::ACCT:role/aws-reserved/sso.amazonaws.com/*AWSReservedSSO_<Set>_*`.
It has no `/*/` region segment, because Identity Center roles have none when the Identity Center
home region is us-east-1 (and `*` still matches a region segment if it is elsewhere). Phase 2
confirms the Identity Center region read-only and records it in the runbook.

## 3.1 Instance role trust (`jarvis-instance`)

```json
{"Version":"2012-10-17","Statement":[
 {"Sid":"Ec2AssumeRole","Effect":"Allow","Principal":{"Service":"ec2.amazonaws.com"},"Action":"sts:AssumeRole"}]}
```
Why: only EC2 can assume it, through the instance profile.

Managed attachment: `arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore` (brief 2). Nothing else.

## 3.2 Instance role inline policies

`jarvis-secrets`:
```json
{"Version":"2012-10-17","Statement":[
 {"Sid":"ReadModeSecrets","Effect":"Allow","Action":"secretsmanager:GetSecretValue",
  "Resource":["<secret:jarvis/work>","<secret:jarvis/personal>","<secret:jarvis/shared>","<secret:jarvis/tailscale>"]},
 {"Sid":"DecryptModeSecrets","Effect":"Allow","Action":"kms:Decrypt","Resource":"<key:jarvis>",
  "Condition":{"StringEquals":{"kms:ViaService":"secretsmanager.us-east-1.amazonaws.com",
   "kms:EncryptionContext:SecretARN":["<secret:jarvis/work>","<secret:jarvis/personal>","<secret:jarvis/shared>","<secret:jarvis/tailscale>"]}}},
 {"Sid":"PublishApiTokens","Effect":"Allow","Action":"secretsmanager:PutSecretValue",
  "Resource":["<secret:jarvis/work/api-token>","<secret:jarvis/personal/api-token>"]},
 {"Sid":"EncryptApiTokens","Effect":"Allow","Action":["kms:GenerateDataKey","kms:Decrypt"],"Resource":"<key:jarvis>",
  "Condition":{"StringEquals":{"kms:ViaService":"secretsmanager.us-east-1.amazonaws.com",
   "kms:EncryptionContext:SecretARN":["<secret:jarvis/work/api-token>","<secret:jarvis/personal/api-token>"]}}},
 {"Sid":"DenyOffInstance","Effect":"Deny","Action":"*","Resource":"*",
  "Condition":{"NotIpAddress":{"aws:SourceIp":"<eip>/32"},"Bool":{"aws:ViaAWSService":"false"}}}]}
```
- ReadModeSecrets: `jarvis-secrets sync` and the Tailscale join read exactly the brief's four.
- DecryptModeSecrets: Secrets Manager decrypts with the CMK on the caller's behalf; the context
  pins it to those four secrets (tighter than the brief's bare `kms:Decrypt`).
- PublishApiTokens: AD6 deviation, see below.
- EncryptApiTokens: `PutSecretValue` on a CMK secret needs `GenerateDataKey` and `Decrypt`; pinned
  to the two token secrets. (Phase 2 amendment K1: `kms:Decrypt` added, same context pin.)

- DenyOffInstance (in all three inline policies, AD29): `<eip>` is `aws_eip.jarvis.public_ip`.
  Any call made with the role's credentials from an address other than the EIP is denied, unless
  an AWS service makes it on the role's behalf (`aws:ViaAWSService = true`; for example Secrets
  Manager or S3 calling KMS). Stolen instance credentials are useless off the box. It is repeated
  in each policy so removing one policy never removes the pin. It applies to every action the role
  holds, including those from AmazonSSMManagedInstanceCore, because an explicit Deny in any
  attached policy wins.

The instance never reads the token secrets. `publish-token` compares against a local hash in
`/var/lib/jarvis-secrets/<mode>.token.sha256` to decide "changed" (AD6), so no `GetSecretValue`
on them is needed.

**AD6 deviation.** Brief 2 gives the role only `GetSecretValue`. Brief 4.3 requires the root
`jarvis-secrets` service to copy each daemon's token into `jarvis/<mode>/api-token` so the Windows
client can read it with the human's SSO profile. A copy into Secrets Manager is a
`PutSecretValue` call, and on a CMK-encrypted secret that call makes Secrets Manager call
`kms:GenerateDataKey` and `kms:Decrypt` as the caller (Phase 2 amendment K1: `kms:Decrypt` added).
Without these two statements brief 4.3 cannot work. Both are
scoped to the two token secret ARNs; the role cannot write the four value secrets.

`jarvis-artifacts`:
```json
{"Version":"2012-10-17","Statement":[
 {"Sid":"ReadArtifacts","Effect":"Allow","Action":"s3:GetObject",
  "Resource":["arn:aws:s3:::jarvis-artifacts-ACCT/releases/*","arn:aws:s3:::jarvis-artifacts-ACCT/bootstrap/*"]},
 {"Sid":"ListArtifacts","Effect":"Allow","Action":"s3:ListBucket","Resource":"arn:aws:s3:::jarvis-artifacts-ACCT",
  "Condition":{"StringLike":{"s3:prefix":["releases/*","bootstrap/*"]}}},
 {"Sid":"DecryptArtifacts","Effect":"Allow","Action":"kms:Decrypt","Resource":"<key:jarvis>",
  "Condition":{"StringEquals":{"kms:ViaService":"s3.us-east-1.amazonaws.com"},
   "StringLike":{"kms:EncryptionContext:aws:s3:arn":["arn:aws:s3:::jarvis-artifacts-ACCT","arn:aws:s3:::jarvis-artifacts-ACCT/*"]}}},
 {"Sid":"DenyOffInstance","Effect":"Deny","Action":"*","Resource":"*",
  "Condition":{"NotIpAddress":{"aws:SourceIp":"<eip>/32"},"Bool":{"aws:ViaAWSService":"false"}}}]}
```
- ReadArtifacts: cloud-init fetches the bootstrap tarball; `jarvis-deploy` fetches releases.
- ListArtifacts: makes a missing `releases/DEPLOYED` a 404, not a 403.
- DecryptArtifacts: objects are SSE-KMS. With a bucket key the context is the bucket ARN, without
  one it is the object ARN; both are allowed.
- DenyOffInstance: as in `jarvis-secrets` (AD29).

`jarvis-telemetry`:
```json
{"Version":"2012-10-17","Statement":[
 {"Sid":"WriteJarvisLogs","Effect":"Allow","Action":["logs:CreateLogStream","logs:PutLogEvents","logs:DescribeLogStreams"],
  "Resource":["arn:aws:logs:us-east-1:ACCT:log-group:/jarvis/work:*","arn:aws:logs:us-east-1:ACCT:log-group:/jarvis/personal:*",
              "arn:aws:logs:us-east-1:ACCT:log-group:/jarvis/cloud-init:*"]},
 {"Sid":"PutMetricDataJarvisNamespace","Effect":"Allow","Action":"cloudwatch:PutMetricData","Resource":"*",
  "Condition":{"StringEquals":{"cloudwatch:namespace":"Jarvis"}}},
 {"Sid":"DenyOffInstance","Effect":"Deny","Action":"*","Resource":"*",
  "Condition":{"NotIpAddress":{"aws:SourceIp":"<eip>/32"},"Bool":{"aws:ViaAWSService":"false"}}}]}
```
- WriteJarvisLogs: the CloudWatch agent writes streams into the three pre-created groups. There is
  no `logs:CreateLogGroup`.
- PutMetricDataJarvisNamespace: the agent's `disk_used_percent`. The action has no resource ARN;
  the namespace condition is the constraint (brief 2).
- DenyOffInstance: as in `jarvis-secrets` (AD29).

No `ec2:Describe*` is granted. The agent config takes `InstanceId` from IMDS
(`${aws:InstanceId}`), not from `ec2:DescribeTags`.

## 3.3 KMS key policy (`alias/jarvis`)

```json
{"Version":"2012-10-17","Statement":[
 {"Sid":"AccountRootAdmin","Effect":"Allow","Principal":{"AWS":"arn:aws:iam::ACCT:root"},"Action":"kms:*","Resource":"*"},
 {"Sid":"CloudWatchLogsUse","Effect":"Allow","Principal":{"Service":"logs.us-east-1.amazonaws.com"},
  "Action":["kms:Encrypt","kms:Decrypt","kms:ReEncrypt*","kms:GenerateDataKey*","kms:DescribeKey"],"Resource":"*",
  "Condition":{"ArnLike":{"kms:EncryptionContext:aws:logs:arn":"arn:aws:logs:us-east-1:ACCT:log-group:/jarvis/*"}}},
 {"Sid":"FlowLogDeliveryUse","Effect":"Allow","Principal":{"Service":"delivery.logs.amazonaws.com"},
  "Action":["kms:Encrypt","kms:Decrypt","kms:ReEncrypt*","kms:GenerateDataKey*","kms:DescribeKey"],"Resource":"*",
  "Condition":{"StringEquals":{"aws:SourceAccount":"ACCT"},"ArnLike":{"aws:SourceArn":"arn:aws:logs:us-east-1:ACCT:*"}}},
 {"Sid":"AlarmsAndBudgetsToSns","Effect":"Allow","Principal":{"Service":["cloudwatch.amazonaws.com","budgets.amazonaws.com"]},
  "Action":["kms:Decrypt","kms:GenerateDataKey*"],"Resource":"*","Condition":{"StringEquals":{"aws:SourceAccount":"ACCT"}}},
 {"Sid":"BackupRoleUse","Effect":"Allow","Principal":{"AWS":"<role:jarvis-backup>"},
  "Action":["kms:Decrypt","kms:DescribeKey","kms:GenerateDataKeyWithoutPlaintext","kms:ReEncrypt*"],"Resource":"*",
  "Condition":{"StringEquals":{"kms:ViaService":["ec2.us-east-1.amazonaws.com","backup.us-east-1.amazonaws.com"]}}},
 {"Sid":"BackupRoleGrants","Effect":"Allow","Principal":{"AWS":"<role:jarvis-backup>"},"Action":"kms:CreateGrant","Resource":"*",
  "Condition":{"Bool":{"kms:GrantIsForAWSResource":"true"}}}]}
```
- AccountRootAdmin: lets IAM policies grant key use. Without it the instance role statements do
  nothing, and the key could become unmanageable.
- CloudWatchLogsUse: log group encryption. The context pins it to `/jarvis/*` groups.
- FlowLogDeliveryUse: the log delivery service writes SSE-KMS objects into the flow log bucket.
- AlarmsAndBudgetsToSns: publishing to a CMK-encrypted topic needs these two actions, and only
  on behalf of this account (`aws:SourceAccount`). No `aws:SourceArn`: it is not reliably sent.
  The only effect is that they can publish to topics that use this key.
- BackupRoleUse and BackupRoleGrants: AWS Backup snapshots and restores CMK-encrypted EBS.
- EBS use by EC2 and Secrets Manager use go through IAM (AccountRootAdmin) and the caller's
  `kms:ViaService` statements. Auto-recovery reuses the instance's existing grants.

Key policy for `alias/jarvis-tfstate`: the `AccountRootAdmin` statement only. Access is decided by
the state bucket policy and the JarvisAdmin permission set.

## 3.4 Flow log delivery

S3 flow logs need no IAM role. Delivery is the `delivery.logs.amazonaws.com` principal. It is
allowed by the flow log bucket policy below and by `FlowLogDeliveryUse` in 3.3.

```json
{"Version":"2012-10-17","Statement":[
 {"Sid":"AWSLogDeliveryWrite","Effect":"Allow","Principal":{"Service":"delivery.logs.amazonaws.com"},"Action":"s3:PutObject",
  "Resource":"arn:aws:s3:::jarvis-flowlogs-ACCT/AWSLogs/ACCT/*",
  "Condition":{"StringEquals":{"aws:SourceAccount":"ACCT","s3:x-amz-acl":"bucket-owner-full-control"},
   "ArnLike":{"aws:SourceArn":"arn:aws:logs:us-east-1:ACCT:*"}}},
 {"Sid":"AWSLogDeliveryAclCheck","Effect":"Allow","Principal":{"Service":"delivery.logs.amazonaws.com"},
  "Action":["s3:GetBucketAcl","s3:ListBucket"],"Resource":"arn:aws:s3:::jarvis-flowlogs-ACCT",
  "Condition":{"StringEquals":{"aws:SourceAccount":"ACCT"},"ArnLike":{"aws:SourceArn":"arn:aws:logs:us-east-1:ACCT:*"}}},
 {"Sid":"DenyInsecureTransport","Effect":"Deny","Principal":"*","Action":"s3:*",
  "Resource":["arn:aws:s3:::jarvis-flowlogs-ACCT","arn:aws:s3:::jarvis-flowlogs-ACCT/*"],
  "Condition":{"Bool":{"aws:SecureTransport":"false"}}}]}
```
- AWSLogDeliveryWrite: the documented delivery grant, limited to this account's log sources.
- AWSLogDeliveryAclCheck: delivery checks the bucket before writing.
- DenyInsecureTransport: TLS only.

## 3.5 to 3.7 Secrets resource policies

None. The six secrets have no resource policy. Access is the instance role (3.2) and the
permission sets (3.12). A resource policy would add a second place to audit and nothing more.

## 3.8 Artifacts bucket policy (`jarvis-artifacts-ACCT`)

`<allowed>` = `["<role:jarvis-instance>", "<sso:JarvisOperator>", "<sso:JarvisAdmin>",
"arn:aws:iam::ACCT:role/OrganizationAccountAccessRole"]`.

```json
{"Version":"2012-10-17","Statement":[
 {"Sid":"DenyInsecureTransport","Effect":"Deny","Principal":"*","Action":"s3:*",
  "Resource":["arn:aws:s3:::jarvis-artifacts-ACCT","arn:aws:s3:::jarvis-artifacts-ACCT/*"],"Condition":{"Bool":{"aws:SecureTransport":"false"}}},
 {"Sid":"DenyUnlistedPrincipals","Effect":"Deny","Principal":"*","Action":"s3:*",
  "Resource":["arn:aws:s3:::jarvis-artifacts-ACCT","arn:aws:s3:::jarvis-artifacts-ACCT/*"],
  "Condition":{"ArnNotLike":{"aws:PrincipalArn":"<allowed>"}}},
 {"Sid":"DenyInstanceWrites","Effect":"Deny","Principal":{"AWS":"<role:jarvis-instance>"},
  "Action":["s3:PutObject","s3:DeleteObject","s3:DeleteObjectVersion","s3:PutObjectAcl"],"Resource":"arn:aws:s3:::jarvis-artifacts-ACCT/*"},
 {"Sid":"DenyWrongKmsKey","Effect":"Deny","Principal":"*","Action":"s3:PutObject","Resource":"arn:aws:s3:::jarvis-artifacts-ACCT/*",
  "Condition":{"Null":{"s3:x-amz-server-side-encryption-aws-kms-key-id":"false"},
   "StringNotEquals":{"s3:x-amz-server-side-encryption-aws-kms-key-id":"<key:jarvis>"}}},
 {"Sid":"DenyNonKmsEncryption","Effect":"Deny","Principal":"*","Action":"s3:PutObject","Resource":"arn:aws:s3:::jarvis-artifacts-ACCT/*",
  "Condition":{"Null":{"s3:x-amz-server-side-encryption":"false"},"StringNotEquals":{"s3:x-amz-server-side-encryption":"aws:kms"}}},
 {"Sid":"DenyKmsWithoutKeyId","Effect":"Deny","Principal":"*","Action":"s3:PutObject","Resource":"arn:aws:s3:::jarvis-artifacts-ACCT/*",
  "Condition":{"StringEquals":{"s3:x-amz-server-side-encryption":"aws:kms"},"Null":{"s3:x-amz-server-side-encryption-aws-kms-key-id":"true"}}}]}
```
- DenyInsecureTransport: TLS only.
- DenyUnlistedPrincipals: brief 2 says access only from the instance role and the deployer. Admin
  is listed because Terraform manages the bucket and uploads `bootstrap/`.
  `OrganizationAccountAccessRole` is break-glass.
- DenyInstanceWrites: the instance can never plant a release, even if a later policy grants it.
- DenyWrongKmsKey: applies only when the key-id header is present (`Null ... false`) and names
  anything but the full ARN of `<key:jarvis>`. A PutObject or UploadPart with no SSE headers is
  allowed and gets the bucket default, `<key:jarvis>`.
- DenyNonKmsEncryption: an upload that names SSE-S3 (`AES256`) or anything but `aws:kms` is
  refused.
- DenyKmsWithoutKeyId: `aws:kms` without a key id would fall back to the AWS managed key, so it is
  refused. Callers either send no SSE headers (bucket default applies) or send both headers; a
  caller naming the key must send its full key ARN, never an alias or bare key id. Phase 2 checks
  what the S3 backend sends for `.tflock` with a real init.

## 3.9 State bucket policy (`jarvis-tfstate-ACCT`)

`<admins>` = `["<sso:JarvisAdmin>", "arn:aws:iam::ACCT:role/OrganizationAccountAccessRole"]`.

```json
{"Version":"2012-10-17","Statement":[
 {"Sid":"DenyInsecureTransport","Effect":"Deny","Principal":"*","Action":"s3:*",
  "Resource":["arn:aws:s3:::jarvis-tfstate-ACCT","arn:aws:s3:::jarvis-tfstate-ACCT/*"],"Condition":{"Bool":{"aws:SecureTransport":"false"}}},
 {"Sid":"DenyUnlistedPrincipals","Effect":"Deny","Principal":"*","Action":"s3:*",
  "Resource":["arn:aws:s3:::jarvis-tfstate-ACCT","arn:aws:s3:::jarvis-tfstate-ACCT/*"],
  "Condition":{"ArnNotLike":{"aws:PrincipalArn":"<admins>"}}},
 {"Sid":"DenyWrongKmsKey","Effect":"Deny","Principal":"*","Action":"s3:PutObject","Resource":"arn:aws:s3:::jarvis-tfstate-ACCT/*",
  "Condition":{"Null":{"s3:x-amz-server-side-encryption-aws-kms-key-id":"false"},
   "StringNotEquals":{"s3:x-amz-server-side-encryption-aws-kms-key-id":"<key:jarvis-tfstate>"}}},
 {"Sid":"DenyNonKmsEncryption","Effect":"Deny","Principal":"*","Action":"s3:PutObject","Resource":"arn:aws:s3:::jarvis-tfstate-ACCT/*",
  "Condition":{"Null":{"s3:x-amz-server-side-encryption":"false"},"StringNotEquals":{"s3:x-amz-server-side-encryption":"aws:kms"}}},
 {"Sid":"DenyKmsWithoutKeyId","Effect":"Deny","Principal":"*","Action":"s3:PutObject","Resource":"arn:aws:s3:::jarvis-tfstate-ACCT/*",
  "Condition":{"StringEquals":{"s3:x-amz-server-side-encryption":"aws:kms"},"Null":{"s3:x-amz-server-side-encryption-aws-kms-key-id":"true"}}},
 {"Sid":"DenyBucketDelete","Effect":"Deny","Principal":"*","Action":"s3:DeleteBucket","Resource":"arn:aws:s3:::jarvis-tfstate-ACCT"}]}
```
- DenyInsecureTransport: TLS only.
- DenyUnlistedPrincipals: only Terraform (JarvisAdmin) and break-glass touch state. The instance
  and the operator never do.
- DenyWrongKmsKey: applies only when the key-id header is present and is not the full ARN of
  `<key:jarvis-tfstate>`. Header-less state and `.tflock` writes (and UploadPart) are allowed and
  get the bucket default, the state key.
- DenyNonKmsEncryption: an upload that names SSE-S3 (`AES256`) or anything but `aws:kms` is
  refused.
- DenyKmsWithoutKeyId: `aws:kms` without a key id would fall back to the AWS managed key, so it is
  refused. Callers either send no SSE headers (bucket default applies) or send both headers; a
  caller naming the key must send its full key ARN, never an alias or bare key id. Phase 2 checks
  what the S3 backend sends for `.tflock` with a real init.
- DenyBucketDelete: removing the policy statement is a deliberate two-step act.

## 3.10 AWS Backup role (`jarvis-backup`)

```json
{"Version":"2012-10-17","Statement":[
 {"Sid":"BackupAssumeRole","Effect":"Allow","Principal":{"Service":"backup.amazonaws.com"},"Action":"sts:AssumeRole",
  "Condition":{"StringEquals":{"aws:SourceAccount":"ACCT"}}}]}
```
Why: only AWS Backup, acting for this account, assumes it. Permissions are the AWS managed
`AWSBackupServiceRolePolicyForBackup` and `AWSBackupServiceRolePolicyForRestores`, plus the key
policy statements in 3.3.

## 3.11 SNS topic policy (`jarvis-alerts`)

```json
{"Version":"2012-10-17","Statement":[
 {"Sid":"OwnerManage","Effect":"Allow","Principal":{"AWS":"arn:aws:iam::ACCT:root"},
  "Action":["sns:GetTopicAttributes","sns:SetTopicAttributes","sns:Subscribe","sns:ListSubscriptionsByTopic","sns:Publish","sns:DeleteTopic"],
  "Resource":"<topic:jarvis-alerts>"},
 {"Sid":"CloudWatchAlarmsPublish","Effect":"Allow","Principal":{"Service":"cloudwatch.amazonaws.com"},"Action":"sns:Publish",
  "Resource":"<topic:jarvis-alerts>","Condition":{"StringEquals":{"aws:SourceAccount":"ACCT"},
   "ArnLike":{"aws:SourceArn":"arn:aws:cloudwatch:us-east-1:ACCT:alarm:jarvis-*"}}},
 {"Sid":"BudgetsPublish","Effect":"Allow","Principal":{"Service":"budgets.amazonaws.com"},"Action":"sns:Publish",
  "Resource":"<topic:jarvis-alerts>","Condition":{"StringEquals":{"aws:SourceAccount":"ACCT"},
   "ArnLike":{"aws:SourceArn":"arn:aws:budgets::ACCT:*"}}},
 {"Sid":"DenyInsecurePublish","Effect":"Deny","Principal":"*","Action":"sns:Publish","Resource":"<topic:jarvis-alerts>",
  "Condition":{"Bool":{"aws:SecureTransport":"false"}}}]}
```
- OwnerManage: account principals (Terraform) manage the topic; delegated to IAM.
- CloudWatchAlarmsPublish: only `jarvis-*` alarms in this account (`aws:SourceAccount` and
  `aws:SourceArn` both set).
- BudgetsPublish: only budgets in this account (`aws:SourceAccount` and `aws:SourceArn` both set).
- DenyInsecurePublish: TLS only.

## 3.12 IAM Identity Center permission sets (proposed; `org/`, the human applies)

Three sets, adopted by AD25. `org/` creates them only when `create_permission_sets = true`
(default `false`, because it touches the existing Identity Center instance); the runbook gives
the manual console alternative with the same JSON. The Windows workstation signs in as a
dedicated Identity Center user that is assigned only JarvisClient. The human's own user holds
JarvisOperator and JarvisAdmin and is not signed in on the workstation.

| Set | Session | Used by | Content |
|---|---|---|---|
| `JarvisClient` | PT1H | Windows tray client (AD16), dedicated workstation user | Read the two token secrets |
| `JarvisOperator` | PT4H | `make release/deploy/status/sync-agents`, `oauth-login.sh`, setting secret values | Inline policy below |
| `JarvisAdmin` | PT1H | `terraform apply` for `bootstrap/` and `envs/prod` only | AWS managed `AdministratorAccess` |

`JarvisClient` inline policy:
```json
{"Version":"2012-10-17","Statement":[
 {"Sid":"ReadTokenSecrets","Effect":"Allow","Action":"secretsmanager:GetSecretValue",
  "Resource":["arn:aws:secretsmanager:us-east-1:ACCT:secret:jarvis/work/api-token-??????",
              "arn:aws:secretsmanager:us-east-1:ACCT:secret:jarvis/personal/api-token-??????"]},
 {"Sid":"DecryptTokenSecrets","Effect":"Allow","Action":"kms:Decrypt","Resource":"arn:aws:kms:us-east-1:ACCT:key/*",
  "Condition":{"ForAnyValue:StringEquals":{"kms:ResourceAliases":"alias/jarvis"},
   "StringEquals":{"kms:ViaService":"secretsmanager.us-east-1.amazonaws.com"},
   "StringLike":{"kms:EncryptionContext:SecretARN":["arn:aws:secretsmanager:us-east-1:ACCT:secret:jarvis/work/api-token-??????",
     "arn:aws:secretsmanager:us-east-1:ACCT:secret:jarvis/personal/api-token-??????"]}}}]}
```
- ReadTokenSecrets: brief 4.6. `org/` is applied before the secrets exist, so the 6-character
  suffix is a `?` pattern. `jarvis/work-??????` cannot match `jarvis/work/api-token-...`.
- DecryptTokenSecrets: the key ARN is unknown in `org/`; the alias and context pin it.

`JarvisOperator` inline policy:
```json
{"Version":"2012-10-17","Statement":[
 {"Sid":"SessionAndCommandOnJarvisInstance","Effect":"Allow","Action":["ssm:StartSession","ssm:SendCommand"],
  "Resource":"arn:aws:ec2:us-east-1:ACCT:instance/*","Condition":{"StringEquals":{"aws:ResourceTag/app":"jarvis"},
   "BoolIfExists":{"ssm:SessionDocumentAccessCheck":"true"}}},
 {"Sid":"SessionDocuments","Effect":"Allow","Action":"ssm:StartSession",
  "Resource":["arn:aws:ssm:us-east-1::document/AWS-StartPortForwardingSession","arn:aws:ssm:us-east-1::document/AWS-StartInteractiveCommand",
              "arn:aws:ssm:us-east-1:ACCT:document/SSM-SessionManagerRunShell"]},
 {"Sid":"CommandDocuments","Effect":"Allow","Action":"ssm:SendCommand",
  "Resource":["arn:aws:ssm:us-east-1:ACCT:document/jarvis-deploy","arn:aws:ssm:us-east-1:ACCT:document/jarvis-restart",
              "arn:aws:ssm:us-east-1:ACCT:document/jarvis-secrets-sync","arn:aws:ssm:us-east-1:ACCT:document/jarvis-status"]},
 {"Sid":"OwnSessionsOnly","Effect":"Allow","Action":["ssm:TerminateSession","ssm:ResumeSession"],
  "Resource":"arn:aws:ssm:us-east-1:ACCT:session/*"},
 {"Sid":"SsmAndEc2ReadOnly","Effect":"Allow","Action":["ssm:DescribeInstanceInformation","ssm:GetCommandInvocation",
   "ssm:ListCommandInvocations","ssm:ListCommands","ssm:DescribeSessions","ec2:DescribeInstances"],"Resource":"*",
  "Condition":{"StringEquals":{"aws:RequestedRegion":"us-east-1"}}},
 {"Sid":"WriteReleases","Effect":"Allow","Action":["s3:PutObject","s3:GetObject"],"Resource":"arn:aws:s3:::jarvis-artifacts-ACCT/releases/*"},
 {"Sid":"ListReleases","Effect":"Allow","Action":"s3:ListBucket","Resource":"arn:aws:s3:::jarvis-artifacts-ACCT",
  "Condition":{"StringLike":{"s3:prefix":["releases/*"]}}},
 {"Sid":"PutValueSecrets","Effect":"Allow","Action":["secretsmanager:PutSecretValue","secretsmanager:DescribeSecret"],
  "Resource":["arn:aws:secretsmanager:us-east-1:ACCT:secret:jarvis/work-??????","arn:aws:secretsmanager:us-east-1:ACCT:secret:jarvis/personal-??????",
              "arn:aws:secretsmanager:us-east-1:ACCT:secret:jarvis/shared-??????","arn:aws:secretsmanager:us-east-1:ACCT:secret:jarvis/tailscale-??????"]},
 {"Sid":"JarvisKeyViaServices","Effect":"Allow","Action":["kms:GenerateDataKey","kms:Decrypt"],"Resource":"arn:aws:kms:us-east-1:ACCT:key/*",
  "Condition":{"ForAnyValue:StringEquals":{"kms:ResourceAliases":"alias/jarvis"},
   "StringEquals":{"kms:ViaService":["s3.us-east-1.amazonaws.com","secretsmanager.us-east-1.amazonaws.com"]}}}]}
```
- SessionAndCommandOnJarvisInstance: shell, port forwarding (`oauth-login.sh`) and Run Command,
  only on instances tagged `app=jarvis`. The instance id is unknown in `org/`.
- SessionDocuments: the three session documents only. The `SessionDocumentAccessCheck` condition
  on the instance statement forces `start-session` to name one of them.
- CommandDocuments: the four Jarvis documents only; no `AWS-RunShellScript`.
- OwnSessionsOnly: end or resume sessions in this account. The `${aws:userid}-*` self-restriction
  is dropped: for SSO principals `aws:userid` is `AROA...:<session name>` while session ids start
  with the session name only, so the pattern could not be verified to match. JarvisOperator is the
  only principal with SSM session rights, so the account-scoped pattern loses nothing.
- SsmAndEc2ReadOnly: these calls do not support resource-level permissions. Region-pinned.
- WriteReleases and ListReleases: `make release` uploads the tarball and `.sha256`; `make deploy`
  writes `releases/DEPLOYED`.
- PutValueSecrets: the human sets values with the CLI (brief 0.2). Write only: the operator cannot
  read the values back.
- JarvisKeyViaServices: SSE-KMS uploads and `PutSecretValue` need the key through those services
  only.

The operator has no `GetSecretValue` on any secret, no IAM, and no `terraform apply` power.

## S. SCP documents (explained in DESIGN.md section 11)

### S1 `jarvis-region-deny`

```json
{"Version":"2012-10-17","Statement":[
 {"Sid":"DenyOutsideHomeRegion","Effect":"Deny","Resource":"*",
  "NotAction":["a4b:*","access-analyzer:*","account:*","acm:*","activate:*","artifact:*","aws-marketplace-management:*",
   "aws-marketplace:*","aws-portal:*","billing:*","billingconductor:*","budgets:*","ce:*","chatbot:*","chime:*",
   "cloudfront:*","cloudtrail:LookupEvents","compute-optimizer:*","config:*","consoleapp:*","consolidatedbilling:*","cur:*",
   "datapipeline:GetAccountLimits","devicefarm:*","directconnect:*","ec2:DescribeRegions","ec2:DescribeTransitGateways",
   "ec2:DescribeVpnGateways","ecr-public:*","fms:*","freetier:*","globalaccelerator:*","health:*","iam:*","importexport:*",
   "invoicing:*","iq:*","kms:*","license-manager:ListReceivedLicenses","lightsail:Get*","mobileanalytics:*","networkmanager:*",
   "notifications-contacts:*","notifications:*","organizations:*","payments:*","pricing:*",
   "quicksight:DescribeAccountSubscription","resource-explorer-2:*","route53-recovery-cluster:*",
   "route53-recovery-control-config:*","route53-recovery-readiness:*","route53:*","route53domains:*",
   "s3:CreateMultiRegionAccessPoint","s3:DeleteMultiRegionAccessPoint","s3:DescribeMultiRegionAccessPointOperation",
   "s3:GetAccountPublicAccessBlock","s3:GetBucketLocation","s3:GetBucketPolicyStatus","s3:GetBucketPublicAccessBlock",
   "s3:GetMultiRegionAccessPoint","s3:GetMultiRegionAccessPointPolicy","s3:GetMultiRegionAccessPointPolicyStatus",
   "s3:GetStorageLensConfiguration","s3:GetStorageLensDashboard","s3:ListAllMyBuckets","s3:ListMultiRegionAccessPoints",
   "s3:ListStorageLensConfigurations","s3:PutAccountPublicAccessBlock","s3:PutMultiRegionAccessPointPolicy",
   "savingsplans:*","shield:*","sso:*","sts:*","support:*","supportapp:*","supportplans:*","sustainability:*",
   "tag:GetResources","tax:*","trustedadvisor:*","vendor-insights:ListEntitledSecurityProfiles","waf-regional:*","waf:*","wafv2:*"],
  "Condition":{"StringNotEquals":{"aws:RequestedRegion":["us-east-1"]}}}]}
```
- DenyOutsideHomeRegion: nothing regional runs outside us-east-1; the global and us-east-1-only
  services above stay usable.

### S2 `jarvis-org-guard`

```json
{"Version":"2012-10-17","Statement":[
 {"Sid":"DenyLeaveOrganization","Effect":"Deny","Action":"organizations:LeaveOrganization","Resource":"*"},
 {"Sid":"DenyCloudTrailTampering","Effect":"Deny","Resource":"*",
  "Action":["cloudtrail:StopLogging","cloudtrail:DeleteTrail","cloudtrail:UpdateTrail","cloudtrail:PutEventSelectors",
   "cloudtrail:PutInsightSelectors","cloudtrail:DeleteEventDataStore","cloudtrail:UpdateEventDataStore",
   "cloudtrail:StopEventDataStoreIngestion"]}]}
```
- DenyLeaveOrganization: the account cannot escape the SCPs.
- DenyCloudTrailTampering: defense in depth for audit logging (DESIGN.md 11.2).

### S3 `jarvis-guardrail`

```json
{"Version":"2012-10-17","Statement":[
 {"Sid":"DenyLongLivedCredsAndIngress","Effect":"Deny","Resource":"*",
  "Action":["iam:CreateUser","iam:CreateAccessKey","ec2:CreateKeyPair","ec2:ImportKeyPair","ec2:AuthorizeSecurityGroupIngress"]}]}
```
- DenyLongLivedCredsAndIngress: brief rule 0.3 enforced at the account boundary (AD27).
