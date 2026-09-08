# -*- coding: utf-8 -*-
"""
Unit tests for Microsoft Incoming Mail Processor.

Run with: python -m odoo -d test_db --test-enable --test-tags=pan_mail_pro
"""
from datetime import datetime
from unittest.mock import patch
from odoo.tests import TransactionCase, tagged
import unittest

from odoo.addons.pan_mail_pro.models.mail_provider_client import FOLDER_INBOX, FOLDER_SENT
from odoo.addons.pan_mail_pro.tests.common import MailProTestCase



@tagged('pan_mail_pro', 'post_install', '-at_install')
class TestInternalDomain(TransactionCase):
    """Test internal domain filtering against the configured domain list."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.processor = cls.env['pan.mail.fetcher']
        cls.env['pan.mail.domain'].set_domains(['company.com', 'internal.org'])

    def test_internal_domain_match(self):
        """Emails from configured domains should be detected as internal."""
        self.assertTrue(self.processor._is_internal_domain('user@company.com'))
        self.assertTrue(self.processor._is_internal_domain('user@internal.org'))

    def test_internal_domain_case_insensitive(self):
        """Domain matching should be case insensitive."""
        self.assertTrue(self.processor._is_internal_domain('user@COMPANY.COM'))
        self.assertTrue(self.processor._is_internal_domain('user@Company.Com'))

    def test_external_domain(self):
        """Emails from external domains should not be detected as internal."""
        self.assertFalse(self.processor._is_internal_domain('user@gmail.com'))
        self.assertFalse(self.processor._is_internal_domain('user@external.com'))

    def test_invalid_email(self):
        """Invalid emails should return False."""
        self.assertFalse(self.processor._is_internal_domain(''))
        self.assertFalse(self.processor._is_internal_domain('invalid'))
        self.assertFalse(self.processor._is_internal_domain(None))

    def test_no_matching_domain(self):
        """When the email domain isn't in the list, it's not internal."""
        self.assertFalse(self.processor._is_internal_domain('user@external.net'))

    def test_no_mailbox_can_opt_out_of_the_internal_filter(self):
        """There is no per-mailbox escape hatch left. Every mailbox filters."""
        mailbox = self.env['pan.mail.mailbox'].create({
            'email': 'team@company.com',
            'mailbox_type': 'shared',
        })
        self.assertTrue(self.processor._is_internal_domain('user@company.com', mailbox))


@tagged('pan_mail_pro', 'post_install', '-at_install')
class TestDuplicateDetection(TransactionCase):
    """Test duplicate message detection."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.processor = cls.env['pan.mail.fetcher']
        cls.partner = cls.env['res.partner'].create({
            'name': 'Test Partner',
            'email': 'test@example.com',
        })

    def test_no_duplicate_for_new_message(self):
        """New message IDs should not be detected as duplicates."""
        self.assertFalse(self.processor._is_duplicate('<new-message-id@example.com>'))

    def test_duplicate_in_mail_message(self):
        """Messages already in mail.message should be detected."""
        # Create a mail.message with this message_id
        self.env['mail.message'].create({
            'message_id': '<existing-message@example.com>',
            'model': 'res.partner',
            'res_id': self.partner.id,
            'body': 'Test',
        })
        self.assertTrue(self.processor._is_duplicate('<existing-message@example.com>'))

    def test_duplicate_sent_from_odoo(self):
        """Mail sent from Odoo is known under the Message-ID the provider minted.

        Graph assigns its own internetMessageId on send, so the id that comes
        back through Sent Items is not `mail.message.message_id`. The send path
        records it in the ref index; the sync must find it there.
        """
        message = self.env['mail.message'].create({
            'model': 'res.partner',
            'res_id': self.partner.id,
            'body': 'Sent from Odoo',
            'message_id': '<odoo-generated@example.com>',
        })
        self.env['pan.mail.message.ref'].record(
            message, '<sent-via-graph@outlook.com>', source='provider')
        self.assertTrue(self.processor._is_duplicate('<sent-via-graph@outlook.com>'))

    def test_empty_message_id(self):
        """Empty message IDs should not be duplicates."""
        self.assertFalse(self.processor._is_duplicate(''))
        self.assertFalse(self.processor._is_duplicate(None))


@tagged('pan_mail_pro', 'post_install', '-at_install')
class TestPartnerMatching(TransactionCase):
    """Test partner finding and creation."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.processor = cls.env['pan.mail.fetcher']
        cls.existing_partner = cls.env['res.partner'].create({
            'name': 'Existing Partner',
            'email': 'existing@example.com',
        })

    def test_find_existing_partner(self):
        """Should find existing partner by email."""
        partner = self.processor._find_partner('existing@example.com')
        self.assertEqual(partner, self.existing_partner)

    def test_find_partner_case_insensitive(self):
        """Partner lookup should be case insensitive."""
        partner = self.processor._find_partner('EXISTING@EXAMPLE.COM')
        self.assertEqual(partner, self.existing_partner)

    def test_find_partner_not_found(self):
        """Should return False for unknown emails."""
        partner = self.processor._find_partner('unknown@example.com')
        self.assertFalse(partner)

    def test_find_or_create_existing(self):
        """Should find existing partner without creating new one."""
        partner = self.processor._find_or_create_partner('existing@example.com', 'Some Name')
        self.assertEqual(partner, self.existing_partner)
        # Name should not be updated
        self.assertEqual(partner.name, 'Existing Partner')

    def test_find_or_create_new(self):
        """Should create new partner for unknown email."""
        partner = self.processor._find_or_create_partner('new@example.com', 'New Person')
        self.assertTrue(partner)
        self.assertEqual(partner.name, 'New Person')
        self.assertEqual(partner.email, 'new@example.com')

    def test_find_or_create_no_name(self):
        """Should use email local part as name if not provided."""
        partner = self.processor._find_or_create_partner('noname@example.com')
        self.assertEqual(partner.name, 'noname')


@tagged('pan_mail_pro', 'post_install', '-at_install')
class TestAliasRouting(TransactionCase):
    """Routing an incoming mail through a mailbox that has no alias.

    Kept apart from the Helpdesk class on purpose. This path needs no
    Enterprise module, but it used to share a setUpClass that created a
    helpdesk.team -- so it skipped itself on every CI run, losing coverage to
    a dependency it never had.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        # Mail Pro refuses to create a mailbox while the internal domain
        # list is empty. A domain nothing in this fixture uses, so the gate
        # opens without turning any fixture address internal.
        cls.env['pan.mail.domain'].set_domains(['gate-fixture.test'])
        cls.processor = cls.env['pan.mail.fetcher']
        cls.partner = cls.env['res.partner'].create({
            'name': 'Test Sender',
            'email': 'sender@example.com',
        })

    def test_route_no_alias_falls_back_to_partner(self):
        """Without alias, email should be posted to partner chatter."""
        mailbox_no_alias = self.env['pan.mail.mailbox'].create({
            'email': 'noalias@company.com',
            'mailbox_type': 'shared',
        })

        msg_dict = {
            'message_type': 'email',
            'subject': 'Test',
            'body': '<p>Test</p>',
            'attachments': [],
            'message_id': '<test-456@example.com>',
            'author_id': self.partner.id,
            'email_from': 'sender@example.com',
        }

        record, message = self.processor._route_email_via_alias(
            mailbox=mailbox_no_alias,
            partner=self.partner,
            msg_dict=msg_dict,
            contact_email='sender@example.com',
        )

        self.assertEqual(record, self.partner)
        self.assertTrue(message)


@tagged('pan_mail_pro', 'post_install', '-at_install')
class TestHelpdeskRouting(TransactionCase):
    """Routing an incoming mail onto a helpdesk ticket via the team alias.

    `helpdesk` ships only in Odoo Enterprise, so this class skips itself on the
    community image CI uses. It is the module's one genuine CI gap; run it
    locally against the Enterprise source, and see TESTPLAN.md.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        # Mail Pro refuses to create a mailbox while the internal domain
        # list is empty. A domain nothing in this fixture uses, so the gate
        # opens without turning any fixture address internal.
        cls.env['pan.mail.domain'].set_domains(['gate-fixture.test'])
        if 'helpdesk.team' not in cls.env:
            raise unittest.SkipTest("Helpdesk module not installed")
        cls.processor = cls.env['pan.mail.fetcher']
        cls.partner = cls.env['res.partner'].create({
            'name': 'Test Sender',
            'email': 'sender@example.com',
        })

        # Create a helpdesk team and mailbox with alias + route_to_team enabled
        cls.helpdesk_team = cls.env['helpdesk.team'].create({
            'name': 'Test Support',
        })
        cls.mailbox = cls.env['pan.mail.mailbox'].create({
            'email': 'support@company.com',
            'mailbox_type': 'shared',
            'route_to_team': True,  # Enable team routing
            'alias_id': cls.helpdesk_team.alias_id.id,
        })

    def test_route_to_helpdesk(self):
        """Email should be routed to helpdesk ticket via alias."""
        msg_dict = {
            'message_type': 'email',
            'subject': 'Help needed',
            'from': '"Test Sender" <sender@example.com>',
            'to': 'support@company.com',
            'body': '<p>I need help</p>',
            'attachments': [],
            'message_id': '<test-123@example.com>',
            'author_id': self.partner.id,
            'email_from': '"Test Sender" <sender@example.com>',
        }

        record, message = self.processor._route_email_via_alias(
            mailbox=self.mailbox,
            partner=self.partner,
            msg_dict=msg_dict,
            contact_email='sender@example.com',
        )

        self.assertEqual(record._name, 'helpdesk.ticket')
        self.assertEqual(record.name, 'Help needed')
        self.assertEqual(record.partner_id, self.partner)
        self.assertTrue(message)


@tagged('pan_mail_pro', 'post_install', '-at_install')
class TestSavepointIsolation(TransactionCase):
    """Regression: one failing message must not poison the rest of the batch.

    Without per-message savepoints, a psycopg-level error inside
    `_process_message` leaves the surrounding transaction in `aborted`
    state and every later message in the same cron run fails with
    "cursor already closed". Observed in production on 2026-05-11.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        # Mail Pro refuses to create a mailbox while the internal domain
        # list is empty. A domain nothing in this fixture uses, so the gate
        # opens without turning any fixture address internal.
        cls.env['pan.mail.domain'].set_domains(['gate-fixture.test'])
        cls.processor = cls.env['pan.mail.fetcher']
        cls.mailbox = cls.env['pan.mail.mailbox'].create({
            'email': 'inbox@company.test',
            'mailbox_type': 'shared',
        })

    def test_one_bad_message_does_not_poison_batch(self):
        Partner = self.env['res.partner']
        IncomingProcessor = type(self.processor)
        GraphClient = type(self.env['microsoft.graph.client'])

        # Normalized messages, as a provider client hands them back.
        fake_messages = [
            {'provider_message_id': 'g1', 'message_id': '<msg-1@test>',
             'date': datetime(2026, 5, 12, 10, 0, 0)},
            {'provider_message_id': 'g2', 'message_id': '<msg-2@test>',
             'date': datetime(2026, 5, 12, 10, 1, 0)},
            {'provider_message_id': 'g3', 'message_id': '<msg-3@test>',
             'date': datetime(2026, 5, 12, 10, 2, 0)},
        ]

        call_log = []

        def fake_process_message(self_, mailbox, message, folder):
            msg_id = message['message_id']
            call_log.append(msg_id)
            # Observable side effect: each call creates its own partner.
            Partner.create({
                'name': f'Sender of {msg_id}',
                'email': f'sender-{len(call_log)}@example.com',
            })
            if msg_id == '<msg-2@test>':
                # A query that aborts the transaction at the PostgreSQL
                # level. Without savepointing, every subsequent query
                # in this cron run would fail with "cursor already closed".
                self_.env.cr.execute("SELECT 1/0")
            return True

        with patch.object(GraphClient, 'fetch_messages',
                          return_value=fake_messages, autospec=True), \
             patch.object(IncomingProcessor, '_process_message',
                          fake_process_message):
            processed, _, stalled_on = self.processor._fetch_folder(
                self.mailbox, FOLDER_INBOX)

        # All three messages were attempted in order — the failure didn't
        # short-circuit the loop.
        self.assertEqual(
            call_log,
            ['<msg-1@test>', '<msg-2@test>', '<msg-3@test>'],
        )
        # Successful messages' partners persisted.
        self.assertTrue(Partner.search([('email', '=', 'sender-1@example.com')]))
        self.assertTrue(Partner.search([('email', '=', 'sender-3@example.com')]))
        # The failing message's partner was rolled back by its savepoint.
        self.assertFalse(Partner.search([('email', '=', 'sender-2@example.com')]))
        # Processed count reflects only the successful messages.
        self.assertEqual(processed, 2)
        # And the cursor stopped at the message that failed, so the next run
        # meets it again instead of stepping over it forever.
        self.assertEqual(stalled_on['message_id'], '<msg-2@test>')


@tagged('pan_mail_pro', 'post_install', '-at_install')
class TestCursorHoldsOnFailure(TransactionCase):
    """Issue #97: a message that raised must not be stepped over.

    The cursor used to advance to the last message of the batch whatever
    happened inside it, so a mail that failed to process was skipped for good
    and nothing said so. Processing dedups on Message-ID, so holding the
    cursor costs a lookup on the retry and loses nothing.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env['pan.mail.domain'].set_domains(['gate-fixture.test'])
        cls.processor = cls.env['pan.mail.fetcher']
        cls.mailbox = cls.env['pan.mail.mailbox'].create({
            'email': 'inbox@company.test',
            'mailbox_type': 'shared',
        })
        cls.mailbox.last_sync_date = datetime(2026, 5, 12, 9, 0, 0)

    def _fetch(self, messages, failing_ids):
        """Run one folder fetch where `failing_ids` raise inside processing."""
        IncomingProcessor = type(self.processor)
        GraphClient = type(self.env['microsoft.graph.client'])

        def fake_process_message(self_, mailbox, message, folder):
            if message['message_id'] in failing_ids:
                raise ValueError('boom')
            return True

        with patch.object(GraphClient, 'fetch_messages',
                          return_value=messages, autospec=True), \
             patch.object(IncomingProcessor, '_process_message',
                          fake_process_message):
            return self.processor._fetch_folder(self.mailbox, FOLDER_INBOX)

    @staticmethod
    def _msg(n, minute):
        return {
            'provider_message_id': f'g{n}',
            'message_id': f'<msg-{n}@test>',
            'subject': f'Message {n}',
            'date': datetime(2026, 5, 12, 10, minute, 0),
        }

    def test_cursor_stops_before_the_failed_message(self):
        messages = [self._msg(1, 0), self._msg(2, 1), self._msg(3, 2)]

        processed, cursor, stalled_on = self._fetch(messages, {'<msg-2@test>'})

        self.assertEqual(processed, 2)
        # Not 10:02: that would put msg-2 behind the cursor forever.
        self.assertEqual(cursor, datetime(2026, 5, 12, 10, 0, 0))
        self.assertEqual(stalled_on['message_id'], '<msg-2@test>')

    def test_first_message_failing_holds_the_cursor_where_it_was(self):
        messages = [self._msg(1, 0), self._msg(2, 1)]

        processed, cursor, stalled_on = self._fetch(messages, {'<msg-1@test>'})

        self.assertEqual(processed, 1)
        # No progress to report, but reporting None would let the caller
        # decide the folder was empty and jump to now().
        self.assertEqual(cursor, self.mailbox.last_sync_date)
        self.assertEqual(stalled_on['message_id'], '<msg-1@test>')

    def test_clean_batch_still_advances_to_the_last_message(self):
        messages = [self._msg(1, 0), self._msg(2, 1), self._msg(3, 2)]

        processed, cursor, stalled_on = self._fetch(messages, set())

        self.assertEqual(processed, 3)
        self.assertEqual(cursor, datetime(2026, 5, 12, 10, 2, 0))
        self.assertIsNone(stalled_on)

    def test_mailbox_cursor_does_not_move_past_a_stall(self):
        """The whole point, seen from `_process_mailbox`."""
        before = self.mailbox.last_sync_date
        messages = [self._msg(1, 0), self._msg(2, 1)]
        IncomingProcessor = type(self.processor)
        GraphClient = type(self.env['microsoft.graph.client'])

        def fake_process_message(self_, mailbox, message, folder):
            raise ValueError('boom')

        with patch.object(GraphClient, 'fetch_messages',
                          return_value=messages, autospec=True), \
             patch.object(IncomingProcessor, '_process_message',
                          fake_process_message):
            stall = self.processor._process_mailbox(self.mailbox)

        self.assertEqual(self.mailbox.last_sync_date, before)
        # And it says so where somebody looks.
        self.assertIn('Message 1', stall)
        self.assertIn('g1', stall)

    def test_a_stall_puts_the_mailbox_in_error(self):
        messages = [self._msg(1, 0)]
        IncomingProcessor = type(self.processor)
        GraphClient = type(self.env['microsoft.graph.client'])

        def fake_process_message(self_, mailbox, message, folder):
            raise ValueError('boom')

        with patch.object(GraphClient, 'fetch_messages',
                          return_value=messages, autospec=True), \
             patch.object(IncomingProcessor, '_process_message',
                          fake_process_message), \
             patch.object(type(self.mailbox), '_has_working_credentials',
                          return_value=True, autospec=True), \
             patch.object(type(self.env['pan.mail.setup']), 'is_ready',
                          return_value=True, autospec=True):
            self.processor._cron_fetch_incoming_mail()

        self.assertEqual(self.mailbox.state, 'error')
        self.assertIn('Message 1', self.mailbox.error_message)


@tagged('pan_mail_pro', 'post_install', '-at_install')
class TestPerFolderCursor(MailProTestCase):
    """Issue #116: one folder must not hold the other one back.

    The cursor was the minimum of both folders' progress, so a mailbox that
    receives mail but sends none through that account never advanced past its
    last sent item: every run re-fetched everything since then and discarded it
    at the duplicate gate, and once more than one batch sat in that gap the
    newest mail was never reached at all.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.processor = cls.env['pan.mail.fetcher']
        cls.mailbox = cls.personal_mailbox
        cls.mailbox.write({
            'sync_sent': True,
            'last_sync_date': datetime(2026, 5, 12, 9, 0, 0),
        })

    @staticmethod
    def _msg(n, when):
        return {
            'provider_message_id': f'g{n}',
            'message_id': f'<msg-{n}@test>',
            'subject': f'Message {n}',
            'date': when,
        }

    def _sync(self, per_folder, failing_ids=frozenset()):
        """Run one mailbox sync where each folder returns its own messages."""
        IncomingProcessor = type(self.processor)
        GraphClient = type(self.env['microsoft.graph.client'])

        def fake_fetch(self_, account, mailbox, folder, **kwargs):
            self.since[folder] = kwargs.get('since_datetime')
            return per_folder.get(folder, [])

        def fake_process_message(self_, mailbox, message, folder):
            if message['message_id'] in failing_ids:
                raise ValueError('boom')
            return True

        self.since = {}
        with patch.object(GraphClient, 'fetch_messages', fake_fetch), \
             patch.object(IncomingProcessor, '_process_message',
                          fake_process_message):
            return self.processor._process_mailbox(self.mailbox)

    def test_a_quiet_sent_folder_does_not_pin_the_inbox(self):
        """The bug, exactly as reported."""
        inbox_last = datetime(2026, 9, 8, 8, 44, 6)
        self._sync({
            FOLDER_INBOX: [self._msg(1, inbox_last)],
            FOLDER_SENT: [self._msg(2, datetime(2026, 7, 10, 23, 11, 40))],
        })

        self.assertEqual(self.mailbox.last_sync_date, inbox_last)
        self.assertEqual(self.mailbox.last_sent_sync_date,
                         datetime(2026, 7, 10, 23, 11, 40))

    def test_each_folder_is_fetched_from_its_own_cursor(self):
        self.mailbox.write({
            'last_sync_date': datetime(2026, 9, 8, 8, 0, 0),
            'last_sent_sync_date': datetime(2026, 7, 10, 23, 11, 40),
        })

        self._sync({})

        self.assertEqual(self.since[FOLDER_INBOX], datetime(2026, 9, 8, 8, 0, 0))
        self.assertEqual(self.since[FOLDER_SENT],
                         datetime(2026, 7, 10, 23, 11, 40))

    def test_a_folder_without_its_own_cursor_resumes_from_the_shared_one(self):
        """Upgrade path: the Sent cursor is empty on every existing mailbox."""
        self.mailbox.write({'last_sent_sync_date': False})
        shared = self.mailbox.last_sync_date

        self._sync({})

        self.assertEqual(self.since[FOLDER_SENT], shared)

    def test_a_stall_in_one_folder_leaves_the_other_free(self):
        before = self.mailbox.last_sync_date
        stall = self._sync(
            {
                FOLDER_INBOX: [self._msg(1, datetime(2026, 5, 12, 10, 0, 0))],
                FOLDER_SENT: [self._msg(2, datetime(2026, 5, 12, 10, 5, 0))],
            },
            failing_ids={'<msg-1@test>'},
        )

        self.assertIn('Message 1', stall)
        self.assertEqual(self.mailbox.last_sync_date, before)
        self.assertEqual(self.mailbox.last_sent_sync_date,
                         datetime(2026, 5, 12, 10, 5, 0))

    def test_an_empty_folder_catches_up_on_its_own(self):
        self.mailbox.write({
            'last_sent_sync_date': datetime(2026, 7, 10, 23, 11, 40)})

        self._sync({FOLDER_INBOX: [self._msg(1, datetime(2026, 5, 12, 10, 0))]})

        # Nothing in Sent means Sent is caught up, whatever the inbox did.
        self.assertGreater(self.mailbox.last_sent_sync_date,
                           datetime(2026, 9, 1))
        self.assertEqual(self.mailbox.last_sync_date,
                         datetime(2026, 5, 12, 10, 0))

    def test_rewinding_the_start_date_rewinds_both_cursors(self):
        self.mailbox.write({
            'last_sync_date': datetime(2026, 9, 8, 8, 0, 0),
            'last_sent_sync_date': datetime(2026, 9, 8, 9, 0, 0),
        })

        self.mailbox.write({'sync_start_date': datetime(2026, 1, 1, 0, 0, 0)})

        self.assertEqual(self.mailbox.last_sync_date, datetime(2026, 1, 1))
        self.assertEqual(self.mailbox.last_sent_sync_date, datetime(2026, 1, 1))

    def test_turning_sent_sync_back_on_resumes_from_the_inbox_cursor(self):
        """Otherwise the switch imports months of old sent mail."""
        self.mailbox.write({
            'sync_sent': False,
            'last_sent_sync_date': datetime(2026, 1, 1, 0, 0, 0),
        })

        self.mailbox.write({'sync_sent': True})

        self.assertFalse(self.mailbox.last_sent_sync_date)
