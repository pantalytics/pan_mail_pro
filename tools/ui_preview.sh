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
# One provider row -- Mail Pro runs on one, and a second is refused.
call('pan.mail.provider', 'create', {
    'provider': 'outlook', 'client_id': 'demo-client', 'client_secret': 'demo-secret',
    'tenant_id': '11111111-2222-3333-4444-555555555555'})
for name in ('example.com', 'example.odoo.com'):
    call('pan.mail.domain', 'create', {'name': name})
# The instance stays connected: accounts are only created on a connected one,
# and the settings page shows nothing but the Connect button until it is.
# `tools/ui_check.py` disconnects it itself to look at that state.
import datetime
call('pan.mail.license', 'create', {
    'status': 'active',
    'valid_until': (datetime.datetime.now(datetime.UTC)
                    + datetime.timedelta(days=14)).strftime('%Y-%m-%d %H:%M:%S')})
# One account per user per provider, and it carries the admin's own address:
# that is what makes their own mailbox below personal rather than shared.
# Credentials are resolved per person, not per address, so the notification
# mailbox still reads and sends with this same account.
call('res.users', 'write', [uid], {'email': 'admin@example.com'})
call('pan.mail.account', 'create', {
    'user_id': uid, 'provider': 'outlook', 'email': 'admin@example.com',
    'refresh_token': 'demo', 'access_token': 'demo'})
ids = [call('pan.mail.mailbox', 'create', vals) for vals in (
    {'email': 'notifications@example.com', 'provider': 'outlook',
     'is_notification_mailbox': True, 'owner_user_id': uid},
    {'email': 'support@example.com', 'provider': 'outlook', 'owner_user_id': uid},
    {'email': 'sales@example.com', 'provider': 'outlook', 'owner_user_id': uid})]
call('pan.mail.mailbox', 'write', ids[1:], {'state': 'error'})
# The admin's own mailbox, the only kind that has consent to give. Sequence 90
# keeps it last in the mailbox list, where the checks that index into that list
# do not trip over it. The level is set in a second call, as its owner, because
# that is the write that dates the choice.
own = call('pan.mail.mailbox', 'create', {
    'email': 'admin@example.com', 'provider': 'outlook',
    'owner_user_id': uid, 'sequence': 90})
call('pan.mail.mailbox', 'write', [own], {'sync_level': 'contacts'})
# Mail, so the Inbox screen shows the thing it is for rather than its empty
# state. Three messages on one lead: a question, our answer, their reply.
#
# Every date below is explicit, and that is not decoration. The Inbox orders
# conversations by their newest message and breaks a tie on the model name, so
# a seed that lets Postgres stamp everything `now()` opens on whichever record
# sorts first *when the seed happens to straddle a second*. That passed on a
# laptop and failed on a CI runner, which is the definition of a flaky
# fixture. The lead thread is the most recent, the two unlinked ones are days
# old, and the screen opens on the same conversation every time.
def ago(**kw):
    return (datetime.datetime.now(datetime.UTC) - datetime.timedelta(**kw)).strftime(
        '%Y-%m-%d %H:%M:%S')


# A 1x1 PNG, built rather than pasted. Odoo opens an image attachment with
# PIL at create(), so a base64 blob that is a few bytes short is a
# valid-looking string and a create that fails with "Truncated File Read".
import base64
import struct
import zlib


def _png_chunk(kind, data):
    return (struct.pack('>I', len(data)) + kind + data
            + struct.pack('>I', zlib.crc32(kind + data) & 0xffffffff))


PIXEL = base64.b64encode(
    b'\x89PNG\r\n\x1a\n'
    + _png_chunk(b'IHDR', struct.pack('>IIBBBBB', 1, 1, 8, 2, 0, 0, 0))
    + _png_chunk(b'IDAT', zlib.compress(b'\x00\xff\xff\xff', 9))
    + _png_chunk(b'IEND', b'')).decode()


customer = call('res.partner', 'create', {
    'name': 'Vandermolen Techniek B.V.', 'email': 'bart@vandermolen.example'})
lead = call('crm.lead', 'create', {
    'name': 'Asafdichtingen, revisie', 'partner_id': customer,
    'email_from': 'bart@vandermolen.example'})
for subject, body, direction, when in (
    ('Offerte revisie asafdichtingen',
     '<p>Kunnen jullie de levertijd op regel 3 nog bevestigen?</p>',
     'incoming', ago(hours=26)),
    ('Re: Offerte revisie asafdichtingen',
     '<p>Drie weken vanaf akkoord, dat leggen we vast.</p>',
     'outgoing', ago(hours=22)),
    ('Re: Offerte revisie asafdichtingen',
     '<p>Prima. Dan graag opdracht bevestigen.</p>',
     'incoming', ago(hours=2)),
):
    message = call('mail.message', 'create', {
        'model': 'crm.lead', 'res_id': lead, 'message_type': 'email',
        'subject': subject, 'body': body, 'author_id': customer,
        'email_from': 'bart@vandermolen.example', 'date': when,
        'x_direction': direction, 'x_mailbox_id': ids[2]})
    if direction == 'outgoing':
        # One attachment, so the Files tab shows the list it exists for
        # instead of its empty state. An image, because it is the one type
        # the viewer renders without a plugin: what the check has to prove
        # is that a click opens Odoo's own viewer at all.
        attachment = call('ir.attachment', 'create', {
            'name': 'asafdichting.png', 'mimetype': 'image/png',
            'res_model': 'crm.lead', 'res_id': lead, 'datas': PIXEL})
        call('mail.message', 'write', [message],
             {'attachment_ids': [(6, 0, [attachment])]})
# Two follow-ups on the lead. The Activities tab draws Odoo's own activity
# card, and both the icon and the colour come off the activity type and the
# deadline: one planned and one overdue is the pair that shows both.
def xmlid(module, name):
    return call('ir.model.data', 'search_read',
                [('module', '=', module), ('name', '=', name)],
                fields=['res_id'])[0]['res_id']
lead_model = call('ir.model', 'search', [('model', '=', 'crm.lead')])[0]
for ref, summary, days in (
    ('mail_activity_data_call', 'Bart terugbellen over regel 3', 3),
    ('mail_activity_data_todo', 'Opdrachtbevestiging opstellen', -2),
):
    call('mail.activity', 'create', {
        'res_model_id': lead_model, 'res_id': lead,
        'activity_type_id': xmlid('mail', ref),
        'summary': summary, 'user_id': uid,
        'date_deadline': (datetime.date.today()
                          + datetime.timedelta(days=days)).strftime('%Y-%m-%d')})
# Two conversations that landed on a contact and nowhere better: the real
# `fallback` outcome, delivered but to a place nobody is looking. Each carries
# the suggestion the ladder nearly picked, which is what the Inbox offers with
# one click. Two different contacts, because mail on one contact is one
# conversation however many messages it holds -- and linking the first has to
# leave a second behind.
for name, address, subject, body, when in (
    ('Vandermolen Techniek B.V.', 'bart@vandermolen.example',
     'Storing aan de pers, spoed',
     '<p>De pers loopt vast bij het inschakelen. Kunnen jullie meekijken?</p>',
     ago(days=2)),
    ('Keersluis Onderhoud', 'inkoop@keersluis.example',
     'Nieuwe aanvraag afdichtingen',
     '<p>Graag een prijs voor twee sets, zelfde maat als vorig jaar.</p>',
     ago(days=3)),
):
    sender = call('res.partner', 'create', {'name': name, 'email': address}) \
        if address != 'bart@vandermolen.example' else customer
    fallen_back = call('mail.message', 'create', {
        'model': 'res.partner', 'res_id': sender,
        'message_type': 'email', 'subject': subject, 'body': body,
        'author_id': sender, 'email_from': address, 'date': when,
        'x_direction': 'incoming', 'x_mailbox_id': ids[2]})
    call('pan.mail.routing.log', 'create', {
        'mailbox_id': ids[2], 'mail_message_id': fallen_back, 'outcome': 'fallback',
        'subject': subject, 'email_from': address,
        'reason': 'No rule reached the routing threshold (1 proposal(s))',
        'rule': False, 'candidate_count': 1,
        'suggested_model': 'crm.lead', 'suggested_res_id': lead,
        'suggested_name': 'Asafdichtingen, revisie',
        'suggested_reason': 'The only open Lead/Opportunity for %s' % name})
PY

echo "http://localhost:8069 — admin / admin"
