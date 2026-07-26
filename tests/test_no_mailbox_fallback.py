# -*- coding: utf-8 -*-
"""
Tests for graceful degradation when Mail Pro is not configured.

When no x_microsoft.mailbox records exist anywhere in the system, the module
must defer to Odoo's standard mail handling (SMTP / mail queue) instead of
silently cancelling outbound emails. This keeps demo/QA/dev environments
working out-of-the-box.

Once an admin creates at least one mailbox, the module activates and either
routes via Graph API (happy path) or cancels mails it can't route (safety
against unintended SMTP leakage in production).
"""
from unittest.mock import patch

from odoo.tests import TransactionCase, tagged


@tagged('pan_mail_pro', 'post_install', '-at_install')
class TestNoMailboxFallback(TransactionCase):
    """Module is installed but no mailboxes are configured."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        # Ensure a clean slate — no mailboxes anywhere
        cls.env['x_microsoft.mailbox'].sudo().search([]).unlink()
        assert cls.env['x_microsoft.mailbox'].sudo().search_count([]) == 0

    def _make_mail(self):
        return self.env['mail.mail'].sudo().create({
            'subject': 'Welcome',
            'body_html': '<p>Hi</p>',
            'email_to': 'customer@example.com',
            'author_id': self.env.user.partner_id.id,
        })

    def test_no_mailboxes_falls_through_to_super(self):
        """With zero mailboxes, send() must call super().send() — not cancel."""
        mail = self._make_mail()
        Mail = type(self.env['mail.mail'])
        with patch.object(Mail, '_send_via_microsoft_graph') as graph_path, \
             patch('odoo.addons.mail.models.mail_mail.MailMail.send',
                   autospec=True, return_value=True) as super_send:
            mail.send()
        graph_path.assert_not_called()
        # super().send() must have been invoked with the graph_mails recordset
        self.assertTrue(super_send.called,
                        "super().send() must run when no mailboxes are configured")
        # Mail must NOT be cancelled
        self.assertNotEqual(mail.state, 'cancel',
                            "mail.mail must not be cancelled when Mail Pro is unconfigured")

    def test_no_mailboxes_user_create_does_not_crash(self):
        """Creating a user with email triggers auth_signup's welcome mail.
        With no mailboxes, this path used to crash on `m.mailing_id` (when
        mass_mailing not installed) or silently cancel. Now it must succeed."""
        # This implicitly tests the hasattr(m, 'mailing_id') guard plus the
        # super().send() fallback. We just need user creation to not raise.
        try:
            user = self.env['res.users'].sudo().create({
                'name': 'Welcome Test',
                'login': 'welcome_test@example.com',
                'email': 'welcome_test@example.com',
                'group_ids': [(6, 0, [self.env.ref('base.group_user').id])],
            })
        except AttributeError as e:
            self.fail(f"user create raised AttributeError (mailing_id guard regressed?): {e}")
        self.assertTrue(user.exists())


@tagged('pan_mail_pro', 'post_install', '-at_install')
class TestMailboxesExistButUnusable(TransactionCase):
    """Mailboxes exist but cannot route this specific mail.

    Behavior must remain: cancel (not super().send()), to avoid leaking via
    SMTP when the admin has explicitly opted into Graph routing.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        # Create a mailbox so search_count([]) > 0, but make it inactive so
        # _is_mail_pro_configured() returns False (it filters on active).
        cls.env['x_microsoft.mailbox'].sudo().search([]).unlink()
        cls.inactive_mailbox = cls.env['x_microsoft.mailbox'].sudo().create({
            'email': 'inactive@company.test',
            'x_mailbox_type': 'shared',
            'active': False,
        })

    def test_inactive_mailbox_only_cancels_instead_of_super(self):
        """At least one mailbox exists (inactive) → cancel path, not SMTP."""
        mail = self.env['mail.mail'].sudo().create({
            'subject': 'X',
            'body_html': '<p>X</p>',
            'email_to': 'customer@example.com',
            'author_id': self.env.user.partner_id.id,
        })
        Mail = type(self.env['mail.mail'])
        with patch.object(Mail, '_send_via_microsoft_graph') as graph_path, \
             patch('odoo.addons.mail.models.mail_mail.MailMail.send',
                   autospec=True, return_value=True) as super_send:
            mail.send()
        graph_path.assert_not_called()
        super_send.assert_not_called()
        self.assertEqual(mail.state, 'cancel',
                         "mail must be cancelled when mailboxes exist but none usable")
