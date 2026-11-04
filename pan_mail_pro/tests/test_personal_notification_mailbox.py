# -*- coding: utf-8 -*-
"""A personal mailbox that carries the system email (#387).

The first mailbox anyone adds is their own, and because one mailbox has to
carry Odoo's own mail it becomes the notification mailbox on its own. Two
things followed from that on 3 Nov. Its owner could no longer set sync on
their own address, because the flag hid the Sync settings page; and nothing
said that password resets now went out from a person's inbox. The flag no
longer hides or skips anything (the mailbox syncs whatever its level says),
and step 5 is amber (step 4 until #415), read off the mailbox's own type,
until a shared mailbox carries the flag.
"""
from datetime import timedelta

from lxml import etree

from odoo import fields
from odoo.tests import tagged

from .common import MailProTestCase


@tagged('pan_mail_pro', 'post_install', '-at_install')
class TestPersonalNotificationMailbox(MailProTestCase):

    def setUp(self):
        super().setUp()
        self.personal_mailbox.is_notification_mailbox = True
        self.assertFalse(self.notification_mailbox.is_notification_mailbox, 'one carries it')
        self.assertEqual(self.personal_mailbox.provider_type, 'personal')

    def settings(self):
        return self.env['res.config.settings'].create({})

    def test_its_owner_keeps_their_sync_setting(self):
        user = self.salesperson.with_user(self.salesperson)
        self.assertEqual(user.x_pan_mail_personal_mailbox_id, self.personal_mailbox)
        user.x_pan_mail_sync_level = 'contacts'
        self.assertEqual(self.personal_mailbox.sync_level, 'contacts')

    def test_the_form_shows_its_sync_level(self):
        arch = self.env['pan.mail.mailbox'].get_view(
            self.env.ref('pan_mail_pro.view_pan_mail_mailbox_form').id, 'form')['arch']
        field, = etree.fromstring(arch).xpath("//field[@name='sync_level']")
        self.assertNotIn('is_notification_mailbox', field.get('invisible') or '')

    def test_it_goes_stale_like_any_mailbox_that_syncs(self):
        self.personal_mailbox.last_check_date = fields.Datetime.now() - timedelta(days=2)
        self.assertTrue(self.personal_mailbox._sync_is_stale())

    def test_it_is_in_its_owner_s_inbox(self):
        listed = self.env['pan.mail.conversation'].with_user(self.salesperson).inbox_mailboxes()
        self.assertIn(self.personal_mailbox.id, [row['id'] for row in listed])

    def test_step_4_reads_its_type_and_turns_green_on_a_shared_one(self):
        self.assertEqual(self.settings().x_notification_mailbox_type, 'personal')
        self.notification_mailbox.is_notification_mailbox = True
        self.assertEqual(self.settings().x_notification_mailbox_type, 'shared')

    def test_google_and_imap_get_the_status(self):
        self.env.cr.execute("UPDATE pan_mail_mailbox SET provider = 'gmail' WHERE id = %s",
                            [self.personal_mailbox.id])
        self.personal_mailbox.invalidate_recordset()
        self.assertEqual(self.settings().x_notification_mailbox_type, 'personal')

    def test_the_list_offers_no_way_to_make_a_shared_mailbox(self):
        """#417: the admin center link and its Show me slot are gone, on
        every provider, whatever carries the system email."""
        self.assertFalse(hasattr(self.env['pan.mail.mailbox'], 'shared_mailbox_hint'))

    def test_the_step_5_row_says_so_in_one_line(self):
        arch = self.env['res.config.settings'].get_view(
            self.env.ref('pan_mail_pro.res_config_settings_view_form').id, 'form')['arch']
        self.assertIn('System email from a personal mailbox', arch)
        self.assertIn("x_notification_mailbox_type != 'personal'", arch)


@tagged('pan_mail_pro', 'post_install', '-at_install')
class TestMailboxFormOnePage(MailProTestCase):
    """The mailbox form is one page (#420): no tabs, no setup steps, no
    button that asks the reader to check anything, and no owner on a
    personal mailbox."""

    def arch(self):
        return etree.fromstring(self.env['pan.mail.mailbox'].get_view(
            self.env.ref('pan_mail_pro.view_pan_mail_mailbox_form').id, 'form')['arch'])

    def test_one_page_and_nothing_to_check(self):
        arch = self.arch()
        self.assertFalse(arch.xpath('//notebook'))
        self.assertFalse(arch.xpath("//button[@name='action_check_mailbox']"))
        self.assertFalse(arch.xpath("//*[contains(@class, 'o_mailpro_step')]"))
        self.assertNotIn('Not tested', etree.tostring(arch, encoding='unicode'))

    def test_the_owner_is_one_plain_field_hidden_by_the_owner_rule(self):
        owner, = self.arch().xpath("//field[@name='owner_user_id']")
        self.assertEqual(owner.get('invisible'), 'owner_is_address and id')
        self.assertFalse(owner.get('groups'), 'no developer-mode copy')
        self.assertEqual(owner.get('required'), 'is_notification_mailbox')

    def test_owner_is_address_follows_the_owner_rule(self):
        emovr = self.env['pan.mail.mailbox'].create({
            'email': 'info@emovr.test', 'owner_user_id': self.salesperson.id})
        emovr.sudo().write({'address_kind': 'user'})
        self.assertEqual(emovr.provider_type, 'personal')
        cases = {
            'personal': (self.personal_mailbox, True),
            'shared': (self.shared_mailbox, False),
            'notification on another address': (self.notification_mailbox, False),
            'Emovr': (emovr, False),
        }
        for name, (mailbox, expected) in cases.items():
            with self.subTest(name):
                self.assertIs(mailbox.owner_is_address, expected)
        self.personal_mailbox.is_notification_mailbox = True
        self.assertEqual(self.personal_mailbox.mailbox_type, 'personal')
        self.assertTrue(self.personal_mailbox.owner_is_address,
                        'a personal notification mailbox shows no owner either')

    def test_the_fix_is_told_where_the_error_shows(self):
        """The "what to do" half of the old Check step is on the status
        line now, and never asks anyone to check again."""
        account = self.salesperson.x_pan_mail_account_ids
        self.env['pan.mail.mailbox.access'].note_send(
            self.shared_mailbox, account, False, error='Exchange refused it.')
        self.shared_mailbox.invalidate_recordset(['access_ids', 'status_message'])
        self.assertEqual(
            self.shared_mailbox.status_message,
            'Exchange refused it. Allow that sign-in to send as this address in '
            'your mail admin.')
        self.assertNotIn('check again', self.shared_mailbox.status_message)
