# -*- coding: utf-8 -*-
"""
A neutralized database must not talk to a provider.

Odoo neutralizes a staging copy by deactivating every `ir_mail_server` and
inserting an invalid one. Mail Pro never touches `ir_mail_server`, so that
protection misses it entirely and a restored dump mails real customers with the
real credentials. `data/neutralize.sql` takes those credentials away; these
tests cover the runtime half, which holds even when a mailbox is put back.
"""
import os
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

    def test_outgoing_mail_is_recorded_but_never_routed(self):
        """It reads as sent, and no mailbox or provider was asked anything.

        Staging is where the *flow* is tested, so the mail has to land on the
        record like any other. It must still not reach a provider -- which is
        why the assertion is on `_send_one` not being called at all, rather
        than on a client that would have refused one layer further down.
        """
        mail = self.env['mail.mail'].create({
            'subject': 'Staging must not send this',
            'body_html': '<p>Hi</p>',
            'email_to': 'customer@example.com',
            'author_id': self.salesperson.partner_id.id,
        })

        with patch.object(type(mail), '_send_one', autospec=True) as send_one:
            error = send_and_capture(mail)

        self.assertIsNone(error, 'Staging must not interrupt the sender')
        send_one.assert_not_called()
        self.assertEqual(mail.state, 'sent')
        self.assertIn('neutralized', mail.failure_reason)

    def test_the_chatter_keeps_the_message(self):
        """The whole point: the email is on the record as if it had gone out.

        Refusing with a `UserError` used to unwind the `message_post` that
        created it, so the tester got a dialog where the test was supposed to
        be. The recipient row has to read `sent` too -- it outlives the
        `mail.mail` and is what the chatter renders.
        """
        message = self.external_partner.with_user(self.salesperson).sudo().message_post(
            body='<p>Testing the flow</p>',
            subject='Staging',
            partner_ids=[self.external_partner.id],
            message_type='comment',
            subtype_xmlid='mail.mt_comment',
        )

        self.assertTrue(message.exists(), 'The chatter entry was rolled back')
        notification = self.env['mail.notification'].search([
            ('mail_message_id', '=', message.id),
            ('res_partner_id', '=', self.external_partner.id),
        ])
        self.assertEqual(notification.notification_status, 'sent')

    def test_the_sender_is_told(self):
        """A toast, because the usual way of saying it would roll back the post."""
        mail = self.env['mail.mail'].create({
            'subject': 'Staging must not send this',
            'body_html': '<p>Hi</p>',
            'email_to': 'customer@example.com',
            'author_id': self.salesperson.partner_id.id,
        })

        with patch.object(type(self.env['res.partner']), '_bus_send') as bus_send:
            mail.send()

        bus_send.assert_called_once()
        self.assertEqual(bus_send.call_args[0][0], 'simple_notification')
        self.assertIn('neutralized', bus_send.call_args[0][1]['message'])

    def test_the_mail_queue_tells_nobody(self):
        """The cron sends on nobody's behalf, so it raises no toast."""
        mail = self.env['mail.mail'].create({
            'subject': 'Queued before the restore',
            'body_html': '<p>Hi</p>',
            'email_to': 'customer@example.com',
            'author_id': self.salesperson.partner_id.id,
        })

        with patch.object(type(self.env['res.partner']), '_bus_send') as bus_send:
            mail.send(auto_commit=True)

        bus_send.assert_not_called()
        self.assertEqual(mail.state, 'sent')

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
