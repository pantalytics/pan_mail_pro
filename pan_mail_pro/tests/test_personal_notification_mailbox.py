# -*- coding: utf-8 -*-
"""A personal mailbox that carries the system email (#387).

The first mailbox anyone adds is their own, and because one mailbox has to
carry Odoo's own mail it becomes the notification mailbox on its own. Two
things followed from that on 3 Nov. Its owner could no longer set sync on
their own address, because the flag hid the Sync settings page; and nothing
said that password resets now went out from a person's inbox. The flag no
longer hides or skips anything (the mailbox syncs whatever its level says),
and step 4 is amber, read off the mailbox's own type, until a shared
mailbox carries the flag.
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

    def test_the_form_shows_its_sync_settings(self):
        arch = self.env['pan.mail.mailbox'].get_view(
            self.env.ref('pan_mail_pro.view_pan_mail_mailbox_form').id, 'form')['arch']
        page, = etree.fromstring(arch).xpath("//page[@name='sync']")
        self.assertNotIn('is_notification_mailbox', page.get('invisible') or '')

    def test_it_goes_stale_like_any_mailbox_that_syncs(self):
        self.personal_mailbox.last_check_date = fields.Datetime.now() - timedelta(days=2)
        self.assertTrue(self.personal_mailbox._sync_is_stale())

    def test_it_is_in_its_owner_s_inbox(self):
        listed = self.env['pan.mail.conversation'].with_user(self.salesperson).inbox_mailboxes()
        self.assertIn(self.personal_mailbox.id, [row['id'] for row in listed])

    def test_step_4_reads_its_type_and_turns_green_on_a_shared_one(self):
        self.assertEqual(self.settings().x_notification_mailbox_type, 'personal')
        hint = self.env['pan.mail.mailbox'].shared_mailbox_hint()
        self.assertEqual(hint['url'], 'https://admin.microsoft.com/#/SharedMailbox')
        self.notification_mailbox.is_notification_mailbox = True
        self.assertEqual(self.settings().x_notification_mailbox_type, 'shared')
        self.assertFalse(self.env['pan.mail.mailbox'].shared_mailbox_hint()['url'],
                         'no action once a shared mailbox carries it')

    def test_google_and_imap_get_the_status_and_no_link(self):
        self.env.cr.execute("UPDATE pan_mail_mailbox SET provider = 'gmail' WHERE id = %s",
                            [self.personal_mailbox.id])
        self.personal_mailbox.invalidate_recordset()
        self.assertEqual(self.settings().x_notification_mailbox_type, 'personal')
        self.assertFalse(self.env['pan.mail.mailbox'].shared_mailbox_hint()['url'])

    def test_the_step_4_row_says_so_in_one_line(self):
        arch = self.env['res.config.settings'].get_view(
            self.env.ref('pan_mail_pro.res_config_settings_view_form').id, 'form')['arch']
        self.assertIn('System email from a personal mailbox', arch)
        self.assertIn("x_notification_mailbox_type != 'personal'", arch)
