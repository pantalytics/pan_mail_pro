# -*- coding: utf-8 -*-
"""The access check: what a mailbox is, and what a sign-in may do with it.

Three providers, one table. Each provider's `inspect_mailbox` is driven with
a faked transport through the responses in `docs/research/provider-probes.md`,
and the table (`pan.mail.mailbox.access`) is asserted to end up saying what
the provider said. The health rungs and the sentences that read the table
are in `test_mailbox_health.py`; this file is the evidence they read.

Every fixture response is a string from the research doc. A cell marked
unverified there is a cell here too: when the real matrix is run, the
fixture changes with it and these tests say what else does.
"""
import smtplib
from unittest.mock import MagicMock, patch

import requests

from odoo.tests import TransactionCase, tagged

from odoo.addons.pan_mail_pro.models.mail_provider_client import (
    ERROR_ACCESS_DENIED,
    PROVIDER_CLIENTS,
    access_shape,
    get_provider_client,
)

from .common import MailProTestCase

GRAPH = 'odoo.addons.pan_mail_pro.models.providers.microsoft.graph_client'
GMAIL = 'odoo.addons.pan_mail_pro.models.providers.google.gmail_client'
IMAP = 'odoo.addons.pan_mail_pro.models.providers.imap_smtp.imap_client'
GRAPH_CLIENT = f'{GRAPH}.MicrosoftGraphClient'
GMAIL_CLIENT = f'{GMAIL}.GoogleGmailClient'
ALL_SCOPES = 'openid Mail.ReadWrite Mail.Send MailboxSettings.Read User.ReadBasic.All'


def graph_response(status, payload=None):
    """A requests response, raising on a 4xx/5xx the way `raise_for_status` does."""
    response = MagicMock()
    response.status_code = status
    response.json.return_value = payload if payload is not None else {}
    if status >= 400:
        error = requests.exceptions.HTTPError(f'{status} Client Error')
        error.response = response
        response.raise_for_status.side_effect = error
    else:
        response.raise_for_status.return_value = None
    return response


def graph_refusal(code, status=403):
    return graph_response(status, {'error': {'code': code, 'message': 'refused'}})


class GraphFake:
    """Answers the ladder's three GETs by path suffix."""

    def __init__(self, inbox, purpose=None, directory=None):
        self.inbox, self.purpose, self.directory = inbox, purpose, directory
        self.paths = []

    def __call__(self, method, url, headers, timeout=30, idempotent=True, **kwargs):
        self.paths.append(url)
        if '/mailFolders/inbox' in url:
            return self.inbox
        if '/mailboxSettings/userPurpose' in url:
            return self.purpose or graph_response(500)
        if '$select=mail,userPrincipalName' in url:
            return self.directory or graph_response(500)
        raise AssertionError(f'unexpected probe {url}')


@tagged('pan_mail_pro', 'post_install', '-at_install')
class TestContract(TransactionCase):

    def test_access_shape_refuses_what_is_not_in_the_vocabulary(self):
        with self.assertRaises(ValueError):
            access_shape(kind='mailbox')
        with self.assertRaises(ValueError):
            access_shape(can_read='maybe')
        self.assertEqual(access_shape()['can_send'], 'unknown')

    def test_every_client_implements_inspect_mailbox(self):
        contract = self.env['mail.provider.client']
        for code in PROVIDER_CLIENTS:
            self.assertIsNot(
                getattr(type(get_provider_client(self.env, code)), 'inspect_mailbox'),
                getattr(type(contract), 'inspect_mailbox'),
                f"'{code}' does not implement inspect_mailbox()")


@tagged('pan_mail_pro', 'post_install', '-at_install')
class TestMicrosoftLadder(TransactionCase):
    """docs/research/provider-probes.md, the Microsoft 365 tables."""

    def setUp(self):
        super().setUp()
        self.client = get_provider_client(self.env, 'outlook')
        self.user = self.env['res.users'].create({
            'name': 'Robert', 'login': 'robert@stalero.test', 'email': 'robert@stalero.test'})
        self.account = self.env['pan.mail.account'].create({
            'email': 'robert@stalero.test', 'provider': 'outlook', 'user_id': self.user.id,
            'refresh_token': 'r', 'access_token': 'a', 'identity_name': 'Robert van Laar',
            'granted_scopes': ALL_SCOPES})

    def _inspect(self, fake, address='info@emovr.test'):
        with patch.object(type(self.client), 'get_valid_token', return_value='t'), \
                patch.object(type(self.client), '_request_with_retry', side_effect=fake):
            return self.client.inspect_mailbox(self.account, address)

    def test_a_user_mailbox_the_sign_in_can_read(self):
        fake = GraphFake(graph_response(200, {'id': 'x'}),
                         graph_response(200, {'value': 'user'}),
                         graph_response(200, {'mail': 'info@emovr.test'}))
        answer = self._inspect(fake)
        self.assertEqual((answer['kind'], answer['can_read'], answer['can_send']),
                         ('user', 'yes', 'unknown'))
        self.assertIsNone(answer['error'])
        self.assertEqual(len(fake.paths), 3)

    def test_a_shared_mailbox(self):
        fake = GraphFake(graph_response(200, {'id': 'x'}),
                         graph_response(200, {'value': 'shared'}),
                         graph_response(200, {'mail': 'info@emovr.test'}))
        self.assertEqual(self._inspect(fake)['kind'], 'shared')

    def test_no_full_access_is_a_refusal_naming_the_sign_in(self):
        """The Emovr line: the store's 404 is the same missing Full Access as
        the permission check's 403, and both name the sign-in, not the user."""
        for code, status in (('ErrorAccessDenied', 403), ('ErrorItemNotFound', 404)):
            with self.subTest(code=code):
                answer = self._inspect(GraphFake(graph_refusal(code, status)))
                self.assertEqual(answer['can_read'], 'no')
                self.assertIn('Robert van Laar (robert@stalero.test) cannot read '
                              'info@emovr.test', answer['error'])
                self.assertIn('Full Access', answer['error'])

    def test_no_mailbox_at_the_address_stops_the_ladder(self):
        fake = GraphFake(graph_refusal('ErrorInvalidUser', 404))
        answer = self._inspect(fake, 'info@ticomit.test')
        self.assertEqual((answer['kind'], answer['can_read'], answer['can_send']),
                         ('none', 'no', 'no'))
        self.assertIn('no mailbox at info@ticomit.test', answer['error'])
        self.assertEqual(len(fake.paths), 1)

    def test_a_room_is_not_an_address_mail_leaves_from(self):
        fake = GraphFake(graph_response(200, {'id': 'x'}),
                         graph_response(200, {'value': 'room'}))
        answer = self._inspect(fake)
        self.assertEqual((answer['kind'], answer['can_send']), ('resource', 'no'))

    def test_an_alias_is_told_from_a_mailbox_by_the_directory(self):
        """Exchange resolves a proxy address; the directory resolves only
        principals. Both answered, the address is an alias."""
        fake = GraphFake(graph_response(200, {'id': 'x'}),
                         graph_response(200, {'value': 'user'}),
                         graph_response(404))
        answer = self._inspect(fake, 'sales@emovr.test')
        self.assertEqual(answer['kind'], 'alias')
        self.assertIn('alias on another mailbox', answer['error'])

    def test_a_grant_without_the_scopes_reads_and_asks_to_reconnect(self):
        """An older grant answers the question that matters (can it read)
        and is not sent on a probe that would 403 for want of consent."""
        self.account.granted_scopes = 'openid Mail.ReadWrite Mail.Send'
        fake = GraphFake(graph_response(200, {'id': 'x'}))
        answer = self._inspect(fake)
        self.assertEqual((answer['kind'], answer['can_read']), ('unknown', 'yes'))
        self.assertTrue(answer['needs_reconnect'])
        self.assertIn('Reconnect', answer['error'])
        self.assertEqual(len(fake.paths), 1)

    def test_an_unexpected_answer_is_kept_in_the_providers_words(self):
        answer = self._inspect(GraphFake(graph_refusal('ErrorServerBusy', 503)))
        self.assertEqual(answer['can_read'], 'unknown')
        self.assertIn('503', answer['error'])
        self.assertIn('ErrorServerBusy', answer['error'])

    def test_a_denied_send_carries_the_rights_code(self):
        """What `mail.mail` branches on to write the access row."""
        mailbox = self.env['pan.mail.mailbox']
        self.env['pan.mail.domain'].set_domains(['gate-fixture.test'])
        mailbox = mailbox.create({'email': 'info@emovr.test'})
        mail = self.env['mail.mail'].create({
            'subject': 'Hello', 'body_html': '<p>Hi</p>', 'email_to': 'to@example.com',
            'author_id': self.user.partner_id.id})
        for code in ('ErrorSendAsDenied', 'ErrorAccessDenied', 'ErrorItemNotFound'):
            with self.subTest(code=code), \
                    patch.object(type(self.client), 'get_valid_token', return_value='t'), \
                    patch(f'{GRAPH}.requests.post', return_value=graph_refusal(code)):
                result = self.client.send_email_via_graph(mail, mailbox, self.account)
            self.assertEqual(result['error_code'], ERROR_ACCESS_DENIED)
            self.assertIn('robert@stalero.test cannot send from info@emovr.test',
                          result['error'])


@tagged('pan_mail_pro', 'post_install', '-at_install')
class TestGmailSendAs(TransactionCase):
    """docs/research/provider-probes.md, the Google tables."""

    def setUp(self):
        super().setUp()
        self.client = get_provider_client(self.env, 'gmail')
        self.account = self.env['pan.mail.account'].create({
            'email': 'sales@workspace.test', 'provider': 'gmail',
            'refresh_token': 'r', 'access_token': 'a'})

    def _inspect(self, send_as, address='sales@workspace.test', own='sales@workspace.test'):
        def fake_get(account, url, params=None):
            if url.endswith('/profile'):
                return {'emailAddress': own}
            if url.endswith('/settings/sendAs'):
                return {'sendAs': send_as}
            raise AssertionError(url)
        with patch.object(type(self.client), 'get_valid_token', return_value='t'), \
                patch.object(type(self.client), '_api_get', side_effect=fake_get):
            return self.client.inspect_mailbox(self.account, address)

    def test_the_primary_address_is_a_user_that_may_send(self):
        answer = self._inspect([{'sendAsEmail': 'sales@workspace.test', 'isPrimary': True}])
        self.assertEqual((answer['kind'], answer['can_read'], answer['can_send']),
                         ('user', 'yes', 'yes'))

    def test_a_verified_alias_may_send(self):
        answer = self._inspect(
            [{'sendAsEmail': 'sales@workspace.test', 'isPrimary': True},
             {'sendAsEmail': 'info@workspace.test', 'verificationStatus': 'accepted'}],
            address='info@workspace.test')
        self.assertEqual((answer['kind'], answer['can_send']), ('alias', 'yes'))

    def test_a_pending_alias_may_not(self):
        answer = self._inspect(
            [{'sendAsEmail': 'info@workspace.test', 'verificationStatus': 'pending'}],
            address='info@workspace.test')
        self.assertEqual((answer['kind'], answer['can_send']), ('alias', 'no'))
        self.assertIn('not verified yet', answer['error'])

    def test_an_address_the_sign_in_has_no_send_as_for(self):
        """The Gmail shape of the Emovr mistake: the admin signed in as
        themselves and filed the team address as a shared mailbox."""
        answer = self._inspect(
            [{'sendAsEmail': 'sales@workspace.test', 'isPrimary': True}],
            address='info@workspace.test')
        self.assertEqual((answer['kind'], answer['can_read'], answer['can_send']),
                         ('unknown', 'no', 'no'))
        self.assertIn('signed in as sales@workspace.test', answer['error'])

    def test_a_delegation_denied_send_carries_the_rights_code(self):
        self.env['pan.mail.domain'].set_domains(['gate-fixture.test'])
        mailbox = self.env['pan.mail.mailbox'].create(
            {'email': 'info@workspace.test', 'provider': 'gmail'})
        mail = self.env['mail.mail'].create({
            'subject': 'Hello', 'body_html': '<p>Hi</p>', 'email_to': 'to@example.com'})
        refusal = graph_response(403, {'error': {
            'code': 403, 'message': 'Delegation denied for sales@workspace.test',
            'status': 'PERMISSION_DENIED'}})
        with patch.object(type(self.client), 'get_valid_token', return_value='t'), \
                patch(f'{GMAIL}.requests.post', return_value=refusal):
            result = self.client.send_message(mail, mailbox, self.account)
        self.assertEqual(result['error_code'], ERROR_ACCESS_DENIED)
        self.assertIn('sales@workspace.test may not send as info@workspace.test',
                      result['error'])


class FakeImap:
    def __init__(self, select_ok=True):
        self.select_ok = select_ok

    def login(self, user, password):
        return ('OK', [b'Logged in'])

    def select(self, name, readonly=False):
        if not self.select_ok:
            import imaplib
            raise imaplib.IMAP4.error('SELECT failed: no such mailbox')
        return ('OK', [b'1'])

    def logout(self):
        return ('BYE', [b''])


class FakeSmtp:
    def __init__(self, mail_code=250, line=b'OK'):
        self.mail_code, self.line = mail_code, line
        self.reset = False

    def login(self, user, password):
        pass

    def mail(self, sender, options=()):
        self.sender = sender
        return self.mail_code, self.line

    def rset(self):
        self.reset = True

    def quit(self):
        pass


@tagged('pan_mail_pro', 'post_install', '-at_install')
class TestImapProbe(TransactionCase):
    """docs/research/provider-probes.md, the IMAP/SMTP table."""

    def setUp(self):
        super().setUp()
        self.client = get_provider_client(self.env, 'imap')
        self.account = self.env['pan.mail.account'].create({
            'email': 'info@company.test', 'provider': 'imap',
            'imap_host': 'imap.test', 'smtp_host': 'smtp.test', 'password': 'hunter2'})

    def _inspect(self, imap, smtp):
        with patch(f'{IMAP}.imaplib.IMAP4_SSL', return_value=imap), \
                patch(f'{IMAP}.smtplib.SMTP_SSL', return_value=smtp):
            return self.client.inspect_mailbox(self.account, 'info@company.test')

    def test_a_login_that_opens_the_inbox_is_an_account_that_reads(self):
        smtp = FakeSmtp()
        answer = self._inspect(FakeImap(), smtp)
        self.assertEqual((answer['kind'], answer['can_read'], answer['can_send']),
                         ('account', 'yes', 'unknown'))
        self.assertEqual(smtp.sender, 'info@company.test')
        self.assertTrue(smtp.reset, 'the envelope probe is reset, never sent')

    def test_a_refused_mail_from_is_a_no_with_the_servers_line(self):
        answer = self._inspect(FakeImap(), FakeSmtp(
            550, b'5.7.60 SMTP; Client does not have permissions to send as this sender'))
        self.assertEqual(answer['can_send'], 'no')
        self.assertIn('5.7.60', answer['error'])

    def test_a_failed_select_is_a_no_on_reading(self):
        answer = self._inspect(FakeImap(select_ok=False), FakeSmtp())
        self.assertEqual(answer['can_read'], 'no')
        self.assertIn('IMAP:', answer['error'])

    def test_a_refused_sender_on_a_real_send_carries_the_rights_code(self):
        self.env['pan.mail.domain'].set_domains(['gate-fixture.test'])
        mailbox = self.env['pan.mail.mailbox'].create(
            {'email': 'info@company.test', 'provider': 'imap'})
        mail = self.env['mail.mail'].create({
            'subject': 'Hello', 'body_html': '<p>Hi</p>', 'email_to': 'to@example.com'})
        smtp = MagicMock()
        smtp.send_message.side_effect = smtplib.SMTPSenderRefused(
            550, b'5.7.60 Client does not have permissions to send as this sender',
            'info@company.test')
        with patch(f'{IMAP}.smtplib.SMTP_SSL', return_value=smtp):
            result = self.client.send_message(mail, mailbox, self.account)
        self.assertEqual(result['error_code'], ERROR_ACCESS_DENIED)
        self.assertIn('may not send as info@company.test', result['error'])


@tagged('pan_mail_pro', 'post_install', '-at_install')
class TestAccessTable(MailProTestCase):
    """The table the screens read, written by the check and by the send."""

    def _answer(self, **values):
        return access_shape(**values)

    def test_the_check_writes_the_kind_and_the_row(self):
        mailbox = self.shared_mailbox
        account = self.salesperson.x_pan_mail_account_ids
        answer = self._answer(kind='shared', can_read='yes')
        with patch(f'{GRAPH_CLIENT}.inspect_mailbox', return_value=answer):
            row = mailbox._verify_access(account)
        self.assertEqual(mailbox.address_kind, 'shared')
        self.assertTrue(mailbox.access_checked_date)
        self.assertEqual((row.can_read, row.can_send), ('yes', 'unknown'))
        self.assertTrue(account.verified_date)
        self.assertEqual(len(mailbox.access_ids), 1)

    def test_a_refusal_is_a_ledger_row_naming_the_pair(self):
        mailbox = self.shared_mailbox
        account = self.salesperson.x_pan_mail_account_ids
        answer = self._answer(can_read='no', error='sales@company.test cannot read info@company.test.')
        with patch(f'{GRAPH_CLIENT}.inspect_mailbox', return_value=answer):
            mailbox._verify_access(account)
        error = self.env['pan.mail.error'].search([('code', '=', 'access.read_denied')])
        self.assertEqual((error.mailbox_id, error.account_id), (mailbox, account))
        self.assertEqual(mailbox.address_kind, 'unknown', 'a refusal learned nothing about the kind')

    def test_a_refusal_does_not_forget_a_kind_found_earlier(self):
        mailbox = self.shared_mailbox
        account = self.salesperson.x_pan_mail_account_ids
        mailbox.sudo().write({'address_kind': 'user'})
        with patch(f'{GRAPH_CLIENT}.inspect_mailbox', return_value=self._answer(can_read='no')):
            mailbox._verify_access(account)
        self.assertEqual(mailbox.address_kind, 'user')

    def test_a_check_that_raises_records_and_keeps_the_previous_answer(self):
        mailbox = self.shared_mailbox
        account = self.salesperson.x_pan_mail_account_ids
        with patch(f'{GRAPH_CLIENT}.inspect_mailbox', return_value=self._answer(can_read='yes')):
            mailbox._verify_access(account)
        with patch(f'{GRAPH_CLIENT}.inspect_mailbox', side_effect=Exception('network')):
            self.assertIsNone(mailbox._verify_access(account))
        row = self.env['pan.mail.mailbox.access'].for_pair(mailbox, account)
        self.assertEqual(row.can_read, 'yes')
        self.assertTrue(self.env['pan.mail.error'].search([('code', '=', 'access.check_failed')]))

    def test_a_delivered_mail_says_yes_and_a_refused_one_says_no(self):
        Access = self.env['pan.mail.mailbox.access']
        mailbox = self.shared_mailbox
        account = self.salesperson.x_pan_mail_account_ids
        Access.note_send(mailbox, account, True)
        row = Access.for_pair(mailbox, account)
        self.assertEqual((row.can_read, row.can_send), ('yes', 'yes'))
        Access.note_send(mailbox, account, False, error='refused')
        self.assertEqual((row.can_send, row.error), ('no', 'refused'))
        self.assertEqual(row.can_read, 'yes', 'a refused send says nothing about reading')

    def test_the_check_leaves_what_a_send_proved(self):
        """Microsoft cannot answer Send As; the check's `unknown` must not
        erase the `yes` a delivered mail wrote."""
        Access = self.env['pan.mail.mailbox.access']
        mailbox = self.shared_mailbox
        account = self.salesperson.x_pan_mail_account_ids
        Access.note_send(mailbox, account, True)
        with patch(f'{GRAPH_CLIENT}.inspect_mailbox', return_value=self._answer(can_read='yes')):
            mailbox._verify_access(account)
        self.assertEqual(Access.for_pair(mailbox, account).can_send, 'yes')

    def test_the_consent_callback_checks_every_mailbox_the_sign_in_serves(self):
        """The owner's mailboxes, and a shared one this sign-in has sent through."""
        Access = self.env['pan.mail.mailbox.access']
        account = self.notif_owner.x_pan_mail_account_ids
        Access.note_send(self.shared_mailbox, account, False, error='x')
        with patch(f'{GRAPH_CLIENT}.inspect_mailbox',
                   return_value=self._answer(kind='user', can_read='yes')) as inspect:
            self.env['pan.mail.mailbox']._verify_for_account(account)
        checked = {call.args[1] for call in inspect.call_args_list}
        self.assertEqual(checked, {self.notification_mailbox.email, self.shared_mailbox.email})

    def test_the_cron_checks_once_an_hour(self):
        mailbox = self.personal_mailbox
        self.assertTrue(mailbox._access_check_due())
        with patch(f'{GRAPH_CLIENT}.inspect_mailbox', return_value=self._answer(can_read='yes')):
            mailbox._verify_access()
        self.assertFalse(mailbox._access_check_due())

    def test_a_plain_user_reads_their_own_rows_and_a_colleagues_not(self):
        Access = self.env['pan.mail.mailbox.access']
        mine = Access.note_send(self.shared_mailbox, self.salesperson.x_pan_mail_account_ids, True)
        theirs = Access.note_send(self.shared_mailbox, self.other_user.x_pan_mail_account_ids, True)
        visible = Access.with_user(self.salesperson).search([])
        self.assertIn(mine, visible)
        self.assertNotIn(theirs, visible)
