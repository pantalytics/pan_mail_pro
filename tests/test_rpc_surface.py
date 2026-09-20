# -*- coding: utf-8 -*-
"""Every public method on a model answers `call_kw` from any logged-in session.

A group on a menu is not an access rule, and a `sudo()` inside a public
`@api.model` method is a way for anybody to do what that method does. The
Inbox learnt this in 19.0.10.0.0 (`pan.mail.conversation._check_caller`); the
domain list, the Pantalytics connection and the two indexes had not. So this
file walks them as a plain user and expects a refusal, and it pins the public
surface of the models that search as sudo, so a new public method there is a
decision somebody made rather than a door somebody left open.
"""
from odoo.exceptions import AccessError
from odoo.tests import TransactionCase, new_test_user, tagged

# Public methods that may stay public: they read nothing a plain user could
# not compute themselves, or take a recordset no RPC caller can hand over.
ALLOWED_PUBLIC = {
    'pan.mail.matcher': {'describe', 'thread_keys'},
    'pan.mail.message.ref': {'record'},
    'pan.mail.thread.link': {'record', 'record_all'},
}


@tagged('pan_mail_pro', 'post_install', '-at_install')
class TestRpcSurface(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.plain = new_test_user(cls.env, login='rpc_plain', groups='base.group_user')
        cls.manager = new_test_user(
            cls.env, login='rpc_manager',
            groups='base.group_user,pan_mail_pro.group_mail_mailbox_manager')
        cls.env['pan.mail.domain'].set_domains(['surface.test'])

    def test_the_domain_list_is_a_managers_to_change(self):
        Domains = self.env['pan.mail.domain']
        with self.assertRaises(AccessError):
            Domains.with_user(self.plain).set_domains(['evil.test'])
        self.assertEqual(Domains.get_domains(), ['surface.test'])

        Domains.with_user(self.manager).set_domains(['surface.test', 'second.test'])
        self.assertEqual(sorted(Domains.get_domains()), ['second.test', 'surface.test'])

    def test_the_pantalytics_connection_is_an_administrators(self):
        License = self.env['pan.mail.license']
        with self.assertRaises(AccessError):
            License.with_user(self.plain).action_connect()
        link = License.sudo().create({'status': 'pending'})
        with self.assertRaises(AccessError):
            link.with_user(self.plain).action_check_approval()
        with self.assertRaises(AccessError):
            link.with_user(self.plain).action_disconnect()
        self.assertEqual(link.status, 'pending')

    def test_the_sudo_lookups_are_not_public(self):
        """`match`, `lookup` and `find_for_record` searched as sudo across every
        document model and answered which record a reference belongs to.
        They are private now; this is what keeps them so."""
        # `base` is the registry's own abstract model: every method that web,
        # mail and the ORM hang on all models, and nothing of this module's.
        base = set(dir(type(self.env['base'])))
        for model_name, allowed in ALLOWED_PUBLIC.items():
            Model = type(self.env[model_name])
            public = {
                name for name in dir(Model)
                if not name.startswith('_') and callable(getattr(Model, name, None))
            } - base
            self.assertEqual(
                public, allowed,
                f'{model_name} grew a public method; decide whether RPC may call it '
                f'and add it to ALLOWED_PUBLIC, or make it private')
