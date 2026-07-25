#!/usr/bin/env bash
set -Eeuo pipefail

# This script is shipped inside zerox-theme.zip beside zerox-theme.blueprint.
# It delegates installation to Blueprint instead of modifying panel files.

readonly SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
readonly PACKAGE="${SCRIPT_DIR}/zerox-theme.blueprint"

if [[ ${1:-} == "--check" ]]; then
    [[ -r "${PACKAGE}" ]] || { echo "Missing ${PACKAGE}" >&2; exit 1; }
    command -v blueprint >/dev/null 2>&1 || { echo "Blueprint CLI was not found in PATH." >&2; exit 1; }
    echo "ZeroX Theme package and Blueprint CLI are available."
    exit 0
fi

if [[ ${EUID} -ne 0 ]]; then
    echo "Run this installer with sudo; Blueprint needs access to the panel files." >&2
    exit 1
fi

if [[ ! -r "${PACKAGE}" ]]; then
    echo "The release is incomplete: zerox-theme.blueprint is missing." >&2
    exit 1
fi

if ! command -v blueprint >/dev/null 2>&1; then
    echo "Blueprint CLI was not found. Install Blueprint before installing ZeroX Theme." >&2
    exit 1
fi

echo "Installing ZeroX Theme through Blueprint (existing addons will not be edited)..."
blueprint -install "${PACKAGE}"
echo "ZeroX Theme installed. Configure it in Admin > Extensions > ZeroX Theme."
