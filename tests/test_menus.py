# -*- coding: utf-8 -*-
"""One entry in Settings → Technical → Email, not seven.

Every screen this module adds is a configuration or a diagnostic, so they all
live under Settings → Technical → Email. Until 19.0.7.7.0 each one was hung
straight off that menu, interleaved with Odoo's own Emails / Templates /
Aliases entries and reading as seven unrelated features; two of them even
shared a sequence, so their order was whatever the loader happened to do.

They are one module, so they get one submenu. The cost of that shape is that
it is invisible from any single view file — a new screen added tomorrow will
reach for `parent="base.menu_email"` by copying its neighbours' old habit and
nothing would say otherwise. This test says otherwise.
"""
from odoo.tests import tagged

from .common import MailProTestCase


@tagged('pan_mail_pro', 'post_install', '-at_install')
class TestMenus(MailProTestCase):

    def _declared_menus(self):
        data = self.env['ir.model.data'].search([
            ('module', '=', 'pan_mail_pro'),
            ('model', '=', 'ir.ui.menu'),
        ])
        return self.env['ir.ui.menu'].browse(data.mapped('res_id')).exists()

    def test_every_menu_hangs_off_the_one_root(self):
        root = self.env.ref('pan_mail_pro.menu_pan_mail_root')
        self.assertEqual(root.parent_id, self.env.ref('base.menu_email'))
        self.assertFalse(root.action, "the root is a section header, not a screen")

        strays = [
            menu.complete_name for menu in self._declared_menus() - root
            if menu.parent_id != root
        ]
        self.assertFalse(
            strays,
            "menus outside the Mail Pro section: %s" % strays,
        )

    def test_no_two_menus_share_a_sequence(self):
        children = self._declared_menus().filtered(
            lambda menu: menu.parent_id == self.env.ref('pan_mail_pro.menu_pan_mail_root'))
        self.assertTrue(children, "the Mail Pro section is empty")
        sequences = children.mapped('sequence')
        self.assertEqual(
            len(set(sequences)), len(sequences),
            "two menus share a sequence, so their order is undefined: %s" % sorted(
                (menu.sequence, menu.name) for menu in children),
        )
