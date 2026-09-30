# -*- coding: utf-8 -*-
"""Read and unread: one fact, owned by the mailbox.

The question this file answers is not "does the checkbox flip". It is whether
Mail Pro and Outlook can ever disagree about what you have read, which is the
whole reason the feature exists. So it pins the direction of authority (the
provider decides, Odoo mirrors), the two writes that keep it that way, and the
one bridge to Odoo's own notifications: reading here clears the reader's bell
and nobody else's.

No services: the provider client is patched at the contract, which is the seam
all three implementations sit behind.
"""
from datetime import timedelta
from unittest.mock import patch

from odoo.exceptions import AccessError
from odoo.tests import TransactionCase, new_test_user, tagged

from odoo.addons.pan_mail_pro.models.mail_message import READ_MIRROR_CTX
from odoo.addons.pan_mail_pro.models.mail_provider_client import (
    FOLDER_INBOX, UNREAD_CAP, get_provider_client,
)
from odoo.addons.pan_mail_pro.models.pan_mail_mailbox import READ_STATE_TTL

from .test_imap_provider import FakeImap

IMAP_MODULE = 'odoo.addons.pan_mail_pro.models.providers.imap_smtp.imap_client'


@tagged('pan_mail_pro', 'post_install', '-at_install')
class TestReadState(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env['pan.mail.domain'].set_domains(['company.test'])
        cls.Conversation = cls.env['pan.mail.conversation']
        # Reads the leads too: `mark_read` asks whether the caller may read
        # the mail, and Odoo answers that from the document it hangs on.
        cls.manager = new_test_user(
            cls.env, login='read_manager',
            groups='base.group_user,pan_mail_pro.group_mail_mailbox_manager,'
                   'sales_team.group_sale_salesman_all_leads')
        cls.colleague = new_test_user(
            cls.env, login='read_colleague',
            groups='base.group_user,pan_mail_pro.group_mail_mailbox_manager')
        cls.customer = cls.env['res.partner'].create({
            'name': 'Vandermolen Techniek', 'email': 'bart@vandermolen.test',
        })
        cls.mailbox = cls.env['pan.mail.mailbox'].create({
            'email': 'sales@company.test', 'provider': 'imap',
            'mailbox_type': 'shared',
        })
        cls.lead = cls.env['crm.lead'].create({
            'name': 'Asafdichtingen', 'partner_id': cls.customer.id,
        })

    def _mail(self, handle='INBOX:42:7', read=True, subject='Offerte'):
        return self.env['mail.message'].create({
            'model': 'crm.lead', 'res_id': self.lead.id,
            'message_type': 'email', 'subject': subject,
            'body': '<p>Kunnen jullie de levertijd bevestigen?</p>',
            'author_id': self.customer.id, 'email_from': self.customer.email,
            'x_direction': 'incoming', 'x_mailbox_id': self.mailbox.id,
            'x_provider_message_id': handle, 'x_is_read': read,
        })

    # -- the mirror ------------------------------------------------------- #

    def test_a_message_is_read_until_something_says_otherwise(self):
        """The default is read, and it is not an opinion about the mail.

        A database that upgrades onto this column has no mirror yet. Lighting
        every message in it up as unread would be a worse lie than calling it
        read, and the first refresh corrects whatever is actually unread.
        """
        self.assertTrue(self.env['mail.message'].create({
            'model': 'crm.lead', 'res_id': self.lead.id,
            'message_type': 'email', 'subject': 'Zonder spiegel',
        }).x_is_read)

    def test_marking_read_writes_the_mirror_and_tells_the_provider(self):
        message = self._mail(read=False)
        with patch.object(type(self.mailbox), 'push_read_state',
                          return_value=1) as push:
            result = self.Conversation.set_read('crm.lead', self.lead.id, read=True)

        self.assertEqual(result, {'read': True, 'count': 1,
                                  'message_ids': message.ids})
        self.assertTrue(message.x_is_read)
        self.assertEqual(push.call_count, 1)
        self.assertTrue(push.call_args.kwargs['read'])

    def test_marking_unread_is_the_way_back(self):
        message = self._mail(read=True)
        with patch.object(type(self.mailbox), 'push_read_state', return_value=1):
            self.Conversation.set_read('crm.lead', self.lead.id, read=False)
        self.assertFalse(message.x_is_read)
        row = self.Conversation.search_conversations(
            mailbox_id=self.mailbox.id)[0]
        self.assertTrue(row['unread'])

    def test_a_conversation_already_read_costs_no_provider_call(self):
        """The Inbox asks on every open. Saying so twice must be free.

        Without this, every conversation anybody looks at is a write to
        Microsoft for a fact Microsoft already had.
        """
        self._mail(read=True)
        with patch.object(type(self.mailbox), 'push_read_state') as push:
            result = self.Conversation.set_read('crm.lead', self.lead.id, read=True)
        self.assertEqual(result['count'], 0)
        push.assert_not_called()

    def test_the_whole_conversation_is_marked_at_once(self):
        """Nobody reads the fourth message of a thread and not the fifth."""
        first = self._mail(handle='INBOX:42:7', read=False)
        second = self._mail(handle='INBOX:42:8', read=False, subject='Re: Offerte')
        with patch.object(type(self.mailbox), 'push_read_state', return_value=2):
            result = self.Conversation.set_read('crm.lead', self.lead.id, read=True)
        self.assertEqual(result['count'], 2)
        self.assertTrue(first.x_is_read)
        self.assertTrue(second.x_is_read)

    def test_reading_a_mailbox_is_a_managers_act(self):
        self._mail(read=False)
        plain = new_test_user(self.env, login='read_plain', groups='base.group_user')
        with self.assertRaises(AccessError):
            self.Conversation.with_user(plain).set_read('crm.lead', self.lead.id)
        with self.assertRaises(AccessError):
            self.Conversation.with_user(plain).refresh_read_state()

    # -- a conversation is not a message ---------------------------------- #

    def _reply(self, handle='INBOX:42:9', read=True):
        message = self._mail(handle=handle, read=read, subject='Re: Offerte')
        message.write({'x_direction': 'outgoing', 'author_id': False})
        return message

    def test_marking_a_conversation_unread_marks_only_the_newest_incoming_mail(self):
        """Gmail's list does this, and it is the smaller write.

        The row is unread while any message is, so one mail is enough to put
        the dot back; marking the whole history unread would make the mailbox
        say every old mail in the thread is waiting for somebody. Our own
        reply is never the one: nobody has to read what they sent.
        """
        older = self._mail(handle='INBOX:42:7')
        newest_in = self._mail(handle='INBOX:42:8', subject='Re: Offerte')
        reply = self._reply()
        reply.date = newest_in.date + timedelta(minutes=5)
        with patch.object(type(self.mailbox), 'push_read_state',
                          return_value=1) as push:
            result = self.Conversation.set_read('crm.lead', self.lead.id, read=False)

        self.assertEqual(result['message_ids'], newest_in.ids)
        self.assertFalse(newest_in.x_is_read)
        self.assertTrue(older.x_is_read)
        self.assertTrue(reply.x_is_read)
        self.assertEqual(push.call_args.args[0], newest_in)

    def test_a_conversation_is_unread_while_any_message_in_it_is(self):
        """What the Unread filter finds, the row must draw as unread."""
        self._mail(handle='INBOX:42:7', read=False)
        newest = self._mail(handle='INBOX:42:8', subject='Re: Offerte')
        newest.date = newest.date + timedelta(minutes=5)
        row = self.Conversation.search_conversations(
            mailbox_id=self.mailbox.id)[0]
        self.assertEqual(row['message_id'], newest.id)
        self.assertTrue(row['unread'])

    # -- one message, many messages, any caller -------------------------- #

    def test_mark_read_and_unread_work_on_exactly_the_messages_named(self):
        """The API's way in, for one mail or a hundred: no conversation logic."""
        first = self._mail(handle='INBOX:42:7', read=False)
        second = self._mail(handle='INBOX:42:8', read=False, subject='Re: Offerte')
        with patch.object(type(self.mailbox), 'push_read_state', return_value=1):
            self.assertEqual(first.with_user(self.manager).mark_read(), 1)
        self.assertTrue(first.x_is_read)
        self.assertFalse(second.x_is_read)

        with patch.object(type(self.mailbox), 'push_read_state', return_value=2):
            both = (first | second).with_user(self.manager)
            self.assertEqual(both.mark_unread(), 1)
        self.assertFalse(first.x_is_read)

    def test_bulk_is_one_provider_call_per_mailbox(self):
        """Forty messages are one `set_seen` per mailbox, not forty."""
        other = self.env['pan.mail.mailbox'].create({
            'email': 'support@company.test', 'provider': 'imap',
            'mailbox_type': 'shared',
        })
        here = self._mail(handle='INBOX:42:7', read=False) \
            | self._mail(handle='INBOX:42:8', read=False)
        there = self._mail(handle='INBOX:43:1', read=False)
        there.x_mailbox_id = other
        with patch.object(type(self.mailbox), 'push_read_state',
                          return_value=1) as push:
            (here | there).with_user(self.manager).mark_read()
        self.assertEqual(push.call_count, 2)
        pushed = sorted(len(call.args[0]) for call in push.call_args_list)
        self.assertEqual(pushed, [1, 2])

    def test_a_plain_write_reaches_the_provider_too(self):
        """An integration that writes the field is marking the mailbox.

        Without this, `update_record` over the API changes the dot and the
        next refresh quietly changes it back.
        """
        message = self._mail(read=False)
        with patch.object(type(self.mailbox), 'push_read_state',
                          return_value=1) as push:
            message.with_user(self.manager).sudo().write({'x_is_read': True})
        self.assertEqual(push.call_count, 1)
        self.assertTrue(push.call_args.kwargs['read'])

    def test_a_copy_of_the_provider_is_not_pushed_back_to_it(self):
        message = self._mail(read=False)
        with patch.object(type(self.mailbox), 'push_read_state') as push:
            message.with_context(**READ_MIRROR_CTX).write({'x_is_read': True})
            self._refresh(['INBOX:42:7'], force=True)
        push.assert_not_called()
        self.assertFalse(message.x_is_read)

    def test_marking_mail_is_a_managers_act_over_the_api_too(self):
        message = self._mail(read=False)
        plain = new_test_user(self.env, login='mark_plain', groups='base.group_user')
        with self.assertRaises(AccessError):
            message.with_user(plain).mark_read()
        with self.assertRaises(AccessError):
            message.with_user(plain).write({'x_is_read': True})

    # -- the bridge to Odoo's own notifications --------------------------- #

    def _notify(self, message, partner):
        return self.env['mail.notification'].create({
            'mail_message_id': message.id, 'res_partner_id': partner.id,
            'notification_type': 'inbox', 'is_read': False,
        })

    def test_reading_here_clears_your_own_bell_and_nobody_elses(self):
        """A mention is addressed to a person.

        Clearing the reader's own row is the point of the bridge -- a bell
        still counting mail you have read on the next screen is the double
        work this feature exists to remove. Clearing a colleague's would mean
        one person reading a shared mailbox silently answers everybody's Odoo
        Inbox for them.
        """
        message = self._mail(read=False)
        mine = self._notify(message, self.env.user.partner_id)
        theirs = self._notify(message, self.colleague.partner_id)

        with patch.object(type(self.mailbox), 'push_read_state', return_value=1):
            self.Conversation.set_read('crm.lead', self.lead.id, read=True)

        self.assertTrue(mine.is_read)
        self.assertFalse(theirs.is_read)

    def test_the_bell_is_cleared_even_when_the_mailbox_had_already_read_it(self):
        """Two facts, and the notification is the one that outlives the mail.

        A mail a colleague read in Outlook this morning reaches you here as
        read, and your mention on it is still ringing. Opening it has to
        answer that, which is why the bell is cleared for every message and
        not only for the ones whose mirror moved.
        """
        message = self._mail(read=True)
        mine = self._notify(message, self.env.user.partner_id)
        with patch.object(type(self.mailbox), 'push_read_state') as push:
            self.Conversation.set_read('crm.lead', self.lead.id, read=True)
        push.assert_not_called()
        self.assertTrue(mine.is_read)

    def test_marking_unread_does_not_put_a_notification_back(self):
        """One way. Discuss can un-tick its own Inbox; this cannot."""
        message = self._mail(read=True)
        mine = self._notify(message, self.env.user.partner_id)
        mine.is_read = True
        with patch.object(type(self.mailbox), 'push_read_state', return_value=1):
            self.Conversation.set_read('crm.lead', self.lead.id, read=False)
        self.assertTrue(mine.is_read)
        self.assertFalse(message.x_is_read)

    # -- the refresh ------------------------------------------------------ #

    def _refresh(self, unread_handles, **kwargs):
        client = self.mailbox._get_client()
        with patch.object(type(self.mailbox), '_has_working_credentials',
                          return_value=True), \
             patch.object(type(client), 'resolve_receiving_account',
                          return_value=self.env['pan.mail.account']), \
             patch.object(type(client), 'unread_message_ids',
                          return_value=unread_handles) as ask:
            changed = self.mailbox.with_user(self.manager).refresh_read_state(**kwargs)
        return changed, ask

    def test_the_provider_decides_in_both_directions(self):
        """The mirror follows the provider, not the other way round.

        Read it in Outlook and the dot goes out here; leave it unread there
        and it comes back, even if somebody clicked it read in Odoo.
        """
        opened = self._mail(handle='INBOX:42:7', read=False)
        untouched = self._mail(handle='INBOX:42:8', read=True, subject='Re: Offerte')

        changed, ask = self._refresh(['INBOX:42:8'])

        self.assertEqual(changed, 2)
        self.assertTrue(opened.x_is_read)
        self.assertFalse(untouched.x_is_read)
        self.assertEqual(ask.call_args.args[2], FOLDER_INBOX)

    def test_a_capped_answer_does_not_read_the_rest_of_the_pile(self):
        """A full page is the newest unread, not all of them. A mail that
        missed the page is older, not read, and must keep its dot."""
        older = self._mail(handle='INBOX:42:1', read=False)
        handles = ['INBOX:42:%d' % n for n in range(2, UNREAD_CAP + 2)]
        self._refresh(handles)
        self.assertFalse(older.x_is_read)

    def test_the_refresh_is_throttled_per_mailbox(self):
        """The Inbox asks on every visit; the provider is asked once a minute."""
        self._mail(read=False)
        self._refresh(['INBOX:42:7'])
        _changed, ask = self._refresh([])
        ask.assert_not_called()

        self.mailbox.read_state_synced -= timedelta(seconds=READ_STATE_TTL + 1)
        _changed, ask = self._refresh([])
        self.assertEqual(ask.call_count, 1)

    def test_force_asks_anyway(self):
        self._mail(read=False)
        self._refresh(['INBOX:42:7'])
        _changed, ask = self._refresh([], force=True)
        self.assertEqual(ask.call_count, 1)

    def test_a_provider_that_is_down_leaves_the_mirror_alone(self):
        """The last thing we knew beats a wrong answer, and the Inbox opens."""
        message = self._mail(read=False)
        client = self.mailbox._get_client()
        with patch.object(type(self.mailbox), '_has_working_credentials',
                          return_value=True), \
             patch.object(type(client), 'resolve_receiving_account',
                          return_value=self.env['pan.mail.account']), \
             patch.object(type(client), 'unread_message_ids',
                          side_effect=OSError('connection reset')):
            self.assertEqual(
                self.mailbox.with_user(self.manager).refresh_read_state(), 0)
        self.assertFalse(message.x_is_read)

    def test_a_mailbox_without_credentials_is_not_asked(self):
        self._mail(read=False)
        client = self.mailbox._get_client()
        with patch.object(type(self.mailbox), '_has_working_credentials',
                          return_value=False), \
             patch.object(type(client), 'unread_message_ids') as ask:
            self.mailbox.with_user(self.manager).refresh_read_state()
        ask.assert_not_called()

    def test_a_failed_push_never_undoes_the_odoo_write(self):
        """Marking is the one mail write that undoes itself, so a provider
        that is down costs a disagreement the next refresh settles, not a
        button that does not work."""
        message = self._mail(read=False)
        client = self.mailbox._get_client()
        with patch.object(type(self.mailbox), '_has_working_credentials',
                          return_value=True), \
             patch.object(type(client), 'resolve_receiving_account',
                          return_value=self.env['pan.mail.account']), \
             patch.object(type(client), 'set_seen',
                          side_effect=OSError('connection reset')):
            self.Conversation.set_read('crm.lead', self.lead.id, read=True)
        self.assertTrue(message.x_is_read)

    def test_a_message_without_a_handle_is_not_pushed(self):
        """Mail that predates the handle is still markable in Odoo; there is
        simply nothing at the provider to mark."""
        message = self._mail(handle=False, read=False)
        client = self.mailbox._get_client()
        with patch.object(type(self.mailbox), '_has_working_credentials',
                          return_value=True), \
             patch.object(type(client), 'resolve_receiving_account',
                          return_value=self.env['pan.mail.account']), \
             patch.object(type(client), 'set_seen') as mark:
            self.Conversation.set_read('crm.lead', self.lead.id, read=True)
        mark.assert_not_called()
        self.assertTrue(message.x_is_read)


@tagged('pan_mail_pro', 'post_install', '-at_install')
class TestImapUnreadIds(TransactionCase):
    """The one implementation whose handle is not the provider's own id.

    An IMAP reference is `folder:uidvalidity:uid`, and the sync and this
    method have to build it the same way or the intersection matches nothing
    and every mail reads as read.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env['pan.mail.domain'].set_domains(['gate-fixture.test'])
        cls.client = get_provider_client(cls.env, 'imap')
        cls.account = cls.env['pan.mail.account'].create({
            'email': 'info@company.test', 'provider': 'imap', 'user_id': False,
            'imap_host': 'imap.soverin.net', 'imap_port': 993,
            'imap_security': 'ssl', 'smtp_host': 'smtp.soverin.net',
            'smtp_port': 465, 'smtp_security': 'ssl', 'password': 'hunter2',
        })
        cls.mailbox = cls.env['pan.mail.mailbox'].create({
            'email': 'info@company.test', 'provider': 'imap',
        })

    def test_unread_ids_are_folder_scoped_references(self):
        imap = FakeImap(uids=[b'7', b'9'], uidvalidity=b'42')
        with patch(f'{IMAP_MODULE}.imaplib.IMAP4_SSL', return_value=imap):
            handles = self.client.unread_message_ids(
                self.account, self.mailbox, folder=FOLDER_INBOX)

        self.assertEqual(handles, ['inbox:42:7', 'inbox:42:9'])
        self.assertIn('UNSEEN', imap.searched)
        # Read-only, like every other read: asking which mail is unread must
        # not be what marks it read.
        self.assertTrue(imap.readonly)
