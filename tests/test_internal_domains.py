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
        cls.Domains = cls.env['pan.mail.domain']

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
        cls.Domains = cls.env['pan.mail.domain']
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
            'mailbox_type': 'personal',
            'is_notification_mailbox': True,
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
        """A blocked sync must be visible, not just logged.

        Emptying the list puts the module back into the setup phase, so the
        cron stops before it fetches anything — but it still says so on the
        mailbox, which is where somebody looks.
        """
        self.Domains.set_domains(['gate.test'])
        mailbox = self._sync_mailbox()
        mailbox.write({'state': 'active'})
        self.Domains.set_domains([])

        self.env['pan.mail.fetcher']._cron_fetch_incoming_mail()

        self.assertEqual(mailbox.state, 'error')
        self.assertIn('still being set up', mailbox.error_message)


@tagged('pan_mail_pro', 'post_install', '-at_install')
class TestInternalDomainFiltering(TransactionCase):
    """Who gets skipped, and who decides."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Domains = cls.env['pan.mail.domain']
        cls.Domains.set_domains(['company.com'])
        cls.mailbox = cls.env['pan.mail.mailbox'].create({
            'email': 'info@company.com', 'mailbox_type': 'shared',
        })

    def test_internal_sender_is_skipped(self):
        self.assertTrue(self.Domains.should_skip('colleague@company.com', self.mailbox))

    def test_external_sender_is_kept(self):
        self.assertFalse(self.Domains.should_skip('customer@example.com', self.mailbox))

    def test_there_is_no_opt_out(self):
        """The filter has no off switch, globally or per mailbox.

        The old global parameter is still readable by anything that kept a
        reference to it; setting it must change nothing. See ARCHITECTURE.md
        §9.12 for why the escape hatch was removed rather than defaulted off.
        """
        self.env['ir.config_parameter'].sudo().set_param(
            'pan_mail_pro.sync_internal_email', 'True'
        )
        self.assertTrue(self.Domains.should_skip('colleague@company.com', self.mailbox))
        self.assertNotIn('exclude_internal', self.env['pan.mail.mailbox']._fields)

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
        cls.Domains = cls.env['pan.mail.domain']
        cls.Domains.set_domains(['scaffolding.test'])
        cls.env['pan.mail.mailbox'].create({
            'email': 'info@suggested.test', 'mailbox_type': 'shared',
        })

    def test_mailbox_domains_are_suggested(self):
        self.assertIn('suggested.test', self.Domains.suggest_domains())

    def test_uncovered_mailbox_domain_is_reported(self):
        """A mailbox we send from is ours by definition — a list missing it is wrong."""
        self.Domains.set_domains(['elsewhere.test'])
        self.assertIn('suggested.test', self.Domains.uncovered_domains())

    def test_nothing_uncovered_when_list_matches(self):
        """Everything the database can demonstrate is ours, not only the
        mailbox: the internal users' own domains count too, which is the whole
        point of the check."""
        self.Domains.set_domains(self.Domains.suggest_domains())
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
        cls.Domains = cls.env['pan.mail.domain']
        cls.Domains.set_domains(['scaffolding.test'])
        cls.Mailbox = cls.env['pan.mail.mailbox']
        cls.Mailbox.create({
            'email': 'info@first.test', 'mailbox_type': 'shared',
        })
        # `no_reset_password` because creating a user with an email otherwise
        # sends the signup mail, and by this point the mailbox above has already
        # switched Odoo's outgoing mail onto the provider.
        cls.colleague = cls.env['res.users'].with_context(
            no_reset_password=True
        ).create({
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
        portal = self.env['res.users'].with_context(
            no_reset_password=True
        ).create({
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

    def test_applying_the_suggestion_always_makes_the_list_complete(self):
        """The property the "Apply suggested" button promises.

        Hardcoding the expected domains would test this fixture rather than the
        rule: any database carries users the fixture did not create, and the
        real question is whether one click can ever leave the admin with a list
        the save still refuses.
        """
        self.Domains.set_domains(self.Domains.suggest_domains())

        self.assertEqual(self.Domains.uncovered_domains(), [])
        self.assertIsNone(self.Domains.completeness_error())

    def test_a_personal_address_does_not_drag_its_provider_in(self):
        """A colleague whose Odoo login is a personal address must not put a
        mail provider's domain on the internal list. Marking it internal stops
        syncing every customer on that provider, silently."""
        self.env['res.users'].with_context(no_reset_password=True).create({
            'name': 'Colleague With A Personal Login',
            'login': 'someone@gmail.com',
            'email': 'someone@gmail.com',
            'group_ids': [(6, 0, [self.env.ref('base.group_user').id])],
        })

        self.assertNotIn('gmail.com', self.Domains.suggest_domains())
        self.assertNotIn('gmail.com', self.Domains.uncovered_domains())

    def test_an_empty_list_is_absent_rather_than_incomplete(self):
        """Two different failures with two different gates. This one is the
        mailbox constraint's job, and saying both at once would be noise."""
        self.Domains.set_domains([])

        self.assertEqual(self.Domains.uncovered_domains(), [])
        self.assertIsNone(self.Domains.completeness_error())
        self.assertIsNotNone(self.Domains.configuration_error())

    def _settings_with(self, domains):
        rows = self.Domains.create([{'name': d} for d in domains])
        return self.env['res.config.settings'].create({
            'x_internal_domain_ids': [(6, 0, rows.ids)],
        })

    def test_saving_settings_refuses_an_incomplete_list(self):
        with self.assertRaises(UserError):
            self._settings_with(['first.test']).set_values()

    def test_saving_settings_accepts_a_complete_list(self):
        self._settings_with(self.Domains.suggest_domains()).set_values()

        self.assertIn('second.test', self.Domains.get_domains())

    def test_a_domain_removed_from_the_form_is_removed_from_the_table(self):
        """The tags widget is the whole list, so an untagged domain is gone."""
        self.Domains.set_domains(['gone.test'] + self.Domains.suggest_domains())
        keep = self.Domains.search([('name', '!=', 'gone.test')])

        self.env['res.config.settings'].create({
            'x_internal_domain_ids': [(6, 0, keep.ids)],
        }).set_values()

        self.assertNotIn('gone.test', self.Domains.get_domains())

    def test_a_domain_is_cleaned_on_the_way_in(self):
        """An admin pastes an address or a stray @; a domain comes out."""
        self.assertEqual(self.Domains.create({'name': '@Company.COM'}).name, 'company.com')
        self.assertEqual(
            self.Domains.create({'name': 'jan@voorbeeld.test'}).name, 'voorbeeld.test')
        with self.assertRaises(ValidationError):
            self.Domains.create({'name': 'not a domain'})
