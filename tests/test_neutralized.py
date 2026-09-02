# -*- coding: utf-8 -*-
"""
A neutralized database must not talk to a provider.

Odoo neutralizes a staging copy by deactivating every `ir_mail_server` and
inserting an invalid one. Mail Pro never touches `ir_mail_server`, so that
protection misses it entirely and a restored dump mails real customers with the
real credentials. `data/neutralize.sql` takes those credentials away; these
tests cover the runtime half, which holds even when a mailbox is put back.
"""
from unittest.mock import patch

from odoo.exceptions import UserError
from odoo.tests import tagged

from .common import OutlookProTestCase, send_and_capture


@tagged('pan_mail_pro', 'post_install', '-at_install')
class TestNeutralizedDatabase(OutlookProTestCase):

    def setUp(self):
        super().setUp()
        self.env['ir.config_parameter'].sudo().set_param('database.is_neutralized', 'True')

    def test_outgoing_mail_is_not_sent(self):
        """The mail is refused with a readable reason, not delivered."""
        mail = self.env['mail.mail'].create({
            'subject': 'Staging must not send this',
            'body_html': '<p>Hi</p>',
            'email_to': 'customer@example.com',
            'author_id': self.salesperson.partner_id.id,
        })

        error = send_and_capture(mail)

        self.assertIsNotNone(error, 'A neutralized database sent an email')
        self.assertIn('neutralized', str(error))
        self.assertNotEqual(mail.state, 'sent')

    def test_incoming_sync_cron_does_nothing(self):
        """The cron returns before it touches a mailbox."""
        processor = self.env['microsoft.incoming.mail.processor']
        with patch.object(
            type(processor), '_process_mailbox', autospec=True
        ) as process:
            processor._cron_fetch_incoming_mail()
        process.assert_not_called()

    def test_manual_sync_is_refused(self):
        """"Sync Now" says why instead of quietly reading live mail."""
        with self.assertRaises(UserError) as caught:
            self.shared_mailbox.action_sync_now()
        self.assertIn('neutralized', str(caught.exception))
