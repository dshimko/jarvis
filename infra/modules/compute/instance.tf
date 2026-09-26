locals {
  is_arm64 = startswith(var.instance_type, "t4g.")
  # Ubuntu names the x86_64 image "amd64"; the EC2 architecture filter says "x86_64".
  ami_arch      = local.is_arm64 ? "arm64" : "x86_64"
  ami_name_arch = local.is_arm64 ? "arm64" : "amd64"

  instance_count = var.instance_enabled ? 1 : 0

  # Launch-template tag specifications do not inherit provider default_tags.
  base_tags = { app = "jarvis", env = "prod", owner = "dushan" }

  # AD18 variables, plus the sha256 (DESIGN.md 6.4 step 1) and the reboot time (6.4 step e2).
  # bootstrap-engineer owns the template.
  user_data = templatefile("${path.module}/templates/user_data.sh.tftpl", {
    artifacts_bucket     = var.artifacts_bucket
    bootstrap_key        = var.bootstrap_key
    bootstrap_sha256     = var.bootstrap_sha256
    region               = var.aws_region
    instance_type        = var.instance_type
    tailscale_secret_id  = var.tailscale_secret_id
    auto_reboot_time_utc = var.auto_reboot_time_utc
  })
}

data "aws_ami" "ubuntu" {
  most_recent = true
  owners      = ["099720109477"]

  filter {
    name   = "name"
    values = ["ubuntu/images/hvm-ssd-gp3/ubuntu-noble-24.04-${local.ami_name_arch}-server-*"]
  }

  filter {
    name   = "architecture"
    values = [local.ami_arch]
  }

  filter {
    name   = "virtualization-type"
    values = ["hvm"]
  }
}

resource "aws_network_interface" "primary" {
  subnet_id       = var.subnet_id
  security_groups = [var.security_group_id]
  description     = "jarvis primary ENI"

  tags = { Name = "jarvis-eni" }
}

resource "aws_eip" "jarvis" {
  domain            = "vpc"
  network_interface = aws_network_interface.primary.id

  tags = { Name = "jarvis-eip" }
}

resource "aws_launch_template" "jarvis" {
  name                    = "jarvis"
  image_id                = data.aws_ami.ubuntu.id
  instance_type           = var.instance_type
  user_data               = base64encode(local.user_data)
  disable_api_termination = true
  update_default_version  = true

  iam_instance_profile {
    arn = aws_iam_instance_profile.instance.arn
  }

  metadata_options {
    http_endpoint               = "enabled"
    http_tokens                 = "required"
    http_put_response_hop_limit = 1
    http_protocol_ipv6          = "disabled"
    instance_metadata_tags      = "disabled"
  }

  credit_specification {
    cpu_credits = "standard"
  }

  maintenance_options {
    auto_recovery = "default"
  }

  block_device_mappings {
    device_name = "/dev/sda1"

    ebs {
      volume_type           = "gp3"
      volume_size           = var.root_volume_size_gib
      iops                  = 3000
      throughput            = 125
      encrypted             = true
      kms_key_id            = aws_kms_key.jarvis.arn
      delete_on_termination = false
    }
  }

  network_interfaces {
    device_index         = 0
    network_interface_id = aws_network_interface.primary.id
  }

  tag_specifications {
    resource_type = "volume"
    tags          = merge(local.base_tags, { Name = "jarvis-root", backup = "jarvis" })
  }

  tag_specifications {
    resource_type = "instance"
    tags          = merge(local.base_tags, { Name = "jarvis" })
  }

  tags = { Name = "jarvis" }

  depends_on = [aws_eip.jarvis]
}

resource "aws_instance" "jarvis" {
  count = local.instance_count

  disable_api_termination = true

  launch_template {
    id      = aws_launch_template.jarvis.id
    version = aws_launch_template.jarvis.latest_version
  }

  # Mirrors the launch template so scanners and drift checks see the same settings.
  metadata_options {
    http_endpoint               = "enabled"
    http_tokens                 = "required"
    http_put_response_hop_limit = 1
    http_protocol_ipv6          = "disabled"
    instance_metadata_tags      = "disabled"
  }

  credit_specification {
    cpu_credits = "standard"
  }

  tags = { Name = "jarvis" }

  # A new AMI or bootstrap tarball never replaces the instance (DESIGN.md section 1).
  lifecycle {
    ignore_changes = [ami, user_data, launch_template]
  }

  # cloud-init needs the role's permissions on first boot.
  depends_on = [aws_iam_role_policy.instance, aws_iam_role_policy_attachment.ssm_core]
}

resource "aws_cloudwatch_metric_alarm" "auto_recover" {
  count = local.instance_count

  alarm_name          = "jarvis-auto-recover"
  alarm_description   = "Recover the instance on system status check failure"
  namespace           = "AWS/EC2"
  metric_name         = "StatusCheckFailed_System"
  statistic           = "Maximum"
  period              = 60
  evaluation_periods  = 2
  datapoints_to_alarm = 2
  threshold           = 1
  comparison_operator = "GreaterThanOrEqualToThreshold"
  treat_missing_data  = "missing"
  dimensions          = { InstanceId = aws_instance.jarvis[0].id }
  alarm_actions       = ["arn:aws:automate:${var.aws_region}:ec2:recover", var.alert_topic_arn]
  ok_actions          = [var.alert_topic_arn]
}
