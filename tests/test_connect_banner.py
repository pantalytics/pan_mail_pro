# -*- coding: utf-8 -*-
"""Who gets asked to connect their mailbox, and who is left alone.

The banner itself is four lines of OWL. The decision behind it is the part
worth guarding: it is shown on every screen, so every case where the button
would not work is a case where the module nags about something the reader
cannot do.
"""
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
