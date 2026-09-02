# -*- coding: utf-8 -*-
"""The one invariant the outbound side of the sync has to hold.

Thirty test files covered where imported mail *lands* and none of them covered
what the sync *sends*. That is why a no-op safeguard survived six months in
production code carrying a comment describing behaviour it never had: nothing
looked. This file looks.

The invariant is deliberately absolute, because an imported mail has already
reached its recipients through the provider:

    a message the sync imported notifies nobody.

No `mail.notification`, no `mail.mail`, no new follower. Not "fewer people",
not "skip whoever was already on the original" -- nobody. A rule with no
legitimate exception is a rule a test can pin, which is the whole reason for
stating it that way.

Four things have to be covered separately, because each could break on its own:

1. **The rule itself**, with no provider anywhere near it. This is the boundary.
2. **Both directions.** Inbox and Sent Items are different branches of
   `_process_message`, and it was the Sent Items branch that leaked at
   Juffermans.
3. **All three providers.** Graph, Gmail and IMAP feed the same
   `_process_message`, so they cannot disagree by construction -- but "cannot
   disagree by construction" is exactly what was believed about the internal
   filter before it turned out to run on one folder only.
4. **Odoo's own sending, unaffected.** The dangerous failure of this change is
   not that it misses an import; it is that it silences a message a person
   wrote. Two tests guard that direction.

See ARCHITECTURE.md §9.10.
"""
import base64
from unittest.mock import MagicMock, patch

from odoo.tests import tagged

from ..models.mail_provider_client import FOLDER_INBOX, FOLDER_SENT
from .common import OutlookProTestCase

GRAPH_MSG_ID = 'AAMkAGI2_fake_graph_id'
INTERNET_ID = '<quiet-sync-001@example.com>'
CONV_ID = 'CONV_QUIET_001'
CUSTOMER = 'customer@example.com'


class QuietSyncMixin:
    """Assertions shared by every provider and both directions."""

    def assertSyncSentNothing(self, before, partner=None):
        """`before` is the snapshot taken by `snapshot()` prior to the sync."""
        self.assertEqual(
            self.env['mail.notification'].search_count([]), before['notifications'],
            "an imported mail already reached its recipients through the provider; "
            "Odoo must not notify anybody about it",
        )
        self.assertEqual(
            self.env['mail.mail'].search_count([]), before['mails'],
            "the sync must never put an envelope in the queue",
        )
        if partner is not None:
            added = self._followers_of(partner) - before['followers']
            self.assertFalse(
                added,
                "a follower is a human act; the sync never creates one. "
                "Subscribed by this run: %s" % ', '.join(
                    '%s (id=%s)' % (p.name, p.id) for p in added
                ),
            )

    def snapshot(self, partner=None):
        return {
            'notifications': self.env['mail.notification'].search_count([]),
            'mails': self.env['mail.mail'].search_count([]),
            'followers': self._followers_of(partner) if partner is not None
            else self.env['res.partner'],
        }

    def _followers_of(self, partner):
        """The partners following `partner`'s own record.

        Returned as a recordset rather than a count so a failure can name who
        was subscribed. A number tells you the sync did something it should not
        have; the name tells you which call site did it.
        """
        return self.env['mail.followers'].search([
            ('res_model', '=', 'res.partner'),
            ('res_id', '=', partner.id),
        ]).partner_id


@tagged('pan_mail_pro', 'post_install', '-at_install')
class TestNotifyBoundary(OutlookProTestCase, QuietSyncMixin):
    """The rule, with no provider involved.

    Everything else in this file proves the fetcher arms the boundary. This
    class proves the boundary exists, which is a different claim and the one
    that has to survive a provider being added or replaced.
    """

    def setUp(self):
        super().setUp()
        self.partner = self.external_partner
        self.partner.sudo().message_subscribe(partner_ids=[self.company_partner.id])

    def test_an_imported_post_notifies_nobody(self):
        before = self.snapshot(self.partner)

        self.partner.with_context(pan_mail_imported=True).message_post(
            body='<p>Imported from a mailbox.</p>',
            subject='Imported',
            message_type='email',
            subtype_xmlid='mail.mt_comment',
        )

        self.assertSyncSentNothing(before, self.partner)

    def test_the_same_post_without_the_flag_does_notify(self):
        """The control. Without it this file would also pass if the override
        killed notification for everything rather than for imports only.

        `mail_notify_force_send=False` keeps the envelope in the queue instead
        of delivering it inline, which is what a test wants either way: the
        assertion is that a notification and a `mail.mail` were produced, not
        that a fake Graph accepted them.
        """
        message = self.partner.with_context(
            mail_notify_force_send=False,
        ).message_post(
            body='<p>Written by a person.</p>',
            subject='Human message',
            message_type='comment',
            subtype_xmlid='mail.mt_comment',
        )

        self.assertTrue(
            message.notification_ids,
            "a follower must still be notified of an ordinary chatter message",
        )

    def test_odoo_still_queues_its_own_outgoing_mail(self):
        """Path C in ARCHITECTURE.md §3: a person clicked send, or a colleague
        was notified about a task. Suppressing that would break ordinary Odoo,
        and it is the failure this change could plausibly cause."""
        before = self.env['mail.mail'].search_count([])

        self.partner.with_context(
            mail_notify_force_send=False,
        ).message_post(
            body='<p>Please find the quote attached.</p>',
            subject='Quote',
            message_type='comment',
            subtype_xmlid='mail.mt_comment',
        )

        self.assertGreater(
            self.env['mail.mail'].search_count([]), before,
            "Odoo's own outgoing mail must still reach the queue",
        )


@tagged('pan_mail_pro', 'post_install', '-at_install')
class TestGraphSyncSendsNothing(OutlookProTestCase, QuietSyncMixin):
    """Microsoft 365, both directions, driven through `_process_mailbox`."""

    def setUp(self):
        super().setUp()
        self.mailbox = self.personal_mailbox
        self.mailbox.write({
            'x_sync_mode': 'all',
            'x_last_sync_date': '2026-01-01 00:00:00',
        })

    def _graph_message(self, outgoing=False):
        us = {'emailAddress': {'name': 'Sales', 'address': 'sales@company.test'}}
        them = {'emailAddress': {'name': 'External Customer', 'address': CUSTOMER}}
        return {
            'id': GRAPH_MSG_ID,
            'internetMessageId': INTERNET_ID,
            'conversationId': CONV_ID,
            'subject': 'Question about my order',
            'from': them if not outgoing else us,
            'toRecipients': [us if not outgoing else them],
            'ccRecipients': [],
            'receivedDateTime': '2026-02-01T10:30:00Z',
            'sentDateTime': '2026-02-01T10:30:00Z',
            'hasAttachments': False,
            'internetMessageHeaders': [{'name': 'Message-ID', 'value': INTERNET_ID}],
            'body': {'contentType': 'html', 'content': '<p>Where is it?</p>'},
        }

    @staticmethod
    def _response(payload):
        resp = MagicMock()
        resp.status_code = 200
        resp.raise_for_status.return_value = None
        resp.json.return_value = payload
        return resp

    def _sync(self, outgoing=False):
        message = self._graph_message(outgoing=outgoing)
        inbox = [] if outgoing else [message]
        sent = [message] if outgoing else []

        def fake_get(url, headers=None, params=None, timeout=None, **kwargs):
            if '/mailFolders/Inbox/messages' in url:
                return self._response({'value': inbox})
            if '/mailFolders/SentItems/messages' in url:
                return self._response({'value': sent})
            if url.endswith('/attachments'):
                return self._response({'value': []})
            if f'/messages/{GRAPH_MSG_ID}' in url:
                return self._response(message)
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

    def test_the_fixture_actually_imports(self):
        """Guard for the two below: each passes trivially on an empty run."""
        self._sync()

        self.assertEqual(
            self.env['mail.message'].search_count([('message_id', '=', INTERNET_ID)]), 1,
            "the fixture must import exactly one mail, or this class proves nothing",
        )

    def test_inbox_sync_sends_nothing(self):
        before = self.snapshot(self.external_partner)
        self._sync()
        self.assertSyncSentNothing(before, self.external_partner)

    def test_sent_items_sync_sends_nothing(self):
        """The Juffermans direction. A colleague's own sent mail landed on a
        contact card, the contact was following that card, and it went back
        out."""
        before = self.snapshot(self.external_partner)
        self._sync(outgoing=True)
        self.assertSyncSentNothing(before, self.external_partner)


@tagged('pan_mail_pro', 'post_install', '-at_install')
class TestGmailSyncSendsNothing(OutlookProTestCase, QuietSyncMixin):
    """Gmail. Same invariant, different normalization on the way in."""

    GMAIL_ID = 'gmail_quiet_1'
    THREAD_ID = 'thread_quiet_1'
    GMAIL_GET = ('odoo.addons.pan_mail_pro.models.providers.google'
                 '.gmail_client.requests.get')

    def setUp(self):
        super().setUp()
        user = self.env['res.users'].with_context(**self.SILENT_CTX).create({
            'name': 'Gmail Sync User',
            'login': 'gmail_quiet@test.local',
            'email': 'gmail_quiet@test.local',
            'group_ids': [(6, 0, [self.env.ref('base.group_user').id])],
        })
        self.env['pan.mail.account'].create({
            'email': 'gmail_quiet@test.local',
            'provider': 'gmail',
            'user_id': user.id,
            'refresh_token': 'goog-refresh',
        })
        self.mailbox = self.env['x_microsoft.mailbox'].create({
            'email': 'gmail_quiet@test.local',
            'x_provider': 'gmail',
            'x_mailbox_type': 'personal',
            'x_owner_user_id': user.id,
            'x_sync_mode': 'all',
            'x_last_sync_date': '2026-01-01 00:00:00',
        })

    def _message(self, label='INBOX'):
        body = base64.urlsafe_b64encode(b'<p>Where is my order?</p>').decode()
        return {
            'id': self.GMAIL_ID,
            'threadId': self.THREAD_ID,
            'labelIds': [label],
            'internalDate': '1769941800000',
            'payload': {
                'mimeType': 'text/html',
                'body': {'data': body},
                'headers': [
                    {'name': 'Message-Id', 'value': INTERNET_ID},
                    {'name': 'Subject', 'value': 'Question about my order'},
                    {'name': 'From', 'value': f'External Customer <{CUSTOMER}>'},
                    {'name': 'To', 'value': 'Gmail Sync <gmail_quiet@test.local>'},
                ],
            },
        }

    @staticmethod
    def _response(payload):
        resp = MagicMock()
        resp.status_code = 200
        resp.raise_for_status.return_value = None
        resp.json.return_value = payload
        return resp

    def _sync(self, label='INBOX'):
        message = self._message(label)

        def fake_get(url, headers=None, params=None, timeout=None, **kwargs):
            if '/messages' in url and not url.rstrip('/').endswith(self.GMAIL_ID):
                wanted = (params or {}).get('labelIds')
                listed = [{'id': self.GMAIL_ID, 'threadId': self.THREAD_ID}]
                if wanted in (label, [label], None):
                    return self._response({'messages': listed})
                return self._response({'messages': []})
            return self._response(message)

        processor = self.env['microsoft.incoming.mail.processor']
        with patch.object(
            type(self.env['google.gmail.client']), 'get_valid_token',
            autospec=True, return_value='fake-google-token',
        ), patch(self.GMAIL_GET, side_effect=fake_get):
            processor._process_mailbox(self.mailbox)

    def test_gmail_sync_sends_nothing(self):
        before = self.snapshot(self.external_partner)
        self._sync()
        self.assertSyncSentNothing(before, self.external_partner)

    def test_gmail_sent_label_sends_nothing(self):
        before = self.snapshot(self.external_partner)
        self._sync(label='SENT')
        self.assertSyncSentNothing(before, self.external_partner)


@tagged('pan_mail_pro', 'post_install', '-at_install')
class TestImapSyncSendsNothing(OutlookProTestCase, QuietSyncMixin):
    """IMAP/SMTP. No OAuth, no owner, and a shared mailbox by default -- the
    shape that broke every check written against a Microsoft assumption.

    Entered at `_process_message` with the client's fetch stubbed, rather than
    faking imaplib a second time. The boundary is armed in `_process_message`,
    so that is the seam worth pinning; `test_imap_provider.py` owns the socket
    layer.
    """

    def setUp(self):
        super().setUp()
        self.env['pan.mail.account'].create({
            'email': 'imap_quiet@company.test',
            'provider': 'imap',
            'user_id': False,
            'imap_host': 'imap.example.test', 'imap_port': 993, 'imap_security': 'ssl',
            'smtp_host': 'smtp.example.test', 'smtp_port': 465, 'smtp_security': 'ssl',
            'password': 'hunter2',
        })
        self.mailbox = self.env['x_microsoft.mailbox'].create({
            'email': 'imap_quiet@company.test',
            'x_provider': 'imap',
            'x_mailbox_type': 'shared',
            'x_sync_mode': 'all',
            'x_last_sync_date': '2026-01-01 00:00:00',
        })

    def _normalized(self, folder):
        return {
            'provider_message_id': 'INBOX:1:42',
            'message_id': INTERNET_ID,
            'thread_id': CONV_ID,
            'subject': 'Question about my order',
            'from': {'email': CUSTOMER, 'name': 'External Customer'},
            'to': [{'email': 'imap_quiet@company.test', 'name': 'Shared'}],
            'cc': [],
            'date': '2026-02-01 10:30:00',
            'body_html': '<p>Where is it?</p>',
            'body_is_html': True,
            'has_attachments': False,
            'headers': {'message-id': INTERNET_ID},
            'is_read': True,
        }

    def _process(self, folder):
        message = self._normalized(folder)
        processor = self.env['microsoft.incoming.mail.processor']
        client = type(self.env['imap.smtp.client'])
        with patch.object(client, 'get_message', autospec=True, return_value=message), \
                patch.object(client, 'get_message_attachments', autospec=True, return_value=[]):
            processor._process_message(self.mailbox, message, folder)

    def test_imap_inbox_sends_nothing(self):
        before = self.snapshot(self.external_partner)
        self._process(FOLDER_INBOX)
        self.assertSyncSentNothing(before, self.external_partner)

    def test_imap_sent_sends_nothing(self):
        before = self.snapshot(self.external_partner)
        self._process(FOLDER_SENT)
        self.assertSyncSentNothing(before, self.external_partner)
