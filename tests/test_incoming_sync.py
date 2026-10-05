# -*- coding: utf-8 -*-
"""End-to-end cover for the incoming sync pipeline.

test_incoming_mail.py covers the helpers (_duplicate_of, _find_partner,
_is_internal_domain, _route_email_via_alias) but never drives _process_mailbox,
so the orchestration itself - fetch, normalize, route, post - had no coverage at
all. This file fills that gap.

Deliberately entered through `_process_mailbox(mailbox)` with only HTTP mocked.
That is the widest seam whose signature does not change across the provider
refactor, so the same tests pass before and after and can prove the refactor
preserved behaviour rather than merely not crashing.
"""
import base64
from datetime import timedelta
from unittest.mock import MagicMock, patch

from odoo import fields
from odoo.tests import tagged

from odoo.addons.pan_mail_pro.models.pan_mail_fetcher import FETCH_BATCH_SIZE, odoo_db_marker

from .common import MailProTestCase

GRAPH = 'https://graph.microsoft.com/v1.0'
MSG_ID = 'AAMkAGI2_fake_graph_id'
INTERNET_ID = '<inbound-001@example.com>'
CONV_ID = 'CONV_INBOUND_001'


@tagged('pan_mail_pro', 'post_install', '-at_install')
class TestCronProgress(MailProTestCase):
    """Under the cron, every mailbox's mail is committed before the next one.
    A person's Sync Now stays one transaction. Odoo's per-job budget is ten
    seconds and the API reports what is left of it; nothing here reads that
    number, because the re-run it would buy starts from the top."""

    def _run(self, context, seconds_left=3.0):
        fetcher = self.env['pan.mail.fetcher'].with_context(**context)
        Cron = type(self.env['ir.cron'])
        with patch.object(type(self.env['pan.mail.setup']), 'is_ready', return_value=True), \
                patch.object(type(self.env['pan.mail.mailbox']), '_has_working_credentials',
                             return_value=True), \
                patch.object(type(fetcher), '_process_mailbox', return_value=None) as process, \
                patch.object(Cron, '_commit_progress', return_value=seconds_left) as progress:
            fetcher._cron_fetch_incoming_mail()
        return process, progress

    def test_the_cron_commits_after_every_mailbox(self):
        process, progress = self._run({'ir_cron_progress_id': 1})
        self.assertEqual(process.call_count, 3)
        # Once with the total, once after each mailbox, counting down.
        self.assertEqual(progress.call_count, 4)
        self.assertEqual(progress.call_args_list[0].kwargs['remaining'], 3)
        self.assertEqual(
            [c.kwargs['remaining'] for c in progress.call_args_list[1:]], [2, 1, 0])
        self.assertTrue(all(c.kwargs['processed'] == 1 for c in progress.call_args_list[1:]))

    def test_the_stalest_mailbox_goes_first(self):
        self.shared_mailbox.last_sync_date = '2026-01-01 00:00:00'
        self.personal_mailbox.last_sync_date = '2026-02-01 00:00:00'
        self.notification_mailbox.last_sync_date = False
        process, _progress = self._run({'ir_cron_progress_id': 1})
        self.assertEqual(
            [c.args[0] for c in process.call_args_list],
            [self.notification_mailbox, self.shared_mailbox, self.personal_mailbox])

    def test_the_license_retry_runs_before_the_setup_gate(self):
        """Setup is when a first heartbeat can meet a bad minute, and setup is
        when the cron has nothing else to do and returns early."""
        fetcher = self.env['pan.mail.fetcher']
        License = type(self.env['pan.mail.license'])
        with patch.object(type(self.env['pan.mail.setup']), 'is_ready', return_value=False), \
                patch.object(License, '_retry_if_stuck') as retry:
            fetcher._cron_fetch_incoming_mail()
        retry.assert_called_once()

    def test_outside_the_cron_nothing_is_committed(self):
        process, progress = self._run({})
        self.assertEqual(process.call_count, 3)
        progress.assert_not_called()


@tagged('pan_mail_pro', 'post_install', '-at_install')
class TestIncomingSync(MailProTestCase):

    def setUp(self):
        super().setUp()
        self.mailbox = self.personal_mailbox
        self.mailbox.write({
            'sync_level': 'everyone',
            'last_sync_date': '2026-01-01 00:00:00',
        })
        self.fetched_urls = []
        # The same calls with their query string, for the tests that ask what
        # the listing was told rather than only which folder it hit.
        self.fetched_requests = []

    # ------------------------------------------------------------------ #
    # Graph fakes
    # ------------------------------------------------------------------ #
    def _preview(self, **overrides):
        preview = {
            'id': MSG_ID,
            'internetMessageId': INTERNET_ID,
            'subject': 'Question about my order',
            'from': {'emailAddress': {'name': 'External Customer',
                                      'address': 'customer@example.com'}},
            'toRecipients': [{'emailAddress': {'name': 'Sales',
                                               'address': 'sales@company.test'}}],
            'ccRecipients': [],
            'receivedDateTime': '2026-02-01T10:30:00Z',
            'hasAttachments': False,
        }
        preview.update(overrides)
        return preview

    def _full_message(self, **overrides):
        message = self._preview()
        message.update({
            'conversationId': CONV_ID,
            'internetMessageHeaders': [
                {'name': 'Message-ID', 'value': INTERNET_ID},
                {'name': 'Subject', 'value': 'Question about my order'},
            ],
            'body': {'contentType': 'html', 'content': '<p>Where is it?</p>'},
        })
        message.update(overrides)
        return message

    @staticmethod
    def _response(payload):
        resp = MagicMock()
        resp.status_code = 200
        resp.raise_for_status.return_value = None
        resp.json.return_value = payload
        return resp

    def _mock_graph_get(self, inbox=None, full=None, attachments=None):
        """Patch requests.get so the whole pipeline runs against fake Graph data."""
        inbox = self._preview() if inbox is None else inbox
        inbox_value = [] if inbox is False else [inbox]
        full = self._full_message() if full is None else full

        def fake_get(url, headers=None, params=None, timeout=None, **kwargs):
            self.fetched_urls.append(url)
            self.fetched_requests.append((url, dict(params or {})))
            if '/mailFolders/Inbox/messages' in url:
                return self._response({'value': inbox_value})
            if '/mailFolders/SentItems/messages' in url:
                return self._response({'value': []})
            if url.endswith('/attachments'):
                return self._response({'value': attachments or []})
            if f'/messages/{MSG_ID}' in url:
                return self._response(full)
            return self._response({})

        return patch(
            'odoo.addons.pan_mail_pro.models.providers.microsoft.graph_client.requests.get',
            side_effect=fake_get,
        )

    def _sync(self, **mock_kwargs):
        """Run one sync of the mailbox; hands back what `_process_mailbox`
        returned (None, or why the cursor is held)."""
        processor = self.env['pan.mail.fetcher']
        with patch.object(
            type(self.env['microsoft.graph.client']), 'get_valid_token',
            autospec=True, return_value='fake-bearer-token',
        ), self._mock_graph_get(**mock_kwargs):
            return processor._process_mailbox(self.mailbox)

    def _messages_on(self, partner):
        return self.env['mail.message'].search([
            ('model', '=', 'res.partner'),
            ('res_id', '=', partner.id),
            ('message_type', '=', 'email'),
        ])

    def _inbox_listings(self):
        """The query parameters of every listing of the Inbox folder, in order."""
        return [params for url, params in self.fetched_requests
                if '/mailFolders/Inbox/messages' in url]

    def _lead_alias(self, defaults):
        """An alias on crm.lead, as a sales team's alias is, with the given
        `alias_defaults`. `crm` is a dependency, so unlike the Helpdesk class in
        test_incoming_mail.py this runs on the community image CI uses."""
        return self.env['mail.alias'].create({
            'alias_name': 'leads-fixture',
            'alias_model_id': self.env['ir.model']._get_id('crm.lead'),
            'alias_defaults': defaults,
        })

    def _lead_for(self, partner):
        return self.env['crm.lead'].search([('partner_id', '=', partner.id)])

    # ------------------------------------------------------------------ #
    # Tests
    # ------------------------------------------------------------------ #
    def test_inbound_email_lands_on_partner_chatter(self):
        self._sync()

        messages = self._messages_on(self.external_partner)
        self.assertEqual(len(messages), 1, "inbound email should post exactly once")
        self.assertEqual(messages.subject, 'Question about my order')
        self.assertIn('Where is it?', messages.body)

    def test_inbound_email_is_stamped_for_the_lens(self):
        """Direction and mailbox must be recorded, or the overview cannot show
        where this mail came in. Stamped on a write that already happens, which
        makes it cheap and also easy to lose in a refactor."""
        self._sync()

        message = self._messages_on(self.external_partner)
        self.assertEqual(message.x_direction, 'incoming')
        self.assertEqual(message.x_mailbox_id, self.mailbox)

    def test_inbound_email_stores_ids_for_threading(self):
        """Reply threading depends on these two ids landing in the right index.

        The Message-ID goes into Odoo's native message_id (via message_post).
        The provider's thread handle goes into pan.mail.thread.link, scoped to
        the mailbox that saw it. The legacy column on mail.message is no longer
        written: one index per fact.
        """
        self._sync()

        message = self._messages_on(self.external_partner)
        self.assertEqual(message.message_id, INTERNET_ID)
        self.assertFalse(message.x_provider_thread_id,
                         "the legacy thread column is read-only since 19.0.6.0.0")
        link = self.env['pan.mail.thread.link'].search([
            ('mailbox_id', '=', self.mailbox.id),
            ('thread_id', '=', CONV_ID),
        ])
        self.assertEqual(link.last_message_id, message)

    def test_reply_threads_onto_the_message_it_answers(self):
        """The path that breaks silently: a reply must find its parent.

        Covers the in-reply-to -> native message_id branch of
        _find_parent_message, i.e. a customer replying to a mail we imported.
        """
        self._sync()
        parent = self._messages_on(self.external_partner)

        reply_id = '<inbound-002@example.com>'
        reply_preview = self._preview(id='REPLY_ID', internetMessageId=reply_id,
                                      subject='Re: Question about my order',
                                      receivedDateTime='2026-02-01T11:00:00Z')
        reply_full = self._full_message(
            id='REPLY_ID', internetMessageId=reply_id,
            subject='Re: Question about my order',
            receivedDateTime='2026-02-01T11:00:00Z',
            conversationId=CONV_ID,
            internetMessageHeaders=[
                {'name': 'Message-ID', 'value': reply_id},
                {'name': 'In-Reply-To', 'value': INTERNET_ID},
            ],
            body={'contentType': 'html', 'content': '<p>Any update?</p>'},
        )

        def fake_get(url, headers=None, params=None, timeout=None, **kwargs):
            if '/mailFolders/Inbox/messages' in url:
                return self._response({'value': [reply_preview]})
            if '/mailFolders/SentItems/messages' in url:
                return self._response({'value': []})
            if '/messages/REPLY_ID' in url:
                return self._response(reply_full)
            return self._response({})

        processor = self.env['pan.mail.fetcher']
        with patch.object(
            type(self.env['microsoft.graph.client']), 'get_valid_token',
            autospec=True, return_value='fake-bearer-token',
        ), patch(
            'odoo.addons.pan_mail_pro.models.providers.microsoft.graph_client.requests.get',
            side_effect=fake_get,
        ):
            processor._process_mailbox(self.mailbox)

        reply = self._messages_on(self.external_partner).filtered(
            lambda m: m.message_id == reply_id
        )
        self.assertTrue(reply, "reply should have been imported")
        self.assertEqual(reply.parent_id, parent, "reply must thread onto its parent")

    def test_sync_records_where_the_mail_landed(self):
        """The fetcher must actually write the routing log, not merely be able to.

        Unit tests cover the log model itself; this covers the wiring, which is
        the part that silently stops happening when someone refactors the
        posting branches.
        """
        self._sync()

        log = self.env['pan.mail.routing.log'].search([
            ('mailbox_id', '=', self.mailbox.id),
        ])
        self.assertEqual(len(log), 1, "one row per delivered mail")
        self.assertIn('customer@example.com', log.email_from)
        self.assertEqual(log.internet_message_id, INTERNET_ID)
        # No alias configured, nothing to thread onto: contact chatter, which
        # is exactly the case worth flagging.
        self.assertEqual(log.outcome, 'fallback')
        self.assertEqual(log.model, 'res.partner')
        self.assertEqual(log.res_id, self.external_partner.id)
        self.assertTrue(log.needs_review)

    def test_html_body_is_not_escaped(self):
        self._sync()

        body = self._messages_on(self.external_partner).body
        self.assertIn('<p>', body, "html body must survive as markup, not escaped text")

    def test_cc_recipients_are_carried_through(self):
        full = self._full_message(ccRecipients=[
            {'emailAddress': {'name': 'Colleague', 'address': 'cc@example.com'}},
        ])
        self._sync(full=full)

        self.assertEqual(len(self._messages_on(self.external_partner)), 1)

    def test_odoo_originated_email_is_skipped(self):
        """X-Odoo headers mark our own outbound mail; re-importing it would loop."""
        full = self._full_message(internetMessageHeaders=[
            {'name': 'Message-ID', 'value': INTERNET_ID},
            {'name': 'X-Odoo-Model', 'value': 'res.partner'},
            {'name': 'X-Odoo-Db', 'value': odoo_db_marker(self.env)},
        ])
        self._sync(full=full)

        self.assertFalse(self._messages_on(self.external_partner))

    def test_duplicate_is_not_posted_twice(self):
        self._sync()
        self._sync()

        self.assertEqual(len(self._messages_on(self.external_partner)), 1)

    def test_sync_cursor_advances_to_last_message(self):
        self._sync()

        self.assertEqual(
            str(self.mailbox.last_sync_date), '2026-02-01 10:30:00',
            "cursor must advance to the last message's date, in naive UTC",
        )

    def test_imported_mail_keeps_the_date_it_was_sent(self):
        """A historical import must not collapse onto the day it ran.

        `message_post` defaults `date` to now(), so omitting it dates every
        imported mail to import time — a year of correspondence lands on one
        afternoon and the chatter stops being a timeline. Covers the branch
        that posts to contact chatter via _route_email_via_alias.
        """
        self._sync()

        message = self._messages_on(self.external_partner)
        self.assertEqual(
            str(message.date), '2026-02-01 10:30:00',
            "message must carry the provider's date, not the import time",
        )

    def test_threaded_reply_keeps_its_own_date(self):
        """The threading branch posts separately, so it needs its own cover."""
        self._sync()

        reply_id = '<inbound-002@example.com>'
        # `id` stays MSG_ID so the harness's /messages/{id} fake still answers;
        # dedup keys on internetMessageId, which does change.
        overrides = {
            'internetMessageId': reply_id,
            'subject': 'Re: Question about my order',
            'receivedDateTime': '2026-02-03T09:15:00Z',
        }
        reply_full = self._full_message(
            **overrides,
            conversationId=CONV_ID,
            internetMessageHeaders=[
                {'name': 'Message-ID', 'value': reply_id},
                {'name': 'In-Reply-To', 'value': INTERNET_ID},
            ],
        )
        self._sync(inbox=self._preview(**overrides), full=reply_full)

        reply = self._messages_on(self.external_partner).filtered(
            lambda m: m.message_id == reply_id
        )
        self.assertTrue(reply, "reply should have been imported")
        self.assertEqual(str(reply.date), '2026-02-03 09:15:00')

    def test_attachment_is_stored(self):
        attachments = [{
            '@odata.type': '#microsoft.graph.fileAttachment',
            'name': 'invoice.pdf',
            'contentType': 'application/pdf',
            'contentBytes': base64.b64encode(b'%PDF-1.4 fake').decode(),
            'isInline': False,
        }]
        self._sync(full=self._full_message(hasAttachments=True), attachments=attachments)

        message = self._messages_on(self.external_partner)
        self.assertEqual(message.attachment_ids.mapped('name'), ['invoice.pdf'])
        self.assertEqual(base64.b64decode(message.attachment_ids.datas), b'%PDF-1.4 fake')

    def test_inline_image_becomes_web_image_url(self):
        """Inline attachments go in as 3-tuples so Odoo rewrites cid: to /web/image/."""
        attachments = [{
            '@odata.type': '#microsoft.graph.fileAttachment',
            'name': 'logo.png',
            'contentType': 'image/png',
            'contentBytes': base64.b64encode(b'\x89PNG fake').decode(),
            'isInline': True,
            'contentId': 'logo123',
        }]
        full = self._full_message(
            hasAttachments=False,  # Graph reports False for inline-only
            body={'contentType': 'html', 'content': '<p><img src="cid:logo123"></p>'},
        )
        self._sync(full=full, attachments=attachments)

        body = self._messages_on(self.external_partner).body
        self.assertNotIn('cid:logo123', body, "cid: should have been rewritten")
        self.assertIn('/web/image/', body)

    def test_reference_attachment_is_ignored(self):
        """Only fileAttachment carries contentBytes; others must not crash the sync."""
        attachments = [
            {'@odata.type': '#microsoft.graph.referenceAttachment', 'name': 'onedrive-link'},
            {'@odata.type': '#microsoft.graph.fileAttachment', 'name': 'real.txt',
             'contentType': 'text/plain',
             'contentBytes': base64.b64encode(b'hello').decode(), 'isInline': False},
        ]
        self._sync(full=self._full_message(hasAttachments=True), attachments=attachments)

        message = self._messages_on(self.external_partner)
        self.assertEqual(message.attachment_ids.mapped('name'), ['real.txt'])

    def test_attachments_not_fetched_for_skipped_message(self):
        """Attachments are fetched lazily, after the skip checks - not before.

        On a 1-minute cron most messages are already-seen duplicates; fetching
        their attachments would be pure waste.
        """
        self._sync()  # first pass imports it
        self.fetched_urls.clear()
        self._sync()  # second pass sees a duplicate

        self.assertFalse(
            [url for url in self.fetched_urls if url.endswith('/attachments')],
            "a duplicate must not trigger an attachment fetch",
        )

    # ------------------------------------------------------------------ #
    # The first sync
    # ------------------------------------------------------------------ #
    def test_first_sync_without_start_date_only_probes_the_connection(self):
        """A mailbox that has never synced and names no start date is not
        read: the first run asks the provider for one message to prove the
        credentials work, imports nothing, and plants the cursor at now, so
        the next run starts from there. Without the probe a wrong tenant or
        a revoked consent would surface a minute later, on a run that also
        had mail to lose."""
        self.mailbox.write({'last_sync_date': False, 'sync_start_date': False})
        before = fields.Datetime.now()

        self._sync()

        listings = self._inbox_listings()
        self.assertEqual(len(listings), 1, "exactly one listing: the probe")
        self.assertEqual(listings[0]['$top'], 1, "the probe asks for one message")
        self.assertNotIn('$filter', listings[0],
                         "there is no cursor yet, so nothing to filter on")
        self.assertFalse(
            [url for url in self.fetched_urls if f'/messages/{MSG_ID}' in url],
            "the probe must not fetch the message it listed")
        self.assertFalse(
            [url for url in self.fetched_urls if '/SentItems/' in url],
            "the first run stops after the probe; no folder is read")
        self.assertFalse(self._messages_on(self.external_partner),
                         "nothing is imported on the probe run")
        self.assertTrue(self.mailbox.last_sync_date)
        self.assertGreaterEqual(self.mailbox.last_sync_date, before)
        self.assertLessEqual(self.mailbox.last_sync_date - before, timedelta(minutes=1),
                             "the cursor is planted at now, not at the message")

    def test_first_sync_with_start_date_imports_from_that_date(self):
        """With a start date the first run is a real one: the listing is asked
        from that date, with the full batch size rather than the probe's one,
        the mail behind it is imported, and the cursor then advances past the
        start date to the newest message read."""
        self.mailbox.write({'last_sync_date': False,
                            'sync_start_date': '2026-01-15 00:00:00'})

        self._sync()

        listings = self._inbox_listings()
        self.assertEqual(len(listings), 1)
        self.assertEqual(listings[0]['$filter'],
                         'receivedDateTime gt 2026-01-15T00:00:00Z',
                         "the cursor the listing reads is the start date")
        self.assertEqual(listings[0]['$top'], FETCH_BATCH_SIZE,
                         "a historical sync reads a batch, not a probe")
        self.assertEqual(len(self._messages_on(self.external_partner)), 1,
                         "the mail behind the start date is imported")
        self.assertEqual(str(self.mailbox.last_sync_date), '2026-02-01 10:30:00',
                         "the cursor moves on from the start date to the newest "
                         "message read")

    # ------------------------------------------------------------------ #
    # Alias routing, on a model CI has
    # ------------------------------------------------------------------ #
    def test_new_conversation_is_routed_to_a_lead_via_the_alias(self):
        """`_route_email_via_alias` creates the record through `message_new`
        with the alias's own defaults, and posts the mail under the import
        boundary. Until now that path was only asserted on a Helpdesk ticket,
        in a class that skips itself on the community image CI runs. A lead
        is the same path on a model that is always installed."""
        alias = self._lead_alias(repr({'user_id': self.salesperson.id}))
        self.mailbox.write({'route_to_team': True, 'alias_id': alias.id})
        Mail = self.env['mail.mail'].sudo()
        last_mail_id = max(Mail.with_context(active_test=False).search([]).ids or [0])

        self._sync()

        lead = self._lead_for(self.external_partner)
        self.assertEqual(len(lead), 1, "one lead for the new conversation")
        self.assertEqual(lead.name, 'Question about my order')
        self.assertEqual(lead.user_id, self.salesperson,
                         "alias_defaults is parsed and reaches message_new")
        self.assertFalse(self._messages_on(self.external_partner),
                         "routed to the lead, not to the contact's chatter")

        message = self.env['mail.message'].search([
            ('model', '=', 'crm.lead'), ('res_id', '=', lead.id),
            ('message_type', '=', 'email'),
        ])
        self.assertEqual(len(message), 1, "the mail is posted on the lead once")
        self.assertIn('Where is it?', message.body)
        self.assertEqual(str(message.date), '2026-02-01 10:30:00',
                         "the post carries the provider's date")
        self.assertEqual(message.author_id, self.external_partner)

        # The sender is not a follower (mail_create_nosubscribe) and gets
        # nothing back (IMPORT_CTX drops the notification pass): the two
        # things the native message_new() flow was chosen for.
        self.assertNotIn(self.external_partner, lead.message_partner_ids,
                         "the sender must not be subscribed to the record")
        self.assertFalse(message.notification_ids,
                         "an imported post notifies nobody")
        queued = Mail.with_context(active_test=False).search([('id', '>', last_mail_id)])
        for mail in queued:
            self.assertNotIn('customer@example.com', mail.email_to or '',
                             "no mail.mail may be queued to the sender")
            self.assertNotIn(self.external_partner, mail.recipient_ids,
                             "no mail.mail may be queued to the sender")

        log = self.env['pan.mail.routing.log'].search([
            ('mailbox_id', '=', self.mailbox.id)])
        self.assertEqual(log.outcome, 'created')
        self.assertEqual(log.model, 'crm.lead')
        self.assertEqual(log.res_id, lead.id)

    def test_broken_alias_defaults_does_not_stop_the_import(self):
        """`alias_defaults` is a Python literal typed by an administrator.
        `mail.alias` refuses a broken one on write, but a row can carry one
        from before that check or from a direct update; the route then
        creates the record without the defaults rather than failing the
        message, which would stall the mailbox on it."""
        alias = self._lead_alias(repr({'user_id': self.salesperson.id}))
        # Past the constraint, the way a stale row would be.
        self.env.cr.execute(
            "UPDATE mail_alias SET alias_defaults = %s WHERE id = %s",
            ("{'user_id': ", alias.id))
        alias.invalidate_recordset(['alias_defaults'])
        self.assertEqual(alias.alias_defaults, "{'user_id': ")
        self.mailbox.write({'route_to_team': True, 'alias_id': alias.id})

        stall = self._sync()

        self.assertIsNone(stall, "a broken default is not a stalled mailbox")
        lead = self._lead_for(self.external_partner)
        self.assertEqual(len(lead), 1, "the lead is still created")
        self.assertEqual(lead.name, 'Question about my order')
        self.assertEqual(str(self.mailbox.last_sync_date), '2026-02-01 10:30:00',
                         "the cursor moves past the message")

