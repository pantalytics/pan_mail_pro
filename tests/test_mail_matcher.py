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
        cls.matcher = cls.env['pan.mail.matcher']

        Mailbox = cls.env['x_microsoft.mailbox']
        cls.mailbox = Mailbox.create({
            'email': 'support@company.test',
            'x_mailbox_type': 'shared',
        })
        cls.other_mailbox = Mailbox.create({
            'email': 'sales@company.test',
            'x_mailbox_type': 'shared',
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
        (old | new).sudo().write({'x_microsoft_conversation_id': 'CONV-LEGACY'})

        decision = self.matcher.match(
            self._message(thread_id='CONV-LEGACY'), mailbox=self.mailbox)

        self.assertEqual(decision['rule'], RULE_THREAD_LINK_LEGACY)
        self.assertEqual(decision['res_id'], self.lead.id)
        self.assertEqual(decision['parent_message_id'], new.id)

    def test_excluded_models_are_never_a_target(self):
        """Team routing must not thread a reply back onto contact chatter."""
        message = self._post_on(self.customer, '<on-partner@example.com>')
        message.sudo().write({'x_microsoft_conversation_id': 'CONV-PARTNER'})

        decision = self.matcher.match(
            self._message(thread_id='CONV-PARTNER'),
            mailbox=self.mailbox,
            exclude_models=('res.partner',),
        )

        self.assertFalse(decision['model'])

    # ------------------------------------------------------------------ #
    # Rule 4 — subject heuristics, proposal only
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
