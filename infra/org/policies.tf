locals {
  acct = local.account_id
  r    = var.home_region

  token_secret_patterns = [
    "arn:aws:secretsmanager:${local.r}:${local.acct}:secret:jarvis/work/api-token-??????",
    "arn:aws:secretsmanager:${local.r}:${local.acct}:secret:jarvis/personal/api-token-??????",
  ]
  value_secret_patterns = [
    for n in ["work", "personal", "shared", "tailscale", "ofw"] : # ofw: PLAN AD34, set by the human
    "arn:aws:secretsmanager:${local.r}:${local.acct}:secret:jarvis/${n}-??????"
  ]
  artifacts_arn = "arn:aws:s3:::jarvis-artifacts-${local.acct}"
  any_key_arn   = "arn:aws:kms:${local.r}:${local.acct}:key/*"

  # S1 NotAction, verbatim from DESIGN-IAM.md section S1 (AD28). Diff against the current
  # AWS Control Tower region-deny list before attaching (DESIGN.md 11.1).
  region_deny_not_actions = [
    "a4b:*", "access-analyzer:*", "account:*", "acm:*", "activate:*", "artifact:*",
    "aws-marketplace-management:*", "aws-marketplace:*", "aws-portal:*", "billing:*",
    "billingconductor:*", "budgets:*", "ce:*", "chatbot:*", "chime:*", "cloudfront:*",
    "cloudtrail:LookupEvents", "compute-optimizer:*", "config:*", "consoleapp:*",
    "consolidatedbilling:*", "cur:*", "datapipeline:GetAccountLimits", "devicefarm:*",
    "directconnect:*", "ec2:DescribeRegions", "ec2:DescribeTransitGateways",
    "ec2:DescribeVpnGateways", "ecr-public:*", "fms:*", "freetier:*", "globalaccelerator:*",
    "health:*", "iam:*", "importexport:*", "invoicing:*", "iq:*", "kms:*",
    "license-manager:ListReceivedLicenses", "lightsail:Get*", "mobileanalytics:*",
    "networkmanager:*", "notifications-contacts:*", "notifications:*", "organizations:*",
    "payments:*", "pricing:*", "quicksight:DescribeAccountSubscription", "resource-explorer-2:*",
    "route53-recovery-cluster:*", "route53-recovery-control-config:*",
    "route53-recovery-readiness:*", "route53:*", "route53domains:*",
    "s3:CreateMultiRegionAccessPoint", "s3:DeleteMultiRegionAccessPoint",
    "s3:DescribeMultiRegionAccessPointOperation", "s3:GetAccountPublicAccessBlock",
    "s3:GetBucketLocation", "s3:GetBucketPolicyStatus", "s3:GetBucketPublicAccessBlock",
    "s3:GetMultiRegionAccessPoint", "s3:GetMultiRegionAccessPointPolicy",
    "s3:GetMultiRegionAccessPointPolicyStatus", "s3:GetStorageLensConfiguration",
    "s3:GetStorageLensDashboard", "s3:ListAllMyBuckets", "s3:ListMultiRegionAccessPoints",
    "s3:ListStorageLensConfigurations", "s3:PutAccountPublicAccessBlock",
    "s3:PutMultiRegionAccessPointPolicy", "savingsplans:*", "shield:*", "sso:*", "sts:*",
    "support:*", "supportapp:*", "supportplans:*", "sustainability:*", "tag:GetResources", "tax:*",
    "trustedadvisor:*", "vendor-insights:ListEntitledSecurityProfiles", "waf-regional:*", "waf:*",
    "wafv2:*",
  ]

  # DESIGN-IAM.md 3.12 and section S.
  policies = {
    "permission_set/JarvisClient" = jsonencode({
      Version = "2012-10-17"
      Statement = [
        {
          Sid      = "ReadTokenSecrets"
          Effect   = "Allow"
          Action   = "secretsmanager:GetSecretValue"
          Resource = local.token_secret_patterns
        },
        {
          Sid      = "DecryptTokenSecrets"
          Effect   = "Allow"
          Action   = "kms:Decrypt"
          Resource = local.any_key_arn
          Condition = {
            "ForAnyValue:StringEquals" = { "kms:ResourceAliases" = "alias/jarvis" }
            StringEquals               = { "kms:ViaService" = "secretsmanager.${local.r}.amazonaws.com" }
            StringLike                 = { "kms:EncryptionContext:SecretARN" = local.token_secret_patterns }
          }
        },
      ]
    })

    "permission_set/JarvisOperator" = jsonencode({
      Version = "2012-10-17"
      Statement = [
        {
          Sid      = "SessionAndCommandOnJarvisInstance"
          Effect   = "Allow"
          Action   = ["ssm:StartSession", "ssm:SendCommand"]
          Resource = "arn:aws:ec2:${local.r}:${local.acct}:instance/*"
          Condition = {
            StringEquals = { "aws:ResourceTag/app" = "jarvis" }
            BoolIfExists = { "ssm:SessionDocumentAccessCheck" = "true" }
          }
        },
        {
          Sid    = "SessionDocuments"
          Effect = "Allow"
          Action = "ssm:StartSession"
          Resource = [
            "arn:aws:ssm:${local.r}::document/AWS-StartPortForwardingSession",
            "arn:aws:ssm:${local.r}::document/AWS-StartInteractiveCommand",
            "arn:aws:ssm:${local.r}:${local.acct}:document/SSM-SessionManagerRunShell",
          ]
        },
        {
          Sid    = "CommandDocuments"
          Effect = "Allow"
          Action = "ssm:SendCommand"
          Resource = [
            for d in [
              "jarvis-deploy", "jarvis-restart", "jarvis-secrets-sync", "jarvis-status",
              "jarvis-ofw-login", "jarvis-ofw-reset", # PLAN AD40
            ] :
            "arn:aws:ssm:${local.r}:${local.acct}:document/${d}"
          ]
        },
        {
          Sid      = "OwnSessionsOnly"
          Effect   = "Allow"
          Action   = ["ssm:TerminateSession", "ssm:ResumeSession"]
          Resource = "arn:aws:ssm:${local.r}:${local.acct}:session/*"
        },
        {
          Sid    = "SsmAndEc2ReadOnly"
          Effect = "Allow"
          Action = [
            "ssm:DescribeInstanceInformation", "ssm:GetCommandInvocation", "ssm:ListCommandInvocations",
            "ssm:ListCommands", "ssm:DescribeSessions", "ec2:DescribeInstances",
          ]
          Resource  = "*"
          Condition = { StringEquals = { "aws:RequestedRegion" = local.r } }
        },
        {
          Sid      = "WriteReleases"
          Effect   = "Allow"
          Action   = ["s3:PutObject", "s3:GetObject"]
          Resource = "${local.artifacts_arn}/releases/*"
        },
        {
          Sid       = "ListReleases"
          Effect    = "Allow"
          Action    = "s3:ListBucket"
          Resource  = local.artifacts_arn
          Condition = { StringLike = { "s3:prefix" = ["releases/*"] } }
        },
        {
          Sid      = "PutValueSecrets"
          Effect   = "Allow"
          Action   = ["secretsmanager:PutSecretValue", "secretsmanager:DescribeSecret"]
          Resource = local.value_secret_patterns
        },
        {
          Sid      = "JarvisKeyViaServices"
          Effect   = "Allow"
          Action   = ["kms:GenerateDataKey", "kms:Decrypt"]
          Resource = local.any_key_arn
          Condition = {
            "ForAnyValue:StringEquals" = { "kms:ResourceAliases" = "alias/jarvis" }
            StringEquals = {
              "kms:ViaService" = ["s3.${local.r}.amazonaws.com", "secretsmanager.${local.r}.amazonaws.com"]
            }
          }
        },
      ]
    })

    "scp/jarvis-region-deny" = jsonencode({
      Version = "2012-10-17"
      Statement = [{
        Sid       = "DenyOutsideHomeRegion"
        Effect    = "Deny"
        NotAction = local.region_deny_not_actions
        Resource  = "*"
        Condition = { StringNotEquals = { "aws:RequestedRegion" = [local.r] } }
      }]
    })

    "scp/jarvis-org-guard" = jsonencode({
      Version = "2012-10-17"
      Statement = [
        {
          Sid      = "DenyLeaveOrganization"
          Effect   = "Deny"
          Action   = "organizations:LeaveOrganization"
          Resource = "*"
        },
        {
          Sid    = "DenyCloudTrailTampering"
          Effect = "Deny"
          Action = [
            "cloudtrail:StopLogging", "cloudtrail:DeleteTrail", "cloudtrail:UpdateTrail",
            "cloudtrail:PutEventSelectors", "cloudtrail:PutInsightSelectors",
            "cloudtrail:DeleteEventDataStore", "cloudtrail:UpdateEventDataStore",
            "cloudtrail:StopEventDataStoreIngestion",
          ]
          Resource = "*"
        },
      ]
    })

    "scp/jarvis-guardrail" = jsonencode({
      Version = "2012-10-17"
      Statement = [{
        Sid    = "DenyLongLivedCredsAndIngress"
        Effect = "Deny"
        Action = [
          "iam:CreateUser", "iam:CreateAccessKey", "ec2:CreateKeyPair", "ec2:ImportKeyPair",
          "ec2:AuthorizeSecurityGroupIngress",
        ]
        Resource = "*"
      }]
    })
  }
}
