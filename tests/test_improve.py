# -*- coding: utf-8 -*-
"""Help improve Mail Pro, the browser side: what the session carries, and who
is in it.

The consent chain is tested in test_license.py (`improve_active`). This asks
what reaches the browser once it says yes: a host, a token, a pseudonymous
person, and nothing that names anyone.
"""
from odoo.tests import HttpCase, TransactionCase, tagged

from odoo.addons.pan_mail_pro.models.pan_mail_license import IMPROVE_REFUSED_PARAM


@tagged('pan_mail_pro', 'post_install', '-at_install')
class TestImproveConfig(TransactionCase):

    def setUp(self):
        super().setUp()
        self.License = self.env['pan.mail.license']
        self.link = self.License.sudo().create({
            'status': 'active',
            'improve': True,
            'improve_host': 'https://app.mailpro.pantalytics.test/i/7-abc',
            'improve_token': 'phc_test',
            'replay_sample': 0.5,
        })

    def test_the_session_config_names_the_proxy_and_a_pseudonym(self):
        config = self.License.improve_config()
        self.assertEqual(set(config), {'host', 'token', 'user', 'sample', 'version'})
        self.assertEqual(config['host'], 'https://app.mailpro.pantalytics.test/i/7-abc')
        self.assertEqual(config['token'], 'phc_test')
        self.assertEqual(config['sample'], 0.5)
        self.assertRegex(config['user'], r'^u:[0-9a-f]{12}$')
        for personal in (self.env.user.login, self.env.user.name, self.env.user.email or '@'):
            self.assertNotIn(personal, str(config))

    def test_two_users_are_two_ids_and_one_user_is_one_id(self):
        other = self.env['res.users'].create({
            'name': 'Nora Employee', 'login': 'nora@company.test',
            'group_ids': [(6, 0, [self.env.ref('base.group_user').id])],
        })
        mine = self.License.improve_config()['user']
        theirs = self.License.with_user(other).improve_config()['user']
        self.assertNotEqual(mine, theirs)
        self.assertEqual(mine, self.License.improve_config()['user'])

    def test_a_refusal_here_empties_the_session(self):
        self.env['ir.config_parameter'].sudo().set_param(IMPROVE_REFUSED_PARAM, True)
        self.assertFalse(self.License.improve_config())

    def test_the_settings_page_shows_the_refusal_only_while_it_is_on(self):
        settings = self.env['res.config.settings'].create({})
        self.assertTrue(settings.x_improve_on)
        self.assertFalse(settings.x_improve_refused)
        settings.x_improve_refused = True
        settings.execute()
        self.assertTrue(self.env['ir.config_parameter'].sudo().get_param(IMPROVE_REFUSED_PARAM))
        self.assertFalse(self.License.improve_config())
        self.link.write({'improve': False})
        self.assertFalse(self.env['res.config.settings'].create({}).x_improve_on)


@tagged('pan_mail_pro', 'post_install', '-at_install')
class TestImproveSession(HttpCase):
    """The config reaches the browser in `session_info`, and only an internal
    user's session carries it at all."""

    def test_the_session_carries_the_config_for_an_internal_user(self):
        self.env['pan.mail.license'].sudo().create({
            'status': 'active', 'improve': True,
            'improve_host': 'https://app.mailpro.pantalytics.test/i/7-abc',
            'improve_token': 'phc_test', 'replay_sample': 1.0,
        })
        self.env['res.users'].create({
            'name': 'Nora Employee', 'login': 'nora@company.test',
            'password': 'nora@company.test', 'email': 'nora@company.test',
            'group_ids': [(6, 0, [self.env.ref('base.group_user').id])],
        })
        self.authenticate('nora@company.test', 'nora@company.test')
        info = self.make_jsonrpc_request('/web/session/get_session_info', {})
        self.assertEqual(info['pan_mail_improve']['token'], 'phc_test')
        self.assertTrue(info['pan_mail_improve']['user'].startswith('u:'))
        self.assertNotIn('nora', str(info['pan_mail_improve']))

    def test_an_instance_that_never_said_yes_carries_nothing(self):
        self.env['res.users'].create({
            'name': 'Nora Employee', 'login': 'nora@company.test',
            'password': 'nora@company.test',
            'group_ids': [(6, 0, [self.env.ref('base.group_user').id])],
        })
        self.authenticate('nora@company.test', 'nora@company.test')
        info = self.make_jsonrpc_request('/web/session/get_session_info', {})
        self.assertFalse(info['pan_mail_improve'])
