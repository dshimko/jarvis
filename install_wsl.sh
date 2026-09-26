#!/usr/bin/env bash
# Installer for Jarvis on WSL2 (Ubuntu, systemd). Idempotent: safe to re-run.
set -euo pipefail

JARVIS_DIR="$(cd "$(dirname "$0")" && pwd)"
JARVIS_DOT_DIR="$HOME/.jarvis"
MODES=(work personal)
declare -A API_PORT=([work]=8781 [personal]=8782)   # PLAN AD13; must match config.local.yaml

# --- a. WSL2 + systemd-as-PID1 check ---------------------------------------
OSRELEASE="$(cat /proc/sys/kernel/osrelease)"
if ! grep -qi microsoft <<<"$OSRELEASE" || ! grep -q WSL2 <<<"$OSRELEASE"; then
  echo "This does not look like WSL2 (osrelease: $OSRELEASE). Run inside WSL2 Ubuntu." >&2
  exit 1
fi

INIT_COMM="$(ps -p 1 -o comm= 2>/dev/null || true)"
if [ "$INIT_COMM" != "systemd" ]; then
  {
    echo "systemd is not PID 1 (found: '$INIT_COMM'). Jarvis needs systemd for the user service."
    echo
    echo "Fix:"
    if [ -f /etc/wsl.conf ]; then
      echo "  1. /etc/wsl.conf already exists -- edit it by hand and add (keep any"
      echo "     existing [interop]/[automount] sections):"
      echo "       [boot]"
      echo "       systemd=true"
    else
      echo "  1. Run:"
      echo "       sudo tee /etc/wsl.conf >/dev/null <<'WSLCONF'"
      echo "       [boot]"
      echo "       systemd=true"
      echo "       WSLCONF"
    fi
    echo "  2. From Windows PowerShell (not this WSL shell), run:"
    echo "       wsl.exe --shutdown"
    echo "  3. Reopen Ubuntu and re-run this script."
  } >&2
  exit 1
fi

# --- b. Refuse to run from /mnt/ (Windows FS) -------------------------------
REAL_JARVIS_DIR="$(realpath "$JARVIS_DIR")"
case "$REAL_JARVIS_DIR" in
  /mnt/*)
    echo "Refusing to install from $REAL_JARVIS_DIR: it is on the Windows filesystem (/mnt/*)." >&2
    echo "Secrets (env/*.env) must not live on the Windows FS. Clone the repo into your Linux" >&2
    echo "home instead, e.g.: git clone <repo> ~/jarvis && cd ~/jarvis && ./install_wsl.sh" >&2
    exit 1
    ;;
esac

# --- c. Resolve Windows USERPROFILE / LOCALAPPDATA via interop --------------
mkdir -p "$JARVIS_DOT_DIR"
chmod 700 "$JARVIS_DOT_DIR"

resolve_win_path() {
  # $1 = cache file under ~/.jarvis, $2 = Windows env var name (e.g. USERPROFILE)
  local cache_file="$1" win_var="$2" raw resolved cached stripped
  if [ -s "$cache_file" ]; then
    cached="$(head -n1 "$cache_file")"
    stripped="${cached//[[:space:]]/}"
    # Treat empty/whitespace-only content, or a dir that no longer exists, as absent.
    if [ -n "$stripped" ] && [ -d "$cached" ]; then
      printf '%s\n' "$cached"
      return
    fi
  fi
  raw="$(/mnt/c/Windows/System32/cmd.exe /c "echo %${win_var}%" 2>/dev/null | tr -d '\r')"
  if [ -z "$raw" ] || [ "$raw" = "%${win_var}%" ]; then
    {
      echo "Could not read Windows %${win_var}% via WSL interop."
      echo "Check that interop is enabled in /etc/wsl.conf:"
      echo "  [interop]"
      echo "  enabled=true"
    } >&2
    exit 1
  fi
  resolved="$(wslpath -u "$raw")"
  if [ -z "$resolved" ] || [ ! -d "$resolved" ]; then
    echo "Resolved path for %${win_var}% ('$resolved') is empty or does not exist." >&2
    exit 1
  fi
  printf '%s\n' "$resolved" > "$cache_file"
  printf '%s\n' "$resolved"
}

WIN_HOME="$(resolve_win_path "$JARVIS_DOT_DIR/win_home" USERPROFILE)"
WIN_LOCALAPPDATA="$(resolve_win_path "$JARVIS_DOT_DIR/win_localappdata" LOCALAPPDATA)"
mkdir -p "$WIN_LOCALAPPDATA/Jarvis"

# --- e. apt prerequisites, venv, pip deps (must run before d: Claude Code's
#        install script needs curl) ------------------------------------------
missing_pkgs=()
dpkg -s python3-venv >/dev/null 2>&1 || missing_pkgs+=(python3-venv)
command -v git >/dev/null 2>&1 || missing_pkgs+=(git)
command -v curl >/dev/null 2>&1 || missing_pkgs+=(curl)
if [ "${#missing_pkgs[@]}" -gt 0 ]; then
  echo "Installing missing prerequisites: ${missing_pkgs[*]}"
  sudo apt-get update -qq
  sudo apt-get install -y "${missing_pkgs[@]}"
fi

[ -d "$JARVIS_DIR/.venv" ] || python3 -m venv "$JARVIS_DIR/.venv"
"$JARVIS_DIR/.venv/bin/pip" install -q -r "$JARVIS_DIR/requirements.txt"

# --- d. Claude Code (native install) ----------------------------------------
export PATH="$HOME/.local/bin:$PATH"
if ! command -v claude >/dev/null 2>&1; then
  echo "Installing Claude Code..."
  curl -fsSL https://claude.ai/install.sh | bash
fi

# --- f. Vaults on the Windows FS ---------------------------------------------
for mode in work personal; do
  case "$mode" in
    work) vault_name="Jarvis-Work" ;;
    personal) vault_name="Jarvis-Personal" ;;
  esac
  dest="$WIN_HOME/Vaults/$vault_name"
  if [ ! -d "$dest" ]; then
    mkdir -p "$(dirname "$dest")"
    cp -R "$JARVIS_DIR/vaults/$mode" "$dest"
    git -C "$dest" init -q
    git -C "$dest" config core.autocrlf false
    git -C "$dest" config core.filemode false
  elif [ -d "$dest/.git" ]; then
    git -C "$dest" config core.autocrlf false
    git -C "$dest" config core.filemode false
  fi
done

# --- g. env files ------------------------------------------------------------
for mode in "${MODES[@]}"; do
  [ -f "$JARVIS_DIR/env/$mode.env" ] || cp "$JARVIS_DIR/env/$mode.env.example" "$JARVIS_DIR/env/$mode.env"
done
chmod 600 "$JARVIS_DIR"/env/*.env
chmod 700 "$JARVIS_DIR/env"

# --- h. systemd user units: one daemon per mode (jarvis@work, jarvis@personal) --
UNIT_DIR="$HOME/.config/systemd/user"
mkdir -p "$UNIT_DIR"
# The single combined daemon (jarvis.service, port 8765) is replaced by the per-mode template.
if [ -f "$UNIT_DIR/jarvis.service" ]; then
  systemctl --user disable --now jarvis.service 2>/dev/null || true
  rm -f "$UNIT_DIR/jarvis.service"
fi
sed "s#__JARVIS_DIR__#$JARVIS_DIR#g" "$JARVIS_DIR/ops/jarvis@.service" > "$UNIT_DIR/jarvis@.service"
systemctl --user daemon-reload

# No secret value may appear in both env files (key names are printed, never values). A violation
# leaves the units disabled.
if ! ( cd "$JARVIS_DIR" && .venv/bin/python -m jarvis.secrets_check env/work.env env/personal.env ); then
  for mode in "${MODES[@]}"; do systemctl --user disable --now "jarvis@$mode" 2>/dev/null || true; done
  echo "Refusing to enable jarvis@work / jarvis@personal: fix the shared secrets above, then re-run." >&2
  exit 1
fi
for mode in "${MODES[@]}"; do systemctl --user enable "jarvis@$mode"; done
sudo loginctl enable-linger "$USER"
echo "Services jarvis@work and jarvis@personal installed but NOT started (env files are still empty)."

# --- i. clock / timezone sanity ------------------------------------------------
echo "--- timedatectl ---"
timedatectl || true
TZ_NAME="$(timedatectl show --property=Timezone --value 2>/dev/null || true)"
case "$TZ_NAME" in
  UTC|Etc/*)
    echo "Warning: system timezone is '$TZ_NAME'. If Windows is not on UTC, set a real" >&2
    echo "timezone (e.g. sudo timedatectl set-timezone America/Los_Angeles) so schedules" >&2
    echo "and daily caps line up with your local day." >&2
    ;;
esac
echo "Note: after Windows sleep/resume the WSL2 clock can drift; if so, run: sudo hwclock -s"

# --- j. verify_secrets (best-effort, non-fatal on first install) --------------
if [ -f "$JARVIS_DIR/jarvis/verify_secrets.py" ]; then
  ( cd "$JARVIS_DIR" && .venv/bin/python -m jarvis.verify_secrets ) \
    || echo "verify_secrets reported issues (expected on first install; env files are empty)."
fi

# --- k. remaining manual steps -------------------------------------------------
cat <<EOF

Next steps:
  1. Fill in env/work.env and env/personal.env with real secrets.
  2. Start the daemons: systemctl --user start jarvis@work jarvis@personal
     (re-run this script after editing the env files: it re-checks for shared secrets)
  3. In EACH vault dir, run 'claude' once and use /mcp to authorize OAuth servers:
       cd "$WIN_HOME/Vaults/Jarvis-Work"     && claude   (then /mcp)
       cd "$WIN_HOME/Vaults/Jarvis-Personal" && claude   (then /mcp)
     A browser should open on Windows for the OAuth flow; if it does not,
     'sudo apt install wslu' (for wslview) or copy the printed URL into a
     Windows browser by hand. The localhost OAuth callback is forwarded.
  4. Fill in the exact read_tools/write_tools MCP names in config.yaml
     (run 'claude mcp list' plus a tool listing in each vault).
  5. On Windows, run windows_client\\install.ps1
  6. Verify from Windows PowerShell (work, then personal):
       curl.exe -H "Authorization: Bearer \$(Get-Content \$env:LOCALAPPDATA\\Jarvis\\api_token)" http://localhost:${API_PORT[work]}/health
       curl.exe -H "Authorization: Bearer \$(Get-Content \$env:LOCALAPPDATA\\Jarvis\\api_token)" http://localhost:${API_PORT[personal]}/health
EOF
