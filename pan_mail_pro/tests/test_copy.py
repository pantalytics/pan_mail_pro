# -*- coding: utf-8 -*-
"""
A copy shows mail and sends none (pan_mail_pro#379).

The recommended order is staging first, then production, and staging is a
neutralized copy. There, outgoing mail is routed the way production would
route it and handed to Odoo's own mail server, so the host's catcher (Odoo.sh's
Mails tab, MailHog, Mailpit) shows it with the From, the threading and the
X-Odoo headers it would have carried. No provider client is asked. Incoming
mail is not read at all, and nothing is ever written to a live mailbox: no
mark read, no move, no draft. The settings page and the Inbox say so in one
line.

`tests/test_neutralized.py` holds the older half: the credentials are gone and
nothing can decrypt them. This file holds what a person testing on staging
sees.
"""
import imaplib
import os
import smtplib
from contextlib import ExitStack
from unittest.mock import MagicMock, patch

import requests

from odoo.exceptions import UserError
from odoo.tests import HttpCase, tagged

from ..models.mail_mail import ON_COPY
from ..models.mail_provider_client import PROVIDER_CLIENTS, get_provider_client
from ..models.neutralization import COPY_FROM_HEADER
from .common import MailProTestCase, send_and_capture

MARKER = 'This is a copy. Mail is shown, not sent.'
MODULE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def neutralize(env):
    """What Odoo does to a staging copy, Mail Pro's half included: the flag,
    then `data/neutralize.sql` (every mailbox archived, every credential
    dropped)."""
    env['ir.config_parameter'].sudo().set_param('database.is_neutralized', 'True')
    env.flush_all()
    with open(os.path.join(MODULE, 'data', 'neutralize.sql'), encoding='utf-8') as sql:
        env.cr.execute(sql.read())
    env.invalidate_all()


class Transports:
    """Every way out of this database a provider client has, watched.

    Graph and Gmail speak HTTP through `requests` (every verb ends in
    `Session.request`), the IMAP/SMTP client opens `imaplib` and `smtplib`.
    Each attempt is recorded and refused with the error a dead network gives,
    so a client that catches its own transport errors cannot hide one.
    """

    def __init__(self):
        self.calls = []

    def _refuse(self, kind):
        def refuse(*args, **kwargs):
            self.calls.append(kind)
            raise OSError('%s from a copy' % kind)
        return refuse

    def watch(self, stack):
        stack.enter_context(patch.object(
            requests.sessions.Session, 'request', side_effect=self._refuse('http')))
        stack.enter_context(patch.object(
            imaplib.IMAP4, '__init__', side_effect=self._refuse('imap')))
        stack.enter_context(patch.object(
            smtplib.SMTP, '__init__', side_effect=self._refuse('smtp')))
        return self


@tagged('pan_mail_pro', 'post_install', '-at_install')
class TestCopyOutgoing(MailProTestCase):
    """Outgoing on a copy: routed as here, built and sent by Odoo."""

    def setUp(self):
        super().setUp()
        neutralize(self.env)
        self.parent = self.env['mail.message'].sudo().create({
            'model': 'res.partner',
            'res_id': self.external_partner.id,
            'message_type': 'email',
            'message_id': '<question@customer.example.com>',
            'body': '<p>A question</p>',
            'email_from': 'customer@example.com',
        })

    def _mail(self, **values):
        vals = {
            'subject': 'Re: A question',
            'body_html': '<p>An answer</p>',
            'email_to': 'customer@example.com',
            'author_id': self.salesperson.partner_id.id,
            'model': 'res.partner',
            'res_id': self.external_partner.id,
        }
        vals.update(values)
        return self.env['mail.mail'].sudo().create(vals)

    def _watched(self):
        """Odoo's SMTP send, captured, and every provider door watched.

        `_disable_send` is Odoo's test-mode switch and `_connect__` its SMTP
        session: both stood in for, so the message Odoo built reaches
        `send_email` and is kept for the assertions.
        """
        stack = ExitStack()
        mail_server = type(self.env['ir.mail_server'])
        stack.enter_context(patch.object(mail_server, '_disable_send', return_value=False))
        stack.enter_context(patch.object(mail_server, '_connect__', return_value=MagicMock()))
        sent = []

        def send_email(server, message, *args, **kwargs):
            sent.append(message)
            return message['Message-Id']
        stack.enter_context(patch.object(mail_server, 'send_email', autospec=True,
                                         side_effect=send_email))
        provider_sends = []
        for code in PROVIDER_CLIENTS:
            client = type(get_provider_client(self.env, code))
            stack.enter_context(patch.object(
                client, 'send_message', autospec=True,
                side_effect=lambda *a, **k: provider_sends.append(a) or {'success': True}))
        transports = Transports().watch(stack)
        return stack, sent, provider_sends, transports

    def test_a_reply_reaches_the_catcher_as_the_mailbox_would_send_it(self):
        """The From is the mailbox, the reply threads, the loop guard is on.

        The salesperson's default is the shared mailbox, archived by the
        neutralization like every other: the copy still names it.
        """
        mail = self._mail()
        message = mail.mail_message_id
        stack, sent, provider_sends, transports = self._watched()
        with stack:
            error = send_and_capture(mail)

        self.assertIsNone(error)
        self.assertFalse(provider_sends, 'a copy asked a provider client to send')
        self.assertFalse(transports.calls, 'a copy opened a provider transport')
        self.assertEqual(len(sent), 1, "Odoo's own mail server did not get the mail")
        msg = sent[0]
        self.assertEqual(msg['From'], 'info@company.test')
        self.assertEqual(msg['Reply-To'], 'info@company.test')
        self.assertEqual(msg[COPY_FROM_HEADER], 'info@company.test')
        self.assertEqual(msg['In-Reply-To'], '<question@customer.example.com>')
        self.assertIn('<question@customer.example.com>', msg['References'])
        self.assertEqual(msg['Message-Id'], message.message_id,
                         "a copy's Message-ID is Odoo's own; no provider minted one")
        self.assertEqual(msg['X-Odoo-Model'], 'res.partner')
        self.assertEqual(msg['X-Odoo-Record-Id'], str(self.external_partner.id))
        self.assertEqual(msg['X-Odoo-Message-Id'], str(message.id))
        self.assertTrue(msg['X-Odoo-Db'])
        self.assertEqual(msg['To'], 'customer@example.com')

    def test_the_mailbox_a_person_chose_is_the_from(self):
        """Send From on the mail wins, archived or not."""
        mail = self._mail(x_send_from_mailbox_id=self.personal_mailbox.id)
        stack, sent, provider_sends, _transports = self._watched()
        with stack:
            send_and_capture(mail)
        self.assertFalse(provider_sends)
        self.assertEqual(sent[0]['From'], 'sales@company.test')

    def test_system_mail_leaves_from_the_notification_mailbox(self):
        """Account mail takes the notification route, archived or not, and
        no account is asked for: a copy has none."""
        mail = self._mail(message_type='user_notification', model=False, res_id=False,
                          subject='Your password', author_id=False)
        stack, sent, provider_sends, _transports = self._watched()
        with stack:
            send_and_capture(mail)
        self.assertFalse(provider_sends)
        self.assertEqual(sent[0]['From'], 'notifications@company.test')
        self.assertIsNone(sent[0]['In-Reply-To'])

    def test_the_chatter_keeps_its_own_from(self):
        """A notification shares its `mail.message` with the person who
        wrote it. The From is put on the built message, not on that row, so
        the chatter says what it said before."""
        message = self.external_partner.with_user(self.salesperson).sudo().with_context(
            mail_notify_force_send=False).message_post(
                body='<p>Testing the flow</p>', subject='Staging',
                partner_ids=[self.external_partner.id],
                message_type='comment', subtype_xmlid='mail.mt_comment')
        before = message.email_from
        mails = self.env['mail.mail'].sudo().search([('mail_message_id', '=', message.id)])
        self.assertTrue(mails, 'the post queued no mail')
        stack, sent, provider_sends, _transports = self._watched()
        with stack:
            mails.send()
        self.assertFalse(provider_sends)
        self.assertEqual(sent[0]['From'], 'info@company.test')
        self.assertEqual(message.email_from, before)

    def test_a_mail_production_would_refuse_is_refused_here_too(self):
        """A copy that sent what production refuses would test nothing. No
        default mailbox: the same sentence, and nothing reaches the catcher."""
        mail = self._mail(author_id=self.other_user.partner_id.id)
        stack, sent, provider_sends, _transports = self._watched()
        with stack:
            error = send_and_capture(mail)
        self.assertTrue(error, 'the person was not told')
        self.assertIn('default mailbox', str(error))
        self.assertFalse(sent)
        self.assertFalse(provider_sends)
        self.assertEqual(mail.state, 'exception')

    def test_odoos_own_mail_keeps_its_from(self):
        """The From is only swapped inside `_send_on_staging`, and only for a
        message that names a mailbox."""
        Server = self.env['ir.mail_server']
        build = dict(email_to=['b@example.com'], subject='s', body='b',
                     headers={COPY_FROM_HEADER: 'info@company.test'})
        self.assertEqual(Server._build_email__(email_from='odoo@company.test', **build)['From'],
                         'odoo@company.test')
        on_copy = Server.with_context(**{ON_COPY: True})
        self.assertEqual(on_copy._build_email__(email_from='odoo@company.test', **build)['From'],
                         'info@company.test')
        self.assertEqual(on_copy._build_email__(
            email_from='odoo@company.test', email_to=['b@example.com'], subject='s',
            body='b')['From'], 'odoo@company.test')


@tagged('pan_mail_pro', 'post_install', '-at_install')
class TestCopyIncoming(MailProTestCase):
    """Incoming on a copy: nothing is read, and nothing is written back.

    A dump that missed `data/neutralize.sql` still carries live credentials,
    so these put them back first: what holds is the flag, not their absence.
    """

    def setUp(self):
        super().setUp()
        self.env['ir.config_parameter'].sudo().set_param('database.is_neutralized', 'True')
        self.accounts = {
            code: self.env['pan.mail.account'].sudo().create({
                'email': f'{code}-copy@company.test', 'provider': code,
                'refresh_token': 'a-live-refresh-token', 'access_token': 'a-live-token',
                'password': 'a-live-password',
            }) for code in PROVIDER_CLIENTS
        }
        self.mail = self.env['mail.mail'].sudo().create({
            'subject': 'Draft', 'body_html': '<p>x</p>', 'email_to': 'customer@example.com'})

    WRITES = {
        'set_seen': lambda c, a, m, mail: c.set_seen(a, m, ['ID-1'], seen=True),
        'set_flagged': lambda c, a, m, mail: c.set_flagged(a, m, ['ID-1'], flagged=True),
        'move_messages': lambda c, a, m, mail: c.move_messages(a, m, ['ID-1'], 'archive'),
        'delete_messages': lambda c, a, m, mail: c.delete_messages(a, m, ['ID-1']),
        'create_folder': lambda c, a, m, mail: c.create_folder(a, m, 'Copy'),
        'rename_folder': lambda c, a, m, mail: c.rename_folder(a, m, 'Copy', 'Renamed'),
        'delete_folder': lambda c, a, m, mail: c.delete_folder(a, m, 'Copy'),
        'save_draft': lambda c, a, m, mail: c.save_draft(mail, m, a),
        'update_draft': lambda c, a, m, mail: c.update_draft(mail, m, a, 'ID-1'),
        'send_draft': lambda c, a, m, mail: c.send_draft(a, m, 'ID-1'),
    }

    def test_no_client_writes_to_a_live_mailbox(self):
        """Mark read, flag, move, delete, folders, drafts: every write the
        contract has, on every provider, stops before the network."""
        for code in PROVIDER_CLIENTS:
            client = get_provider_client(self.env, code)
            for name, write in self.WRITES.items():
                with self.subTest(provider=code, write=name), ExitStack() as stack:
                    transports = Transports().watch(stack)
                    try:
                        write(client, self.accounts[code], self.shared_mailbox, self.mail)
                    except UserError:
                        pass
                    self.assertFalse(transports.calls, '%s reached %s' % (name, transports.calls))

    def test_the_inbox_does_not_mark_read_at_the_provider(self):
        """Odoo's read mark stays in Odoo: the push to the provider asks for
        working credentials first, and a copy has none it may use."""
        message = self.env['mail.message'].sudo().create({
            'model': 'res.partner', 'res_id': self.external_partner.id,
            'message_type': 'email', 'body': '<p>x</p>',
            'x_provider_message_id': 'ID-1', 'x_mailbox_id': self.shared_mailbox.id,
        })
        with ExitStack() as stack:
            transports = Transports().watch(stack)
            pushed = self.shared_mailbox.push_read_state(message, read=True)
        self.assertEqual(pushed, 0)
        self.assertFalse(transports.calls)

    def test_the_sync_reads_nothing(self):
        """Mailboxes switched back on, credentials back: the cron still
        returns before it opens any of them."""
        self.env['pan.mail.mailbox'].sudo().with_context(active_test=False).search(
            []).write({'active': True})
        Fetcher = type(self.env['pan.mail.fetcher'])
        with ExitStack() as stack:
            transports = Transports().watch(stack)
            process = stack.enter_context(patch.object(Fetcher, '_process_mailbox', autospec=True))
            self.env['pan.mail.fetcher']._cron_fetch_incoming_mail()
        process.assert_not_called()
        self.assertFalse(transports.calls)


@tagged('pan_mail_pro', 'post_install', '-at_install')
class TestCopyMarker(MailProTestCase):
    """One line, on the settings page and in the Inbox. No switch."""

    def test_the_settings_page_says_it_on_a_copy_only(self):
        Settings = self.env['res.config.settings']
        self.assertFalse(Settings.create({}).x_is_copy)
        self.env['ir.config_parameter'].sudo().set_param('database.is_neutralized', 'True')
        self.assertTrue(Settings.create({}).x_is_copy)

        arch = self.env.ref('pan_mail_pro.res_config_settings_view_form').arch
        self.assertIn(MARKER, arch)
        self.assertIn('invisible="not x_is_copy"', arch)

    def test_the_inbox_says_it_whatever_state_it_is_in(self):
        """Above the gate and the panes alike, read off the session."""
        with open(os.path.join(MODULE, 'static', 'src', 'xml', 'conversation_view.xml'),
                  encoding='utf-8') as handle:
            template = handle.read()
        marker = template.index(MARKER)
        self.assertIn('t-if="isCopy"', template[template.rindex('<div', 0, marker):marker])
        self.assertLess(marker, template.index('o_mailpro_gate'),
                        'the line has to come before the gate, or a copy that is not '
                        'connected never shows it')
        with open(os.path.join(MODULE, 'static', 'src', 'js', 'conversation_view',
                               'conversation_view.js'), encoding='utf-8') as handle:
            self.assertIn('session.pan_mail_copy', handle.read())


@tagged('pan_mail_pro', 'post_install', '-at_install')
class TestCopySession(HttpCase):
    """The Inbox reads the copy from the session, before its first paint."""

    def test_the_session_says_whether_this_is_a_copy(self):
        self.authenticate('admin', 'admin')
        info = self.make_jsonrpc_request('/web/session/get_session_info', {})
        self.assertIs(info['pan_mail_copy'], False)

        self.env['ir.config_parameter'].sudo().set_param('database.is_neutralized', 'True')
        info = self.make_jsonrpc_request('/web/session/get_session_info', {})
        self.assertIs(info['pan_mail_copy'], True)
