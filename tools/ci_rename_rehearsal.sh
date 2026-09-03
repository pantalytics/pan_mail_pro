#!/usr/bin/env bash
#
# Rehearse the upgrade path a pre-rename customer database actually takes:
#
#   install pan_outlook_pro at an old tag
#     -> tools/rename_to_mail_pro.sql  (outside Odoo, registry stopped)
#       -> -u pan_mail_pro at HEAD, running every migration in between
#
# `tools/ci_odoo.sh --mode=upgrade` cannot cover this. It hops from the newest
# previous tag to HEAD, one version, module already renamed. A customer sitting
# on 19.0.1.x crosses eight migration folders *and* a rename that happens in
# SQL before Odoo loads. That is the path nothing tested until this script.
#
#   tools/ci_rename_rehearsal.sh
#   BASE_TAG=v19.0.3.0.0 tools/ci_rename_rehearsal.sh
#
# What it proves: the chain runs end to end and the suite passes on the result.
# What it does NOT prove: that the data-moving migrations move real data. The
# baseline here is a fresh install, so every backfill logs "0 rows". Rehearse
# against a restored customer backup before a real rollout.
set -euo pipefail

REPO=$(cd "$(dirname "$0")/.." && pwd)
BASE_TAG=${BASE_TAG:-v19.0.2.0.1}
LOG_DIR=${LOG_DIR:-$REPO}
NET=pan_reh_net
DB=pan_reh_db
DBNAME=rehearse

SERIES=$(python3 - "$REPO" <<'PY'
import ast, sys
manifest = ast.literal_eval(open(f"{sys.argv[1]}/__manifest__.py").read())
print(".".join(manifest['version'].split('.')[:2]))
PY
)

OLD_ADDONS=""
cleanup() {
    if [ -z "${KEEP_DB:-}" ]; then
        docker rm -f "$DB" >/dev/null 2>&1 || true
        docker network rm "$NET" >/dev/null 2>&1 || true
    fi
    [ -n "$OLD_ADDONS" ] && rm -rf "$OLD_ADDONS"
    return 0
}
trap cleanup EXIT

docker rm -f "$DB" >/dev/null 2>&1 || true
docker network create "$NET" >/dev/null 2>&1 || true
docker run -d --name "$DB" --network "$NET" \
    -e POSTGRES_USER=odoo -e POSTGRES_PASSWORD=odoo -e POSTGRES_DB=postgres \
    postgres:15 >/dev/null
echo -n "Waiting for Postgres"
for _ in $(seq 1 60); do
    docker exec "$DB" pg_isready -U odoo >/dev/null 2>&1 && { echo " ready."; break; }
    echo -n "."
    sleep 1
done
docker exec "$DB" pg_isready -U odoo >/dev/null

odoo_run() {
    # $1 = module directory on the host, $2 = the name to mount it under
    local dir=$1 name=$2
    shift 2
    docker run --rm --network "$NET" \
        -v "${dir}:/mnt/extra-addons/${name}:ro" \
        --entrypoint odoo "odoo:${SERIES}" -d "$DBNAME" \
        --db_host="$DB" --db_port=5432 --db_user=odoo --db_password=odoo \
        --addons-path=/usr/lib/python3/dist-packages/odoo/addons,/mnt/extra-addons \
        --stop-after-init --max-cron-threads=0 "$@"
}

# The old module has to be mounted under its old name: Odoo takes a module's
# name from its directory, and the whole point of the rename runbook is that
# `pan_outlook_pro` is what is in ir_module_module before it runs.
OLD_ADDONS=$(mktemp -d)
git -C "$REPO" archive "$BASE_TAG" | tar -x -C "$OLD_ADDONS"
chmod -R a+rX "$OLD_ADDONS"
OLD_NAME=$(python3 - "$OLD_ADDONS" <<'PY'
import ast, sys
manifest = ast.literal_eval(open(f"{sys.argv[1]}/__manifest__.py").read())
# "Outlook Pro - ..." was the module before 19.0.4.0.0 renamed it.
print('pan_outlook_pro' if manifest['name'].startswith('Outlook Pro') else 'pan_mail_pro')
PY
)
echo "=== Baseline: ${BASE_TAG} installed as ${OLD_NAME}"
grep "'version'" "$OLD_ADDONS/__manifest__.py"
odoo_run "$OLD_ADDONS" "$OLD_NAME" \
    -i "${OLD_NAME},sale,mass_mailing" --without-demo=all --log-level=warn

if [ "$OLD_NAME" = "pan_outlook_pro" ]; then
    echo "=== Rename, in SQL, with Odoo stopped"
    docker run --rm --network "$NET" -v "$REPO/tools:/sql:ro" postgres:15 \
        psql "postgresql://odoo:odoo@${DB}:5432/${DBNAME}" \
        -v ON_ERROR_STOP=1 -f /sql/rename_to_mail_pro.sql
fi

echo "=== Upgrade to HEAD, running every migration in between"
LOG="$LOG_DIR/odoo-rehearsal.log"
set -o pipefail
odoo_run "$REPO" pan_mail_pro \
    -u pan_mail_pro --test-enable --test-tags=pan_mail_pro --log-level=info 2>&1 \
    | tee "$LOG"

echo "=== Migrations that ran"
grep -E "odoo\.modules\.migration: module pan_mail_pro" "$LOG" || {
    echo "::error::No migration ran. A rehearsal that skips the migrations proves nothing."
    exit 1
}

"$REPO/tools/ci_assert_tests.sh" "$LOG"
