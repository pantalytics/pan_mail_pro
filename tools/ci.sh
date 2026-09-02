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
#
# Needs Docker and network access to Docker Hub. That is all -- no Enterprise
# source, no Azure credentials, no local Odoo.
set -euo pipefail
cd "$(dirname "$0")/.."

WHAT=${1:-all}
case "$WHAT" in
    lint)    tools/ci_lint.sh ;;
    test)    tools/ci_odoo.sh --mode=fresh ;;
    upgrade) tools/ci_odoo.sh --mode=upgrade ;;
    all)
        tools/ci_lint.sh
        tools/ci_odoo.sh --mode=fresh
        tools/ci_odoo.sh --mode=upgrade
        ;;
    *) echo "usage: tools/ci.sh [all|lint|test|upgrade]" >&2; exit 2 ;;
esac
