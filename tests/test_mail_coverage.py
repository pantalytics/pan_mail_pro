# -*- coding: utf-8 -*-
"""Link coverage.

The ratio this reports is the gate on building a triage queue, so it has to be
right in the one way that matters: it must count what the lens would show, and
the drill-down must land on exactly the rows the number claims.
"""
from odoo.tests import tagged

from .common import OutlookProTestCase


@tagged('pan_mail_pro', 'post_install', '-at_install')
class TestMailCoverage(OutlookProTestCase):

    def _message(self, model=None, res_id=None, direction='incoming'):
        return self.env['mail.message'].sudo().create({
            'message_type': 'email',
            'subject': 'Test',
            'model': model,
            'res_id': res_id,
            'x_direction': direction,
        })

    def setUp(self):
        super().setUp()
        # Two filed on a real document, one on a contact, two filed nowhere.
        self.env['mail.message'].sudo().search([('x_direction', '!=', False)]).unlink()
        self._message('res.users', self.salesperson.id)
        self._message('res.users', self.other_user.id)
        self._message('res.partner', self.external_partner.id)
        self._message()
        self._message()
        self.coverage = self.env['pan.mail.coverage'].create({})

    def test_counts(self):
        self.assertEqual(self.coverage.total_count, 5)
        self.assertEqual(self.coverage.unlinked_count, 2)
        self.assertEqual(self.coverage.linked_count, 3)
        self.assertEqual(self.coverage.contact_only_count, 1)

    def test_ratio(self):
        self.assertAlmostEqual(self.coverage.unlinked_ratio, 40.0, places=4)

    def test_ratio_is_zero_on_an_empty_database(self):
        """No mail is not 100% unfiled; a division by zero here would be a
        crash on every fresh install."""
        self.env['mail.message'].sudo().search([('x_direction', '!=', False)]).unlink()
        empty = self.env['pan.mail.coverage'].create({})
        self.assertEqual(empty.total_count, 0)
        self.assertEqual(empty.unlinked_ratio, 0.0)

    def test_drill_down_matches_the_count(self):
        """The number and the list must not disagree."""
        action = self.coverage.action_view_unlinked()
        found = self.env['mail.message'].sudo().search(action['domain'])
        self.assertEqual(len(found), self.coverage.unlinked_count)

    def test_drill_down_keeps_the_period(self):
        action = self.coverage.action_view_all()
        self.assertIn(('x_direction', '!=', False), action['domain'])
        self.assertTrue(
            any(leaf[0] == 'date' for leaf in action['domain'] if isinstance(leaf, tuple)),
            "the drill-down must carry the period, or it answers a different question",
        )

    def test_notes_and_logs_are_not_counted(self):
        """The lens counts mail Mail Pro carried, not chatter in general."""
        self.env['mail.message'].sudo().create({
            'message_type': 'comment',
            'subject': 'Internal note',
            'model': 'res.partner',
            'res_id': self.external_partner.id,
        })
        self.coverage.invalidate_recordset()
        self.assertEqual(self.coverage.total_count, 5)
