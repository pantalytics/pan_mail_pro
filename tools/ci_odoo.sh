#!/usr/bin/env bash
#
# Install this module into a real Odoo and run its suite. The whole thing --
# Postgres included -- runs in Docker, so it behaves identically on a GitHub
# runner, on a laptop and in a Claude cloud session.
#
#   tools/ci_odoo.sh                # fresh install + tests
#   tools/ci_odoo.sh --mode=upgrade # install the last release, upgrade, test
#
# Env:
#   KEEP_DB=1      leave the Postgres container running afterwards
#   LOG_DIR=path   where to write the Odoo logs (default: repo root)
#
# .github/workflows/ci.yml calls this file instead of inlining docker commands,
# so what CI runs and what you can run yourself cannot drift apart.
set -euo pipefail

REPO=$(cd "$(dirname "$0")/.." && pwd)
MODE=fresh
for arg in "$@"; do
    case "$arg" in
        --mode=*) MODE="${arg#--mode=}" ;;
        *) echo "unknown argument: $arg" >&2; exit 2 ;;
    esac
done

LOG_DIR=${LOG_DIR:-$REPO}
mkdir -p "$LOG_DIR"

SERIES=$(python3 - "$REPO" <<'PY'
import ast, sys
manifest = ast.literal_eval(open(f"{sys.argv[1]}/__manifest__.py").read())
print(".".join(manifest['version'].split('.')[:2]))
PY
)
echo "Testing against Odoo ${SERIES} (mode: ${MODE})"

NET=pan_ci_net
DB=pan_ci_db

cleanup() {
    if [ -z "${KEEP_DB:-}" ]; then
        docker rm -f "$DB" >/dev/null 2>&1 || true
        docker network rm "$NET" >/dev/null 2>&1 || true
    fi
}
trap cleanup EXIT

# ---------------------------------------------------------------------------
# Postgres. A container rather than a GitHub service so the same script works
# where there are no service containers.
# ---------------------------------------------------------------------------
docker rm -f "$DB" >/dev/null 2>&1 || true
docker network create "$NET" >/dev/null 2>&1 || true
docker run -d --name "$DB" --network "$NET" \
    -e POSTGRES_USER=odoo -e POSTGRES_PASSWORD=odoo -e POSTGRES_DB=postgres \
    postgres:15 >/dev/null

echo -n "Waiting for Postgres"
for _ in $(seq 1 60); do
    if docker exec "$DB" pg_isready -U odoo >/dev/null 2>&1; then
        echo " ready."
        break
    fi
    echo -n "."
    sleep 1
done
docker exec "$DB" pg_isready -U odoo >/dev/null

odoo_run() {
    # $1 = the module directory on the host, $2 = database, rest = odoo arguments
    local module=$1 db=$2
    shift 2
    # Mounted at its own name under the addons path. A host symlink inside a
    # bind mount would not resolve in the container, so the module directory is
    # mounted directly rather than a parent full of links.
    docker run --rm --network "$NET" \
        -v "${module}:/mnt/extra-addons/pan_mail_pro:ro" \
        --entrypoint odoo "odoo:${SERIES}" \
        -d "$db" \
        --db_host="$DB" --db_port=5432 --db_user=odoo --db_password=odoo \
        --addons-path=/usr/lib/python3/dist-packages/odoo/addons,/mnt/extra-addons \
        --stop-after-init --max-cron-threads=0 "$@"
}

BASE_ADDONS=""
cleanup_all() {
    cleanup
    [ -n "$BASE_ADDONS" ] && rm -rf "$BASE_ADDONS"
    return 0
}
trap cleanup_all EXIT

# `sale` and `mass_mailing` are not dependencies, but nine tests skip themselves
# without them -- and a skip is invisible in the summary, so those paths were
# passing by never running. `helpdesk` is Enterprise-only and stays a real gap,
# covered by TESTPLAN.md.
MODULES=pan_mail_pro,sale,mass_mailing

if [ "$MODE" = "fresh" ]; then
    LOG="$LOG_DIR/odoo-test.log"
    set -o pipefail
    # --log-handler=odoo.tools.convert:DEBUG turns "Invalid view <name>
    # definition" with an empty context into a real traceback.
    odoo_run "$REPO" ci_test \
        -i "$MODULES" --test-enable --test-tags=pan_mail_pro \
        --without-demo=all --log-level=info \
        --log-handler=odoo.tools.convert:DEBUG 2>&1 | tee "$LOG"

    "$REPO/tools/ci_assert_tests.sh" "$LOG"

    # Pins the intent: if a dependency bump stops sale or mass_mailing from
    # installing, their tests would quietly go back to skipping and stay green.
    MISSING=$(docker run --rm --network "$NET" postgres:15 \
        psql "postgresql://odoo:odoo@${DB}:5432/ci_test" -tAc \
        "SELECT name FROM ir_module_module
          WHERE name IN ('sale','mass_mailing','crm') AND state <> 'installed'")
    if [ -n "$MISSING" ]; then
        echo "::error::Not installed, so their tests skipped instead of running: $(echo $MISSING | tr '\n' ' ')"
        exit 1
    fi
    echo "sale, mass_mailing and crm are installed — no silent skips from missing modules."
    exit 0
fi

if [ "$MODE" != "upgrade" ]; then
    echo "unknown mode: $MODE (expected fresh or upgrade)" >&2
    exit 2
fi

# ---------------------------------------------------------------------------
# Upgrade: every customer database takes this path, not the fresh one. It runs
# the scripts in migrations/ and runs the suite against pre-existing rows.
# ---------------------------------------------------------------------------
HEAD_SHA=$(git -C "$REPO" rev-parse HEAD)
TAG=""
for t in $(git -C "$REPO" tag -l "v${SERIES}.*" --sort=-v:refname); do
    if [ "$(git -C "$REPO" rev-parse "${t}^{commit}")" != "$HEAD_SHA" ]; then
        TAG=$t
        break
    fi
done
if [ -z "$TAG" ]; then
    echo "No previous v${SERIES}.* tag — nothing to upgrade from."
    exit 0
fi
echo "Upgrading from ${TAG}"

BASE_ADDONS=$(mktemp -d)
git -C "$REPO" archive "$TAG" | tar -x -C "$BASE_ADDONS"
# mktemp gives 0700. The Odoo image runs as the unprivileged `odoo` user, which
# then cannot read the mount and reports the module as an invalid addons
# directory -- a confusing way to say "permission denied".
chmod -R a+rX "$BASE_ADDONS"
grep "'version'" "$BASE_ADDONS/__manifest__.py"

set -o pipefail
odoo_run "$BASE_ADDONS" ci_upgrade \
    -i "$MODULES" --without-demo=all --log-level=warn 2>&1 \
    | tee "$LOG_DIR/odoo-baseline.log"

# -u is what triggers the migration scripts; --test-enable then runs the suite
# against the upgraded database rather than a fresh one.
odoo_run "$REPO" ci_upgrade \
    -u pan_mail_pro --test-enable --test-tags=pan_mail_pro --log-level=info 2>&1 \
    | tee "$LOG_DIR/odoo-upgrade.log"

# -u on a module that is not installed silently does nothing and exits 0, which
# is the exact failure this job would otherwise miss.
"$REPO/tools/ci_assert_tests.sh" "$LOG_DIR/odoo-upgrade.log"
