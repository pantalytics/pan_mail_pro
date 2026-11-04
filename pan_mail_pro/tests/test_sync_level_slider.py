# -*- coding: utf-8 -*-
"""The sync level is a slider, and its grid says what the fetcher does (#390).

The grid under the slider claims, per stop, which emails Mail Pro brings into
Odoo (#419: two groups, receiving and sending, one In Odoo column). Those claims are written in JavaScript and enforced in Python
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
    ('both', 'Received and sent replies'),
    ('contacts', 'Replies and existing contacts'),
    ('everyone', 'Replies, existing and new contacts'),
]


@tagged('pan_mail_pro', 'post_install', '-at_install')
class TestSyncLevelSlider(MailProTestCase):

    def test_the_labels_name_what_each_stop_adds(self):
        selection = self.env['pan.mail.mailbox']._fields['sync_level'].selection
        self.assertEqual(list(selection), LABELS)

    def _source(self):
        with open(WIDGET, encoding='utf-8') as handle:
            return handle.read()

    def _rows(self):
        """The grid's rows as the widget declares them, in screen order: the
        first stop at which each one is in Odoo, None for a row no stop
        brings in."""
        block = self._source().split('const GROUPS = [', 1)[1].split('];', 1)[0]
        return [None if found == 'null' else int(found)
                for found in re.findall(r'from:\s*(\d+|null)', block)]

    def test_the_grid_is_the_gates(self):
        """Receiving: replies at every stop, a new conversation from an
        existing contact once new conversations enter, one from a new contact
        once a new address may become a contact. Sending: replies from the
        mail app once the Sent folder is read, and a new conversation started
        there at no stop (`_gate_wanted` refuses it whatever the level;
        `test_incoming_gates`' `test_no_rung_lets_the_outbox_start_a_conversation`
        holds the gate to that)."""
        self.assertEqual(self._rows(), [0, 2, 3, 1, None])
        mailbox = self.personal_mailbox.sudo()
        for stop, (value, _label) in enumerate(LABELS):
            mailbox.sync_level = value
            from_code = [
                True,
                mailbox._syncs_new_conversations(),
                mailbox._syncs_strangers(),
                mailbox._reads_sent_folder(),
                False,
            ]
            from_grid = [first is not None and stop >= first for first in self._rows()]
            self.assertEqual(from_grid, from_code, value)

    def test_each_stop_adds_one_row(self):
        """What makes it a ladder: every stop above the first turns exactly
        one row on, so the flash on a stop change marks one row."""
        firsts = [first for first in self._rows() if first is not None]
        self.assertEqual(sorted(firsts), list(range(len(LABELS))))

    def test_the_copy_links_a_conversation_to_a_record(self):
        """The widget says Mail Pro links a conversation to a record, a contact
        being one. Never that an email lands on or goes on something (#419)."""
        source = self._source().lower()
        for vague in ('lands on', 'land on', 'goes on', 'sent items', 'known contact'):
            self.assertNotIn(vague, source, vague)
        for label in (_label for _value, _label in LABELS):
            self.assertNotIn('sent items', label.lower())

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
