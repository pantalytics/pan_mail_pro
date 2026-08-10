# -*- coding: utf-8 -*-
"""IMAP/SMTP client: credentials, dispatch, sending, and IMAP normalization.

`imap.smtp.client` is the third implementation of the `mail.provider.client`
contract and the first without OAuth, so most of what is asserted here is that
it behaves like the other two where the contract says it must, and differs only
where the protocols genuinely do: credentials are a password, a message id is a
folder-scoped UID, and a thread key is the References root.

imaplib and smtplib are faked at the class boundary — no sockets, no server.
"""
import re
from datetime import datetime
from email import message_from_bytes, policy
from unittest.mock import patch

from odoo.exceptions import UserError
from odoo.tests import TransactionCase, tagged

from odoo.addons.pan_mail_pro.models.mail_provider_client import (
    FOLDER_INBOX,
    FOLDER_SENT,
    get_provider_client,
)

IMAP_MODULE = 'odoo.addons.pan_mail_pro.models.providers.imap_smtp.imap_client'


class FakeImap:
    """Just enough IMAP4 to answer the calls the client makes."""

    def __init__(self, uids=(), fetch=None, folders=None, uidvalidity=b'42',
                 internaldates=None):
        self.uids = list(uids)
        self.fetch_data = fetch or []
        # {uid: INTERNALDATE} answered to the metadata-only probe the client
        # uses to narrow a widened SEARCH SINCE window. See `_dates_for`.
        self.internaldates = internaldates or {}
        self.folders = folders or [b'(\\HasNoChildren \\Sent) "." "Sent Items"']
        self.uidvalidity = uidvalidity
        self.selected = None
        self.readonly = None
        self.searched = None
        self.appended = []
        self.logged_out = False

    # -- connection ------------------------------------------------------- #
    def login(self, user, password):
        self.credentials = (user, password)
        return ('OK', [b'Logged in'])

    def logout(self):
        self.logged_out = True
        return ('BYE', [b''])

    # -- folders ---------------------------------------------------------- #
    def list(self, *args, **kwargs):
        return ('OK', self.folders)

    def select(self, name, readonly=False):
        self.selected = name
        self.readonly = readonly
        return ('OK', [b'1'])

    def response(self, key):
        return (key, [self.uidvalidity])

    # -- messages --------------------------------------------------------- #
    def uid(self, command, *args):
        if command == 'SEARCH':
            self.searched = args
            return ('OK', [b' '.join(self.uids)])
        if command == 'FETCH':
            if 'BODY' not in args[-1]:
                # The date probe. A metadata-only FETCH has no literal, so the
                # server answers with bare lines rather than (meta, body) pairs.
                self.probed = args
                return ('OK', [
                    b'1 (UID %s INTERNALDATE "%s")' % (uid, self.internaldates[uid])
                    for uid in args[0].split(b',') if uid in self.internaldates
                ])
            self.fetched = args
            return ('OK', self.fetch_data)
        raise AssertionError(f'unexpected IMAP command {command}')

    def append(self, folder, flags, date_time, message):
        self.appended.append((folder, flags, message))
        return ('OK', [b'Appended'])


class FakeSmtp:
    def __init__(self):
        self.sent = []
        self.quit_called = False

    def login(self, user, password):
        self.credentials = (user, password)

    def send_message(self, msg, from_addr=None, to_addrs=None):
        self.sent.append({'msg': msg, 'from': from_addr, 'to': to_addrs})

    def quit(self):
        self.quit_called = True


def imap_fetch_item(raw, uid=b'7', flags='\\Seen',
                    internaldate='12-May-2026 10:00:00 +0200'):
    """One imaplib FETCH response entry: (metadata line, literal)."""
    meta = b'1 (UID %s FLAGS (%s) INTERNALDATE "%s" BODY[] {%d}' % (
        uid, flags.encode(), internaldate.encode(), len(raw))
    return [(meta, raw), b')']


@tagged('pan_mail_pro', 'post_install', '-at_install')
class TestImapProvider(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Account = cls.env['pan.mail.account']
        cls.client = get_provider_client(cls.env, 'imap')
        cls.user = cls.env['res.users'].create({
            'name': 'IMAP User', 'login': 'imap_user@test.local',
            'email': 'imap_user@test.local',
        })
        # Incoming sync is gated on internal domains being declared. A domain
        # nothing in this fixture uses, so the gate opens without turning any
        # fixture address internal.
        cls.env['pan.mail.internal.domains'].set_domains(['gate-fixture.test'])

    def _imap_account(self, email='info@company.test', **vals):
        base = {
            'email': email, 'provider': 'imap', 'user_id': False,
            'imap_host': 'imap.soverin.net', 'imap_port': 993, 'imap_security': 'ssl',
            'smtp_host': 'smtp.soverin.net', 'smtp_port': 465, 'smtp_security': 'ssl',
            'password': 'hunter2',
        }
        base.update(vals)
        return self.Account.create(base)

    def _mailbox(self, email='info@company.test', **vals):
        base = {'email': email, 'x_provider': 'imap', 'x_mailbox_type': 'shared'}
        base.update(vals)
        return self.env['x_microsoft.mailbox'].create(base)

    # ------------------------------------------------------------------ #
    # Dispatch and capabilities
    # ------------------------------------------------------------------ #
    def test_imap_mailbox_dispatches_to_the_imap_client(self):
        mailbox = self._mailbox()
        self.assertEqual(mailbox._get_client()._name, 'imap.smtp.client')
        self.assertEqual(mailbox._get_client().provider_code(), 'imap')

    def test_imap_services_every_mailbox_type_but_lends_no_token(self):
        # An address is a login: there is no send-as to borrow and nothing to
        # delegate, so a shared mailbox is its own account.
        self.assertEqual(set(self.client.supported_mailbox_types),
                         {'personal', 'shared', 'notification'})
        self.assertFalse(self.client.supports_shared_mailbox)
        self.assertFalse(self.client.supports_delegation)

    def test_shared_imap_mailbox_needs_no_owner(self):
        """The constraint that demands an owner is Microsoft's SendAs model.
        Requiring one here would make the mailbox unconfigurable."""
        self.env['x_microsoft.mailbox'].create({
            'email': 'notifications@company.test', 'x_mailbox_type': 'notification',
            'x_owner_user_id': self.user.id,
        })
        mailbox = self._mailbox(x_sync_mode='known_partners')
        self.assertFalse(mailbox.x_owner_user_id)

    # ------------------------------------------------------------------ #
    # Credentials
    # ------------------------------------------------------------------ #
    def test_connected_means_password_not_refresh_token(self):
        """The OAuth definition of "connected" would report every IMAP account
        as dead, which is exactly the bug `account_is_connected` prevents."""
        account = self._imap_account()
        self.assertTrue(account.connected)
        self.assertFalse(account.refresh_token_encrypted)

        account.password = False
        self.assertFalse(account.connected)

    def test_connected_requires_both_halves(self):
        account = self._imap_account(smtp_host=False)
        self.assertFalse(account.connected)
        account.smtp_host = 'smtp.soverin.net'
        self.assertTrue(account.connected)

    def test_password_is_encrypted_at_rest(self):
        account = self._imap_account()
        self.assertTrue(account.password_encrypted)
        self.assertNotIn('hunter2', account.password_encrypted)
        account.invalidate_recordset()
        self.assertEqual(account.password, 'hunter2')

    def test_account_resolves_by_address_for_send_and_receive(self):
        account = self._imap_account()
        mailbox = self._mailbox()
        self.assertEqual(mailbox._get_client().resolve_receiving_account(mailbox), account)
        # The author is irrelevant on SMTP — no send-as to lend a token to.
        self.assertEqual(
            mailbox._get_client().resolve_sending_account(mailbox, author_user=self.user),
            account)

    def test_personal_mailbox_falls_back_to_the_owners_account(self):
        account = self._imap_account(email='imap_user@test.local', user_id=self.user.id)
        mailbox = self._mailbox(
            email='alias@company.test', x_mailbox_type='personal',
            x_owner_user_id=self.user.id)
        self.assertEqual(mailbox._get_client().resolve_receiving_account(mailbox), account)

    def test_a_connected_imap_account_makes_a_selectable_owner(self):
        """The owner dropdowns filter on the provider-neutral flag; a per-provider
        one would hide a user whose only account is IMAP."""
        self.assertFalse(self.user.x_pan_mail_connected)
        self._imap_account(email='imap_user@test.local', user_id=self.user.id)
        self.assertTrue(self.user.x_pan_mail_connected)
        self.assertFalse(self.env['pan.mail.account']._for_user(self.user, 'outlook'))

    def test_entering_credentials_makes_the_mailbox_syncable(self):
        """A service account is found by address, so no field path leads to it.

        This was a stored compute that needed invalidating by hand from the
        account's write; the cron asks _has_working_credentials() when it needs
        the answer instead, so there is nothing left to keep in sync.
        """
        self.env['x_microsoft.mailbox'].create({
            'email': 'notifications@company.test', 'x_mailbox_type': 'notification',
            'x_owner_user_id': self.user.id,
        })
        mailbox = self._mailbox(x_sync_mode='all')
        self.assertFalse(mailbox._has_working_credentials())

        self._imap_account()
        self.assertTrue(mailbox._has_working_credentials())

    # ------------------------------------------------------------------ #
    # No OAuth
    # ------------------------------------------------------------------ #
    def test_oauth_methods_refuse_in_the_contracts_terms(self):
        account = self._imap_account()
        for call in (
            lambda: self.client.get_authorization_url('https://odoo.test/cb'),
            lambda: self.client._exchange_code_for_tokens('code', 'https://odoo.test/cb'),
            lambda: self.client.refresh_access_token(account),
            lambda: self.client.get_valid_token(account),
        ):
            with self.assertRaises(UserError):
                call()

    # ------------------------------------------------------------------ #
    # Sending
    # ------------------------------------------------------------------ #
    def _patch_smtp(self, smtp):
        return patch(f'{IMAP_MODULE}.smtplib.SMTP_SSL', return_value=smtp)

    def _patch_imap(self, imap):
        return patch(f'{IMAP_MODULE}.imaplib.IMAP4_SSL', return_value=imap)

    def test_send_builds_the_message_and_files_a_copy_in_sent(self):
        account, mailbox = self._imap_account(), self._mailbox()
        mail = self.env['mail.mail'].create({
            'subject': 'Hello', 'body_html': '<p>Hi there</p>',
            'email_to': 'customer@example.com',
        })
        smtp, imap = FakeSmtp(), FakeImap()
        with self._patch_smtp(smtp), self._patch_imap(imap):
            result = self.client.send_message(mail, mailbox, account)

        self.assertTrue(result['success'])
        self.assertIn('@company.test>', result['message_id'])
        sent = smtp.sent[0]
        self.assertEqual(sent['from'], 'info@company.test')
        self.assertEqual(sent['to'], ['customer@example.com'])
        self.assertEqual(sent['msg']['Subject'], 'Hello')
        self.assertEqual(sent['msg']['Message-ID'], result['message_id'])
        self.assertIn('Hi there', sent['msg'].get_body(('html',)).get_content())

        # SMTP files nothing by itself; the copy in Sent is ours to APPEND.
        self.assertTrue(imap.appended)
        folder, flags, raw = imap.appended[0]
        self.assertEqual(folder, '"Sent Items"')  # from the server's \Sent flag
        self.assertIn(b'Hi there', raw)

    def test_send_sets_the_odoo_loop_guard_headers(self):
        account, mailbox = self._imap_account(), self._mailbox()
        partner = self.env['res.partner'].create({'name': 'C', 'email': 'c@example.com'})
        mail = self.env['mail.mail'].create({
            'subject': 'x', 'body_html': '<p>x</p>', 'email_to': 'c@example.com',
            'model': 'res.partner', 'res_id': partner.id,
        })
        smtp = FakeSmtp()
        with self._patch_smtp(smtp), self._patch_imap(FakeImap()):
            self.client.send_message(mail, mailbox, account)

        msg = smtp.sent[0]['msg']
        self.assertEqual(msg['X-Odoo-Model'], 'res.partner')
        self.assertEqual(msg['X-Odoo-Record-Id'], str(partner.id))
        self.assertEqual(msg['X-Odoo-Mail-Id'], str(mail.id))

    def test_send_puts_cc_on_the_envelope(self):
        """A Cc recipient only receives the mail if it is in the envelope; the
        header alone delivers to nobody."""
        account, mailbox = self._imap_account(), self._mailbox()
        partner = self.env['res.partner'].create({'name': 'One', 'email': 'one@example.com'})
        mail = self.env['mail.mail'].create({
            'subject': 'x', 'body_html': '<p>x</p>',
            'recipient_ids': [(6, 0, [partner.id])], 'email_cc': 'boss@example.com',
        })
        smtp = FakeSmtp()
        with self._patch_smtp(smtp), self._patch_imap(FakeImap()):
            self.client.send_message(mail, mailbox, account)

        self.assertEqual(sorted(smtp.sent[0]['to']),
                         ['boss@example.com', 'one@example.com'])
        self.assertIn('boss@example.com', smtp.sent[0]['msg']['Cc'])

    def test_send_without_recipients_is_a_no_recipients_error(self):
        account, mailbox = self._imap_account(), self._mailbox()
        mail = self.env['mail.mail'].create({'subject': 'x', 'body_html': '<p>x</p>'})
        with patch(f'{IMAP_MODULE}.smtplib.SMTP_SSL') as smtp_ssl:
            result = self.client.send_message(mail, mailbox, account)
            smtp_ssl.assert_not_called()  # never open a socket without a recipient
        self.assertFalse(result['success'])
        self.assertEqual(result['error_code'], 'no_recipients')

    def test_a_failing_sent_copy_does_not_fail_the_send(self):
        """The mail is already delivered; failing to file a copy is not a send
        failure and must not be reported as one."""
        account, mailbox = self._imap_account(), self._mailbox()
        mail = self.env['mail.mail'].create({
            'subject': 'x', 'body_html': '<p>x</p>', 'email_to': 'c@example.com'})
        with self._patch_smtp(FakeSmtp()), \
                patch(f'{IMAP_MODULE}.imaplib.IMAP4_SSL', side_effect=OSError('imap down')):
            result = self.client.send_message(mail, mailbox, account)
        self.assertTrue(result['success'])

    def test_smtp_failure_is_reported_not_raised(self):
        import smtplib
        account, mailbox = self._imap_account(), self._mailbox()
        mail = self.env['mail.mail'].create({
            'subject': 'x', 'body_html': '<p>x</p>', 'email_to': 'c@example.com'})
        with patch(f'{IMAP_MODULE}.smtplib.SMTP_SSL',
                   side_effect=smtplib.SMTPAuthenticationError(535, b'Bad login')):
            result = self.client.send_message(mail, mailbox, account)
        self.assertFalse(result['success'])
        self.assertIn('Bad login', result['error'])

    def test_a_reply_keeps_the_threads_key(self):
        """MIME has no thread id, so the References root stands in — the sent
        reply and the message it answers share one key."""
        account, mailbox = self._imap_account(), self._mailbox()
        mail = self.env['mail.mail'].create({
            'subject': 'Re: x', 'body_html': '<p>x</p>', 'email_to': 'c@example.com',
            'references': '<root@client.test> <parent@client.test>',
        })
        smtp = FakeSmtp()
        with self._patch_smtp(smtp), self._patch_imap(FakeImap()):
            result = self.client.send_message(mail, mailbox, account)

        self.assertEqual(result['thread_id'], '<root@client.test>')
        self.assertEqual(smtp.sent[0]['msg']['In-Reply-To'], '<parent@client.test>')

    # ------------------------------------------------------------------ #
    # Receiving
    # ------------------------------------------------------------------ #
    def _raw_email(self, subject='Quote request', html='<p>Hello</p>',
                   message_id='<abc@client.test>', extra_headers=None):
        headers = {
            'Message-ID': message_id,
            'Subject': subject,
            'From': 'Ann <ann@client.test>',
            'To': 'Sales <sales@company.test>',
            'Cc': 'bob@client.test, carl@client.test',
            'Date': 'Tue, 12 May 2026 12:00:00 +0200',
        }
        headers.update(extra_headers or {})
        lines = [f'{k}: {v}' for k, v in headers.items()]
        lines += ['MIME-Version: 1.0', 'Content-Type: text/html; charset="utf-8"', '']
        lines.append(html)
        return '\r\n'.join(lines).encode()

    def test_fetch_normalizes_and_sorts_oldest_first(self):
        account, mailbox = self._imap_account(), self._mailbox()
        imap = FakeImap(
            uids=[b'7', b'9'],
            fetch=imap_fetch_item(self._raw_email(), uid=b'7',
                                  internaldate='12-May-2026 10:00:00 +0200')
            + imap_fetch_item(self._raw_email(message_id='<later@client.test>'), uid=b'9',
                              internaldate='13-May-2026 09:00:00 +0000'),
        )
        with self._patch_imap(imap):
            messages = self.client.fetch_messages(account, mailbox, folder=FOLDER_INBOX)

        self.assertEqual([m['message_id'] for m in messages],
                         ['<abc@client.test>', '<later@client.test>'])
        first = messages[0]
        # INTERNALDATE is +0200 -> naive UTC, which is what the cursor compares.
        self.assertEqual(first['date'], datetime(2026, 5, 12, 8, 0, 0))
        self.assertEqual(first['subject'], 'Quote request')
        self.assertEqual(first['from'], {'name': 'Ann', 'email': 'ann@client.test'})
        self.assertEqual([c['email'] for c in first['cc']],
                         ['bob@client.test', 'carl@client.test'])
        self.assertTrue(first['body_is_html'])
        self.assertIn('Hello', first['body_html'])
        self.assertTrue(first['is_read'])
        # Selected read-only: syncing must never mark somebody's mail as read.
        self.assertTrue(imap.readonly)

    def test_a_busy_day_of_slack_does_not_empty_the_batch(self):
        """SEARCH SINCE is date-granular, so the window is asked a day wide.

        Cutting that window to `limit` before applying the real cutoff hands
        back a batch consisting only of the slack day — on a mailbox busier
        than `limit` messages a day, every one of them then fails the exact
        `>= since` test and the fetch comes back empty. An empty fetch reads as
        "caught up", so `_process_mailbox` jumps the cursor to now() and the
        mail in between is gone for good.
        """
        account, mailbox = self._imap_account(), self._mailbox()
        since = datetime(2026, 5, 12, 0, 0, 0)
        imap = FakeImap(
            # Three from the slack day, one genuinely after the cursor.
            uids=[b'1', b'2', b'3', b'9'],
            internaldates={
                b'1': b'11-May-2026 08:00:00 +0000',
                b'2': b'11-May-2026 09:00:00 +0000',
                b'3': b'11-May-2026 10:00:00 +0000',
                b'9': b'12-May-2026 09:00:00 +0000',
            },
            fetch=imap_fetch_item(self._raw_email(), uid=b'9',
                                  internaldate='12-May-2026 09:00:00 +0000'),
        )
        with self._patch_imap(imap):
            messages = self.client.fetch_messages(
                account, mailbox, folder=FOLDER_INBOX,
                since_datetime=since, limit=3)

        self.assertEqual(len(messages), 1, 'The slack day must not eat the batch')
        self.assertTrue(messages[0]['provider_message_id'].endswith(':9'))
        # And only the survivor is pulled in full — the point of probing first.
        self.assertEqual(imap.fetched[0], b'9')

    def test_no_cursor_means_no_date_probe(self):
        """A first sync has nothing to narrow against, so it skips the probe."""
        account, mailbox = self._imap_account(), self._mailbox()
        imap = FakeImap(uids=[b'7'], fetch=imap_fetch_item(self._raw_email(), uid=b'7'))
        with self._patch_imap(imap):
            self.client.fetch_messages(account, mailbox, folder=FOLDER_INBOX)

        self.assertFalse(hasattr(imap, 'probed'))

    def test_fetch_reads_the_inbox_and_the_servers_own_sent_folder(self):
        account, mailbox = self._imap_account(), self._mailbox()
        imap = FakeImap(uids=[], fetch=[])
        with self._patch_imap(imap):
            self.client.fetch_messages(account, mailbox, folder=FOLDER_INBOX)
            self.assertEqual(imap.selected, '"INBOX"')
            self.client.fetch_messages(account, mailbox, folder=FOLDER_SENT)
            # Not hardcoded 'Sent': taken from the \Sent special-use flag.
            self.assertEqual(imap.selected, '"Sent Items"')

        account.imap_sent_folder = 'INBOX.Verzonden'
        with self._patch_imap(imap):
            self.client.fetch_messages(account, mailbox, folder=FOLDER_SENT)
        self.assertEqual(imap.selected, '"INBOX.Verzonden"')

    def test_headers_are_lowercased_for_the_loop_guard(self):
        account, mailbox = self._imap_account(), self._mailbox()
        raw = self._raw_email(extra_headers={
            'X-Odoo-Model': 'crm.lead', 'In-Reply-To': '<parent@x>'})
        imap = FakeImap(uids=[b'7'], fetch=imap_fetch_item(raw))
        with self._patch_imap(imap):
            message = self.client.fetch_messages(account, mailbox)[0]

        self.assertEqual(message['headers']['x-odoo-model'], 'crm.lead')
        self.assertEqual(message['headers']['in-reply-to'], '<parent@x>')

    def test_thread_key_is_the_references_root(self):
        account, mailbox = self._imap_account(), self._mailbox()
        raw = self._raw_email(extra_headers={
            'References': '<root@x> <parent@x>', 'In-Reply-To': '<parent@x>'})
        imap = FakeImap(uids=[b'7'], fetch=imap_fetch_item(raw))
        with self._patch_imap(imap):
            message = self.client.fetch_messages(account, mailbox)[0]
        self.assertEqual(message['thread_id'], '<root@x>')

        # A message that starts a thread is its own root.
        imap = FakeImap(uids=[b'7'], fetch=imap_fetch_item(self._raw_email()))
        with self._patch_imap(imap):
            message = self.client.fetch_messages(account, mailbox)[0]
        self.assertEqual(message['thread_id'], '<abc@client.test>')

    def test_message_reference_carries_folder_and_uidvalidity(self):
        account, mailbox = self._imap_account(), self._mailbox()
        imap = FakeImap(uids=[b'7'], fetch=imap_fetch_item(self._raw_email()))
        with self._patch_imap(imap):
            message = self.client.fetch_messages(account, mailbox)[0]
        self.assertEqual(message['provider_message_id'], 'inbox:42:7')

        with self._patch_imap(imap):
            full = self.client.get_message(account, mailbox, 'inbox:42:7')
        self.assertEqual(full['message_id'], '<abc@client.test>')

    def test_renumbered_folder_is_refused_rather_than_misread(self):
        """A UID means nothing once UIDVALIDITY changes; fetching whatever now
        sits at that number would import the wrong message."""
        account, mailbox = self._imap_account(), self._mailbox()
        imap = FakeImap(uids=[b'7'], fetch=imap_fetch_item(self._raw_email()),
                        uidvalidity=b'99')
        with self._patch_imap(imap), self.assertRaises(UserError):
            self.client.get_message(account, mailbox, 'inbox:42:7')

    def test_search_since_is_widened_and_then_filtered_exactly(self):
        """IMAP's SINCE is date-granular and server-local, so it is asked wide
        and narrowed here."""
        account, mailbox = self._imap_account(), self._mailbox()
        imap = FakeImap(
            uids=[b'7', b'9'],
            fetch=imap_fetch_item(self._raw_email(), uid=b'7',
                                  internaldate='12-May-2026 10:00:00 +0000')
            + imap_fetch_item(self._raw_email(message_id='<new@client.test>'), uid=b'9',
                              internaldate='14-May-2026 10:00:00 +0000'),
        )
        with self._patch_imap(imap):
            messages = self.client.fetch_messages(
                account, mailbox, since_datetime=datetime(2026, 5, 13, 0, 0, 0))

        self.assertIn('SINCE', imap.searched)
        self.assertIn('12-May-2026', imap.searched)  # a day of slack
        self.assertEqual([m['message_id'] for m in messages], ['<new@client.test>'])

    def test_backlog_is_walked_oldest_first(self):
        """Taking the newest N would step over everything older, and the caller's
        cursor would never come back for it."""
        account, mailbox = self._imap_account(), self._mailbox()
        imap = FakeImap(uids=[b'1', b'2', b'3'], fetch=[])
        with self._patch_imap(imap):
            self.client.fetch_messages(account, mailbox, limit=2)
        self.assertEqual(imap.fetched[0], b'1,2')

    def test_attachments_are_normalized_inline_and_regular(self):
        account, mailbox = self._imap_account(), self._mailbox()
        raw = (
            b'Message-ID: <att@client.test>\r\n'
            b'From: ann@client.test\r\n'
            b'To: sales@company.test\r\n'
            b'Subject: With files\r\n'
            b'MIME-Version: 1.0\r\n'
            b'Content-Type: multipart/mixed; boundary="B"\r\n'
            b'\r\n'
            b'--B\r\n'
            b'Content-Type: text/html; charset="utf-8"\r\n\r\n'
            b'<p>See <img src="cid:logo123"/></p>\r\n'
            b'--B\r\n'
            b'Content-Type: image/png\r\n'
            b'Content-Disposition: inline; filename="logo.png"\r\n'
            b'Content-ID: <logo123>\r\n'
            b'Content-Transfer-Encoding: base64\r\n\r\n'
            b'aGVsbG8=\r\n'
            b'--B\r\n'
            b'Content-Type: application/pdf\r\n'
            b'Content-Disposition: attachment; filename="report.pdf"\r\n'
            b'Content-Transfer-Encoding: base64\r\n\r\n'
            b'JVBERi0=\r\n'
            b'--B--\r\n'
        )
        imap = FakeImap(uids=[b'7'], fetch=imap_fetch_item(raw))
        with self._patch_imap(imap):
            message = self.client.get_message(account, mailbox, 'inbox:42:7')
            attachments = self.client.get_message_attachments(
                account, mailbox, 'inbox:42:7')

        self.assertTrue(message['has_attachments'])
        by_name = {a['name']: a for a in attachments}
        self.assertEqual(by_name['logo.png']['content'], b'hello')
        self.assertTrue(by_name['logo.png']['is_inline'])
        self.assertEqual(by_name['logo.png']['content_id'], 'logo123')
        self.assertFalse(by_name['report.pdf']['is_inline'])

    def test_attachment_failure_returns_empty_rather_than_raising(self):
        account, mailbox = self._imap_account(), self._mailbox()
        with patch(f'{IMAP_MODULE}.imaplib.IMAP4_SSL', side_effect=OSError('imap down')):
            self.assertEqual(
                self.client.get_message_attachments(account, mailbox, 'inbox:42:7'), [])

    def test_plain_text_mail_is_not_marked_html(self):
        account, mailbox = self._imap_account(), self._mailbox()
        raw = (b'Message-ID: <p@x>\r\nFrom: a@b.com\r\nTo: c@d.com\r\n'
               b'Subject: plain\r\nContent-Type: text/plain; charset="utf-8"\r\n\r\n'
               b'just text\r\n')
        imap = FakeImap(uids=[b'7'], fetch=imap_fetch_item(raw))
        with self._patch_imap(imap):
            message = self.client.fetch_messages(account, mailbox)[0]
        self.assertFalse(message['body_is_html'])
        self.assertIn('just text', message['body_html'])

    def test_unseen_message_is_reported_unread(self):
        account, mailbox = self._imap_account(), self._mailbox()
        imap = FakeImap(uids=[b'7'],
                        fetch=imap_fetch_item(self._raw_email(), flags='\\Recent'))
        with self._patch_imap(imap):
            message = self.client.fetch_messages(account, mailbox)[0]
        self.assertFalse(message['is_read'])

    # ------------------------------------------------------------------ #
    # Test connection
    # ------------------------------------------------------------------ #
    def test_test_connection_checks_both_halves(self):
        account = self._imap_account()
        with self._patch_imap(FakeImap()), self._patch_smtp(FakeSmtp()):
            result = self.client.test_connection(account)
        self.assertTrue(result['success'])
        self.assertEqual(result['email'], 'info@company.test')

        # A mailbox that reads but cannot send is broken, and says which half.
        import smtplib
        with self._patch_imap(FakeImap()), \
                patch(f'{IMAP_MODULE}.smtplib.SMTP_SSL',
                      side_effect=smtplib.SMTPAuthenticationError(535, b'Bad login')):
            result = self.client.test_connection(account)
        self.assertFalse(result['success'])
        self.assertTrue(re.match(r'SMTP:', result['error']))

    def test_incomplete_credentials_are_refused_before_dialling(self):
        account = self._imap_account(imap_host=False)
        with patch(f'{IMAP_MODULE}.imaplib.IMAP4_SSL') as imap_ssl:
            result = self.client.test_connection(account)
            imap_ssl.assert_not_called()
        self.assertFalse(result['success'])


@tagged('pan_mail_pro', 'post_install', '-at_install')
class TestImapOutgoingRouting(TransactionCase):
    """mail.mail must route an IMAP mailbox without asking for an access token."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.user = cls.env['res.users'].create({
            'name': 'IMAP Sender', 'login': 'imap_sender@test.local',
            'email': 'imap_sender@test.local',
        })
        cls.account = cls.env['pan.mail.account'].create({
            'email': 'sales@company.test', 'provider': 'imap', 'user_id': cls.user.id,
            'imap_host': 'imap.soverin.net', 'smtp_host': 'smtp.soverin.net',
            'password': 'hunter2',
        })
        cls.mailbox = cls.env['x_microsoft.mailbox'].create({
            'email': 'sales@company.test', 'x_provider': 'imap',
            'x_mailbox_type': 'personal', 'x_owner_user_id': cls.user.id,
        })
        cls.user.x_microsoft_default_mailbox_id = cls.mailbox

    def test_mail_is_sent_through_the_imap_client(self):
        mail = self.env['mail.mail'].create({
            'subject': 'Routed', 'body_html': '<p>Routed</p>',
            'email_to': 'customer@example.com',
            'author_id': self.user.partner_id.id,
        })
        smtp = FakeSmtp()
        with patch(f'{IMAP_MODULE}.smtplib.SMTP_SSL', return_value=smtp), \
                patch(f'{IMAP_MODULE}.imaplib.IMAP4_SSL', return_value=FakeImap()):
            mail.send()

        self.assertEqual(mail.state, 'sent')
        self.assertEqual(smtp.sent[0]['from'], 'sales@company.test')
        self.assertTrue(mail.x_microsoft_message_id)

    def test_sent_message_id_is_stored_for_dedup(self):
        """The incoming sync skips anything whose Message-ID it already has;
        that only works if the send recorded one."""
        mail = self.env['mail.mail'].create({
            'subject': 'Dedup', 'body_html': '<p>x</p>',
            'email_to': 'customer@example.com',
            'author_id': self.user.partner_id.id,
        })
        smtp = FakeSmtp()
        with patch(f'{IMAP_MODULE}.smtplib.SMTP_SSL', return_value=smtp), \
                patch(f'{IMAP_MODULE}.imaplib.IMAP4_SSL', return_value=FakeImap()):
            mail.send()

        raw = smtp.sent[0]['msg'].as_bytes()
        sent = message_from_bytes(raw, policy=policy.default)
        self.assertEqual(mail.x_microsoft_message_id, sent['Message-ID'])
