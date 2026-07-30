#!/usr/bin/env bash
#
# Assert that an Odoo test run actually ran tests, and that none of them failed.
#
#   tools/ci_assert_tests.sh <odoo-log>
#
# Shared by the fresh-install job and the upgrade job in ci.yml, so both hold
# the same bar. The Odoo exit code already fails the step on a crash; this adds
# the two things an exit code does not tell you.
set -euo pipefail

LOG=${1:?usage: ci_assert_tests.sh <odoo-log>}

# Anchored on `odoo.tests.result` on purpose. A looser match picks up ordinary
# log lines from tests that deliberately exercise failure paths ("Email 41
# failed to send") and turns a green build red.
SUMMARY=$(grep -oE 'odoo\.tests\.result: [0-9]+ failed, [0-9]+ error\(s\)[^"]*' "$LOG" | tail -1 || true)

if [ -z "$SUMMARY" ]; then
    echo "::error::Odoo printed no test summary — the run died before finishing."
    tail -30 "$LOG"
    exit 1
fi
echo "$SUMMARY"

FAILED=$(echo "$SUMMARY" | sed -nE 's/.*result: ([0-9]+) failed.*/\1/p')
ERRORS=$(echo "$SUMMARY" | sed -nE 's/.*failed, ([0-9]+) error\(s\).*/\1/p')

if [ "$FAILED" -ne 0 ] || [ "$ERRORS" -ne 0 ]; then
    echo "::error::$SUMMARY"
    grep -E 'tests\.[a-z_]+: (ERROR|FAIL):' "$LOG" | head -30 || true
    exit 1
fi

# A suite that ran zero tests also reports "0 failed, 0 error(s)", so the check
# above passes and the build goes green having verified nothing. That is not
# hypothetical: it is what happens when `-u` is used on a module that is not
# installed, when a rename leaves the addons path pointing at the old name, or
# when tests/__init__.py stops importing a file. CLAUDE.md warns about it
# precisely because it has happened.
TOTAL=$(echo "$SUMMARY" | sed -nE 's/.*of ([0-9]+) tests.*/\1/p')
if [ -z "$TOTAL" ]; then
    # Fallback: the stats line carries the count too.
    TOTAL=$(grep -oE 'odoo\.tests\.stats: [^ ]+ ([0-9]+) tests' "$LOG" | tail -1 \
        | grep -oE '[0-9]+ tests' | grep -oE '[0-9]+' || true)
fi

if [ -z "$TOTAL" ]; then
    echo "::error::Could not determine how many tests ran. Odoo's summary format" \
         "changed — update tools/ci_assert_tests.sh rather than dropping the check."
    exit 1
fi

if [ "$TOTAL" -lt 1 ]; then
    echo "::error::Odoo ran 0 tests. The module loaded but its suite never executed."
    exit 1
fi

# Skips are not failures, but they are invisible in the summary and this repo
# has real ones (Enterprise-only paths). Printing them keeps "green" honest.
SKIPPED=$(grep -icE 'odoo\.tests[^:]*: .*\bskip' "$LOG" || true)
echo "Ran ${TOTAL} test(s), ${SKIPPED} skipped, 0 failed, 0 errors."
if [ "${SKIPPED:-0}" -gt 0 ]; then
    echo "Skipped tests:"
    grep -iE 'odoo\.tests[^:]*: .*\bskip' "$LOG" | sed 's/^/  /' | head -20
fi

grep -E 'odoo\.tests\.stats' "$LOG" | tail -1 || true
