# -*- coding: utf-8 -*-
"""Guards the provider-first setup flow on the settings page.

The page shows one provider's steps at a time, driven by `x_mail_provider`.
Two things have to hold for that to be trustworthy: the picker offers exactly
the providers the registry knows about, and the "how far along am I" flags the
steps hide behind answer for the *selected* provider rather than for Microsoft.
"""
from odoo.tests import TransactionCase, tagged

from odoo.addons.pan_mail_pro.models.mail_provider_client import PROVIDER_SELECTION


@tagged('pan_mail_pro', 'post_install', '-at_install')
class TestSetupFlow(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Settings = cls.env['res.config.settings']
        cls.IrConfigParameter = cls.env['ir.config_parameter'].sudo()

    def _clear_credentials(self):
        for param in (
            'pan_mail_pro.microsoft_client_id',
            'pan_mail_pro.microsoft_tenant_id',
            'pan_mail_pro.microsoft_client_secret_encrypted',
            'pan_mail_pro.google_client_id',
            'pan_mail_pro.google_client_secret_encrypted',
            'pan_mail_pro.setup_provider',
        ):
            self.IrConfigParameter.set_param(param, '')

    def test_picker_offers_exactly_the_registered_providers(self):
        """Adding a provider to the registry must surface it in the picker."""
        selection = self.env['res.config.settings']._fields['x_mail_provider'].selection
        self.assertEqual(selection, PROVIDER_SELECTION)

    def test_no_provider_selected_means_no_steps(self):
        settings = self.Settings.new({'x_mail_provider': False})
        self.assertFalse(settings.x_provider_credentials_set)
        self.assertFalse(settings.x_provider_connected)

    def test_microsoft_credentials_complete_only_with_tenant(self):
        """Azure needs a tenant; a client id and secret alone are not setup."""
        settings = self.Settings.new({
            'x_mail_provider': 'outlook',
            'x_microsoft_client_id': 'client-id',
            'x_microsoft_client_secret': 'secret',
        })
        self.assertFalse(settings.x_provider_credentials_set)

        settings.x_microsoft_tenant_id = 'tenant-id'
        self.assertTrue(settings.x_provider_credentials_set)

    def test_google_credentials_complete_without_tenant(self):
        """Google has no tenant, so the same flags must not demand one."""
        settings = self.Settings.new({
            'x_mail_provider': 'gmail',
            'x_google_client_id': 'client-id.apps.googleusercontent.com',
            'x_google_client_secret': 'secret',
        })
        self.assertTrue(settings.x_provider_credentials_set)

    def test_imap_setup_is_measured_by_its_accounts(self):
        """IMAP has no global credential and no consent screen, so the two flags
        the steps hide behind have to read the accounts instead of this form."""
        self.assertFalse(self.Settings.new({'x_mail_provider': 'imap'}).x_provider_credentials_set)

        account = self.env['pan.mail.account'].create({
            'email': 'setup@company.test', 'provider': 'imap',
            'imap_host': 'imap.soverin.net', 'smtp_host': 'smtp.soverin.net',
        })
        settings = self.Settings.new({'x_mail_provider': 'imap'})
        self.assertTrue(settings.x_provider_credentials_set)
        # An account without a password is configured but not usable, and the
        # page must not claim otherwise.
        self.assertFalse(settings.x_provider_connected)

        account.password = 'hunter2'
        self.assertTrue(self.Settings.new({'x_mail_provider': 'imap'}).x_provider_connected)

    def test_microsoft_credentials_do_not_count_for_gmail(self):
        """Switching the picker switches which credentials are being judged."""
        settings = self.Settings.new({
            'x_mail_provider': 'gmail',
            'x_microsoft_client_id': 'client-id',
            'x_microsoft_client_secret': 'secret',
            'x_microsoft_tenant_id': 'tenant-id',
        })
        self.assertFalse(settings.x_provider_credentials_set)

    def test_existing_database_lands_on_its_configured_provider(self):
        """No stored choice + a working Azure tenant must not read as "empty"."""
        self._clear_credentials()
        self.IrConfigParameter.set_param('pan_mail_pro.microsoft_client_id', 'client-id')
        defaults = self.Settings.default_get(['x_mail_provider'])
        self.assertEqual(defaults.get('x_mail_provider'), 'outlook')

    def test_google_only_database_lands_on_gmail(self):
        self._clear_credentials()
        self.IrConfigParameter.set_param('pan_mail_pro.google_client_id', 'google-id')
        defaults = self.Settings.default_get(['x_mail_provider'])
        self.assertEqual(defaults.get('x_mail_provider'), 'gmail')

    def test_stored_choice_wins_over_the_fallback(self):
        self._clear_credentials()
        self.IrConfigParameter.set_param('pan_mail_pro.microsoft_client_id', 'client-id')
        self.IrConfigParameter.set_param('pan_mail_pro.setup_provider', 'gmail')
        defaults = self.Settings.default_get(['x_mail_provider'])
        self.assertEqual(defaults.get('x_mail_provider'), 'gmail')

    def test_unconfigured_database_preselects_nothing(self):
        self._clear_credentials()
        self.assertFalse(self.Settings.default_get(['x_mail_provider']).get('x_mail_provider'))
