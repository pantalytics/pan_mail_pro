# -*- coding: utf-8 -*-
"""
Decision-table tests for mail.mail._get_mailbox_and_user.

Each test exercises one row of the routing matrix. No HTTP, no composer —
pure logic. Runs in <1s.

Matrix dimensions:
- internal-user-notification?
- explicit dropdown set?
- mailbox type (notification / shared / personal)
- author has res.users link?
- sender OAuth-connected?
"""
from odoo.tests import tagged

from .common import OutlookProTestCase


@tagged('pan_outlook_pro', 'post_install', '-at_install')
class TestMailboxRouting(OutlookProTestCase):

    def _make_mail(self, **overrides):
        """Create mail.mail as the salesperson user but with admin rights —
        we want env.user = salesperson (so _get_sending_account picks them up)
        without hitting mail.mail's admin-only ACL."""
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
    # 1. Internal user notifications always go via notification mailbox
    # ------------------------------------------------------------------ #

    def test_01_internal_user_notification_uses_notification_mailbox(self):
        mail = self._make_mail(
            recipient_ids=[(6, 0, [self.other_user.partner_id.id])],
            email_to=False,
        )
        mailbox, user = mail._get_mailbox_and_user()
        self.assertEqual(mailbox, self.notification_mailbox)
        self.assertEqual(user, self.notif_owner)

    def test_14_internal_notification_overrides_dropdown(self):
        """Even if the composer leaked a dropdown value onto the
        notification mail.mail, the notification mailbox must win."""
        mail = self._make_mail(
            recipient_ids=[(6, 0, [self.other_user.partner_id.id])],
            email_to=False,
            x_microsoft_mailbox_id=self.shared_mailbox.id,
        )
        mailbox, user = mail._get_mailbox_and_user()
        self.assertEqual(mailbox, self.notification_mailbox)
        self.assertEqual(user, self.notif_owner)

    # ------------------------------------------------------------------ #
    # 2-5. Dropdown selected: dropdown wins over author heuristic
    # ------------------------------------------------------------------ #

    def test_02_dropdown_shared_with_user_author(self):
        mail = self._make_mail(x_microsoft_mailbox_id=self.shared_mailbox.id)
        mailbox, user = mail._get_mailbox_and_user()
        self.assertEqual(mailbox, self.shared_mailbox)
        # Shared mailbox: prefer author's user when present
        self.assertEqual(user, self.salesperson)

    def test_03_dropdown_shared_with_company_author_falls_back_to_env_user(self):
        """The bug-of-the-day: author is the company partner (no user_ids).
        Dropdown must win, sender = env.user (the salesperson clicking Send)."""
        mail = self._make_mail(
            author_id=self.company_partner.id,
            x_microsoft_mailbox_id=self.shared_mailbox.id,
        )
        mailbox, user = mail._get_mailbox_and_user()
        self.assertEqual(mailbox, self.shared_mailbox)
        self.assertEqual(user, self.salesperson)

    def test_04_dropdown_personal_uses_owner(self):
        mail = self._make_mail(x_microsoft_mailbox_id=self.personal_mailbox.id)
        mailbox, user = mail._get_mailbox_and_user()
        self.assertEqual(mailbox, self.personal_mailbox)
        self.assertEqual(user, self.salesperson)  # owner of personal mailbox

    def test_05_dropdown_notification_uses_notification_owner(self):
        mail = self._make_mail(x_microsoft_mailbox_id=self.notification_mailbox.id)
        mailbox, user = mail._get_mailbox_and_user()
        self.assertEqual(mailbox, self.notification_mailbox)
        self.assertEqual(user, self.notif_owner)

    # ------------------------------------------------------------------ #
    # 6-7. Dropdown set but sender unavailable → fallback
    # ------------------------------------------------------------------ #

    def test_06_dropdown_shared_env_user_disconnected_falls_back_to_author(self):
        """If env.user is disconnected but author is, the author path takes
        over — still produces a working route."""
        # Disconnect env.user (salesperson) but keep author intact via a
        # different connected user.
        self.salesperson.sudo().write({'x_microsoft_refresh_token_encrypted': False})
        mail = self.env['mail.mail'].with_user(self.salesperson).sudo().create({
            'subject': 'X',
            'body_html': '<p>X</p>',
            'email_to': 'customer@example.com',
            'author_id': self.other_user.partner_id.id,
            'x_microsoft_mailbox_id': self.shared_mailbox.id,
        })
        mailbox, user = mail._get_mailbox_and_user()
        # Dropdown sender resolves to author user (still connected via other_user)
        self.assertEqual(mailbox, self.shared_mailbox)
        self.assertEqual(user, self.other_user)

    def test_07_dropdown_personal_owner_disconnected_returns_none(self):
        self.salesperson.sudo().write({'x_microsoft_refresh_token_encrypted': False})
        mail = self._make_mail(
            x_microsoft_mailbox_id=self.personal_mailbox.id,
            author_id=self.company_partner.id,  # also no user_ids → no author fallback
        )
        mailbox, user = mail._get_mailbox_and_user()
        # Dropdown owner disconnected → falls through; author has no user →
        # tries notification mailbox (which is configured) → succeeds there.
        self.assertEqual(mailbox, self.notification_mailbox)
        self.assertEqual(user, self.notif_owner)

    # ------------------------------------------------------------------ #
    # 8-12. No dropdown — author-based routing
    # ------------------------------------------------------------------ #

    def test_08_no_dropdown_uses_author_default_mailbox(self):
        mail = self._make_mail()  # author=salesperson, no dropdown
        mailbox, user = mail._get_mailbox_and_user()
        self.assertEqual(mailbox, self.salesperson.x_microsoft_default_mailbox_id)
        self.assertEqual(user, self.salesperson)

    def test_09_no_dropdown_no_default_mailbox_falls_back_to_notification(self):
        self.salesperson.x_microsoft_default_mailbox_id = False
        mail = self._make_mail()
        mailbox, user = mail._get_mailbox_and_user()
        self.assertEqual(mailbox, self.notification_mailbox)
        self.assertEqual(user, self.notif_owner)

    def test_10_no_dropdown_author_disconnected_falls_back_to_notification(self):
        self.salesperson.sudo().write({'x_microsoft_refresh_token_encrypted': False})
        mail = self._make_mail()
        mailbox, user = mail._get_mailbox_and_user()
        self.assertEqual(mailbox, self.notification_mailbox)
        self.assertEqual(user, self.notif_owner)

    def test_11_no_dropdown_company_partner_author_falls_back_to_notification(self):
        mail = self._make_mail(author_id=self.company_partner.id)
        mailbox, user = mail._get_mailbox_and_user()
        self.assertEqual(mailbox, self.notification_mailbox)
        self.assertEqual(user, self.notif_owner)

    def test_12_no_author_returns_none(self):
        mail = self._make_mail(author_id=False)
        mailbox, user = mail._get_mailbox_and_user()
        self.assertFalse(mailbox)
        self.assertFalse(user)

    # ------------------------------------------------------------------ #
    # 13. No notification mailbox configured at all
    # ------------------------------------------------------------------ #

    def test_13_no_notification_mailbox_returns_none(self):
        # Disable the only notification mailbox so the search returns empty.
        self.notification_mailbox.active = False
        mail = self._make_mail(author_id=self.company_partner.id)
        mailbox, user = mail._get_mailbox_and_user()
        self.assertFalse(mailbox)
        self.assertFalse(user)

    # ------------------------------------------------------------------ #
    # 15. Mass mailing bypasses Graph API entirely
    # ------------------------------------------------------------------ #

    def test_15_mass_mailing_skips_graph_path(self):
        """Mass mailing emails (mailing_id set) must route via super().send()
        and never hit _send_via_microsoft_graph."""
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
        with patch.object(Mail, '_send_via_microsoft_graph') as graph_path:
            # Block the actual SMTP path so we don't really send;
            # we only care that the Graph path is skipped.
            with patch('odoo.addons.mail.models.mail_mail.MailMail.send',
                       autospec=True, return_value=True):
                mail.send()
        graph_path.assert_not_called()
