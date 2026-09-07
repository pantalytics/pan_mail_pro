# -*- coding: utf-8 -*-
"""The core loop: Odoo sends from a record, the answer comes back to that record.

Everything else this module does is an extension of this. A salesperson writes
from the chatter of a lead, the customer replies in their own mail client, and
the reply has to appear under the question. If that does not work, nothing else
being configurable matters.

It is also the one path that must need no configuration. Since 19.0.7.5.0 a
reply threads onto its record whatever the mailbox's sync switches say: the
switches govern email that starts a *new* conversation, and a reply to something
Odoo sent is not that. These tests pin the behaviour with every switch off,
which is what a mailbox looks like on the day it is created.

Driven through the real seams -- `mail.mail.send()` out, `_process_mailbox()`
back in -- with only HTTP faked, so the ref index is written by the code that
writes it in production rather than by the fixture.
"""
from unittest.mock import MagicMock, patch

from odoo.tests import tagged

from .common import MailProTestCase

GRAPH_MSG_ID = 'AAMkAGI2_reply_graph_id'
SENT_MESSAGE_ID = '<odoo-outgoing-001@company.test>'
REPLY_MESSAGE_ID = '<customer-reply-001@example.com>'
STRANGER_MESSAGE_ID = '<stranger-001@example.com>'


@tagged('pan_mail_pro', 'post_install', '-at_install')
class TestReplySync(MailProTestCase):

    def setUp(self):
        super().setUp()
        self.mailbox = self.personal_mailbox
        # The state a mailbox is in the moment it is created: nothing switched
        # on. The reply still has to arrive, so every assertion below runs
        # against the out-of-the-box configuration.
        self.mailbox.write({
            'sync_received': False,
            'sync_sent': False,
            'last_sync_date': '2026-01-01 00:00:00',
        })
        self.lead = self.env['crm.lead'].with_context(**self.SILENT_CTX).create({
            'name': 'Juffermans - new roof',
            'partner_id': self.external_partner.id,
            'email_from': self.external_partner.email,
        })

    # ------------------------------------------------------------------ #
    # Outgoing: send from the record's chatter
    # ------------------------------------------------------------------ #
    def _send_from_chatter(self):
        """Post on the lead and send it, the way the composer does.

        Returns the `mail.message` the chatter kept. Sending is what writes the
        outgoing Message-ID into the ref index, which is the only reason the
        reply below can be recognised.
        """
        message = self.lead.with_context(**self.SILENT_CTX).message_post(
            body='<p>Here is our quote.</p>',
            subject='Your roof',
            message_type='email',
            subtype_xmlid='mail.mt_comment',
        )
        mail = self.env['mail.mail'].create({
            'subject': 'Your roof',
            'body_html': '<p>Here is our quote.</p>',
            'email_to': self.external_partner.email,
            'model': 'crm.lead',
            'res_id': self.lead.id,
            'mail_message_id': message.id,
            'x_send_from_mailbox_id': self.mailbox.id,
        })
        with self.mock_graph(message_id=SENT_MESSAGE_ID):
            mail.send()
        return message

    # ------------------------------------------------------------------ #
    # Incoming: the reply comes back through the Inbox
    # ------------------------------------------------------------------ #
    def _inbox_message(self, message_id, in_reply_to=None, subject='Re: Your roof',
                       sender=None):
        sender = sender or {'name': 'External Customer',
                            'address': self.external_partner.email}
        headers = [{'name': 'Message-ID', 'value': message_id}]
        if in_reply_to:
            headers.append({'name': 'In-Reply-To', 'value': in_reply_to})
            headers.append({'name': 'References', 'value': in_reply_to})
        return {
            'id': GRAPH_MSG_ID,
            'internetMessageId': message_id,
            'subject': subject,
            'from': {'emailAddress': sender},
            'toRecipients': [{'emailAddress': {'name': 'Sales',
                                               'address': self.mailbox.email}}],
            'ccRecipients': [],
            'receivedDateTime': '2026-02-01T10:30:00Z',
            'hasAttachments': False,
            'conversationId': 'CONV_REPLY_001',
            'internetMessageHeaders': headers,
            'body': {'contentType': 'html', 'content': '<p>Looks good.</p>'},
        }

    @staticmethod
    def _response(payload):
        resp = MagicMock()
        resp.status_code = 200
        resp.raise_for_status.return_value = None
        resp.json.return_value = payload
        return resp

    def _sync(self, message):
        """Run the incoming sync with `message` sitting in the Inbox."""
        def fake_get(url, headers=None, params=None, timeout=None, **kwargs):
            if '/mailFolders/Inbox/messages' in url:
                return self._response({'value': [message]})
            if '/mailFolders/SentItems/messages' in url:
                return self._response({'value': []})
            if url.endswith('/attachments'):
                return self._response({'value': []})
            if f'/messages/{GRAPH_MSG_ID}' in url:
                return self._response(message)
            return self._response({})

        with patch.object(
            type(self.env['microsoft.graph.client']), 'get_valid_token',
            autospec=True, return_value='fake-bearer-token',
        ), patch(
            'odoo.addons.pan_mail_pro.models.providers.microsoft.graph_client.requests.get',
            side_effect=fake_get,
        ):
            self.env['pan.mail.fetcher']._process_mailbox(self.mailbox)

    def _messages_on_lead(self):
        return self.env['mail.message'].search([
            ('model', '=', 'crm.lead'),
            ('res_id', '=', self.lead.id),
            ('message_type', '=', 'email'),
        ])

    # ------------------------------------------------------------------ #
    # The loop
    # ------------------------------------------------------------------ #
    def test_a_reply_lands_on_the_record_it_answers(self):
        """The whole product in one test."""
        sent = self._send_from_chatter()

        self._sync(self._inbox_message(REPLY_MESSAGE_ID, in_reply_to=SENT_MESSAGE_ID))

        replies = self._messages_on_lead() - sent
        self.assertEqual(
            len(replies), 1,
            "the customer's reply must appear on the lead it answers",
        )
        self.assertIn('Looks good', replies.body)

    def test_the_reply_needs_no_setting_switched_on(self):
        """Stated as its own assertion because it is the promise the form makes:
        "Replies to email sent from Odoo always appear on the record they belong
        to." A test that only passes with sync switched on would let that
        sentence become false without anything failing."""
        self.assertFalse(self.mailbox.sync_received)
        self.assertFalse(self.mailbox.sync_sent)
        self.assertFalse(self.mailbox._syncs_more_than_replies())

        sent = self._send_from_chatter()
        self._sync(self._inbox_message(REPLY_MESSAGE_ID, in_reply_to=SENT_MESSAGE_ID))

        self.assertTrue(self._messages_on_lead() - sent)

    def test_a_reply_from_an_address_we_do_not_know_still_lands(self):
        """A colleague of the customer answers from their own address. It is
        still the answer to our question, so the narrow scope must not catch it
        -- the scope governs new conversations, not this one."""
        sent = self._send_from_chatter()

        self._sync(self._inbox_message(
            REPLY_MESSAGE_ID,
            in_reply_to=SENT_MESSAGE_ID,
            sender={'name': 'Colleague', 'address': 'colleague@example.com'},
        ))

        self.assertTrue(
            self._messages_on_lead() - sent,
            "a reply threads on its chain, not on who happened to send it",
        )

    def test_an_unrelated_email_does_not_come_in(self):
        """The other half of the promise. With nothing switched on, a mail that
        starts a conversation stays in the mailbox -- otherwise "replies only"
        would quietly mean "everything"."""
        self._send_from_chatter()
        before = self.env['mail.message'].search_count([])

        self._sync(self._inbox_message(
            STRANGER_MESSAGE_ID,
            subject='Buy cheap roof tiles',
            sender={'name': 'Stranger', 'address': 'stranger@example.com'},
        ))

        self.assertEqual(
            self.env['mail.message'].search_count([]), before,
            "an unrelated email must leave no trace anywhere",
        )

    def test_the_same_unrelated_email_comes_in_once_asked_for(self):
        """And the switch has to actually do something, or the test above is
        passing for the wrong reason."""
        self.mailbox.write({'sync_received': True, 'sync_received_scope': 'all'})
        before = self.env['mail.message'].search_count([])

        self._sync(self._inbox_message(
            STRANGER_MESSAGE_ID,
            subject='Buy cheap roof tiles',
            sender={'name': 'Stranger', 'address': 'stranger@example.com'},
        ))

        self.assertGreater(self.env['mail.message'].search_count([]), before)

    def test_the_reply_is_not_imported_twice(self):
        """The cron overlaps its fetch window on purpose, so the same reply is
        offered again on the next run."""
        sent = self._send_from_chatter()
        reply = self._inbox_message(REPLY_MESSAGE_ID, in_reply_to=SENT_MESSAGE_ID)

        self._sync(reply)
        self.mailbox.write({'last_sync_date': '2026-01-01 00:00:00'})
        self._sync(reply)

        self.assertEqual(len(self._messages_on_lead() - sent), 1)
