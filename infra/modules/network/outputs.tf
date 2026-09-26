output "vpc_id" {
  description = "VPC id."
  value       = aws_vpc.this.id
}

output "subnet_id" {
  description = "Public subnet id (jarvis-public-a)."
  value       = aws_subnet.public.id
}

output "security_group_id" {
  description = "Instance security group id (zero ingress)."
  value       = aws_security_group.instance.id
}

output "flowlogs_bucket" {
  description = "Flow log bucket name."
  value       = aws_s3_bucket.flowlogs.bucket
}

output "policies" {
  description = "Every policy document this module creates, keyed by policy name (read by infra/tests)."
  value       = local.policies
}

output "security_groups" {
  description = "Security group rule inventory for the no_ingress test."
  value = {
    instance_sg_ingress = aws_security_group.instance.ingress
    default_sg_ingress  = aws_default_security_group.default.ingress
    default_sg_egress   = aws_default_security_group.default.egress
    egress_rules = { for k, r in aws_vpc_security_group_egress_rule.this : k => {
      proto = r.ip_protocol
      from  = r.from_port
      to    = r.to_port
      cidr  = r.cidr_ipv4
    } }
  }
}
