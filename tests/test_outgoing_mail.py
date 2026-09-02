# -*- coding: utf-8 -*-
"""
Unit tests for outgoing mail via Microsoft Graph API.

Run with: python -m odoo -d test_db --test-enable --test-tags=pan_mail_pro
"""
from unittest.mock import patch, MagicMock

from odoo.tests import TransactionCase, tagged


@tagged('pan_mail_pro', 'post_install', '-at_install')
class TestGraphSendPayload(TransactionCase):
    """Verify the Graph API draft payload includes To/Cc recipients correctly."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        # Mail Pro refuses to create a mailbox while the internal domain
        # list is empty. A domain nothing in this fixture uses, so the gate
        # opens without turning any fixture address internal.
        cls.env['pan.mail.internal.domains'].set_domains(['gate-fixture.test'])
        cls.client = cls.env['microsoft.graph.client']

        cls.mailbox = cls.env['pan.mail.mailbox'].create({
            'email': 'sender@company.com',
            'mailbox_type': 'shared',
        })
        cls.user = cls.env.user

    def _make_mail(self, **overrides):
        vals = {
            'subject': 'Test',
            'body_html': '<p>Hi</p>',
            'email_to': 'to@example.com',
            'author_id': self.user.partner_id.id,
        }
        vals.update(overrides)
        return self.env['mail.mail'].create(vals)

    def _fake_draft_response(self):
        resp = MagicMock()
        resp.json.return_value = {
            'id': 'DRAFT_ID',
            'internetMessageId': '<msg@example.com>',
            'conversationId': 'CONV_ID',
        }
        resp.raise_for_status.return_value = None
        return resp

    def _patched_send(self, mail):
        """Run send_email_via_graph with requests.post mocked; return captured draft payload."""
        captured = {}

        def fake_post(url, headers=None, json=None, timeout=None, **kwargs):
            if '/messages' in url and url.endswith('/messages'):
                captured['draft'] = json
                return self._fake_draft_response()
            # /send endpoint
            resp = MagicMock()
            resp.raise_for_status.return_value = None
            return resp

        with patch.object(
            type(self.client), 'get_valid_token', return_value='fake_token'
        ), patch('odoo.addons.pan_mail_pro.models.providers.microsoft.graph_client.requests.post', side_effect=fake_post):
            result = self.client.send_email_via_graph(mail, self.mailbox, self.user)

        return result, captured.get('draft')

    def test_cc_recipients_included(self):
        """email_cc must be translated to Graph API ccRecipients."""
        mail = self._make_mail(email_cc='cc1@example.com, "Name Two" <cc2@example.com>')
        result, payload = self._patched_send(mail)

        self.assertTrue(result['success'])
        self.assertIn('ccRecipients', payload)
        addresses = [r['emailAddress']['address'] for r in payload['ccRecipients']]
        self.assertEqual(addresses, ['cc1@example.com', 'cc2@example.com'])
        # Named recipient keeps its display name
        named = [r for r in payload['ccRecipients'] if r['emailAddress']['address'] == 'cc2@example.com'][0]
        self.assertEqual(named['emailAddress']['name'], 'Name Two')

    def test_no_cc_omits_key(self):
        """When email_cc is empty, ccRecipients key should not be present."""
        mail = self._make_mail(email_cc=False)
        result, payload = self._patched_send(mail)

        self.assertTrue(result['success'])
        self.assertNotIn('ccRecipients', payload)

    def test_cc_only_still_sends(self):
        """A mail with only CC (no To) should still be accepted."""
        mail = self._make_mail(email_to=False, email_cc='cc@example.com')
        result, payload = self._patched_send(mail)

        self.assertTrue(result['success'])
        self.assertEqual(payload['toRecipients'], [])
        self.assertEqual(
            [r['emailAddress']['address'] for r in payload['ccRecipients']],
            ['cc@example.com'],
        )
