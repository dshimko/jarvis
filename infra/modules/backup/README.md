# backup

`jarvis-backup` vault (jarvis CMK), plan `jarvis-ebs` with rule `daily` (08:00 UTC, 14 days) and
rule `weekly` (09:00 UTC Sunday, 56 days), 60/360 minute windows, the `jarvis-backup` service
role (trust pinned by `aws:SourceAccount`, AWS managed backup and restore policies), and a
selection of EBS volumes in this account tagged `backup=jarvis`.
