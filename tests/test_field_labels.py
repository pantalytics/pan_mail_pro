# -*- coding: utf-8 -*-
"""Registry label collisions on mail.message.

Odoo warns on every registry load when two fields of one model share a label,
and mail.message is crowded: the account module already ships a field labeled
'Account'. Our x_account_id must therefore keep a label of its own — this
pins it, so a rename back to 'Account' fails a test instead of re-introducing
log noise on every worker start.
"""
from odoo.tests import tagged

from .common import MailProTestCase


@tagged('pan_mail_pro', 'post_install', '-at_install')
class TestFieldLabels(MailProTestCase):

    def test_x_account_id_label_is_unique(self):
        fields = self.env['mail.message']._fields
        label = fields['x_account_id'].string
        self.assertEqual(label, 'Mail Account')
        clashes = [
            name for name, field in fields.items()
            if name != 'x_account_id' and field.string == label
        ]
        self.assertFalse(
            clashes,
            "mail.message fields sharing the label %r: %s" % (label, clashes),
        )
