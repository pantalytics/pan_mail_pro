# -*- coding: utf-8 -*-
"""End-to-end incoming sync for a Gmail mailbox.

test_google_provider.py covers the Gmail client in isolation and
test_incoming_sync.py drives the whole pipeline — but only ever against
Microsoft. So the orchestration had never actually run on Gmail data: label
mapping, the two-call preview/full fetch, threading on threadId, and the Odoo
loop guard were all unverified through `_process_mailbox`.

TESTPLAN A4 covers this manually against a real Workspace tenant. What can be
automated is automated here, so the manual run is left with the parts that
genuinely need Google: the OAuth consent screen and real token refresh.

Deliberately entered through `_process_mailbox(mailbox)` with only HTTP mocked,
mirroring test_incoming_sync.py — same seam, same guarantees, different provider.
"""
import base64
import json
from unittest.mock import MagicMock, patch

from odoo.tests import tagged

from .common import MailProTestCase

GMAIL_ID = 'gmail_msg_0001'
INTERNET_ID = '<inbound-gmail-001@example.com>'
THREAD_ID = 'THREAD_GMAIL_001'
GMAIL_GET = 'odoo.addons.pan_mail_pro.models.providers.google.gmail_client.requests.get'


@tagged('pan_mail_pro', 'post_install', '-at_install')
class TestGmailIncomingSync(MailProTestCase):

    def setUp(self):
        super().setUp()
        Account = self.env['pan.mail.account']
        Mailbox = self.env['pan.mail.mailbox']

        self.gmail_user = self.env['res.users'].with_context(
            **self.SILENT_CTX
        ).create({
            'name': 'Gmail Sync User',
            'login': 'gmail_sync@test.local',
            'email': 'gmail_sync@test.local',
            'group_ids': [(6, 0, [self.env.ref('base.group_user').id])],
        })
        Account.create({
            'email': 'gmail_sync@test.local',
            'provider': 'gmail',
            'user_id': self.gmail_user.id,
            'refresh_token': 'goog-refresh',
        })
        # A notification mailbox must exist before any mailbox may sync; the
        # one from the fixture qualifies, its provider is irrelevant here.

        self.mailbox = Mailbox.create({
            'email': 'gmail_sync@test.local',
            'provider': 'gmail',
            'mailbox_type': 'personal',
            'owner_user_id': self.gmail_user.id,
            'sync_mode': 'all',
            'last_sync_date': '2026-01-01 00:00:00',
        })
        self.requested_urls = []

    # ------------------------------------------------------------------ #
    # Gmail fakes
    # ------------------------------------------------------------------ #
    def _payload(self, body='<p>Where is my order?</p>', message_id=INTERNET_ID,
                 extra_headers=()):
        headers = [
            {'name': 'Message-Id', 'value': message_id},
            {'name': 'Subject', 'value': 'Question about my order'},
            {'name': 'From', 'value': 'External Customer <customer@example.com>'},
            {'name': 'To', 'value': 'Gmail Sync <gmail_sync@test.local>'},
        ]
        headers.extend({'name': n, 'value': v} for n, v in extra_headers)
        return {
            'headers': headers,
            'mimeType': 'text/html',
            'body': {'data': base64.urlsafe_b64encode(body.encode()).decode()},
        }

    def _message(self, **overrides):
        message = {
            'id': GMAIL_ID,
            'threadId': THREAD_ID,
            'labelIds': ['INBOX', 'UNREAD'],
            'internalDate': '1769941800000',  # 2026-02-01T10:30:00Z, in ms
            'payload': self._payload(),
        }
        message.update(overrides)
        return message

    @staticmethod
    def _response(payload):
        resp = MagicMock()
        resp.status_code = 200
        resp.raise_for_status.return_value = None
        resp.json.return_value = payload
        resp.text = json.dumps(payload)
        return resp

    def _mock_gmail_get(self, inbox_ids=None, message=None):
        """Patch requests.get so the pipeline runs on fake Gmail data.

        Gmail needs two shapes the Graph fake does not: a list endpoint that
        returns bare ids, and a per-message endpoint hit twice (metadata for the
        preview, full for the body).
        """
        inbox_ids = [{'id': GMAIL_ID, 'threadId': THREAD_ID}] if inbox_ids is None else inbox_ids
        message = self._message() if message is None else message

        def fake_get(url, headers=None, params=None, timeout=None, **kwargs):
            params = params or {}
            self.requested_urls.append((url, params.get('labelIds')))
            if url.endswith('/messages'):
                if params.get('labelIds') == 'INBOX':
                    return self._response({'messages': inbox_ids})
                return self._response({'messages': []})
            # Matched loosely on purpose: the reply test fetches a different id,
            # and Gmail is hit twice per message (metadata, then full).
            if '/messages/' in url:
                return self._response(message)
            return self._response({})

        return patch(GMAIL_GET, side_effect=fake_get)

    def _sync(self, **mock_kwargs):
        processor = self.env['pan.mail.fetcher']
        with patch.object(
            type(self.env['google.gmail.client']), 'get_valid_token',
            autospec=True, return_value='fake-google-token',
        ), self._mock_gmail_get(**mock_kwargs):
            processor._process_mailbox(self.mailbox)

    def _messages_on(self, partner):
        return self.env['mail.message'].search([
            ('model', '=', 'res.partner'),
            ('res_id', '=', partner.id),
            ('message_type', '=', 'email'),
        ])

    # ------------------------------------------------------------------ #
    # A4: inkomende mail gesynct (INBOX-label)
    # ------------------------------------------------------------------ #
    def test_inbound_gmail_lands_on_partner_chatter(self):
        self._sync()

        messages = self._messages_on(self.external_partner)
        self.assertEqual(len(messages), 1, "inbound Gmail should post exactly once")
        self.assertEqual(messages.subject, 'Question about my order')
        self.assertIn('Where is my order?', messages.body)

    def test_sync_asks_gmail_for_the_inbox_and_sent_labels(self):
        """Folder ids from the contract must map to Gmail labels, not Graph folders."""
        self._sync()

        labels = {label for _url, label in self.requested_urls if label}
        self.assertIn('INBOX', labels)
        self.assertIn('SENT', labels)

    def test_inbound_gmail_stores_message_id_for_threading(self):
        self._sync()

        message = self._messages_on(self.external_partner)
        self.assertEqual(message.message_id, INTERNET_ID)

    # ------------------------------------------------------------------ #
    # A4: reply landt in dezelfde thread (threadId)
    # ------------------------------------------------------------------ #
    def test_reply_threads_onto_the_same_record(self):
        """Second message, same Gmail thread, must not create a second parent."""
        self._sync()
        first = self._messages_on(self.external_partner)
        self.assertEqual(len(first), 1)

        reply = self._message(
            id='gmail_msg_0002',
            payload=self._payload(
                body='<p>Any update?</p>',
                message_id='<inbound-gmail-002@example.com>',
                extra_headers=(('In-Reply-To', INTERNET_ID),),
            ),
        )
        self._sync(inbox_ids=[{'id': 'gmail_msg_0002', 'threadId': THREAD_ID}],
                   message=reply)

        messages = self._messages_on(self.external_partner)
        self.assertEqual(len(messages), 2, "reply should post to the same partner")
        self.assertEqual(
            set(messages.mapped('res_id')), {self.external_partner.id},
            "both messages hang on one record",
        )

    # ------------------------------------------------------------------ #
    # Loop guard and dedup on the Gmail path
    # ------------------------------------------------------------------ #
    def test_odoo_originated_gmail_is_not_reimported(self):
        """We stamp X-Odoo-* on everything we send; Gmail hands it back in SENT."""
        message = self._message(
            payload=self._payload(extra_headers=(('X-Odoo-Model', 'res.partner'),)),
        )
        self._sync(message=message)

        self.assertFalse(
            self._messages_on(self.external_partner),
            "a mail Odoo sent itself must not come back in as inbound",
        )

    def test_second_sync_does_not_duplicate(self):
        self._sync()
        self._sync()

        self.assertEqual(
            len(self._messages_on(self.external_partner)), 1,
            "dedup on Message-ID must hold across sync runs",
        )
