#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SOURCE="${SCRIPT_DIR}/99-svm-v11-2"
TARGET="/etc/update-motd.d/99-svm-v11-2"

if [[ "$(id -u)" != 0 ]]; then
  printf 'Run with sudo: sudo bash %s\n' "$0" >&2
  exit 1
fi
[[ -f "$SOURCE" ]] || { printf 'Missing MOTD script: %s\n' "$SOURCE" >&2; exit 1; }
[[ -d /etc/update-motd.d ]] || { printf 'Dynamic MOTD directory is not available on this system.\n' >&2; exit 1; }

tmp="$(mktemp /etc/update-motd.d/.svm-motd.XXXXXX)"
trap 'rm -f "$tmp"' EXIT
install -m 0755 "$SOURCE" "$tmp"
mv -f "$tmp" "$TARGET"
trap - EXIT

printf 'Installed the SVM display-only MOTD at %s\n' "$TARGET"
printf 'Existing MOTD scripts, PAM configuration, and SSH service settings were left unchanged.\n'