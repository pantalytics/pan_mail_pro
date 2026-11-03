# -*- coding: utf-8 -*-
"""The sync level is a slider, and its grid says what the fetcher does (#390).

The grid under the slider claims, per stop, which kind of mail reaches the
Odoo record. Those claims are written in JavaScript and enforced in Python
(`pan.mail.mailbox`'s three rung questions, which `_folders_to_sync` and
`_gate_wanted` ask), so a rung that moves in one place and not the other
would put a picture on screen the sync does not keep. This file holds the
two to each other, and holds the labels and both screens to the slider.
"""
import os
import re

from lxml import etree

from odoo.tests import tagged

from .common import MailProTestCase

MODULE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WIDGET = os.path.join(MODULE, 'static', 'src', 'js', 'sync_level_field.js')

LABELS = [
    ('replies', 'Replies'),
    ('both', 'Replies and Sent Items'),
    ('contacts', 'Replies, Sent Items and known contacts'),
    ('everyone', 'Replies, Sent Items, known and new contacts'),
]


@tagged('pan_mail_pro', 'post_install', '-at_install')
class TestSyncLevelSlider(MailProTestCase):

    def test_the_labels_name_what_each_stop_adds(self):
        selection = self.env['pan.mail.mailbox']._fields['sync_level'].selection
        self.assertEqual(list(selection), LABELS)

    def _rows(self):
        """The grid's rows as the widget declares them: first stop at which
        the Odoo record gets each one."""
        with open(WIDGET, encoding='utf-8') as handle:
            source = handle.read()
        block = source.split('const ROWS = [', 1)[1].split('];', 1)[0]
        return [int(found) for found in re.findall(r'from:\s*(\d+)', block)]

    def test_the_grid_is_the_gates(self):
        """Row 0 (replies) at every stop, row 1 (Sent Items) once the Sent
        folder is read, row 2 once new conversations from contacts enter,
        row 3 once a new address may become a contact."""
        self.assertEqual(self._rows(), [0, 1, 2, 3])
        mailbox = self.personal_mailbox.sudo()
        for stop, (value, _label) in enumerate(LABELS):
            mailbox.sync_level = value
            from_code = [
                True,
                mailbox._reads_sent_folder(),
                mailbox._syncs_new_conversations(),
                mailbox._syncs_strangers(),
            ]
            from_grid = [stop >= first for first in self._rows()]
            self.assertEqual(from_grid, from_code, value)

    def _field(self, view, name):
        arch = self.env[view.model].get_view(view.id)['arch']
        return etree.fromstring(arch).xpath(f'//field[@name="{name}"]')

    def test_the_mailbox_form_draws_the_slider(self):
        fields = self._field(
            self.env.ref('pan_mail_pro.view_pan_mail_mailbox_form'), 'sync_level')
        self.assertTrue(fields)
        self.assertEqual({f.get('widget') for f in fields}, {'mailpro_sync_level'})

    def test_my_preferences_draws_the_same_slider(self):
        for xmlid in ('base.view_users_form_simple_modif', 'base.view_users_form'):
            fields = self._field(self.env.ref(xmlid), 'x_pan_mail_sync_level')
            self.assertTrue(fields, xmlid)
            self.assertEqual({f.get('widget') for f in fields},
                             {'mailpro_sync_level'}, xmlid)
