# -*- coding: utf-8 -*-
"""
The routing log: what happened to each incoming mail, and whether to care.

Better matching does not tell anyone where mail landed — it only makes the
answer right more often. These tests pin the two things that make the log worth
having: the outcomes are told apart, and `needs_review` flags exactly the cases
a human should look at and nothing else. A review queue that cries wolf on every
routed mail gets ignored, and then it may as well not exist.
"""
from odoo import fields
from odoo.tests import TransactionCase, tagged


@tagged('pan_mail_pro', 'post_install', '-at_install')
class TestRoutingLog(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        # Mail Pro refuses to create a mailbox while the internal domain
        # list is empty. A domain nothing in this fixture uses, so the gate
        # opens without turning any fixture address internal.
        cls.env['pan.mail.internal.domains'].set_domains(['gate-fixture.test'])
        cls.Log = cls.env['pan.mail.routing.log']
        cls.mailbox = cls.env['pan.mail.mailbox'].create({
            'email': 'support@company.test', 'mailbox_type': 'shared',
        })
        cls.partner = cls.env['res.partner'].create({
            'name': 'Customer', 'email': 'customer@example.com',
        })
        cls.lead = cls.env['crm.lead'].create({'name': 'Existing opportunity'})

    def _match(self, **overrides):
        match = {
            'model': False, 'res_id': False, 'parent_message_id': False,
            'rule': False, 'confidence': 0.0, 'reason': 'nothing matched',
            'thread_id': False, 'candidates': [],
        }
        match.update(overrides)
        return match

    def _log(self, outcome, target=None, **match_overrides):
        return self.Log.log_decision(
            mailbox=self.mailbox,
            match=self._match(**match_overrides),
            outcome=outcome,
            target_record=target,
            subject='Question about my order',
            email_from='customer@example.com',
            internet_message_id='<inbound@example.com>',
        )

    # ------------------------------------------------------------------ #
    # Writing
    # ------------------------------------------------------------------ #

    def test_a_routed_mail_records_where_and_why(self):
        log = self._log(
            'threaded', target=self.lead,
            model='crm.lead', res_id=self.lead.id,
            rule='references', confidence=1.0, reason='Replies to <p@x>',
        )

        self.assertEqual(log.outcome, 'threaded')
        self.assertEqual(log.model, 'crm.lead')
        self.assertEqual(log.res_id, self.lead.id)
        self.assertEqual(log.rule, 'references')
        self.assertEqual(log.reason, 'Replies to <p@x>')
        # Stored, not computed: the log must stay readable after the target is
        # renamed or deleted.
        self.assertEqual(log.target_name, self.lead.display_name)

    def test_candidates_are_written_out_readably(self):
        log = self._log('created', target=self.lead, candidates=[
            {'model': 'crm.lead', 'res_id': self.lead.id,
             'rule': 'subject_participants', 'confidence': 0.5,
             'reason': 'Same subject from Customer'},
        ])

        self.assertEqual(log.candidate_count, 1)
        self.assertIn('crm.lead/%s' % self.lead.id, log.candidates)
        self.assertIn('subject_participants', log.candidates)
        self.assertIn('0.50', log.candidates)

    # ------------------------------------------------------------------ #
    # needs_review — the whole point
    # ------------------------------------------------------------------ #

    def test_a_fallback_needs_review(self):
        """Contact chatter with no match is delivered but effectively invisible."""
        self.assertTrue(self._log('fallback', target=self.partner).needs_review)

    def test_a_new_record_with_candidates_needs_review(self):
        """The expensive, silent mistake: a duplicate ticket.

        We created something new while the ladder had candidates it could not
        act on — so this may be a second record for a conversation that was
        already running, and nothing else in Odoo would ever say so.
        """
        log = self._log('created', target=self.lead, candidates=[
            {'model': 'crm.lead', 'res_id': self.lead.id,
             'rule': 'subject_participants', 'confidence': 0.5, 'reason': 'x'},
        ])

        self.assertTrue(log.needs_review)

    def test_a_clean_new_record_does_not_need_review(self):
        self.assertFalse(self._log('created', target=self.lead).needs_review)

    def test_a_threaded_mail_does_not_need_review(self):
        log = self._log('threaded', target=self.lead, rule='references',
                        confidence=1.0, candidates=[
                            {'model': 'crm.lead', 'res_id': self.lead.id,
                             'rule': 'references', 'confidence': 1.0, 'reason': 'x'},
                        ])

        self.assertFalse(log.needs_review)

    def test_sent_items_do_not_flood_the_queue(self):
        """Our own outgoing mail lands on contact chatter by design."""
        self.assertFalse(self._log('sent_item', target=self.partner).needs_review)

    def test_marking_reviewed_clears_it_from_the_queue(self):
        log = self._log('fallback', target=self.partner)
        log.action_mark_reviewed()

        self.assertTrue(log.reviewed)
        self.assertFalse(self.Log.search([
            ('id', '=', log.id),
            ('needs_review', '=', True), ('reviewed', '=', False),
        ]))

    # ------------------------------------------------------------------ #
    # Navigation
    # ------------------------------------------------------------------ #

    def test_open_target_returns_an_action_for_the_record(self):
        log = self._log('threaded', target=self.lead)
        action = log.action_open_target()

        self.assertEqual(action['res_model'], 'crm.lead')
        self.assertEqual(action['res_id'], self.lead.id)

    def test_open_target_is_harmless_when_the_record_is_gone(self):
        log = self._log('fallback', target=self.partner)
        log.model = False

        self.assertFalse(log.action_open_target())

    # ------------------------------------------------------------------ #
    # Housekeeping
    # ------------------------------------------------------------------ #

    def test_gc_removes_old_rows_but_keeps_anything_awaiting_a_human(self):
        """A row per mail on a one-minute cron adds up; a flagged row is a task.

        Deleting rows somebody was asked to look at would quietly empty the
        queue, which is worse than keeping too much.
        """
        old = fields.Datetime.subtract(fields.Datetime.now(), days=200)

        stale_routed = self._log('threaded', target=self.lead)
        stale_flagged = self._log('fallback', target=self.partner)
        stale_reviewed = self._log('fallback', target=self.partner)
        stale_reviewed.reviewed = True
        recent_routed = self._log('threaded', target=self.lead)
        (stale_routed | stale_flagged | stale_reviewed).write({'date': old})

        self.Log._gc_routing_logs()

        self.assertFalse(stale_routed.exists(), "old routed rows should go")
        self.assertFalse(stale_reviewed.exists(), "old reviewed rows should go")
        self.assertTrue(stale_flagged.exists(), "flagged rows survive age")
        self.assertTrue(recent_routed.exists(), "recent rows survive")

    def test_retention_is_configurable(self):
        self.env['ir.config_parameter'].sudo().set_param(
            'pan_mail_pro.routing_log_retention_days', '7')
        self.assertEqual(self.Log._retention_days(), 7)

        # Nonsense values fall back to the default rather than deleting
        # everything or nothing.
        self.env['ir.config_parameter'].sudo().set_param(
            'pan_mail_pro.routing_log_retention_days', 'not-a-number')
        self.assertEqual(self.Log._retention_days(), 90)
