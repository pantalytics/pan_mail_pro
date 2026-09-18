# -*- coding: utf-8 -*-
"""
Unit tests for the provider-neutral thread matcher.

Deliberately free of HTTP mocks and provider fixtures: the matcher takes a
normalized message dict and returns a decision, so every rule can be exercised
by handing it a dict. That is most of the reason it is a separate model — the
old `_find_parent_message` could only be tested by driving a full Graph sync.

Each test names the failure it prevents, because every one of these is a silent
failure in production: mail still arrives, just somewhere nobody looks.
"""
from datetime import timedelta
from unittest.mock import patch

from odoo import fields
from odoo.tests import TransactionCase, tagged

from odoo.addons.pan_mail_pro.models.pan_mail_matcher import (
    AUTO_ROUTE_CONFIDENCE,
    RULE_ODOO_HEADERS,
    RULE_ONLY_OPEN_RECORD,
    RULE_RECORD_REFERENCE,
    RULE_REFERENCES,
    RULE_SUBJECT_PARTICIPANTS,
    RULE_THREAD_LINK,
    RULE_THREAD_LINK_LEGACY,
)


@tagged('pan_mail_pro', 'post_install', '-at_install')
class TestMailMatcher(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        # Mail Pro refuses to create a mailbox while the internal domain
        # list is empty. A domain nothing in this fixture uses, so the gate
        # opens without turning any fixture address internal.
        cls.env['pan.mail.domain'].set_domains(['gate-fixture.test'])
        cls.matcher = cls.env['pan.mail.matcher']

        Mailbox = cls.env['pan.mail.mailbox']
        cls.mailbox = Mailbox.create({
            'email': 'support@company.test',
        })
        cls.other_mailbox = Mailbox.create({
            'email': 'sales@company.test',
        })

        cls.customer = cls.env['res.partner'].create({
            'name': 'Customer One',
            'email': 'customer@example.com',
        })
        cls.other_customer = cls.env['res.partner'].create({
            'name': 'Customer Two',
            'email': 'other@example.com',
        })

        # Two records that a mail could plausibly land on, so "it picked the
        # right one" is a meaningful assertion rather than the only option.
        cls.lead = cls.env['crm.lead'].create({'name': 'Existing opportunity'})
        cls.other_lead = cls.env['crm.lead'].create({'name': 'Unrelated opportunity'})

    # ------------------------------------------------------------------ #
    # Helpers
    # ------------------------------------------------------------------ #

    def _message(self, subject='Question', thread_id=None, headers=None):
        """A normalized message dict, as a provider client would return it."""
        return {
            'message_id': '<incoming@example.com>',
            'thread_id': thread_id,
            'subject': subject,
            'from': {'email': self.customer.email, 'name': self.customer.name},
            'headers': headers or {},
        }

    def _sale_order(self):
        """An order whose `name` is a real sequence, which is the point.

        `sale` is installed alongside the module in CI precisely so paths like
        this one run instead of skipping themselves.
        """
        return self.env['sale.order'].create({'partner_id': self.customer.id})

    def _route_mailbox_to(self, model):
        """Point the mailbox at a team, the way a support address is set up."""
        alias = self.env['mail.alias'].create({
            'alias_name': 'matcher-fixture',
            'alias_model_id': self.env['ir.model']._get(model).id,
        })
        self.mailbox.write({'alias_id': alias.id, 'route_to_team': True})
        return alias

    def _post_on(self, record, message_id, subject='Question', author=None, date=None):
        """Post a message onto a record and return it, as an import would."""
        message = record.with_context(
            mail_create_nosubscribe=True, mail_notrack=True,
        ).message_post(
            body='<p>hello</p>',
            subject=subject,
            message_type='email',
            subtype_xmlid='mail.mt_comment',
            author_id=(author or self.customer).id,
            message_id=message_id,
        )
        if date:
            message.sudo().write({'date': date})
        return message

    # ------------------------------------------------------------------ #
    # Rule 1 — our own headers
    # ------------------------------------------------------------------ #

    def test_odoo_headers_win(self):
        """A mail carrying our own routing headers needs no guessing."""
        decision = self.matcher.match(self._message(headers={
            'X-Odoo-Model': 'crm.lead',
            'X-Odoo-Record-Id': str(self.lead.id),
        }), mailbox=self.mailbox)

        self.assertEqual(decision['model'], 'crm.lead')
        self.assertEqual(decision['res_id'], self.lead.id)
        self.assertEqual(decision['rule'], RULE_ODOO_HEADERS)

    def test_odoo_headers_pointing_at_a_deleted_record_do_not_match(self):
        """A stale reference must not resurrect a record that no longer exists.

        Without the existence check this routes to a ghost id and message_post
        raises inside the cron instead of falling through to the next rule.
        """
        ghost = self.env['crm.lead'].create({'name': 'To be deleted'})
        ghost_id = ghost.id
        ghost.unlink()

        decision = self.matcher.match(self._message(headers={
            'X-Odoo-Model': 'crm.lead',
            'X-Odoo-Record-Id': str(ghost_id),
        }), mailbox=self.mailbox)

        self.assertFalse(decision['model'])

    # ------------------------------------------------------------------ #
    # Rule 2 — References (the portable one)
    # ------------------------------------------------------------------ #

    def test_in_reply_to_matches(self):
        parent = self._post_on(self.lead, '<parent@example.com>')

        decision = self.matcher.match(self._message(headers={
            'In-Reply-To': '<parent@example.com>',
        }), mailbox=self.mailbox)

        self.assertEqual(decision['model'], 'crm.lead')
        self.assertEqual(decision['res_id'], self.lead.id)
        self.assertEqual(decision['rule'], RULE_REFERENCES)
        self.assertEqual(decision['parent_message_id'], parent.id)

    def test_references_chain_matches_when_in_reply_to_is_absent(self):
        """The regression this whole rung exists for.

        The old implementation read In-Reply-To only. A client that sets just
        References — or a mail that came back through a forward — fell straight
        through to the unscoped conversation-id lookup.
        """
        self._post_on(self.lead, '<root@example.com>')

        decision = self.matcher.match(self._message(headers={
            'References': '<unknown@example.com> <root@example.com>',
        }), mailbox=self.mailbox)

        self.assertEqual(decision['model'], 'crm.lead')
        self.assertEqual(decision['res_id'], self.lead.id)
        self.assertEqual(decision['rule'], RULE_REFERENCES)

    def test_nearest_ancestor_wins_over_older_ones(self):
        """A thread that moved records must follow the move, not its origin."""
        self._post_on(self.other_lead, '<root@example.com>')
        self._post_on(self.lead, '<recent@example.com>')

        decision = self.matcher.match(self._message(headers={
            # References is root-first, so <recent> is the nearest ancestor.
            'References': '<root@example.com> <recent@example.com>',
        }), mailbox=self.mailbox)

        self.assertEqual(decision['res_id'], self.lead.id)

    def test_references_resolve_through_the_wire_message_id(self):
        """Microsoft assigns its own Message-ID, and that is what comes back.

        Graph mints `internetMessageId` on send and gives no way to override it,
        so the id the recipient replies to is never the one Odoo generated. The
        ref index is what closes that gap.
        """
        message = self._post_on(self.lead, '<odoo-generated@company.test>')
        self.env['pan.mail.message.ref'].record(
            message, '<graph-assigned@outlook.com>', source='provider')

        decision = self.matcher.match(self._message(headers={
            'In-Reply-To': '<graph-assigned@outlook.com>',
        }), mailbox=self.mailbox)

        self.assertEqual(decision['model'], 'crm.lead')
        self.assertEqual(decision['res_id'], self.lead.id)

    def test_references_are_honoured_without_a_mailbox(self):
        """IMAP-shaped input: no mailbox context, no thread id, just headers."""
        self._post_on(self.lead, '<parent@example.com>')

        decision = self.matcher.match(self._message(headers={
            'In-Reply-To': '<parent@example.com>',
        }))

        self.assertEqual(decision['model'], 'crm.lead')

    # ------------------------------------------------------------------ #
    # Rule 3 — provider thread id, scoped
    # ------------------------------------------------------------------ #

    def test_thread_link_matches_within_the_same_mailbox(self):
        message = self._post_on(self.lead, '<seen@example.com>')
        self.env['pan.mail.thread.link'].record(
            mailbox=self.mailbox, thread_id='CONV-1',
            model='crm.lead', res_id=self.lead.id, message=message)

        decision = self.matcher.match(
            self._message(thread_id='CONV-1'), mailbox=self.mailbox)

        self.assertEqual(decision['model'], 'crm.lead')
        self.assertEqual(decision['rule'], RULE_THREAD_LINK)
        self.assertEqual(decision['parent_message_id'], message.id)

    def test_thread_link_does_not_leak_across_mailboxes(self):
        """Thread ids are mailbox-local; matching them globally is the bug.

        Two mailboxes can hold the same id for entirely unrelated exchanges,
        which is how a reply to sales@ lands on a support@ ticket.
        """
        self.env['pan.mail.thread.link'].record(
            mailbox=self.other_mailbox, thread_id='CONV-1',
            model='crm.lead', res_id=self.lead.id)

        decision = self.matcher.match(
            self._message(thread_id='CONV-1'), mailbox=self.mailbox)

        self.assertFalse(decision['model'])

    def test_stale_thread_link_is_not_evidence(self):
        """Outlook derives conversationId from the subject, so ids resurface."""
        link = self.env['pan.mail.thread.link'].record(
            mailbox=self.mailbox, thread_id='CONV-OLD',
            model='crm.lead', res_id=self.lead.id)
        link.sudo().write({
            'last_seen': fields.Datetime.now() - timedelta(days=400),
        })

        decision = self.matcher.match(
            self._message(thread_id='CONV-OLD'), mailbox=self.mailbox)

        self.assertFalse(decision['model'])

    def test_legacy_conversation_id_picks_the_newest_message(self):
        """The exact misrouting reported from production.

        The old lookup ordered `id asc`, so a reply threaded onto whichever
        record first touched the conversation — typically a months-old contact
        chatter post rather than the ticket the customer is actually answering.
        """
        old = self._post_on(self.other_lead, '<old@example.com>')
        new = self._post_on(self.lead, '<new@example.com>')
        (old | new).sudo().write({'x_provider_thread_id': 'CONV-LEGACY'})

        decision = self.matcher.match(
            self._message(thread_id='CONV-LEGACY'), mailbox=self.mailbox)

        self.assertEqual(decision['rule'], RULE_THREAD_LINK_LEGACY)
        self.assertEqual(decision['res_id'], self.lead.id)
        self.assertEqual(decision['parent_message_id'], new.id)

    def test_excluded_models_are_never_a_target(self):
        """Team routing must not thread a reply back onto contact chatter."""
        message = self._post_on(self.customer, '<on-partner@example.com>')
        message.sudo().write({'x_provider_thread_id': 'CONV-PARTNER'})

        decision = self.matcher.match(
            self._message(thread_id='CONV-PARTNER'),
            mailbox=self.mailbox,
            exclude_models=('res.partner',),
        )

        self.assertFalse(decision['model'])

    # ------------------------------------------------------------------ #
    # Rule 4 — a document reference quoted in the subject
    # ------------------------------------------------------------------ #

    def test_subject_reference_routes_on_an_exact_document_number(self):
        """The case rules 1 to 3 are silent on: a fresh mail about an order.

        No References chain, no thread the provider has seen, and today it
        falls all the way through to the contact's own chatter.
        """
        order = self._sale_order()

        decision = self.matcher.match(
            self._message(subject='Vraag over %s' % order.name),
            mailbox=self.mailbox,
            partner=self.customer,
        )

        self.assertEqual(decision['model'], 'sale.order')
        self.assertEqual(decision['res_id'], order.id)
        self.assertEqual(decision['rule'], RULE_RECORD_REFERENCE)
        self.assertGreaterEqual(decision['confidence'], AUTO_ROUTE_CONFIDENCE)

    def test_subject_reference_stops_being_an_answer_when_it_names_two(self):
        """Two documents in one subject is a question, not a lookup.

        The rule's whole claim is that it is exact. A subject naming an order
        *and* an invoice is not, so both drop to proposals and the mail is
        routed by nothing.
        """
        first = self._sale_order()
        second = self._sale_order()

        decision = self.matcher.match(
            self._message(subject='%s en %s samenvoegen' % (first.name, second.name)),
            mailbox=self.mailbox,
            partner=self.customer,
        )

        self.assertFalse(decision['model'], 'an ambiguous reference must not route')
        found = {(c['model'], c['res_id']) for c in decision['candidates']
                 if c['rule'] == RULE_RECORD_REFERENCE}
        self.assertEqual(found, {('sale.order', first.id), ('sale.order', second.id)})
        for candidate in decision['candidates']:
            self.assertLess(candidate['confidence'], AUTO_ROUTE_CONFIDENCE)

    def test_subject_reference_ignores_words_and_dates(self):
        """A token needs a letter and a digit, which is what keeps this cheap.

        Without that, every subject would run a search per word per model on
        a one-minute cron.
        """
        tokens = self.matcher._reference_tokens(
            'Re: betaling van 2026-09-18 voor de offerte')
        self.assertEqual(tokens, [])

        self.assertEqual(
            self.matcher._reference_tokens('Re: SO0042 en INV/2026/00017'),
            ['SO0042', 'INV/2026/00017'],
        )

    def test_subject_reference_yields_to_an_existing_thread(self):
        """A reply belongs on its thread, even when it still quotes the order.

        Order matters here: a conversation that started from an order and
        moved to a ticket keeps quoting the order number in every subject
        line, and it belongs on the ticket.
        """
        order = self._sale_order()
        parent = self._post_on(self.lead, '<thread@example.com>')

        decision = self.matcher.match(
            self._message(
                subject='Re: %s' % order.name,
                headers={'In-Reply-To': '<thread@example.com>'},
            ),
            mailbox=self.mailbox,
            partner=self.customer,
        )

        self.assertEqual(decision['model'], 'crm.lead')
        self.assertEqual(decision['res_id'], self.lead.id)
        self.assertEqual(decision['rule'], RULE_REFERENCES)
        self.assertEqual(decision['parent_message_id'], parent.id)

    # ------------------------------------------------------------------ #
    # Rule 5 — the sender's only open record, proposal only
    # ------------------------------------------------------------------ #

    def test_only_open_record_proposes_but_never_routes(self):
        """Arithmetic says there is one candidate; it does not say this is it.

        The customer with one open printer ticket who writes in about an
        invoice is why this never routes on its own. What it earns is the
        suggestion somebody confirms in one click.
        """
        self.lead.write({'partner_id': self.customer.id})
        self._route_mailbox_to('crm.lead')

        decision = self.matcher.match(
            self._message(subject='Nieuwe vraag'),
            mailbox=self.mailbox,
            partner=self.customer,
        )

        self.assertFalse(decision['model'], 'one open record is not a decision')
        candidate = decision['candidates'][0]
        self.assertEqual(candidate['rule'], RULE_ONLY_OPEN_RECORD)
        self.assertEqual(candidate['res_id'], self.lead.id)
        self.assertLess(candidate['confidence'], AUTO_ROUTE_CONFIDENCE)

    def test_only_open_record_is_silent_when_there_are_two(self):
        """Two open records is exactly the case a person has to settle."""
        self.lead.write({'partner_id': self.customer.id})
        self.other_lead.write({'partner_id': self.customer.id})
        self._route_mailbox_to('crm.lead')

        decision = self.matcher.match(
            self._message(subject='Nieuwe vraag'),
            mailbox=self.mailbox,
            partner=self.customer,
        )

        self.assertFalse([c for c in decision['candidates']
                          if c['rule'] == RULE_ONLY_OPEN_RECORD])

    def test_only_open_record_needs_a_mailbox_that_routes_somewhere(self):
        """Without a target model there is no set to be alone in.

        A mailbox that files to contact chatter says nothing about what its
        mail is about, so this rule has no premise to stand on.
        """
        self.lead.write({'partner_id': self.customer.id})

        decision = self.matcher.match(
            self._message(subject='Nieuwe vraag'),
            mailbox=self.mailbox,
            partner=self.customer,
        )

        self.assertFalse([c for c in decision['candidates']
                          if c['rule'] == RULE_ONLY_OPEN_RECORD])

    # ------------------------------------------------------------------ #
    # Rule 6 — subject heuristics, proposal only
    # ------------------------------------------------------------------ #

    def test_subject_match_proposes_but_never_routes(self):
        """A guess stays a guess: it appears as a candidate, never as a target."""
        self._post_on(self.lead, '<sub@example.com>', subject='Invoice 2024-11')

        decision = self.matcher.match(
            self._message(subject='Re: Invoice 2024-11'),
            mailbox=self.mailbox,
            partner=self.customer,
        )

        self.assertFalse(decision['model'], 'subject matching must not auto-route')
        self.assertTrue(decision['candidates'], 'but it must still propose')
        candidate = decision['candidates'][0]
        self.assertEqual(candidate['rule'], RULE_SUBJECT_PARTICIPANTS)
        self.assertEqual(candidate['res_id'], self.lead.id)
        self.assertLess(candidate['confidence'], AUTO_ROUTE_CONFIDENCE)

    def test_subject_match_requires_the_same_correspondent(self):
        self._post_on(self.lead, '<sub@example.com>', subject='Invoice 2024-11')

        decision = self.matcher.match(
            self._message(subject='Re: Invoice 2024-11'),
            mailbox=self.mailbox,
            partner=self.other_customer,
        )

        self.assertFalse(decision['candidates'])

    def test_subject_normalisation(self):
        normalize = self.matcher._normalize_subject
        self.assertEqual(normalize('Re: Order 12'), 'Order 12')
        self.assertEqual(normalize('RE: FW: Order 12'), 'Order 12')
        self.assertEqual(normalize('Antw: Order 12'), 'Order 12')
        self.assertEqual(normalize('Re[2]: Order 12'), 'Order 12')
        self.assertEqual(normalize('  Order   12  '), 'Order 12')
        # Not a prefix, just a word that starts with the same letters.
        self.assertEqual(normalize('Reminder: pay up'), 'Reminder: pay up')

    # ------------------------------------------------------------------ #
    # Thread id derivation — the IMAP path
    # ------------------------------------------------------------------ #

    def test_thread_id_is_derived_from_references_when_absent(self):
        """IMAP has no thread handle; the References root is the stand-in.

        Every participant in a thread carries the same root, so this gives
        providers without a thread concept the same rung the others get.
        """
        decision = self.matcher.match(self._message(headers={
            'References': '<root@example.com> <middle@example.com>',
            'In-Reply-To': '<middle@example.com>',
        }), mailbox=self.mailbox)

        self.assertEqual(decision['thread_id'], '<root@example.com>')

    def test_provider_thread_id_is_preferred_over_the_derived_one(self):
        decision = self.matcher.match(self._message(
            thread_id='CONV-1',
            headers={'References': '<root@example.com>'},
        ), mailbox=self.mailbox)

        self.assertEqual(decision['thread_id'], 'CONV-1')

    def test_reference_ids_are_ordered_nearest_first(self):
        ids = self.matcher._reference_ids({
            'in-reply-to': '<c@x>',
            'references': '<a@x> <b@x> <c@x>',
        })
        self.assertEqual(ids, ['<c@x>', '<b@x>', '<a@x>'])

    # ------------------------------------------------------------------ #
    # No match
    # ------------------------------------------------------------------ #

    def test_unknown_mail_returns_an_empty_decision(self):
        decision = self.matcher.match(self._message(), mailbox=self.mailbox)

        self.assertFalse(decision['model'])
        self.assertFalse(decision['res_id'])
        self.assertFalse(decision['rule'])
        self.assertEqual(decision['confidence'], 0.0)
        self.assertEqual(decision['candidates'], [])

    def test_a_broken_rule_does_not_stop_the_ladder(self):
        """One bad rule must degrade matching, not abort a whole cron batch."""
        self._post_on(self.lead, '<parent@example.com>')

        with patch.object(
            type(self.matcher), '_rule_odoo_headers',
            autospec=True, side_effect=ValueError('boom'),
        ):
            decision = self.matcher.match(self._message(headers={
                'In-Reply-To': '<parent@example.com>',
            }), mailbox=self.mailbox)

        self.assertEqual(decision['model'], 'crm.lead')
