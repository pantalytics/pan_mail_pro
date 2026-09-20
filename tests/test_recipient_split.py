# -*- coding: utf-8 -*-
"""One provider send per recipient partner (#96), and what happens after a send.

Odoo's own SMTP path sends one message per notified partner, so two customers
following the same lead never see each other's address. The provider clients
read `recipient_ids` straight into one To header, so every follower saw every
other follower. The split happens in `mail.mail.send()` before any client sees
the mail, and this file is what keeps it there.

The second half is the bookkeeping Odoo does after a send and this path did
not: the notification rows turn `sent`, an `auto_delete` mail leaves the
table, and a failure only raises at the person when the send *is* what they
did (the composer), never out of the action it rode along with.
"""
from datetime import timedelta
from unittest.mock import patch

from odoo import fields
from odoo.exceptions import UserError
from odoo.tests import tagged

from .common import MailProTestCase


@tagged('pan_mail_pro', 'post_install', '-at_install')
class TestOneSendPerRecipient(MailProTestCase):

    def setUp(self):
        super().setUp()
        self.second_partner = self.env['res.partner'].create({
            'name': 'Other Customer', 'email': 'other@elsewhere.example',
        })
        self.sends = []

    def _fake_send(self, success=True):
        sends = self.sends

        def send_message(client_self, mail_record, mailbox, account, reply_context=None):
            sends.append({
                'to': sorted(mail_record.recipient_ids.mapped('email')),
                'email_to': mail_record.email_to or False,
                'email_cc': mail_record.email_cc or False,
                'mail': mail_record,
            })
            if not success:
                return {'success': False, 'error': 'provider said no'}
            return {'success': True, 'message_id': f'<m{len(sends)}@test>',
                    'thread_id': 'T'}
        return patch.object(
            type(self.env['microsoft.graph.client']), 'send_message', send_message)

    def _mail(self, **vals):
        base = {
            'subject': 'Quotation',
            'body_html': '<p>Hello</p>',
            'author_id': self.salesperson.partner_id.id,
            'x_send_from_mailbox_id': self.shared_mailbox.id,
            # The follower mails Odoo creates for a chatter post: the case the
            # split exists for.
            'is_notification': True,
        }
        base.update(vals)
        return self.env['mail.mail'].create(base)

    def test_two_partners_are_two_sends_with_one_address_each(self):
        mail = self._mail(recipient_ids=[(6, 0, [
            self.external_partner.id, self.second_partner.id])])
        with self._fake_send():
            mail.send()
        self.assertEqual(len(self.sends), 2)
        self.assertEqual(
            sorted(send['to'] for send in self.sends),
            [['customer@example.com'], ['other@elsewhere.example']])
        for send in self.sends:
            self.assertEqual(len(send['to']), 1)
            self.assertFalse(send['email_to'])

    def test_typed_addresses_stay_together_and_partners_go_alone(self):
        """As core: `email_to` is one message, with the Cc once; every
        partner is a message of its own."""
        mail = self._mail(email_to='a@typed.example, b@typed.example',
                          email_cc='cc@typed.example',
                          recipient_ids=[(6, 0, [self.external_partner.id])])
        with self._fake_send():
            mail.send()
        self.assertEqual(len(self.sends), 2)
        typed = [s for s in self.sends if s['email_to']][0]
        partner = [s for s in self.sends if not s['email_to']][0]
        self.assertEqual(typed['to'], [])
        self.assertEqual(typed['email_cc'], 'cc@typed.example')
        self.assertEqual(partner['to'], ['customer@example.com'])
        self.assertFalse(partner['email_cc'])

    def test_a_mail_that_is_not_a_notification_is_not_split(self):
        """A template's mail owns its message: deleting one copy after the
        send would cascade the message and every sibling with it. It keeps
        its one To header, as before, and is deleted whole."""
        mail = self._mail(is_notification=False, auto_delete=True, recipient_ids=[(6, 0, [
            self.external_partner.id, self.second_partner.id])])
        with self._fake_send():
            mail.send()
        self.assertEqual(len(self.sends), 1)
        self.assertEqual(self.sends[0]['to'], ['customer@example.com', 'other@elsewhere.example'])
        self.assertFalse(mail.exists())

    def test_one_partner_is_one_send_untouched(self):
        mail = self._mail(recipient_ids=[(6, 0, [self.external_partner.id])])
        with self._fake_send():
            mail.send()
        self.assertEqual(len(self.sends), 1)
        self.assertEqual(self.sends[0]['mail'], mail)

    def test_the_inbox_to_line_still_names_everybody(self):
        mail = self._mail(recipient_ids=[(6, 0, [
            self.external_partner.id, self.second_partner.id])])
        message = mail.mail_message_id
        with self._fake_send():
            mail.send()
        self.assertEqual(
            sorted(message.x_email_to.split(', ')),
            ['customer@example.com', 'other@elsewhere.example'])

    def test_the_notification_moves_with_its_partner(self):
        mail = self._mail(is_notification=True, recipient_ids=[(6, 0, [
            self.external_partner.id, self.second_partner.id])])
        Notification = self.env['mail.notification'].sudo()
        for partner in (self.external_partner, self.second_partner):
            Notification.create({
                'mail_message_id': mail.mail_message_id.id,
                'res_partner_id': partner.id,
                'notification_type': 'email',
                'notification_status': 'ready',
                'mail_mail_id': mail.id,
            })
        with self._fake_send():
            mail.send()
        notifications = Notification.search([
            ('mail_message_id', '=', mail.mail_message_id.id)])
        self.assertEqual(len(notifications), 2)
        self.assertEqual(set(notifications.mapped('notification_status')), {'sent'})


@tagged('pan_mail_pro', 'post_install', '-at_install')
class TestAfterTheSend(MailProTestCase):

    def _mail(self, **vals):
        base = {
            'subject': 'Note',
            'body_html': '<p>Hello</p>',
            'author_id': self.salesperson.partner_id.id,
            'x_send_from_mailbox_id': self.shared_mailbox.id,
            'recipient_ids': [(6, 0, [self.external_partner.id])],
        }
        base.update(vals)
        return self.env['mail.mail'].create(base)

    @staticmethod
    def _send_ok(client_self, mail_record, mailbox, account, reply_context=None):
        return {'success': True, 'message_id': '<sent@test>', 'thread_id': 'T'}

    @staticmethod
    def _send_fail(client_self, mail_record, mailbox, account, reply_context=None):
        return {'success': False, 'error': 'provider said no'}

    def test_an_auto_delete_mail_leaves_the_table_like_after_smtp(self):
        mail = self._mail(auto_delete=True)
        kept = self._mail(auto_delete=False)
        with patch.object(type(self.env['microsoft.graph.client']), 'send_message', self._send_ok):
            (mail | kept).send()
        self.assertFalse(mail.exists())
        self.assertEqual(kept.state, 'sent')

    def test_the_notification_row_says_sent(self):
        """The row that outlives the mail must not keep saying pending."""
        mail = self._mail(is_notification=True)
        notification = self.env['mail.notification'].sudo().create({
            'mail_message_id': mail.mail_message_id.id,
            'res_partner_id': self.external_partner.id,
            'notification_type': 'email',
            'notification_status': 'ready',
            'mail_mail_id': mail.id,
        })
        with patch.object(type(self.env['microsoft.graph.client']), 'send_message', self._send_ok):
            mail.send()
        self.assertEqual(notification.notification_status, 'sent')

    def test_a_failure_riding_along_with_another_action_does_not_raise(self):
        """Creating a user, posting a note, changing a stage: the mail is a side
        effect. Its failure stays on the mail and in the chatter and never
        unwinds the thing the person actually did."""
        mail = self._mail()
        with patch.object(type(self.env['microsoft.graph.client']), 'send_message', self._send_fail):
            mail.send()  # no raise
        self.assertEqual(mail.state, 'exception')
        self.assertIn('provider said no', mail.failure_reason)

    def test_the_composers_own_send_raises_at_the_person(self):
        mail = self._mail()
        with patch.object(type(self.env['microsoft.graph.client']), 'send_message', self._send_fail), \
                self.assertRaisesRegex(UserError, 'provider said no'):
            mail.with_context(pan_mail_interactive_send=True).send()

    def test_a_throttled_send_waits_instead_of_failing(self):
        """A Retry-After too long to sleep is a pause, not a failure: the mail
        stays outgoing with a scheduled date the queue honours."""
        from odoo.addons.pan_mail_pro.models.mail_provider_client import ERROR_THROTTLED

        def throttled(client_self, mail_record, mailbox, account, reply_context=None):
            return {'success': False, 'error': 'Microsoft asked to wait 120 seconds',
                    'error_code': ERROR_THROTTLED, 'retry_after': 120}
        mail = self._mail()
        before = fields.Datetime.now()
        with patch.object(type(self.env['microsoft.graph.client']), 'send_message', throttled):
            mail.with_context(pan_mail_interactive_send=True).send()  # no raise either
        self.assertEqual(mail.state, 'outgoing')
        self.assertIn('asked to wait', mail.failure_reason)
        self.assertGreater(mail.scheduled_date, before + timedelta(seconds=60))

    def test_a_caller_who_asks_for_the_exception_gets_odoos_own(self):
        """auth_signup sends the invitation with raise_exception=True and
        catches MailDeliveryException, creating the user anyway. A UserError
        here killed the user along with the mail."""
        from odoo.addons.base.models.ir_mail_server import MailDeliveryException
        mail = self._mail()
        with patch.object(type(self.env['microsoft.graph.client']), 'send_message', self._send_fail), \
                self.assertRaises(MailDeliveryException):
            mail.send(raise_exception=True)

    def test_a_transport_error_the_client_caught_reads_as_a_sentence_too(self):
        """The Graph client catches requests' exceptions itself and hands back
        their text; the dialog and the failure reason must not show the pool
        dump either way."""
        def caught(client_self, mail_record, mailbox, account, reply_context=None):
            return {'success': False, 'error': (
                "HTTPSConnectionPool(host='graph.microsoft.com', port=443): Max retries "
                "exceeded with url: /v1.0/users/x/messages (Caused by SSLError("
                "SSLCertVerificationError(1, '[SSL: CERTIFICATE_VERIFY_FAILED] ...')))")}
        mail = self._mail()
        with patch.object(type(self.env['microsoft.graph.client']), 'send_message', caught):
            mail.send()
        self.assertIn('certificate check failed', mail.failure_reason)
        self.assertNotIn('SSLCertVerificationError', mail.failure_reason.split('(')[0])

    def test_a_transport_error_reads_as_a_sentence(self):
        import requests

        def boom(client_self, mail_record, mailbox, account, reply_context=None):
            raise requests.exceptions.SSLError(
                "HTTPSConnectionPool(host='graph.microsoft.com', port=443): Max retries "
                "exceeded\n[SSL: CERTIFICATE_VERIFY_FAILED]")
        mail = self._mail()
        with patch.object(type(self.env['microsoft.graph.client']), 'send_message', boom):
            mail.send()
        self.assertIn('certificate check failed', mail.failure_reason)
        self.assertNotIn('CERTIFICATE_VERIFY_FAILED', mail.failure_reason)
