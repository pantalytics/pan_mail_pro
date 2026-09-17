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
from unittest.mock import patch

from odoo.tests import tagged

from ..models.mail_provider_client import FOLDER_INBOX, FOLDER_SENT
from ..models.pan_mail_fetcher import Skip
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
        self.mailbox.write({'sync_level': 'everyone'})

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
                '_gate_odoo_originated',
                '_gate_duplicate',
                '_gate_counterpart',
                '_gate_internal_domain',
                '_gate_blocked_contact',
                '_gate_wanted',
            ],
            "the ladder order is the contract; change it deliberately or not at all",
        )

    def test_the_loop_guard_runs_before_the_duplicate_gate(self):
        """Issue #107: a gate that reads the mail before refusing it cannot sit
        behind a gate that refuses it earlier. The sent copy always matches the
        duplicate gate, because the send path indexed its Message-ID."""
        order = self.processor._gate_rules()
        self.assertLess(
            order.index('_gate_odoo_originated'), order.index('_gate_duplicate'),
            "the loop guard re-indexes the sent copy; behind the duplicate "
            "gate it never runs",
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
        """Gates 4 to 6 ask about the address gate 3 resolved."""
        order = self.processor._gate_rules()
        counterpart = order.index('_gate_counterpart')
        for reader in ('_gate_internal_domain', '_gate_blocked_contact',
                       '_gate_wanted'):
            self.assertGreater(
                order.index(reader), counterpart,
                "%s reads the counterpart, so it must run after it" % reader,
            )

    def test_the_partner_gate_runs_before_the_gates_that_read_it(self):
        order = self.processor._gate_rules()
        self.assertGreater(
            order.index('_gate_wanted'), order.index('_gate_blocked_contact'),
            "_gate_wanted reads the partner the blocked-contact gate resolved",
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

    def _refused_by(self, ctx):
        """The gate that refused, or None. Named rather than asserted on
        `_refuse` directly because a sent item that continues nothing is
        refused for a reason these tests are not about: they ask which address
        the ladder picked as the counterpart, not whether the mail may enter.
        """
        skip = self.processor._refuse(ctx)
        return skip.reason if skip else None

    def test_one_external_recipient_makes_it_correspondence(self):
        """Any external party means the content already left the building, so
        the internal-domain gate stands aside and the external party is the
        counterpart, not the colleague."""
        ctx = self._ctx(FOLDER_SENT, to=[
            {'email': INTERNAL, 'name': 'Planning'},
            {'email': CUSTOMER, 'name': 'External Customer'},
        ])

        self.assertNotEqual(self._refused_by(ctx), 'internal_domain')
        self.assertEqual(
            ctx['contact_email'], CUSTOMER,
            "the counterpart is the external party, whatever order they were in",
        )

    def test_the_first_external_recipient_wins(self):
        ctx = self._ctx(FOLDER_SENT, to=[
            {'email': CUSTOMER, 'name': 'External Customer'},
            {'email': 'second@elsewhere.test', 'name': 'Someone Else'},
        ])

        self.assertNotEqual(self._refused_by(ctx), 'internal_domain')
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
        self.mailbox.write({'sync_level': 'contacts'})
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

    def test_a_customer_with_a_portal_login_is_still_a_customer(self):
        """#103. A gate refused every address whose partner had a user, so a
        customer given portal access lost the mail they sent from that same
        address -- replies to our own threads included. Who is a colleague is
        the domain list's question, and it is asked one gate earlier."""
        ctx = self._ctx(**{'from': {
            'email': self.portal_user.email, 'name': self.portal_user.name,
        }})

        self.assertIsNone(self.processor._refuse(ctx))

    def test_skip_defaults_to_a_visible_refusal(self):
        """The safe default: a new gate is logged at INFO unless it says
        otherwise. Only the duplicate gate is allowed to be quiet."""
        skip = Skip('some_reason')
        self.assertEqual(skip.detail, '')
        self.assertFalse(skip.quiet)


@tagged('pan_mail_pro', 'post_install', '-at_install')
class TestEachDirectionIsItsOwnSwitch(MailProTestCase):
    """Receiving and sending are two questions, not one.

    They shared `sync_mode` until 19.0.7.6.0, so a customer who asked to receive
    mail in Odoo also got a copy of everything their people wrote in Outlook.
    That is a different promise to the mailbox's owner, and nobody was asked to
    make it. These assertions pin the split: which folders a mailbox reads, and
    that the Sent folder never creates a contact whatever the receiving scope
    says.
    """

    def setUp(self):
        super().setUp()
        self.processor = self.env['pan.mail.fetcher']
        self.mailbox = self.personal_mailbox

    def _folders(self):
        return self.processor._folders_to_sync(self.mailbox)

    def test_the_inbox_is_read_at_the_bottom_of_the_ladder(self):
        """Replies need no setting, so the folder they arrive in is never
        skipped. What may enter is the gate ladder's decision, not this."""
        self.mailbox.write({'sync_level': 'replies'})
        self.assertEqual(self._folders(), [FOLDER_INBOX])
        self.assertFalse(self.mailbox._syncs_more_than_replies())

    def test_the_sent_folder_is_read_from_both_on(self):
        for level in ('both', 'contacts', 'everyone'):
            self.mailbox.write({'sync_level': level})
            self.assertEqual(self._folders(), [FOLDER_INBOX, FOLDER_SENT], level)
            self.assertTrue(self.mailbox._syncs_more_than_replies(), level)

    def test_a_new_mailbox_starts_at_the_bottom(self):
        """Nobody lands on "create contacts from anyone" by accepting
        defaults: the widest answer is three deliberate clicks away."""
        defaults = self.env['pan.mail.mailbox'].default_get(['sync_level'])
        self.assertEqual(defaults.get('sync_level'), 'replies')

    def test_every_rung_above_replies_owes_credentials(self):
        """Reading the Sent folder is reading, so the mailbox has to be able to
        reach the provider from the second rung on."""
        self.mailbox.write({'sync_level': 'both'})
        self.assertTrue(self.mailbox._needs_credentials())

    def test_a_shared_replies_only_mailbox_still_owes_nothing(self):
        """Reply sync is best-effort on top, never a new obligation. A Microsoft
        shared mailbox sends with the author's own token and has no credentials
        of its own; making it owe some would turn a working configuration red.
        """
        mailbox = self.shared_mailbox
        mailbox.write({'sync_level': 'replies'})
        self.assertFalse(mailbox._needs_credentials())


@tagged('pan_mail_pro', 'post_install', '-at_install')
class TestSyncLadder(MailProTestCase):
    """Each rung keeps strictly more of the mailbox than the one below it.

    That nesting is the whole reason `sync_level` is one field and not three:
    a customer who climbs one step gets exactly one more kind of mail, and a
    customer who reads the four labels top to bottom reads a ladder rather than
    a menu of combinations. Every assertion here is one rung, one situation.
    """

    def setUp(self):
        super().setUp()
        self.processor = self.env['pan.mail.fetcher']
        self.mailbox = self.personal_mailbox

    def _ctx(self, folder, partner=None):
        return {
            'mailbox': self.mailbox,
            'folder': folder,
            'is_outgoing': folder == FOLDER_SENT,
            'partner': partner,
            'force_import': False,
            'internet_message_id': INTERNET_ID,
            'full_message': {'headers': {}},
        }

    def _wanted(self, level, folder, partner=None):
        self.mailbox.write({'sync_level': level})
        return self.processor._gate_wanted(self._ctx(folder, partner))

    def _reason(self, level, folder, partner=None):
        skip = self._wanted(level, folder, partner)
        return skip.reason if skip else None

    # --- what a rung adds --------------------------------------------------
    def test_replies_lets_no_new_conversation_in(self):
        self.assertEqual(
            self._reason('replies', FOLDER_INBOX, self.external_partner),
            'not_a_reply')

    def test_both_adds_the_sent_folder_and_nothing_from_the_inbox(self):
        """The second rung is about the owner's own replies, which enter on
        the reply clause; a new conversation from the inbox still waits for
        the third rung."""
        self.assertEqual(
            self._reason('both', FOLDER_INBOX, self.external_partner),
            'not_a_reply')

    def test_contacts_lets_a_known_contact_start_a_conversation(self):
        self.assertIsNone(self._wanted('contacts', FOLDER_INBOX, self.external_partner))

    def test_contacts_leaves_a_stranger_where_they_are(self):
        self.assertEqual(self._reason('contacts', FOLDER_INBOX), 'unknown_contact')

    def test_everyone_lets_a_stranger_in(self):
        self.assertIsNone(self._wanted('everyone', FOLDER_INBOX))

    def test_no_rung_lets_the_outbox_start_a_conversation(self):
        """Sending has no rung of its own: a sent item enters only as a reply.
        Where a conversation the owner starts from their mail app belongs is a
        question the module cannot answer, so no level widens it, not even to
        a known contact."""
        for level in ('both', 'contacts', 'everyone'):
            self.assertEqual(
                self._reason(level, FOLDER_SENT, self.external_partner),
                'not_a_reply', level)
            self.assertEqual(self._reason(level, FOLDER_SENT), 'not_a_reply', level)

    # --- what no rung takes away ------------------------------------------
    def test_a_reply_passes_at_every_rung(self):
        """The floor is not on the ladder: a reply to something Odoo holds
        belongs on its record whatever the setting says."""
        with patch.object(type(self.processor), '_is_reply_to_odoo', return_value=True):
            for level in ('replies', 'both', 'contacts', 'everyone'):
                self.assertIsNone(self._wanted(level, FOLDER_INBOX), level)
                self.assertIsNone(self._wanted(level, FOLDER_SENT), level)

    def test_each_rung_is_a_superset_of_the_one_below(self):
        """The property the labels promise, checked rather than implied: every
        situation that passes at one rung still passes at the next."""
        situations = [
            (FOLDER_INBOX, self.external_partner),
            (FOLDER_INBOX, None),
            (FOLDER_SENT, self.external_partner),
            (FOLDER_SENT, None),
        ]
        levels = ('replies', 'both', 'contacts', 'everyone')
        for lower, higher in zip(levels, levels[1:]):
            for folder, partner in situations:
                if self._wanted(lower, folder, partner) is None:
                    self.assertIsNone(
                        self._wanted(higher, folder, partner),
                        '%s passed at %s but not at %s' % (folder, lower, higher))


@tagged('pan_mail_pro', 'post_install', '-at_install')
class TestSentEmailOnlyEntersAsAReply(MailProTestCase):
    """A sent item enters on one door: it answers something Odoo already holds.

    Deliberately not offered the scope question that receiving gets, and
    deliberately narrower than "the recipient is a contact". Where a mail that
    starts a new conversation belongs -- the contact, a lead, an opportunity --
    is a question this module cannot answer yet, so it does not guess.
    """

    def setUp(self):
        super().setUp()
        self.processor = self.env['pan.mail.fetcher']
        self.mailbox = self.personal_mailbox
        self.mailbox.write({'sync_level': 'everyone'})

    def _ctx(self, folder, partner=None, headers=None):
        return {
            'mailbox': self.mailbox,
            'folder': folder,
            'is_outgoing': folder == FOLDER_SENT,
            'partner': partner,
            'force_import': False,
            'internet_message_id': INTERNET_ID,
            # Pre-seeded so the reply check reads the headers without a provider
            # round-trip, the same cache `_full_message()` fills.
            'full_message': {'headers': headers or {}},
        }

    def test_an_unknown_recipient_is_refused_even_on_the_widest_scope(self):
        skip = self.processor._gate_wanted(self._ctx(FOLDER_SENT))

        self.assertIsNotNone(
            skip, "a receiving scope of 'all' must not widen the Sent folder")
        self.assertEqual(skip.reason, 'not_a_reply')

    def test_the_same_message_would_be_accepted_from_the_inbox(self):
        """Same mailbox, same unknown address, opposite direction. The
        asymmetry is the decision, so it is asserted rather than implied."""
        self.assertIsNone(self.processor._gate_wanted(self._ctx(FOLDER_INBOX)))

    def test_a_known_contact_is_not_enough_on_its_own(self):
        """The narrowing decision, asserted rather than implied.

        A mail to an existing contact that continues nothing Odoo has is still
        a new conversation, and a new conversation has no home to land on yet.
        """
        skip = self.processor._gate_wanted(
            self._ctx(FOLDER_SENT, partner=self.external_partner)
        )

        self.assertIsNotNone(skip)
        self.assertEqual(skip.reason, 'not_a_reply')

    def test_a_reply_to_a_conversation_odoo_has_passes_without_a_contact(self):
        """The one case the mailbox form promises.

        The answer to a question that is already on a record belongs under it,
        whoever it went to -- a known contact is not required, and on its own
        is not enough.
        """
        parent = self.external_partner.message_post(
            body='The question Odoo already holds',
            message_type='email',
        )
        parent.message_id = '<parent@odoo.example.com>'

        skip = self.processor._gate_wanted(self._ctx(
            FOLDER_SENT,
            headers={'References': '<parent@odoo.example.com>'},
        ))

        self.assertIsNone(skip)
