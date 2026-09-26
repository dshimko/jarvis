# DESIGN.md section 4 "IAM wildcard allowlist", 4.1 (Resource "*", W1 to W16), keyed by "<policy>|<Sid>".
# Every row states effect, Action/NotAction set, resources, principals, and each condition as
# (operator, key, exact values) (PLAN 3.2 "Policy test precision"). Values use the fixed test
# identities the tftest overrides set: ACCT 111122223333, alias/jarvis key 1111...,
# alias/jarvis-tfstate key 2222..., EIP 203.0.113.10, roles jarvis-instance and jarvis-backup.
# <sso:Set> is arn:aws:iam::ACCT:role/aws-reserved/sso.amazonaws.com/*AWSReservedSSO_<Set>_*
# (Identity Center is in us-east-1, so there is no region segment).
# Amended per PLAN 3.2: W6 carries aws:SourceAccount; P13 and P18 use Null + StringNotEquals.
locals {
  allowlist_resource_star = {
    # W1
    "instance/jarvis-telemetry|PutMetricDataJarvisNamespace" = {
      id         = "W1"
      effect     = "Allow"
      action_key = "Action"
      actions    = ["cloudwatch:PutMetricData"]
      resources  = ["*"]
      principals = []
      conditions = [
        { op = "StringEquals", key = "cloudwatch:namespace", values = ["Jarvis"] },
      ]
    }
    # W2
    "permission_set/JarvisOperator|SsmAndEc2ReadOnly" = {
      id         = "W2"
      effect     = "Allow"
      action_key = "Action"
      actions = [
        "ssm:DescribeInstanceInformation",
        "ssm:GetCommandInvocation",
        "ssm:ListCommandInvocations",
        "ssm:ListCommands",
        "ssm:DescribeSessions",
        "ec2:DescribeInstances",
      ]
      resources  = ["*"]
      principals = []
      conditions = [
        { op = "StringEquals", key = "aws:RequestedRegion", values = [var.region] },
      ]
    }
    # W3
    "key/jarvis|AccountRootAdmin" = {
      id         = "W3"
      effect     = "Allow"
      action_key = "Action"
      actions    = ["kms:*"]
      resources  = ["*"]
      principals = ["AWS:arn:aws:iam::111122223333:root"]
      conditions = []
    }
    # W4
    "key/jarvis|CloudWatchLogsUse" = {
      id         = "W4"
      effect     = "Allow"
      action_key = "Action"
      actions    = ["kms:Encrypt", "kms:Decrypt", "kms:ReEncrypt*", "kms:GenerateDataKey*", "kms:DescribeKey"]
      resources  = ["*"]
      principals = ["Service:logs.${var.region}.amazonaws.com"]
      conditions = [
        { op = "ArnLike", key = "kms:EncryptionContext:aws:logs:arn", values = ["arn:aws:logs:${var.region}:111122223333:log-group:/jarvis/*"] },
      ]
    }
    # W5
    "key/jarvis|FlowLogDeliveryUse" = {
      id         = "W5"
      effect     = "Allow"
      action_key = "Action"
      actions    = ["kms:Encrypt", "kms:Decrypt", "kms:ReEncrypt*", "kms:GenerateDataKey*", "kms:DescribeKey"]
      resources  = ["*"]
      principals = ["Service:delivery.logs.amazonaws.com"]
      conditions = [
        { op = "StringEquals", key = "aws:SourceAccount", values = ["111122223333"] },
        { op = "ArnLike", key = "aws:SourceArn", values = ["arn:aws:logs:${var.region}:111122223333:*"] },
      ]
    }
    # W6
    "key/jarvis|AlarmsAndBudgetsToSns" = {
      id         = "W6"
      effect     = "Allow"
      action_key = "Action"
      actions    = ["kms:Decrypt", "kms:GenerateDataKey*"]
      resources  = ["*"]
      principals = ["Service:cloudwatch.amazonaws.com", "Service:budgets.amazonaws.com"]
      conditions = [
        { op = "StringEquals", key = "aws:SourceAccount", values = ["111122223333"] },
      ]
    }
    # W7
    "key/jarvis|BackupRoleUse" = {
      id         = "W7"
      effect     = "Allow"
      action_key = "Action"
      actions    = ["kms:Decrypt", "kms:DescribeKey", "kms:GenerateDataKeyWithoutPlaintext", "kms:ReEncrypt*"]
      resources  = ["*"]
      principals = ["AWS:arn:aws:iam::111122223333:role/jarvis-backup"]
      conditions = [
        { op = "StringEquals", key = "kms:ViaService", values = ["ec2.${var.region}.amazonaws.com", "backup.${var.region}.amazonaws.com"] },
      ]
    }
    # W8
    "key/jarvis|BackupRoleGrants" = {
      id         = "W8"
      effect     = "Allow"
      action_key = "Action"
      actions    = ["kms:CreateGrant"]
      resources  = ["*"]
      principals = ["AWS:arn:aws:iam::111122223333:role/jarvis-backup"]
      conditions = [
        { op = "Bool", key = "kms:GrantIsForAWSResource", values = ["true"] },
      ]
    }
    # W9
    "key/jarvis-tfstate|AccountRootAdmin" = {
      id         = "W9"
      effect     = "Allow"
      action_key = "Action"
      actions    = ["kms:*"]
      resources  = ["*"]
      principals = ["AWS:arn:aws:iam::111122223333:root"]
      conditions = []
    }
    # W10
    "scp/jarvis-region-deny|DenyOutsideHomeRegion" = {
      id         = "W10"
      effect     = "Deny"
      action_key = "NotAction"
      actions = [
        "a4b:*",
        "access-analyzer:*",
        "account:*",
        "acm:*",
        "activate:*",
        "artifact:*",
        "aws-marketplace-management:*",
        "aws-marketplace:*",
        "aws-portal:*",
        "billing:*",
        "billingconductor:*",
        "budgets:*",
        "ce:*",
        "chatbot:*",
        "chime:*",
        "cloudfront:*",
        "cloudtrail:LookupEvents",
        "compute-optimizer:*",
        "config:*",
        "consoleapp:*",
        "consolidatedbilling:*",
        "cur:*",
        "datapipeline:GetAccountLimits",
        "devicefarm:*",
        "directconnect:*",
        "ec2:DescribeRegions",
        "ec2:DescribeTransitGateways",
        "ec2:DescribeVpnGateways",
        "ecr-public:*",
        "fms:*",
        "freetier:*",
        "globalaccelerator:*",
        "health:*",
        "iam:*",
        "importexport:*",
        "invoicing:*",
        "iq:*",
        "kms:*",
        "license-manager:ListReceivedLicenses",
        "lightsail:Get*",
        "mobileanalytics:*",
        "networkmanager:*",
        "notifications-contacts:*",
        "notifications:*",
        "organizations:*",
        "payments:*",
        "pricing:*",
        "quicksight:DescribeAccountSubscription",
        "resource-explorer-2:*",
        "route53-recovery-cluster:*",
        "route53-recovery-control-config:*",
        "route53-recovery-readiness:*",
        "route53:*",
        "route53domains:*",
        "s3:CreateMultiRegionAccessPoint",
        "s3:DeleteMultiRegionAccessPoint",
        "s3:DescribeMultiRegionAccessPointOperation",
        "s3:GetAccountPublicAccessBlock",
        "s3:GetBucketLocation",
        "s3:GetBucketPolicyStatus",
        "s3:GetBucketPublicAccessBlock",
        "s3:GetMultiRegionAccessPoint",
        "s3:GetMultiRegionAccessPointPolicy",
        "s3:GetMultiRegionAccessPointPolicyStatus",
        "s3:GetStorageLensConfiguration",
        "s3:GetStorageLensDashboard",
        "s3:ListAllMyBuckets",
        "s3:ListMultiRegionAccessPoints",
        "s3:ListStorageLensConfigurations",
        "s3:PutAccountPublicAccessBlock",
        "s3:PutMultiRegionAccessPointPolicy",
        "savingsplans:*",
        "shield:*",
        "sso:*",
        "sts:*",
        "support:*",
        "supportapp:*",
        "supportplans:*",
        "sustainability:*",
        "tag:GetResources",
        "tax:*",
        "trustedadvisor:*",
        "vendor-insights:ListEntitledSecurityProfiles",
        "waf-regional:*",
        "waf:*",
        "wafv2:*",
      ]
      resources  = ["*"]
      principals = []
      conditions = [
        { op = "StringNotEquals", key = "aws:RequestedRegion", values = [var.region] },
      ]
    }
    # W11
    "scp/jarvis-org-guard|DenyLeaveOrganization" = {
      id         = "W11"
      effect     = "Deny"
      action_key = "Action"
      actions    = ["organizations:LeaveOrganization"]
      resources  = ["*"]
      principals = []
      conditions = []
    }
    # W12
    "scp/jarvis-org-guard|DenyCloudTrailTampering" = {
      id         = "W12"
      effect     = "Deny"
      action_key = "Action"
      actions = [
        "cloudtrail:StopLogging",
        "cloudtrail:DeleteTrail",
        "cloudtrail:UpdateTrail",
        "cloudtrail:PutEventSelectors",
        "cloudtrail:PutInsightSelectors",
        "cloudtrail:DeleteEventDataStore",
        "cloudtrail:UpdateEventDataStore",
        "cloudtrail:StopEventDataStoreIngestion",
      ]
      resources  = ["*"]
      principals = []
      conditions = []
    }
    # W13
    "instance/jarvis-secrets|DenyOffInstance" = {
      id         = "W13"
      effect     = "Deny"
      action_key = "Action"
      actions    = ["*"]
      resources  = ["*"]
      principals = []
      conditions = [
        { op = "NotIpAddress", key = "aws:SourceIp", values = ["203.0.113.10/32"] },
        { op = "Bool", key = "aws:ViaAWSService", values = ["false"] },
      ]
    }
    # W14
    "instance/jarvis-artifacts|DenyOffInstance" = {
      id         = "W14"
      effect     = "Deny"
      action_key = "Action"
      actions    = ["*"]
      resources  = ["*"]
      principals = []
      conditions = [
        { op = "NotIpAddress", key = "aws:SourceIp", values = ["203.0.113.10/32"] },
        { op = "Bool", key = "aws:ViaAWSService", values = ["false"] },
      ]
    }
    # W15
    "instance/jarvis-telemetry|DenyOffInstance" = {
      id         = "W15"
      effect     = "Deny"
      action_key = "Action"
      actions    = ["*"]
      resources  = ["*"]
      principals = []
      conditions = [
        { op = "NotIpAddress", key = "aws:SourceIp", values = ["203.0.113.10/32"] },
        { op = "Bool", key = "aws:ViaAWSService", values = ["false"] },
      ]
    }
    # W16
    "scp/jarvis-guardrail|DenyLongLivedCredsAndIngress" = {
      id         = "W16"
      effect     = "Deny"
      action_key = "Action"
      actions = [
        "iam:CreateUser",
        "iam:CreateAccessKey",
        "ec2:CreateKeyPair",
        "ec2:ImportKeyPair",
        "ec2:AuthorizeSecurityGroupIngress",
      ]
      resources  = ["*"]
      principals = []
      conditions = []
    }
  }
}
