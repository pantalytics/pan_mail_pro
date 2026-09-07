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
3. **That a refusal is a refusal.** No gate may import the mail it turned
   away, and none may leave a copy of it behind. 19.0.7.0.0 removed the triage
   queue that made this a per-gate choice; the property it protected is the
   same one, so the assertions stayed.

Deliberately no provider anywhere: the ladder is fed a pre-seeded context, the
way `_full_message()`'s cache allows. Gate behaviour is not a Graph question.
"""
from odoo.tests import tagged

from ..models.mail_provider_client import FOLDER_INBOX, FOLDER_SENT
from ..models.pan_mail_fetcher import Skip
from ..models.pan_mail_mailbox import SYNCING_MODES
from .common import MailProTestCase

CUSTOMER = 'customer@example.com'
# The fixture declares gate-fixture.test as the company's own domain.
INTERNAL = 'planning@gate-fixture.test'
OTHER_INTERNAL = 'administratie@gate-fixture.test'
INTERNET_ID = '<gate-001@example.com>'


@tagged('pan_mail_pro', 'post_install', '-at_install')
class TestIncomingGates(MailProTestCase):

    def setUp(self):
        super().setUp()
        self.processor = self.env['pan.mail.fetcher']
        self.mailbox = self.personal_mailbox
        self.mailbox.write({'sync_mode': 'all'})

    def _messages_on(self, partner):
        """Chatter on a contact. A refused mail must not add to it, and must
        not leave a copy of itself anywhere else either."""
        return self.env['mail.message'].search_count([
            ('model', '=', 'res.partner'), ('res_id', '=', partner.id),
        ])

    def _ctx(self, folder=FOLDER_INBOX, **full):
        """A context with the full message pre-seeded, so no gate reaches out.

        This is the same cache `_full_message()` uses to keep a duplicate from
        costing a provider round-trip; here it keeps the tests off the network.
        """
        full_message = {
            # The normalized shape always carries this, so a fixture without
            # it tests a message no provider could produce.
            'provider_message_id': 'X1',
            'message_id': INTERNET_ID,
            'date': '2026-02-01 10:30:00',
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
    # The counterpart rule — both directions, any external party
    # ------------------------------------------------------------------ #
    def test_a_sent_item_to_our_own_address_never_enters(self):
        """The case that produced this gate, in one test.

        A colleague mails a shared internal address; the sync reads it from the
        Sent folder. This gate guarded the inbox only, so the mail came in,
        landed on that address's contact card, and went back out to everyone
        following it.
        """
        ctx = self._ctx(FOLDER_SENT, to=[{'email': INTERNAL, 'name': 'Planning'}])

        skip = self.processor._refuse(ctx)

        self.assertEqual(skip.reason, 'internal_domain')

    def test_an_inbox_mail_from_our_own_address_never_enters(self):
        ctx = self._ctx(FOLDER_INBOX)
        ctx['full_message']['from'] = {'email': INTERNAL, 'name': 'Planning'}

        skip = self.processor._refuse(ctx)

        self.assertEqual(skip.reason, 'internal_domain')

    def test_one_external_recipient_makes_it_correspondence(self):
        """Any external party means the content already left the building, so
        the mail is logged — on the external party, not on the colleague."""
        ctx = self._ctx(FOLDER_SENT, to=[
            {'email': INTERNAL, 'name': 'Planning'},
            {'email': CUSTOMER, 'name': 'External Customer'},
        ])

        self.assertIsNone(self.processor._refuse(ctx))
        self.assertEqual(
            ctx['contact_email'], CUSTOMER,
            "the counterpart is the external party, whatever order they were in",
        )

    def test_the_first_external_recipient_wins(self):
        ctx = self._ctx(FOLDER_SENT, to=[
            {'email': CUSTOMER, 'name': 'External Customer'},
            {'email': 'second@elsewhere.test', 'name': 'Someone Else'},
        ])

        self.assertIsNone(self.processor._refuse(ctx))
        self.assertEqual(ctx['contact_email'], CUSTOMER)

    def test_every_recipient_ours_means_nothing_enters(self):
        ctx = self._ctx(FOLDER_SENT, to=[
            {'email': INTERNAL, 'name': 'Planning'},
            {'email': OTHER_INTERNAL, 'name': 'Administration'},
        ])

        skip = self.processor._refuse(ctx)

        self.assertEqual(skip.reason, 'internal_domain')

    def test_internal_mail_leaves_nothing_but_a_log_line(self):
        """The one refusal that must never be reversible: an Import button
        here would be a button for leaking. The log line carries the mailbox,
        the Message-ID and the reason, which is all that may be kept about a
        mail we declined to read."""
        ctx = self._ctx(FOLDER_SENT, to=[{'email': INTERNAL, 'name': 'Planning'}])
        before = self._messages_on(self.external_partner)

        skip = self.processor._refuse(ctx)

        self.assertEqual(skip.reason, 'internal_domain')
        self.assertEqual(self._messages_on(self.external_partner), before)

    def test_a_forced_import_still_lifts_the_internal_filter(self):
        """`force_import` lifts the filters. Deliberately not extended to the
        guards that are not filters: duplicates, Odoo's own mail, a blocked
        contact."""
        ctx = self._ctx(FOLDER_SENT, to=[{'email': INTERNAL, 'name': 'Planning'}])
        ctx['force_import'] = True

        self.assertIsNone(self.processor._gate_internal_domain(ctx))

    # ------------------------------------------------------------------ #
    # A refusal is a refusal
    # ------------------------------------------------------------------ #
    def test_an_unknown_sender_is_refused_not_held(self):
        """A mailbox on `known_partners` leaves the mail where it is. There is
        no queue it waits in; widening the sync mode is how that answer
        changes."""
        self.mailbox.write({'sync_mode': 'known_partners'})
        ctx = self._ctx()
        ctx['full_message']['from'] = {'email': 'stranger@nowhere.test', 'name': ''}

        skip = self.processor._refuse(ctx)

        self.assertEqual(skip.reason, 'unknown_contact')

    def test_a_blocked_contact_leaves_nothing_behind(self):
        """The block list is an objection to processing, so any row naming the
        person would itself be processing."""
        self.external_partner.x_email_sync_blocked = True
        ctx = self._ctx()
        before = self._messages_on(self.external_partner)

        skip = self.processor._refuse(ctx)

        self.assertEqual(skip.reason, 'blocked_contact')
        self.assertEqual(
            self._messages_on(self.external_partner), before,
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

    def test_skip_defaults_to_a_visible_refusal(self):
        """The safe default: a new gate is logged at INFO unless it says
        otherwise. Only the duplicate gate is allowed to be quiet."""
        skip = Skip('some_reason')
        self.assertEqual(skip.detail, '')
        self.assertFalse(skip.quiet)


@tagged('pan_mail_pro', 'post_install', '-at_install')
class TestOutgoingCaptureIsItsOwnSwitch(MailProTestCase):
    """Incoming mail and mail sent outside Odoo are two questions, not one.

    They shared `sync_mode` until 19.0.7.4.0, so a customer who asked for
    incoming mail also got a copy of everything their people wrote in Outlook.
    That is a different promise to the mailbox's owner, and nobody was asked to
    make it. These assertions pin the split: which folders a mailbox reads, and
    that the Sent folder never creates a contact whatever the incoming mode
    says.
    """

    def setUp(self):
        super().setUp()
        self.processor = self.env['pan.mail.fetcher']
        self.mailbox = self.personal_mailbox

    def _folders(self):
        return self.processor._folders_to_sync(self.mailbox)

    def test_a_send_only_mailbox_reads_nothing(self):
        self.mailbox.write({'sync_mode': 'none', 'capture_sent': False})
        self.assertEqual(self._folders(), [])
        self.assertFalse(self.mailbox._syncs_mail())

    def test_incoming_alone_does_not_read_the_sent_folder(self):
        """The regression this split exists to prevent."""
        self.mailbox.write({'sync_mode': 'all', 'capture_sent': False})
        self.assertEqual(self._folders(), [FOLDER_INBOX])

    def test_sent_capture_alone_does_not_read_the_inbox(self):
        """The other half has to work on its own too, or it is not a split."""
        self.mailbox.write({'sync_mode': 'none', 'capture_sent': True})
        self.assertEqual(self._folders(), [FOLDER_SENT])
        self.assertTrue(self.mailbox._syncs_mail())

    def test_both_switches_read_both_folders(self):
        self.mailbox.write({'sync_mode': 'all', 'capture_sent': True})
        self.assertEqual(self._folders(), [FOLDER_INBOX, FOLDER_SENT])

    def test_a_new_mailbox_captures_nothing_it_was_not_asked_to(self):
        """The default is the careful answer. A mailbox created today does not
        start copying its owner's outbox because somebody switched on incoming
        mail."""
        self.assertFalse(
            self.env['pan.mail.mailbox'].default_get(['capture_sent'])
            .get('capture_sent'),
            "capture_sent must default to off",
        )

    def test_a_capture_only_mailbox_is_picked_up_by_the_cron(self):
        """The cron used to filter on the sync mode alone, which would have
        skipped every mailbox that only logs what it sends."""
        self.mailbox.write({'sync_mode': 'none', 'capture_sent': True})
        domain = [
            '|',
            ('sync_mode', 'in', SYNCING_MODES),
            ('capture_sent', '=', True),
        ]
        self.assertIn(
            self.mailbox,
            self.env['pan.mail.mailbox'].search(domain),
        )

    def test_a_capture_only_mailbox_needs_credentials(self):
        """Reading the Sent folder is reading, so the mailbox has to be able to
        reach the provider even with incoming mail switched off."""
        self.mailbox.write({'sync_mode': 'none', 'capture_sent': True})
        self.assertTrue(self.mailbox._needs_credentials())


@tagged('pan_mail_pro', 'post_install', '-at_install')
class TestSentItemsNeverCreateAContact(MailProTestCase):
    """A sent item is logged onto a contact that already exists, or not at all.

    Deliberately not the three-way choice the inbox gets. Mailing a stranger
    from Outlook is not a statement that they belong in the database, and a
    customer who switches Sent capture on wants their correspondence with known
    contacts, not a contact list built from their outbox.
    """

    def setUp(self):
        super().setUp()
        self.processor = self.env['pan.mail.fetcher']
        self.mailbox = self.personal_mailbox
        self.mailbox.write({'sync_mode': 'all', 'capture_sent': True})

    def _ctx(self, folder, partner=None):
        return {
            'mailbox': self.mailbox,
            'folder': folder,
            'is_outgoing': folder == FOLDER_SENT,
            'partner': partner,
            'force_import': False,
            'internet_message_id': INTERNET_ID,
        }

    def test_an_unknown_recipient_is_refused_even_on_the_widest_mode(self):
        skip = self.processor._gate_sync_mode(self._ctx(FOLDER_SENT))

        self.assertIsNotNone(skip, "sync_mode='all' must not widen the Sent folder")
        self.assertEqual(skip.reason, 'unknown_contact')

    def test_the_same_message_would_be_accepted_from_the_inbox(self):
        """Same mailbox, same unknown address, opposite direction. The
        asymmetry is the decision, so it is asserted rather than implied."""
        self.assertIsNone(self.processor._gate_sync_mode(self._ctx(FOLDER_INBOX)))

    def test_a_known_contact_passes(self):
        skip = self.processor._gate_sync_mode(
            self._ctx(FOLDER_SENT, partner=self.external_partner)
        )
        self.assertIsNone(skip)
