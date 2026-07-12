#!/usr/bin/env bash
set -euo pipefail

APP_NAME="${APP_NAME:-node-3-ddos-guard}"
INSTALL_DIR="${INSTALL_DIR:-/opt/${APP_NAME}}"
SERVICE_NAME="${SERVICE_NAME:-${APP_NAME}}"
ENV_FILE="${ENV_FILE:-/etc/${APP_NAME}.env}"
HTTP_PORT="${HTTP_PORT:-8080}"
UDP_PORT="${UDP_PORT:-27015}"
GAME_PROTOCOL="${GAME_PROTOCOL:-source}"
TRUSTED_PROXY_HOPS="${TRUSTED_PROXY_HOPS:-0}"
RUN_USER="${RUN_USER:-${APP_NAME}}"
ACTION="${1:-install}"

usage() {
  cat <<USAGE
Usage: sudo ./install-or-run.sh [install|run|restart|status]

Actions:
  install   Copy the app to ${INSTALL_DIR}, create a systemd service, enable it, and start it.
  run       Run the guard in the foreground from the current directory without installing a service.
  restart   Restart the installed systemd service.
  status    Show systemd status for the installed service.

Environment overrides:
  APP_NAME, INSTALL_DIR, SERVICE_NAME, ENV_FILE, HTTP_PORT, UDP_PORT,
  GAME_PROTOCOL, TRUSTED_PROXY_HOPS, RUN_USER
USAGE
}

need_root() {
  if [[ "${EUID}" -ne 0 ]]; then
    echo "This action must be run as root. Try: sudo $0 ${ACTION}" >&2
    exit 1
  fi
}

require_node() {
  if ! command -v node >/dev/null 2>&1; then
    echo "Node.js is required but was not found in PATH." >&2
    echo "Install Node.js 20+ first, then re-run this script." >&2
    exit 1
  fi
}

write_env_file() {
  cat > "${ENV_FILE}" <<ENV
HTTP_PORT=${HTTP_PORT}
UDP_PORT=${UDP_PORT}
GAME_PROTOCOL=${GAME_PROTOCOL}
TRUSTED_PROXY_HOPS=${TRUSTED_PROXY_HOPS}
NODE_ENV=production
ENV
  chmod 0640 "${ENV_FILE}"
  chown root:"${RUN_USER}" "${ENV_FILE}" 2>/dev/null || true
}

install_files() {
  mkdir -p "${INSTALL_DIR}"
  rsync -a --delete \
    --exclude='.git' \
    --exclude='node_modules' \
    --exclude='*.log' \
    ./ "${INSTALL_DIR}/"
  chown -R "${RUN_USER}:${RUN_USER}" "${INSTALL_DIR}"
}

create_user() {
  if ! id -u "${RUN_USER}" >/dev/null 2>&1; then
    useradd --system --home-dir "${INSTALL_DIR}" --shell /usr/sbin/nologin "${RUN_USER}"
  fi
}

write_service() {
  local node_path
  node_path="$(command -v node)"
  cat > "/etc/systemd/system/${SERVICE_NAME}.service" <<SERVICE
[Unit]
Description=Node-3 DDoS Guard Layer 4/Layer 7 Protection
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=${RUN_USER}
Group=${RUN_USER}
WorkingDirectory=${INSTALL_DIR}
EnvironmentFile=${ENV_FILE}
ExecStart=${node_path} ${INSTALL_DIR}/src/server.js
Restart=always
RestartSec=2
StartLimitIntervalSec=0
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=full
ProtectHome=true
ReadWritePaths=${INSTALL_DIR}
LimitNOFILE=1048576
KillSignal=SIGTERM
TimeoutStopSec=15

[Install]
WantedBy=multi-user.target
SERVICE
}

install_service() {
  need_root
  require_node
  if ! command -v rsync >/dev/null 2>&1; then
    echo "rsync is required for installation but was not found." >&2
    exit 1
  fi
  create_user
  install_files
  write_env_file
  write_service
  systemctl daemon-reload
  systemctl enable "${SERVICE_NAME}.service"
  systemctl restart "${SERVICE_NAME}.service"
  systemctl --no-pager --full status "${SERVICE_NAME}.service" || true
  echo "Installed and started ${SERVICE_NAME}.service. Edit ${ENV_FILE} then run: sudo systemctl restart ${SERVICE_NAME}"
}

run_foreground() {
  require_node
  export HTTP_PORT UDP_PORT GAME_PROTOCOL TRUSTED_PROXY_HOPS NODE_ENV=production
  exec node src/server.js
}

case "${ACTION}" in
  install) install_service ;;
  run) run_foreground ;;
  restart) need_root; systemctl restart "${SERVICE_NAME}.service" ;;
  status) systemctl --no-pager --full status "${SERVICE_NAME}.service" ;;
  -h|--help|help) usage ;;
  *) usage; exit 2 ;;
esac
