# -*- coding: utf-8 -*-
"""Google provider: dispatch, credentials, and the OAuth token lifecycle.

Step 1 of the Gmail adapter. Sending and incoming sync are separate steps with
their own tests; this covers the plumbing everything else stands on — that a
google mailbox dispatches to the right provider, that credentials resolve to a
pan.mail.account, and that the token refresh talks to Google correctly.

The HTTP is mocked at the requests boundary, the same way common.py mocks Graph.
No real network, no real Google client.
"""
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch

import requests

from odoo.exceptions import UserError
from odoo.tests import TransactionCase, tagged

# Patch requests.post specifically, not the whole module — the client catches
# requests.exceptions.RequestException, which must stay a real class.
GMAIL_POST = 'odoo.addons.pan_outlook_pro.models.providers.google.gmail_client.requests.post'


@tagged('pan_outlook_pro', 'post_install', '-at_install')
class TestGoogleProvider(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Account = cls.env['pan.mail.account']
        cls.client = cls.env['gmail.client']
        cls.user = cls.env['res.users'].create({
            'name': 'Gmail User', 'login': 'gmail_user@test.local', 'email': 'gmail_user@test.local',
        })
        cls.env['ir.config_parameter'].sudo().set_param(
            'x_pan_outlook_pro.google_client_id', 'test-client-id.apps.googleusercontent.com')

    def _google_account(self, **vals):
        base = {'email': 'gmail_user@test.local', 'provider': 'google', 'user_id': self.user.id}
        base.update(vals)
        return self.Account.create(base)

    # ------------------------------------------------------------------ #
    # Dispatch
    # ------------------------------------------------------------------ #
    def test_google_mailbox_dispatches_to_google_provider(self):
        mailbox = self.env['x_microsoft.mailbox'].create({
            'email': 'team@test.local',
            'x_provider': 'google', 'x_mailbox_type': 'shared',
        })
        provider = mailbox._get_provider()
        self.assertEqual(provider._name, 'pan.mail.provider.google')

    def test_google_provider_supports_the_three_types(self):
        provider = self.env['pan.mail.provider.google']
        self.assertEqual(set(provider._supported_mailbox_types()),
                         {'personal', 'shared', 'notification'})

    # ------------------------------------------------------------------ #
    # Credentials
    # ------------------------------------------------------------------ #
    def test_account_for_user_finds_the_google_account(self):
        account = self._google_account()
        provider = self.env['pan.mail.provider.google']
        self.assertEqual(provider._account_for_user(self.user), account)

    def test_user_can_hold_microsoft_and_google_accounts(self):
        """Different providers, one user — UNIQUE(user_id, provider) allows it.

        This is the whole reason credentials moved off res.users: a user
        connecting both Microsoft and Google needs somewhere for the second.
        """
        ms = self.Account.create({
            'email': 'gmail_user@test.local', 'provider': 'microsoft', 'user_id': self.user.id})
        google = self._google_account()
        self.assertNotEqual(ms, google)
        self.assertEqual(
            self.env['pan.mail.provider.google']._account_for_user(self.user), google)

    def test_shared_mailbox_sends_with_a_service_account(self):
        """A Gmail shared mailbox is its own account: user_id null, keyed on the
        address. Not the author's token (that is the Microsoft SendAs model)."""
        mailbox = self.env['x_microsoft.mailbox'].create({
            'email': 'sales@test.local',
            'x_provider': 'google', 'x_mailbox_type': 'shared',
        })
        service = self.Account.create({
            'email': 'sales@test.local', 'provider': 'google', 'user_id': False,
            'refresh_token': 'service-refresh',
        })
        mail = self.env['mail.mail'].create({'subject': 'x', 'body_html': '<p>x</p>'})

        resolved = mailbox._get_provider()._get_sending_account(mailbox, mail)
        self.assertEqual(resolved, service)
        self.assertFalse(resolved.user_id)

    def test_personal_mailbox_sends_with_the_owner_account(self):
        account = self._google_account(refresh_token='owner-refresh')
        mailbox = self.env['x_microsoft.mailbox'].create({
            'email': 'gmail_user@test.local',
            'x_provider': 'google', 'x_mailbox_type': 'personal',
            'x_owner_user_id': self.user.id,
        })
        mail = self.env['mail.mail'].create({'subject': 'x', 'body_html': '<p>x</p>'})

        self.assertEqual(mailbox._get_provider()._get_sending_account(mailbox, mail), account)

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
            'google', self.user, 'gmail_user@test.local', 'AT1', 'RT1',
            datetime.now() + timedelta(hours=1))
        self.assertEqual(acc.provider, 'google')
        self.assertEqual(acc.refresh_token, 'RT1')

        # Re-authorizing: Google omits the refresh token, and the same account
        # must be reused, not duplicated.
        again = Account._store_tokens(
            'google', self.user, 'gmail_user@test.local', 'AT2', None,
            datetime.now() + timedelta(hours=1))
        self.assertEqual(again, acc)
        again.invalidate_recordset()
        self.assertEqual(again.access_token, 'AT2')
        self.assertEqual(again.refresh_token, 'RT1')  # preserved
        self.assertEqual(
            Account.search_count([('user_id', '=', self.user.id), ('provider', '=', 'google')]), 1)

    def test_google_connected_flag_is_independent_of_microsoft(self):
        self.assertFalse(self.user.x_google_oauth_connected)
        # A Microsoft account must not flip the Google flag.
        self.Account.create({
            'email': 'gmail_user@test.local', 'provider': 'microsoft',
            'user_id': self.user.id, 'refresh_token': 'ms'})
        self.assertFalse(self.user.x_google_oauth_connected)

        self._google_account(refresh_token='goog')
        self.assertTrue(self.user.x_google_oauth_connected)

    def test_disconnect_google_clears_the_account(self):
        self._google_account(access_token='a', refresh_token='r')
        self.user.action_disconnect_google()

        account = self.Account.search([('user_id', '=', self.user.id), ('provider', '=', 'google')])
        self.assertFalse(account.refresh_token_encrypted)
        self.assertFalse(self.user.x_google_oauth_connected)

    # ------------------------------------------------------------------ #
    # Sending
    # ------------------------------------------------------------------ #
    def _sendable(self):
        """A google shared mailbox + its live service account, ready to send."""
        mailbox = self.env['x_microsoft.mailbox'].create({
            'email': 'sales@test.local', 'x_provider': 'google', 'x_mailbox_type': 'shared',
        })
        account = self.Account.create({
            'email': 'sales@test.local', 'provider': 'google', 'user_id': False,
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
            result = mailbox._get_provider()._send(mail, mailbox, account)

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
            mailbox._get_provider()._send(mail, mailbox, account)

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
            mailbox._get_provider()._send(mail, mailbox, account)

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
            mailbox._get_provider()._send(mail, mailbox, account)

        mime = self._decode_raw(captured)
        names = [p.get_filename() for p in mime.walk() if p.get_filename()]
        self.assertIn('report.pdf', names)

    def test_send_without_recipients_is_a_no_recipients_error(self):
        mailbox, account = self._sendable()
        mail = self.env['mail.mail'].create({'subject': 'x', 'body_html': '<p>x</p>'})
        with patch(GMAIL_POST) as post:
            result = mailbox._get_provider()._send(mail, mailbox, account)
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
        msg = self.env['pan.mail.provider.google']._normalize_message(raw)

        self.assertEqual(msg['message_id'], '<abc@mail.gmail.com>')
        self.assertEqual(msg['provider_message_id'], 'g1')
        self.assertEqual(msg['thread_id'], 't1')
        self.assertEqual(msg['in_reply_to'], '<parent@x>')
        self.assertEqual(msg['references'], ['<root@x>', '<parent@x>'])
        self.assertEqual(msg['subject'], 'Re: Quote')
        self.assertEqual(msg['from'], ('Jane Doe', 'jane@example.com'))
        self.assertEqual(msg['to'], [('', 'sales@test.local')])
        self.assertEqual([e for _n, e in msg['cc']], ['boss@example.com', 'cc2@example.com'])
        self.assertEqual(msg['body_html'], '<p>Hello</p>')
        self.assertTrue(msg['is_html'])
        self.assertEqual(msg['date'], datetime(2023, 11, 14, 22, 13, 20))  # epoch ms -> naive UTC
        # message.py: attachments always empty from _get_message.
        self.assertEqual(msg['attachments'], [])

    def test_normalize_falls_back_to_plain_text(self):
        raw = self._gmail_message(
            {'Message-Id': '<p@x>', 'From': 'a@b.com', 'To': 'c@d.com'},
            plain='just text')
        msg = self.env['pan.mail.provider.google']._normalize_message(raw)
        self.assertEqual(msg['body_html'], 'just text')
        self.assertFalse(msg['is_html'])

    def test_normalize_prefers_html_over_plain(self):
        raw = self._gmail_message(
            {'Message-Id': '<h@x>', 'From': 'a@b.com', 'To': 'c@d.com'},
            plain='plain', html='<p>rich</p>')
        msg = self.env['pan.mail.provider.google']._normalize_message(raw)
        self.assertEqual(msg['body_html'], '<p>rich</p>')
        self.assertTrue(msg['is_html'])

    def test_previews_are_folder_mapped_and_sorted_ascending(self):
        provider = self.env['pan.mail.provider.google']
        mailbox = self.env['x_microsoft.mailbox'].create({
            'email': 'gmail_user@test.local', 'x_provider': 'google',
            'x_mailbox_type': 'personal', 'x_owner_user_id': self.user.id,
        })
        self._google_account(refresh_token='r', access_token='a',
                             token_expiry=datetime.now() + timedelta(hours=1))

        # Gmail returns newest-first; ids 'new' then 'old'.
        listed = [{'id': 'new'}, {'id': 'old'}]
        meta = {
            'new': self._gmail_message({'Message-Id': '<new@x>', 'Subject': 'New'},
                                       gmail_id='new', internal_date='1700000600000'),
            'old': self._gmail_message({'Message-Id': '<old@x>', 'Subject': 'Old'},
                                       gmail_id='old', internal_date='1700000000000'),
        }
        Client = type(self.env['gmail.client'])
        with patch.object(Client, 'list_message_ids', return_value=listed) as list_mock, \
             patch.object(Client, 'get_message', side_effect=lambda acc, gid, **kw: meta[gid]):
            previews = provider._fetch_message_previews(mailbox, 'Inbox', since=None, limit=50)

        # Folder mapped to the INBOX label.
        self.assertEqual(list_mock.call_args.args[1], 'INBOX')
        # Sorted oldest-first for the cursor.
        self.assertEqual([p['message_id'] for p in previews], ['<old@x>', '<new@x>'])
        self.assertEqual(previews[-1]['date'], datetime(2023, 11, 14, 22, 23, 20))

    def test_get_attachments_handles_inline_and_regular(self):
        provider = self.env['pan.mail.provider.google']
        mailbox = self.env['x_microsoft.mailbox'].create({
            'email': 'gmail_user@test.local', 'x_provider': 'google',
            'x_mailbox_type': 'personal', 'x_owner_user_id': self.user.id,
        })
        self._google_account(refresh_token='r', access_token='a',
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
        Client = type(self.env['gmail.client'])
        with patch.object(Client, 'get_message', return_value=raw), \
             patch.object(Client, 'get_attachment_data', return_value=b'PDFBYTES'):
            attachments = provider._get_attachments(mailbox, 'g1')

        by_name = {a['name']: a for a in attachments}
        self.assertEqual(by_name['logo.png']['cid'], 'logo')
        self.assertTrue(by_name['logo.png']['is_inline'])
        self.assertEqual(by_name['logo.png']['content'], b'PNGDATA')
        self.assertFalse(by_name['doc.pdf']['is_inline'])
        self.assertIsNone(by_name['doc.pdf']['cid'])
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
