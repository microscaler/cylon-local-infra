#!/bin/bash
# cylon-rc-guard — run a third-party installer without letting it edit shell rc
# files. Managed by cylon-local-infra :: roles/shell_env.
#
#   cylon-rc-guard <command> [args...]
#
# Snapshots the user's rc files, runs the command, then restores any rc file it
# changed and removes any rc file it created. Exit status is the command's.
set -euo pipefail
[ "$#" -gt 0 ] || { echo "usage: $0 <command> [args...]" >&2; exit 2; }

rc_files=(.zshrc .zprofile .zshenv .zlogin .bashrc .bash_profile .profile)
snap=$(mktemp -d "${TMPDIR:-/tmp}/cylon-rc-guard.XXXXXX")
trap 'rm -rf "$snap"' EXIT

for f in "${rc_files[@]}"; do
  if [ -e "$HOME/$f" ]; then cp -p "$HOME/$f" "$snap/$f"; else : > "$snap/$f.absent"; fi
done

rc=0
"$@" || rc=$?

for f in "${rc_files[@]}"; do
  if [ -e "$snap/$f.absent" ]; then
    if [ -e "$HOME/$f" ]; then
      rm -f "$HOME/$f"
      echo "cylon-rc-guard: removed ~/$f created by the installer" >&2
    fi
  elif ! cmp -s "$snap/$f" "$HOME/$f" 2>/dev/null; then
    cp -p "$snap/$f" "$HOME/$f"
    echo "cylon-rc-guard: restored ~/$f (installer edited it)" >&2
  fi
done
exit "$rc"
