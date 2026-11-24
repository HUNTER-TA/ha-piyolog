#!/usr/bin/env bash
set -euo pipefail

# Generic deployment helper for copying custom_components/piyolog to a Home Assistant host via SSH.
# Usage:
#   HA_SSH_TARGET="root@homeassistant.local" ./scripts/deploy.sh
#   ./scripts/deploy.sh root@homeassistant.local /config

SSH_TARGET="${1:-${HA_SSH_TARGET:-}}"
REMOTE_CONFIG_DIR="${2:-${HA_CONFIG_DIR:-/config}}"

if [[ -z "${SSH_TARGET}" ]]; then
  echo "Usage: $0 <user@host> [remote_config_dir]" >&2
  echo "   or: HA_SSH_TARGET=user@host [HA_CONFIG_DIR=/config] $0" >&2
  exit 1
fi

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SRC_DIR="${REPO_ROOT}/custom_components/piyolog"
REMOTE_DEST="${REMOTE_CONFIG_DIR%/}/custom_components/piyolog"

if [[ ! -d "${SRC_DIR}" ]]; then
  echo "Error: source directory not found: ${SRC_DIR}" >&2
  exit 1
fi

echo "Deploying custom_components/piyolog to ${SSH_TARGET}:${REMOTE_DEST} ..."
ssh "${SSH_TARGET}" "mkdir -p '${REMOTE_DEST}'"
tar -C "${SRC_DIR}" \
  --exclude='__pycache__' \
  --exclude='*.pyc' \
  --exclude='.DS_Store' \
  -cf - . \
| ssh "${SSH_TARGET}" "rm -rf '${REMOTE_DEST}'/* && tar --no-same-owner --no-same-permissions -C '${REMOTE_DEST}' -xf - && chmod 755 '${REMOTE_DEST}' && find '${REMOTE_DEST}' -type d -exec chmod 755 {} + && find '${REMOTE_DEST}' -type f -exec chmod 644 {} +"

echo "Deployment complete. Run 'ha core check' and restart Home Assistant to load changes."
