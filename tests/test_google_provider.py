# -*- coding: utf-8 -*-
"""Gmail client: dispatch, credentials, sending, incoming and the token lifecycle.

`google.gmail.client` is the second implementation of the `mail.provider.client`
contract, so most of what is asserted here is that it behaves like the Graph one
where the contract says it must, and differs only where providers genuinely
differ — chiefly that a Gmail shared mailbox is its own Workspace account rather
than a SendAs on somebody else's token.

The HTTP is mocked at the requests boundary, the same way common.py mocks Graph.
No real network, no real Google client.
"""
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch

import requests

from odoo.exceptions import UserError
from odoo.tests import TransactionCase, tagged

from odoo.addons.pan_mail_pro.models.mail_provider_client import (
    FOLDER_INBOX,
    get_provider_client,
)

# Patch requests.post specifically, not the whole module — the client catches
# requests.exceptions.RequestException, which must stay a real class.
GMAIL_POST = 'odoo.addons.pan_mail_pro.models.providers.google.gmail_client.requests.post'


@tagged('pan_mail_pro', 'post_install', '-at_install')
class TestGoogleProvider(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Account = cls.env['pan.mail.account']
        cls.client = get_provider_client(cls.env, 'gmail')
        cls.user = cls.env['res.users'].create({
            'name': 'Gmail User', 'login': 'gmail_user@test.local', 'email': 'gmail_user@test.local',
        })
        cls.env['ir.config_parameter'].sudo().set_param(
            'x_pan_outlook_pro.google_client_id', 'test-client-id.apps.googleusercontent.com')

    def _google_account(self, **vals):
        base = {'email': 'gmail_user@test.local', 'provider': 'gmail', 'user_id': self.user.id}
        base.update(vals)
        return self.Account.create(base)

    # ------------------------------------------------------------------ #
    # Dispatch
    # ------------------------------------------------------------------ #
    def test_gmail_mailbox_dispatches_to_the_gmail_client(self):
        mailbox = self.env['x_microsoft.mailbox'].create({
            'email': 'team@test.local',
            'x_provider': 'gmail', 'x_mailbox_type': 'shared',
        })
        self.assertEqual(mailbox._get_client()._name, 'google.gmail.client')
        self.assertEqual(mailbox._get_client().provider_code(), 'gmail')

    def test_gmail_supports_the_three_types_but_not_send_as(self):
        # 'shared' works, but as a service account — not by lending the author's
        # token the way Microsoft's SendAs does.
        self.assertEqual(set(self.client.supported_mailbox_types),
                         {'personal', 'shared', 'notification'})
        self.assertFalse(self.client.supports_shared_mailbox)
        self.assertTrue(self.client.supports_delegation)

    # ------------------------------------------------------------------ #
    # Credentials
    # ------------------------------------------------------------------ #
    def test_account_for_user_finds_the_gmail_account(self):
        account = self._google_account()
        self.assertEqual(self.client.account_for_user(self.user), account)

    def test_user_can_hold_microsoft_and_google_accounts(self):
        """Different providers, one user — UNIQUE(user_id, provider) allows it.

        This is the whole reason credentials moved off res.users: a user
        connecting both Microsoft and Google needs somewhere for the second.
        """
        ms = self.Account.create({
            'email': 'gmail_user@test.local', 'provider': 'outlook', 'user_id': self.user.id})
        google = self._google_account()
        self.assertNotEqual(ms, google)
        self.assertEqual(self.client.account_for_user(self.user), google)

    def test_shared_mailbox_sends_with_a_service_account(self):
        """A Gmail shared mailbox is its own account: user_id null, keyed on the
        address. Not the author's token (that is the Microsoft SendAs model)."""
        mailbox = self.env['x_microsoft.mailbox'].create({
            'email': 'sales@test.local',
            'x_provider': 'gmail', 'x_mailbox_type': 'shared',
        })
        service = self.Account.create({
            'email': 'sales@test.local', 'provider': 'gmail', 'user_id': False,
            'refresh_token': 'service-refresh',
        })
        # Even with an author who has their own Gmail account, a shared mailbox
        # resolves to the service account — the author is irrelevant here.
        self._google_account(refresh_token='personal-refresh')

        resolved = mailbox._get_client().resolve_sending_account(
            mailbox, author_user=self.user)
        self.assertEqual(resolved, service)
        self.assertFalse(resolved.user_id)

    def test_personal_mailbox_sends_with_the_owner_account(self):
        account = self._google_account(refresh_token='owner-refresh')
        mailbox = self.env['x_microsoft.mailbox'].create({
            'email': 'gmail_user@test.local',
            'x_provider': 'gmail', 'x_mailbox_type': 'personal',
            'x_owner_user_id': self.user.id,
        })
        self.assertEqual(
            mailbox._get_client().resolve_sending_account(mailbox), account)
        self.assertEqual(
            mailbox._get_client().resolve_receiving_account(mailbox), account)

    # ------------------------------------------------------------------ #
    # OAuth flow — connect, store, connected flag, disconnect
    # ------------------------------------------------------------------ #
    def test_connect_stores_state_and_returns_consent_url(self):
        action = self.user.action_connect_google()
        self.assertEqual(action['type'], 'ir.actions.act_url')
        self.assertIn('accounts.google.com', action['url'])
        # The state in the URL must match what was stored, or the callback rejects it.
        self.assertTrue(self.user.sudo().x_google_oauth_state)
        self.assertIn(f"state={self.user.sudo().x_google_oauth_state}", action['url'])

    def test_store_tokens_creates_then_updates_one_account(self):
        Account = self.Account
        acc = Account._store_tokens(
            'gmail', self.user, 'gmail_user@test.local', 'AT1', 'RT1',
            datetime.now() + timedelta(hours=1))
        self.assertEqual(acc.provider, 'gmail')
        self.assertEqual(acc.refresh_token, 'RT1')

        # Re-authorizing: Google omits the refresh token, and the same account
        # must be reused, not duplicated.
        again = Account._store_tokens(
            'gmail', self.user, 'gmail_user@test.local', 'AT2', None,
            datetime.now() + timedelta(hours=1))
        self.assertEqual(again, acc)
        again.invalidate_recordset()
        self.assertEqual(again.access_token, 'AT2')
        self.assertEqual(again.refresh_token, 'RT1')  # preserved
        self.assertEqual(
            Account.search_count([('user_id', '=', self.user.id), ('provider', '=', 'gmail')]), 1)

    def test_google_connected_flag_is_independent_of_microsoft(self):
        self.assertFalse(self.user.x_google_oauth_connected)
        # A Microsoft account must not flip the Google flag.
        self.Account.create({
            'email': 'gmail_user@test.local', 'provider': 'outlook',
            'user_id': self.user.id, 'refresh_token': 'ms'})
        self.assertFalse(self.user.x_google_oauth_connected)

        self._google_account(refresh_token='goog')
        self.assertTrue(self.user.x_google_oauth_connected)

    def test_disconnect_google_clears_the_account(self):
        self._google_account(access_token='a', refresh_token='r')
        self.user.action_disconnect_google()

        account = self.Account.search([('user_id', '=', self.user.id), ('provider', '=', 'gmail')])
        self.assertFalse(account.refresh_token_encrypted)
        self.assertFalse(self.user.x_google_oauth_connected)

    # ------------------------------------------------------------------ #
    # Sending
    # ------------------------------------------------------------------ #
    def _sendable(self):
        """A google shared mailbox + its live service account, ready to send."""
        mailbox = self.env['x_microsoft.mailbox'].create({
            'email': 'sales@test.local', 'x_provider': 'gmail', 'x_mailbox_type': 'shared',
        })
        account = self.Account.create({
            'email': 'sales@test.local', 'provider': 'gmail', 'user_id': False,
            'access_token': 'live-token', 'refresh_token': 'r',
            'token_expiry': datetime.now() + timedelta(hours=1),
        })
        return mailbox, account

    def _capture_send(self):
        """Patch the Gmail send endpoint, returning (patcher_cm, captured)."""
        captured = {}

        def _fake_post(url, headers=None, json=None, timeout=None, **kw):
            captured['url'] = url
            captured['json'] = json
            resp = MagicMock()
            resp.raise_for_status.return_value = None
            resp.json.return_value = {'id': 'gmail-id-1', 'threadId': 'thread-1'}
            return resp

        return patch(GMAIL_POST, side_effect=_fake_post), captured

    def _decode_raw(self, captured):
        import base64
        from email import message_from_bytes, policy
        raw = base64.urlsafe_b64decode(captured['json']['raw'])
        # policy.default gives the modern EmailMessage API (get_body/get_content).
        return message_from_bytes(raw, policy=policy.default)

    def test_send_builds_message_and_returns_ids(self):
        mailbox, account = self._sendable()
        mail = self.env['mail.mail'].create({
            'subject': 'Hello', 'body_html': '<p>Hi there</p>',
            'email_to': 'customer@example.com',
        })
        cm, captured = self._capture_send()
        with cm:
            result = mailbox._get_client().send_message(mail, mailbox, account)

        self.assertTrue(result['success'])
        self.assertEqual(result['thread_id'], 'thread-1')
        # The Message-ID we return is the one we set on the MIME, not Gmail's id,
        # and it is domain-anchored to the sending mailbox.
        self.assertTrue(result['message_id'].startswith('<'))
        self.assertIn('@test.local>', result['message_id'])
        self.assertTrue(captured['url'].endswith('/messages/send'))

        mime = self._decode_raw(captured)
        self.assertEqual(mime['To'], 'customer@example.com')
        self.assertEqual(mime['From'], 'sales@test.local')
        self.assertEqual(mime['Subject'], 'Hello')
        self.assertEqual(mime['Message-ID'], result['message_id'])
        self.assertIn('Hi there', mime.get_body(('html',)).get_content())

    def test_send_sets_the_odoo_loop_guard_headers(self):
        """Outgoing mail must carry X-Odoo-* so the incoming sync skips it and
        never re-imports our own sent messages."""
        mailbox, account = self._sendable()
        partner = self.env['res.partner'].create({'name': 'C', 'email': 'c@example.com'})
        mail = self.env['mail.mail'].create({
            'subject': 'x', 'body_html': '<p>x</p>', 'email_to': 'c@example.com',
            'model': 'res.partner', 'res_id': partner.id,
        })
        cm, captured = self._capture_send()
        with cm:
            mailbox._get_client().send_message(mail, mailbox, account)

        mime = self._decode_raw(captured)
        self.assertEqual(mime['X-Odoo-Model'], 'res.partner')
        self.assertEqual(mime['X-Odoo-Record-Id'], str(partner.id))
        self.assertEqual(mime['X-Odoo-Mail-Id'], str(mail.id))

    def test_send_collects_recipients_from_partners_and_cc(self):
        mailbox, account = self._sendable()
        p1 = self.env['res.partner'].create({'name': 'One', 'email': 'one@example.com'})
        mail = self.env['mail.mail'].create({
            'subject': 'x', 'body_html': '<p>x</p>',
            'recipient_ids': [(6, 0, [p1.id])], 'email_cc': 'boss@example.com',
        })
        cm, captured = self._capture_send()
        with cm:
            mailbox._get_client().send_message(mail, mailbox, account)

        mime = self._decode_raw(captured)
        self.assertIn('one@example.com', mime['To'])
        self.assertIn('boss@example.com', mime['Cc'])

    def test_send_attaches_files(self):
        mailbox, account = self._sendable()
        mail = self.env['mail.mail'].create({
            'subject': 'x', 'body_html': '<p>x</p>', 'email_to': 'c@example.com',
            'attachment_ids': [(0, 0, {
                'name': 'report.pdf', 'raw': b'%PDF-1.4 data', 'mimetype': 'application/pdf',
            })],
        })
        cm, captured = self._capture_send()
        with cm:
            mailbox._get_client().send_message(mail, mailbox, account)

        mime = self._decode_raw(captured)
        names = [p.get_filename() for p in mime.walk() if p.get_filename()]
        self.assertIn('report.pdf', names)

    def test_send_without_recipients_is_a_no_recipients_error(self):
        mailbox, account = self._sendable()
        mail = self.env['mail.mail'].create({'subject': 'x', 'body_html': '<p>x</p>'})
        with patch(GMAIL_POST) as post:
            result = mailbox._get_client().send_message(mail, mailbox, account)
            post.assert_not_called()  # never hit the network without a recipient
        self.assertFalse(result['success'])
        self.assertEqual(result['error_code'], 'no_recipients')

    # ------------------------------------------------------------------ #
    # OAuth URL
    # ------------------------------------------------------------------ #
    def test_authorization_url_requests_offline_consent_and_gmail_scopes(self):
        url = self.client.get_authorization_url('https://odoo.test/google_oauth/callback', state='abc')
        # Offline + consent is what makes Google hand back a refresh token.
        self.assertIn('access_type=offline', url)
        self.assertIn('prompt=consent', url)
        self.assertIn('gmail.modify', url)
        self.assertIn('gmail.send', url)
        self.assertIn('state=abc', url)

    # ------------------------------------------------------------------ #
    # Incoming — normalization and fetch
    # ------------------------------------------------------------------ #
    def _b64(self, text):
        import base64
        return base64.urlsafe_b64encode(text.encode()).decode()

    def _gmail_message(self, headers, html=None, plain=None, parts_extra=None,
                       gmail_id='g1', thread_id='t1', internal_date='1700000000000'):
        header_list = [{'name': k, 'value': v} for k, v in headers.items()]
        parts = []
        if plain is not None:
            parts.append({'mimeType': 'text/plain', 'body': {'data': self._b64(plain)}})
        if html is not None:
            parts.append({'mimeType': 'text/html', 'body': {'data': self._b64(html)}})
        parts.extend(parts_extra or [])
        return {
            'id': gmail_id, 'threadId': thread_id, 'internalDate': internal_date,
            'payload': {'headers': header_list, 'mimeType': 'multipart/mixed', 'parts': parts},
        }

    def test_normalize_maps_gmail_into_the_neutral_shape(self):
        raw = self._gmail_message(
            {'Message-Id': '<abc@mail.gmail.com>', 'Subject': 'Re: Quote',
             'From': 'Jane Doe <jane@example.com>', 'To': 'sales@test.local',
             'Cc': 'boss@example.com, cc2@example.com',
             'In-Reply-To': '<parent@x>', 'References': '<root@x> <parent@x>'},
            html='<p>Hello</p>')
        msg = self.client._normalize_message(raw)

        self.assertEqual(msg['message_id'], '<abc@mail.gmail.com>')
        self.assertEqual(msg['provider_message_id'], 'g1')
        self.assertEqual(msg['thread_id'], 't1')
        self.assertEqual(msg['subject'], 'Re: Quote')
        self.assertEqual(msg['from'], {'name': 'Jane Doe', 'email': 'jane@example.com'})
        self.assertEqual(msg['to'], [{'name': '', 'email': 'sales@test.local'}])
        self.assertEqual([r['email'] for r in msg['cc']],
                         ['boss@example.com', 'cc2@example.com'])
        self.assertEqual(msg['body_html'], '<p>Hello</p>')
        self.assertTrue(msg['body_is_html'])
        self.assertEqual(msg['date'], datetime(2023, 11, 14, 22, 13, 20))  # epoch ms -> naive UTC
        # Threading reads In-Reply-To out of `headers`, lowercased by the client.
        self.assertEqual(msg['headers']['in-reply-to'], '<parent@x>')
        self.assertFalse(msg['has_attachments'])

    def test_normalize_falls_back_to_plain_text(self):
        raw = self._gmail_message(
            {'Message-Id': '<p@x>', 'From': 'a@b.com', 'To': 'c@d.com'},
            plain='just text')
        msg = self.client._normalize_message(raw)
        self.assertEqual(msg['body_html'], 'just text')
        self.assertFalse(msg['body_is_html'])

    def test_normalize_prefers_html_over_plain(self):
        raw = self._gmail_message(
            {'Message-Id': '<h@x>', 'From': 'a@b.com', 'To': 'c@d.com'},
            plain='plain', html='<p>rich</p>')
        msg = self.client._normalize_message(raw)
        self.assertEqual(msg['body_html'], '<p>rich</p>')
        self.assertTrue(msg['body_is_html'])

    def test_fetched_messages_are_folder_mapped_and_sorted_ascending(self):
        mailbox = self.env['x_microsoft.mailbox'].create({
            'email': 'gmail_user@test.local', 'x_provider': 'gmail',
            'x_mailbox_type': 'personal', 'x_owner_user_id': self.user.id,
        })
        account = self._google_account(refresh_token='r', access_token='a',
                                       token_expiry=datetime.now() + timedelta(hours=1))

        # Gmail returns newest-first; ids 'new' then 'old'.
        listed = [{'id': 'new'}, {'id': 'old'}]
        meta = {
            'new': self._gmail_message({'Message-Id': '<new@x>', 'Subject': 'New'},
                                       gmail_id='new', internal_date='1700000600000'),
            'old': self._gmail_message({'Message-Id': '<old@x>', 'Subject': 'Old'},
                                       gmail_id='old', internal_date='1700000000000'),
        }
        Client = type(self.env['google.gmail.client'])
        with patch.object(Client, '_gmail_list_ids', return_value=listed) as list_mock, \
             patch.object(Client, '_gmail_get_message',
                          side_effect=lambda acc, gid, **kw: meta[gid]):
            messages = self.client.fetch_messages(
                account=account, mailbox=mailbox, folder=FOLDER_INBOX, limit=50)

        # The contract's folder id mapped onto Gmail's system label.
        self.assertEqual(list_mock.call_args.args[1], 'INBOX')
        # Sorted oldest-first: the sync cursor advances to the last item's date.
        self.assertEqual([m['message_id'] for m in messages], ['<old@x>', '<new@x>'])
        self.assertEqual(messages[-1]['date'], datetime(2023, 11, 14, 22, 23, 20))

    def test_get_attachments_handles_inline_and_regular(self):
        mailbox = self.env['x_microsoft.mailbox'].create({
            'email': 'gmail_user@test.local', 'x_provider': 'gmail',
            'x_mailbox_type': 'personal', 'x_owner_user_id': self.user.id,
        })
        account = self._google_account(refresh_token='r', access_token='a',
                                       token_expiry=datetime.now() + timedelta(hours=1))

        raw = self._gmail_message(
            {'Message-Id': '<a@x>', 'From': 'a@b.com', 'To': 'c@d.com'},
            html='<p>see <img src="cid:logo"></p>',
            parts_extra=[
                {'mimeType': 'image/png', 'filename': 'logo.png',
                 'headers': [{'name': 'Content-Id', 'value': '<logo>'},
                             {'name': 'Content-Disposition', 'value': 'inline'}],
                 'body': {'data': self._b64('PNGDATA')}},
                {'mimeType': 'application/pdf', 'filename': 'doc.pdf',
                 'headers': [{'name': 'Content-Disposition', 'value': 'attachment'}],
                 'body': {'attachmentId': 'att-1', 'size': 9}},
            ])
        Client = type(self.env['google.gmail.client'])
        with patch.object(Client, '_gmail_get_message', return_value=raw), \
             patch.object(Client, '_gmail_get_attachment_data', return_value=b'PDFBYTES'):
            attachments = self.client.get_message_attachments(account, mailbox, 'g1')

        by_name = {a['name']: a for a in attachments}
        # content_id is stripped of angle brackets, as the contract specifies.
        self.assertEqual(by_name['logo.png']['content_id'], 'logo')
        self.assertTrue(by_name['logo.png']['is_inline'])
        self.assertEqual(by_name['logo.png']['content'], b'PNGDATA')
        self.assertEqual(by_name['logo.png']['mimetype'], 'image/png')
        self.assertFalse(by_name['doc.pdf']['is_inline'])
        self.assertIsNone(by_name['doc.pdf']['content_id'])
        self.assertEqual(by_name['doc.pdf']['content'], b'PDFBYTES')  # fetched via attachmentId

    # ------------------------------------------------------------------ #
    # Token lifecycle
    # ------------------------------------------------------------------ #
    def _ok_response(self, payload):
        resp = MagicMock()
        resp.raise_for_status.return_value = None
        resp.json.return_value = payload
        return resp

    def _http_error(self, payload):
        """A real requests exception carrying a Google error body."""
        resp = MagicMock()
        resp.json.return_value = payload
        exc = requests.exceptions.RequestException('400 Client Error')
        exc.response = resp
        return exc

    def test_exchange_code_returns_tokens(self):
        with patch(GMAIL_POST, return_value=self._ok_response(
                {'access_token': 'AT', 'refresh_token': 'RT', 'expires_in': 3600})):
            tokens = self.client.exchange_code_for_tokens('code', 'https://odoo.test/cb')

        self.assertEqual(tokens['access_token'], 'AT')
        self.assertEqual(tokens['refresh_token'], 'RT')
        self.assertGreater(tokens['token_expiry'], datetime.now())

    def test_get_valid_token_returns_live_token_without_refresh(self):
        account = self._google_account(
            access_token='still-good', refresh_token='r',
            token_expiry=datetime.now() + timedelta(hours=1))
        with patch(GMAIL_POST) as post:
            token = self.client.get_valid_token(account)
            post.assert_not_called()
        self.assertEqual(token, 'still-good')

    def test_get_valid_token_refreshes_when_expired(self):
        account = self._google_account(
            access_token='stale', refresh_token='the-refresh',
            token_expiry=datetime.now() - timedelta(minutes=1))
        # Google omits refresh_token on refresh — the old one must survive.
        with patch(GMAIL_POST, return_value=self._ok_response(
                {'access_token': 'fresh', 'expires_in': 3600})):
            token = self.client.get_valid_token(account)

        self.assertEqual(token, 'fresh')
        account.invalidate_recordset()
        self.assertEqual(account.access_token, 'fresh')
        self.assertEqual(account.refresh_token, 'the-refresh')

    def test_invalid_grant_tells_the_user_to_reconnect(self):
        """A revoked/expired refresh token must surface as a reconnect prompt,
        distinct from a transient network failure.

        The client also clears the dead tokens as defense-in-depth, but that
        write is best-effort: it lands only if the caller commits the
        transaction, and Odoo rolls it back when this UserError propagates out of
        a request. So the *message* is the contract worth pinning; the clearing
        is not unit-testable without asserting on caller commit semantics.
        """
        account = self._google_account(
            access_token='stale', refresh_token='revoked',
            token_expiry=datetime.now() - timedelta(minutes=1))
        with patch(GMAIL_POST, side_effect=self._http_error({'error': 'invalid_grant'})):
            with self.assertRaises(UserError) as ctx:
                self.client.get_valid_token(account)

        self.assertIn('reconnect', str(ctx.exception).lower())

    def test_transient_refresh_error_is_not_a_reconnect_prompt(self):
        """A network blip must NOT tell the user their connection is revoked."""
        account = self._google_account(
            access_token='stale', refresh_token='still-valid',
            token_expiry=datetime.now() - timedelta(minutes=1))
        with patch(GMAIL_POST, side_effect=self._http_error({'error': 'temporarily_unavailable'})):
            with self.assertRaises(UserError) as ctx:
                self.client.get_valid_token(account)

        self.assertNotIn('reconnect', str(ctx.exception).lower())


@tagged('pan_mail_pro', 'post_install', '-at_install')
class TestGmailMailboxIsUsableEndToEnd(TransactionCase):
    """Phase 3: a Gmail mailbox must actually work, not merely be selectable.

    The orchestration layer used to ask `x_microsoft_oauth_connected` everywhere,
    so a Gmail mailbox with a perfectly good Google account reported `error`,
    never enabled incoming sync, and fell back to the notification mailbox on
    send. The owner dropdown listed Google-connected users, which made the gap
    look like it was closed when it was not. These tests fail against that code.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Account = cls.env['pan.mail.account']
        cls.Mailbox = cls.env['x_microsoft.mailbox']
        cls.user = cls.env['res.users'].create({
            'name': 'Gmail Only', 'login': 'gmail_only@test.local',
            'email': 'gmail_only@test.local',
            'group_ids': [(6, 0, [cls.env.ref('base.group_user').id])],
        })
        # Connected to Google and *only* Google — the case that used to break.
        cls.account = cls.Account.create({
            'email': 'gmail_only@test.local', 'provider': 'gmail',
            'user_id': cls.user.id, 'refresh_token': 'goog-refresh',
        })
        # Enabling incoming sync on any mailbox requires a Notification mailbox
        # to exist — see the constraint in microsoft_mailbox.py. It handles mail
        # from authors with no Odoo user, so it is a real product rule, not test
        # scaffolding.
        cls.notification_mailbox = cls.Mailbox.create({
            'email': 'notifications@test.local', 'x_provider': 'gmail',
            'x_mailbox_type': 'notification', 'x_owner_user_id': cls.user.id,
        })
        # Incoming sync is gated on internal domains being declared. A domain
        # nothing in this fixture uses, so the gate opens without turning any
        # fixture address internal.
        cls.env['pan.mail.internal.domains'].set_domains(['gate-fixture.test'])

    def _gmail_mailbox(self, **vals):
        base = {'email': 'gmail_only@test.local', 'x_provider': 'gmail',
                'x_mailbox_type': 'personal', 'x_owner_user_id': self.user.id}
        base.update(vals)
        return self.Mailbox.create(base)

    # ------------------------------------------------------------------ #
    # Health + incoming sync
    # ------------------------------------------------------------------ #
    def test_gmail_mailbox_with_connected_owner_is_healthy(self):
        mailbox = self._gmail_mailbox()
        self.assertFalse(self.user.x_microsoft_oauth_connected,
                         "fixture must be Google-only for this to mean anything")
        self.assertEqual(mailbox.x_health_status, 'healthy')

    def test_gmail_mailbox_without_credentials_is_an_error(self):
        self.account.write({'refresh_token_encrypted': False})
        mailbox = self._gmail_mailbox()
        self.assertEqual(mailbox.x_health_status, 'error')

    def test_gmail_mailbox_enables_incoming_sync(self):
        mailbox = self._gmail_mailbox(x_sync_mode='all')
        self.assertTrue(mailbox.x_incoming_enabled)

    def test_connecting_later_flips_incoming_enabled_on(self):
        """The old depends listed only the mode and the owner, so authorizing
        after configuring the mailbox left sync silently off."""
        self.account.write({'refresh_token_encrypted': False})
        mailbox = self._gmail_mailbox(x_sync_mode='all')
        self.assertFalse(mailbox.x_incoming_enabled)

        self.account.write({'refresh_token': 'reconnected'})
        self.assertTrue(mailbox.x_incoming_enabled)

    def test_shared_gmail_mailbox_is_healthy_on_its_service_account(self):
        """No owner at all: on Gmail a shared address is its own account."""
        self.Account.create({
            'email': 'sales@test.local', 'provider': 'gmail',
            'user_id': False, 'refresh_token': 'service-refresh',
        })
        mailbox = self.Mailbox.create({
            'email': 'sales@test.local', 'x_provider': 'gmail',
            'x_mailbox_type': 'shared', 'x_sync_mode': 'all',
        })
        self.assertFalse(mailbox.x_owner_user_id)
        self.assertTrue(mailbox._has_working_credentials())
        self.assertTrue(mailbox.x_incoming_enabled)

    def test_shared_gmail_mailbox_is_configurable_before_it_is_authorized(self):
        """Creation must not be blocked on credentials that do not exist yet.

        Microsoft demands an Owner on a synced shared mailbox, because reading it
        means borrowing that person's delegated token. Applying that rule to
        Gmail would be a deadlock: the shared address is its own account, so
        there is no owner to name, and the admin could never save the mailbox in
        order to go and authorize it. The missing credentials surface as an
        `error` health status instead — the same way every other unconnected
        mailbox does.
        """
        mailbox = self.Mailbox.create({
            'email': 'unauthorized@test.local', 'x_provider': 'gmail',
            'x_mailbox_type': 'shared', 'x_sync_mode': 'all',
        })
        self.assertFalse(mailbox.x_owner_user_id)
        self.assertFalse(mailbox._has_working_credentials())
        self.assertEqual(mailbox.x_health_status, 'error')

    def test_shared_microsoft_mailbox_still_requires_an_owner(self):
        """The Microsoft rule must survive being made provider-aware."""
        with self.assertRaises(UserError):
            self.Mailbox.create({
                'email': 'shared_ms@test.local', 'x_provider': 'outlook',
                'x_mailbox_type': 'shared', 'x_sync_mode': 'all',
            })

    # ------------------------------------------------------------------ #
    # Sending — the author's default-mailbox path
    # ------------------------------------------------------------------ #
    def test_gmail_user_sends_from_their_default_mailbox(self):
        mailbox = self._gmail_mailbox()
        self.user.x_microsoft_default_mailbox_id = mailbox
        mail = self.env['mail.mail'].with_user(self.user).sudo().create({
            'subject': 'Hi', 'body_html': '<p>x</p>',
            'email_to': 'customer@example.com',
            'author_id': self.user.partner_id.id,
        })

        resolved_mailbox, account = mail._get_mailbox_and_account()

        self.assertEqual(resolved_mailbox, mailbox)
        self.assertEqual(account, self.account)

    def test_shared_gmail_default_mailbox_sends_as_the_service_account(self):
        """Gmail has no SendAs: a shared mailbox must send with its own
        credentials, not the author's personal token."""
        service = self.Account.create({
            'email': 'sales@test.local', 'provider': 'gmail',
            'user_id': False, 'refresh_token': 'service-refresh',
        })
        mailbox = self.Mailbox.create({
            'email': 'sales@test.local', 'x_provider': 'gmail',
            'x_mailbox_type': 'shared',
        })
        self.user.x_microsoft_default_mailbox_id = mailbox
        mail = self.env['mail.mail'].with_user(self.user).sudo().create({
            'subject': 'Hi', 'body_html': '<p>x</p>',
            'email_to': 'customer@example.com',
            'author_id': self.user.partner_id.id,
        })

        resolved_mailbox, account = mail._get_mailbox_and_account()

        self.assertEqual(resolved_mailbox, mailbox)
        self.assertEqual(account, service)
        self.assertFalse(account.user_id)
        self.assertNotEqual(account, self.account)

    def test_missing_credentials_error_names_the_right_provider(self):
        """A Gmail user must not be told to connect Microsoft."""
        self.account.write({'refresh_token_encrypted': False})
        mailbox = self._gmail_mailbox()
        self.user.x_microsoft_default_mailbox_id = mailbox
        mail = self.env['mail.mail'].with_user(self.user).sudo().create({
            'subject': 'Hi', 'body_html': '<p>x</p>',
            'email_to': 'customer@example.com',
            'author_id': self.user.partner_id.id,
        })

        message = mail._get_missing_mailbox_error()
        self.assertIn('Gmail', message)
        self.assertNotIn('Microsoft', message)
