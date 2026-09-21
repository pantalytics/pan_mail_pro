# -*- coding: utf-8 -*-
"""The read side of the conversation view.

The screen stores nothing, so there is no state to test. What there is to test
is the thing that goes wrong quietly in a layer like this: somebody reads a
message on a record they are not allowed to open. `test_access_is_the_orm_s`
is the reason this file exists; the rest guards the shapes the client depends
on, because a missing key here is a blank pane in the browser and an empty
server log.
"""
from unittest.mock import patch

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
                    'date', 'count', 'record_name', 'unread'):
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

        Our own states -- the two unlinked ones -- read as a filter over a
        list, not as places mail sits, so they come back separately and only
        for the folder somebody has open.
        """
        self._mail()
        counts = self.Conversation.folder_counts(
            mailbox_id=self.mailbox.id, folder='inbox')
        self.assertEqual([row['id'] for row in counts['folders']],
                         ['inbox', 'sent'])
        self.assertEqual([row['id'] for row in counts['filters']],
                         ['unread', 'unlinked_contact', 'unlinked_none'])
        by_id = {row['id']: row['count']
                 for row in counts['folders'] + counts['filters']}
        self.assertEqual(by_id['inbox'], 1)
        self.assertEqual(by_id['sent'], 0)

    def test_unread_is_the_mailbox_s_own_read_state(self):
        """The filter and the dot on the list row read the same column.

        `x_is_read` is mirrored from the provider, so a conversation is unread
        here exactly when it is unread in Outlook. Odoo's `needaction` row is
        not consulted: it is per user and answers a different question, and
        the two would disagree the first time a colleague read the mail.
        """
        message = self._mail()
        message.x_is_read = False
        rows = self.Conversation.search_conversations(
            mailbox_id=self.mailbox.id, filter_name='unread')
        self.assertEqual(len(rows), 1)
        self.assertTrue(rows[0]['unread'])
        counts = {row['id']: row['count'] for row in self.Conversation.folder_counts(
            mailbox_id=self.mailbox.id, folder='inbox')['filters']}
        self.assertEqual(counts['unread'], 1)

        message.x_is_read = True
        self.assertFalse(self.Conversation.search_conversations(
            mailbox_id=self.mailbox.id, filter_name='unread'))

    def test_an_odoo_notification_does_not_make_a_conversation_unread(self):
        """The bell and the dot are two facts.

        A mention on a mail the mailbox has read leaves the Inbox alone; it is
        Discuss that still wants something from this reader.
        """
        message = self._mail()
        self.env['mail.notification'].create({
            'mail_message_id': message.id,
            'res_partner_id': self.env.user.partner_id.id,
            'notification_type': 'inbox',
            'is_read': False,
        })
        row = self.Conversation.search_conversations(mailbox_id=self.mailbox.id)[0]
        self.assertFalse(row['unread'])
        self.assertFalse(self.Conversation.search_conversations(
            mailbox_id=self.mailbox.id, filter_name='unread'))

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

    def test_a_list_row_carries_no_state_of_our_own(self):
        """A mail list has read and unread. Everything else we invented.

        "Needs reply" was a state of ours derived from the direction of the
        newest message: a second inbox to keep correct, next to the one the
        same person already triages in Outlook.
        """
        self._mail(direction='incoming')
        row = self.Conversation.search_conversations(
            mailbox_id=self.mailbox.id)[0]
        self.assertNotIn('waiting_on_us', row)
        self.assertIn('unread', row)

    def test_the_row_describes_the_conversation_not_the_page(self):
        """The subject, the date and the message count belong to the whole
        thread, whatever narrowed the list."""
        self._mail(direction='outgoing', subject='First')
        self._mail(direction='outgoing', subject='Second')
        self._mail(direction='incoming', subject='Re: Second')

        row = self.Conversation.search_conversations(
            mailbox_id=self.mailbox.id)[0]
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

    def test_the_row_is_unread_when_the_mailbox_has_not_read_it(self):
        message = self._mail()
        row = self.Conversation.search_conversations(mailbox_id=self.mailbox.id)[0]
        self.assertFalse(row['unread'])

        message.x_is_read = False
        row = self.Conversation.search_conversations(mailbox_id=self.mailbox.id)[0]
        self.assertTrue(row['unread'])

    def test_read_conversation_returns_its_messages(self):
        self._mail(subject='First')
        self._mail(subject='Second')
        thread = self.Conversation.read_conversation('crm.lead', self.lead.id)
        self.assertEqual(len(thread['messages']), 2)
        self.assertIn('records', thread)
        self.assertIn('rejected', thread)

    def test_the_thread_reads_newest_first(self):
        """The message you came for is the newest one, and it is the one the
        pane opens. Below fifty collapsed headers it is an open message
        nobody sees, so the thread is drawn top down."""
        old = self._mail(subject='First')
        new = self._mail(subject='Second')
        old.write({'date': '2026-09-18 08:00:00'})
        new.write({'date': '2026-09-19 08:00:00'})
        thread = self.Conversation.read_conversation('crm.lead', self.lead.id)
        self.assertEqual([m['subject'] for m in thread['messages']],
                         ['Second', 'First'])

    # ------------------------------------------------------- under the chevron

    def test_the_chevron_unfolds_the_mails_of_one_conversation(self):
        """What the list draws under an unfolded row: who, when, one line."""
        self._mail(subject='First')
        self._mail(subject='Second')
        rows = self.Conversation.conversation_messages('crm.lead', self.lead.id)
        self.assertEqual(len(rows), 2)
        for key in ('id', 'author', 'author_id', 'date', 'preview', 'unread'):
            self.assertIn(key, rows[0], f'an unfolded mail draws {key}')

    def test_an_unfolded_mail_carries_no_body(self):
        """A list row needs a snippet, not a mail.

        Twenty unfolded threads with the body on every row is the whole
        mailbox over the wire to draw sixty lines of text, and the body is
        what the conversation pane is for.
        """
        self._mail()
        row = self.Conversation.conversation_messages('crm.lead', self.lead.id)[0]
        self.assertNotIn('body', row)
        self.assertEqual(row['preview'], 'Kunnen jullie de levertijd bevestigen?')

    def test_the_unfolded_mails_read_newest_first(self):
        """The same order as the row above them and as the pane they open."""
        old = self._mail(subject='First')
        new = self._mail(subject='Second')
        old.write({'date': '2026-09-18 08:00:00'})
        new.write({'date': '2026-09-19 08:00:00'})
        rows = self.Conversation.conversation_messages('crm.lead', self.lead.id)
        self.assertEqual([r['id'] for r in rows], [new.id, old.id])

    def test_the_chevron_shows_the_correspondence_and_not_the_notes(self):
        """The unfolded rows are what the row above them counted.

        `count` on the conversation row is mail. A chevron that unfolded into
        more rows than the row said it had would read as a list that cannot
        count, and an internal note is not a mail the conversation had.
        """
        self._mail()
        self.lead.message_post(body='<p>Bellen voor de prijs.</p>',
                               message_type='comment')
        row = self.Conversation.search_conversations(mailbox_id=self.mailbox.id)[0]
        rows = self.Conversation.conversation_messages('crm.lead', self.lead.id)
        self.assertEqual(len(rows), row['count'])

    def test_unfolding_is_for_people_who_read_a_mailbox(self):
        """Every method on this layer is reachable over `call_kw`."""
        outsider = self.env['res.users'].create({
            'name': 'Buitenstaander',
            'login': 'outsider@company.test',
            'email': 'outsider@company.test',
            'group_ids': [(6, 0, [self.env.ref('base.group_user').id])],
        })
        self._mail()
        with self.assertRaises(AccessError):
            self.Conversation.with_user(outsider).conversation_messages(
                'crm.lead', self.lead.id)

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
        self.assertEqual(door['threads'], 1,
                         'mail with no thread indexed is still one conversation')
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

    def _link(self, thread_id, key_type='rfc', record=None, mailbox=None):
        """One row of the thread index, the way `record_all` writes them."""
        record = record if record is not None else self.lead
        return self.env['pan.mail.thread.link'].create({
            'provider': 'imap',
            'mailbox_id': (mailbox or self.mailbox).id,
            'thread_id': thread_id,
            'key_type': key_type,
            'model': record._name,
            'res_id': record.id,
        })

    def test_the_button_counts_the_threads_on_this_record(self):
        """Two exchanges filed on one lead is two threads, and that is the
        number Open in mail needs: one conversation it can open, several and
        the reader picks. `conversations` answers the other question -- how
        many records the mail touched -- and stays 1."""
        self._mail()
        self._link('<offerte-root@vandermolen.test>')
        self._link('<storing-root@vandermolen.test>')
        door = self.Conversation.record_conversations('crm.lead', self.lead.id)
        self.assertEqual(door['threads'], 2)
        self.assertEqual(door['conversations'], 1)

    def test_one_thread_two_mailboxes_is_one_thread(self):
        """sales@ and support@ both saw it, so there are two rows and one
        conversation. The References root is what says so; the provider's own
        handle means something else in the other mailbox, which is why the
        count is not taken from it."""
        self._mail()
        support = self.env['pan.mail.mailbox'].create({
            'email': 'support@company.test',
            'provider': 'imap',
            'mailbox_type': 'shared',
        })
        self._link('<offerte-root@vandermolen.test>')
        self._link('<offerte-root@vandermolen.test>', mailbox=support)
        door = self.Conversation.record_conversations('crm.lead', self.lead.id)
        self.assertEqual(door['threads'], 1)

    def test_a_provider_handle_is_not_a_second_thread(self):
        """One conversation carries a row per handle -- Graph's own and the
        References root. Counting rows would say two."""
        self._mail()
        self._link('AAQkAGI2...', key_type='provider')
        self._link('<offerte-root@vandermolen.test>')
        door = self.Conversation.record_conversations('crm.lead', self.lead.id)
        self.assertEqual(door['threads'], 1)

    def test_a_provider_without_a_handle_still_counts(self):
        """IMAP mints no thread id, so the root is stored as its provider
        key. A count that only read `rfc` rows would report nothing."""
        self._mail()
        self._link('<offerte-root@vandermolen.test>', key_type='provider')
        door = self.Conversation.record_conversations('crm.lead', self.lead.id)
        self.assertEqual(door['threads'], 1)

    def test_the_list_narrows_to_one_record(self):
        """What door 1 opens when the record carries more than one thread:
        the Inbox, listing this record's mail and nobody else's."""
        other = self.env['crm.lead'].create({
            'name': 'Another lead', 'partner_id': self.customer.id,
        })
        self._mail()
        self._mail(record=other, subject='Nieuwe aanvraag')
        rows = self.Conversation.search_conversations(
            mailbox_id=self.mailbox.id,
            record_model='crm.lead', record_id=self.lead.id)
        self.assertEqual([(row['model'], row['res_id']) for row in rows],
                         [('crm.lead', self.lead.id)])

    def test_a_record_chip_carries_the_icon_of_its_app(self):
        """The chip shows the tile of the app whose menu opens the model:
        a lead wears CRM's icon, and a contact wears Contacts', not the cube
        of `base`, the module that happens to define it."""
        self._mail()
        thread = self.Conversation.read_conversation('crm.lead', self.lead.id)
        chip = thread['records'][0]
        self.assertEqual(chip['model'], 'crm.lead')
        self.assertEqual(chip['icon'], '/crm/static/description/icon.png')
        self.assertEqual(self.Conversation._model_icon('res.partner'),
                         '/contacts/static/description/icon.png',
                         "a contact is a Contacts record to the reader, not base's")
        # A base model with no app around it would show base's cube, and no
        # icon reads better than one that names nothing.
        with patch.object(type(self.Conversation), '_app_icon_by_menu',
                          return_value=False):
            self.assertFalse(self.Conversation._model_icon('res.partner'))
        self.assertFalse(self.Conversation._model_icon('no.such.model'),
                         'an unknown model gets no icon, not a broken image')

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

    # ------------------------------------------------------- a new mail's To

    def test_a_new_mail_on_a_contact_goes_to_that_contact(self):
        """The composer fills "To" from nothing; the pane has to."""
        self.assertEqual(
            self.Conversation.new_mail_recipients('res.partner', self.customer.id),
            [self.customer.id])

    def test_a_new_mail_on_a_record_goes_to_its_customer(self):
        self.assertEqual(
            self.Conversation.new_mail_recipients('crm.lead', self.lead.id),
            [self.customer.id])

    def test_a_new_mail_on_a_lead_with_only_an_address_makes_the_contact(self):
        """What accepting the chatter's suggestion does, without the click."""
        lead = self.env['crm.lead'].create({
            'name': 'Koelinstallatie',
            'email_from': 'Piet de Vries <piet@devries.test>',
        })
        ids = self.Conversation.new_mail_recipients('crm.lead', lead.id)
        self.assertEqual(len(ids), 1)
        partner = self.env['res.partner'].browse(ids)
        self.assertEqual(partner.email_normalized, 'piet@devries.test')
        # Asked twice, the same contact: no second Piet.
        self.assertEqual(self.Conversation.new_mail_recipients('crm.lead', lead.id), ids)

    def test_a_new_mail_on_a_record_with_nobody_goes_to_nobody(self):
        """An empty "To" the writer sees beats a guess they do not."""
        lead = self.env['crm.lead'].create({'name': 'Anoniem'})
        self.assertEqual(self.Conversation.new_mail_recipients('crm.lead', lead.id), [])
        self.assertEqual(self.Conversation.new_mail_recipients('crm.lead', 0), [])

    def test_a_new_mail_s_to_is_for_people_who_may_open_the_record(self):
        stranger = self.env['res.users'].create({
            'name': 'Nina Nobody',
            'login': 'nina.to@company.test',
            'email': 'nina.to@company.test',
            'group_ids': [(6, 0, [self.env.ref('base.group_user').id])],
        })
        with self.assertRaises(AccessError):
            self.Conversation.with_user(stranger).new_mail_recipients(
                'crm.lead', self.lead.id)
        with self.assertRaises(AccessError):
            # No chatter, so no place for a mail: the model step refuses it.
            self.Conversation.new_mail_recipients('ir.config_parameter', 1)

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
