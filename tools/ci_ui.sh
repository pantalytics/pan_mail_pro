#!/usr/bin/env bash
#
# The UI job: boot a seeded Odoo, assert its pages read, keep the screenshots.
#
#   tools/ci_ui.sh                 # assert, screenshots in ui-screenshots/
#   UI_OUT=/tmp/shots tools/ci_ui.sh
#   KEEP_UI=1 tools/ci_ui.sh       # leave the instance up to look at yourself
#
# Docker plus `pip install playwright`. The browser comes from the image in a
# Claude cloud session and from `playwright install chromium` on a runner.
#
# This is the check the test suite cannot be: 456 green tests said nothing
# about a checklist that ran the full width of the window.
set -euo pipefail

REPO=$(cd "$(dirname "$0")/.." && pwd)
OUT=${UI_OUT:-$REPO/ui-screenshots}

cleanup() {
    [ -z "${KEEP_UI:-}" ] && "$REPO/tools/ui_preview.sh" --stop >/dev/null 2>&1
    return 0
}
trap cleanup EXIT

"$REPO/tools/ui_preview.sh"
"$REPO/tools/ui_check.py" --out="$OUT"
echo "Screenshots in $OUT"
