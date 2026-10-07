# -*- coding: utf-8 -*-
"""What the screens say once the access table has spoken.

`test_mailbox_access.py` is the evidence: what each provider answers and how
the table records it. This file is the reading of it: the health badge, the
one sentence under it, step 4 of the setup checklist and the alert on the
mailboxes line. Every case here is a database shape, written straight into
the table, and nothing contacts a provider.

The first case is Emovr's, because that is the shape the module read as
healthy for six months: a user mailbox owned in Odoo by somebody connected as
another address, whose every send Exchange refused.
"""
from odoo.tests import tagged

from .common import MailProTestCase


@tagged('pan_mail_pro', 'post_install', '-at_install')
class TestMailboxHealth(MailProTestCase):

    def setUp(self):
        super().setUp()
        self.Access = self.env['pan.mail.mailbox.access']
        self.Setup = self.env['pan.mail.setup']
        # The fixture has no application registration, and without one step 2
        # is open and the phase is `setup` whatever the mailboxes say. The
        # two assertions on the phase below need it answered.
        self.env['pan.mail.provider'].create({
            'provider': 'outlook', 'client_id': 'id', 'client_secret': 'secret',
            'tenant_id': '11111111-2222-3333-4444-555555555555'})
        self.owner_account = self.notif_owner.x_pan_mail_account_ids
        self.sales_account = self.salesperson.x_pan_mail_account_ids

    def _refresh(self, mailbox):
        mailbox.invalidate_recordset(['health_status', 'status_message', 'access_ids'])
        return mailbox

    # ---------------------------------------------------------------- Emovr

    def test_the_emovr_shape_is_a_warning_naming_both_ways_out(self):
        """info@ is a user account owned by Robert, who is connected as
        robert@stalero: shared in Odoo's terms, a person's mailbox in
        Exchange's. Every send borrows the sender's token."""
        mailbox = self.env['pan.mail.mailbox'].create({
            'email': 'info@emovr.test', 'owner_user_id': self.salesperson.id})
        self.assertEqual(mailbox.mailbox_type, 'shared')
        mailbox.sudo().write({'address_kind': 'user'})
        self.Access.note_check(mailbox, self.sales_account,
                               {'kind': 'user', 'can_read': 'yes', 'can_send': 'unknown',
                                'error': None})
        self._refresh(mailbox)
        self.assertEqual(mailbox.health_status, 'warning')
        self.assertEqual(
            mailbox.status_message,
            'info@emovr.test is a user account. Connect it as its own sign-in, or '
            'grant sales@company.test Full Access and Send As on it in the Exchange '
            'admin center.')

    def test_a_refused_sender_on_a_shared_mailbox_is_named(self):
        mailbox = self.shared_mailbox
        self.Access.note_send(mailbox, self.sales_account, False,
                              error='sales@company.test cannot send from info@company.test. '
                                    'An administrator grants Full Access and Send As on that '
                                    'address in the Exchange admin center.')
        self._refresh(mailbox)
        self.assertEqual(mailbox.health_status, 'warning')
        self.assertIn('sales@company.test cannot send from', mailbox.status_message)

    def test_a_delivered_send_clears_the_refusal(self):
        mailbox = self.shared_mailbox
        self.Access.note_send(mailbox, self.sales_account, False, error='refused')
        self.Access.note_send(mailbox, self.sales_account, True)
        self._refresh(mailbox)
        self.assertEqual(mailbox.health_status, 'healthy')
        self.assertFalse(mailbox.status_message)

    # --------------------------------------------------------------- errors

    def test_no_mailbox_at_the_address_is_an_error(self):
        mailbox = self.shared_mailbox
        mailbox.sudo().write({'address_kind': 'none'})
        self._refresh(mailbox)
        self.assertEqual(mailbox.health_status, 'error')
        self.assertIn('no mailbox at info@company.test', mailbox.status_message)

    def test_an_alias_is_an_error_with_the_providers_sentence(self):
        mailbox = self.personal_mailbox
        mailbox.sudo().write({'address_kind': 'alias'})
        self.Access.note_check(mailbox, self.sales_account,
                               {'kind': 'alias', 'can_read': 'yes', 'can_send': 'unknown',
                                'error': 'sales@company.test is an alias on another mailbox.'})
        self._refresh(mailbox)
        self.assertEqual(mailbox.health_status, 'error')
        self.assertEqual(mailbox.status_message,
                         'sales@company.test is an alias on another mailbox.')

    def test_an_owner_who_cannot_read_their_mailbox_is_an_error(self):
        mailbox = self.personal_mailbox
        self.Access.note_check(mailbox, self.sales_account,
                               {'kind': 'unknown', 'can_read': 'no', 'can_send': 'unknown',
                                'error': 'sales@company.test cannot read sales@company.test.'})
        self._refresh(mailbox)
        self.assertEqual(mailbox.health_status, 'error')
        self.assertIn('cannot read', mailbox.status_message)

    def test_a_notification_mailbox_its_owner_may_not_send_from_is_an_error(self):
        mailbox = self.notification_mailbox
        self.Access.note_send(mailbox, self.owner_account, False,
                              error='notif_owner@test.local cannot send from '
                                    'notifications@company.test. An administrator grants '
                                    'Full Access and Send As on that address in the '
                                    'Exchange admin center.')
        self._refresh(mailbox)
        self.assertEqual(mailbox.health_status, 'error')
        self.assertIn('Full Access and Send As', mailbox.status_message)

    def test_a_reconnect_to_finish_the_check_is_a_warning(self):
        mailbox = self.personal_mailbox
        self.Access.note_check(mailbox, self.sales_account,
                               {'kind': 'unknown', 'can_read': 'yes', 'can_send': 'unknown',
                                'error': 'Reconnect Sales Person under My Preferences, Mail '
                                         'Pro, so Mail Pro can check what sales@company.test is.'})
        self._refresh(mailbox)
        self.assertEqual(mailbox.health_status, 'warning')
        self.assertIn('Reconnect', mailbox.status_message)

    def test_a_mailbox_nobody_has_checked_reads_as_it_did(self):
        """`unknown` everywhere is the state every row starts in at the
        upgrade, and it must not turn a working mailbox any colour."""
        for mailbox in (self.personal_mailbox, self.shared_mailbox, self.notification_mailbox):
            with self.subTest(mailbox=mailbox.email):
                self.assertEqual(mailbox.address_kind, 'unknown')
                self.assertIsNone(mailbox._access_problem())

    # --------------------------------------------------------------- step 4

    def test_step_4_is_open_until_the_owner_has_been_seen_to_send(self):
        state, sentence = self.Setup.notification_mailbox_verified()
        self.assertEqual(state, 'open')
        self.assertIn('Not checked yet', sentence)
        self.Access.note_check(self.notification_mailbox, self.owner_account,
                               {'kind': 'user', 'can_read': 'yes', 'can_send': 'unknown',
                                'error': None})
        state, sentence = self.Setup.notification_mailbox_verified()
        self.assertEqual(state, 'open')
        self.assertIn('Not yet sent from', sentence)
        self.Access.note_send(self.notification_mailbox, self.owner_account, True)
        self.assertEqual(self.Setup.notification_mailbox_verified(), ('ok', ''))

    def test_step_4_is_broken_when_the_provider_refused(self):
        self.Access.note_send(self.notification_mailbox, self.owner_account, False,
                              error='refused by Exchange')
        state, sentence = self.Setup.notification_mailbox_verified()
        self.assertEqual(state, 'broken')
        self.assertEqual(sentence, 'refused by Exchange')

    def test_the_phase_does_not_wait_for_the_verified_answer(self):
        """A phase that waited for a probe and a send would stop every
        existing database's sync at the upgrade. The dot waits; the phase
        keeps reading the credentials."""
        self.assertTrue(self.Setup.answers()['mailboxes'])
        self.assertEqual(self.Setup.notification_mailbox_verified()[0], 'open')
        self.assertTrue(self.Setup.is_ready())

    def test_the_settings_page_reads_the_three_states(self):
        settings = self.env['res.config.settings'].create({})
        self.assertEqual(settings.x_notification_access_state, 'open')
        self.Access.note_send(self.notification_mailbox, self.owner_account, False, error='no')
        settings = self.env['res.config.settings'].create({})
        self.assertEqual(settings.x_notification_access_state, 'broken')
        self.assertEqual(settings.x_notification_access_msg, 'no')

    # ---------------------------------------------------------------- alert

    def test_a_refused_mailbox_is_an_alert_on_the_mailboxes_line(self):
        self.assertEqual(self.Setup.mailbox_alert(), '')
        self.shared_mailbox.sudo().write({'address_kind': 'none'})
        self.assertIn('1 mailbox(es) the provider refuses', self.Setup.mailbox_alert())
        self.assertTrue(self.Setup.is_ready(), 'an alert, never a phase')
