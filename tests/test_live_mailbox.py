# -*- coding: utf-8 -*-
"""Your own mailbox, read live, and who else may read it.

The design is in `docs/plans/personal-mailbox.md`. Nothing here is stored, so
there is no state to assert; what there is to assert is the access rule, which
is the whole of why the feature is allowed to exist. Ownership is the check --
not a group, not a menu -- so a colleague with every Mail Pro right there is
still gets an `AccessError`, and a shared mailbox has no owner and is refused
outright.

The provider client is faked at the contract, not at the socket: these methods
are the Odoo side of the seam and the three implementations have their own
files.
"""
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
        """The inbox is for mailbox managers, and this method is reachable over
        `call_kw` by anybody who is logged in."""
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

    def test_a_folder_this_screen_does_not_read(self):
        with self.assertRaises(AccessError):
            self._as_owner().live_messages(self.mailbox.id, folder='trash')

    # ------------------------------------------------------------------ reading

    def test_reading_does_not_mark_the_message_seen(self):
        """Reading mail here must not change what the mail client open next to
        this screen shows. A seen flag written from a preview is how people
        lose an email."""
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
