# -*- coding: utf-8 -*-
"""
A neutralized database must not talk to a provider.

Odoo neutralizes a staging copy by deactivating every `ir_mail_server` and
inserting an invalid one. Mail Pro never touches `ir_mail_server`, so that
protection misses it entirely and a restored dump mails real customers with the
real credentials. `data/neutralize.sql` takes those credentials away; these
tests cover the runtime half, which holds even when a mailbox is put back.
Outgoing mail still leaves, through Odoo's own SMTP path, so the host's
staging catcher (MailHog, odoo.sh's Mails tab) shows what was sent.
"""
import os
from contextlib import ExitStack
from unittest.mock import MagicMock, patch

from odoo.exceptions import UserError
from odoo.tests import tagged

from ..models import encryption_utils
from .common import MailProTestCase, send_and_capture


@tagged('pan_mail_pro', 'post_install', '-at_install')
class TestNeutralizedDatabase(MailProTestCase):

    def setUp(self):
        super().setUp()
        self.env['ir.config_parameter'].sudo().set_param('database.is_neutralized', 'True')

    def _mail(self):
        return self.env['mail.mail'].create({
            'subject': 'Staging must not reach a provider',
            'body_html': '<p>Hi</p>',
            'email_to': 'customer@example.com',
            'author_id': self.salesperson.partner_id.id,
        })

    def _smtp_and_providers(self, mail):
        """Odoo's SMTP send, and the one door to every provider, both watched.

        The SMTP connect is stood in for as well: Odoo opens the session before
        it calls `send_email`, and the test runner has no server to open it to.
        `_disable_send` is Odoo's own test-mode switch, which skips the send
        "unless monkeypatched".
        """
        mail_server = type(self.env['ir.mail_server'])
        stack = ExitStack()
        stack.enter_context(patch.object(mail_server, '_disable_send', return_value=False))
        stack.enter_context(patch.object(mail_server, '_connect__', return_value=MagicMock()))
        send_one = stack.enter_context(patch.object(type(mail), '_send_one', autospec=True))
        send_email = stack.enter_context(patch.object(
            mail_server, 'send_email', autospec=True, return_value='<staging@example.com>'))
        return stack, send_one, send_email

    def test_outgoing_mail_goes_to_odoos_own_smtp(self):
        """The host's staging catcher gets it, and no provider is asked.

        Staging is where the *flow* is tested, and every host already catches
        SMTP there (Cloudpepper's MailHog, odoo.sh's Mails tab). So the mail
        takes Odoo's own path, the one neutralization protects. The provider
        assertion is on `_send_one` not being called at all -- Graph, Gmail
        and the module's own IMAP/SMTP client all sit behind it -- rather than
        on a client that would have refused one layer further down.
        """
        mail = self._mail()
        watched, send_one, send_email = self._smtp_and_providers(mail)

        with watched:
            error = send_and_capture(mail)

        self.assertIsNone(error, 'Staging must not interrupt the sender')
        send_one.assert_not_called()
        send_email.assert_called_once()

    def test_a_mailbox_put_back_still_does_not_send(self):
        """Reactivating a mailbox on staging does not reopen the provider door.

        `data/neutralize.sql` archives every mailbox; an admin testing setup
        might switch one back on. The neutralized check sits after the
        mailbox count, so it is the flag that decides, not the mailboxes.
        """
        self.env['pan.mail.mailbox'].sudo().with_context(active_test=False).search(
            []).write({'active': True})
        mail = self._mail()
        watched, send_one, send_email = self._smtp_and_providers(mail)

        with watched:
            send_and_capture(mail)

        send_one.assert_not_called()
        send_email.assert_called_once()

    def test_the_chatter_keeps_the_message(self):
        """A post on staging lands on the record like any other.

        Refusing with a `UserError` used to unwind the `message_post` that
        created it, so the tester got a dialog where the test was supposed to
        be. Odoo's own send records a failed delivery on the mail instead of
        raising at the poster, so the entry stays either way.
        """
        watched, _send_one, _send_email = self._smtp_and_providers(self.env['mail.mail'])
        with watched:
            message = self.external_partner.with_user(self.salesperson).sudo().message_post(
                body='<p>Testing the flow</p>',
                subject='Staging',
                partner_ids=[self.external_partner.id],
                message_type='comment',
                subtype_xmlid='mail.mt_comment',
            )

        self.assertTrue(message.exists(), 'The chatter entry was rolled back')

    def test_the_composer_says_where_the_mail_goes(self):
        """Send From is filled in with the test inbox, and says why it is fixed.

        Every mailbox is archived on a copy, so the real dropdown would be an
        empty list and the connect warning would ask for something that cannot
        work here. The arch is checked too, so the flag cannot stop meaning
        something on screen.
        """
        composer = self.env['mail.compose.message'].with_user(self.salesperson).new({})
        self.assertTrue(composer.x_on_test_instance)
        self.assertEqual(composer.x_test_send_from, 'Test inbox')

        arch = self.env.ref('pan_mail_pro.view_mail_compose_message_form_inherit').arch
        self.assertIn('subtype_is_log or x_on_test_instance', arch)
        self.assertIn('Every email goes to the test inbox', arch)

        self.env['ir.config_parameter'].sudo().set_param('database.is_neutralized', False)
        self.assertFalse(
            self.env['mail.compose.message'].with_user(self.salesperson).new({}).x_on_test_instance)

    def test_neutralize_sql_takes_every_credential_away(self):
        """The at-rest half. The SQL names columns by hand and is found by
        path, so nothing but this notices a rename or a column it forgot."""
        provider = self.env['pan.mail.provider'].sudo().search([], limit=1) \
            or self.env['pan.mail.provider'].sudo().create({'provider': 'outlook'})
        provider.client_secret = 'app-secret'
        self.assertTrue(provider.client_secret_encrypted)
        account = self.env['pan.mail.account'].sudo().search(
            [('refresh_token_encrypted', '!=', False)], limit=1)
        self.assertTrue(account, "fixture must hold a connected account")

        self.env.flush_all()
        with open(os.path.join(os.path.dirname(__file__), '..', 'data', 'neutralize.sql')) as f:
            self.env.cr.execute(f.read())
        provider.invalidate_recordset()
        account.invalidate_recordset()

        self.assertFalse(provider.client_secret_encrypted)
        self.assertFalse(account.refresh_token_encrypted)
        self.assertFalse(account.access_token_encrypted)
        self.assertFalse(account.password_encrypted)
        self.assertFalse(account.connected)

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
