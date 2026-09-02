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

from ..models import encryption_utils
from .common import MailProTestCase, send_and_capture


@tagged('pan_mail_pro', 'post_install', '-at_install')
class TestNeutralizedDatabase(MailProTestCase):

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
        processor = self.env['pan.mail.fetcher']
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

    def test_no_credential_can_be_decrypted(self):
        """The gate under all three: no credential is readable at all.

        Every credential the module owns -- OAuth access and refresh tokens,
        IMAP/SMTP passwords, both providers' client secrets -- is read through
        `decrypt_value`. A call site that forgets to ask whether the database is
        neutralized still cannot reach a provider.
        """
        ciphertext = encryption_utils.encrypt_value(self.env, 'a-real-refresh-token')
        self.assertTrue(ciphertext, 'fixture did not encrypt')

        self.assertFalse(encryption_utils.decrypt_value(self.env, ciphertext))

    def test_account_hands_out_no_tokens(self):
        """An account holding live credentials reads as empty."""
        account = self.env['pan.mail.account'].sudo().create({
            'email': 'live@company.com',
            'provider': 'outlook',
            'refresh_token': 'a-real-refresh-token',
            'access_token': 'a-real-access-token',
        })
        account.invalidate_recordset()

        self.assertFalse(account.refresh_token)
        self.assertFalse(account.access_token)
        # The ciphertext is untouched: neutralization refuses to read a
        # credential, it does not destroy one. Removing it is neutralize.sql's
        # job, and it runs once.
        self.assertTrue(account.refresh_token_encrypted)
