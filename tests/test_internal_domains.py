# -*- coding: utf-8 -*-
"""
The internal-domain gate.

These tests exist because of a production incident: a database was never given
a list of internal domains, the filter read that as "nothing is internal", and
internal email — including confidential threads — was synced into Odoo where
anyone with record access could read it. The filter did not fail; it failed
*open*, which is worse, because nothing said so.

So what is under test here is mostly the absence of configuration:
- an empty list must block incoming sync rather than sync everything
- turning the filter off must be a deliberate act, not a default
- the block must be reachable from both directions (saving a mailbox, and a
  list emptied after the fact)
"""
from odoo.exceptions import UserError, ValidationError
from odoo.tests import TransactionCase, tagged


@tagged('pan_mail_pro', 'post_install', '-at_install')
class TestInternalDomainParsing(TransactionCase):
    """Whatever an admin types has to end up as clean domains."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Domains = cls.env['pan.mail.internal.domains']

    def test_parses_separators_and_case(self):
        self.assertEqual(
            self.Domains._parse('Company.com, second.NL;third.be\nfourth.de'),
            ['company.com', 'second.nl', 'third.be', 'fourth.de'],
        )

    def test_strips_at_sign_and_full_addresses(self):
        """Admins paste `@company.com` and `info@company.com`. Both mean the domain."""
        self.assertEqual(
            self.Domains._parse('@company.com, info@other.com'),
            ['company.com', 'other.com'],
        )

    def test_drops_duplicates_and_junk(self):
        self.assertEqual(
            self.Domains._parse('company.com, COMPANY.COM, , localhost'),
            ['company.com'],
        )

    def test_roundtrip(self):
        self.Domains.set_domains(['Company.COM', ' second.nl '])
        self.assertEqual(self.Domains.get_domains(), ['company.com', 'second.nl'])


@tagged('pan_mail_pro', 'post_install', '-at_install')
class TestInternalDomainGate(TransactionCase):
    """An unconfigured database must not sync."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Domains = cls.env['pan.mail.internal.domains']
        cls.Mailbox = cls.env['x_microsoft.mailbox']
        cls.user = cls.env['res.users'].create({
            'name': 'Gate Owner', 'login': 'gate@test.local', 'email': 'gate@test.local',
            'group_ids': [(6, 0, [cls.env.ref('base.group_user').id])],
        })
        cls.env['pan.mail.account'].sudo().create({
            'email': 'gate@test.local', 'provider': 'outlook', 'user_id': cls.user.id,
            'refresh_token': 'fake-refresh', 'access_token': 'fake-access',
        })
        # A notification mailbox has to exist before any mailbox may sync; that
        # is a separate, already-tested rule and would otherwise mask this one.
        cls.notification_mailbox = cls.Mailbox.create({
            'email': 'notifications@gate.test',
            'x_mailbox_type': 'notification',
            'x_owner_user_id': cls.user.id,
        })
        cls.Domains.set_domains([])

    def _sync_mailbox(self, **vals):
        base = {
            'email': 'support@gate.test',
            'x_mailbox_type': 'personal',
            'x_owner_user_id': self.user.id,
            'x_sync_mode': 'all',
        }
        base.update(vals)
        return self.Mailbox.create(base)

    def test_enabling_sync_without_domains_is_refused(self):
        with self.assertRaises(ValidationError):
            self._sync_mailbox()

    def test_enabling_sync_with_domains_is_allowed(self):
        self.Domains.set_domains(['gate.test'])
        mailbox = self._sync_mailbox()
        self.assertEqual(mailbox.x_sync_mode, 'all')

    def test_explicit_opt_out_unblocks(self):
        """The escape hatch has to work — but only when someone asked for it."""
        self.env['ir.config_parameter'].sudo().set_param(
            'x_pan_outlook_pro.sync_internal_email', 'True'
        )
        mailbox = self._sync_mailbox()
        self.assertEqual(mailbox.x_sync_mode, 'all')

    def test_send_only_mailbox_is_never_blocked(self):
        """The gate is about *incoming* mail. Sending has no leak to prevent."""
        mailbox = self._sync_mailbox(x_sync_mode='none')
        self.assertEqual(mailbox.x_sync_mode, 'none')

    def test_sync_run_refuses_when_domains_removed_later(self):
        """The constraint guards configuration; this guards the list being emptied."""
        self.Domains.set_domains(['gate.test'])
        mailbox = self._sync_mailbox()
        self.Domains.set_domains([])

        with self.assertRaises(UserError):
            self.env['microsoft.incoming.mail.processor']._process_mailbox(mailbox)

    def test_cron_records_the_block_on_the_mailbox(self):
        """A blocked sync must be visible, not just logged."""
        self.Domains.set_domains(['gate.test'])
        mailbox = self._sync_mailbox()
        mailbox.write({'state': 'active'})
        self.Domains.set_domains([])

        self.env['microsoft.incoming.mail.processor']._cron_fetch_incoming_mail()

        self.assertEqual(mailbox.state, 'error')
        self.assertIn('internal email domains', mailbox.x_error_message)


@tagged('pan_mail_pro', 'post_install', '-at_install')
class TestInternalDomainFiltering(TransactionCase):
    """Who gets skipped, and who decides."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Domains = cls.env['pan.mail.internal.domains']
        cls.Domains.set_domains(['company.com'])
        cls.mailbox = cls.env['x_microsoft.mailbox'].create({
            'email': 'info@company.com', 'x_mailbox_type': 'shared',
        })

    def test_internal_sender_is_skipped(self):
        self.assertTrue(self.Domains.should_skip('colleague@company.com', self.mailbox))

    def test_external_sender_is_kept(self):
        self.assertFalse(self.Domains.should_skip('customer@example.com', self.mailbox))

    def test_global_opt_out_keeps_everything(self):
        self.env['ir.config_parameter'].sudo().set_param(
            'x_pan_outlook_pro.sync_internal_email', 'True'
        )
        self.assertFalse(self.Domains.should_skip('colleague@company.com', self.mailbox))

    def test_per_mailbox_opt_out_keeps_everything_for_that_mailbox(self):
        self.mailbox.x_exclude_internal = False
        self.assertFalse(self.Domains.should_skip('colleague@company.com', self.mailbox))

    def test_empty_list_no_longer_means_nothing_is_internal(self):
        """The original bug, pinned.

        With no domains configured `is_internal` is False for everything — but
        that state is now unreachable for a syncing mailbox, because the gate
        refuses it. This asserts the pairing, not just the filter.
        """
        self.Domains.set_domains([])
        self.assertFalse(self.Domains.is_internal('colleague@company.com'))
        self.assertTrue(self.Domains.configuration_error())


@tagged('pan_mail_pro', 'post_install', '-at_install')
class TestInternalDomainSuggestions(TransactionCase):
    """Filling the list in must be easier than skipping it."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Domains = cls.env['pan.mail.internal.domains']
        cls.env['x_microsoft.mailbox'].create({
            'email': 'info@suggested.test', 'x_mailbox_type': 'shared',
        })

    def test_mailbox_domains_are_suggested(self):
        self.assertIn('suggested.test', self.Domains.suggest_domains())

    def test_uncovered_mailbox_domain_is_reported(self):
        """A mailbox we send from is ours by definition — a list missing it is wrong."""
        self.Domains.set_domains(['elsewhere.test'])
        self.assertEqual(self.Domains.uncovered_mailbox_domains(), ['suggested.test'])

    def test_nothing_uncovered_when_list_matches(self):
        self.Domains.set_domains(['suggested.test'])
        self.assertEqual(self.Domains.uncovered_mailbox_domains(), [])
