# -*- coding: utf-8 -*-
"""IMAP/SMTP against a real mail server, not a fake.

`test_imap_provider.py` fakes imaplib and smtplib at the class boundary, which
proves the client calls them correctly but cannot prove a server accepts what it
says. This one talks to GreenMail -- a real IMAP4 + SMTP server in a container,
started by `tools/ci_odoo.sh` -- so the protocol itself is under test: the login
handshake, SEARCH, FETCH, APPEND, and a message that actually travels from one
mailbox to another.

It is what replaces the manual IMAP round trip in TESTPLAN.md phase A'3. What it
still cannot prove is delivery to the outside world: GreenMail accepts
everything and forwards nothing, so "does mail reach a real customer" remains a
question only a real hoster answers.

Skipped whole when PAN_TEST_IMAP_HOST is unset, so a run without the container
is green rather than broken. CI sets it; `tools/ci.sh test` therefore runs these
and a bare `--test-enable` locally does not.
"""
import imaplib
import os
import time
import unittest
import uuid

from odoo.tests import TransactionCase, tagged

from odoo.addons.pan_mail_pro.models.mail_provider_client import (
    FOLDER_INBOX,
    FOLDER_SENT,
    get_provider_client,
)

IMAP_HOST = os.environ.get('PAN_TEST_IMAP_HOST')
IMAP_PORT = int(os.environ.get('PAN_TEST_IMAP_PORT') or 3143)
SMTP_PORT = int(os.environ.get('PAN_TEST_SMTP_PORT') or 3025)

# Declared to GreenMail on the command line in tools/ci_odoo.sh. The login is
# the local part, not the address -- which is also the realistic case: plenty of
# hosters authenticate on a username that is not the mailbox address, and
# `_imap_login()` is what has to get that right.
DOMAIN = 'company.test'
SENDER_LOGIN, SENDER = 'alice', 'alice@company.test'
RECIPIENT_LOGIN, RECIPIENT = 'bob', 'bob@company.test'
PASSWORD = 'secret'


@tagged('pan_mail_pro', 'post_install', '-at_install')
@unittest.skipUnless(IMAP_HOST, 'PAN_TEST_IMAP_HOST is not set: no mail server to talk to')
class TestImapLive(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.client = get_provider_client(cls.env, 'imap')
        # Creating a mailbox is refused until internal domains exist. A domain
        # none of these addresses use, so the gate opens without turning the
        # fixture's own mail internal.
        cls.env['pan.mail.domain'].set_domains(['gate-fixture.test'])
        cls.sender = cls._account(SENDER, SENDER_LOGIN)
        cls.recipient = cls._account(RECIPIENT, RECIPIENT_LOGIN)
        cls.mailbox = cls.env['pan.mail.mailbox'].create({
            'email': SENDER, 'provider': 'imap',
        })
        # GreenMail starts with INBOX and nothing else. A real hoster has a Sent
        # folder already, so create one -- otherwise every APPEND would fail and
        # the sent-copy tests would pass for the wrong reason.
        cls._raw_imap(SENDER_LOGIN).create('Sent')

    @classmethod
    def _account(cls, email, login):
        return cls.env['pan.mail.account'].create({
            'email': email, 'provider': 'imap', 'user_id': False,
            'username': login, 'password': PASSWORD,
            'imap_host': IMAP_HOST, 'imap_port': IMAP_PORT, 'imap_security': 'none',
            'smtp_host': IMAP_HOST, 'smtp_port': SMTP_PORT, 'smtp_security': 'none',
        })

    @classmethod
    def _raw_imap(cls, login):
        """A connection made by the test itself, so a failing client cannot
        also break the fixture that is meant to check it."""
        conn = imaplib.IMAP4(IMAP_HOST, IMAP_PORT, timeout=30)
        conn.login(login, PASSWORD)
        cls.addClassCleanup(lambda c=conn: c.logout())
        return conn

    def _send(self, subject, body='<p>Hello there</p>', to=RECIPIENT):
        mail = self.env['mail.mail'].create({
            'subject': subject, 'body_html': body, 'email_to': to,
        })
        return self.client.send_message(mail, self.mailbox, self.sender)

    def _wait_for(self, account, subject, folder=FOLDER_INBOX, timeout=10):
        """Poll until a message with `subject` shows up, or give up.

        SMTP delivery is asynchronous on any server; a bare fetch right after
        the send is the classic way to write a test that passes on a fast
        machine and fails in CI.
        """
        deadline = time.monotonic() + timeout
        while True:
            messages = self.client.fetch_messages(account, self.mailbox, folder=folder)
            match = [m for m in messages if m['subject'] == subject]
            if match or time.monotonic() > deadline:
                return match[0] if match else None
            time.sleep(0.3)

    # ------------------------------------------------------------------ #
    # Credentials
    # ------------------------------------------------------------------ #
    def test_connection_checks_both_halves_against_a_real_server(self):
        result = self.client.test_connection(self.sender)
        self.assertTrue(result['success'], result.get('error'))
        self.assertEqual(result['email'], SENDER)

    def test_a_wrong_password_says_which_half_failed(self):
        """The whole point of checking both halves separately: an admin who
        mistypes a password must not be told 'connection failed'."""
        broken = self._account('nobody@%s' % DOMAIN, SENDER_LOGIN)
        broken.password = 'wrong'
        result = self.client.test_connection(broken)
        self.assertFalse(result['success'])
        self.assertTrue(result['error'].startswith('IMAP:'), result['error'])

    # ------------------------------------------------------------------ #
    # A message that really travels
    # ------------------------------------------------------------------ #
    def test_a_sent_message_arrives_and_reads_back_intact(self):
        subject = 'live-%s' % uuid.uuid4().hex[:8]
        result = self._send(subject)
        self.assertTrue(result['success'], result.get('error'))

        arrived = self._wait_for(self.recipient, subject)
        self.assertIsNotNone(arrived, 'the message never reached the recipient')
        self.assertEqual(arrived['message_id'], result['message_id'])
        self.assertEqual(arrived['from']['email'], SENDER)
        self.assertEqual([a['email'] for a in arrived['to']], [RECIPIENT])
        self.assertIsNotNone(arrived['date'])

    def test_the_message_reference_survives_a_round_trip(self):
        """`folder:uidvalidity:uid` is this provider's whole answer to "which
        message was that". It has to be usable to fetch the same one back."""
        subject = 'live-%s' % uuid.uuid4().hex[:8]
        self._send(subject)
        listed = self._wait_for(self.recipient, subject)
        self.assertIsNotNone(listed)

        folder, uidvalidity, uid = listed['provider_message_id'].split(':')
        self.assertEqual(folder, FOLDER_INBOX)
        self.assertTrue(uidvalidity.isdigit() and uid.isdigit())

        full = self.client.get_message(
            self.recipient, self.mailbox, listed['provider_message_id'])
        self.assertEqual(full['message_id'], listed['message_id'])
        self.assertIn('Hello there', full['body_html'])

    def test_the_sent_copy_is_filed_in_the_sent_folder(self):
        """SMTP files nothing by itself. Graph and Gmail do it for you, so this
        APPEND is the only reason a user's own mail client shows what Odoo
        sent."""
        subject = 'live-%s' % uuid.uuid4().hex[:8]
        self._send(subject)

        filed = self._wait_for(self.sender, subject, folder=FOLDER_SENT)
        self.assertIsNotNone(filed, 'no copy was filed in Sent')
        self.assertTrue(filed['is_read'], 'the copy should not arrive unread')

    def test_a_send_still_succeeds_when_the_sent_folder_is_missing(self):
        """Best-effort means best-effort: the mail is already delivered, so a
        server without the folder we guessed must not turn a successful send
        into a failure."""
        subject = 'live-%s' % uuid.uuid4().hex[:8]
        self.sender.imap_sent_folder = 'No-Such-Folder'
        result = self._send(subject)

        self.assertTrue(result['success'], result.get('error'))
        self.assertIsNotNone(self._wait_for(self.recipient, subject))
