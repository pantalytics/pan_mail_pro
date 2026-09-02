# -*- coding: utf-8 -*-
"""
Tests for the provider-agnostic client contract.

These guard the seam itself rather than any one provider: that a mailbox
resolves to a client, that the client declares what it can service, and that
Graph payloads are translated into the normalized shapes every caller depends
on. A second provider (Gmail) has to satisfy the same assertions.
"""
from datetime import datetime

from odoo.exceptions import UserError
from odoo.tests import TransactionCase, tagged

from odoo.addons.pan_mail_pro.models.mail_provider_client import (
    DEFAULT_PROVIDER,
    FOLDER_INBOX,
    FOLDER_SENT,
    PROVIDER_CLIENTS,
    get_provider_client,
)


@tagged('post_install', '-at_install', 'pan_mail_pro')
class TestProviderRegistry(TransactionCase):
    """The registry is the only place a provider code becomes a model."""

    def test_every_registered_provider_resolves_to_a_client(self):
        for code in PROVIDER_CLIENTS:
            client = get_provider_client(self.env, code)
            self.assertEqual(
                client.provider_code(), code,
                f"client for '{code}' reports a different provider_code",
            )

    def test_every_client_implements_the_whole_contract(self):
        """A provider that inherits the contract but forgets a method would
        raise NotImplementedError at send time, in production."""
        contract = self.env['mail.provider.client']
        required = [
            'provider_code', 'account_for_user', 'resolve_sending_account',
            'resolve_receiving_account', 'get_authorization_url',
            '_exchange_code_for_tokens', 'refresh_access_token',
            'get_valid_token', 'get_user_email', 'test_connection',
            'send_message', 'fetch_messages', 'get_message',
            'get_message_attachments',
        ]
        for code in PROVIDER_CLIENTS:
            client = get_provider_client(self.env, code)
            for name in required:
                self.assertIsNot(
                    getattr(type(client), name, None),
                    getattr(type(contract), name),
                    f"'{code}' does not implement {name}()",
                )

    def test_unknown_provider_raises(self):
        with self.assertRaises(UserError):
            get_provider_client(self.env, 'carrier-pigeon')

    def test_mailbox_resolves_to_its_client(self):
        mailbox = self.env['x_microsoft.mailbox'].create({
            'email': 'contract@company.test',
            'x_mailbox_type': 'shared',
        })
        self.assertEqual(mailbox.x_provider, DEFAULT_PROVIDER)
        self.assertEqual(mailbox._get_client().provider_code(), DEFAULT_PROVIDER)


@tagged('post_install', '-at_install', 'pan_mail_pro')
class TestProviderCapabilities(TransactionCase):
    """Providers differ in how 'send as somebody else' works."""

    def setUp(self):
        super().setUp()
        self.client = get_provider_client(self.env, 'outlook')

    def test_outlook_supports_shared_mailboxes(self):
        # Microsoft 365 sends from a shared mailbox with the user's own token.
        self.assertTrue(self.client.supports_shared_mailbox)
        for mailbox_type in ('personal', 'shared', 'notification'):
            self.client.check_mailbox_supported(mailbox_type)

    def test_unsupported_mailbox_type_is_rejected(self):
        with self.assertRaises(UserError):
            self.client.check_mailbox_supported('carrier-pigeon')

    def test_mailbox_type_is_validated_against_provider_on_create(self):
        """A mailbox its provider cannot service must fail at config time,
        not at send time."""
        Mailbox = self.env['x_microsoft.mailbox']
        original = type(self.client).supported_mailbox_types
        type(self.client).supported_mailbox_types = ('personal',)
        self.addCleanup(
            setattr, type(self.client), 'supported_mailbox_types', original
        )
        # Not a tuple: Odoo's assertRaises override calls issubclass() on the
        # argument, which raises TypeError on a tuple. ValidationError
        # subclasses UserError anyway, so this still covers both.
        with self.assertRaises(UserError):
            Mailbox.create({
                'email': 'unsupported@company.test',
                'x_mailbox_type': 'shared',
            })

    def _connected_user(self, login):
        """A user with credentials for this provider, which is what
        resolve_sending_account hands back."""
        user = self.env['res.users'].create({
            'name': login, 'login': login, 'email': login,
        })
        self.env['pan.mail.account'].create({
            'email': login,
            'provider': self.client.provider_code(),
            'user_id': user.id,
        })
        return user

    def test_notification_mailbox_sends_with_owner_token(self):
        """Notification mail must not depend on who triggered it."""
        owner = self._connected_user('notify-owner@company.test')
        other = self._connected_user('someone-else@company.test')
        mailbox = self.env['x_microsoft.mailbox'].new({
            'email': 'notifications@company.test',
            'x_mailbox_type': 'notification',
            'x_owner_user_id': owner.id,
        })
        resolved = self.client.resolve_sending_account(mailbox, author_user=other)
        self.assertEqual(resolved.user_id, owner)

    def test_shared_mailbox_sends_with_author_token(self):
        """Shared mailboxes rely on the author's own SendAs rights, which
        matters in cron context where env.user is the cron runner."""
        author = self._connected_user('author@company.test')
        mailbox = self.env['x_microsoft.mailbox'].new({
            'email': 'team@company.test',
            'x_mailbox_type': 'shared',
        })
        resolved = self.client.resolve_sending_account(mailbox, author_user=author)
        self.assertEqual(resolved.user_id, author)

    def test_reading_a_mailbox_uses_the_owners_token(self):
        owner = self._connected_user('reader@company.test')
        mailbox = self.env['x_microsoft.mailbox'].new({
            'email': 'inbox@company.test',
            'x_mailbox_type': 'personal',
            'x_owner_user_id': owner.id,
        })
        self.assertEqual(
            self.client.resolve_receiving_account(mailbox).user_id, owner)


@tagged('post_install', '-at_install', 'pan_mail_pro')
class TestGraphNormalization(TransactionCase):
    """Graph payload shapes must not leak past the client."""

    def setUp(self):
        super().setUp()
        self.client = get_provider_client(self.env, 'outlook')

    def test_folder_ids_map_to_graph_names(self):
        self.assertEqual(self.client._graph_folder(FOLDER_INBOX), 'Inbox')
        self.assertEqual(self.client._graph_folder(FOLDER_SENT), 'SentItems')
        with self.assertRaises(UserError):
            self.client._graph_folder('drafts')

    def test_full_message_is_normalized(self):
        raw = {
            'id': 'AAMkAG...',
            'internetMessageId': '<abc@contoso.com>',
            'conversationId': 'conv-123',
            'subject': 'Quote request',
            'from': {'emailAddress': {'address': 'ann@client.test', 'name': 'Ann'}},
            'toRecipients': [
                {'emailAddress': {'address': 'sales@company.test', 'name': 'Sales'}},
            ],
            'ccRecipients': [
                {'emailAddress': {'address': 'bob@client.test', 'name': 'Bob'}},
            ],
            'receivedDateTime': '2026-05-12T10:00:00Z',
            'body': {'contentType': 'html', 'content': '<p>Hello</p>'},
            'hasAttachments': True,
            'isRead': False,
            'internetMessageHeaders': [
                {'name': 'X-Odoo-Model', 'value': 'crm.lead'},
            ],
        }

        msg = self.client._normalize_message(raw)

        self.assertEqual(msg['provider_message_id'], 'AAMkAG...')
        self.assertEqual(msg['message_id'], '<abc@contoso.com>')
        self.assertEqual(msg['thread_id'], 'conv-123')
        self.assertEqual(msg['from'], {'email': 'ann@client.test', 'name': 'Ann'})
        self.assertEqual(msg['to'], [{'email': 'sales@company.test', 'name': 'Sales'}])
        self.assertEqual(msg['cc'], [{'email': 'bob@client.test', 'name': 'Bob'}])
        self.assertEqual(msg['date'], datetime(2026, 5, 12, 10, 0, 0))
        self.assertEqual(msg['body_html'], '<p>Hello</p>')
        self.assertTrue(msg['body_is_html'])
        self.assertTrue(msg['has_attachments'])
        self.assertFalse(msg['is_read'])
        # Headers are lowercased so callers can look them up predictably.
        self.assertEqual(msg['headers']['x-odoo-model'], 'crm.lead')

    def test_list_message_without_body_falls_back_to_preview(self):
        """List responses carry only bodyPreview; callers still get body_html."""
        msg = self.client._normalize_message({
            'id': 'g1',
            'internetMessageId': '<preview@test>',
            'bodyPreview': 'Short preview',
        })
        self.assertEqual(msg['body_html'], 'Short preview')
        self.assertFalse(msg['body_is_html'])
        self.assertIsNone(msg['date'])
        self.assertEqual(msg['to'], [])
        self.assertEqual(msg['from'], {'email': '', 'name': ''})

    def test_unparseable_date_does_not_raise(self):
        msg = self.client._normalize_message({
            'id': 'g1',
            'receivedDateTime': 'not-a-date',
        })
        self.assertIsNone(msg['date'])

    def test_send_result_is_normalized(self):
        """send_message translates Graph's key names to the contract's.

        Also pins that `reply_context` reaches the implementation: it is an
        optional argument, so a client that silently dropped it would still
        send — just unthreaded, which is the failure mode it exists to fix.
        """
        Client = type(self.client)
        original = Client.send_email_via_graph
        seen = {}

        def fake_send(self_, mail_record, mailbox, account, reply_context=None):
            seen['reply_context'] = reply_context
            return {
                'success': True,
                'microsoft_message_id': '<sent@contoso.com>',
                'microsoft_conversation_id': 'conv-999',
            }

        Client.send_email_via_graph = fake_send
        self.addCleanup(setattr, Client, 'send_email_via_graph', original)

        result = self.client.send_message(
            mail_record=None, mailbox=None, account=None,
            reply_context={'provider_message_id': 'GRAPH-MSG-1'},
        )

        self.assertTrue(result['success'])
        self.assertEqual(result['message_id'], '<sent@contoso.com>')
        self.assertEqual(result['thread_id'], 'conv-999')
        self.assertIsNone(result['error'])
        self.assertEqual(seen['reply_context'], {'provider_message_id': 'GRAPH-MSG-1'})


@tagged('post_install', '-at_install', 'pan_mail_pro')
class TestNeutralizedProviderCalls(TransactionCase):
    """No client may reach the network from a database copy.

    An empty credential already makes a send fail, but it fails at the far end:
    staging would still open the connection and collect a rejection from the
    provider on every attempt. Each client guards the one point its transport
    cannot avoid, and a new provider has to do the same.
    """

    def setUp(self):
        super().setUp()
        self.env['ir.config_parameter'].sudo().set_param('database.is_neutralized', 'True')

    def test_no_registered_client_will_contact_its_provider(self):
        for code in PROVIDER_CLIENTS:
            client = get_provider_client(self.env, code)
            account = self.env['pan.mail.account'].sudo().create({
                'email': f'{code}@example.com',
                'provider': code,
            })
            with self.subTest(provider=code):
                with self.assertRaises(UserError) as caught:
                    if client.uses_oauth:
                        client.get_valid_token(account)
                    else:
                        client._require_credentials(account)
                self.assertIn('neutralized', str(caught.exception))

    def test_no_oauth_client_will_exchange_an_authorization_code(self):
        """Re-authorizing in staging would write live credentials back in."""
        for code in PROVIDER_CLIENTS:
            client = get_provider_client(self.env, code)
            if not client.uses_oauth:
                continue
            with self.subTest(provider=code):
                with self.assertRaises(UserError) as caught:
                    client._exchange_code_for_tokens('a-code', 'https://odoo.test/cb')
                self.assertIn('neutralized', str(caught.exception))

