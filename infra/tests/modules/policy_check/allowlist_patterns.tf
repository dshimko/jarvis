# DESIGN.md section 4 "IAM wildcard allowlist", 4.2 (ARN patterns with * or ?, P1 to P22), keyed by "<policy>|<Sid>".
# Every row states effect, Action/NotAction set, resources, principals, and each condition as
# (operator, key, exact values) (PLAN 3.2 "Policy test precision"). Values use the fixed test
# identities the tftest overrides set: ACCT 111122223333, alias/jarvis key 1111...,
# alias/jarvis-tfstate key 2222..., EIP 203.0.113.10, roles jarvis-instance and jarvis-backup.
# <sso:Set> is arn:aws:iam::ACCT:role/aws-reserved/sso.amazonaws.com/*AWSReservedSSO_<Set>_*
# (Identity Center is in us-east-1, so there is no region segment).
# Amended per PLAN 3.2: W6 carries aws:SourceAccount; P13 and P18 use Null + StringNotEquals.
locals {
  allowlist_patterns = {
    # P1
    "instance/jarvis-artifacts|ReadArtifacts" = {
      id         = "P1"
      effect     = "Allow"
      action_key = "Action"
      actions    = ["s3:GetObject"]
      resources = [
        "arn:aws:s3:::jarvis-artifacts-111122223333/releases/*",
        "arn:aws:s3:::jarvis-artifacts-111122223333/bootstrap/*",
      ]
      principals = []
      conditions = []
    }
    # P2
    "instance/jarvis-telemetry|WriteJarvisLogs" = {
      id         = "P2"
      effect     = "Allow"
      action_key = "Action"
      actions    = ["logs:CreateLogStream", "logs:PutLogEvents", "logs:DescribeLogStreams"]
      resources = [
        "arn:aws:logs:${var.region}:111122223333:log-group:/jarvis/work:*",
        "arn:aws:logs:${var.region}:111122223333:log-group:/jarvis/personal:*",
        "arn:aws:logs:${var.region}:111122223333:log-group:/jarvis/cloud-init:*",
      ]
      principals = []
      conditions = []
    }
    # P3
    "permission_set/JarvisClient|ReadTokenSecrets" = {
      id         = "P3"
      effect     = "Allow"
      action_key = "Action"
      actions    = ["secretsmanager:GetSecretValue"]
      resources = [
        "arn:aws:secretsmanager:${var.region}:111122223333:secret:jarvis/work/api-token-??????",
        "arn:aws:secretsmanager:${var.region}:111122223333:secret:jarvis/personal/api-token-??????",
      ]
      principals = []
      conditions = []
    }
    # P4
    "permission_set/JarvisClient|DecryptTokenSecrets" = {
      id         = "P4"
      effect     = "Allow"
      action_key = "Action"
      actions    = ["kms:Decrypt"]
      resources  = ["arn:aws:kms:${var.region}:111122223333:key/*"]
      principals = []
      conditions = [
        { op = "ForAnyValue:StringEquals", key = "kms:ResourceAliases", values = ["alias/jarvis"] },
        { op = "StringEquals", key = "kms:ViaService", values = ["secretsmanager.${var.region}.amazonaws.com"] },
        { op = "StringLike", key = "kms:EncryptionContext:SecretARN", values = [
          "arn:aws:secretsmanager:${var.region}:111122223333:secret:jarvis/work/api-token-??????",
          "arn:aws:secretsmanager:${var.region}:111122223333:secret:jarvis/personal/api-token-??????",
        ] },
      ]
    }
    # P5
    "permission_set/JarvisOperator|SessionAndCommandOnJarvisInstance" = {
      id         = "P5"
      effect     = "Allow"
      action_key = "Action"
      actions    = ["ssm:StartSession", "ssm:SendCommand"]
      resources  = ["arn:aws:ec2:${var.region}:111122223333:instance/*"]
      principals = []
      conditions = [
        { op = "StringEquals", key = "aws:ResourceTag/app", values = ["jarvis"] },
        { op = "BoolIfExists", key = "ssm:SessionDocumentAccessCheck", values = ["true"] },
      ]
    }
    # P6
    "permission_set/JarvisOperator|OwnSessionsOnly" = {
      id         = "P6"
      effect     = "Allow"
      action_key = "Action"
      actions    = ["ssm:TerminateSession", "ssm:ResumeSession"]
      resources  = ["arn:aws:ssm:${var.region}:111122223333:session/*"]
      principals = []
      conditions = []
    }
    # P7
    "permission_set/JarvisOperator|WriteReleases" = {
      id         = "P7"
      effect     = "Allow"
      action_key = "Action"
      actions    = ["s3:PutObject", "s3:GetObject"]
      resources  = ["arn:aws:s3:::jarvis-artifacts-111122223333/releases/*"]
      principals = []
      conditions = []
    }
    # P8
    "permission_set/JarvisOperator|PutValueSecrets" = {
      id         = "P8"
      effect     = "Allow"
      action_key = "Action"
      actions    = ["secretsmanager:PutSecretValue", "secretsmanager:DescribeSecret"]
      resources = [
        "arn:aws:secretsmanager:${var.region}:111122223333:secret:jarvis/work-??????",
        "arn:aws:secretsmanager:${var.region}:111122223333:secret:jarvis/personal-??????",
        "arn:aws:secretsmanager:${var.region}:111122223333:secret:jarvis/shared-??????",
        "arn:aws:secretsmanager:${var.region}:111122223333:secret:jarvis/tailscale-??????",
      ]
      principals = []
      conditions = []
    }
    # P9
    "permission_set/JarvisOperator|JarvisKeyViaServices" = {
      id         = "P9"
      effect     = "Allow"
      action_key = "Action"
      actions    = ["kms:GenerateDataKey", "kms:Decrypt"]
      resources  = ["arn:aws:kms:${var.region}:111122223333:key/*"]
      principals = []
      conditions = [
        { op = "ForAnyValue:StringEquals", key = "kms:ResourceAliases", values = ["alias/jarvis"] },
        { op = "StringEquals", key = "kms:ViaService", values = ["s3.${var.region}.amazonaws.com", "secretsmanager.${var.region}.amazonaws.com"] },
      ]
    }
    # P10
    "bucket/artifacts|DenyInsecureTransport" = {
      id         = "P10"
      effect     = "Deny"
      action_key = "Action"
      actions    = ["s3:*"]
      resources = [
        "arn:aws:s3:::jarvis-artifacts-111122223333",
        "arn:aws:s3:::jarvis-artifacts-111122223333/*",
      ]
      principals = ["*"]
      conditions = [
        { op = "Bool", key = "aws:SecureTransport", values = ["false"] },
      ]
    }
    # P11
    "bucket/artifacts|DenyUnlistedPrincipals" = {
      id         = "P11"
      effect     = "Deny"
      action_key = "Action"
      actions    = ["s3:*"]
      resources = [
        "arn:aws:s3:::jarvis-artifacts-111122223333",
        "arn:aws:s3:::jarvis-artifacts-111122223333/*",
      ]
      principals = ["*"]
      conditions = [
        { op = "ArnNotLike", key = "aws:PrincipalArn", values = [
          "arn:aws:iam::111122223333:role/jarvis-instance",
          "arn:aws:iam::111122223333:role/aws-reserved/sso.amazonaws.com/*AWSReservedSSO_JarvisOperator_*",
          "arn:aws:iam::111122223333:role/aws-reserved/sso.amazonaws.com/*AWSReservedSSO_JarvisAdmin_*",
          "arn:aws:iam::111122223333:role/OrganizationAccountAccessRole",
        ] },
      ]
    }
    # P12
    "bucket/artifacts|DenyInstanceWrites" = {
      id         = "P12"
      effect     = "Deny"
      action_key = "Action"
      actions    = ["s3:PutObject", "s3:DeleteObject", "s3:DeleteObjectVersion", "s3:PutObjectAcl"]
      resources  = ["arn:aws:s3:::jarvis-artifacts-111122223333/*"]
      principals = ["AWS:arn:aws:iam::111122223333:role/jarvis-instance"]
      conditions = []
    }
    # P13
    "bucket/artifacts|DenyWrongKmsKey" = {
      id         = "P13"
      effect     = "Deny"
      action_key = "Action"
      actions    = ["s3:PutObject"]
      resources  = ["arn:aws:s3:::jarvis-artifacts-111122223333/*"]
      principals = ["*"]
      conditions = [
        { op = "Null", key = "s3:x-amz-server-side-encryption-aws-kms-key-id", values = ["false"] },
        { op = "StringNotEquals", key = "s3:x-amz-server-side-encryption-aws-kms-key-id", values = ["arn:aws:kms:${var.region}:111122223333:key/11111111-1111-4111-8111-111111111111"] },
      ]
    }
    # P14
    "bucket/artifacts|DenyNonKmsEncryption" = {
      id         = "P14"
      effect     = "Deny"
      action_key = "Action"
      actions    = ["s3:PutObject"]
      resources  = ["arn:aws:s3:::jarvis-artifacts-111122223333/*"]
      principals = ["*"]
      conditions = [
        { op = "Null", key = "s3:x-amz-server-side-encryption", values = ["false"] },
        { op = "StringNotEquals", key = "s3:x-amz-server-side-encryption", values = ["aws:kms"] },
      ]
    }
    # P15
    "bucket/artifacts|DenyKmsWithoutKeyId" = {
      id         = "P15"
      effect     = "Deny"
      action_key = "Action"
      actions    = ["s3:PutObject"]
      resources  = ["arn:aws:s3:::jarvis-artifacts-111122223333/*"]
      principals = ["*"]
      conditions = [
        { op = "StringEquals", key = "s3:x-amz-server-side-encryption", values = ["aws:kms"] },
        { op = "Null", key = "s3:x-amz-server-side-encryption-aws-kms-key-id", values = ["true"] },
      ]
    }
    # P16
    "bucket/tfstate|DenyInsecureTransport" = {
      id         = "P16"
      effect     = "Deny"
      action_key = "Action"
      actions    = ["s3:*"]
      resources  = ["arn:aws:s3:::jarvis-tfstate-111122223333", "arn:aws:s3:::jarvis-tfstate-111122223333/*"]
      principals = ["*"]
      conditions = [
        { op = "Bool", key = "aws:SecureTransport", values = ["false"] },
      ]
    }
    # P17
    "bucket/tfstate|DenyUnlistedPrincipals" = {
      id         = "P17"
      effect     = "Deny"
      action_key = "Action"
      actions    = ["s3:*"]
      resources  = ["arn:aws:s3:::jarvis-tfstate-111122223333", "arn:aws:s3:::jarvis-tfstate-111122223333/*"]
      principals = ["*"]
      conditions = [
        { op = "ArnNotLike", key = "aws:PrincipalArn", values = [
          "arn:aws:iam::111122223333:role/aws-reserved/sso.amazonaws.com/*AWSReservedSSO_JarvisAdmin_*",
          "arn:aws:iam::111122223333:role/OrganizationAccountAccessRole",
        ] },
      ]
    }
    # P18
    "bucket/tfstate|DenyWrongKmsKey" = {
      id         = "P18"
      effect     = "Deny"
      action_key = "Action"
      actions    = ["s3:PutObject"]
      resources  = ["arn:aws:s3:::jarvis-tfstate-111122223333/*"]
      principals = ["*"]
      conditions = [
        { op = "Null", key = "s3:x-amz-server-side-encryption-aws-kms-key-id", values = ["false"] },
        { op = "StringNotEquals", key = "s3:x-amz-server-side-encryption-aws-kms-key-id", values = ["arn:aws:kms:${var.region}:111122223333:key/22222222-2222-4222-8222-222222222222"] },
      ]
    }
    # P19
    "bucket/tfstate|DenyNonKmsEncryption" = {
      id         = "P19"
      effect     = "Deny"
      action_key = "Action"
      actions    = ["s3:PutObject"]
      resources  = ["arn:aws:s3:::jarvis-tfstate-111122223333/*"]
      principals = ["*"]
      conditions = [
        { op = "Null", key = "s3:x-amz-server-side-encryption", values = ["false"] },
        { op = "StringNotEquals", key = "s3:x-amz-server-side-encryption", values = ["aws:kms"] },
      ]
    }
    # P20
    "bucket/tfstate|DenyKmsWithoutKeyId" = {
      id         = "P20"
      effect     = "Deny"
      action_key = "Action"
      actions    = ["s3:PutObject"]
      resources  = ["arn:aws:s3:::jarvis-tfstate-111122223333/*"]
      principals = ["*"]
      conditions = [
        { op = "StringEquals", key = "s3:x-amz-server-side-encryption", values = ["aws:kms"] },
        { op = "Null", key = "s3:x-amz-server-side-encryption-aws-kms-key-id", values = ["true"] },
      ]
    }
    # P21
    "bucket/flowlogs|AWSLogDeliveryWrite" = {
      id         = "P21"
      effect     = "Allow"
      action_key = "Action"
      actions    = ["s3:PutObject"]
      resources  = ["arn:aws:s3:::jarvis-flowlogs-111122223333/AWSLogs/111122223333/*"]
      principals = ["Service:delivery.logs.amazonaws.com"]
      conditions = [
        { op = "StringEquals", key = "aws:SourceAccount", values = ["111122223333"] },
        { op = "StringEquals", key = "s3:x-amz-acl", values = ["bucket-owner-full-control"] },
        { op = "ArnLike", key = "aws:SourceArn", values = ["arn:aws:logs:${var.region}:111122223333:*"] },
      ]
    }
    # P22
    "bucket/flowlogs|DenyInsecureTransport" = {
      id         = "P22"
      effect     = "Deny"
      action_key = "Action"
      actions    = ["s3:*"]
      resources  = ["arn:aws:s3:::jarvis-flowlogs-111122223333", "arn:aws:s3:::jarvis-flowlogs-111122223333/*"]
      principals = ["*"]
      conditions = [
        { op = "Bool", key = "aws:SecureTransport", values = ["false"] },
      ]
    }
  }
}
