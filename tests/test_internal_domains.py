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
        cls.Mailbox = cls.env['pan.mail.mailbox']
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
        # The gate now guards every mailbox, so the fixture opens it to build
        # its own scaffolding and closes it again below.
        cls.Domains.set_domains(['gate.test'])
        cls.notification_mailbox = cls.Mailbox.create({
            'email': 'notifications@gate.test',
            'mailbox_type': 'notification',
            'owner_user_id': cls.user.id,
        })
        cls.Domains.set_domains([])

    def _sync_mailbox(self, **vals):
        base = {
            'email': 'support@gate.test',
            'mailbox_type': 'personal',
            'owner_user_id': self.user.id,
            'sync_mode': 'all',
        }
        base.update(vals)
        return self.Mailbox.create(base)

    def test_enabling_sync_without_domains_is_refused(self):
        with self.assertRaises(ValidationError):
            self._sync_mailbox()

    def test_enabling_sync_with_domains_is_allowed(self):
        self.Domains.set_domains(['gate.test'])
        mailbox = self._sync_mailbox()
        self.assertEqual(mailbox.sync_mode, 'all')

    def test_explicit_opt_out_unblocks(self):
        """The escape hatch has to work — but only when someone asked for it."""
        self.env['ir.config_parameter'].sudo().set_param(
            'pan_mail_pro.sync_internal_email', 'True'
        )
        mailbox = self._sync_mailbox()
        self.assertEqual(mailbox.sync_mode, 'all')

    def test_a_send_only_mailbox_is_blocked_too(self):
        """The gate moved from the sync switch to the mailbox.

        It used to let a send-only mailbox through, on the reasoning that
        sending has no leak to prevent. True of that mailbox on that day, and
        the reason the setting read as an option belonging to sync — right up
        until somebody flipped the switch. A mailbox is where Mail Pro takes
        over the company's mail, so it is where the question gets asked.
        """
        with self.assertRaises(ValidationError):
            self._sync_mailbox(sync_mode='none')

    def test_domains_let_a_send_only_mailbox_through(self):
        self.Domains.set_domains(['gate.test'])
        mailbox = self._sync_mailbox(sync_mode='none')
        self.assertEqual(mailbox.sync_mode, 'none')

    def test_sync_run_refuses_when_domains_removed_later(self):
        """The constraint guards configuration; this guards the list being emptied."""
        self.Domains.set_domains(['gate.test'])
        mailbox = self._sync_mailbox()
        self.Domains.set_domains([])

        with self.assertRaises(UserError):
            self.env['pan.mail.fetcher']._process_mailbox(mailbox)

    def test_cron_records_the_block_on_the_mailbox(self):
        """A blocked sync must be visible, not just logged."""
        self.Domains.set_domains(['gate.test'])
        mailbox = self._sync_mailbox()
        mailbox.write({'state': 'active'})
        self.Domains.set_domains([])

        self.env['pan.mail.fetcher']._cron_fetch_incoming_mail()

        self.assertEqual(mailbox.state, 'error')
        self.assertIn('internal email domains', mailbox.error_message)


@tagged('pan_mail_pro', 'post_install', '-at_install')
class TestInternalDomainFiltering(TransactionCase):
    """Who gets skipped, and who decides."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Domains = cls.env['pan.mail.internal.domains']
        cls.Domains.set_domains(['company.com'])
        cls.mailbox = cls.env['pan.mail.mailbox'].create({
            'email': 'info@company.com', 'mailbox_type': 'shared',
        })

    def test_internal_sender_is_skipped(self):
        self.assertTrue(self.Domains.should_skip('colleague@company.com', self.mailbox))

    def test_external_sender_is_kept(self):
        self.assertFalse(self.Domains.should_skip('customer@example.com', self.mailbox))

    def test_global_opt_out_keeps_everything(self):
        self.env['ir.config_parameter'].sudo().set_param(
            'pan_mail_pro.sync_internal_email', 'True'
        )
        self.assertFalse(self.Domains.should_skip('colleague@company.com', self.mailbox))

    def test_per_mailbox_opt_out_keeps_everything_for_that_mailbox(self):
        self.mailbox.exclude_internal = False
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
        cls.Domains.set_domains(['scaffolding.test'])
        cls.env['pan.mail.mailbox'].create({
            'email': 'info@suggested.test', 'mailbox_type': 'shared',
        })

    def test_mailbox_domains_are_suggested(self):
        self.assertIn('suggested.test', self.Domains.suggest_domains())

    def test_uncovered_mailbox_domain_is_reported(self):
        """A mailbox we send from is ours by definition — a list missing it is wrong."""
        self.Domains.set_domains(['elsewhere.test'])
        self.assertEqual(self.Domains.uncovered_domains(), ['suggested.test'])

    def test_nothing_uncovered_when_list_matches(self):
        self.Domains.set_domains(['suggested.test'])
        self.assertEqual(self.Domains.uncovered_domains(), [])


@tagged('pan_mail_pro', 'post_install', '-at_install')
class TestInternalDomainCompleteness(TransactionCase):
    """Configured is not the same as complete, and only complete is worth much.

    A list that names the obvious domain and misses a second one passes every
    check that asks whether it is configured, and then treats a colleague on
    that second domain as a customer. That is the original leak, on a database
    nothing objects to. Juffermans Machinebouw is the shape: every mailbox on
    one domain, a colleague on another.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Domains = cls.env['pan.mail.internal.domains']
        cls.Domains.set_domains(['scaffolding.test'])
        cls.Mailbox = cls.env['pan.mail.mailbox']
        cls.Mailbox.create({
            'email': 'info@first.test', 'mailbox_type': 'shared',
        })
        cls.colleague = cls.env['res.users'].create({
            'name': 'Colleague On Another Domain',
            'login': 'wvb@second.test',
            'email': 'wvb@second.test',
            'group_ids': [(6, 0, [cls.env.ref('base.group_user').id])],
        })

    def test_a_colleagues_domain_is_suggested(self):
        """The source that catches a second domain. A company that acquired
        another one has colleagues on it long before it has mailboxes on it."""
        self.assertIn('second.test', self.Domains.suggest_domains())

    def test_a_customer_with_a_login_is_not_the_company(self):
        """Portal users have logins too, and folding their domain in would mark
        a customer's mail internal and quietly stop syncing it."""
        portal = self.env['res.users'].create({
            'name': 'Customer With Portal Access',
            'login': 'buyer@customer.test',
            'email': 'buyer@customer.test',
            'group_ids': [(6, 0, [self.env.ref('base.group_portal').id])],
        })
        self.assertTrue(portal.share, "fixture must be a share user to prove this")
        self.assertNotIn('customer.test', self.Domains.suggest_domains())

    def test_a_list_missing_our_own_domain_is_incomplete(self):
        self.Domains.set_domains(['first.test'])

        missing = self.Domains.uncovered_domains()

        self.assertIn('second.test', missing)
        self.assertIsNotNone(self.Domains.completeness_error())

    def test_a_complete_list_reports_nothing(self):
        self.Domains.set_domains(['first.test', 'second.test', 'scaffolding.test'])

        self.assertEqual(self.Domains.uncovered_domains(), [])
        self.assertIsNone(self.Domains.completeness_error())

    def test_an_empty_list_is_absent_rather_than_incomplete(self):
        """Two different failures with two different gates. This one is the
        mailbox constraint's job, and saying both at once would be noise."""
        self.Domains.set_domains([])

        self.assertEqual(self.Domains.uncovered_domains(), [])
        self.assertIsNone(self.Domains.completeness_error())
        self.assertIsNotNone(self.Domains.configuration_error())

    def test_saving_settings_refuses_an_incomplete_list(self):
        settings = self.env['res.config.settings'].create({
            'x_internal_domains': 'first.test',
        })

        with self.assertRaises(UserError):
            settings.set_values()

    def test_saving_settings_accepts_a_complete_list(self):
        settings = self.env['res.config.settings'].create({
            'x_internal_domains': 'first.test, second.test, scaffolding.test',
        })

        settings.set_values()

        self.assertIn('second.test', self.Domains.get_domains())
