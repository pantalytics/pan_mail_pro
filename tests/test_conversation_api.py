# -*- coding: utf-8 -*-
"""The read side of the conversation view.

The screen stores nothing, so there is no state to test. What there is to test
is the thing that goes wrong quietly in a layer like this: somebody reads a
message on a record they are not allowed to open. `test_access_is_the_orm_s`
is the reason this file exists; the rest guards the shapes the client depends
on, because a missing key here is a blank pane in the browser and an empty
server log.
"""
from odoo.exceptions import AccessError
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
        cls.manager_group = cls.env.ref('pan_mail_pro.group_mail_mailbox_manager')

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

    def test_preview_stops_where_the_quote_starts(self):
        """A one-line answer on top of the quoted thread previews as the
        answer. With the quote in it, the list reads as if the customer wrote
        what we wrote last week."""
        self._mail(body='<p>Prima, akkoord.</p>'
                        '<blockquote>Op 12 sep schreef sales@company.test: '
                        'de levertijd is drie weken</blockquote>')
        row = self.Conversation.search_conversations(mailbox_id=self.mailbox.id)[0]
        self.assertEqual(row['preview'], 'Prima, akkoord.')
        # Outlook marks the history with a div, not a blockquote.
        self._mail(body='<p>Dank!</p><div id="divRplyFwdMsg">From: sales</div>'
                        '<div>de levertijd is drie weken</div>')
        row = self.Conversation.search_conversations(mailbox_id=self.mailbox.id)[0]
        self.assertEqual(row['preview'], 'Dank!')

    def test_folder_counts_cover_every_folder(self):
        self._mail()
        counts = self.Conversation.folder_counts(mailbox_id=self.mailbox.id)
        self.assertEqual(
            [row['id'] for row in counts],
            ['inbox', 'needs_reply', 'waiting',
             'unfiled_contact', 'unfiled_none'],
            'no Sent folder: it was the same query as Waiting on customer',
        )
        by_id = {row['id']: row['count'] for row in counts}
        self.assertEqual(by_id['inbox'], 1)
        self.assertEqual(by_id['needs_reply'], 1)
        self.assertEqual(by_id['waiting'], 0)

    def test_an_answered_conversation_leaves_needs_reply(self):
        """The folder is about the newest message, not about any message.

        Filtering the messages instead of the conversation put one thread in
        Needs reply and Waiting on customer at the same time, and left it in
        Needs reply forever after it was answered.
        """
        self._mail(direction='incoming')
        self._mail(direction='outgoing', subject='Re: Offerte')

        needs = self.Conversation.search_conversations(
            mailbox_id=self.mailbox.id, folder='needs_reply')
        waiting = self.Conversation.search_conversations(
            mailbox_id=self.mailbox.id, folder='waiting')
        self.assertEqual(needs, [])
        self.assertEqual(len(waiting), 1)

        counts = {row['id']: row['count']
                  for row in self.Conversation.folder_counts(mailbox_id=self.mailbox.id)}
        self.assertEqual(counts['needs_reply'], 0)
        self.assertEqual(counts['waiting'], 1)

    def test_the_row_describes_the_conversation_not_the_folder(self):
        """In a folder that filters on direction, the subject, the date and
        the message count still belong to the whole thread."""
        self._mail(direction='incoming', subject='First')
        self._mail(direction='incoming', subject='Second')
        self._mail(direction='outgoing', subject='Re: Second')

        row = self.Conversation.search_conversations(
            mailbox_id=self.mailbox.id, folder='waiting')[0]
        self.assertEqual(row['subject'], 'Re: Second')
        self.assertEqual(row['count'], 3, 'three messages, not one outgoing')

    def test_unfiled_mail_is_not_one_conversation(self):
        """Two unmatched mails from two companies are two rows.

        Grouping on (model, res_id) collapsed every unfiled message in the
        database into a single row belonging to nobody, in the one folder that
        exists to make those messages reviewable.
        """
        for subject in ('Stranger one', 'Stranger two'):
            self.env['mail.message'].create({
                'model': False,
                'message_type': 'email',
                'subject': subject,
                'body': '<p>Who is this</p>',
                'email_from': 'nobody@elsewhere.test',
                'x_direction': 'incoming',
                'x_mailbox_id': self.mailbox.id,
            })
        rows = self.Conversation.search_conversations(
            mailbox_id=self.mailbox.id, folder='unfiled_none')
        self.assertEqual(len(rows), 2)
        self.assertEqual({row['subject'] for row in rows},
                         {'Stranger one', 'Stranger two'})

        # And each one opens on its own message rather than on all of them.
        thread = self.Conversation.read_conversation(
            False, 0, message_id=rows[0]['message_id'])
        self.assertEqual(len(thread['messages']), 1)

    def test_pagination_does_not_repeat_or_lose_a_conversation(self):
        leads = [self.lead] + [self.env['crm.lead'].create({
            'name': f'Lead {index}', 'partner_id': self.customer.id,
        }) for index in range(2)]
        for lead in leads:
            self._mail(record=lead)

        first = self.Conversation.search_conversations(
            mailbox_id=self.mailbox.id, limit=2, offset=0)
        second = self.Conversation.search_conversations(
            mailbox_id=self.mailbox.id, limit=2, offset=2)
        keys = [(row['model'], row['res_id']) for row in first + second]
        self.assertEqual(len(keys), 3)
        self.assertEqual(len(set(keys)), 3, 'no conversation on two pages')

    def test_a_search_narrows_the_list_and_the_rail_together(self):
        self._mail(subject='Offerte asafdichtingen')
        self._mail(record=self.env['crm.lead'].create({
            'name': 'Other', 'partner_id': self.customer.id,
        }), subject='Storing pomp')

        rows = self.Conversation.search_conversations(
            mailbox_id=self.mailbox.id, search='asafdicht')
        self.assertEqual(len(rows), 1)
        counts = {row['id']: row['count'] for row in self.Conversation.folder_counts(
            mailbox_id=self.mailbox.id, search='asafdicht')}
        self.assertEqual(counts['inbox'], 1,
                         'the rail describes the same mail as the list')

    def test_the_mailbox_filter_filters(self):
        other = self.env['pan.mail.mailbox'].create({
            'email': 'support@company.test',
            'provider': 'imap',
            'mailbox_type': 'shared',
        })
        self._mail()
        message = self._mail(subject='Via support')
        message.x_mailbox_id = other

        mine = self.Conversation.search_conversations(mailbox_id=self.mailbox.id)
        theirs = self.Conversation.search_conversations(mailbox_id=other.id)
        self.assertEqual(len(mine), 1)
        self.assertEqual(len(theirs), 1)
        self.assertEqual(theirs[0]['subject'], 'Via support')

    def test_unread_is_odoo_s_own_needaction_row(self):
        message = self._mail()
        row = self.Conversation.search_conversations(mailbox_id=self.mailbox.id)[0]
        self.assertFalse(row['unread'])

        self.env['mail.notification'].create({
            'mail_message_id': message.id,
            'res_partner_id': self.env.user.partner_id.id,
            'notification_type': 'inbox',
            'is_read': False,
        })
        row = self.Conversation.search_conversations(mailbox_id=self.mailbox.id)[0]
        self.assertTrue(row['unread'])

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

    def _mailbox_manager(self, login):
        """Someone the screen is for: reads a mailbox, not the CRM.

        This is the user the layer has to be right about. A plain internal
        user sees nothing because Odoo hides the messages from them, which
        would let a layer that shows nobody anything pass this file.
        """
        return self.env['res.users'].create({
            'name': login,
            'login': login,
            'email': login,
            'group_ids': [(6, 0, [self.env.ref('base.group_user').id,
                                  self.manager_group.id])],
        })

    def test_access_is_the_orm_s(self):
        """The test this layer exists to pass.

        A mailbox manager with no CRM access sees the mail on a contact they
        may read and nothing at all of the lead: no message, no conversation,
        no record chip. Nothing here calls `sudo()` to answer a question, so
        the rule is Odoo's own and stays right when the record's rules change.
        """
        self._mail()
        on_contact = self._mail(record=self.customer, subject='Not a lead')
        reader = self._mailbox_manager('sam@company.test')

        as_reader = self.Conversation.with_user(reader)
        rows = as_reader.search_conversations(mailbox_id=self.mailbox.id)
        self.assertEqual([row['subject'] for row in rows], ['Not a lead'],
                         'the positive control: they do see mail they may read')
        self.assertNotIn('crm.lead', [row['model'] for row in rows])

        thread = as_reader.read_conversation('crm.lead', self.lead.id)
        self.assertEqual(thread['messages'], [])
        self.assertEqual(thread['records'], [], 'not even the record name')

        timeline = as_reader.customer_timeline(self.customer.id)
        ids = [item['id'] for item in timeline['items']]
        self.assertIn(on_contact.id, ids)
        self.assertNotIn(self.lead.message_ids[:1].id, ids,
                         'the lead is not theirs, on the timeline either')

    def test_the_inbox_is_for_people_who_read_a_mailbox(self):
        """A group on a menu is not an access rule, so the methods check too."""
        stranger = self.env['res.users'].create({
            'name': 'Nina Nobody',
            'login': 'nina@company.test',
            'email': 'nina@company.test',
            'group_ids': [(6, 0, [self.env.ref('base.group_user').id])],
        })
        with self.assertRaises(AccessError):
            self.Conversation.with_user(stranger).search_conversations()

    def test_a_page_is_a_page(self):
        """`limit` arrives over RPC, and the queries under it are not free."""
        for index in range(3):
            self._mail(record=self.env['crm.lead'].create({
                'name': f'Lead {index}', 'partner_id': self.customer.id,
            }))
        rows = self.Conversation.search_conversations(
            mailbox_id=self.mailbox.id, limit=10 ** 9)
        self.assertLessEqual(len(rows), 200)

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
        self.assertEqual(door['partner_id'], self.customer.id,
                         'the customer, not whoever happened to reply last')

    def test_a_thread_that_crossed_two_records_says_so(self):
        """The chip row and the door's count both come from the index.

        This is the one place that reads a table the ORM does not fence, so it
        is the one place worth a test of its own.

        The two links sit in two mailboxes on purpose: `pan.mail.thread.link`
        is unique on (provider, mailbox, thread), so one mailbox maps a thread
        to exactly one record. A thread reaching two records is the case where
        sales@ and support@ both saw it, and the key that may cross a mailbox
        is the `references` root, never the provider's own handle.
        """
        other = self.env['crm.lead'].create({
            'name': 'Same thread, other record', 'partner_id': self.customer.id,
        })
        second_mailbox = self.env['pan.mail.mailbox'].create({
            'email': 'help@company.test',
            'provider': 'imap',
            'mailbox_type': 'shared',
        })
        here = self._mail()
        there = self._mail(record=other, subject='Re: Offerte')
        for message, record, mailbox in ((here, self.lead, self.mailbox),
                                         (there, other, second_mailbox)):
            self.env['pan.mail.thread.link'].create({
                'provider': 'imap',
                'mailbox_id': mailbox.id,
                'thread_id': '<root-shared@vandermolen.test>',
                'key_type': 'rfc',
                'model': record._name,
                'res_id': record.id,
                'last_message_id': message.id,
            })

        thread = self.Conversation.read_conversation('crm.lead', self.lead.id)
        self.assertEqual(len(thread['records']), 2, 'both records are chips')

        door = self.Conversation.record_conversations('crm.lead', self.lead.id)
        self.assertEqual(door['elsewhere'], 1, 'one message sits elsewhere')

    def test_the_chips_a_reader_may_not_open_are_not_there(self):
        other = self.env['crm.lead'].create({
            'name': 'Hidden', 'partner_id': self.customer.id,
        })
        second_mailbox = self.env['pan.mail.mailbox'].create({
            'email': 'hidden@company.test',
            'provider': 'imap',
            'mailbox_type': 'shared',
        })
        on_contact = self._mail(record=self.customer)
        hidden = self._mail(record=other)
        for message, record, mailbox in ((on_contact, self.customer, self.mailbox),
                                         (hidden, other, second_mailbox)):
            self.env['pan.mail.thread.link'].create({
                'provider': 'imap',
                'mailbox_id': mailbox.id,
                'thread_id': '<root-mixed@vandermolen.test>',
                'key_type': 'rfc',
                'model': record._name,
                'res_id': record.id,
                'last_message_id': message.id,
            })

        reader = self._mailbox_manager('rita@company.test')
        thread = self.Conversation.with_user(reader).read_conversation(
            'res.partner', self.customer.id)
        self.assertEqual([row['model'] for row in thread['records']],
                         ['res.partner'])

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

    def test_next_is_this_customer_s_activities_only(self):
        """Not every activity assigned to me, which is what an OR gave."""
        stranger = self.env['res.partner'].create({'name': 'Somebody else'})
        self.env['mail.activity'].create({
            'res_model_id': self.env['ir.model']._get('res.partner').id,
            'res_id': self.customer.id,
            'activity_type_id': self.env.ref('mail.mail_activity_data_call').id,
            'summary': 'Call Bart about line 3',
            'user_id': self.env.user.id,
        })
        self.env['mail.activity'].create({
            'res_model_id': self.env['ir.model']._get('res.partner').id,
            'res_id': stranger.id,
            'activity_type_id': self.env.ref('mail.mail_activity_data_call').id,
            'summary': 'Somebody else entirely',
            'user_id': self.env.user.id,
        })
        timeline = self.Conversation.customer_timeline(self.customer.id)
        self.assertEqual([row['summary'] for row in timeline['next']],
                         ['Call Bart about line 3'])
