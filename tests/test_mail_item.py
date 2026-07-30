# -*- coding: utf-8 -*-
"""The triage queue.

The point of this model is that mail stops disappearing silently. So the tests
that matter most are the negative ones: what must *not* end up here, and what
must survive a failure that rolls its own transaction back.
"""
from unittest.mock import patch

from odoo import fields
from odoo.tests import tagged

from .common import OutlookProTestCase
from .test_incoming_sync import TestIncomingSync


@tagged('pan_mail_pro', 'post_install', '-at_install')
class TestMailItemRecording(TestIncomingSync):
    """Driven through the real fetcher, reusing the Graph fakes."""

    def _items(self):
        return self.env['pan.mail.item'].sudo().search([('mailbox_id', '=', self.mailbox.id)])

    def test_unknown_sender_under_known_partners_is_queued(self):
        self.mailbox.x_sync_mode = 'known_partners'
        self.external_partner.unlink()

        self._sync()

        item = self._items()
        self.assertEqual(len(item), 1)
        self.assertEqual(item.reason, 'unknown_contact')
        self.assertEqual(item.state, 'pending')
        self.assertEqual(item.email_from, 'customer@example.com')

    def test_queue_stores_no_body(self):
        """The provider stays the source of truth for content."""
        self.mailbox.x_sync_mode = 'known_partners'
        self.external_partner.unlink()
        self._sync()

        item = self._items()
        self.assertFalse(
            [f for f in item._fields if 'body' in f],
            "pan.mail.item must not grow a body field",
        )

    def test_blocked_contact_is_not_queued(self):
        """A block is an objection to processing. Recording it anyway, even as
        metadata, inverts what the flag means."""
        self.external_partner.x_email_sync_blocked = True

        self._sync()

        self.assertFalse(self._items(), "blocked contacts must leave no trace")

    def test_internal_user_sender_is_not_queued(self):
        """Employee-to-employee mail has no document context and no ACL."""
        self.external_partner.write({'email': 'sales@test.local'})
        self._sync()

        self.assertFalse(
            self._items().filtered(lambda i: i.reason != 'error'),
            "mail from internal users must not be recorded",
        )

    def test_duplicate_is_not_queued(self):
        """The cron refetches the same window constantly; a duplicate is not
        mail that landed nowhere, and queueing it would add a row a minute."""
        self._sync()
        self._sync()

        self.assertFalse(self._items())

    def test_successfully_routed_mail_is_not_queued(self):
        self._sync()

        self.assertFalse(self._items())
        self.assertTrue(self._messages_on(self.external_partner))

    def test_failure_is_recorded_and_survives_the_rollback(self):
        """The error item is written after the savepoint rolls back.

        Written inside it, the record would be undone by the very failure it
        exists to report — which is how this kind of bookkeeping usually fails.
        """
        original = type(self.env['microsoft.incoming.mail.processor'])._process_message

        def boom(self_processor, mailbox, message, folder):
            raise ValueError('synthetic failure')

        with patch.object(
            type(self.env['microsoft.incoming.mail.processor']),
            '_process_message', boom,
        ):
            self._sync()

        item = self._items()
        self.assertEqual(len(item), 1)
        self.assertEqual(item.reason, 'error')
        self.assertIn('synthetic failure', item.reason_detail)
        self.assertTrue(original, "sanity: the original method still exists")


@tagged('pan_mail_pro', 'post_install', '-at_install')
class TestMailItemLifecycle(OutlookProTestCase):

    def _item(self, **overrides):
        vals = {
            'provider_message_id': 'PROVIDER_1',
            'message_id': '<queued-1@example.com>',
            'mailbox_id': self.personal_mailbox.id,
            'reason': 'unknown_contact',
            'subject': 'Held',
        }
        vals.update(overrides)
        return self.env['pan.mail.item'].sudo().create(vals)

    def test_expiry_is_set_on_create(self):
        self.assertTrue(self._item().expiry_date)

    def test_retention_is_capped(self):
        """A configurable retention that can be set to 'forever' is not a
        retention policy."""
        self.env['ir.config_parameter'].sudo().set_param(
            'pan_mail_pro.item_retention_days', '9999')
        item = self._item(message_id='<queued-2@example.com>')
        self.assertLess(
            (item.expiry_date - fields.Datetime.now()).days, 91,
            "retention must stay within the hard maximum",
        )

    def test_gc_removes_expired_items_whatever_their_state(self):
        pending = self._item(message_id='<gc-1@example.com>')
        ignored = self._item(message_id='<gc-2@example.com>', state='ignored')
        (pending | ignored).write({'expiry_date': '2020-01-01 00:00:00'})

        self.env['pan.mail.item']._gc_items()

        self.assertFalse(pending.exists())
        self.assertFalse(ignored.exists())

    def test_gc_leaves_live_items_alone(self):
        item = self._item(message_id='<gc-3@example.com>')
        self.env['pan.mail.item']._gc_items()
        self.assertTrue(item.exists())

    def test_ignore_only_affects_pending(self):
        imported = self._item(message_id='<done@example.com>', state='imported')
        imported.action_ignore()
        self.assertEqual(imported.state, 'imported')

    def test_same_message_is_recorded_once_per_mailbox(self):
        message = {
            'provider_message_id': 'P1',
            'message_id': '<dup@example.com>',
            'from': {'email': 'customer@example.com'},
            'to': [],
            'subject': 'Dup',
            'date': False,
        }
        Item = self.env['pan.mail.item']
        first = Item._record_skip(self.personal_mailbox, message, 'inbox', 'unknown_contact')
        second = Item._record_skip(self.personal_mailbox, message, 'inbox', 'unknown_contact')
        self.assertEqual(first, second)

    def test_recording_never_raises(self):
        """A triage failure must never cost a mail."""
        result = self.env['pan.mail.item']._record_skip(
            self.personal_mailbox, {'provider_message_id': None}, 'inbox', 'not_a_reason',
        )
        self.assertFalse(result)
