locals {
  az          = coalesce(var.availability_zone, "${var.aws_region}a")
  resolver_ip = cidrhost(var.vpc_cidr, 2)

  # DESIGN.md section 2, egress table E1 to E6. One resource per rule; zero ingress anywhere.
  egress_rules = {
    https     = { proto = "tcp", port = 443, cidr = "0.0.0.0/0", why = "E1 AWS APIs, Anthropic, Slack, Telegram, MCP, Tailscale control/DERP" }
    http      = { proto = "tcp", port = 80, cidr = "0.0.0.0/0", why = "E2 package mirrors only" }
    wireguard = { proto = "udp", port = 41641, cidr = "0.0.0.0/0", why = "E3 Tailscale WireGuard" }
    stun      = { proto = "udp", port = 3478, cidr = "0.0.0.0/0", why = "E4 Tailscale STUN" }
    dns_udp   = { proto = "udp", port = 53, cidr = "${local.resolver_ip}/32", why = "E5 DNS to the VPC resolver only" }
    dns_tcp   = { proto = "tcp", port = 53, cidr = "${local.resolver_ip}/32", why = "E6 DNS over TCP to the VPC resolver only" }
  }
}

resource "aws_vpc" "this" {
  cidr_block           = var.vpc_cidr
  enable_dns_support   = true
  enable_dns_hostnames = true

  tags = { Name = "jarvis-vpc" }
}

resource "aws_subnet" "public" {
  vpc_id                  = aws_vpc.this.id
  cidr_block              = var.subnet_cidr
  availability_zone       = local.az
  map_public_ip_on_launch = false

  tags = { Name = "jarvis-public-a" }
}

resource "aws_internet_gateway" "this" {
  vpc_id = aws_vpc.this.id

  tags = { Name = "jarvis-igw" }
}

resource "aws_route_table" "public" {
  vpc_id = aws_vpc.this.id

  route {
    cidr_block = "0.0.0.0/0"
    gateway_id = aws_internet_gateway.this.id
  }

  tags = { Name = "jarvis-public-rt" }
}

resource "aws_route_table_association" "public" {
  subnet_id      = aws_subnet.public.id
  route_table_id = aws_route_table.public.id
}

# Zero ingress rules. Egress rules are separate resources below.
resource "aws_security_group" "instance" {
  name        = "jarvis-instance-sg"
  description = "Jarvis instance: no inbound, egress per DESIGN.md section 2"
  vpc_id      = aws_vpc.this.id

  tags = { Name = "jarvis-instance-sg" }
}

# E1 to E4 are egress to 0.0.0.0/0 by design (DESIGN.md section 2, concern C11).
# trivy:ignore:AVD-AWS-0104 Egress to 0.0.0.0/0 on 443/80/41641/3478 is required; see DESIGN.md section 2
resource "aws_vpc_security_group_egress_rule" "this" {
  for_each = local.egress_rules

  security_group_id = aws_security_group.instance.id
  description       = each.value.why
  ip_protocol       = each.value.proto
  from_port         = each.value.port
  to_port           = each.value.port
  cidr_ipv4         = each.value.cidr

  tags = { Name = "jarvis-egress-${each.key}" }
}

# Adopt the default SG and strip every rule (no ingress or egress blocks).
resource "aws_default_security_group" "default" {
  vpc_id = aws_vpc.this.id

  tags = { Name = "jarvis-default-sg-unused" }
}

resource "aws_flow_log" "vpc" {
  vpc_id                   = aws_vpc.this.id
  traffic_type             = "ALL"
  log_destination_type     = "s3"
  log_destination          = aws_s3_bucket.flowlogs.arn
  max_aggregation_interval = 600

  # Hive-compatible partitions would write under AWSLogs/aws-account-id=ACCT/, which the
  # allowlisted delivery grant (DESIGN.md P21, AWSLogs/ACCT/*) does not cover. Kept off.
  destination_options {
    file_format                = "plain-text"
    hive_compatible_partitions = false
    per_hour_partition         = false
  }

  tags = { Name = "jarvis-vpc-flowlog" }

  depends_on = [aws_s3_bucket_policy.flowlogs]
}
