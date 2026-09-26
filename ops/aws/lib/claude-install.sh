#!/usr/bin/env bash
# Installs Claude Code (native installer) per mode user. PLAN.md AD31: root never opens a path
# inside a mode user's home directly, so this runs entirely as that user via `runuser`.

install_claude_code_for_all_users() {
  local mode user
  for mode in work personal; do
    user="jarvis-$mode"
    if runuser -u "$user" -- test -x "/home/$user/.local/bin/claude"; then
      log "claude already installed for $mode, skipping"
      continue
    fi
    runuser -u "$user" -- bash -c 'curl -fsSL https://claude.ai/install.sh | bash'
  done
}
