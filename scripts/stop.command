#!/bin/zsh
set -euo pipefail
cd "${0:A:h}/.."
exec .venv/bin/python src/manage.py stop
