#!/usr/bin/env bash
# Offline PR validation. Never invoke the authenticated live-data renderer here.
set -euo pipefail
cd "$(dirname "$0")/.."
python_bin="${LOCAL_CHECKS_PYTHON:-python3.12}"
command -v "$python_bin" >/dev/null || {
  echo 'Python 3.12 is required; set LOCAL_CHECKS_PYTHON to its executable.' >&2
  exit 1
}
"$python_bin" -I -c 'import sys; assert sys.version_info[:2] == (3, 12), "Python 3.12 required"'
command -v actionlint >/dev/null || {
  echo 'actionlint is required for workflow validation.' >&2
  exit 1
}
"$python_bin" -I -W error -m unittest discover -s tests -v
actionlint .github/workflows/refresh.yml
