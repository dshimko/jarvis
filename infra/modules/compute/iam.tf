locals {
  # AD34: "ofw" is a recorded deviation from brief section 2 (like G4); DESIGN-IAM.md 3.2.
  value_secrets = [for k in ["work", "personal", "shared", "tailscale", "ofw"] : var.value_secret_arns[k]]
  token_secrets = [for k in ["work", "personal"] : var.token_secret_arns[k]]
  artifacts_arn = "arn:aws:s3:::${var.artifacts_bucket}"
  ssm_core_arn  = "arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore"

  # AD29: repeated in every inline policy so removing one policy never removes the pin.
  deny_off_instance = {
    Sid      = "DenyOffInstance"
    Effect   = "Deny"
    Action   = "*"
    Resource = "*"
    Condition = {
      NotIpAddress = { "aws:SourceIp" = "${aws_eip.jarvis.public_ip}/32" }
      Bool         = { "aws:ViaAWSService" = "false" }
    }
  }

  # DESIGN-IAM.md 3.1 and 3.2.
  policies = {
    "trust/jarvis-instance" = jsonencode({
      Version = "2012-10-17"
      Statement = [{
        Sid       = "Ec2AssumeRole"
        Effect    = "Allow"
        Principal = { Service = "ec2.amazonaws.com" }
        Action    = "sts:AssumeRole"
      }]
    })

    "instance/jarvis-secrets" = jsonencode({
      Version = "2012-10-17"
      Statement = [
        {
          Sid      = "ReadModeSecrets"
          Effect   = "Allow"
          Action   = "secretsmanager:GetSecretValue"
          Resource = local.value_secrets
        },
        {
          Sid      = "DecryptModeSecrets"
          Effect   = "Allow"
          Action   = "kms:Decrypt"
          Resource = aws_kms_key.jarvis.arn
          Condition = {
            StringEquals = {
              "kms:ViaService"                  = "secretsmanager.${var.aws_region}.amazonaws.com"
              "kms:EncryptionContext:SecretARN" = local.value_secrets
            }
          }
        },
        {
          Sid      = "PublishApiTokens"
          Effect   = "Allow"
          Action   = "secretsmanager:PutSecretValue"
          Resource = local.token_secrets
        },
        {
          Sid      = "EncryptApiTokens"
          Effect   = "Allow"
          Action   = ["kms:GenerateDataKey", "kms:Decrypt"]
          Resource = aws_kms_key.jarvis.arn
          Condition = {
            StringEquals = {
              "kms:ViaService"                  = "secretsmanager.${var.aws_region}.amazonaws.com"
              "kms:EncryptionContext:SecretARN" = local.token_secrets
            }
          }
        },
        local.deny_off_instance,
      ]
    })

    "instance/jarvis-artifacts" = jsonencode({
      Version = "2012-10-17"
      Statement = [
        {
          Sid      = "ReadArtifacts"
          Effect   = "Allow"
          Action   = "s3:GetObject"
          Resource = ["${local.artifacts_arn}/releases/*", "${local.artifacts_arn}/bootstrap/*"]
        },
        {
          Sid       = "ListArtifacts"
          Effect    = "Allow"
          Action    = "s3:ListBucket"
          Resource  = local.artifacts_arn
          Condition = { StringLike = { "s3:prefix" = ["releases/*", "bootstrap/*"] } }
        },
        {
          Sid      = "DecryptArtifacts"
          Effect   = "Allow"
          Action   = "kms:Decrypt"
          Resource = aws_kms_key.jarvis.arn
          Condition = {
            StringEquals = { "kms:ViaService" = "s3.${var.aws_region}.amazonaws.com" }
            StringLike   = { "kms:EncryptionContext:aws:s3:arn" = [local.artifacts_arn, "${local.artifacts_arn}/*"] }
          }
        },
        local.deny_off_instance,
      ]
    })

    "instance/jarvis-telemetry" = jsonencode({
      Version = "2012-10-17"
      Statement = [
        {
          Sid      = "WriteJarvisLogs"
          Effect   = "Allow"
          Action   = ["logs:CreateLogStream", "logs:PutLogEvents", "logs:DescribeLogStreams"]
          Resource = [for g in var.log_group_names : "arn:aws:logs:${var.aws_region}:${var.account_id}:log-group:${g}:*"]
        },
        {
          Sid       = "PutMetricDataJarvisNamespace"
          Effect    = "Allow"
          Action    = "cloudwatch:PutMetricData"
          Resource  = "*"
          Condition = { StringEquals = { "cloudwatch:namespace" = "Jarvis" } }
        },
        local.deny_off_instance,
      ]
    })
  }

  inline_policy_names = ["jarvis-secrets", "jarvis-artifacts", "jarvis-telemetry"]
}

resource "aws_iam_role" "instance" {
  name               = "jarvis-instance"
  description        = "Jarvis EC2 instance role (DESIGN-IAM.md 3.1)"
  assume_role_policy = local.policies["trust/jarvis-instance"]
}

resource "aws_iam_role_policy_attachment" "ssm_core" {
  role       = aws_iam_role.instance.name
  policy_arn = local.ssm_core_arn
}

resource "aws_iam_role_policy" "instance" {
  for_each = toset(local.inline_policy_names)

  name   = each.key
  role   = aws_iam_role.instance.id
  policy = local.policies["instance/${each.key}"]
}

resource "aws_iam_instance_profile" "instance" {
  name = "jarvis-instance"
  role = aws_iam_role.instance.name
}
