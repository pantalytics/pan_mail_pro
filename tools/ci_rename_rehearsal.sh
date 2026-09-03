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
#   BASE_DUMP=/path/to/backup tools/ci_rename_rehearsal.sh
#
# What the tag mode proves: the chain runs end to end and the suite passes on
# the result. What it does NOT prove: that the data-moving migrations move real
# data. The baseline is a fresh install, so every backfill logs "0 rows".
#
# BASE_DUMP is the missing half: instead of installing a tag it restores a
# database backup (pg_dump custom format, plain SQL, gzipped SQL, or an Odoo
# zip backup) into the throwaway database and takes the same path from there —
# rename SQL, then one `-u` to HEAD. That is the rehearsal to run against a
# restored customer backup before a real rollout: the log then carries real
# row counts and real timings.
#
# The restored copy is neutralized first (`database.is_neutralized`, mail
# servers and crons off), so nothing in the throwaway database can ever mail a
# customer. The migrations move ciphertext without decrypting it, so
# neutralization changes nothing about what is being measured. The test suite
# does not run in dump mode — it expects a non-neutralized database and is
# already proven in tag mode; the migration assert below still applies.
set -euo pipefail

REPO=$(cd "$(dirname "$0")/.." && pwd)
BASE_TAG=${BASE_TAG:-v19.0.2.0.1}
BASE_DUMP=${BASE_DUMP:-}
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

pg() {
    docker run --rm -i --network "$NET" postgres:15 \
        psql "postgresql://odoo:odoo@${DB}:5432/${DBNAME}" -v ON_ERROR_STOP=1 "$@"
}

if [ -n "$BASE_DUMP" ]; then
    [ -f "$BASE_DUMP" ] || { echo "::error::BASE_DUMP not found: $BASE_DUMP"; exit 1; }
    echo "=== Baseline: restoring $(basename "$BASE_DUMP")"
    docker exec "$DB" createdb -U odoo "$DBNAME"
    case "$(file -b "$BASE_DUMP")" in
        PostgreSQL\ custom\ database\ dump*)
            docker run --rm -i --network "$NET" postgres:15 \
                pg_restore --no-owner --no-privileges \
                -d "postgresql://odoo:odoo@${DB}:5432/${DBNAME}" < "$BASE_DUMP" ;;
        Zip\ archive*)  # an Odoo backup: dump.sql next to the filestore
            unzip -p "$BASE_DUMP" dump.sql | pg -q ;;
        gzip\ compressed*)
            gunzip -c "$BASE_DUMP" | pg -q ;;
        *)
            pg -q < "$BASE_DUMP" ;;
    esac

    # A restored dump still carries live OAuth refresh tokens and mailbox
    # passwords. `database.is_neutralized` is the module's own hard gate
    # (models/neutralization.py); the rest matches what base neutralization
    # does. The filestore is not restored — no migration reads it.
    echo "=== Neutralizing the copy: it must never be able to mail a customer"
    pg <<'SQL'
UPDATE ir_mail_server SET active = false;
UPDATE ir_cron SET active = false;
DO $$ BEGIN
    IF to_regclass('fetchmail_server') IS NOT NULL THEN
        EXECUTE 'UPDATE fetchmail_server SET active = false';
    END IF;
END $$;
INSERT INTO ir_config_parameter (key, value)
VALUES ('database.is_neutralized', 'true')
ON CONFLICT (key) DO UPDATE SET value = 'true';
SQL

    OLD_NAME=$(pg -tA -c "SELECT name FROM ir_module_module
                           WHERE name IN ('pan_outlook_pro', 'pan_mail_pro')
                             AND state = 'installed'")
    case "$OLD_NAME" in
        pan_outlook_pro|pan_mail_pro) ;;
        *) echo "::error::The dump has neither pan_outlook_pro nor pan_mail_pro installed."
           exit 1 ;;
    esac
    pg -c "SELECT name, state, latest_version FROM ir_module_module WHERE name = '$OLD_NAME'"
else
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
fi

if [ "$OLD_NAME" = "pan_outlook_pro" ]; then
    echo "=== Rename, in SQL, with Odoo stopped"
    docker run --rm --network "$NET" -v "$REPO/tools:/sql:ro" postgres:15 \
        psql "postgresql://odoo:odoo@${DB}:5432/${DBNAME}" \
        -v ON_ERROR_STOP=1 -f /sql/rename_to_mail_pro.sql
fi

echo "=== Upgrade to HEAD, running every migration in between"
LOG="$LOG_DIR/odoo-rehearsal.log"
set -o pipefail
TEST_ARGS="--test-enable --test-tags=pan_mail_pro"
[ -n "$BASE_DUMP" ] && TEST_ARGS=""
odoo_run "$REPO" pan_mail_pro \
    -u pan_mail_pro $TEST_ARGS --log-level=info 2>&1 \
    | tee "$LOG"

echo "=== Migrations that ran"
grep -E "odoo\.modules\.migration: module pan_mail_pro" "$LOG" || {
    echo "::error::No migration ran. A rehearsal that skips the migrations proves nothing."
    exit 1
}

echo "=== What the data-moving migrations logged"
grep -E "\[(Mail Pro|Migration)\]" "$LOG" || true

if [ -n "$BASE_DUMP" ]; then
    echo "=== Configuration parameters after the upgrade (secrets masked)"
    pg -c "SELECT key,
                  CASE WHEN key LIKE '%encrypt%'
                       THEN '<masked, ' || length(value) || ' chars>'
                       ELSE left(value, 120) END AS value
             FROM ir_config_parameter
            WHERE key LIKE 'x_pan_outlook_pro.%' OR key LIKE 'pan_mail_pro.%'
            ORDER BY key"
else
    "$REPO/tools/ci_assert_tests.sh" "$LOG"
fi
