# -*- coding: utf-8 -*-
"""
The reply to our own mail, which is the case threading exists for.

Reproduced from production (issue #94): a mail Mail Pro had just sent was
answered with a correct `In-Reply-To`, and neither the References rule nor the
thread-link rule proposed anything. The mail reached the right record on the
weakest rung of the ladder — same subject, same participant, confidence 0.50 —
and was flagged for review.

Two independent causes, and the tests here name them separately because either
one alone is enough to produce that outcome:

1. Microsoft reports the *draft's* `conversationId` on send, and the reply can
   arrive in the same mailbox under a different one. A thread keyed only on the
   provider's handle goes quiet at exactly that point.
2. Graph does not always hand back `internetMessageHeaders`. Without it the
   matcher has no References chain to walk at all, and cannot tell that from a
   mail that genuinely has no ancestors.

Both are answered the same way: never depend on one handle. A conversation is
keyed under the provider's id *and* the root of its References chain, the
threading headers are read from the MAPI properties when the header collection
is missing, and the sent copy re-indexes whatever the draft got wrong.
"""
from unittest.mock import patch

from odoo.tests import TransactionCase, tagged

from odoo.addons.pan_mail_pro.models.pan_mail_matcher import (
    RULE_REFERENCES,
    RULE_THREAD_LINK,
    RULE_SUBJECT_PARTICIPANTS,
)


@tagged('pan_mail_pro', 'post_install', '-at_install')
class TestThreadKeys(TransactionCase):
    """A conversation is keyed under every handle it carries, not just one."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env['pan.mail.domain'].set_domains(['gate-fixture.test'])
        cls.matcher = cls.env['pan.mail.matcher']
        cls.mailbox = cls.env['pan.mail.mailbox'].create({
            'email': 'support@company.test',
            'mailbox_type': 'shared',
        })
        cls.lead = cls.env['crm.lead'].create({'name': 'Existing opportunity'})

    def test_provider_handle_leads_and_the_references_root_follows(self):
        keys = self.matcher.thread_keys({
            'thread_id': 'CONV-1',
            'message_id': '<reply@example.com>',
            'headers': {
                'References': '<root@example.com> <middle@example.com>',
                'In-Reply-To': '<middle@example.com>',
            },
        })

        self.assertEqual(keys, ['CONV-1', '<root@example.com>'])

    def test_a_mail_that_starts_a_thread_is_its_own_root(self):
        """Otherwise the first reply has nothing keyed to match against."""
        keys = self.matcher.thread_keys({
            'thread_id': False,
            'message_id': '<first@example.com>',
            'headers': {},
        })

        self.assertEqual(keys, ['<first@example.com>'])

    def test_imap_yields_one_key_not_two(self):
        """The References root *is* the handle there; two rows would be waste."""
        keys = self.matcher.thread_keys({
            'thread_id': '<root@example.com>',
            'message_id': '<reply@example.com>',
            'headers': {'References': '<root@example.com>'},
        })

        self.assertEqual(keys, ['<root@example.com>'])

    def test_a_drifted_conversation_id_still_matches_on_the_rfc_root(self):
        """The production failure, at the rung that was silent.

        Sent under CONV-OUT, the reply arrives under CONV-IN. The provider's
        handle matches nothing; the References root is the same for both ends
        of the conversation and does.
        """
        message = self.lead.with_context(
            mail_create_nosubscribe=True, mail_notrack=True,
        ).message_post(body='<p>hi</p>', message_type='email',
                       subtype_xmlid='mail.mt_comment')
        self.env['pan.mail.thread.link'].record_all(
            mailbox=self.mailbox,
            thread_ids=['CONV-OUT', '<root@company.test>'],
            model='crm.lead', res_id=self.lead.id, message=message,
        )

        decision = self.matcher.match({
            'message_id': '<reply@example.com>',
            'thread_id': 'CONV-IN',
            'subject': 'Re: Question',
            'headers': {'References': '<root@company.test>'},
        }, mailbox=self.mailbox)

        self.assertEqual(decision['model'], 'crm.lead')
        self.assertEqual(decision['res_id'], self.lead.id)
        self.assertEqual(decision['rule'], RULE_THREAD_LINK)

    def test_record_all_tags_which_key_is_the_provider_s(self):
        self.env['pan.mail.thread.link'].record_all(
            mailbox=self.mailbox,
            thread_ids=['CONV-OUT', '<root@company.test>'],
            model='crm.lead', res_id=self.lead.id,
        )

        links = self.env['pan.mail.thread.link'].search([
            ('mailbox_id', '=', self.mailbox.id),
        ])
        self.assertEqual(len(links), 2)
        self.assertEqual(
            {link.thread_id: link.key_type for link in links},
            {'CONV-OUT': 'provider', '<root@company.test>': 'rfc'},
        )

    def test_sending_gets_the_provider_s_handle_back_never_the_rfc_root(self):
        """Gmail rejects a threadId it did not mint, so the key type decides.

        Both rows point at the same record and `find_for_record` takes one; the
        RFC root is a matching key, and handing it to a provider as a thread id
        would fail the send.
        """
        self.env['pan.mail.thread.link'].record_all(
            mailbox=self.mailbox,
            thread_ids=['CONV-OUT', '<root@company.test>'],
            model='crm.lead', res_id=self.lead.id,
        )

        link = self.env['pan.mail.thread.link'].find_for_record(
            self.mailbox, 'crm.lead', self.lead.id)

        self.assertEqual(link.thread_id, 'CONV-OUT')

    def test_a_thread_with_only_an_rfc_key_is_still_found(self):
        """IMAP, and any conversation whose provider handle never arrived."""
        self.env['pan.mail.thread.link'].record_all(
            mailbox=self.mailbox,
            thread_ids=['<root@company.test>'],
            model='crm.lead', res_id=self.lead.id,
        )

        link = self.env['pan.mail.thread.link'].find_for_record(
            self.mailbox, 'crm.lead', self.lead.id)

        self.assertEqual(link.thread_id, '<root@company.test>')


@tagged('pan_mail_pro', 'post_install', '-at_install')
class TestRoutingLogEvidence(TransactionCase):
    """A fallback must say what the matcher had, not just that it failed."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env['pan.mail.domain'].set_domains(['gate-fixture.test'])
        cls.mailbox = cls.env['pan.mail.mailbox'].create({
            'email': 'support@company.test',
            'mailbox_type': 'shared',
        })

    def _log(self, message):
        match = self.env['pan.mail.matcher'].match(message, mailbox=self.mailbox)
        return self.env['pan.mail.routing.log'].log_decision(
            mailbox=self.mailbox, match=match, outcome='fallback',
            subject=message.get('subject'), email_from='customer@example.com',
            internet_message_id=message.get('message_id'),
        )

    def test_an_empty_header_set_is_visible_as_zero_references(self):
        """The question issue #94 had to reverse-engineer from four tables."""
        log = self._log({
            'message_id': '<orphan@example.com>',
            'subject': 'Re: Order 12',
            'headers': {},
        })

        self.assertEqual(log.reference_count, 0)
        self.assertFalse(log.reference_ids)

    def test_a_chain_that_resolved_to_nothing_is_visible_too(self):
        """Same outcome, different cause: the headers were there and unknown."""
        log = self._log({
            'message_id': '<reply@example.com>',
            'subject': 'Re: Order 12',
            'headers': {
                'In-Reply-To': '<unknown@example.com>',
                'References': '<root@example.com> <unknown@example.com>',
            },
        })

        self.assertEqual(log.reference_count, 2)
        self.assertIn('<unknown@example.com>', log.reference_ids)
        self.assertEqual(log.thread_id, '<root@example.com>')


@tagged('pan_mail_pro', 'post_install', '-at_install')
class TestGraphThreadingHeaders(TransactionCase):
    """Graph's header collection is optional; the MAPI properties are not."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.client = cls.env['microsoft.graph.client']

    def test_headers_are_read_from_the_extended_properties_when_absent(self):
        normalized = self.client._normalize_message({
            'id': 'GRAPH-1',
            'conversationId': 'CONV-IN',
            'subject': 'Re: Order 12',
            'singleValueExtendedProperties': [
                {'id': 'String 0x1035', 'value': '<reply@example.com>'},
                {'id': 'String 0x1042', 'value': '<sent@outlook.com>'},
                {'id': 'String 0x1039', 'value': '<root@x> <sent@outlook.com>'},
            ],
        })

        self.assertEqual(normalized['headers']['in-reply-to'], '<sent@outlook.com>')
        self.assertEqual(normalized['headers']['references'], '<root@x> <sent@outlook.com>')
        self.assertEqual(normalized['message_id'], '<reply@example.com>')

    def test_the_header_collection_still_wins_when_it_is_there(self):
        """The properties are a fallback, not a second source of truth."""
        normalized = self.client._normalize_message({
            'id': 'GRAPH-1',
            'internetMessageId': '<reply@example.com>',
            'internetMessageHeaders': [
                {'name': 'In-Reply-To', 'value': '<from-headers@outlook.com>'},
            ],
            'singleValueExtendedProperties': [
                {'id': 'String 0x1042', 'value': '<from-properties@outlook.com>'},
            ],
        })

        self.assertEqual(
            normalized['headers']['in-reply-to'], '<from-headers@outlook.com>')

    def test_a_message_with_neither_normalizes_without_raising(self):
        normalized = self.client._normalize_message({'id': 'GRAPH-1'})

        self.assertEqual(normalized['headers'], {})
        self.assertFalse(normalized['message_id'])

    def test_the_threading_properties_are_requested(self):
        """A fallback nobody asks the API for is not a fallback."""
        captured = {}

        class _Response:
            @staticmethod
            def raise_for_status():
                return None

            @staticmethod
            def json():
                return {'id': 'GRAPH-1'}

        def _fake_request(client_self, method, url, headers, timeout=30, **kwargs):
            captured['params'] = kwargs.get('params')
            return _Response()

        account = self.env['pan.mail.account']
        with patch.object(type(self.client), 'get_valid_token',
                          autospec=True, return_value='token'), \
             patch.object(type(self.client), '_request_with_retry',
                          autospec=True, side_effect=_fake_request):
            self.client._graph_get_message(account, 'support@company.test', 'GRAPH-1')

        expand = captured['params']['$expand']
        self.assertIn('singleValueExtendedProperties', expand)
        for prop_id in ('String 0x1035', 'String 0x1039', 'String 0x1042'):
            self.assertIn(prop_id, expand)


@tagged('pan_mail_pro', 'post_install', '-at_install')
class TestSentCopyReindexes(TransactionCase):
    """The sent copy is the message as it left. The draft was only a promise."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env['pan.mail.domain'].set_domains(['gate-fixture.test'])
        cls.fetcher = cls.env['pan.mail.fetcher']
        cls.mailbox = cls.env['pan.mail.mailbox'].create({
            'email': 'support@company.test',
            'mailbox_type': 'shared',
        })
        cls.partner = cls.env['res.partner'].create({
            'name': 'External Customer', 'email': 'customer@example.com',
        })
        cls.message = cls.partner.with_context(
            mail_create_nosubscribe=True, mail_notrack=True,
        ).message_post(body='<p>hi</p>', message_type='email',
                       subtype_xmlid='mail.mt_comment')

    def _sent_copy(self, **overrides):
        """The Sent Items copy of the mail we just posted, as Graph returns it."""
        message = {
            'provider_message_id': 'GRAPH-SENT-1',
            'message_id': '<real-wire-id@outlook.com>',
            'thread_id': 'CONV-REAL',
            'subject': 'Question',
            'headers': {
                'x-odoo-model': 'res.partner',
                'x-odoo-record-id': str(self.partner.id),
                'x-odoo-message-id': str(self.message.id),
            },
        }
        message.update(overrides)
        return message

    def _ctx(self, full_message, **overrides):
        ctx = {
            'mailbox': self.mailbox,
            'folder': 'sent',
            'is_outgoing': True,
            'message': full_message,
            'internet_message_id': full_message.get('message_id'),
            'force_import': False,
            'full_message': full_message,
        }
        ctx.update(overrides)
        return ctx

    def _gate(self, full_message):
        return self.fetcher._gate_odoo_originated(self._ctx(full_message))

    def test_the_mail_is_still_refused(self):
        """Harvesting the ids must not turn the loop guard into an import."""
        skip = self._gate(self._sent_copy())

        self.assertEqual(skip.reason, 'odoo_originated')

    def test_the_wire_message_id_is_indexed_onto_the_original_message(self):
        """What the draft promised and what went out are not always the same."""
        self._gate(self._sent_copy())

        parent = self.env['pan.mail.matcher']._resolve_message_id(
            '<real-wire-id@outlook.com>')

        self.assertEqual(parent, self.message)

    def test_the_real_thread_handle_is_linked_to_the_record(self):
        self._gate(self._sent_copy())

        link = self.env['pan.mail.thread.link'].search([
            ('mailbox_id', '=', self.mailbox.id),
            ('thread_id', '=', 'CONV-REAL'),
        ])

        self.assertEqual(link.model, 'res.partner')
        self.assertEqual(link.res_id, self.partner.id)

    def test_a_reply_then_threads_at_full_confidence(self):
        """End to end: the case that produced a 0.50 guess in production."""
        self._gate(self._sent_copy())

        decision = self.env['pan.mail.matcher'].match({
            'message_id': '<reply@example.com>',
            'thread_id': 'CONV-DIFFERENT',
            'subject': 'Re: Question',
            'headers': {'In-Reply-To': '<real-wire-id@outlook.com>'},
        }, mailbox=self.mailbox, partner=self.partner)

        self.assertEqual(decision['model'], 'res.partner')
        self.assertEqual(decision['res_id'], self.partner.id)
        self.assertEqual(decision['rule'], RULE_REFERENCES)
        self.assertNotEqual(decision['rule'], RULE_SUBJECT_PARTICIPANTS)

    def test_a_deleted_message_still_leaves_the_thread_linked(self):
        """The record is what the link needs; the message is a nicety."""
        copy = self._sent_copy()
        copy['headers']['x-odoo-message-id'] = '999999999'

        self._gate(copy)

        link = self.env['pan.mail.thread.link'].search([
            ('mailbox_id', '=', self.mailbox.id),
            ('thread_id', '=', 'CONV-REAL'),
        ])
        self.assertEqual(link.res_id, self.partner.id)

    def test_a_mail_we_did_not_send_is_not_touched(self):
        copy = self._sent_copy(headers={})

        self.assertIsNone(self._gate(copy))
        self.assertFalse(self.env['pan.mail.thread.link'].search_count([
            ('mailbox_id', '=', self.mailbox.id),
        ]))


@tagged('pan_mail_pro', 'post_install', '-at_install')
class TestSentCopyReachesTheReindex(TransactionCase):
    """The ladder, not one gate. Issue #107.

    `_gate_odoo_originated` was proven correct in isolation and unreachable in
    place: it sat behind `_gate_duplicate`, and the sent copy is a duplicate by
    construction because the send path indexes the Message-ID the provider
    minted for the draft — which on Microsoft is the id the sent mail keeps.
    Every test here goes through `_refuse`, seeded with the index state a send
    actually leaves behind, because that is the only shape in which the bug is
    visible.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env['pan.mail.domain'].set_domains(['gate-fixture.test'])
        cls.fetcher = cls.env['pan.mail.fetcher']
        cls.mailbox = cls.env['pan.mail.mailbox'].create({
            'email': 'support@company.test',
            'mailbox_type': 'shared',
        })
        cls.partner = cls.env['res.partner'].create({
            'name': 'External Customer', 'email': 'customer@example.com',
        })
        cls.message = cls.partner.with_context(
            mail_create_nosubscribe=True, mail_notrack=True,
        ).message_post(body='<p>hi</p>', message_type='email',
                       subtype_xmlid='mail.mt_comment')
        cls.wire_id = '<DU0PR09MB8199@outlook.com>'

    def _index_the_send(self):
        """Exactly what `mail.mail._index_sent_message` leaves behind.

        Odoo's own Message-ID under `odoo`, the one Graph minted for the draft
        under `provider`, and a thread link on the draft's conversationId.
        """
        Ref = self.env['pan.mail.message.ref']
        Ref.record(self.message, self.message.message_id, source='odoo')
        Ref.record(self.message, self.wire_id, source='provider')
        self.env['pan.mail.thread.link'].record_all(
            mailbox=self.mailbox,
            thread_ids=['CONV-DRAFT'],
            model='res.partner',
            res_id=self.partner.id,
            message=self.message,
        )

    def _sent_copy(self):
        """The Sent Items copy, carrying the handles the provider really used."""
        return {
            'provider_message_id': 'GRAPH-SENT-1',
            'message_id': self.wire_id,
            'thread_id': 'CONV-SENT',
            'subject': 'Question',
            'headers': {
                'x-odoo-model': 'res.partner',
                'x-odoo-record-id': str(self.partner.id),
                'x-odoo-message-id': str(self.message.id),
            },
        }

    def _ctx(self, full_message):
        return {
            'mailbox': self.mailbox,
            'folder': 'sent',
            'is_outgoing': True,
            'message': full_message,
            'internet_message_id': full_message.get('message_id'),
            'force_import': False,
            'full_message': full_message,
        }

    def test_the_loop_guard_refuses_the_sent_copy_not_the_duplicate_gate(self):
        """The bug, stated as an assertion about which gate answers."""
        self._index_the_send()

        skip = self.fetcher._refuse(self._ctx(self._sent_copy()))

        self.assertEqual(skip.reason, 'odoo_originated')

    def test_the_real_thread_handle_is_linked_through_the_ladder(self):
        """The re-index runs where it never could before."""
        self._index_the_send()

        self.fetcher._refuse(self._ctx(self._sent_copy()))

        link = self.env['pan.mail.thread.link'].search([
            ('mailbox_id', '=', self.mailbox.id),
            ('thread_id', '=', 'CONV-SENT'),
        ])
        self.assertEqual(link.model, 'res.partner')
        self.assertEqual(link.res_id, self.partner.id)

    def test_the_mail_still_does_not_enter(self):
        """Reachable is not the same as permitted."""
        self._index_the_send()

        self.assertTrue(self.fetcher._refuse(self._ctx(self._sent_copy())))

    def test_a_message_we_merely_imported_costs_no_provider_call(self):
        """The duplicate gate's cheapness is the reason it was first.

        A mail already imported has no `odoo` ref, so the loop guard answers
        from the index and never reaches for the message.
        """
        self.env['pan.mail.message.ref'].record(
            self.message, '<imported@example.com>', source='provider')
        ctx = {
            'mailbox': self.mailbox,
            'folder': 'inbox',
            'is_outgoing': False,
            'message': {'provider_message_id': 'GRAPH-IN-1'},
            'internet_message_id': '<imported@example.com>',
            'force_import': False,
        }

        with patch.object(type(self.fetcher), '_full_message', autospec=True,
                          side_effect=AssertionError('fetched the message')):
            skip = self.fetcher._refuse(ctx)

        self.assertEqual(skip.reason, 'duplicate')

    def test_an_unknown_message_is_read_by_the_loop_guard(self):
        """No index entry means it gets fetched downstream anyway."""
        copy = self._sent_copy()
        copy['message_id'] = '<never-seen@outlook.com>'

        skip = self.fetcher._refuse(self._ctx(copy))

        self.assertEqual(skip.reason, 'odoo_originated')

