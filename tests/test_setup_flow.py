# -*- coding: utf-8 -*-
"""Guards the provider-first setup flow on the settings page.

The page shows one provider's steps at a time, driven by `x_mail_provider`.
Two things have to hold for that to be trustworthy: the picker offers exactly
the providers the registry knows about, and the "how far along am I" flags the
steps hide behind answer for the *selected* provider rather than for Microsoft.
"""
from unittest.mock import patch

from odoo.exceptions import UserError
from odoo.tests import TransactionCase, tagged

from odoo.addons.pan_mail_pro.models import pan_mail_setup
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


@tagged('pan_mail_pro', 'post_install', '-at_install')
class TestSetupPhase(TransactionCase):
    """The phase, and what it stops.

    Setup is not advice. Until all five steps are answered the module is not in
    service: the cron does not fetch and "Sync Now" refuses. The rule lives in
    one place so a sixth reason to refuse cannot be invented at a call site.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Setup = cls.env['pan.mail.setup']
        # A mailbox cannot be created at all until the domains are answered —
        # see pan_mail_mailbox._check_internal_domains_configured.
        cls.env['pan.mail.domain'].set_domains(['company.test'])

    def _answers(self, **overrides):
        answers = {code: True for code, _label in pan_mail_setup.STEPS}
        answers.update(overrides)
        return answers

    def test_the_steps_are_the_contract(self):
        """A new step, or a reordering, has to be a deliberate edit here."""
        self.assertEqual(
            [code for code, _label in pan_mail_setup.STEPS],
            ['provider', 'domains', 'mailboxes'],
        )

    def test_every_step_is_mandatory(self):
        """Each one on its own is enough to hold the whole phase back, and the
        status names that step rather than a generic "not configured"."""
        for index, (code, label) in enumerate(pan_mail_setup.STEPS, start=1):
            answers = self._answers(**{code: False})
            self.assertEqual(self.Setup.phase(answers), pan_mail_setup.PHASE_SETUP,
                             f'missing {code} must keep the module in setup')
            self.assertFalse(self.Setup.is_ready(answers))
            self.assertEqual(self.Setup.blocking_step(answers)[:2], (index, code))
            self.assertIn(label, self.Setup.blocking_step_label(answers))

    def test_all_five_answered_is_syncing(self):
        answers = self._answers()
        self.assertEqual(self.Setup.phase(answers), pan_mail_setup.PHASE_SYNCING)
        self.assertTrue(self.Setup.is_ready(answers))
        self.assertFalse(self.Setup.blocking_step(answers))

    def test_the_blocking_step_is_the_first_unanswered_one(self):
        """The banner names the step to do next, not the last one that failed."""
        answers = self._answers(domains=False, mailboxes=False)
        index, code, _label = self.Setup.blocking_step(answers)
        self.assertEqual((index, code), (2, 'domains'))

    def test_connection_is_about_the_database_not_about_you(self):
        """`provider_is_connected` still answers for the database rather than
        for the reader, even though it is no longer a step of its own."""
        self.assertFalse(self.Setup.provider_is_connected('imap'))
        account = self.env['pan.mail.account'].create({
            'email': 'phase@company.test', 'provider': 'imap',
            'imap_host': 'imap.soverin.net', 'smtp_host': 'smtp.soverin.net',
            'password': 'hunter2',
        })
        self.assertFalse(account.user_id)
        self.assertTrue(self.Setup.provider_is_connected('imap'))

    def test_no_alert_when_nothing_is_broken(self):
        with patch.object(type(self.Setup), '_mailboxes_in_error',
                          return_value=self.env['pan.mail.mailbox']):
            self.assertEqual(self.Setup.mailbox_alert(), '')

    def test_half_a_provider_is_no_provider(self):
        """Picking a provider without filling in its registration is not a step
        answered — the two were separate steps and are one answer now."""
        self.assertFalse(self.Setup.answers(provider='outlook')['provider'])

    def test_a_broken_mailbox_is_an_alert_not_a_phase(self):
        """One stopped mailbox must not switch the module off for the others:
        it is a line on the checklist, never a reason to report `setup`."""
        broken = self.env['pan.mail.mailbox'].create({
            'email': 'broken@company.test',
            'provider': 'imap',
            'mailbox_type': 'shared',
            'state': 'error',
        })
        answers = self._answers()
        with patch.object(type(self.Setup), '_mailboxes_in_error', return_value=broken):
            self.assertIn('stopped syncing', self.Setup.mailbox_alert())
            self.assertTrue(self.Setup.is_ready(answers))
            self.assertEqual(self.Setup.phase(answers), pan_mail_setup.PHASE_SYNCING)

    def test_cron_fetches_nothing_during_setup(self):
        fetcher = self.env['pan.mail.fetcher']
        with patch.object(type(self.Setup), 'is_ready', return_value=False), \
                patch.object(type(fetcher), '_process_mailbox') as process:
            fetcher._cron_fetch_incoming_mail()
        process.assert_not_called()

    def test_sync_now_refuses_during_setup(self):
        mailbox = self.env['pan.mail.mailbox'].create({
            'email': 'phase-sync@company.test',
            'provider': 'imap',
            'mailbox_type': 'shared',
        })
        with patch.object(type(self.Setup), 'is_ready', return_value=False):
            with self.assertRaises(UserError):
                mailbox.action_sync_now()
