# -*- coding: utf-8 -*-
"""How much of their own mailbox Odoo reads back is the user's call.

Everything else about a mailbox belongs to the workspace: which address, whose
credentials, which team an alias routes to. The sync level is different — it
decides how much of somebody's correspondence lands in a shared database, and
routing that decision through an administrator makes a privacy choice on behalf
of the person whose mail it is.

The field lives on `res.users`, reads and writes the mailbox's own
`sync_level`, and is aimable at any user id over RPC — which is what
`_check_mailbox_is_mine` is for.
"""
from odoo.exceptions import AccessError
from odoo.tests import tagged

from .common import MailProTestCase


@tagged('pan_mail_pro', 'post_install', '-at_install')
class TestUserSyncLevel(MailProTestCase):

    def test_the_ladder_is_the_mailbox_s_own(self):
        """One ladder, not a second copy of it on res.users."""
        user_levels = self.env['res.users']._fields[
            'x_pan_mail_sync_level']._description_selection(self.env)
        mailbox_levels = self.env['pan.mail.mailbox']._fields[
            'sync_level']._description_selection(self.env)
        self.assertEqual(dict(user_levels), dict(mailbox_levels))

    def test_a_user_reads_their_own_mailbox_level(self):
        self.personal_mailbox.sudo().sync_level = 'contacts'
        user = self.salesperson.with_user(self.salesperson)
        self.assertEqual(user.x_pan_mail_personal_mailbox_id,
                         self.personal_mailbox)
        self.assertEqual(user.x_pan_mail_sync_level, 'contacts')

    def test_a_user_sets_it_without_a_mailbox_manager(self):
        """The whole point: an ordinary internal user, no manager group, and
        `pan.mail.mailbox` is read-only for them in the ACL."""
        self.assertFalse(self.salesperson.has_group(
            'pan_mail_pro.group_mail_mailbox_manager'))
        self.salesperson.with_user(self.salesperson).write({
            'x_pan_mail_sync_level': 'everyone',
        })
        self.assertEqual(self.personal_mailbox.sudo().sync_level, 'everyone')

    def test_a_colleague_cannot_set_it(self):
        """Reachable over RPC for any id an internal user can browse, which is
        all of them. `sudo()` in the inverse would otherwise write the row."""
        self.personal_mailbox.sudo().sync_level = 'replies'
        with self.assertRaises(AccessError):
            self.salesperson.with_user(self.other_user)._check_mailbox_is_mine()
        with self.assertRaises(AccessError):
            self.salesperson.with_user(self.other_user).write({
                'x_pan_mail_sync_level': 'everyone',
            })
        self.assertEqual(self.personal_mailbox.sudo().sync_level, 'replies')

    def test_an_administrator_may_set_it_for_somebody_else(self):
        """Same exemption as connecting a mailbox on a user's behalf: a real
        support task, and the admin can reach the mailbox form anyway."""
        self.salesperson.with_user(self.env.ref('base.user_admin')).write({
            'x_pan_mail_sync_level': 'both',
        })
        self.assertEqual(self.personal_mailbox.sudo().sync_level, 'both')

    def test_no_personal_mailbox_means_nothing_to_set(self):
        """A connected user whose address has no mailbox of its own, and every
        user before they connect. The field is empty and writing it creates
        nothing — the view hides it on the same condition."""
        user = self.other_user.with_user(self.other_user)
        self.assertFalse(user.x_pan_mail_personal_mailbox_id)
        self.assertFalse(user.x_pan_mail_sync_level)
        before = self.env['pan.mail.mailbox'].sudo().search_count([])
        user.write({'x_pan_mail_sync_level': 'everyone'})
        self.assertEqual(
            self.env['pan.mail.mailbox'].sudo().search_count([]), before)

    def test_the_notification_mailbox_is_not_a_personal_setting(self):
        """It carries the system email; its own form hides Sync Settings."""
        self.assertFalse(
            self.notif_owner.x_pan_mail_personal_mailbox_id)
