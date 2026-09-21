# -*- coding: utf-8 -*-
"""One entry in Settings → Technical → Email, not seven. Plus one app.

Every screen this module adds is a configuration or a diagnostic, so they all
live under Settings → Technical → Email. Until 19.0.7.7.0 each one was hung
straight off that menu, interleaved with Odoo's own Emails / Templates /
Aliases entries and reading as seven unrelated features; two of them even
shared a sequence, so their order was whatever the loader happened to do.

They are one module, so they get one submenu. The cost of that shape is that
it is invisible from any single view file — a new screen added tomorrow will
reach for `parent="base.menu_email"` by copying its neighbours' old habit and
nothing would say otherwise. This test says otherwise.

**The one exception, named on purpose.** 19.0.10.0.0 gave the Inbox an
application of its own, by 19.0.7.0.0's own test: a tile is a promise about how
often a screen is opened, and this is the screen somebody answers customer mail
in all day. It is the exception because it is not a diagnostic. Anything else
that wants to be an app has to change this list and say why.
"""
import os

from odoo.tests import tagged

from .common import MailProTestCase

MODULE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def read(*parts):
    with open(os.path.join(MODULE, *parts), encoding='utf-8') as handle:
        return handle.read()


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

        app = self.env.ref('pan_mail_pro.menu_pan_mail_app')
        allowed = root | app | app.child_id

        strays = [
            menu.complete_name for menu in self._declared_menus() - allowed
            if menu.parent_id != root
        ]
        self.assertFalse(
            strays,
            "menus outside the Mail Pro section: %s" % strays,
        )

    def test_the_app_holds_the_inbox_and_nothing_else(self):
        """The tile is a promise about daily use. One screen keeps it."""
        app = self.env.ref('pan_mail_pro.menu_pan_mail_app')
        self.assertFalse(app.parent_id, "the app tile sits on the home screen")
        self.assertEqual(
            app.child_id.mapped('name'), ['Inbox'],
            "a second screen under the app tile needs its own argument",
        )

    def test_the_inbox_is_fullscreen_and_keeps_its_way_back(self):
        """Fullscreen takes Odoo's navbar off the screen. Both halves, or none.

        `web.WebClient` draws its navbar under `t-if="!state.fullscreen"`, so
        this target is what gives the Inbox the row the navbar was using. It
        also takes away the app switcher, the systray and the breadcrumb --
        every door out of this screen at once. The Inbox's own bar carries
        them instead, and a change that keeps the target while dropping the
        icon leaves a reader in a screen with no way back to Odoo.
        """
        action = self.env.ref('pan_mail_pro.action_pan_mail_conversation')
        self.assertEqual(action.target, 'fullscreen')

        template = read('static', 'src', 'xml', 'conversation_view.xml')
        self.assertIn(
            'o_mailpro_home', template,
            "the Inbox is fullscreen and has no way back to Odoo",
        )
        self.assertIn('href="/odoo"', template)
        self.assertIn(
            'o_menu_systray', template,
            "the systray went with the navbar and nothing brought it back",
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

    def test_a_mailbox_manager_can_reach_the_screens_their_acl_covers(self):
        """The group is only worth granting if it opens something.

        `ir.model.access.csv` gives Mailbox Manager write on the mailbox and
        the routing log, and `action_sync_now` / `action_test_incoming` are
        gated on that group -- while every child menu but Internal Domains
        asked for `base.group_system`. The only people allowed to press those
        buttons could not open the form the buttons are on.

        Credentials are the deliberate exception: Email Accounts and Providers
        stay administrator-only.
        """
        manager = self.env.ref('pan_mail_pro.group_mail_mailbox_manager')
        system = self.env.ref('base.group_system')
        for xmlid in ('menu_pan_mail_root',
                      'menu_pan_mail_mailbox',
                      'menu_pan_mail_routing_log',
                      'menu_communication_domains'):
            menu = self.env.ref('pan_mail_pro.%s' % xmlid)
            self.assertIn(manager, menu.group_ids, xmlid)
        for xmlid in ('menu_pan_mail_account', 'menu_pan_mail_provider'):
            menu = self.env.ref('pan_mail_pro.%s' % xmlid)
            self.assertEqual(
                menu.group_ids, system,
                "%s holds credentials and stays administrator-only" % xmlid)
