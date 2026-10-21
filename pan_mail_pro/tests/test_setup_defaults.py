# -*- coding: utf-8 -*-
"""Steps 3 and 4 proposed by the first sign-in instead of asked.

The OAuth callback calls `pan.mail.setup.propose_internal_domain` before it
claims the personal mailbox and `propose_notification_mailbox` after. Both only
fill an empty answer, both only for a mailbox manager, and the domain never
comes from a public mail provider.
"""
from odoo.tests import TransactionCase, tagged


@tagged('pan_mail_pro', 'post_install', '-at_install')
class TestSetupDefaults(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Setup = cls.env['pan.mail.setup']
        cls.Domain = cls.env['pan.mail.domain'].sudo()
        cls.Mailbox = cls.env['pan.mail.mailbox'].sudo()
        cls.admin = cls.env.ref('base.user_admin')
        cls.plain = cls.env['res.users'].with_context(no_reset_password=True).create({
            'name': 'Plain User',
            'login': 'plain@acme.test',
            'email': 'plain@acme.test',
            'group_ids': [(6, 0, [cls.env.ref('base.group_user').id])],
        })
        # Whatever the database shipped with is not this test's starting point.
        cls.Domain.search([]).unlink()

    def _connect(self, user, email):
        return self.env['pan.mail.account'].sudo().create({
            'email': email, 'provider': 'outlook', 'user_id': user.id,
            'refresh_token': 'fake-refresh', 'access_token': 'fake-access',
        })

    # --- the internal domain ---------------------------------------------

    def test_the_first_sign_in_proposes_its_own_domain(self):
        self.Setup.propose_internal_domain(self.admin, 'Rutger@Acme.test')
        self.assertEqual(self.Domain.get_domains(), ['acme.test'])

    def test_a_list_that_exists_is_left_alone(self):
        self.Domain.set_domains(['company.test'])
        self.Setup.propose_internal_domain(self.admin, 'rutger@acme.test')
        self.assertEqual(self.Domain.get_domains(), ['company.test'])

    def test_a_public_mail_domain_is_never_proposed(self):
        self.Setup.propose_internal_domain(self.admin, 'someone@gmail.com')
        self.assertFalse(self.Domain.is_configured())

    def test_a_plain_user_does_not_decide_the_company_domains(self):
        self.Setup.propose_internal_domain(self.plain, 'plain@acme.test')
        self.assertFalse(self.Domain.is_configured())

    # --- the notification mailbox ----------------------------------------

    def _admin_mailbox(self):
        self.Domain.set_domains(['acme.test'])
        self._connect(self.admin, 'boss@acme.test')
        return self.Mailbox.create({
            'email': 'boss@acme.test', 'provider': 'outlook',
            'owner_user_id': self.admin.id,
        })

    def test_the_admins_own_mailbox_becomes_the_notification_mailbox(self):
        mailbox = self._admin_mailbox()
        self.Setup.propose_notification_mailbox(self.admin, 'boss@acme.test')
        self.assertTrue(mailbox.is_notification_mailbox)

    def test_a_notification_mailbox_that_exists_is_left_alone(self):
        mailbox = self._admin_mailbox()
        mailbox.is_notification_mailbox = True
        self._connect(self.plain, 'plain@acme.test')
        other = self.Mailbox.create({
            'email': 'plain@acme.test', 'provider': 'outlook',
            'owner_user_id': self.plain.id,
        })
        self.Setup.propose_notification_mailbox(self.admin, 'plain@acme.test')
        self.assertTrue(mailbox.is_notification_mailbox)
        self.assertFalse(other.is_notification_mailbox)

    def test_a_plain_users_sign_in_does_not_pick_where_system_mail_goes(self):
        self.Domain.set_domains(['acme.test'])
        self._connect(self.plain, 'plain@acme.test')
        mailbox = self.Mailbox.create({
            'email': 'plain@acme.test', 'provider': 'outlook',
            'owner_user_id': self.plain.id,
        })
        self.Setup.propose_notification_mailbox(self.plain, 'plain@acme.test')
        self.assertFalse(mailbox.is_notification_mailbox)
