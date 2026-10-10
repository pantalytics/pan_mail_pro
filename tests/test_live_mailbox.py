# -*- coding: utf-8 -*-
"""Your own mailbox, read live, and who else may read it.

The design is in `docs/research/personal-mailbox.md`. Nothing here is stored, so
there is no state to assert; what there is to assert is the access rule, which
is the whole of why the feature is allowed to exist. Ownership is the check --
not a group, not a menu -- so a colleague with every Mail Pro right there is
still gets an `AccessError`, and a shared mailbox has no owner and is refused
outright.

The provider client is faked at the contract, not at the socket: these methods
are the Odoo side of the seam and the three implementations have their own
files.
"""
import os
import re
from datetime import datetime
from unittest.mock import patch

from odoo.exceptions import AccessError
from odoo.tests import TransactionCase, new_test_user, tagged

CLIENT = 'imap.smtp.client'


@tagged('pan_mail_pro', 'post_install', '-at_install')
class TestLiveMailbox(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env['pan.mail.domain'].set_domains(['company.test'])
        cls.Conversation = cls.env['pan.mail.conversation']
        groups = 'base.group_user,pan_mail_pro.group_mail_mailbox_manager'
        cls.owner = new_test_user(cls.env, login='mp_owner', groups=groups,
                                  email='rutger@company.test')
        cls.colleague = new_test_user(cls.env, login='mp_colleague', groups=groups,
                                      email='sanne@company.test')
        cls.plain = new_test_user(cls.env, login='mp_plain', groups='base.group_user')
        cls.account = cls.env['pan.mail.account'].create({
            'email': 'rutger@company.test',
            'provider': 'imap',
            'user_id': cls.owner.id,
            'imap_host': 'imap.company.test', 'imap_port': 993, 'imap_security': 'ssl',
            'smtp_host': 'smtp.company.test', 'smtp_port': 465, 'smtp_security': 'ssl',
            'password': 'hunter2',
        })
        cls.mailbox = cls.env['pan.mail.mailbox'].create({
            'email': 'rutger@company.test',
            'provider': 'imap',
            'mailbox_type': 'personal',
            'owner_user_id': cls.owner.id,
        })
        cls.customer = cls.env['res.partner'].create({
            'name': 'Vandermolen Techniek',
            'email': 'bart@vandermolen.test',
        })
        cls.lead = cls.env['crm.lead'].create({
            'name': 'Asafdichtingen',
            'partner_id': cls.customer.id,
        })

    # ------------------------------------------------------------------ fakes

    def _message(self, provider_id='AAA', message_id='<one@vandermolen.test>',
                 subject='Offerte 1042', **vals):
        message = {
            'provider_message_id': provider_id,
            'message_id': message_id,
            'thread_id': 'thread-%s' % provider_id,
            'subject': subject,
            'from': {'email': 'bart@vandermolen.test', 'name': 'Bart'},
            'to': [{'email': 'rutger@company.test', 'name': 'Rutger'}],
            'cc': [],
            'date': datetime(2026, 9, 20, 10, 0, 0),
            'body_html': '<p>Kunnen jullie de levertijd bevestigen?</p>',
            'body_is_html': True,
            'is_read': False,
            'has_attachments': False,
            'headers': {},
        }
        message.update(vals)
        return message

    def _as_owner(self):
        return self.Conversation.with_user(self.owner)

    def _serving(self, messages):
        """Patch the contract's read methods to serve `messages`."""
        client = type(self.env[CLIENT])
        return (
            patch.object(client, 'search_messages', return_value=messages),
            patch.object(client, 'get_message', side_effect=lambda **kw: next(
                (m for m in messages
                 if m['provider_message_id'] == kw['provider_message_id']), None)),
        )

    def _already_in_odoo(self, message_id, record=None):
        """A mail Odoo holds, indexed the way the fetcher indexes one."""
        record = record if record is not None else self.customer
        message = self.env['mail.message'].create({
            'model': record._name,
            'res_id': record.id,
            'message_type': 'email',
            'subject': 'Offerte 1042',
            'author_id': self.customer.id,
            'email_from': self.customer.email,
        })
        self.env['pan.mail.message.ref'].record(message, [message_id])
        return message

    # ------------------------------------------------------------------ access

    def test_a_plain_user_gets_nothing(self):
        """Not their mailbox. The Inbox is every internal user's, and this
        method is reachable over `call_kw` by anybody who is logged in, so
        ownership is what refuses, not a group."""
        with self.assertRaises(AccessError):
            self.Conversation.with_user(self.plain).live_messages(self.mailbox.id)

    def test_a_colleague_cannot_read_your_mailbox(self):
        """Every Mail Pro right there is, and still no. Ownership is the rule:
        this is the question the whole feature had to answer before it could
        be built at all."""
        with self.assertRaises(AccessError):
            self.Conversation.with_user(self.colleague).live_messages(self.mailbox.id)

    def test_a_shared_mailbox_is_refused(self):
        """Nobody owns it, so "your own mail" means nothing here. A shared
        mailbox is read through what the sync imported, like today."""
        shared = self.env['pan.mail.mailbox'].create({
            'email': 'sales@company.test',
            'provider': 'imap',
            'mailbox_type': 'shared',
        })
        with self.assertRaises(AccessError):
            self._as_owner().live_messages(shared.id)

    def test_a_mailbox_that_is_not_there(self):
        with self.assertRaises(AccessError):
            self._as_owner().live_messages(0)

    def test_only_your_own_mailbox_is_offered(self):
        their_account = self.env['pan.mail.account'].create({
            'email': 'sanne@company.test', 'provider': 'imap',
            'user_id': self.colleague.id,
            'imap_host': 'imap.company.test', 'imap_port': 993, 'imap_security': 'ssl',
            'smtp_host': 'smtp.company.test', 'smtp_port': 465, 'smtp_security': 'ssl',
            'password': 'hunter2',
        })
        self.env['pan.mail.mailbox'].create({
            'email': 'sanne@company.test', 'provider': 'imap',
            'mailbox_type': 'personal', 'owner_user_id': self.colleague.id,
        })
        self.assertTrue(their_account.connected)
        offered = self._as_owner().live_mailboxes()
        self.assertEqual([m['email'] for m in offered], ['rutger@company.test'])

    # ------------------------------------------------------------------ the list

    def test_the_row_says_whether_odoo_has_it(self):
        """The filter the whole feature is for. One mail Odoo holds, one it
        has never seen, and the list tells them apart."""
        self._already_in_odoo('<one@vandermolen.test>')
        messages = [self._message(),
                    self._message(provider_id='BBB',
                                  message_id='<two@vandermolen.test>',
                                  subject='Vraag over de levering')]
        search, get = self._serving(messages)
        with search, get:
            result = self._as_owner().live_messages(self.mailbox.id)

        self.assertTrue(result['connected'])
        self.assertEqual(result['scanned'], 2)
        first, second = result['rows']
        self.assertTrue(first['linked'])
        self.assertEqual(first['model'], 'res.partner')
        self.assertEqual(first['res_id'], self.customer.id)
        self.assertEqual(first['record_name'], self.customer.display_name)
        self.assertFalse(second['linked'])
        # A live row carries no Odoo message id, which is how the list tells
        # it apart from an imported one.
        self.assertFalse(second['message_id'])
        self.assertEqual(second['subject'], 'Vraag over de levering')
        self.assertEqual(second['correspondent'], 'Bart')
        self.assertTrue(second['unread'])
        self.assertIn('levertijd', second['preview'])

    def test_sent_is_listed_under_who_it_went_to(self):
        """Inbox and Sent of your own mailbox are both the provider's, and a
        mail you wrote reads under its recipient, the way Sent reads in the
        mail client next to this screen."""
        sent = self._message(
            provider_id='SSS', message_id='<sent@company.test>',
            **{'from': {'email': 'rutger@company.test', 'name': 'Rutger'},
               'to': [{'email': 'bart@vandermolen.test', 'name': 'Bart'}]})
        search, get = self._serving([sent])
        with search as served, get:
            result = self._as_owner().live_messages(self.mailbox.id, folder='sent')

        self.assertEqual(served.call_args.kwargs['folder'], 'sent')
        self.assertEqual(result['rows'][0]['correspondent'], 'Bart')
        self.assertEqual(result['rows'][0]['email'], 'bart@vandermolen.test')

    def test_the_filter_is_over_that_flag(self):
        self._already_in_odoo('<one@vandermolen.test>')
        messages = [self._message(),
                    self._message(provider_id='BBB',
                                  message_id='<two@vandermolen.test>')]
        search, get = self._serving(messages)
        with search, get:
            missing = self._as_owner().live_messages(self.mailbox.id, linked=False)
            held = self._as_owner().live_messages(self.mailbox.id, linked=True)

        self.assertEqual([r['live_id'] for r in missing['rows']], ['BBB'])
        self.assertEqual([r['live_id'] for r in held['rows']], ['AAA'])
        # A filtered page shows fewer rows than it read, and says so, because
        # the provider cannot be asked "is this in Odoo".
        self.assertEqual(missing['scanned'], 2)

    def test_odoo_has_it_is_answered_without_naming_the_record(self):
        """The ref index is read as sudo, so it can answer for a record the
        reader may not open. What it may say then is "yes", never what it is
        filed on."""
        self._already_in_odoo('<one@vandermolen.test>', record=self.lead)
        search, get = self._serving([self._message()])
        with search, get:
            row = self._as_owner().live_messages(self.mailbox.id)['rows'][0]

        # Not a mock: a mailbox manager with no sales rights cannot read this
        # lead, and the ORM is what says so.
        self.assertFalse(self.lead.with_user(self.owner)._filtered_access('read'))

        self.assertTrue(row['linked'])
        self.assertEqual(row['model'], 'crm.lead')
        self.assertEqual(row['res_id'], self.lead.id)
        self.assertEqual(row['record_name'], '')

    def test_the_id_odoo_stores_itself_counts_as_in_odoo(self):
        """The trap this lookup was written wrong for first. `message_post`
        stores the Message-ID on `mail.message`, and the fetcher's ref index
        gets a row only when the two differ -- which on an ordinary import
        they do not. Reading only the index reports every imported mail as
        "not in Odoo", which is the whole feature backwards."""
        message = self.env['mail.message'].create({
            'model': 'res.partner',
            'res_id': self.customer.id,
            'message_type': 'email',
            'subject': 'Offerte 1042',
            'message_id': '<one@vandermolen.test>',
            'author_id': self.customer.id,
        })
        self.assertFalse(self.env['pan.mail.message.ref'].search(
            [('message_id', '=', message.message_id)]))

        search, get = self._serving([self._message()])
        with search, get:
            row = self._as_owner().live_messages(self.mailbox.id)['rows'][0]

        self.assertEqual(row['res_id'], self.customer.id)

    def test_a_mailbox_with_no_credentials_says_so(self):
        self.account.password = False
        result = self._as_owner().live_messages(self.mailbox.id)
        self.assertFalse(result['connected'])
        self.assertEqual(result['rows'], [])

    def test_a_provider_that_cannot_be_reached_is_not_a_broken_screen(self):
        """An expired grant or a network that is down makes this folder
        unavailable. The imported folders beside it still read, because they
        never leave the database."""
        with patch.object(type(self.env[CLIENT]), 'search_messages',
                          side_effect=Exception('token expired')):
            result = self._as_owner().live_messages(self.mailbox.id)

        self.assertFalse(result['connected'])
        self.assertEqual(result['rows'], [])

    def test_archive_and_deleted_are_read_too(self):
        """A mail you archived or deleted is in neither Inbox nor Sent, so
        without these two the reader goes back to the mail client for it,
        which is the trip the live read exists to remove. The role goes to
        the client as is: each provider resolves it to its own folder."""
        for folder in ('archive', 'trash'):
            search, get = self._serving([self._message()])
            with search as served, get:
                result = self._as_owner().live_messages(self.mailbox.id, folder=folder)
            self.assertEqual(served.call_args.kwargs['folder'], folder)
            self.assertEqual(len(result['rows']), 1, folder)

    def test_a_folder_this_screen_does_not_read(self):
        with self.assertRaises(AccessError):
            self._as_owner().live_messages(self.mailbox.id, folder='junk')

    # ------------------------------------------------------------------ reading

    def test_reading_does_not_mark_the_message_seen(self):
        """The read itself changes nothing. The Inbox marks the mail with
        `live_mark` once the reader opened it, so a preview or a read that
        failed half-way cannot mark a mail nobody saw."""
        search, get = self._serving([self._message()])
        with search, get, patch.object(type(self.env[CLIENT]), 'set_seen') as seen:
            row = self._as_owner().read_live_message(self.mailbox.id, 'AAA')

        seen.assert_not_called()
        self.assertIn('levertijd', row['body'])
        self.assertEqual(row['to'], ['rutger@company.test'])

    def test_the_body_is_sanitized_before_it_leaves(self):
        """This body never passed through `message_post`, where the Html field
        sanitizes on write, and it is rendered in an Odoo session. A mail from
        anybody would otherwise run script as the reader."""
        hostile = self._message(
            body_html='<p>Hoi</p><script>window.stolen = 1</script>')
        search, get = self._serving([hostile])
        with search, get:
            row = self._as_owner().read_live_message(self.mailbox.id, 'AAA')

        self.assertIn('Hoi', row['body'])
        self.assertNotIn('<script', row['body'])

    def test_a_plain_text_body_keeps_its_line_breaks(self):
        """The one body on this screen that never passed through
        `message_post`, so nothing turned its newlines into line breaks and
        the whole mail arrived as one block."""
        plain = self._message(
            body_html='Hoi,\n\nKunnen jullie de levertijd bevestigen?\nGraag voor vrijdag.',
            body_is_html=False)
        search, get = self._serving([plain])
        with search, get:
            row = self._as_owner().read_live_message(self.mailbox.id, 'AAA')

        # A blank line is a paragraph, a single newline a line break -- the
        # same two answers `plaintext2html` gives the imported bodies.
        self.assertIn('</p><p>', row['body'])
        self.assertIn('<br', row['body'])

    def _with_inline_logo(self, mimetype='image/png', content=b'\x89PNG fake'):
        """A mail whose only part is an image its own body shows."""
        message = self._message(
            body_html='<p>Hoi</p><img src="cid:logo-1@notion">',
            has_attachments=False)
        part = {'name': 'logo.png', 'mimetype': mimetype, 'content': content,
                'size': len(content or b''), 'is_inline': True,
                'content_id': 'logo-1@notion'}
        return message, part

    def test_an_embedded_image_is_shown(self):
        """`cid:` resolves nowhere in a browser. The part comes back inside
        the answer, also when the provider said the mail has no attachments
        (Graph says so for inline-only images)."""
        message, part = self._with_inline_logo()
        search, get = self._serving([message])
        parts = patch.object(type(self.env[CLIENT]), 'get_message_attachments',
                             return_value=[part])
        with search, get, parts:
            row = self._as_owner().read_live_message(self.mailbox.id, 'AAA')

        self.assertNotIn('cid:', row['body'])
        self.assertIn('src="data:image/png;base64,iVBORyBmYWtl"', row['body'])

    def test_a_body_without_embedded_images_asks_for_no_parts(self):
        search, get = self._serving([self._message()])
        parts = patch.object(type(self.env[CLIENT]), 'get_message_attachments')
        with search, get, parts as fetched:
            self._as_owner().read_live_message(self.mailbox.id, 'AAA')

        fetched.assert_not_called()

    def test_only_a_raster_image_under_the_cap_is_embedded(self):
        """The data: URI is built after the sanitizer, so what goes into it is
        decided here. Anything else keeps its unresolved cid:."""
        for mimetype, content in (('text/html', b'<script>x</script>'),
                                  ('image/svg+xml', b'<svg/>'),
                                  ('image/png', None),
                                  ('image/png', b'x' * (2 * 1024 * 1024 + 1))):
            message, part = self._with_inline_logo(mimetype, content)
            search, get = self._serving([message])
            parts = patch.object(type(self.env[CLIENT]),
                                 'get_message_attachments', return_value=[part])
            with search, get, parts:
                row = self._as_owner().read_live_message(self.mailbox.id, 'AAA')
            self.assertNotIn('data:', row['body'], mimetype)

    def test_reading_a_message_that_is_gone(self):
        search, get = self._serving([self._message()])
        with search, get, self.assertRaises(AccessError):
            self._as_owner().read_live_message(self.mailbox.id, 'NOPE')

    # ------------------------------------------------------------------ filing

    def test_importing_files_it_and_says_where(self):
        """The moment a private read becomes Odoo data. It runs the ordinary
        fetcher, so where the mail lands is the matcher's answer and not a
        second one."""
        search, get = self._serving([self._message()])
        with search, get:
            result = self._as_owner().import_live_message(self.mailbox.id, 'AAA')

        self.assertTrue(result['imported'])
        self.assertEqual(result['linked']['model'], 'res.partner')
        self.assertEqual(result['linked']['res_id'], self.customer.id)

    def test_importing_lands_on_the_record_the_reader_picked(self):
        """Add to Odoo asks for a model and a record first. The answer is the
        matcher's first rule, so the mail lands there and not on the sender's
        contact, the routing log says why, and the conversation is linked to
        that record for the next reply."""
        self.owner.group_ids |= self.env.ref('sales_team.group_sale_salesman')
        lead = self.env['crm.lead'].create({
            'name': 'Levertijd Vandermolen', 'user_id': self.owner.id})
        search, get = self._serving([self._message()])
        with search, get:
            result = self._as_owner().import_live_message(
                self.mailbox.id, 'AAA', model='crm.lead', res_id=lead.id)

        self.assertTrue(result['imported'])
        self.assertEqual(result['linked']['model'], 'crm.lead')
        self.assertEqual(result['linked']['res_id'], lead.id)
        log = self.env['pan.mail.routing.log'].search(
            [('mailbox_id', '=', self.mailbox.id)], order='id desc', limit=1)
        self.assertEqual(log.rule, 'chosen')
        link = self.env['pan.mail.thread.link'].search([
            ('mailbox_id', '=', self.mailbox.id), ('thread_id', '=', 'thread-AAA')])
        self.assertEqual((link.model, link.res_id), ('crm.lead', lead.id))

    def test_importing_onto_a_record_you_may_not_write_is_refused(self):
        """The import runs as the system, so the pick is checked as the
        reader: no sales rights, no mail on a lead."""
        lead = self.env['crm.lead'].create({'name': 'Not yours'})
        search, get = self._serving([self._message()])
        with search, get, self.assertRaises(AccessError):
            self._as_owner().import_live_message(
                self.mailbox.id, 'AAA', model='crm.lead', res_id=lead.id)

    def test_importing_does_not_overrule_the_block_list(self):
        """`force_import` lifts the filters. An objection to processing is not
        a filter."""
        self.customer.x_email_sync_blocked = True
        search, get = self._serving([self._message()])
        with search, get:
            result = self._as_owner().import_live_message(self.mailbox.id, 'AAA')

        self.assertFalse(result['imported'])
        self.assertFalse(result['linked'])

    def test_a_colleague_cannot_file_from_your_mailbox(self):
        search, get = self._serving([self._message()])
        with search, get, self.assertRaises(AccessError):
            self.Conversation.with_user(self.colleague).import_live_message(
                self.mailbox.id, 'AAA')


    def test_two_unlinked_live_rows_are_two_conversations(self):
        """Every unlinked live row has the same empty model, res_id and
        message_id, so the client's `sameConversation` has to read the
        provider handle too. Without it, picking one "Not in Odoo" row
        painted the whole folder selected. Static, because the comparison
        is JavaScript and the browser check reaches no provider."""
        path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            'static', 'src', 'js', 'conversation_view', 'conversation_view.js')
        with open(path, encoding='utf-8') as handle:
            source = handle.read()
        body = re.search(r'sameConversation\(left, right\) \{(.*?)\n    \}',
                         source, re.S)
        self.assertTrue(body, 'sameConversation is gone from the Inbox')
        self.assertIn('live_id', body.group(1))

    def test_the_live_body_is_drawn_before_the_conversation(self):
        """Opening a live row sets `state.selected` as well as `state.live`,
        so whichever branch of the pane comes first wins. With the
        conversation's first, a "Not in Odoo" mail opened on an empty
        conversation and its body never showed. Static, for the same
        reason as the test above."""
        path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            'static', 'src', 'xml', 'conversation_view.xml')
        with open(path, encoding='utf-8') as handle:
            source = handle.read()
        live = source.find('<t t-elif="state.live">')
        selected = source.find('<t t-elif="state.selected">')
        self.assertNotEqual(live, -1, 'the live branch is gone from the Inbox')
        self.assertLess(live, selected)

    # ------------------------------------------------------------------ marking

    def _marking(self, name, return_value=1):
        return patch.object(type(self.env[CLIENT]), name, return_value=return_value)

    def test_opening_marks_it_read_at_the_provider(self):
        """What Outlook does with a mail you open, done to the same mail, so
        the dot here and the one in Outlook are one fact."""
        with self._marking('set_seen') as seen:
            result = self._as_owner().live_mark(self.mailbox.id, 'AAA', 'read')
        self.assertEqual(seen.call_args.args[2], ['AAA'])
        self.assertTrue(seen.call_args.kwargs['seen'])
        self.assertEqual(result, {'live_id': 'AAA'})

    def test_marking_read_moves_the_imported_copy_without_a_second_call(self):
        """A mail the sync imported carries the same handle. Its mirror follows
        the provider, and the mirror write itself pushes nothing."""
        imported = self._already_in_odoo('<one@vandermolen.test>')
        imported.write({'x_mailbox_id': self.mailbox.id,
                        'x_provider_message_id': 'AAA'})
        imported.with_context(pan_mail_read_mirror=True).write({'x_is_read': False})
        with self._marking('set_seen') as seen:
            self._as_owner().live_mark(self.mailbox.id, 'AAA', 'read')
        self.assertEqual(seen.call_count, 1)
        self.assertTrue(imported.x_is_read)

    def test_unread_is_the_way_back(self):
        with self._marking('set_seen') as seen:
            self._as_owner().live_mark(self.mailbox.id, 'AAA', 'unread')
        self.assertFalse(seen.call_args.kwargs['seen'])

    def test_flag_and_unflag(self):
        with self._marking('set_flagged') as flagged:
            self._as_owner().live_mark(self.mailbox.id, 'AAA', 'flag')
            self.assertTrue(flagged.call_args.kwargs['flagged'])
            self._as_owner().live_mark(self.mailbox.id, 'AAA', 'unflag')
            self.assertFalse(flagged.call_args.kwargs['flagged'])

    def test_the_row_says_whether_it_is_flagged(self):
        search, get = self._serving([self._message(is_flagged=True)])
        with search, get:
            row = self._as_owner().live_messages(self.mailbox.id)['rows'][0]
        self.assertTrue(row['flagged'])

    def test_archive_moves_it_and_the_handle_follows(self):
        """Every provider mints a new handle on a move. The imported copy takes
        the new one, or its next read state goes to a message that is gone."""
        imported = self._already_in_odoo('<one@vandermolen.test>')
        imported.write({'x_mailbox_id': self.mailbox.id,
                        'x_provider_message_id': 'AAA'})
        with self._marking('move_messages', return_value=['ZZZ']) as move:
            result = self._as_owner().live_mark(self.mailbox.id, 'AAA', 'archive')
        self.assertEqual(move.call_args.args[3], 'archive')
        self.assertEqual(result, {'live_id': 'ZZZ'})
        self.assertEqual(imported.x_provider_message_id, 'ZZZ')

    def test_a_colleague_cannot_mark_your_mail(self):
        with self._marking('set_seen') as seen, self.assertRaises(AccessError):
            self.Conversation.with_user(self.colleague).live_mark(
                self.mailbox.id, 'AAA', 'read')
        seen.assert_not_called()

    def test_delete_is_not_an_action_here(self):
        """Trash is a folder this screen reads, not a button it offers."""
        with self.assertRaises(AccessError):
            self._as_owner().live_mark(self.mailbox.id, 'AAA', 'delete')
