#!/usr/bin/env bash
#
# Turn a checkout of main into the tree a series branch carries
# (docs/plans/multi-version.md): the manifests' series rewritten, the files
# in tools/series/<series>/ laid over the tree, the files its REMOVE lists
# deleted, its MANIFEST.sed applied to the module manifest.
#
#   tools/cut_series.sh 20.0            # in place, on this checkout (cutting the branch)
#   tools/cut_series.sh 20.0 <dir>      # on a copy (tools/odoo_local.sh, tools/ui_local.sh)
#
# A series branch is exactly main with this applied, which is what
# tools/ci_port_drift.sh checks.
set -euo pipefail

REPO=$(cd "$(dirname "$0")/.." && pwd)
SERIES=${1:?usage: cut_series.sh <series> [<dir>]}
TREE=${2:-$REPO}
OVERLAY="$REPO/tools/series/$SERIES"
[ -d "$OVERLAY" ] || { echo "no tools/series/$SERIES: not a series this repository knows" >&2; exit 2; }

FROM=$(python3 -c "import ast;print('.'.join(ast.literal_eval(open('$TREE/pan_mail_pro/__manifest__.py').read())['version'].split('.')[:2]))")
for manifest in "$TREE/pan_mail_pro/__manifest__.py" "$TREE/tools/ci_addons/pan_mail_pro_ci_dark/__manifest__.py"; do
    sed -i "s/'version': '${FROM}\./'version': '${SERIES}./" "$manifest"
done
if [ -f "$OVERLAY/REMOVE" ]; then
    while read -r path; do
        [ -n "$path" ] && rm -f "$TREE/$path"
    done < "$OVERLAY/REMOVE"
fi
find "$OVERLAY" -type f ! -name REMOVE ! -name MANIFEST.sed ! -name README.md | while read -r file; do
    rel=${file#"$OVERLAY"/}
    mkdir -p "$TREE/$(dirname "$rel")"
    cp "$file" "$TREE/$rel"
done
if [ -f "$OVERLAY/MANIFEST.sed" ]; then
    sed -i -f "$OVERLAY/MANIFEST.sed" "$TREE/pan_mail_pro/__manifest__.py"
fi
echo "Cut for Odoo $SERIES in $TREE"
