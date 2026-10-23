#!/bin/zsh
# One-time setup on an Apple silicon Mac: Python environment, local speech models, runtime folders.
set -euo pipefail
cd "${0:A:h}/.."

if [[ "$(uname -s)" != Darwin || "$(uname -m)" != arm64 ]]; then
  echo "Jax StopWatch needs macOS on Apple silicon (speech recognition runs on MLX)." >&2
  exit 1
fi
command -v uv >/dev/null || { echo "Install uv first: https://docs.astral.sh/uv/" >&2; exit 1; }
command -v codex >/dev/null || echo "Note: the Codex CLI is not on PATH; the Codex assistant stays offline until it is installed and logged in."

uv sync --locked --python 3.12
.venv/bin/python scripts/download_models.py
mkdir -p runtime workspace
chmod 700 runtime
echo
echo "Setup done. Start the bridge with: scripts/start.command"
echo "Then open http://127.0.0.1:18765/ and flash the firmware (docs/setup.md)."
