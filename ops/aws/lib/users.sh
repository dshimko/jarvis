#!/usr/bin/env bash
# Users, groups, and directories (infra/DESIGN.md sections 6.1-6.2; PLAN.md AD17, AD32).
# Every step here is idempotent: checked with `id -u` / `getent group` / `install -d` before
# acting, so re-running bootstrap.sh never fails on "already exists".

# C1: refuses to proceed if /home/<user> is anything other than a real directory owned by that
# user. Every later step that creates a path under that home does so via `runuser -u <user> --`,
# never as root, so a symlink planted there (e.g. vault -> /home/jarvis-personal) can only ever
# redirect onto something the user already owns -- but that guarantee only holds if the home
# itself has not been swapped for a symlink first, hence this check runs before any of them.
assert_real_home_dir() {
  local user="$1" home="/home/$1" owner
  if [ -L "$home" ]; then
    log "FAIL: $home is a symlink, refusing to proceed"
    return 1
  fi
  if [ ! -d "$home" ]; then
    log "FAIL: $home is not a directory, refusing to proceed"
    return 1
  fi
  owner=$(stat -c '%U' "$home")
  if [ "$owner" != "$user" ]; then
    log "FAIL: $home is not owned by $user (owner=$owner), refusing to proceed"
    return 1
  fi
}

create_mode_user() {
  local name="$1" id_num="$2"
  if id -u "$name" >/dev/null 2>&1; then
    return 0
  fi
  getent group "$name" >/dev/null 2>&1 || groupadd --system --gid "$id_num" "$name"
  useradd --system --uid "$id_num" --gid "$name" --home-dir "/home/$name" --create-home \
    --shell /usr/sbin/nologin --no-user-group "$name"
  assert_real_home_dir "$name"
  chmod 0700 "/home/$name"
}

setup_users() {
  create_mode_user jarvis-work 2001
  create_mode_user jarvis-personal 2002

  if ! id -u jarvis-build >/dev/null 2>&1; then
    # No home: jarvis-deploy gives it a fresh temp HOME per test run (DESIGN.md section 8.3).
    useradd --system --no-create-home --home-dir /nonexistent --shell /usr/sbin/nologin jarvis-build
  fi

  # procadm has no members; it exists only as the /proc hidepid= gid exemption for root tools
  # and the logind/polkit drop-ins (PLAN.md AD32).
  getent group procadm >/dev/null 2>&1 || groupadd --system procadm

  install -d -m 0755 -o root -g root /opt/jarvis
  install -d -m 0755 -o root -g root /opt/jarvis/releases
  install -d -m 0755 -o root -g root /opt/jarvis/bin
  install -d -m 0755 -o root -g root /opt/jarvis/libexec
  install -d -m 0700 -o root -g root /run/jarvis
  # Setgid so every file jarvis-logexport@ creates inherits group "adm" (M10: 0640 root:adm)
  # regardless of the creating process's own primary group.
  install -d -m 2750 -o root -g adm /var/log/jarvis
  install -d -m 0700 -o root -g root /var/lib/jarvis-logexport
  install -d -m 0700 -o root -g root /var/lib/jarvis-secrets
  install -d -m 0755 -o root -g root /etc/jarvis

  local mode user
  for mode in work personal; do
    user="jarvis-$mode"
    assert_real_home_dir "$user"
    # C1: every path below is under /home/$user. Created AS that user (never as root), so a
    # symlink planted at any of these names can only ever redirect onto a path that user already
    # owns -- root chown-ing through a symlink here would be a cross-mode write primitive.
    runuser -u "$user" -- install -d -m 0700 "/home/$user/vault"
    runuser -u "$user" -- install -d -m 0700 "/home/$user/.jarvis"
    runuser -u "$user" -- install -d -m 0700 "/home/$user/.claude"
    runuser -u "$user" -- install -d -m 0700 "/home/$user/.local/bin"
    runuser -u "$user" -- install -d -m 0700 "/home/$user/.local/state/syncthing"
  done

  echo root > /etc/cron.allow
  echo root > /etc/at.allow
  chmod 0644 /etc/cron.allow /etc/at.allow
}
