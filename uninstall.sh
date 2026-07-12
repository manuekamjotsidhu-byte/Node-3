#!/usr/bin/env bash
set -euo pipefail

APP_NAME="${APP_NAME:-node-3-ddos-guard}"
INSTALL_DIR="${INSTALL_DIR:-/opt/${APP_NAME}}"
SERVICE_NAME="${SERVICE_NAME:-${APP_NAME}}"
ENV_FILE="${ENV_FILE:-/etc/${APP_NAME}.env}"
RUN_USER="${RUN_USER:-${APP_NAME}}"
REMOVE_DATA="${REMOVE_DATA:-yes}"
REMOVE_USER="${REMOVE_USER:-yes}"

if [[ "${EUID}" -ne 0 ]]; then
  echo "This uninstall script must be run as root. Try: sudo $0" >&2
  exit 1
fi

systemctl stop "${SERVICE_NAME}.service" 2>/dev/null || true
systemctl disable "${SERVICE_NAME}.service" 2>/dev/null || true
rm -f "/etc/systemd/system/${SERVICE_NAME}.service"
systemctl daemon-reload
systemctl reset-failed "${SERVICE_NAME}.service" 2>/dev/null || true

if [[ "${REMOVE_DATA}" == "yes" ]]; then
  rm -rf "${INSTALL_DIR}"
  rm -f "${ENV_FILE}"
fi

if [[ "${REMOVE_USER}" == "yes" ]] && id -u "${RUN_USER}" >/dev/null 2>&1; then
  userdel "${RUN_USER}" 2>/dev/null || true
fi

echo "Uninstalled ${SERVICE_NAME}.service. Set REMOVE_DATA=no or REMOVE_USER=no to preserve files/user on future runs."
