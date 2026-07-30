# -*- coding: utf-8 -*-
"""
Who may send from which mailbox.

A personal mailbox sends with its *owner's* delegated token. The composer's
view domain filters what the dropdown offers, but the field is writable over
RPC, so without a server-side check any internal user can send mail as a
colleague — signed by that colleague's own credentials. Microsoft does not
stop it, because it is not a SendAs.

These tests are the boundary the view cannot be.
"""
from odoo.exceptions import AccessError, ValidationError
from odoo.tests import tagged

from .common import OutlookProTestCase


@tagged('post_install', '-at_install')
class TestMailboxPermission(OutlookProTestCase):

    def _mail_vals(self, mailbox=None):
        vals = {
            'subject': 'Test',
            'body_html': '<p>Test</p>',
            'email_to': 'customer@example.com',
        }
        if mailbox is not None:
            vals['x_microsoft_mailbox_id'] = mailbox.id
        return vals

    # -- the core case ---------------------------------------------------- #

    def test_other_user_cannot_send_from_personal_mailbox(self):
        """Setting a colleague's personal mailbox directly is refused."""
        with self.assertRaises(AccessError):
            self.env['mail.mail'].with_user(self.other_user).create(
                self._mail_vals(self.personal_mailbox)
            )

    def test_other_user_cannot_send_via_context(self):
        """The context key mail.compose.message uses is guarded too."""
        with self.assertRaises(AccessError):
            self.env['mail.mail'].with_user(self.other_user).with_context(
                microsoft_mailbox_id=self.personal_mailbox.id
            ).create(self._mail_vals())

    def test_other_user_cannot_reassign_on_write(self):
        """A mail created innocently cannot be re-pointed afterwards."""
        mail = self.env['mail.mail'].with_user(self.other_user).create(
            self._mail_vals(self.shared_mailbox)
        )
        with self.assertRaises(AccessError):
            mail.write({'x_microsoft_mailbox_id': self.personal_mailbox.id})

    def test_composer_field_is_constrained(self):
        """The composer enforces the same rule its view domain suggests."""
        composer = self.env['mail.compose.message'].with_user(self.other_user).create({
            'subject': 'Test',
            'body': '<p>Test</p>',
        })
        with self.assertRaises(ValidationError):
            composer.x_microsoft_send_from_id = self.personal_mailbox

    # -- what must keep working ------------------------------------------- #

    def test_owner_may_send_from_own_personal_mailbox(self):
        mail = self.env['mail.mail'].with_user(self.salesperson).create(
            self._mail_vals(self.personal_mailbox)
        )
        self.assertEqual(mail.x_microsoft_mailbox_id, self.personal_mailbox)

    def test_anyone_may_send_from_shared_mailbox(self):
        """Shared mailboxes are shared on purpose — this must not regress."""
        mail = self.env['mail.mail'].with_user(self.other_user).create(
            self._mail_vals(self.shared_mailbox)
        )
        self.assertEqual(mail.x_microsoft_mailbox_id, self.shared_mailbox)

    def test_superuser_is_exempt(self):
        """System mail and templates pick a mailbox on nobody's behalf."""
        mail = self.env['mail.mail'].sudo().create(
            self._mail_vals(self.personal_mailbox)
        )
        self.assertEqual(mail.x_microsoft_mailbox_id, self.personal_mailbox)

    # -- defence in depth at send time ------------------------------------ #

    def test_send_falls_back_when_author_may_not_use_mailbox(self):
        """A row that predates the check must not send with the owner's token.

        It falls back rather than raising: this runs in the mail queue, where an
        exception would stall every other mail behind it.
        """
        mail = self.env['mail.mail'].sudo().create({
            **self._mail_vals(self.personal_mailbox),
            'author_id': self.other_user.partner_id.id,
        })
        mailbox, _account = mail._get_mailbox_and_account()
        self.assertNotEqual(
            mailbox, self.personal_mailbox,
            "Mail authored by a non-owner must not resolve to a personal mailbox",
        )

    # -- discovery half of the problem ------------------------------------ #

    def test_personal_mailbox_not_readable_by_others(self):
        """You cannot pick what you cannot see; the record rule closes that."""
        visible = self.env['x_microsoft.mailbox'].with_user(self.other_user).search([])
        self.assertNotIn(self.personal_mailbox, visible)
        self.assertIn(self.shared_mailbox, visible)

    def test_owner_still_sees_own_personal_mailbox(self):
        visible = self.env['x_microsoft.mailbox'].with_user(self.salesperson).search([])
        self.assertIn(self.personal_mailbox, visible)
