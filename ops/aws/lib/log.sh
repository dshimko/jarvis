#!/usr/bin/env bash
# Shared logging helper for ops/aws scripts. Callers are responsible for never passing a secret
# value or vault content to log() -- it prints its argument verbatim to stdout/cloud-init output.
log() {
  printf '[bootstrap %s] %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$*"
}
