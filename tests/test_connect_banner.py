# -*- coding: utf-8 -*-
"""Who gets asked to connect their mailbox, and who is left alone.

The banner itself is four lines of OWL. The decision behind it is the part
worth guarding: it is shown on every screen, so every case where the button
would not work is a case where the module nags about something the reader
cannot do.
"""
from datetime import timedelta

from odoo import fields
from odoo.addons.pan_mail_pro.controllers.main import SETTINGS_URL
from odoo.exceptions import AccessError, UserError
from odoo.tests import HttpCase, TransactionCase, tagged


@tagged('pan_mail_pro', 'post_install', '-at_install')
class TestConnectBanner(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env['pan.mail.domain'].set_domains(['company.test'])
        cls.user = cls.env['res.users'].create({
            'name': 'Nora Employee',
            'login': 'nora@company.test',
            'email': 'nora@company.test',
            'group_ids': [(6, 0, [cls.env.ref('base.group_user').id])],
        })

    def _set_up_provider(self, provider='gmail', **overrides):
        vals = {
            'provider': provider,
            'client_id': 'id', 'client_secret': 'secret',
        }
        vals.update(overrides)
        return self.env['pan.mail.provider'].create(vals)

    def test_no_provider_no_banner(self):
        """`action_connect_mailbox` raises without one, so the button is a
        dead end and the banner would be an invitation to an error dialog."""
        self.assertFalse(self.user._pan_mail_should_prompt_connect())

    def test_incomplete_registration_no_banner(self):
        """Half a provider is no provider: the consent URL cannot be built."""
        self._set_up_provider(client_secret=False)
        self.assertFalse(self.user._pan_mail_should_prompt_connect())

    def test_a_usable_provider_asks_the_user(self):
        self._set_up_provider()
        self.assertTrue(self.user._pan_mail_should_prompt_connect())

    def test_setup_being_unfinished_does_not_silence_it(self):
        """The first person to connect is usually the admin on step 3, who
        needs an owner for the notification mailbox. A banner that waits for
        setup waits for the thing it exists to unblock."""
        self._set_up_provider()
        self.assertFalse(self.env['pan.mail.setup'].is_ready())
        self.assertTrue(self.user._pan_mail_should_prompt_connect())

    def test_a_password_provider_has_nothing_to_click(self):
        """IMAP credentials are typed in by an administrator on the account.
        There is no sign-in screen to send this user to."""
        self._set_up_provider(provider='imap', client_id=False, client_secret=False)
        self.assertFalse(self.user._pan_mail_should_prompt_connect())

    def test_a_connected_user_is_left_alone(self):
        self._set_up_provider()
        self.env['pan.mail.account'].create({
            'email': 'nora@company.test',
            'provider': 'gmail',
            'user_id': self.user.id,
            'refresh_token': 'token',
        })
        self.assertTrue(self.user.x_pan_mail_connected)
        self.assertFalse(self.user._pan_mail_should_prompt_connect())

    def test_a_copy_never_invites_anyone(self):
        """Connecting on a staging restore hands a throwaway database real
        credentials. Same question `decrypt_value` asks, asked earlier."""
        self._set_up_provider()
        self.env['ir.config_parameter'].sudo().set_param('database.is_neutralized', 'True')
        self.assertFalse(self.user._pan_mail_should_prompt_connect())

    def test_an_unconnected_instance_asks_nobody(self):
        """A new account is refused until the instance is connected to
        Pantalytics, so the button would end in a refusal after the consent
        screen. The banner stays away, and the button refuses first."""
        self._set_up_provider()
        real_gate = self.user.with_context(pan_mail_pro_real_gate=True)
        self.assertFalse(real_gate._pan_mail_should_prompt_connect())
        with self.assertRaisesRegex(UserError, 'Connect this Odoo instance'):
            real_gate.action_connect_mailbox('gmail')
        self.env['pan.mail.license'].sudo().create({
            'status': 'active', 'valid_until': fields.Datetime.now() + timedelta(days=1)})
        self.assertTrue(real_gate._pan_mail_should_prompt_connect())
        self.assertEqual(real_gate.action_connect_mailbox('gmail')['type'], 'ir.actions.act_url')

    def test_a_portal_user_may_not_connect_a_mailbox(self):
        portal = self.env['res.users'].create({
            'name': 'Customer',
            'login': 'customer-connect@example.test',
            'group_ids': [(6, 0, [self.env.ref('base.group_portal').id])],
        })
        self._set_up_provider()
        with self.assertRaises(AccessError):
            portal.action_connect_mailbox('gmail')

    def test_a_portal_user_is_not_asked(self):
        portal = self.env['res.users'].create({
            'name': 'Customer',
            'login': 'customer@example.test',
            'group_ids': [(6, 0, [self.env.ref('base.group_portal').id])],
        })
        self._set_up_provider()
        self.assertFalse(portal._pan_mail_should_prompt_connect())


@tagged('pan_mail_pro', 'post_install', '-at_install')
class TestConnectBannerSession(HttpCase):
    """The flag reaches the browser in `session_info`.

    The banner reads it there rather than making an RPC of its own, so it draws
    with the first paint instead of appearing a moment after the screen has
    settled. That only holds while the key is actually in the session payload,
    which is what this asks.
    """

    def test_the_session_carries_the_answer(self):
        self.env['pan.mail.domain'].set_domains(['company.test'])
        self.env['pan.mail.provider'].create({
            'provider': 'gmail',
            'client_id': 'id', 'client_secret': 'secret',
        })
        self.env['res.users'].create({
            'name': 'Nora Employee',
            'login': 'nora@company.test',
            'password': 'nora@company.test',
            'email': 'nora@company.test',
            'group_ids': [(6, 0, [self.env.ref('base.group_user').id])],
        })
        self.authenticate('nora@company.test', 'nora@company.test')

        info = self.make_jsonrpc_request('/web/session/get_session_info', {})

        self.assertTrue(info['pan_mail_connect_prompt'])
        # Door 1's button rides the same payload. An employee who may not
        # open the Inbox never gets one, so the chatter never calls the read
        # layer to be refused by it.
        self.assertFalse(info['pan_mail_inbox'])

    def test_the_session_says_who_may_open_the_inbox(self):
        self.env['pan.mail.domain'].set_domains(['company.test'])
        self.env['res.users'].create({
            'name': 'Mira Manager',
            'login': 'mira@company.test',
            'password': 'mira@company.test',
            'email': 'mira@company.test',
            'group_ids': [(6, 0, [
                self.env.ref('base.group_user').id,
                self.env.ref('pan_mail_pro.group_mail_mailbox_manager').id,
            ])],
        })
        self.authenticate('mira@company.test', 'mira@company.test')

        info = self.make_jsonrpc_request('/web/session/get_session_info', {})

        self.assertTrue(info['pan_mail_inbox'])
        # Whether the Inbox opens at all rides the same payload. The suite
        # runs connected (tests/connected.py), so the answer here is yes;
        # the no, and the screen it draws, is tools/ui_check.py's.
        self.assertIn('pan_mail_connected', info)
        self.assertTrue(info['pan_mail_connected'])


@tagged('pan_mail_pro', 'post_install', '-at_install')
class TestConnectRoute(HttpCase):
    """The button behind the banner, followed.

    Everything above asks whether the banner is *shown*. Nothing asked where
    it goes, and it went nowhere: `get_provider_client` returns a recordset,
    an empty recordset is falsy, and `if not client` was therefore true for
    every provider that exists. Every click landed on the settings page --
    which for the employee the banner is aimed at is a page they cannot open,
    so the button read as dead.
    """

    def _connect(self):
        return self.url_open('/mail_pro/connect', allow_redirects=False)

    def setUp(self):
        super().setUp()
        self.env['pan.mail.domain'].set_domains(['company.test'])
        self.env['res.users'].create({
            'name': 'Nora Employee',
            'login': 'nora@company.test',
            'password': 'nora@company.test',
            'email': 'nora@company.test',
            'group_ids': [(6, 0, [self.env.ref('base.group_user').id])],
        })
        self.authenticate('nora@company.test', 'nora@company.test')

    def test_the_button_reaches_the_consent_screen(self):
        self.env['pan.mail.provider'].create({
            'provider': 'gmail', 'client_id': 'id', 'client_secret': 'secret',
        })

        response = self._connect()

        self.assertEqual(response.status_code, 303)
        self.assertTrue(
            response.headers['Location'].startswith(
                'https://accounts.google.com/o/oauth2/v2/auth'),
            'the connect button does not reach the provider: %s'
            % response.headers['Location'])

    def test_a_password_provider_goes_to_the_settings_page(self):
        """IMAP has no consent screen, so there is nowhere else to send."""
        self.env['pan.mail.provider'].create({'provider': 'imap'})

        response = self._connect()

        self.assertEqual(response.status_code, 303)
        self.assertTrue(response.headers['Location'].endswith(SETTINGS_URL))

    def test_no_provider_goes_to_the_settings_page(self):
        response = self._connect()

        self.assertEqual(response.status_code, 303)
        self.assertTrue(response.headers['Location'].endswith(SETTINGS_URL))


@tagged('pan_mail_pro', 'post_install', '-at_install')
class TestConnectInvites(TransactionCase):
    """Who receives the invitation, and who is spared it.

    The button is reached by selecting rows in the user list, and the natural
    gesture there is "select all". So the filter has to be in the code: an
    invitation to somebody who has already connected, or who is on a provider
    where an administrator types the password, is a mail that can only confuse
    the reader.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env['pan.mail.domain'].set_domains(['company.test'])
        cls.env['pan.mail.provider'].create({
            'provider': 'gmail', 'client_id': 'id', 'client_secret': 'secret',
        })
        cls.employee = cls.env['res.users'].create({
            'name': 'Nora Employee',
            'login': 'nora@company.test',
            'email': 'nora@company.test',
            'group_ids': [(6, 0, [cls.env.ref('base.group_user').id])],
        })
        cls.connected = cls.env['res.users'].create({
            'name': 'Sam Connected',
            'login': 'sam@company.test',
            'email': 'sam@company.test',
            'group_ids': [(6, 0, [cls.env.ref('base.group_user').id])],
        })
        cls.env['pan.mail.account'].create({
            'provider': 'gmail',
            'user_id': cls.connected.id,
            'email': 'sam@company.test',
            'refresh_token': 'token',
        })

    def test_an_unconnected_user_is_invited(self):
        self.assertEqual(self.employee._send_connect_invites(), 1)

    def test_a_connected_user_is_not_invited(self):
        self.assertTrue(self.connected.x_pan_mail_connected)
        self.assertEqual(self.connected._send_connect_invites(), 0)

    def test_selecting_everybody_only_mails_who_needs_it(self):
        both = self.employee | self.connected
        self.assertEqual(both._send_connect_invites(), 1)

    def test_a_user_without_an_address_is_not_invited(self):
        self.employee.partner_id.email = False
        self.assertEqual(self.employee._send_connect_invites(), 0)
