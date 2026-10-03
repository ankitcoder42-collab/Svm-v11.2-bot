#!/usr/bin/env bash
set -Eeuo pipefail

# ============================================================================
# SVM V11.2 PRODUCTION INSTALLER / UPGRADER
# Large SVM branding • Made by AnkitCoder
# Safe re-run • Config preservation • Backups • Validation • Systemd • Health
# ============================================================================

APP_DIR="${SVM_DIR:-/opt/svm}"
SERVICE="${SVM_SERVICE:-svm}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PY="${APP_DIR}/venv/bin/python"
PIP="${APP_DIR}/venv/bin/pip"
ENV_FILE="${APP_DIR}/.env"
BACKUP_DIR="${APP_DIR}/backups"
RUN_DIR="${APP_DIR}/run"
LOCK_FILE="/var/lock/svm-v11.2-install.lock"
STAMP="$(date +%Y%m%d-%H%M%S)"

R='\033[0m'; B='\033[1m'; C='\033[36m'; G='\033[32m'; Y='\033[33m'; E='\033[31m'; M='\033[35m'

banner() {
  printf '\n'
  printf "${B}${C}╔══════════════════════════════════════════════════════════════════════════════╗${R}\n"
  printf "${B}${C}║                                                                            ║${R}\n"
  printf "${B}${C}║                         S V M   V 1 1 . 2                                ║${R}\n"
  printf "${B}${M}║                    S V M - V 1 1 . 2  PRO                               ║${R}\n"
  printf "${B}${G}║                         Made by AnkitCoder                                ║${R}\n"
  printf "${B}${C}║                                                                            ║${R}\n"
  printf "${B}${C}║   VPS • KVM • LXC • QEMU • LIBVIRT • BILLING • PAYMENT • DISCORD         ║${R}\n"
  printf "${B}${C}║   WEBSSH • IPAM • PORTS • MONITORING • BACKUPS • AUTO RECOVERY            ║${R}\n"
  printf "${B}${C}║                                                                            ║${R}\n"
  printf "${B}${C}╚══════════════════════════════════════════════════════════════════════════════╝${R}\n\n"
}
info(){ printf "${C}➜${R} %s\n" "$*"; }
ok(){ printf "${G}✓${R} %s\n" "$*"; }
warn(){ printf "${Y}!${R} %s\n" "$*"; }
die(){ printf "${E}✗${R} %s\n" "$*" >&2; exit 1; }
section(){ printf "\n${B}${C}━━━ %s ━━━${R}\n" "$*"; }

ai_cli() {
  local python="${PY}"
  [[ -x "${python}" ]] || python="$(command -v python3 || true)"
  [[ -n "${python}" ]] || die "python3 is required"
  [[ -f "${SCRIPT_DIR}/ai_agent.py" ]] || die "ai_agent.py is missing beside install.sh"
  "${python}" "${SCRIPT_DIR}/ai_agent.py" "$1" \
    --env-file "${ENV_FILE}" --state-file "${APP_DIR}/ai.disabled"
}

case "${1:-install}" in
  install)
    ;;
  ai-install)
    info "Deploying the in-process AI gateway with the SVM bot; LocalAI and model files are managed separately."
    ;;
  ai-start|ai-restart)
    banner
    action="start"
    [[ "${1}" == "ai-restart" ]] && action="restart"
    ai_cli "${action}"
    exit $?
    ;;
  ai-stop)
    banner
    ai_cli stop
    exit $?
    ;;
  ai-status)
    banner
    ai_cli status
    exit $?
    ;;
  ai-models)
    banner
    ai_cli models
    exit $?
    ;;
  ai-doctor)
    banner
    ai_cli doctor
    exit $?
    ;;
  status)
    banner
    systemctl status "${SERVICE}.service" --no-pager
    exit $?
    ;;
  doctor)
    banner
    command -v python3 >/dev/null 2>&1 || die "python3 is required"
    python3 -c 'import sys; assert sys.version_info >= (3,9), sys.version' || die "Python 3.9+ is required"
    if systemctl is-active --quiet "${SERVICE}.service"; then
      ok "${SERVICE}.service is active"
    else
      warn "${SERVICE}.service is not active"
    fi
    if [[ -x "${PY}" ]]; then
      "${PY}" -c 'import discord, flask, paramiko, requests, psutil, PIL, dotenv' \
        && ok "Python application dependencies import successfully" \
        || warn "One or more application dependencies are unavailable"
    else
      warn "Application virtualenv not found at ${APP_DIR}/venv"
    fi
    ai_cli doctor
    exit $?
    ;;
  *)
    die "Usage: $0 [install|status|doctor|ai-install|ai-start|ai-stop|ai-restart|ai-status|ai-models|ai-doctor]"
    ;;
esac

trap 'die "Installation failed at line ${LINENO}. Check: journalctl -u ${SERVICE} -n 150 --no-pager"' ERR

banner

[[ "$(id -u)" == 0 ]] || die "Run as root: sudo bash install.sh"
[[ -f "${SCRIPT_DIR}/bot.py" ]] || die "bot.py not found beside install.sh"
[[ -f "${SCRIPT_DIR}/ai_agent.py" ]] || die "ai_agent.py not found beside install.sh"
[[ -f "${SCRIPT_DIR}/webssh.html" ]] || die "webssh.html not found beside install.sh"
[[ -f "${SCRIPT_DIR}/requirements.txt" ]] || die "requirements.txt not found beside install.sh"
[[ -f "${SCRIPT_DIR}/.env.example" ]] || die ".env.example not found beside install.sh"

exec 9>"${LOCK_FILE}"
flock -n 9 || die "Another SVM installer is already running"

section "SVM V11.2 ENVIRONMENT"
info "Installer : SVM V11.2 PRO"
info "Developer : AnkitCoder"
info "Directory : ${APP_DIR}"
info "Service   : ${SERVICE}"
info "Time      : ${STAMP}"

section "SYSTEM DETECTION"
OS="unknown"; VERSION="unknown"; ARCH="$(uname -m)"
if [[ -r /etc/os-release ]]; then
  # shellcheck disable=SC1091
  . /etc/os-release
  OS="${ID:-unknown}"
  VERSION="${VERSION_ID:-unknown}"
fi
info "OS        : ${OS} ${VERSION}"
info "Kernel    : $(uname -r)"
info "Arch      : ${ARCH}"
info "Hostname  : $(hostname)"
ok "Running as root"

section "DEPENDENCIES"
export DEBIAN_FRONTEND=noninteractive
if command -v apt-get >/dev/null 2>&1; then
  apt-get update -y
  apt-get install -y \
    python3 python3-venv python3-pip python3-dev \
    curl ca-certificates git sqlite3 openssh-client \
    lxc lxc-utils libvirt-clients qemu-utils \
    qemu-system-x86 qemu-kvm dnsutils iproute2 procps util-linux \
    rsync unzip jq openssl 2>/dev/null || warn "Some optional packages were unavailable; continuing"
elif command -v dnf >/dev/null 2>&1; then
  dnf install -y python3 python3-pip python3-devel curl ca-certificates git sqlite openssh-clients qemu-img libvirt-client iproute procps-ng rsync unzip jq openssl || warn "Some optional packages were unavailable"
elif command -v yum >/dev/null 2>&1; then
  yum install -y python3 python3-pip curl ca-certificates git sqlite openssh-clients qemu-img libvirt-client iproute procps-ng rsync unzip jq openssl || warn "Some optional packages were unavailable"
else
  warn "No supported package manager detected. Install Python 3 + venv manually."
fi

command -v python3 >/dev/null 2>&1 || die "python3 is required"
python3 -c 'import sys; assert sys.version_info >= (3,9), sys.version' || die "Python 3.9+ is required"

section "DIRECTORIES + SAFE BACKUP"
mkdir -p "${APP_DIR}" "${APP_DIR}/data" "${APP_DIR}/logs" "${APP_DIR}/backups" "${RUN_DIR}"
chmod 700 "${APP_DIR}" "${APP_DIR}/data" "${APP_DIR}/backups" "${RUN_DIR}"

# Preserve current live installation before replacing files.
BACKUP_SNAPSHOT="${BACKUP_DIR}/upgrade-${STAMP}"
mkdir -p "${BACKUP_SNAPSHOT}"
for f in bot.py ai_agent.py webssh.html requirements.txt .env ai.disabled; do
  [[ -f "${APP_DIR}/${f}" ]] && cp -a "${APP_DIR}/${f}" "${BACKUP_SNAPSHOT}/${f}"
done
if [[ -f "${APP_DIR}/vps.db" ]]; then
  python3 - "${APP_DIR}/vps.db" "${BACKUP_SNAPSHOT}/vps.db" <<'PY'
import sqlite3, sys
source = sqlite3.connect(sys.argv[1], timeout=30)
target = sqlite3.connect(sys.argv[2])
try:
    source.backup(target)
    result = target.execute("PRAGMA integrity_check").fetchone()[0]
    if result != "ok":
        raise SystemExit(f"SQLite backup integrity check failed: {result}")
finally:
    target.close()
    source.close()
PY
  ok "Consistent SQLite database backup created"
fi
ok "Backup snapshot: ${BACKUP_SNAPSHOT}"

section "CONFIGURATION PRESERVATION"
if [[ -f "${ENV_FILE}" ]]; then
  cp -a "${ENV_FILE}" "${BACKUP_SNAPSHOT}/.env.previous"
  ok "Existing .env preserved"
else
  cp "${SCRIPT_DIR}/.env.example" "${ENV_FILE}"
  warn "Created ${ENV_FILE}; configure Discord/payment/provider secrets before production use"
fi

# Merge new keys from .env.example without overwriting existing values.
tmp_env="$(mktemp)"
cp "${ENV_FILE}" "${tmp_env}"
while IFS= read -r line || [[ -n "$line" ]]; do
  [[ "$line" =~ ^([A-Za-z_][A-Za-z0-9_]*)= ]] || continue
  key="${BASH_REMATCH[1]}"
  if ! grep -qE "^[[:space:]]*${key}=" "${tmp_env}"; then
    printf '%s\n' "$line" >> "${tmp_env}"
  fi
done < "${SCRIPT_DIR}/.env.example"
install -m 600 "${tmp_env}" "${ENV_FILE}"
rm -f "${tmp_env}"
ok "Configuration preserved and newly introduced keys merged"

section "DEPLOY SVM V11.2 APPLICATION"
install -m 750 "${SCRIPT_DIR}/bot.py" "${APP_DIR}/bot.py"
install -m 750 "${SCRIPT_DIR}/ai_agent.py" "${APP_DIR}/ai_agent.py"
install -m 644 "${SCRIPT_DIR}/webssh.html" "${APP_DIR}/webssh.html"
install -m 644 "${SCRIPT_DIR}/requirements.txt" "${APP_DIR}/requirements.txt"
install -d -m 750 "${APP_DIR}/tests"
install -m 644 "${SCRIPT_DIR}/tests/test_ai_agent.py" "${APP_DIR}/tests/test_ai_agent.py"
ok "bot.py + LocalAI gateway + WebSSH + requirements deployed"

section "PYTHON VIRTUAL ENVIRONMENT"
if [[ ! -x "${PY}" ]]; then
  python3 -m venv "${APP_DIR}/venv"
fi
"${PY}" -m pip install --upgrade pip setuptools wheel
"${PIP}" install --upgrade -r "${APP_DIR}/requirements.txt"
ok "Python dependencies installed"

section "STATIC VALIDATION"
"${PY}" -m py_compile "${APP_DIR}/bot.py"
"${PY}" -m py_compile "${APP_DIR}/ai_agent.py"
"${PY}" - "${APP_DIR}" <<'PY'
import ast, pathlib, sys
root = pathlib.Path(sys.argv[1])
for name in ("bot.py", "ai_agent.py"):
    ast.parse((root / name).read_text(encoding="utf-8"))
print("AST validation: PASS")
PY
PYTHONPATH="${APP_DIR}" "${PY}" -m unittest discover -s "${APP_DIR}/tests" -v
ok "SVM Python syntax, AST validation, and LocalAI unit tests passed"

CONFIG_READY="$("${PY}" - "${ENV_FILE}" <<'PY'
import sys
values = {}
for raw in open(sys.argv[1], encoding="utf-8"):
    line = raw.strip()
    if not line or line.startswith("#") or "=" not in line:
        continue
    key, value = line.split("=", 1)
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        value = value[1:-1]
    values[key.strip()] = value
token = values.get("DISCORD_TOKEN", "").strip()
try:
    admin_id = int(values.get("MAIN_ADMIN_ID", "0").strip())
except ValueError:
    admin_id = 0
print("yes" if token and token != "your_discord_bot_token_here" and admin_id > 0 else "no")
PY
)"

section "VIRTUALIZATION HEALTH"
if [[ -e /dev/kvm ]]; then
  ok "/dev/kvm detected — KVM hardware/device access is available"
else
  warn "/dev/kvm not detected — KVM VPS creation will require host/hypervisor support"
fi

if command -v kvm-ok >/dev/null 2>&1; then
  kvm-ok >/dev/null 2>&1 && ok "CPU virtualization check passed" || warn "CPU virtualization check did not pass"
fi
command -v lxc >/dev/null 2>&1 && ok "LXC client available" || warn "LXC client unavailable"
command -v virsh >/dev/null 2>&1 && ok "libvirt client available" || warn "libvirt client unavailable"
command -v qemu-img >/dev/null 2>&1 && ok "qemu-img available" || warn "qemu-img unavailable"

section "SYSTEMD SERVICE"
cat > "/etc/systemd/system/${SERVICE}.service" <<UNIT
[Unit]
Description=SVM V11.2 PRO VPS Platform — Made by AnkitCoder
Documentation=https://github.com/AnkitKing7/Svm-v4
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=root
Group=root
WorkingDirectory=${APP_DIR}
EnvironmentFile=${ENV_FILE}
Environment=PYTHONUNBUFFERED=1
Environment=PYTHONDONTWRITEBYTECODE=1
ExecStart=${PY} ${APP_DIR}/bot.py
Restart=always
RestartSec=5
StartLimitIntervalSec=60
StartLimitBurst=10
TimeoutStartSec=60
TimeoutStopSec=30
KillSignal=SIGINT
StandardOutput=journal
StandardError=journal
SyslogIdentifier=svm-v11.2

# The SVM virtualization layer may need root capabilities for LXC/libvirt,
# network namespaces, bridges, mounts and QEMU/KVM operations.
NoNewPrivileges=false
PrivateTmp=false
ProtectSystem=false
ProtectHome=false
ReadWritePaths=${APP_DIR}

[Install]
WantedBy=multi-user.target
UNIT

systemctl daemon-reload
if [[ "${CONFIG_READY}" == "yes" ]]; then
  systemctl enable "${SERVICE}.service" >/dev/null
  systemctl restart "${SERVICE}.service"
  ok "systemd service installed, enabled, and restarted"
else
  warn "Service was not started: set DISCORD_TOKEN and a positive MAIN_ADMIN_ID in ${ENV_FILE}, then run systemctl enable --now ${SERVICE}"
fi

section "POST-INSTALL HEALTH CHECK"
if [[ "${CONFIG_READY}" == "yes" ]]; then
  sleep 3
  if systemctl is-active --quiet "${SERVICE}.service"; then
    ok "SVM V11.2 service is RUNNING"
  else
    warn "SVM service is not running; showing latest logs"
    journalctl -u "${SERVICE}.service" -n 120 --no-pager || true
    die "SVM V11.2 failed to start"
  fi

  # Basic process/log sanity check without assuming a specific bot token.
  if journalctl -u "${SERVICE}.service" -n 80 --no-pager | grep -Eiq 'Traceback|SyntaxError|ImportError|ModuleNotFoundError'; then
    warn "Startup logs contain an error-like entry; inspect: journalctl -u ${SERVICE} -n 150 --no-pager"
  else
    ok "No common Python startup error detected in recent logs"
  fi
else
  warn "Configure DISCORD_TOKEN and MAIN_ADMIN_ID before starting the service."
fi

section "INSTALLATION COMPLETE"
printf "\n${B}${G}╔══════════════════════════════════════════════════════════════════════════════╗${R}\n"
printf "${B}${G}║                    SVM V11.2 PRO INSTALLED                               ║${R}\n"
printf "${B}${C}║                         Made by AnkitCoder                                ║${R}\n"
printf "${B}${G}╚══════════════════════════════════════════════════════════════════════════════╝${R}\n\n"
printf "${B}App       :${R} %s\n" "${APP_DIR}"
printf "${B}Config    :${R} %s (chmod 600)\n" "${ENV_FILE}"
printf "${B}Service   :${R} systemctl status ${SERVICE} --no-pager\n"
printf "${B}Logs      :${R} journalctl -u ${SERVICE} -f\n"
printf "${B}Restart   :${R} systemctl restart ${SERVICE}\n"
printf "${B}Stop      :${R} systemctl stop ${SERVICE}\n"
printf "${B}AI status :${R} sudo %s ai-status\n" "${SCRIPT_DIR}/install.sh"
printf "${B}AI models :${R} sudo %s ai-models\n" "${SCRIPT_DIR}/install.sh"
printf "${B}Backup    :${R} %s\n" "${BACKUP_SNAPSHOT}"
printf "${B}Brand     :${R} SVM V11.2 • Made by AnkitCoder\n\n"

warn "Before production use: configure DISCORD_TOKEN, MAIN_ADMIN_ID and payment/provider secrets in ${ENV_FILE}."
warn "Payment screenshots/OCR must not be treated as authoritative payment confirmation; use gateway verification/webhooks."
