#!/usr/bin/env bash
#
# The whole of CI, locally: static checks, a fresh install with the suite, and
# the upgrade from the last release. This is what .github/workflows/ci.yml runs
# on a push, in the same order, calling the same scripts.
#
#   tools/ci.sh                 # everything
#   tools/ci.sh lint            # static checks only (seconds)
#   tools/ci.sh test            # fresh install + suite
#   tools/ci.sh upgrade         # upgrade from the last release + suite
#   tools/ci.sh ui              # boot a seeded Odoo and check the pages read
#
# Needs Docker and network access to Docker Hub. That is all -- no Enterprise
# source, no Azure credentials, no local Odoo. The `ui` job additionally needs
# Playwright (`pip install playwright`), which is why it is last.
set -euo pipefail
cd "$(dirname "$0")/.."

WHAT=${1:-all}
case "$WHAT" in
    lint)    tools/ci_lint.sh ;;
    test)    tools/ci_odoo.sh --mode=fresh ;;
    upgrade) tools/ci_odoo.sh --mode=upgrade ;;
    ui)      tools/ci_ui.sh ;;
    all)
        tools/ci_lint.sh
        tools/ci_odoo.sh --mode=fresh
        tools/ci_odoo.sh --mode=upgrade
        tools/ci_ui.sh
        ;;
    *) echo "usage: tools/ci.sh [all|lint|test|upgrade|ui]" >&2; exit 2 ;;
esac
