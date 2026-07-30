# -*- coding: utf-8 -*-
"""End-to-end cover for the incoming sync pipeline.

test_incoming_mail.py covers the helpers (_is_duplicate, _find_partner,
_is_internal_domain, _route_email_via_alias) but never drives _process_mailbox,
so the orchestration itself - fetch, normalize, route, post - had no coverage at
all. This file fills that gap.

Deliberately entered through `_process_mailbox(mailbox)` with only HTTP mocked.
That is the widest seam whose signature does not change across the provider
refactor, so the same tests pass before and after and can prove the refactor
preserved behaviour rather than merely not crashing.
"""
import base64
from unittest.mock import MagicMock, patch

from odoo.tests import tagged

from .common import OutlookProTestCase

GRAPH = 'https://graph.microsoft.com/v1.0'
MSG_ID = 'AAMkAGI2_fake_graph_id'
INTERNET_ID = '<inbound-001@example.com>'
CONV_ID = 'CONV_INBOUND_001'


@tagged('pan_mail_pro', 'post_install', '-at_install')
class TestIncomingSync(OutlookProTestCase):

    def setUp(self):
        super().setUp()
        self.mailbox = self.personal_mailbox
        self.mailbox.write({
            'x_sync_mode': 'all',
            'x_last_sync_date': '2026-01-01 00:00:00',
        })
        self.fetched_urls = []

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
        processor = self.env['microsoft.incoming.mail.processor']
        with patch.object(
            type(self.env['microsoft.graph.client']), 'get_valid_token',
            autospec=True, return_value='fake-bearer-token',
        ), self._mock_graph_get(**mock_kwargs):
            processor._process_mailbox(self.mailbox)

    def _messages_on(self, partner):
        return self.env['mail.message'].search([
            ('model', '=', 'res.partner'),
            ('res_id', '=', partner.id),
            ('message_type', '=', 'email'),
        ])

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
        """Reply threading depends on these, and the two fields are asymmetric.

        Imported messages land their Message-ID in Odoo's native message_id (via
        message_post), while x_microsoft_message_id is only ever written for mail
        we sent ourselves. _find_parent_message searches both, in that order.
        Asserting x_microsoft_message_id here would be asserting the wrong half
        of the design.
        """
        self._sync()

        message = self._messages_on(self.external_partner)
        self.assertEqual(message.message_id, INTERNET_ID)
        self.assertFalse(
            message.x_microsoft_message_id,
            "x_microsoft_message_id is for outgoing mail only",
        )
        self.assertEqual(message.x_microsoft_conversation_id, CONV_ID)

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

        processor = self.env['microsoft.incoming.mail.processor']
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
            str(self.mailbox.x_last_sync_date), '2026-02-01 10:30:00',
            "cursor must advance to the last message's date, in naive UTC",
        )

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
