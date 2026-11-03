#!/usr/bin/env python3
"""Seed a fresh pan_mail_pro database for the browser checks.

    tools/ui_seed.py <database> [<url>]

Shared by tools/ui_preview.sh (the Docker boot CI uses) and tools/ui_local.sh
(the same without Docker, against the series clones of tools/odoo_local.sh).
The data is fake and the database a throwaway.
"""
import sys
import xmlrpc.client

db, pwd = sys.argv[1], 'admin'
url = sys.argv[2] if len(sys.argv) > 2 else 'http://localhost:8069'
uid = xmlrpc.client.ServerProxy(f'{url}/xmlrpc/2/common').authenticate(db, 'admin', pwd, {})
rpc = xmlrpc.client.ServerProxy(f'{url}/xmlrpc/2/object')
def call(model, method, *args, **kw):
    return rpc.execute_kw(db, uid, pwd, model, method, list(args), kw)
# One provider row -- Mail Pro runs on one, and a second is refused.
# No onboarding tour: on a fresh database Odoo walks the admin through the
# screen with a pointer that sits over the page and takes the clicks the
# check makes (20 starts one on the Inbox; a check that times out on
# "Discard" is that pointer).
if 'tour_enabled' in call('res.users', 'fields_get', [], attributes=['type']):
    call('res.users', 'write', [uid], {'tour_enabled': False})

call('pan.mail.provider', 'create', {
    'provider': 'outlook', 'client_id': 'demo-client', 'client_secret': 'demo-secret',
    'tenant_id': '11111111-2222-3333-4444-555555555555'})
for name in ('example.com', 'example.odoo.com'):
    call('pan.mail.domain', 'create', {'name': name})
# The instance stays connected: accounts are only created on a connected one,
# and the settings page shows nothing but step 1 until it is.
# `tools/ui_check.py` disconnects it itself to look at that state.
import datetime
call('pan.mail.license', 'create', {
    'status': 'active',
    'connected_account': 'seed@pantalytics.test',
    'connected_on': datetime.datetime.now(datetime.UTC).strftime('%Y-%m-%d %H:%M:%S'),
    'valid_until': (datetime.datetime.now(datetime.UTC)
                    + datetime.timedelta(days=14)).strftime('%Y-%m-%d %H:%M:%S')})
call('pan.mail.account', 'create', {
    'user_id': uid, 'provider': 'outlook', 'email': 'notifications@example.com',
    'refresh_token': 'demo', 'access_token': 'demo'})
ids = [call('pan.mail.mailbox', 'create', vals) for vals in (
    {'email': 'notifications@example.com', 'provider': 'outlook',
     'is_notification_mailbox': True, 'owner_user_id': uid},
    {'email': 'support@example.com', 'provider': 'outlook', 'owner_user_id': uid},
    {'email': 'sales@example.com', 'provider': 'outlook', 'owner_user_id': uid})]
call('pan.mail.mailbox', 'write', ids[1:], {'state': 'error'})
# One mailbox that is this user's own, so the folder that reads a mailbox in
# full has somewhere to appear. `mailbox_type` is computed, not chosen: a
# mailbox is personal when its address is one the owner signed in with or the
# one on their user record. A second account is not the way to arrange that --
# a user may hold one account per provider -- so the address on the user is.
#
# Last in the mailbox list, by sequence rather than by address: every check
# below counts on the seeded mail being in the mailbox the screen opens on,
# and an address that happens to sort first would quietly move it.
call('res.users', 'write', [uid], {'email': 'rutger@example.com'})
ids.append(call('pan.mail.mailbox', 'create', {
    'email': 'rutger@example.com', 'provider': 'outlook',
    'owner_user_id': uid, 'sequence': 20}))
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
        # 20 has no `datas` field on ir.attachment; `raw` takes base64 text over RPC.
        content = ({'datas': PIXEL} if 'datas' in call('ir.attachment', 'fields_get', [], attributes=['type'])
                   else {'raw': PIXEL})
        attachment = call('ir.attachment', 'create', {
            'name': 'asafdichting.png', 'mimetype': 'image/png',
            'res_model': 'crm.lead', 'res_id': lead, **content})
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
# Mail in the *other* mailbox, on a lead of its own. Without it every seeded
# conversation is in one mailbox, and All mailboxes reads exactly like the
# mailbox under it -- which proves nothing and hides the bug it exists to
# catch. Older than the lead thread above, so the screen still opens on the
# same conversation.
other_customer = call('res.partner', 'create', {
    'name': 'Waterschap Rijnmond', 'email': 'peter@rijnmond.example'})
other_lead = call('crm.lead', 'create', {
    'name': 'Onderhoudscontract 2027', 'partner_id': other_customer,
    'email_from': 'peter@rijnmond.example'})
call('mail.message', 'create', {
    'model': 'crm.lead', 'res_id': other_lead, 'message_type': 'email',
    'subject': 'Verlenging onderhoudscontract',
    'body': '<p>Kunnen we het contract een jaar verlengen?</p>',
    'author_id': other_customer, 'email_from': 'peter@rijnmond.example',
    'date': ago(hours=20), 'x_direction': 'incoming', 'x_mailbox_id': ids[1]})
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
