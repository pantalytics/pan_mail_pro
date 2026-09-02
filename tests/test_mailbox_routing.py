# -*- coding: utf-8 -*-
"""Decision table for mail.mail._resolve_route.

There are exactly four answers to "which mailbox sends this mail":

  1. It is a notification to one of our own users  -> the notification mailbox
  2. The composer said which mailbox               -> that one
  3. The author has a default mailbox              -> that one
  4. There is no author at all (system mail)       -> the notification mailbox

Anything else raises RoutingError with the sentence that says what to fix.
Rows 2 and 3 in particular do NOT fall back to the notification mailbox when
the credentials are missing: a mail leaving from notifications@ because your
own mailbox was misconfigured is worse than one that did not leave, because
nobody ever finds out.

No HTTP, no composer - pure logic.
"""
from unittest.mock import patch

from odoo.exceptions import UserError
from odoo.tests import tagged
from odoo.tools import mute_logger

from odoo.addons.pan_mail_pro.models.mail_mail import RoutingError

from .common import MailProTestCase, send_and_capture


@tagged('pan_mail_pro', 'post_install', '-at_install')
class TestMailboxRouting(MailProTestCase):

    def _make_mail(self, **overrides):
        """Create mail.mail as the salesperson but with admin rights.

        env.user must be the salesperson (resolve_sending_account reads it for
        a shared mailbox) without hitting mail.mail's admin-only ACL.
        """
        vals = {
            'subject': 'Quotation 0001',
            'body_html': '<p>Body</p>',
            'email_to': 'customer@example.com',
            'recipient_ids': [(6, 0, [self.external_partner.id])],
            'author_id': self.salesperson.partner_id.id,
        }
        vals.update(overrides)
        return self.env['mail.mail'].with_user(self.salesperson).sudo().create(vals)

    # ------------------------------------------------------------------ #
    # 1. Notifications to internal users
    # ------------------------------------------------------------------ #

    def test_internal_user_notification_uses_notification_mailbox(self):
        mail = self._make_mail(
            recipient_ids=[(6, 0, [self.other_user.partner_id.id])],
            email_to=False,
        )
        mailbox, account = mail._resolve_route()
        self.assertEqual(mailbox, self.notification_mailbox)
        self.assertEqual(account.user_id, self.notif_owner)

    def test_internal_notification_outranks_the_dropdown(self):
        """Even when the composer leaked a mailbox onto a notification mail."""
        mail = self._make_mail(
            recipient_ids=[(6, 0, [self.other_user.partner_id.id])],
            email_to=False,
            x_send_from_mailbox_id=self.shared_mailbox.id,
        )
        mailbox, _account = mail._resolve_route()
        self.assertEqual(mailbox, self.notification_mailbox)

    # ------------------------------------------------------------------ #
    # 2. The composer said which mailbox
    # ------------------------------------------------------------------ #

    def test_dropdown_shared_sends_with_the_author_token(self):
        mail = self._make_mail(x_send_from_mailbox_id=self.shared_mailbox.id)
        mailbox, account = mail._resolve_route()
        self.assertEqual(mailbox, self.shared_mailbox)
        self.assertEqual(account.user_id, self.salesperson)

    def test_dropdown_survives_a_company_partner_author(self):
        """A quotation template sets email_from to the company, so author_id
        resolves to a partner with no user. The dropdown is a human's choice
        and must still win, sending with whoever pressed Send."""
        mail = self._make_mail(
            author_id=self.company_partner.id,
            x_send_from_mailbox_id=self.shared_mailbox.id,
        )
        mailbox, account = mail._resolve_route()
        self.assertEqual(mailbox, self.shared_mailbox)
        self.assertEqual(account.user_id, self.salesperson)

    def test_dropdown_personal_sends_with_the_owner_token(self):
        mail = self._make_mail(x_send_from_mailbox_id=self.personal_mailbox.id)
        mailbox, account = mail._resolve_route()
        self.assertEqual(mailbox, self.personal_mailbox)
        self.assertEqual(account.user_id, self.salesperson)

    def test_dropdown_notification_sends_with_its_owner(self):
        mail = self._make_mail(x_send_from_mailbox_id=self.notification_mailbox.id)
        mailbox, account = mail._resolve_route()
        self.assertEqual(mailbox, self.notification_mailbox)
        self.assertEqual(account.user_id, self.notif_owner)

    def test_dropdown_with_disconnected_owner_refuses(self):
        """No silent reroute: the mail names the mailbox it could not use."""
        self.disconnect(self.salesperson)
        mail = self._make_mail(
            x_send_from_mailbox_id=self.personal_mailbox.id,
            author_id=self.company_partner.id,
        )
        with self.assertRaises(RoutingError) as ctx:
            mail._resolve_route()
        self.assertIn(self.salesperson.name, str(ctx.exception))

    # ------------------------------------------------------------------ #
    # 3. The author's default mailbox
    # ------------------------------------------------------------------ #

    def test_author_default_mailbox_is_used(self):
        mail = self._make_mail()
        mailbox, account = mail._resolve_route()
        self.assertEqual(mailbox, self.salesperson.x_default_mailbox_id)
        self.assertEqual(account.user_id, self.salesperson)

    def test_author_without_a_default_mailbox_refuses(self):
        self.salesperson.x_default_mailbox_id = False
        mail = self._make_mail()
        with self.assertRaises(RoutingError) as ctx:
            mail._resolve_route()
        self.assertIn('default mailbox', str(ctx.exception))

    def test_disconnected_author_refuses(self):
        self.disconnect(self.salesperson)
        mail = self._make_mail()
        with self.assertRaises(RoutingError) as ctx:
            mail._resolve_route()
        self.assertIn(self.salesperson.name, str(ctx.exception))

    # ------------------------------------------------------------------ #
    # 4. System mail with nobody behind it
    # ------------------------------------------------------------------ #

    def test_external_author_uses_notification_mailbox(self):
        """An auto-reply triggered by incoming mail has no user to send as."""
        mail = self._make_mail(author_id=self.company_partner.id)
        mailbox, account = mail._resolve_route()
        self.assertEqual(mailbox, self.notification_mailbox)
        self.assertEqual(account.user_id, self.notif_owner)

    def test_no_author_uses_notification_mailbox(self):
        mail = self._make_mail(author_id=False)
        mailbox, _account = mail._resolve_route()
        self.assertEqual(mailbox, self.notification_mailbox)

    def test_no_notification_mailbox_refuses(self):
        self.notification_mailbox.active = False
        mail = self._make_mail(author_id=self.company_partner.id)
        with self.assertRaises(RoutingError) as ctx:
            mail._resolve_route()
        self.assertIn('Notification mailbox', str(ctx.exception))

    # ------------------------------------------------------------------ #
    # A failure lands on the mail, not on the batch
    # ------------------------------------------------------------------ #

    @mute_logger('odoo.addons.pan_mail_pro.models.mail_mail')
    def test_unroutable_mail_does_not_stop_the_ones_behind_it(self):
        """The batch runs to the end, and the failure lands on its own mail."""
        self.salesperson.x_default_mailbox_id = False
        broken = self._make_mail()
        fine = self._make_mail(x_send_from_mailbox_id=self.shared_mailbox.id)

        with self.mock_graph():
            send_and_capture(broken | fine)

        self.assertEqual(broken.state, 'exception')
        self.assertIn('default mailbox', broken.failure_reason)
        self.assertEqual(fine.state, 'sent',
                         'One unroutable mail must not stop the ones behind it')

    @mute_logger('odoo.addons.pan_mail_pro.models.mail_mail')
    def test_a_mixed_batch_does_not_raise_over_a_mail_that_already_went_out(self):
        """The raise is a rollback, and a rollback here is a double delivery.

        Interactively there is no commit until the request ends, so raising
        unwinds `state='sent'` on mails the provider has *already* delivered.
        Odoo then forgets it sent them, the mail queue picks them up a minute
        later, and the customer reads the same email twice. So a batch that
        delivered anything keeps its successes and reports the failures on the
        mails themselves rather than in a dialog.
        """
        self.salesperson.x_default_mailbox_id = False
        broken = self._make_mail()
        fine = self._make_mail(x_send_from_mailbox_id=self.shared_mailbox.id)

        with self.mock_graph():
            error = send_and_capture(broken | fine)

        self.assertIsNone(
            error, 'Raising would roll the delivered mail back to outgoing')
        self.assertEqual(fine.state, 'sent')
        self.assertIn('default mailbox', broken.failure_reason,
                      'The reason still has to be findable on the mail')

    @mute_logger('odoo.addons.pan_mail_pro.models.mail_mail')
    def test_several_failures_report_one_reason_and_a_count(self):
        """Nothing went out, so there is nothing a rollback can cost."""
        self.salesperson.x_default_mailbox_id = False
        first = self._make_mail()
        second = self._make_mail()

        with self.mock_graph():
            error = send_and_capture(first | second)

        self.assertIn('default mailbox', str(error))
        self.assertIn('1 more', str(error))
        self.assertEqual(first.state, 'exception')
        self.assertEqual(second.state, 'exception')

    @mute_logger('odoo.addons.pan_mail_pro.models.mail_mail')
    def test_the_queue_still_reports_a_mixed_batch(self):
        """`auto_commit` is the mail queue, where every send is already safe.

        There the successes are committed as they go, so the raise costs
        nothing and the reason belongs in the cron log. The commit is patched
        out — Odoo forbids a real one inside a test transaction — so this
        asserts the decision to report, not the commit itself.
        """
        self.salesperson.x_default_mailbox_id = False
        broken = self._make_mail()
        fine = self._make_mail(x_send_from_mailbox_id=self.shared_mailbox.id)

        with self.mock_graph(), \
                patch.object(self.env.cr, 'commit', lambda: None), \
                self.assertRaises(UserError):
            (broken | fine).send(auto_commit=True)

    # ------------------------------------------------------------------ #
    # Mass mailing keeps its own delivery path
    # ------------------------------------------------------------------ #

    def test_mass_mailing_skips_the_provider_path(self):
        if 'mailing_id' not in self.env['mail.mail']._fields:
            self.skipTest('mass_mailing module not installed')

        from unittest.mock import patch
        mailing = self.env['mailing.mailing'].create({
            'subject': 'Campaign',
            'body_html': '<p>Hello</p>',
            'mailing_model_id': self.env['ir.model']._get('res.partner').id,
        })
        mail = self._make_mail()
        mail.mailing_id = mailing.id

        Mail = type(self.env['mail.mail'])
        with patch.object(Mail, '_send_one') as provider_path, \
             patch('odoo.addons.mail.models.mail_mail.MailMail.send',
                   autospec=True, return_value=True):
            mail.send()
        provider_path.assert_not_called()
