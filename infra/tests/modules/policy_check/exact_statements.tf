# Exact-match table for every statement WITHOUT a wildcard resource (Gate 2 M1): trust
# policies, the instance role's non-wildcard statements, the permission sets, the SNS topic, and
# the remaining bucket statements. With allowlist_*.tf this pins every statement in every policy:
# a statement in neither table fails. Values use the fixed test identities; the seven secret ARNs
# are per-instance overrides in iam_wildcards.tftest.hcl (suffixes AAAAAA to GGGGGG).
# X6 EncryptApiTokens includes kms:Decrypt (Phase 2 amendment K1, DESIGN-IAM.md 3.2).
# X3 and X4 include jarvis/ofw (PLAN AD34, DESIGN-IAM.md 3.2 "AD34 deviation"): five value ARNs.
# X12 includes jarvis-ofw-login and jarvis-ofw-reset (PLAN AD40).
locals {
  exact_statements = {
    # X1
    "trust/jarvis-instance|Ec2AssumeRole" = {
      id         = "X1"
      effect     = "Allow"
      action_key = "Action"
      actions    = ["sts:AssumeRole"]
      resources  = []
      principals = ["Service:ec2.amazonaws.com"]
      conditions = []
    }
    # X2
    "trust/jarvis-backup|BackupAssumeRole" = {
      id         = "X2"
      effect     = "Allow"
      action_key = "Action"
      actions    = ["sts:AssumeRole"]
      resources  = []
      principals = ["Service:backup.amazonaws.com"]
      conditions = [
        { op = "StringEquals", key = "aws:SourceAccount", values = ["111122223333"] },
      ]
    }
    # X3
    "instance/jarvis-secrets|ReadModeSecrets" = {
      id         = "X3"
      effect     = "Allow"
      action_key = "Action"
      actions    = ["secretsmanager:GetSecretValue"]
      resources = [
        "arn:aws:secretsmanager:${var.region}:111122223333:secret:jarvis/work-AAAAAA",
        "arn:aws:secretsmanager:${var.region}:111122223333:secret:jarvis/personal-BBBBBB",
        "arn:aws:secretsmanager:${var.region}:111122223333:secret:jarvis/shared-CCCCCC",
        "arn:aws:secretsmanager:${var.region}:111122223333:secret:jarvis/tailscale-DDDDDD",
        "arn:aws:secretsmanager:${var.region}:111122223333:secret:jarvis/ofw-GGGGGG",
      ]
      principals = []
      conditions = []
    }
    # X4
    "instance/jarvis-secrets|DecryptModeSecrets" = {
      id         = "X4"
      effect     = "Allow"
      action_key = "Action"
      actions    = ["kms:Decrypt"]
      resources  = ["arn:aws:kms:${var.region}:111122223333:key/11111111-1111-4111-8111-111111111111"]
      principals = []
      conditions = [
        { op = "StringEquals", key = "kms:ViaService", values = ["secretsmanager.${var.region}.amazonaws.com"] },
        { op = "StringEquals", key = "kms:EncryptionContext:SecretARN", values = [
          "arn:aws:secretsmanager:${var.region}:111122223333:secret:jarvis/work-AAAAAA",
          "arn:aws:secretsmanager:${var.region}:111122223333:secret:jarvis/personal-BBBBBB",
          "arn:aws:secretsmanager:${var.region}:111122223333:secret:jarvis/shared-CCCCCC",
          "arn:aws:secretsmanager:${var.region}:111122223333:secret:jarvis/tailscale-DDDDDD",
          "arn:aws:secretsmanager:${var.region}:111122223333:secret:jarvis/ofw-GGGGGG",
        ] },
      ]
    }
    # X5
    "instance/jarvis-secrets|PublishApiTokens" = {
      id         = "X5"
      effect     = "Allow"
      action_key = "Action"
      actions    = ["secretsmanager:PutSecretValue"]
      resources = [
        "arn:aws:secretsmanager:${var.region}:111122223333:secret:jarvis/work/api-token-EEEEEE",
        "arn:aws:secretsmanager:${var.region}:111122223333:secret:jarvis/personal/api-token-FFFFFF",
      ]
      principals = []
      conditions = []
    }
    # X6
    "instance/jarvis-secrets|EncryptApiTokens" = {
      id         = "X6"
      effect     = "Allow"
      action_key = "Action"
      actions    = ["kms:GenerateDataKey", "kms:Decrypt"]
      resources  = ["arn:aws:kms:${var.region}:111122223333:key/11111111-1111-4111-8111-111111111111"]
      principals = []
      conditions = [
        { op = "StringEquals", key = "kms:ViaService", values = ["secretsmanager.${var.region}.amazonaws.com"] },
        { op = "StringEquals", key = "kms:EncryptionContext:SecretARN", values = [
          "arn:aws:secretsmanager:${var.region}:111122223333:secret:jarvis/work/api-token-EEEEEE",
          "arn:aws:secretsmanager:${var.region}:111122223333:secret:jarvis/personal/api-token-FFFFFF",
        ] },
      ]
    }
    # X7
    "instance/jarvis-artifacts|ListArtifacts" = {
      id         = "X7"
      effect     = "Allow"
      action_key = "Action"
      actions    = ["s3:ListBucket"]
      resources  = ["arn:aws:s3:::jarvis-artifacts-111122223333"]
      principals = []
      conditions = [
        { op = "StringLike", key = "s3:prefix", values = ["releases/*", "bootstrap/*"] },
      ]
    }
    # X8
    "instance/jarvis-artifacts|DecryptArtifacts" = {
      id         = "X8"
      effect     = "Allow"
      action_key = "Action"
      actions    = ["kms:Decrypt"]
      resources  = ["arn:aws:kms:${var.region}:111122223333:key/11111111-1111-4111-8111-111111111111"]
      principals = []
      conditions = [
        { op = "StringEquals", key = "kms:ViaService", values = ["s3.${var.region}.amazonaws.com"] },
        { op = "StringLike", key = "kms:EncryptionContext:aws:s3:arn", values = [
          "arn:aws:s3:::jarvis-artifacts-111122223333",
          "arn:aws:s3:::jarvis-artifacts-111122223333/*",
        ] },
      ]
    }
    # X9
    "bucket/flowlogs|AWSLogDeliveryAclCheck" = {
      id         = "X9"
      effect     = "Allow"
      action_key = "Action"
      actions    = ["s3:GetBucketAcl", "s3:ListBucket"]
      resources  = ["arn:aws:s3:::jarvis-flowlogs-111122223333"]
      principals = ["Service:delivery.logs.amazonaws.com"]
      conditions = [
        { op = "StringEquals", key = "aws:SourceAccount", values = ["111122223333"] },
        { op = "ArnLike", key = "aws:SourceArn", values = ["arn:aws:logs:${var.region}:111122223333:*"] },
      ]
    }
    # X10
    "bucket/tfstate|DenyBucketDelete" = {
      id         = "X10"
      effect     = "Deny"
      action_key = "Action"
      actions    = ["s3:DeleteBucket"]
      resources  = ["arn:aws:s3:::jarvis-tfstate-111122223333"]
      principals = ["*"]
      conditions = []
    }
    # X11
    "permission_set/JarvisOperator|SessionDocuments" = {
      id         = "X11"
      effect     = "Allow"
      action_key = "Action"
      actions    = ["ssm:StartSession"]
      resources = [
        "arn:aws:ssm:${var.region}::document/AWS-StartPortForwardingSession",
        "arn:aws:ssm:${var.region}::document/AWS-StartInteractiveCommand",
        "arn:aws:ssm:${var.region}:111122223333:document/SSM-SessionManagerRunShell",
      ]
      principals = []
      conditions = []
    }
    # X12
    "permission_set/JarvisOperator|CommandDocuments" = {
      id         = "X12"
      effect     = "Allow"
      action_key = "Action"
      actions    = ["ssm:SendCommand"]
      resources = [
        "arn:aws:ssm:${var.region}:111122223333:document/jarvis-deploy",
        "arn:aws:ssm:${var.region}:111122223333:document/jarvis-restart",
        "arn:aws:ssm:${var.region}:111122223333:document/jarvis-secrets-sync",
        "arn:aws:ssm:${var.region}:111122223333:document/jarvis-status",
        "arn:aws:ssm:${var.region}:111122223333:document/jarvis-ofw-login",
        "arn:aws:ssm:${var.region}:111122223333:document/jarvis-ofw-reset",
      ]
      principals = []
      conditions = []
    }
    # X13
    "permission_set/JarvisOperator|ListReleases" = {
      id         = "X13"
      effect     = "Allow"
      action_key = "Action"
      actions    = ["s3:ListBucket"]
      resources  = ["arn:aws:s3:::jarvis-artifacts-111122223333"]
      principals = []
      conditions = [
        { op = "StringLike", key = "s3:prefix", values = ["releases/*"] },
      ]
    }
    # X14
    "topic/jarvis-alerts|OwnerManage" = {
      id         = "X14"
      effect     = "Allow"
      action_key = "Action"
      actions = [
        "sns:GetTopicAttributes",
        "sns:SetTopicAttributes",
        "sns:Subscribe",
        "sns:ListSubscriptionsByTopic",
        "sns:Publish",
        "sns:DeleteTopic",
      ]
      resources  = ["arn:aws:sns:${var.region}:111122223333:jarvis-alerts"]
      principals = ["AWS:arn:aws:iam::111122223333:root"]
      conditions = []
    }
    # X15
    "topic/jarvis-alerts|CloudWatchAlarmsPublish" = {
      id         = "X15"
      effect     = "Allow"
      action_key = "Action"
      actions    = ["sns:Publish"]
      resources  = ["arn:aws:sns:${var.region}:111122223333:jarvis-alerts"]
      principals = ["Service:cloudwatch.amazonaws.com"]
      conditions = [
        { op = "StringEquals", key = "aws:SourceAccount", values = ["111122223333"] },
        { op = "ArnLike", key = "aws:SourceArn", values = ["arn:aws:cloudwatch:${var.region}:111122223333:alarm:jarvis-*"] },
      ]
    }
    # X16
    "topic/jarvis-alerts|BudgetsPublish" = {
      id         = "X16"
      effect     = "Allow"
      action_key = "Action"
      actions    = ["sns:Publish"]
      resources  = ["arn:aws:sns:${var.region}:111122223333:jarvis-alerts"]
      principals = ["Service:budgets.amazonaws.com"]
      conditions = [
        { op = "StringEquals", key = "aws:SourceAccount", values = ["111122223333"] },
        { op = "ArnLike", key = "aws:SourceArn", values = ["arn:aws:budgets::111122223333:*"] },
      ]
    }
    # X17
    "topic/jarvis-alerts|DenyInsecurePublish" = {
      id         = "X17"
      effect     = "Deny"
      action_key = "Action"
      actions    = ["sns:Publish"]
      resources  = ["arn:aws:sns:${var.region}:111122223333:jarvis-alerts"]
      principals = ["*"]
      conditions = [
        { op = "Bool", key = "aws:SecureTransport", values = ["false"] },
      ]
    }
  }
}
