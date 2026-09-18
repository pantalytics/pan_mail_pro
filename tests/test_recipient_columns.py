# -*- coding: utf-8 -*-
"""Who else was on the mail, and what that must never turn into.

The Inbox shows a recipients line. Its only source used to be
`mail.message.partner_ids`, which an imported mail never has -- the sync
notifies nobody, so it writes no recipient rows. The line was therefore empty
on exactly the mail the screen exists to read, and no test could see it because
every Python test reads the field rather than the screen.

`x_email_to` / `x_email_cc` answer it in the only shape that is safe: text.
This file pins both halves of that -- that the columns are filled, and that
filling them creates no partner, no follower and no notification. The second
half is the one that would rot quietly: a later "let's resolve those addresses
to contacts" is one line of code and a privacy incident.
"""
from unittest.mock import patch

from odoo.tests import tagged

from ..models.mail_provider_client import FOLDER_INBOX, FOLDER_SENT
from .common import MailProTestCase

CUSTOMER = 'customer@example.com'
COLLEAGUE = 'collega@gate-fixture.test'
STRANGER = 'jan@anderbedrijf.test'
INTERNET_ID = '<recipients-001@example.com>'
REPLY_ID = '<recipients-002@company.test>'


@tagged('pan_mail_pro', 'post_install', '-at_install')
class TestRecipientColumns(MailProTestCase):

    def setUp(self):
        super().setUp()
        self.processor = self.env['pan.mail.fetcher']
        self.mailbox = self.personal_mailbox
        self.mailbox.write({'sync_level': 'everyone'})

    def _normalized(self, **overrides):
        message = {
            'provider_message_id': 'X1',
            'message_id': INTERNET_ID,
            'subject': 'Question about my order',
            'from': {'email': CUSTOMER, 'name': 'External Customer'},
            'to': [{'email': self.mailbox.email, 'name': 'Sales'}],
            'cc': [{'email': COLLEAGUE, 'name': 'Collega'},
                   {'email': STRANGER, 'name': 'Jan'}],
            'date': '2026-02-01 10:30:00',
            'body_html': '<p>Where is it?</p>',
            'body_is_html': True,
            'has_attachments': False,
            'headers': {'message-id': INTERNET_ID},
        }
        message.update(overrides)
        return message

    def _process(self, folder=FOLDER_INBOX, **overrides):
        message = self._normalized(**overrides)
        client = type(self.env['microsoft.graph.client'])
        with patch.object(client, 'get_message', autospec=True, return_value=message), \
                patch.object(client, 'get_message_attachments', autospec=True,
                             return_value=[]):
            self.processor._process_message(self.mailbox, message, folder)
        return self.env['mail.message'].search(
            [('message_id', '=', message['message_id'])], limit=1)

    # ------------------------------------------------------------------ #
    # Filled
    # ------------------------------------------------------------------ #
    def test_an_imported_mail_carries_its_to_and_cc(self):
        message = self._process()
        self.assertTrue(message, "the mail should have been imported")
        self.assertEqual(message.x_email_to, self.mailbox.email)
        self.assertEqual(message.x_email_cc, '%s, %s' % (COLLEAGUE, STRANGER))

    def test_a_mail_without_cc_leaves_the_column_empty(self):
        message = self._process(cc=[])
        self.assertEqual(message.x_email_cc, '')

    def test_addresses_only_no_display_names(self):
        """A quoted display name in a char column is a parsing problem for
        whoever reads it back. Only the addresses go in."""
        message = self._process()
        self.assertNotIn('Collega', message.x_email_cc)
        self.assertNotIn('<', message.x_email_cc)

    def test_a_sent_item_carries_the_customer_as_recipient(self):
        """On the way out the To is the customer, which is the half of the line
        the Inbox shows for a sent item.

        The fixture has to be a reply: `_gate_wanted` only lets a sent item in
        when it answers something Odoo already holds, so a fresh outgoing mail
        never reaches the write at all.
        """
        self._process()
        message = self._process(
            folder=FOLDER_SENT,
            message_id=REPLY_ID,
            provider_message_id='X2',
            headers={'message-id': REPLY_ID, 'in-reply-to': INTERNET_ID},
            **{'from': {'email': self.mailbox.email, 'name': 'Sales'},
               'to': [{'email': CUSTOMER, 'name': 'External Customer'}],
               'cc': []},
        )
        self.assertTrue(message, "the sent item should have been imported")
        self.assertEqual(message.x_email_to, CUSTOMER)
        self.assertEqual(message.x_email_cc, '')

    # ------------------------------------------------------------------ #
    # And what it must never become
    # ------------------------------------------------------------------ #
    def test_a_cc_address_becomes_no_contact(self):
        """Resolving CC to partners builds a contact database out of other
        companies' colleagues. The addresses stay text."""
        self._process()
        for address in (COLLEAGUE, STRANGER):
            self.assertFalse(
                self.env['res.partner'].search([('email', '=ilike', address)]),
                "%s was turned into a contact" % address,
            )

    def test_a_cc_address_becomes_no_follower(self):
        """The follower is the mechanism that would mail the whole of the rest
        of this record's correspondence to somebody who was copied once."""
        message = self._process()
        followers = self.env['mail.followers'].search([
            ('res_model', '=', message.model), ('res_id', '=', message.res_id),
        ])
        for address in (COLLEAGUE, STRANGER):
            self.assertNotIn(
                address, followers.partner_id.mapped('email'),
                "%s was subscribed to the record" % address,
            )

    def test_the_columns_are_not_recipients(self):
        """Filling them must not put a notification row anywhere: the import
        boundary still says this mail reaches nobody through Odoo."""
        message = self._process()
        self.assertFalse(message.partner_ids)
        self.assertFalse(self.env['mail.notification'].search_count(
            [('mail_message_id', '=', message.id)]))

    # ------------------------------------------------------------------ #
    # What the Inbox reads
    # ------------------------------------------------------------------ #
    def test_the_conversation_row_reads_the_columns(self):
        message = self._process()
        row = self.env['pan.mail.conversation']._message_row(message)
        self.assertEqual(row['recipients'], self.mailbox.email)
        self.assertEqual(row['cc'], '%s, %s' % (COLLEAGUE, STRANGER))

    def test_a_message_without_the_columns_falls_back_to_partner_ids(self):
        """Mail written from the chatter, and everything that predates the two
        columns, still shows a recipients line."""
        message = self.env['mail.message'].create({
            'model': 'res.partner',
            'res_id': self.external_partner.id,
            'message_type': 'email',
            'subject': 'Posted from the chatter',
            'partner_ids': [(6, 0, [self.external_partner.id])],
        })
        row = self.env['pan.mail.conversation']._message_row(message)
        self.assertEqual(row['recipients'], self.external_partner.email)
        self.assertEqual(row['cc'], '')
