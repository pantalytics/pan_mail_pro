# -*- coding: utf-8 -*-
"""The one invariant the outbound side of the sync has to hold.

Thirty test files covered where imported mail *lands* and none of them covered
what the sync *sends*. That is why a no-op safeguard survived six months in
production code carrying a comment describing behaviour it never had: nothing
looked. This file looks.

The invariant is deliberately absolute, because an imported mail has already
reached its recipients through the provider:

    a message the sync created notifies nobody.

No `mail.notification`, no `mail.mail`, no new follower. Not "fewer people",
not "skip whoever was already on the original" -- nobody. A rule with no
legitimate exception is a rule a test can pin, which is the whole reason for
stating it that way.

The last test is the control: an ordinary human message on the same record must
still notify normally. Without it this file would also pass if the override
were broad enough to break Odoo's mail entirely.

See ARCHITECTURE.md §9.10.
"""
from unittest.mock import MagicMock, patch

from odoo.tests import tagged

from .common import OutlookProTestCase

MSG_ID = 'AAMkAGI2_fake_graph_id'
INTERNET_ID = '<quiet-sync-001@example.com>'
CONV_ID = 'CONV_QUIET_001'


@tagged('pan_mail_pro', 'post_install', '-at_install')
class TestSyncSendsNothing(OutlookProTestCase):

    def setUp(self):
        super().setUp()
        self.mailbox = self.personal_mailbox
        self.mailbox.write({
            'x_sync_mode': 'all',
            'x_last_sync_date': '2026-01-01 00:00:00',
        })

    # ------------------------------------------------------------------ #
    # Graph fakes — same shape as test_incoming_sync, kept local so a change
    # there cannot quietly weaken the invariant asserted here.
    # ------------------------------------------------------------------ #
    def _full_message(self):
        return {
            'id': MSG_ID,
            'internetMessageId': INTERNET_ID,
            'conversationId': CONV_ID,
            'subject': 'Question about my order',
            'from': {'emailAddress': {'name': 'External Customer',
                                      'address': 'customer@example.com'}},
            'toRecipients': [{'emailAddress': {'name': 'Sales',
                                               'address': 'sales@company.test'}}],
            'ccRecipients': [],
            'receivedDateTime': '2026-02-01T10:30:00Z',
            'hasAttachments': False,
            'internetMessageHeaders': [
                {'name': 'Message-ID', 'value': INTERNET_ID},
            ],
            'body': {'contentType': 'html', 'content': '<p>Where is it?</p>'},
        }

    @staticmethod
    def _response(payload):
        resp = MagicMock()
        resp.status_code = 200
        resp.raise_for_status.return_value = None
        resp.json.return_value = payload
        return resp

    def _sync(self):
        preview = self._full_message()
        full = self._full_message()

        def fake_get(url, headers=None, params=None, timeout=None, **kwargs):
            if '/mailFolders/Inbox/messages' in url:
                return self._response({'value': [preview]})
            if '/mailFolders/SentItems/messages' in url:
                return self._response({'value': []})
            if url.endswith('/attachments'):
                return self._response({'value': []})
            if f'/messages/{MSG_ID}' in url:
                return self._response(full)
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

    def _imported_message(self):
        return self.env['mail.message'].search([
            ('message_id', '=', INTERNET_ID),
        ])

    # ------------------------------------------------------------------ #
    # The invariant
    # ------------------------------------------------------------------ #
    def test_sync_actually_ran(self):
        """Guard for the three tests below: each of them passes trivially if
        nothing was imported, so prove the pipeline did its work first."""
        self._sync()

        message = self._imported_message()
        self.assertEqual(len(message), 1, "the fixture must import exactly one mail")
        self.assertEqual(
            message.x_mailbox_id, self.mailbox,
            "x_mailbox_id must be set by message_post, not by a later write — "
            "it is what marks the message as imported while it is being notified",
        )

    def test_sync_creates_no_notifications(self):
        before = self.env['mail.notification'].search_count([])

        self._sync()

        message = self._imported_message()
        self.assertFalse(
            message.notification_ids,
            "an imported mail already reached its recipients through the provider",
        )
        self.assertEqual(
            self.env['mail.notification'].search_count([]), before,
            "the sync must not add a notification row anywhere",
        )

    def test_sync_creates_no_outgoing_mail(self):
        before = self.env['mail.mail'].search_count([])

        self._sync()

        self.assertEqual(
            self.env['mail.mail'].search_count([]), before,
            "the sync must never put an envelope in the queue",
        )

    def test_sync_creates_no_followers(self):
        """The mechanism behind the Juffermans incident.

        `message_post` subscribes the author unless told otherwise, and the
        contact-chatter path posts on the sender's own record with that sender
        as author — so without `mail_create_nosubscribe` a contact ends up
        following itself and receiving its own correspondence back.
        """
        partner = self.external_partner
        before = self.env['mail.followers'].search_count([
            ('res_model', '=', 'res.partner'),
            ('res_id', '=', partner.id),
        ])

        self._sync()

        self.assertEqual(
            self.env['mail.followers'].search_count([
                ('res_model', '=', 'res.partner'),
                ('res_id', '=', partner.id),
            ]),
            before,
            "a follower is a human act; the sync never creates one",
        )

    # ------------------------------------------------------------------ #
    # Control
    # ------------------------------------------------------------------ #
    def test_a_human_message_still_notifies(self):
        """Without this, the file would also pass if the override killed
        notification for everything rather than for imported mail only."""
        partner = self.external_partner
        partner.sudo().message_subscribe(partner_ids=[self.company_partner.id])

        message = partner.message_post(
            body='<p>Written by a person.</p>',
            subject='Human message',
            message_type='comment',
            subtype_xmlid='mail.mt_comment',
        )

        self.assertTrue(
            message.notification_ids,
            "ordinary Odoo notification must keep working",
        )
