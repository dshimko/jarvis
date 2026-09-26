# compute

The `alias/jarvis` CMK (key policy DESIGN-IAM.md 3.3) and EBS default encryption, the
`jarvis-instance` role with AmazonSSMManagedInstanceCore and the three inline policies of
DESIGN-IAM.md 3.2 (each with the AD29 `DenyOffInstance`), the instance profile, a primary ENI
with the Elastic IP, the launch template (IMDSv2, hop limit 1, gp3 30 GiB CMK root with
`delete_on_termination = false`, standard credits, no key pair), the instance (count from
`instance_enabled`, termination protection, `ignore_changes = [ami, user_data, launch_template]`)
and the `jarvis-auto-recover` alarm.

User data is `templatefile("templates/user_data.sh.tftpl", {artifacts_bucket, bootstrap_key,
bootstrap_sha256, region, instance_type, tailscale_secret_id, auto_reboot_time_utc})` (AD18,
DESIGN.md 6.4). The template is owned by bootstrap-engineer.
The AMI architecture comes from `instance_type` (AD20): `t4g.*` is arm64, anything else x86_64.
