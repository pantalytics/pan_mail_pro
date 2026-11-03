#!/usr/bin/env bash
#
# The whole of CI, locally: static checks, a fresh install with the suite, and
# the upgrade from the last release. This is what .github/workflows/ci.yml runs
# on a push, in the same order, calling the same scripts.
#
#   tools/ci.sh                 # everything
#   tools/ci.sh lint            # static checks only (seconds)
#   tools/ci.sh test            # fresh install + suite
#   tools/ci.sh module-only     # the same with only pan_mail_pro/ deployed (Odoo.sh)
#   tools/ci.sh upgrade         # upgrade from the last release + suite
#   tools/ci.sh series          # the suite on 18 and 20, the checkout cut for each
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
    module-only) tools/ci_odoo.sh --mode=module-only ;;
    upgrade) tools/ci_odoo.sh --mode=upgrade ;;
    series)
        tools/ci_odoo.sh --mode=fresh --series=18.0
        tools/ci_odoo.sh --mode=fresh --series=20.0
        ;;
    ui)      tools/ci_ui.sh ;;
    all)
        tools/ci_lint.sh
        tools/ci_odoo.sh --mode=fresh
        tools/ci_odoo.sh --mode=module-only
        tools/ci_odoo.sh --mode=upgrade
        tools/ci_odoo.sh --mode=fresh --series=18.0
        tools/ci_odoo.sh --mode=fresh --series=20.0
        tools/ci_ui.sh
        ;;
    *) echo "usage: tools/ci.sh [all|lint|test|module-only|upgrade|series|ui]" >&2; exit 2 ;;
esac
