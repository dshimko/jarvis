#!/usr/bin/env bash
# jarvis-status: the SSM `jarvis-status` document body. Thin wrapper around the root jarvis-status
# tool (ops/aws/bin/jarvis-status, bootstrap-engineer): service states, last heartbeat, pending
# outbox counts, disk use, Tailscale status. Reports states and counts only, never secret or vault
# content. infra/DESIGN.md section 8.2; PLAN.md AD19.
set -euo pipefail

JARVIS_ROOT="${JARVIS_ROOT:-/opt/jarvis}"
exec "${JARVIS_STATUS_BIN:-$JARVIS_ROOT/bin/jarvis-status}"
