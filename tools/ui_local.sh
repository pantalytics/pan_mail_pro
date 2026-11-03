#!/usr/bin/env bash
#
# The browser checks without Docker: boot the series clone tools/odoo_local.sh
# keeps, install the module and the dark-mode CI addon, seed, run
# tools/ui_check.py, stop. The same checks as tools/ci_ui.sh, for a cloud
# session and for a series that has no branch yet.
#
#   tools/ui_local.sh                 # the series the manifest names
#   tools/ui_local.sh --series=20.0   # another series: manifests rewritten in a copy
#
# Env: ODOO_LOCAL_DIR, DB_USER, DB_PASS as tools/odoo_local.sh; UI_OUT for the
# screenshots (default: ui-screenshots/ in the repository); KEEP_UI=1 leaves
# the server running on its port.
set -euo pipefail

REPO=$(cd "$(dirname "$0")/.." && pwd)
WORK=${ODOO_LOCAL_DIR:-$HOME/.cache/pan_mail_pro/odoo}
DB_USER=${DB_USER:-odoo}
DB_PASS=${DB_PASS:-odoo}
OUT=${UI_OUT:-$REPO/ui-screenshots}

MANIFEST_SERIES=$(python3 -c "import ast;print('.'.join(ast.literal_eval(open('$REPO/pan_mail_pro/__manifest__.py').read())['version'].split('.')[:2]))")
SERIES=$MANIFEST_SERIES
for arg in "$@"; do
    case "$arg" in
        --series=*) SERIES="${arg#--series=}" ;;
        *) echo "unknown argument: $arg" >&2; exit 2 ;;
    esac
done

"$REPO/tools/odoo_local.sh" --series="$SERIES" --prepare-only
ODOO="$WORK/odoo-$SERIES"
PORT=$((8100 + ${SERIES%%.*}))
DB="pan_ui_${SERIES//./_}"

TREE=$REPO
if [ "$SERIES" != "$MANIFEST_SERIES" ]; then
    TREE=$(mktemp -d)
    git -C "$REPO" ls-files -z --cached --others --exclude-standard | (cd "$REPO" && xargs -0 cp --parents -t "$TREE")
    "$REPO/tools/cut_series.sh" "$SERIES" "$TREE"
    echo "Measuring against Odoo $SERIES on a copy cut for it; not a release verdict."
fi

odoo_cli() {
    python3 "$ODOO/odoo-bin" -d "$DB" --db_user="$DB_USER" --db_password="$DB_PASS" --db_host=127.0.0.1 \
        --data-dir="$WORK/data-ui-$SERIES" \
        --addons-path="$ODOO/addons,$TREE,$TREE/tools/ci_addons" \
        --max-cron-threads=0 --log-level=warn "$@"
}

SERVER=
cleanup() {
    if [ -z "${KEEP_UI:-}" ]; then
        # By name as well as by pid: the server forks its workers.
        [ -n "$SERVER" ] && kill "$SERVER" 2>/dev/null || true
        pkill -f "odoo-bin -d $DB " 2>/dev/null || true
    fi
    [ "$TREE" != "$REPO" ] && [ -z "${KEEP_UI:-}" ] && rm -rf "$TREE"
    return 0
}
trap cleanup EXIT

su postgres -c "dropdb --if-exists $DB" >/dev/null
echo "Installing into $DB"
odoo_cli -i pan_mail_pro,pan_mail_pro_ci_dark,pan_mail_pro_ci_platform --without-demo=all --stop-after-init --no-http

odoo_cli --http-port="$PORT" --dev=xml > "$WORK/ui-$SERIES.log" 2>&1 &
SERVER=$!
echo -n "Waiting for Odoo on :$PORT"
for _ in $(seq 1 60); do
    curl -sf --noproxy '*' -o /dev/null "http://localhost:$PORT/web/login" && break
    echo -n "."; sleep 1
done
echo " ready."

python3 "$REPO/tools/ui_seed.py" "$DB" "http://localhost:$PORT"
mkdir -p "$OUT"
"$REPO/tools/ui_check.py" --url="http://localhost:$PORT" --db="$DB" --out="$OUT"
echo "Screenshots in $OUT"
