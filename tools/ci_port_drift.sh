#!/usr/bin/env bash
#
# A series branch is main with tools/cut_series.sh applied and nothing else
# (docs/plans/multi-version.md §2). This takes the main the branch was ported
# from, cuts it for the branch's series, and fails on any difference.
#
#   tools/ci_port_drift.sh                 # against origin/main
#   tools/ci_port_drift.sh <ref>           # against another ref
#
# On a checkout whose manifest names main's own series there is nothing to
# compare, and the script says so and passes.
set -euo pipefail

REPO=$(cd "$(dirname "$0")/.." && pwd)
BASE=${1:-origin/main}
series_of() { python3 -c "import ast,sys;print('.'.join(ast.literal_eval(sys.stdin.read())['version'].split('.')[:2]))"; }
SERIES=$(series_of < "$REPO/pan_mail_pro/__manifest__.py")
BASE_SERIES=$(git -C "$REPO" show "$BASE:pan_mail_pro/__manifest__.py" | series_of)
if [ "$SERIES" = "$BASE_SERIES" ]; then
    echo "Manifest names $SERIES, the same series as $BASE: not a series branch, nothing to compare."
    exit 0
fi

WORK=$(mktemp -d)
trap 'rm -rf "$WORK"' EXIT
mkdir -p "$WORK/base" "$WORK/head"
git -C "$REPO" archive "$BASE" | tar -x -C "$WORK/base"
git -C "$REPO" archive HEAD | tar -x -C "$WORK/head"
"$WORK/base/tools/cut_series.sh" "$SERIES" "$WORK/base" >/dev/null

# The version's last three numbers may differ (the branch bumps on its own);
# compare the manifests with the version line taken out.
for side in base head; do
    sed -i "/'version': '/d" "$WORK/$side/pan_mail_pro/__manifest__.py" "$WORK/$side/tools/ci_addons/pan_mail_pro_ci_dark/__manifest__.py"
done
if diff -r "$WORK/base" "$WORK/head" > "$WORK/diff.txt"; then
    echo "OK: this $SERIES branch is $BASE cut for $SERIES, byte for byte."
else
    echo "::error::This $SERIES branch differs from $BASE cut for $SERIES. Either port the change to main or add it to tools/series/$SERIES."
    head -40 "$WORK/diff.txt"
    exit 1
fi
