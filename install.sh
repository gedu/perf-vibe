#!/usr/bin/env bash
# perfvibe installer — installs the `perfvibe` CLI globally and isolated via pipx.
#
#   curl -fsSL https://raw.githubusercontent.com/gedu/perf-vibe/main/install.sh | bash
#
# perfvibe is a Python CLI, so (unlike a Go single-binary tool) it needs a
# Python 3.11+ interpreter present. This script uses pipx — the standard way to
# install a Python CLI onto your PATH in its own isolated environment, without
# touching your system/global Python. No PyPI publish required: it installs
# straight from the Git repository.
set -euo pipefail

REPO_URL="${PERFVIBE_REPO:-https://github.com/gedu/perf-vibe.git}"
REF="${PERFVIBE_REF:-main}"

info() { printf '\033[36m==>\033[0m %s\n' "$1"; }
err()  { printf '\033[31merror:\033[0m %s\n' "$1" >&2; }

# --- Python 3.11+ ---
PY=""
for candidate in python3.13 python3.12 python3.11 python3; do
  if command -v "$candidate" >/dev/null 2>&1; then
    if "$candidate" -c 'import sys; raise SystemExit(0 if sys.version_info[:2] >= (3, 11) else 1)'; then
      PY="$candidate"; break
    fi
  fi
done
if [ -z "$PY" ]; then
  err "Python 3.11+ is required and was not found on PATH."
  err "Install it (e.g. 'brew install python@3.12') and re-run."
  exit 1
fi
info "Using $($PY --version) at $(command -v "$PY")"

# --- pipx (install if missing) ---
if ! command -v pipx >/dev/null 2>&1; then
  info "pipx not found — installing it with '$PY -m pip install --user pipx'"
  "$PY" -m pip install --user pipx
  "$PY" -m pipx ensurepath
  PIPX="$PY -m pipx"
else
  PIPX="pipx"
fi

# --- install perfvibe from the repo ---
info "Installing perfvibe from ${REPO_URL}@${REF}"
# shellcheck disable=SC2086
$PIPX install --force "git+${REPO_URL}@${REF}"

info "Done. Try:  perfvibe --help"
info "If 'perfvibe' is not found, open a new terminal (pipx added ~/.local/bin to PATH) or run 'pipx ensurepath'."
