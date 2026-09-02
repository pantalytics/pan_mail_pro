# -*- coding: utf-8 -*-
"""The gate ladder as a contract, not as an implementation detail.

`_gate_rules()` says "order is the contract: a gate may assume every gate
before it passed". A sentence in a docstring is not a contract, so this file
is. Three things it pins, each of which broke once already or would break
silently:

1. **The order.** The internal-domain check spent months guarding one folder
   because nothing said where it sat or what it could assume. Reordering the
   ladder is a legitimate change; doing it by accident is not, and a diff on
   this list is what tells the two apart.
2. **What each gate may assume.** A gate that resolves something for the ones
   after it -- the counterpart, the partner -- has to run before them.
3. **Which refusals leave a trace.** Whether a refusal reaches
   `pan.mail.item` is declared by the gate. Nothing else asserts that a gate
   saying `record=True` actually records, or that the ones saying nothing stay
   silent.

Deliberately no provider anywhere: the ladder is fed a pre-seeded context, the
way `_full_message()`'s cache allows. Gate behaviour is not a Graph question.
"""
from odoo.tests import tagged

from ..models.mail_provider_client import FOLDER_INBOX, FOLDER_SENT
from ..models.pan_mail_fetcher import Skip
from .common import OutlookProTestCase

CUSTOMER = 'customer@example.com'
INTERNET_ID = '<gate-001@example.com>'


@tagged('pan_mail_pro', 'post_install', '-at_install')
class TestIncomingGates(OutlookProTestCase):

    def setUp(self):
        super().setUp()
        self.processor = self.env['microsoft.incoming.mail.processor']
        self.mailbox = self.personal_mailbox
        self.mailbox.write({'x_sync_mode': 'all'})

    def _ctx(self, folder=FOLDER_INBOX, **full):
        """A context with the full message pre-seeded, so no gate reaches out.

        This is the same cache `_full_message()` uses to keep a duplicate from
        costing a provider round-trip; here it keeps the tests off the network.
        """
        full_message = {
            'message_id': INTERNET_ID,
            'subject': 'Question about my order',
            'from': {'email': CUSTOMER, 'name': 'External Customer'},
            'to': [{'email': 'sales@company.test', 'name': 'Sales'}],
            'cc': [],
            'headers': {},
            'has_attachments': False,
            'body_html': '<p>Where is it?</p>',
        }
        full_message.update(full)
        return {
            'mailbox': self.mailbox,
            'folder': folder,
            'message': {'message_id': INTERNET_ID, 'provider_message_id': 'X1'},
            'full_message': full_message,
            'internet_message_id': INTERNET_ID,
            'is_outgoing': folder == FOLDER_SENT,
            'force_import': False,
        }

    # ------------------------------------------------------------------ #
    # The contract
    # ------------------------------------------------------------------ #
    def test_the_ladder_is_the_documented_order(self):
        """A reorder is a real change and should read as one in the diff."""
        self.assertEqual(
            self.processor._gate_rules(),
            [
                '_gate_duplicate',
                '_gate_odoo_originated',
                '_gate_counterpart',
                '_gate_internal_domain',
                '_gate_blocked_contact',
                '_gate_internal_user',
                '_gate_sync_mode',
            ],
            "the ladder order is the contract; change it deliberately or not at all",
        )

    def test_every_named_gate_exists(self):
        """A name in the list with no method behind it fails at runtime, on a
        customer's mailbox, in a cron nobody is watching."""
        for name in self.processor._gate_rules():
            self.assertTrue(
                callable(getattr(self.processor, name, None)),
                "%s is listed in _gate_rules() but is not a method" % name,
            )

    def test_the_counterpart_gate_runs_before_everything_that_reads_it(self):
        """Gates 4 to 7 ask about the address gate 3 resolved."""
        order = self.processor._gate_rules()
        counterpart = order.index('_gate_counterpart')
        for reader in ('_gate_internal_domain', '_gate_blocked_contact',
                       '_gate_internal_user', '_gate_sync_mode'):
            self.assertGreater(
                order.index(reader), counterpart,
                "%s reads the counterpart, so it must run after it" % reader,
            )

    def test_the_partner_gate_runs_before_the_gates_that_read_it(self):
        order = self.processor._gate_rules()
        self.assertGreater(
            order.index('_gate_internal_user'), order.index('_gate_blocked_contact'),
            "_gate_blocked_contact resolves the partner the internal-user gate reads",
        )
        self.assertGreater(
            order.index('_gate_sync_mode'), order.index('_gate_blocked_contact'),
            "_gate_sync_mode reads the partner the blocked-contact gate resolved",
        )

    # ------------------------------------------------------------------ #
    # Direction lives in one gate
    # ------------------------------------------------------------------ #
    def test_inbox_takes_the_counterpart_from_the_sender(self):
        ctx = self._ctx(FOLDER_INBOX)

        self.assertIsNone(self.processor._gate_counterpart(ctx))
        self.assertEqual(ctx['contact_email'], CUSTOMER)

    def test_sent_items_takes_the_counterpart_from_the_recipient(self):
        ctx = self._ctx(FOLDER_SENT, to=[{'email': CUSTOMER, 'name': 'External Customer'}])

        self.assertIsNone(self.processor._gate_counterpart(ctx))
        self.assertEqual(ctx['contact_email'], CUSTOMER)

    def test_a_sent_item_with_no_recipient_is_refused(self):
        ctx = self._ctx(FOLDER_SENT, to=[])

        skip = self.processor._gate_counterpart(ctx)
        self.assertEqual(skip.reason, 'no_recipient')

    # ------------------------------------------------------------------ #
    # Which refusals leave a trace
    # ------------------------------------------------------------------ #
    def test_a_recording_refusal_reaches_the_triage_queue(self):
        """An unknown contact is a decision a person may want to reverse."""
        self.mailbox.write({'x_sync_mode': 'known_partners'})
        ctx = self._ctx()
        ctx['full_message']['from'] = {'email': 'stranger@nowhere.test', 'name': ''}
        before = self.env['pan.mail.item'].search_count([])

        skip = self.processor._refuse(ctx)

        self.assertEqual(skip.reason, 'unknown_contact')
        self.assertTrue(skip.record)
        self.assertEqual(
            self.env['pan.mail.item'].search_count([]), before + 1,
            "a refusal declaring record=True must actually record",
        )

    def test_a_silent_refusal_leaves_nothing_behind(self):
        """The block list is an objection to processing, so a queue row naming
        the person would itself be processing."""
        self.external_partner.x_email_sync_blocked = True
        ctx = self._ctx()
        before = self.env['pan.mail.item'].search_count([])

        skip = self.processor._refuse(ctx)

        self.assertEqual(skip.reason, 'blocked_contact')
        self.assertFalse(skip.record)
        self.assertEqual(
            self.env['pan.mail.item'].search_count([]), before,
            "a blocked contact must leave no trace at all",
        )

    def test_the_ladder_stops_at_the_first_refusal(self):
        """Gate 2 refuses, so gate 3 never resolves a counterpart."""
        ctx = self._ctx(headers={'x-odoo-model': 'crm.lead'})

        skip = self.processor._refuse(ctx)

        self.assertEqual(skip.reason, 'odoo_originated')
        self.assertNotIn(
            'contact_email', ctx,
            "a gate after the refusal must not have run",
        )

    def test_a_clean_message_passes_the_whole_ladder(self):
        self.assertIsNone(self.processor._refuse(self._ctx()))

    def test_skip_defaults_to_leaving_no_trace(self):
        """The safe default: a new gate records only if it says so."""
        skip = Skip('some_reason')
        self.assertFalse(skip.record)
        self.assertFalse(skip.quiet)
