#!/usr/bin/env bash
#
# A running Odoo with this module installed and enough rows in it to look at.
# The point is the settings page and the mailbox screens: a view renders or it
# does not, and the test suite cannot see a checklist that runs the full width
# of a 2000px window.
#
#   tools/ui_preview.sh          # boot (or rebuild) http://localhost:8069
#   tools/ui_preview.sh --update # re-apply views/assets after an edit
#   tools/ui_preview.sh --stop   # throw it away
#
# Login: admin / admin. The seed data is fake and the database is a throwaway;
# nothing here is a substitute for tools/ci.sh.
set -euo pipefail

REPO=$(cd "$(dirname "$0")/.." && pwd)
NET=pan_ui_net; DB=pan_ui_db; ODOO=pan_ui_odoo; DBNAME=ui_db
SERIES=$(python3 -c "import ast;print('.'.join(ast.literal_eval(open('$REPO/__manifest__.py').read())['version'].split('.')[:2]))")
MODE=${1:-boot}

odoo_cli() {
    docker run --rm --network "$NET" -v "$REPO:/mnt/extra-addons/pan_mail_pro:ro" \
        --entrypoint odoo "odoo:${SERIES}" -d "$DBNAME" \
        --db_host="$DB" --db_port=5432 --db_user=odoo --db_password=odoo \
        --addons-path=/usr/lib/python3/dist-packages/odoo/addons,/mnt/extra-addons \
        --stop-after-init --max-cron-threads=0 --log-level=warn "$@"
}

if [ "$MODE" = "--stop" ]; then
    docker rm -f "$ODOO" "$DB" >/dev/null 2>&1 || true
    docker network rm "$NET" >/dev/null 2>&1 || true
    echo "stopped."
    exit 0
fi

if [ "$MODE" = "--update" ]; then
    odoo_cli -u pan_mail_pro
    docker restart "$ODOO" >/dev/null
    echo "updated — reload http://localhost:8069"
    exit 0
fi

docker rm -f "$ODOO" "$DB" >/dev/null 2>&1 || true
docker network create "$NET" >/dev/null 2>&1 || true
docker run -d --name "$DB" --network "$NET" \
    -e POSTGRES_USER=odoo -e POSTGRES_PASSWORD=odoo -e POSTGRES_DB=postgres postgres:15 >/dev/null
echo -n "Waiting for Postgres"
for _ in $(seq 1 60); do
    docker exec "$DB" pg_isready -h localhost -U odoo >/dev/null 2>&1 && break
    echo -n "."; sleep 1
done
echo " ready."

odoo_cli -i pan_mail_pro --without-demo=all
docker run -d --name "$ODOO" --network "$NET" -p 8069:8069 \
    -v "$REPO:/mnt/extra-addons/pan_mail_pro:ro" --entrypoint odoo "odoo:${SERIES}" \
    -d "$DBNAME" --db_host="$DB" --db_port=5432 --db_user=odoo --db_password=odoo \
    --addons-path=/usr/lib/python3/dist-packages/odoo/addons,/mnt/extra-addons \
    --max-cron-threads=0 --log-level=warn --dev=xml >/dev/null

echo -n "Waiting for Odoo"
for _ in $(seq 1 60); do
    curl -sf --noproxy '*' -o /dev/null http://localhost:8069/web/login && break
    echo -n "."; sleep 1
done
echo " ready."

# A database mid-setup: a provider with its registration, the internal domains,
# a notification mailbox and two mailboxes that have stopped. Every branch of
# the checklist is on screen at once, which is what makes it worth looking at.
python3 - "$DBNAME" <<'PY'
import sys, xmlrpc.client
db, pwd = sys.argv[1], 'admin'
uid = xmlrpc.client.ServerProxy('http://localhost:8069/xmlrpc/2/common').authenticate(db, 'admin', pwd, {})
rpc = xmlrpc.client.ServerProxy('http://localhost:8069/xmlrpc/2/object')
def call(model, method, *args, **kw):
    return rpc.execute_kw(db, uid, pwd, model, method, list(args), kw)
# All three providers, so the form's per-provider fields are on screen: only
# Microsoft asks for a tenant, only IMAP has no registration at all.
call('pan.mail.provider', 'create', {
    'provider': 'outlook', 'client_id': 'demo-client', 'client_secret': 'demo-secret',
    'tenant_id': 'demo-tenant', 'in_use': True})
call('pan.mail.provider', 'create', {
    'provider': 'gmail', 'client_id': 'demo-client', 'client_secret': 'demo-secret'})
call('pan.mail.provider', 'create', {'provider': 'imap'})
for name in ('example.com', 'example.odoo.com'):
    call('pan.mail.domain', 'create', {'name': name})
call('pan.mail.account', 'create', {
    'user_id': uid, 'provider': 'outlook', 'email': 'notifications@example.com',
    'refresh_token': 'demo', 'access_token': 'demo'})
ids = [call('pan.mail.mailbox', 'create', vals) for vals in (
    {'email': 'notifications@example.com', 'provider': 'outlook',
     'is_notification_mailbox': True, 'owner_user_id': uid},
    {'email': 'support@example.com', 'provider': 'outlook', 'owner_user_id': uid},
    {'email': 'sales@example.com', 'provider': 'outlook', 'owner_user_id': uid})]
call('pan.mail.mailbox', 'write', ids[1:], {'state': 'error'})
PY

echo "http://localhost:8069 — admin / admin"
