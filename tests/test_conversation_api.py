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

    def test_the_rail_holds_folders_and_the_list_holds_filters(self):
        """The rail is the shape every mail client has, and nothing else.

        Our own states -- needs reply, the two unlinked ones -- read as a
        filter over a list, not as places mail sits, so they come back
        separately and only for the folder somebody has open.
        """
        self._mail()
        counts = self.Conversation.folder_counts(
            mailbox_id=self.mailbox.id, folder='inbox')
        self.assertEqual([row['id'] for row in counts['folders']],
                         ['inbox', 'sent'])
        self.assertEqual([row['id'] for row in counts['filters']],
                         ['needs_reply', 'unlinked_contact', 'unlinked_none'])
        by_id = {row['id']: row['count']
                 for row in counts['folders'] + counts['filters']}
        self.assertEqual(by_id['inbox'], 1)
        self.assertEqual(by_id['sent'], 0)
        self.assertEqual(by_id['needs_reply'], 1)

    def test_a_folded_mailbox_is_not_asked_for_filter_counts(self):
        """The filter row belongs to one list, so it costs one mailbox."""
        self._mail()
        counts = self.Conversation.folder_counts(mailbox_id=self.mailbox.id)
        self.assertEqual(counts['filters'], [])
        self.assertEqual(len(counts['folders']), 2)

    def test_sent_is_every_thread_written_in_not_the_last_word(self):
        """A customer answering does not take a thread out of Sent."""
        self._mail(direction='outgoing', subject='Offerte')
        self._mail(direction='incoming', subject='Re: Offerte')

        rows = self.Conversation.search_conversations(
            mailbox_id=self.mailbox.id, folder='sent')
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['subject'], 'Re: Offerte',
                         'the row is the conversation, not the mail we sent')

        # And the filter still asks its own question inside that folder.
        needs = self.Conversation.search_conversations(
            mailbox_id=self.mailbox.id, folder='sent', filter_name='needs_reply')
        self.assertEqual(len(needs), 1)

    def test_an_answered_conversation_leaves_needs_reply(self):
        """The filter is about the newest message, not about any message.

        Filtering the messages instead of the conversation left a thread in
        Needs reply forever after it was answered.
        """
        self._mail(direction='incoming')
        self._mail(direction='outgoing', subject='Re: Offerte')

        needs = self.Conversation.search_conversations(
            mailbox_id=self.mailbox.id, filter_name='needs_reply')
        self.assertEqual(needs, [])

        counts = {row['id']: row['count'] for row in self.Conversation.folder_counts(
            mailbox_id=self.mailbox.id, folder='inbox')['filters']}
        self.assertEqual(counts['needs_reply'], 0)

    def test_the_row_describes_the_conversation_not_the_filter(self):
        """Under a filter on direction, the subject, the date and the message
        count still belong to the whole thread."""
        self._mail(direction='outgoing', subject='First')
        self._mail(direction='outgoing', subject='Second')
        self._mail(direction='incoming', subject='Re: Second')

        row = self.Conversation.search_conversations(
            mailbox_id=self.mailbox.id, filter_name='needs_reply')[0]
        self.assertEqual(row['subject'], 'Re: Second')
        self.assertEqual(row['count'], 3, 'three messages, not one incoming')

    def test_unlinked_mail_is_not_one_conversation(self):
        """Two unmatched mails from two companies are two rows.

        Grouping on (model, res_id) collapsed every unlinked message in the
        database into a single row belonging to nobody, under the one filter
        that exists to make those messages reviewable. The rail counts them
        the same way, one per message.
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
            mailbox_id=self.mailbox.id, filter_name='unlinked_none')
        self.assertEqual(len(rows), 2)
        self.assertEqual({row['subject'] for row in rows},
                         {'Stranger one', 'Stranger two'})
        counts = {row['id']: row['count'] for row in self.Conversation.folder_counts(
            mailbox_id=self.mailbox.id, folder='inbox')['filters']}
        self.assertEqual(counts['unlinked_none'], 2,
                         'the number says how many mails there are to review')

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
            mailbox_id=self.mailbox.id, search='asafdicht')['folders']}
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

    # ------------------------------------------------------------- the tabs

    def _note(self, body='<p>Marge op regel 3 is krap</p>'):
        return self.env['mail.message'].create({
            'model': 'crm.lead',
            'res_id': self.lead.id,
            'message_type': 'comment',
            'body': body,
        })

    def test_the_mail_tab_is_the_correspondence_and_nothing_else(self):
        self._mail()
        self._note()
        thread = self.Conversation.read_conversation('crm.lead', self.lead.id)
        self.assertEqual([m['kind'] for m in thread['messages']], ['mail'])

    def test_everything_brings_the_notes_back(self):
        """The record pane lost its chatter, so this tab is where the notes
        went. If it cannot show them they exist nowhere on this screen.

        The lead logs its own creation, so this also pins the third kind: a
        record event, which the tab draws as one line rather than a card.
        """
        self._mail()
        self._note()
        kinds = set(m['kind'] for m in self.Conversation.read_conversation(
            'crm.lead', self.lead.id, scope='all')['messages'])
        self.assertEqual(kinds, {'mail', 'note', 'event'})

    def test_a_note_in_another_mailbox_s_conversation_is_still_a_note(self):
        """A note carries no mailbox, so running it through the mailbox
        filter would empty the tab that exists to show it."""
        self._mail()
        self._note()
        thread = self.Conversation.read_conversation(
            'crm.lead', self.lead.id, mailbox_id=self.mailbox.id, scope='all')
        self.assertIn('note', [m['kind'] for m in thread['messages']])
        self.assertIn('mail', [m['kind'] for m in thread['messages']])

    def test_an_outgoing_reply_is_mail_even_when_odoo_calls_it_a_comment(self):
        """The chatter posts a `comment`. One that went out over the wire is
        correspondence, and filing it under "internal note" is the mistake on
        this screen a customer eventually reads about."""
        note = self._note(body='<p>Bevestigd, drie weken.</p>')
        note.write({'x_direction': 'outgoing', 'x_mailbox_id': self.mailbox.id})
        thread = self.Conversation.read_conversation(
            'crm.lead', self.lead.id, scope='all')
        kinds = {m['id']: m['kind'] for m in thread['messages']}
        self.assertEqual(kinds[note.id], 'mail')
        self.assertNotIn('note', kinds.values())

    def test_files_and_activities_ride_along_with_every_tab(self):
        """The counts are drawn on the strip itself, so they have to be the
        same whichever tab is open. A number that moves when you click another
        tab reads as a bug."""
        attachment = self.env['ir.attachment'].create({
            'name': 'offerte.pdf',
            'datas': b'JVBERi0=',
        })
        self._mail().write({'attachment_ids': [(6, 0, attachment.ids)]})
        self.env['mail.activity'].create({
            # `res_model` is related to `res_model_id` and writing it alone
            # leaves the column NULL, which the model's own check constraint
            # refuses.
            'res_model_id': self.env['ir.model']._get_id('crm.lead'),
            'res_id': self.lead.id,
            'activity_type_id': self.env.ref('mail.mail_activity_data_todo').id,
            'summary': 'Levertijd navragen',
            'user_id': self.env.user.id,
        })
        for scope in ('mail', 'all'):
            thread = self.Conversation.read_conversation(
                'crm.lead', self.lead.id, scope=scope)
            self.assertEqual(thread['files']['ids'], attachment.ids, scope)
            # The rows are Odoo's own attachment store format, which is what
            # the tab's `AttachmentList` reads.
            self.assertEqual(
                [f['name'] for f in thread['files']['store']['ir.attachment']],
                ['offerte.pdf'], scope)
            self.assertEqual([a['summary'] for a in thread['activities']],
                             ['Levertijd navragen'], scope)

    def test_a_file_on_the_record_and_not_on_a_message_is_still_on_the_tab(self):
        """The tab lists what the chatter's file box lists: the record's own
        attachments. Reading only the messages made a file somebody attached
        from this screen disappear on the very next read."""
        self._mail()
        attachment = self.env['ir.attachment'].create({
            'name': 'tekening.pdf',
            'datas': b'JVBERi0=',
            'res_model': 'crm.lead',
            'res_id': self.lead.id,
        })
        thread = self.Conversation.read_conversation('crm.lead', self.lead.id)
        self.assertEqual(thread['files']['ids'], attachment.ids)

    def test_a_conversation_with_neither_says_so_with_empty_lists(self):
        self._mail()
        thread = self.Conversation.read_conversation('crm.lead', self.lead.id)
        self.assertEqual(thread['files'], {'ids': [], 'store': {}})
        self.assertEqual(thread['activities'], [])

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


@tagged('pan_mail_pro', 'post_install', '-at_install')
class TestInlineComposerView(TransactionCase):
    """The composer the Inbox mounts in its own pane instead of a dialog.

    A form view that is not in a dialog never renders the arch's `<footer>`,
    and nothing says so: the pane shows a composer with no paperclip and no
    template selector, and the server log is empty. The inline view moves the
    two controls a reply needs into the body. These assertions are what keeps
    it doing that.
    """

    def _arch(self):
        view = self.env.ref('pan_mail_pro.mail_compose_message_inline_form')
        return self.env['mail.compose.message'].get_view(view.id)['arch']

    def test_the_inline_composer_has_no_footer_left(self):
        arch = self._arch()
        self.assertNotIn('<footer', arch)
        self.assertIn('o_mailpro_composer_tools', arch)
        self.assertIn('mail_composer_attachment_selector', arch)

    def test_the_inline_composer_names_the_pane_s_controller(self):
        """The js_class is what hands the record to the pane's Send button."""
        self.assertIn('pan_mail_inline_composer_form', self._arch())

    def test_the_inline_composer_still_carries_send_from(self):
        """It is a primary view over mail's own, so this module's own
        extension of that form has to come with it -- a reply that cannot
        pick its mailbox sends from the wrong address."""
        self.assertIn('x_send_from_mailbox_id', self._arch())
