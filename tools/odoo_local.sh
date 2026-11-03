#!/usr/bin/env bash
#
# Run the suite against a real Odoo without Docker: clone odoo/odoo at a
# series, install its Python requirements, use the machine's own PostgreSQL,
# install the module and run the tests. The same verdict as tools/ci_odoo.sh
# for the cases where there is no Docker, which is every Claude cloud session.
# tools/ci_odoo.sh stays what CI runs; this is for measuring.
#
#   tools/odoo_local.sh                 # the series the manifest names
#   tools/odoo_local.sh --series=18.0   # another series, see below
#   tools/odoo_local.sh -- -u pan_mail_pro --test-tags=.test_drafts
#
# --series= other than the manifest's copies the tree to a temporary folder
# and rewrites the manifest's series there, because the loader refuses a
# module whose version names another series ("incompatible version, setting
# installable=False"). That copy exists to find out what a port is up against;
# a green run on it is a measurement, not a release.
#
# Env:
#   ODOO_LOCAL_DIR   where the clones live (default: ~/.cache/pan_mail_pro/odoo)
#   DB_USER/DB_PASS  the PostgreSQL role (default: odoo/odoo, created if absent)
#   LOG_DIR          where the Odoo log goes (default: the repository root)
set -euo pipefail

REPO=$(cd "$(dirname "$0")/.." && pwd)
WORK=${ODOO_LOCAL_DIR:-$HOME/.cache/pan_mail_pro/odoo}
DB_USER=${DB_USER:-odoo}
DB_PASS=${DB_PASS:-odoo}
LOG_DIR=${LOG_DIR:-$REPO}

MANIFEST_SERIES=$(python3 - "$REPO" <<'PY'
import ast, sys
manifest = ast.literal_eval(open(f"{sys.argv[1]}/pan_mail_pro/__manifest__.py").read())
print(".".join(manifest['version'].split('.')[:2]))
PY
)
SERIES=$MANIFEST_SERIES
EXTRA=()
while [ $# -gt 0 ]; do
    case "$1" in
        --series=*) SERIES="${1#--series=}"; shift ;;
        --) shift; EXTRA=("$@"); break ;;
        *) echo "unknown argument: $1" >&2; exit 2 ;;
    esac
done

mkdir -p "$WORK" "$LOG_DIR"
ODOO="$WORK/odoo-$SERIES"

# ---------------------------------------------------------------------------
# PostgreSQL: the cluster the image ships, started if it is not running, and
# a superuser role for Odoo (it creates and drops the test database itself).
# ---------------------------------------------------------------------------
if command -v pg_lsclusters >/dev/null 2>&1; then
    if ! pg_lsclusters --no-header | grep -q online; then
        PG_VERSION=$(ls /usr/lib/postgresql | sort -V | tail -1)
        pg_lsclusters --no-header | grep -q "^$PG_VERSION *main" || pg_createcluster "$PG_VERSION" main >/dev/null
        pg_ctlcluster "$PG_VERSION" main start
    fi
fi
if ! su postgres -c "psql -tAc \"SELECT 1 FROM pg_roles WHERE rolname='$DB_USER'\"" 2>/dev/null | grep -q 1; then
    su postgres -c "psql -qc \"CREATE ROLE $DB_USER SUPERUSER LOGIN PASSWORD '$DB_PASS'\""
fi

# ---------------------------------------------------------------------------
# Odoo: a shallow clone per series, and its requirements. Three packages are
# skipped on purpose: rl-renderPM and pypiwin32 do not build on a current
# Python, python-ldap needs system headers, and none of the three is needed
# to load mail. num2words goes in without its pins. psycopg2 goes in as the
# binary wheel, which needs no libpq headers.
# ---------------------------------------------------------------------------
if [ ! -d "$ODOO" ]; then
    echo "Cloning odoo/odoo $SERIES into $ODOO"
    git clone --quiet --depth 1 --branch "$SERIES" https://github.com/odoo/odoo "$ODOO"
fi
if [ ! -f "$ODOO/.requirements-installed" ]; then
    echo "Installing the requirements of Odoo $SERIES"
    grep -v '^\s*#' "$ODOO/requirements.txt" | sed 's/[;=<>].*//;s/ *$//' | grep -v '^\s*$' | sort -u \
        | grep -v -i -E '^(rl-renderPM|pypiwin32|python-ldap|num2words|psycopg2)$' \
        | while read -r pkg; do
            pip install --quiet --break-system-packages "$pkg" >/dev/null 2>&1 || echo "  could not install $pkg (continuing)"
        done
    pip install --quiet --break-system-packages psycopg2-binary decorator markdown2 >/dev/null
    pip install --quiet --break-system-packages --no-deps num2words >/dev/null
    touch "$ODOO/.requirements-installed"
fi

# ---------------------------------------------------------------------------
# The tree. The repository itself is the addons path entry, as on Cloudpepper
# and Odoo.sh; for another series a copy with the manifest's series rewritten.
# ---------------------------------------------------------------------------
TREE=$REPO
if [ "$SERIES" != "$MANIFEST_SERIES" ]; then
    TREE=$(mktemp -d)
    trap 'rm -rf "$TREE"' EXIT
    # The whole checkout, not only the module: tests read ARCHITECTURE.md and
    # tools/rename_to_mail_pro.sql relative to the module folder.
    git -C "$REPO" ls-files -z --cached --others --exclude-standard | (cd "$REPO" && xargs -0 cp --parents -t "$TREE")
    sed -i "s/'version': '${MANIFEST_SERIES}\./'version': '${SERIES}./" "$TREE/pan_mail_pro/__manifest__.py"
    echo "Measuring against Odoo $SERIES with the manifest rewritten from $MANIFEST_SERIES; not a release verdict."
fi

DB="pan_local_${SERIES//./_}"
LOG="$LOG_DIR/odoo-local-$SERIES.log"
su postgres -c "dropdb --if-exists $DB" >/dev/null

# --test-enable starts the HTTP server whatever --no-http says (HttpCase
# needs it), so each series gets its own port and two can run side by side.
echo "Odoo $SERIES, database $DB, log $LOG"
set +e
python3 "$ODOO/odoo-bin" -d "$DB" \
    --db_user="$DB_USER" --db_password="$DB_PASS" --db_host=127.0.0.1 \
    --data-dir="$WORK/data-$SERIES" \
    --addons-path="$ODOO/addons,$TREE" \
    -i pan_mail_pro,sale,mass_mailing --without-demo=all --test-enable --test-tags=pan_mail_pro \
    --stop-after-init --http-port=$((8000 + ${SERIES%%.*})) \
    --log-level=warn --log-handler=odoo.tests:INFO \
    "${EXTRA[@]}" > "$LOG" 2>&1
STATUS=$?
set -e
if [ $STATUS -ne 0 ]; then
    echo "Odoo exited with $STATUS; the last lines of the log:"
    grep -v "PyPDF2\|warnings.warn\|^$\|markdown2\|^  File\|^    " "$LOG" | tail -15
    exit $STATUS
fi
"$REPO/tools/ci_assert_tests.sh" "$LOG"
