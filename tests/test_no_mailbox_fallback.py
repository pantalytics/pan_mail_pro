# -*- coding: utf-8 -*-
"""What happens when Mail Pro is not configured.

There is exactly one case where outbound mail leaves through Odoo's own SMTP
path: no `x_microsoft.mailbox` records exist anywhere, which means nobody has
opted in yet. That is what keeps demo, QA and a fresh install working before
anyone has been to Azure.

The moment one mailbox exists - even an archived one - the admin has opted in
and mail must not slip out via SMTP behind their back. Mail that cannot be
routed then fails visibly on the record instead.
"""
from unittest.mock import patch

from odoo.tests import TransactionCase, tagged
from odoo.tools import mute_logger

from .common import send_and_capture


@tagged('pan_mail_pro', 'post_install', '-at_install')
class TestNoMailboxFallback(TransactionCase):
    """Module is installed but no mailboxes are configured."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env['x_microsoft.mailbox'].sudo().with_context(
            active_test=False).search([]).unlink()

    def _make_mail(self):
        return self.env['mail.mail'].sudo().create({
            'subject': 'Welcome',
            'body_html': '<p>Hi</p>',
            'email_to': 'customer@example.com',
            'author_id': self.env.user.partner_id.id,
        })

    def test_no_mailboxes_falls_through_to_super(self):
        mail = self._make_mail()
        Mail = type(self.env['mail.mail'])
        with patch.object(Mail, '_send_one') as provider_path, \
             patch('odoo.addons.mail.models.mail_mail.MailMail.send',
                   autospec=True, return_value=True) as super_send:
            mail.send()
        provider_path.assert_not_called()
        self.assertTrue(super_send.called,
                        "super().send() must run when no mailboxes are configured")
        self.assertNotEqual(mail.state, 'cancel',
                            "mail must not be cancelled when Mail Pro is unconfigured")

    def test_creating_a_user_still_works(self):
        """auth_signup's welcome mail used to crash on `m.mailing_id` when
        mass_mailing was absent, or get silently cancelled."""
        user = self.env['res.users'].sudo().create({
            'name': 'Welcome Test',
            'login': 'welcome_test@example.com',
            'email': 'welcome_test@example.com',
            'group_ids': [(6, 0, [self.env.ref('base.group_user').id])],
        })
        self.assertTrue(user.exists())


@tagged('pan_mail_pro', 'post_install', '-at_install')
class TestMailboxesExistButUnusable(TransactionCase):
    """A mailbox exists but cannot route this mail.

    It must not reach SMTP, and it must say why on the record rather than
    disappearing into a silent `cancel` the admin never sees.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env['x_microsoft.mailbox'].sudo().with_context(
            active_test=False).search([]).unlink()
        cls.inactive_mailbox = cls.env['x_microsoft.mailbox'].sudo().create({
            'email': 'inactive@company.test',
            'x_mailbox_type': 'shared',
            'active': False,
        })

    @mute_logger('odoo.addons.pan_mail_pro.models.mail_mail')
    def test_unroutable_mail_fails_visibly_instead_of_reaching_smtp(self):
        mail = self.env['mail.mail'].sudo().create({
            'subject': 'X',
            'body_html': '<p>X</p>',
            'email_to': 'customer@example.com',
            'author_id': self.env.user.partner_id.id,
        })
        with patch('odoo.addons.mail.models.mail_mail.MailMail.send',
                   autospec=True, return_value=True) as super_send:
            error = send_and_capture(mail)

        super_send.assert_not_called()
        self.assertIsNotNone(error, 'The sender must be told')
        self.assertEqual(mail.state, 'exception')
        self.assertTrue(mail.failure_reason,
                        'An unsendable mail must carry the reason it was not sent')
