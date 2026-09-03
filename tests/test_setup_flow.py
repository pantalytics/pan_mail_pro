# -*- coding: utf-8 -*-
"""Guards the setup phase: which step is blocking, and what it stops.

The provider's own picker and credential-completeness rules moved to
`tests/test_pan_mail_provider.py` with the fields they guard — this file is
only about the three-step phase built on top of them.
"""
from unittest.mock import patch

from odoo.exceptions import UserError
from odoo.tests import TransactionCase, tagged

from odoo.addons.pan_mail_pro.models import pan_mail_setup


@tagged('pan_mail_pro', 'post_install', '-at_install')
class TestSetupPhase(TransactionCase):
    """The phase, and what it stops.

    Setup is not advice. Until all three steps are answered the module is not
    in service: the cron does not fetch and "Sync Now" refuses. The rule lives
    in one place so a fourth reason to refuse cannot be invented at a call
    site.
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

    def test_all_three_answered_is_syncing(self):
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
        """A provider with no registered credentials is not a step answered —
        the two used to be separate steps and are one answer now."""
        self.assertFalse(self.Setup.answers(provider='outlook')['provider'])

    def test_a_provider_row_answers_the_step(self):
        """Once `pan.mail.provider` has a complete, in-use row, step 1 reads
        as answered — this is the seam `res.config.settings` reads too."""
        self.env['pan.mail.provider'].create({
            'provider': 'gmail', 'in_use': True,
            'client_id': 'id', 'client_secret': 'secret',
        })
        self.assertTrue(self.Setup.answers()['provider'])

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
