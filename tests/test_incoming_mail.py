# -*- coding: utf-8 -*-
"""
Unit tests for Microsoft Incoming Mail Processor.

Run with: python -m odoo -d test_db --test-enable --test-tags=pan_mail_pro
"""
from datetime import datetime
from unittest.mock import patch
from odoo.tests import TransactionCase, tagged
from odoo.tools import mute_logger
import unittest

from odoo.addons.pan_mail_pro.models.mail_provider_client import (
    FOLDER_INBOX,
    FOLDER_SENT,
    ThrottledError,
)
from odoo.addons.pan_mail_pro.models.pan_mail_fetcher import INTERNAL_DOMAINS_CTX
from odoo.addons.pan_mail_pro.models.pan_mail_mailbox import SYNC_FAILURE_LIMIT
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
        })
        self.assertTrue(self.processor._is_internal_domain('user@company.com', mailbox))

    def test_a_set_the_caller_read_is_what_decides(self):
        """The fetcher reads the list once per run and hands the set down."""
        self.assertTrue(self.processor._is_internal_domain(
            'user@other.example', None, frozenset({'other.example'})))
        self.assertFalse(self.processor._is_internal_domain(
            'user@company.com', None, frozenset({'other.example'})))

    def test_the_run_reads_the_list_once_and_the_gate_searches_nothing(self):
        """`_process_mailbox` puts the set on the context; the gate answers
        every party from `ctx` without a search of its own."""
        Domain = type(self.env['pan.mail.domain'])
        GraphClient = type(self.env['microsoft.graph.client'])
        mailbox = self.env['pan.mail.mailbox'].create({'email': 'team@company.com'})
        mailbox.last_sync_date = datetime(2026, 5, 12, 9, 0, 0)
        seen = []

        def fake_process_message(self_, mailbox_, message, folder):
            seen.append(self_.env.context.get(INTERNAL_DOMAINS_CTX))
            return True

        messages = [
            {'provider_message_id': f'g{n}', 'message_id': f'<msg-{n}@test>',
             'date': datetime(2026, 5, 12, 10, n, 0)}
            for n in (1, 2, 3)
        ]
        real_get_domains = Domain.get_domains
        reads = []

        def counting_get_domains(domain_self):
            reads.append(1)
            return real_get_domains(domain_self)

        with patch.object(GraphClient, 'fetch_messages',
                          return_value=messages, autospec=True), \
             patch.object(type(self.processor), '_process_message',
                          fake_process_message), \
             patch.object(Domain, 'get_domains', counting_get_domains):
            self.processor._process_mailbox(mailbox)

        self.assertEqual(len(seen), 3)
        self.assertEqual(
            [set(s) for s in seen], [{'company.com', 'internal.org'}] * 3,
            'every message of the run sees the same set')
        # One for the fail-closed gate, one for the set. Not one per message,
        # and not one per party.
        self.assertEqual(len(reads), 2)

        # The gate itself, with the set on ctx: no search at all.
        ctx = {
            'force_import': False,
            'mailbox': mailbox,
            'internal_domains': frozenset({'company.com'}),
            'counterparts': [{'email': 'a@company.com'}, {'email': 'b@company.com'},
                             {'email': 'customer@example.com'}],
        }
        with patch.object(Domain, 'get_domains', autospec=True,
                          side_effect=AssertionError('searched')):
            self.assertIsNone(self.processor._gate_internal_domain(ctx))
        self.assertEqual(ctx['contact_email'], 'customer@example.com')

    def test_a_hand_built_ctx_without_the_set_still_reads_the_list(self):
        """A forced import calls `_process_message` outside a run."""
        ctx = {
            'force_import': False,
            'mailbox': None,
            'counterparts': [{'email': 'colleague@company.com'}],
        }
        skip = self.processor._gate_internal_domain(ctx)
        self.assertEqual(skip.reason, 'internal_domain')


@tagged('pan_mail_pro', 'post_install', '-at_install')
class TestDuplicateDetection(TransactionCase):
    """The duplicate gate, and the lookup it is built on.

    `_duplicate_of(ctx)` is the one lookup, cached on `ctx`; `_gate_duplicate`
    is the refusal. Both are driven here with the smallest `ctx` the ladder
    hands them, so a change to either reads as one in the diff.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.processor = cls.env['pan.mail.fetcher']
        cls.partner = cls.env['res.partner'].create({
            'name': 'Test Partner',
            'email': 'test@example.com',
        })

    @staticmethod
    def _ctx(internet_message_id):
        return {'internet_message_id': internet_message_id}

    def test_no_duplicate_for_new_message(self):
        """A Message-ID Odoo has never seen resolves to nothing and passes."""
        ctx = self._ctx('<new-message-id@example.com>')
        self.assertFalse(self.processor._duplicate_of(ctx))
        self.assertIsNone(self.processor._gate_duplicate(ctx))

    def test_duplicate_in_mail_message(self):
        """A Message-ID already on a mail.message is that message, and refused."""
        message = self.env['mail.message'].create({
            'message_id': '<existing-message@example.com>',
            'model': 'res.partner',
            'res_id': self.partner.id,
            'body': 'Test',
        })
        ctx = self._ctx('<existing-message@example.com>')
        self.assertEqual(self.processor._duplicate_of(ctx), message)
        skip = self.processor._gate_duplicate(ctx)
        self.assertEqual(skip.reason, 'duplicate')
        # An overlapping fetch window is the system working, not news.
        self.assertTrue(skip.quiet)

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
        ctx = self._ctx('<sent-via-graph@outlook.com>')
        self.assertEqual(self.processor._duplicate_of(ctx), message)
        self.assertEqual(self.processor._gate_duplicate(ctx).reason, 'duplicate')

    def test_empty_message_id(self):
        """A message with no Message-ID is nobody's duplicate."""
        for empty in ('', None):
            ctx = self._ctx(empty)
            self.assertFalse(self.processor._duplicate_of(ctx))
            self.assertIsNone(self.processor._gate_duplicate(ctx))

    def test_the_lookup_is_resolved_once_per_message(self):
        """Two gates ask; the index is read once and the answer kept on ctx."""
        ctx = self._ctx('<asked-twice@example.com>')
        Matcher = type(self.env['pan.mail.matcher'])
        with patch.object(Matcher, '_resolve_message_id', autospec=True,
                          return_value=self.env['mail.message'].browse()) as resolve:
            self.processor._duplicate_of(ctx)
            self.processor._gate_duplicate(ctx)
        self.assertEqual(resolve.call_count, 1)


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
            processed, _, stalled_on, _ = self.processor._fetch_folder(
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

        processed, cursor, stalled_on, _ = self._fetch(messages, {'<msg-2@test>'})

        self.assertEqual(processed, 2)
        # Not 10:02: that would put msg-2 behind the cursor forever.
        self.assertEqual(cursor, datetime(2026, 5, 12, 10, 0, 0))
        self.assertEqual(stalled_on['message_id'], '<msg-2@test>')

    def test_first_message_failing_holds_the_cursor_where_it_was(self):
        messages = [self._msg(1, 0), self._msg(2, 1)]

        processed, cursor, stalled_on, _ = self._fetch(messages, {'<msg-1@test>'})

        self.assertEqual(processed, 1)
        # No progress to report, but reporting None would let the caller
        # decide the folder was empty and jump to now().
        self.assertEqual(cursor, self.mailbox.last_sync_date)
        self.assertEqual(stalled_on['message_id'], '<msg-1@test>')

    def test_clean_batch_still_advances_to_the_last_message(self):
        messages = [self._msg(1, 0), self._msg(2, 1), self._msg(3, 2)]

        processed, cursor, stalled_on, _ = self._fetch(messages, set())

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
class TestThrottleInsideABatch(TransactionCase):
    """A provider asking for a pause mid-batch is not a poison message.

    `_fetch_folder` catches everything a message raises and stalls the cursor
    on it, which is right for a message this mailbox cannot process. A
    `ThrottledError` raised while fetching one message's body is not that: it
    is the provider's bad minute, and treating it as a stall put the mailbox
    straight in `error` -- past the polite throttle branch and past the
    SYNC_FAILURE_LIMIT escalation that every other transient failure gets.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env['pan.mail.domain'].set_domains(['gate-fixture.test'])
        cls.processor = cls.env['pan.mail.fetcher']
        cls.mailbox = cls.env['pan.mail.mailbox'].create({
            'email': 'inbox@company.test',
            'state': 'active',
        })
        cls.mailbox.last_sync_date = datetime(2026, 5, 12, 9, 0, 0)

    def _messages(self):
        return [{
            'provider_message_id': f'g{n}',
            'message_id': f'<msg-{n}@test>',
            'subject': f'Message {n}',
            'date': datetime(2026, 5, 12, 10, n, 0),
        } for n in (1, 2)]

    def _throttled_get_message(self, on='g2'):
        """The provider answers the list, hands over the first body and
        refuses the next with a long wait."""
        GraphClient = type(self.env['microsoft.graph.client'])

        def get_message(client_self, account, mailbox, provider_message_id):
            if provider_message_id == on:
                raise ThrottledError('Retry after 120s', 120)
            return {'provider_message_id': provider_message_id,
                    'message_id': f'<msg-{provider_message_id[1:]}@test>',
                    'subject': 'Message', 'body_html': '<p>hi</p>',
                    'from_email': 'someone@elsewhere.test', 'to': [],
                    'date': datetime(2026, 5, 12, 10, int(provider_message_id[1:]), 0)}

        return patch.object(GraphClient, 'get_message', get_message)

    def test_a_throttle_stops_the_batch_and_keeps_what_landed(self):
        """Not into `stalled_on`, and not raised either: the cursor sits on
        the last message that landed, and the throttle comes back as a
        value so the caller keeps that mail."""
        GraphClient = type(self.env['microsoft.graph.client'])
        with patch.object(GraphClient, 'fetch_messages',
                          return_value=self._messages(), autospec=True), \
             self._throttled_get_message(on='g2'):
            _processed, cursor, stalled_on, throttled = self.processor._fetch_folder(
                self.mailbox, FOLDER_INBOX)
        self.assertIsNone(stalled_on)
        self.assertIsInstance(throttled, ThrottledError)
        self.assertEqual(cursor, datetime(2026, 5, 12, 10, 1, 0),
                         'the cursor advanced over the message that landed, not over the throttled one')

    def test_a_throttle_before_anything_landed_holds_the_cursor(self):
        GraphClient = type(self.env['microsoft.graph.client'])
        with patch.object(GraphClient, 'fetch_messages',
                          return_value=self._messages(), autospec=True), \
             self._throttled_get_message(on='g1'):
            _processed, cursor, stalled_on, throttled = self.processor._fetch_folder(
                self.mailbox, FOLDER_INBOX)
        self.assertIsNone(stalled_on)
        self.assertIsInstance(throttled, ThrottledError)
        self.assertEqual(cursor, self.mailbox.last_sync_date)

    def test_a_throttle_during_get_message_is_a_pause_not_a_failure(self):
        """Mirrors the mailbox-level handler: the wait on the mailbox, the
        state untouched, no failure counted, one warning in the ledger."""
        before = self.mailbox.last_sync_date
        GraphClient = type(self.env['microsoft.graph.client'])
        with patch.object(GraphClient, 'fetch_messages',
                          return_value=self._messages(), autospec=True), \
             self._throttled_get_message(), \
             patch.object(type(self.mailbox), '_has_working_credentials',
                          return_value=True, autospec=True), \
             patch.object(type(self.env['pan.mail.setup']), 'is_ready',
                          return_value=True, autospec=True):
            self.processor._cron_fetch_incoming_mail()

        self.assertEqual(self.mailbox.state, 'active')
        self.assertFalse(self.mailbox.sync_failure_count)
        self.assertIn('Retry after 120s', self.mailbox.error_message)
        self.assertNotIn('Sync stopped', self.mailbox.error_message,
                         'a throttle is not a stall')
        self.assertEqual(self.mailbox.last_sync_date, datetime(2026, 5, 12, 10, 1, 0),
                         'what landed before the throttle is kept and the cursor sits on it')
        self.assertGreater(self.mailbox.last_sync_date, before)

        rows = self.env['pan.mail.error'].search([
            ('mailbox_id', '=', self.mailbox.id)])
        self.assertEqual(rows.mapped('code'), ['incoming.throttled'])
        self.assertEqual(rows.level, 'warning')


@tagged('pan_mail_pro', 'post_install', '-at_install')
class TestTransientSyncFailures(TransactionCase):
    """Issue #82: one bad minute at the provider is not a broken mailbox.

    `error` is the state the cron used to filter *out*, and the first failure
    of any kind wrote it. So a Graph 503 -- Microsoft asking to be called back
    later -- took the mailbox out of every future run, with no retry, no
    backoff and no way back but a person pressing a button. Two of our own
    shared mailboxes sat out of sync for four months on exactly that.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env['pan.mail.domain'].set_domains(['gate-fixture.test'])
        cls.processor = cls.env['pan.mail.fetcher']
        cls.mailbox = cls.env['pan.mail.mailbox'].create({
            'email': 'inbox@company.test',
            'state': 'active',
        })

    def _run(self, failing=True):
        Mailbox = type(self.mailbox)
        IncomingProcessor = type(self.processor)

        def fake_process_mailbox(self_, mailbox):
            if failing:
                raise ValueError('503 Service Unavailable')
            return None

        with patch.object(IncomingProcessor, '_process_mailbox',
                          fake_process_mailbox), \
             patch.object(Mailbox, '_has_working_credentials',
                          return_value=True, autospec=True), \
             patch.object(type(self.env['pan.mail.setup']), 'is_ready',
                          return_value=True, autospec=True), \
             patch.object(type(self.env['pan.mail.license']), 'sync_allowed',
                          return_value=True, autospec=True):
            self.processor._cron_fetch_incoming_mail()

    def test_one_failure_records_the_reason_and_keeps_syncing(self):
        with mute_logger('odoo.addons.pan_mail_pro.models.pan_mail_fetcher'):
            self._run()

        self.assertEqual(self.mailbox.state, 'active')
        self.assertEqual(self.mailbox.sync_failure_count, 1)
        self.assertIn('503', self.mailbox.error_message)

    def test_it_gives_up_once_the_failures_stop_looking_temporary(self):
        with mute_logger('odoo.addons.pan_mail_pro.models.pan_mail_fetcher'):
            for _ in range(SYNC_FAILURE_LIMIT):
                self._run()

        self.assertEqual(self.mailbox.state, 'error')
        self.assertEqual(self.mailbox.sync_failure_count, SYNC_FAILURE_LIMIT)

    def test_a_mailbox_in_error_is_still_read(self):
        """The half that made it permanent. A mailbox that can still be read
        is read, whatever the last run said; what keeps a genuinely broken one
        from being retried every minute is the credentials filter."""
        self.mailbox.write({'state': 'error', 'error_message': 'old news'})

        self._run(failing=False)

        self.assertEqual(self.mailbox.state, 'active')
        self.assertFalse(self.mailbox.error_message)
        self.assertEqual(self.mailbox.sync_failure_count, 0)

    def test_a_run_that_succeeds_clears_the_count(self):
        with mute_logger('odoo.addons.pan_mail_pro.models.pan_mail_fetcher'):
            self._run()
        self.assertEqual(self.mailbox.sync_failure_count, 1)

        self._run(failing=False)

        self.assertEqual(self.mailbox.sync_failure_count, 0)


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
            'sync_level': 'both',
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

    def test_climbing_back_onto_sent_reading_resumes_from_the_inbox_cursor(self):
        """Otherwise the climb imports months of old sent mail."""
        self.mailbox.write({
            'sync_level': 'replies',
            'last_sent_sync_date': datetime(2026, 1, 1, 0, 0, 0),
        })

        self.mailbox.write({'sync_level': 'both'})

        self.assertFalse(self.mailbox.last_sent_sync_date)

    def test_moving_between_the_upper_rungs_keeps_the_sent_cursor(self):
        """A mailbox already reading Sent is not restarting; its cursor stays."""
        self.mailbox.write({
            'sync_level': 'both',
            'last_sent_sync_date': datetime(2026, 1, 1, 0, 0, 0),
        })

        self.mailbox.write({'sync_level': 'contacts'})

        self.assertEqual(self.mailbox.last_sent_sync_date, datetime(2026, 1, 1))
