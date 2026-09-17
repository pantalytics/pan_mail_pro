# -*- coding: utf-8 -*-
"""The read side of the conversation view.

The screen stores nothing, so there is no state to test. What there is to test
is the thing that goes wrong quietly in a layer like this: somebody reads a
message on a record they are not allowed to open. `test_access_is_the_orm_s`
is the reason this file exists; the rest guards the shapes the client depends
on, because a missing key here is a blank pane in the browser and an empty
server log.
"""
from odoo.tests import TransactionCase, tagged


@tagged('pan_mail_pro', 'post_install', '-at_install')
class TestConversationApi(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        # A mailbox cannot be created before the internal domains are set:
        # without them the module cannot tell a colleague from a customer.
        cls.env['pan.mail.domain'].set_domains(['company.test'])
        cls.Conversation = cls.env['pan.mail.conversation']
        cls.customer = cls.env['res.partner'].create({
            'name': 'Vandermolen Techniek',
            'email': 'bart@vandermolen.test',
        })
        cls.mailbox = cls.env['pan.mail.mailbox'].create({
            'email': 'sales@company.test',
            'provider': 'imap',
            'mailbox_type': 'shared',
        })
        cls.lead = cls.env['crm.lead'].create({
            'name': 'Asafdichtingen',
            'partner_id': cls.customer.id,
        })

    def _mail(self, direction='incoming', subject='Offerte', record=None,
              body='<p>Kunnen jullie de levertijd bevestigen?</p>'):
        record = record if record is not None else self.lead
        return self.env['mail.message'].create({
            'model': record._name,
            'res_id': record.id,
            'message_type': 'email',
            'subject': subject,
            'body': body,
            'author_id': self.customer.id,
            'email_from': self.customer.email,
            'x_direction': direction,
            'x_mailbox_id': self.mailbox.id,
        })

    # ------------------------------------------------------------------ shape

    def test_empty_mailbox_returns_empty(self):
        """An empty result, never an exception: a new database opens the
        screen before a single mail has arrived."""
        rows = self.Conversation.search_conversations(mailbox_id=self.mailbox.id)
        self.assertEqual(rows, [])

    def test_conversation_row_has_what_the_client_draws(self):
        self._mail()
        row = self.Conversation.search_conversations(mailbox_id=self.mailbox.id)[0]
        for key in ('model', 'res_id', 'subject', 'preview', 'correspondent',
                    'date', 'count', 'record_name', 'unread', 'waiting_on_us'):
            self.assertIn(key, row, f'the list draws {key}')
        self.assertEqual(row['subject'], 'Offerte')
        self.assertEqual(row['model'], 'crm.lead')

    def test_preview_is_text_not_markup(self):
        """The list shows one line of the body. Tags in it would render as
        literal angle brackets in a text node."""
        self._mail(body='<p>Hallo <b>Bart</b>,<br/>dank &amp; groet</p>')
        row = self.Conversation.search_conversations(mailbox_id=self.mailbox.id)[0]
        self.assertNotIn('<', row['preview'])
        self.assertIn('dank & groet', row['preview'])

    def test_folder_counts_cover_every_folder(self):
        self._mail()
        counts = self.Conversation.folder_counts(mailbox_id=self.mailbox.id)
        self.assertEqual(
            [row['id'] for row in counts],
            ['inbox', 'needs_reply', 'waiting', 'sent',
             'unfiled_contact', 'unfiled_none'],
        )
        by_id = {row['id']: row['count'] for row in counts}
        self.assertEqual(by_id['inbox'], 1)
        self.assertEqual(by_id['needs_reply'], 1)
        self.assertEqual(by_id['waiting'], 0)

    def test_read_conversation_returns_its_messages(self):
        self._mail(subject='First')
        self._mail(subject='Second')
        thread = self.Conversation.read_conversation('crm.lead', self.lead.id)
        self.assertEqual(len(thread['messages']), 2)
        self.assertIn('records', thread)
        self.assertIn('rejected', thread)

    # ---------------------------------------------------------------- derived

    def test_waiting_on_us_follows_the_last_message(self):
        """Derived, so it cannot go stale. The moment we answer, the
        conversation stops waiting on us, in the same transaction."""
        self._mail(direction='incoming')
        row = self.Conversation.search_conversations(mailbox_id=self.mailbox.id)[0]
        self.assertTrue(row['waiting_on_us'])

        self._mail(direction='outgoing', subject='Re: Offerte')
        row = self.Conversation.search_conversations(mailbox_id=self.mailbox.id)[0]
        self.assertFalse(row['waiting_on_us'])

    def test_notes_stay_out_of_a_screen_about_mail(self):
        self.env['mail.message'].create({
            'model': 'crm.lead',
            'res_id': self.lead.id,
            'message_type': 'comment',
            'body': '<p>Marge op regel 3 is krap</p>',
        })
        self.assertEqual(
            self.Conversation.search_conversations(mailbox_id=self.mailbox.id), [])

    # ----------------------------------------------------------------- access

    def test_access_is_the_orm_s(self):
        """The test this layer exists to pass.

        A user who may open the inbox but not this record sees no message, no
        conversation and no thread key. Nothing here calls `sudo()`, so the
        rule is Odoo's own and stays right when the record's rules change.
        """
        self._mail()
        outsider = self.env['res.users'].create({
            'name': 'Sam Outsider',
            'login': 'sam@company.test',
            'email': 'sam@company.test',
            'group_ids': [(6, 0, [self.env.ref('base.group_user').id])],
        })
        # No sales groups, so the lead is not theirs to read.
        as_outsider = self.Conversation.with_user(outsider)
        self.assertEqual(
            as_outsider.search_conversations(mailbox_id=self.mailbox.id), [])
        thread = as_outsider.read_conversation('crm.lead', self.lead.id)
        self.assertEqual(thread['messages'], [])

    # ------------------------------------------------------------------ doors

    def test_record_conversations_says_nothing_when_there_is_nothing(self):
        """Door 1 draws no line at all on a record without mail."""
        empty = self.Conversation.record_conversations('crm.lead', self.lead.id)
        self.assertEqual(empty['here'], 0)
        self.assertEqual(empty['elsewhere'], 0)

    def test_record_conversations_counts_what_is_here(self):
        self._mail()
        self._mail(subject='Re: Offerte', direction='outgoing')
        door = self.Conversation.record_conversations('crm.lead', self.lead.id)
        self.assertEqual(door['here'], 2)
        # Everything is on this record, so the client draws no extra line.
        self.assertEqual(door['elsewhere'], 0)

    def test_customer_timeline_merges_and_orders(self):
        self._mail(subject='Oldest')
        self._mail(subject='Newest')
        timeline = self.Conversation.customer_timeline(self.customer.id)
        self.assertIn('next', timeline)
        self.assertTrue(timeline['items'])
        dates = [item['date'] for item in timeline['items']]
        self.assertEqual(dates, sorted(dates, reverse=True), 'newest first')

    def test_customer_timeline_honours_kinds(self):
        self._mail()
        only_activities = self.Conversation.customer_timeline(
            self.customer.id, kinds=['activity'])
        self.assertEqual(only_activities['items'], [])
