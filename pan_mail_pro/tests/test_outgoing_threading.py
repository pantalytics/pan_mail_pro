# -*- coding: utf-8 -*-
"""
Outgoing threading: the half of the problem that is not matching.

Mail we sent carried no `In-Reply-To` and no `References`, so the recipient's
client had nothing to attach our message to and started a fresh conversation.
Their reply then came back rooted at that unthreaded mail — which is exactly why
matching had to lean so hard on provider thread ids.

Two shapes of provider, tested separately because the difference is real and not
an implementation detail:

- Gmail (and later IMAP) accept standard headers, so they thread with
  `In-Reply-To` / `References`.
- Microsoft Graph refuses them — `internetMessageHeaders` takes custom `x-`
  headers only — so it threads by replying *to a message* via `createReply`.
"""
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch

import requests

from odoo.tests import TransactionCase, tagged

from odoo.addons.pan_mail_pro.models.mail_mail import REPLY_CHAIN_LIMIT
from odoo.addons.pan_mail_pro.models.pan_mail_fetcher import odoo_db_marker
from odoo.addons.pan_mail_pro.models.providers.microsoft.graph_client import GRAPH_MAX_CUSTOM_HEADERS

from .common import MailProTestCase, send_and_capture

GMAIL_POST = 'odoo.addons.pan_mail_pro.models.providers.google.gmail_client.requests.post'
GRAPH_POST = 'odoo.addons.pan_mail_pro.models.providers.microsoft.graph_client.requests.post'
GRAPH_PATCH = 'odoo.addons.pan_mail_pro.models.providers.microsoft.graph_client.requests.patch'


class OutgoingThreadingCase(TransactionCase):
    """Fixtures shared by the reply-context and per-provider tests."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        # Mail Pro refuses to create a mailbox while the internal domain
        # list is empty. A domain nothing in this fixture uses, so the gate
        # opens without turning any fixture address internal.
        cls.env['pan.mail.domain'].set_domains(['gate-fixture.test'])
        cls.partner = cls.env['res.partner'].create({
            'name': 'Customer', 'email': 'customer@example.com',
        })

    def _post(self, message_id, parent=None, subject='Order 12'):
        return self.partner.with_context(
            mail_create_nosubscribe=True, mail_notrack=True,
        ).message_post(
            body='<p>body</p>',
            subject=subject,
            message_type='email',
            subtype_xmlid='mail.mt_comment',
            message_id=message_id,
            parent_id=parent.id if parent else False,
        )

    def _outgoing_mail(self, **vals):
        base = {
            'subject': 'Re: Order 12',
            'body_html': '<p>Reply body</p>',
            'email_to': 'customer@example.com',
            'model': 'res.partner',
            'res_id': self.partner.id,
        }
        base.update(vals)
        return self.env['mail.mail'].create(base)


@tagged('pan_mail_pro', 'post_install', '-at_install')
class TestReplyContext(OutgoingThreadingCase):
    """What `mail.mail._build_reply_context` hands a provider. No HTTP."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.mailbox = cls.env['pan.mail.mailbox'].create({
            'email': 'support@company.test',
        })

    def test_a_fresh_mail_has_nothing_to_thread_onto(self):
        mail = self.env['mail.mail'].create({
            'subject': 'Cold outreach', 'email_to': 'customer@example.com',
        })
        context = mail._build_reply_context(self.mailbox)

        self.assertIsNone(context['in_reply_to'])
        self.assertEqual(context['references'], [])
        self.assertIsNone(context['thread_id'])
        self.assertIsNone(context['provider_message_id'])

    def test_chain_is_root_first_and_in_reply_to_is_the_direct_parent(self):
        """RFC 5322 orders References oldest first; In-Reply-To names the parent.

        Getting the order backwards is the kind of thing that still *looks*
        threaded in one client and breaks in another.
        """
        root = self._post('<root@example.com>')
        self._post('<parent@example.com>', parent=root)

        context = self._outgoing_mail()._build_reply_context(self.mailbox)

        self.assertEqual(context['references'],
                         ['<root@example.com>', '<parent@example.com>'])
        self.assertEqual(context['in_reply_to'], '<parent@example.com>')

    def test_a_long_chain_keeps_the_root_and_the_nearest_ancestors(self):
        """The trim may never drop the root: the IMAP provider derives its
        thread key from references[0], so a chain trimmed from the front hands
        the same conversation a new thread id partway through. Root plus the
        nearest ancestors is also what RFC 5322 recommends for a truncated
        References header."""
        Message = self.env['mail.message']
        node, wire_ids = False, []
        for i in range(REPLY_CHAIN_LIMIT + 5):
            wire_id = f'<m{i}@example.com>'
            node = Message.create({
                'model': 'res.partner', 'res_id': self.partner.id,
                'message_type': 'email', 'message_id': wire_id,
                'parent_id': node and node.id,
            })
            wire_ids.append(wire_id)

        context = self._outgoing_mail()._build_reply_context(self.mailbox)

        self.assertEqual(len(context['references']), REPLY_CHAIN_LIMIT)
        self.assertEqual(context['references'][0], wire_ids[0])
        self.assertEqual(context['references'][1:],
                         wire_ids[-(REPLY_CHAIN_LIMIT - 1):])
        self.assertEqual(context['in_reply_to'], wire_ids[-1])

    def test_the_wire_message_id_wins_over_odoo_s_own(self):
        """Graph mints its own Message-ID, and that is what the recipient sees.

        Emitting the id Odoo generated would produce a References chain that
        does not match the one coming back, so the reply threads nowhere.
        """
        parent = self._post('<odoo-generated@company.test>')
        self.env['pan.mail.message.ref'].record(
            parent, '<graph-assigned@outlook.com>', source='provider')

        context = self._outgoing_mail()._build_reply_context(self.mailbox)

        self.assertEqual(context['in_reply_to'], '<graph-assigned@outlook.com>')

    def test_thread_handles_come_from_the_link_for_this_mailbox(self):
        self.env['pan.mail.thread.link'].record(
            mailbox=self.mailbox, thread_id='CONV-9',
            model='res.partner', res_id=self.partner.id,
            provider_message_id='GRAPH-MSG-9',
        )

        context = self._outgoing_mail()._build_reply_context(self.mailbox)

        self.assertEqual(context['thread_id'], 'CONV-9')
        self.assertEqual(context['provider_message_id'], 'GRAPH-MSG-9')

    def test_another_mailbox_s_thread_is_not_borrowed(self):
        other = self.env['pan.mail.mailbox'].create({
            'email': 'sales@company.test',
        })
        self.env['pan.mail.thread.link'].record(
            mailbox=other, thread_id='CONV-9',
            model='res.partner', res_id=self.partner.id,
        )

        context = self._outgoing_mail()._build_reply_context(self.mailbox)

        self.assertIsNone(context['thread_id'])


@tagged('pan_mail_pro', 'post_install', '-at_install')
class TestGmailOutgoingThreading(OutgoingThreadingCase):
    """Gmail threads on headers, and validates them before honouring threadId."""

    def _sendable(self):
        mailbox = self.env['pan.mail.mailbox'].create({
            'email': 'sales@test.local', 'provider': 'gmail',
        })
        account = self.env['pan.mail.account'].create({
            'email': 'sales@test.local', 'provider': 'gmail', 'user_id': False,
            'access_token': 'live-token', 'refresh_token': 'r',
            'token_expiry': datetime.now() + timedelta(hours=1),
        })
        return mailbox, account

    def _capture_send(self):
        captured = {}

        def _fake_post(url, headers=None, json=None, timeout=None, **kw):
            captured['json'] = json
            resp = MagicMock()
            resp.raise_for_status.return_value = None
            resp.json.return_value = {'id': 'gmail-id-1', 'threadId': 'thread-1'}
            return resp

        return patch(GMAIL_POST, side_effect=_fake_post), captured

    def _mime(self, captured):
        import base64
        from email import message_from_bytes, policy
        return message_from_bytes(
            base64.urlsafe_b64decode(captured['json']['raw']), policy=policy.default)

    def test_reply_carries_in_reply_to_and_references(self):
        mailbox, account = self._sendable()
        mail = self._outgoing_mail()
        context = {
            'in_reply_to': '<parent@example.com>',
            'references': ['<root@example.com>', '<parent@example.com>'],
            'thread_id': 'thread-9',
            'provider_message_id': None,
        }

        cm, captured = self._capture_send()
        with cm:
            result = mailbox._get_client().send_message(
                mail, mailbox, account, reply_context=context)

        self.assertTrue(result['success'])
        mime = self._mime(captured)
        self.assertEqual(mime['In-Reply-To'], '<parent@example.com>')
        self.assertEqual(mime['References'],
                         '<root@example.com> <parent@example.com>')
        self.assertEqual(captured['json']['threadId'], 'thread-9')

    def test_thread_id_is_not_claimed_without_headers_to_back_it(self):
        """Gmail rejects a threadId whose message is not a valid RFC reply.

        Sending one anyway turns a working send into a hard API error, so the
        handle is only claimed when In-Reply-To is there to justify it.
        """
        mailbox, account = self._sendable()
        context = {
            'in_reply_to': None, 'references': [],
            'thread_id': 'thread-9', 'provider_message_id': None,
        }

        cm, captured = self._capture_send()
        with cm:
            mailbox._get_client().send_message(
                self._outgoing_mail(), mailbox, account, reply_context=context)

        self.assertNotIn('threadId', captured['json'])

    def test_sending_without_a_reply_context_still_works(self):
        mailbox, account = self._sendable()

        cm, captured = self._capture_send()
        with cm:
            result = mailbox._get_client().send_message(
                self._outgoing_mail(), mailbox, account)

        self.assertTrue(result['success'])
        mime = self._mime(captured)
        self.assertIsNone(mime['In-Reply-To'])
        self.assertNotIn('threadId', captured['json'])

    def test_the_mail_names_the_database_that_sent_it(self):
        """X-Odoo-Model says an Odoo sent this; X-Odoo-Db says which one.

        Every Mail Pro stamps the same four X-Odoo-* headers, so without the
        marker a customer's own Mail Pro writing to us reads as our sent copy
        coming back: refused, and its ids re-indexed onto our rows.
        """
        mailbox, account = self._sendable()

        cm, captured = self._capture_send()
        with cm:
            mailbox._get_client().send_message(
                self._outgoing_mail(), mailbox, account)

        mime = self._mime(captured)
        self.assertEqual(mime['X-Odoo-Db'], odoo_db_marker(self.env))
        self.assertEqual(mime['X-Odoo-Model'], 'res.partner',
                         "the marker is added to the loop guard, not in place of it")


@tagged('pan_mail_pro', 'post_install', '-at_install')
class TestGraphOutgoingThreading(OutgoingThreadingCase):
    """Graph cannot be handed headers, so it replies to a message instead."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.mailbox = cls.env['pan.mail.mailbox'].create({
            'email': 'support@test.local',
        })
        cls.account = cls.env['pan.mail.account'].create({
            'email': 'support@test.local', 'provider': 'outlook', 'user_id': False,
            'access_token': 'live-token', 'refresh_token': 'r',
            'token_expiry': datetime.now() + timedelta(hours=1),
        })

    def _mock_graph(self, create_reply_fails=False):
        """Patch the Graph endpoints involved in draft → send.

        Records which URLs were hit so a test can assert *which* draft flow ran,
        which is the entire point — both flows end in a successful send.
        """
        calls = {'urls': [], 'plain_draft': None, 'patched': None}

        def _fake_post(url, headers=None, json=None, timeout=None, **kw):
            calls['urls'].append(url)
            resp = MagicMock()
            resp.raise_for_status.return_value = None
            if '/createReply' in url:
                if create_reply_fails:
                    resp.raise_for_status.side_effect = requests.exceptions.HTTPError(
                        '404 Not Found')
                    return resp
                resp.json.return_value = {'id': 'REPLY_DRAFT'}
            elif url.endswith('/messages'):
                calls['plain_draft'] = json
                resp.json.return_value = {
                    'id': 'PLAIN_DRAFT',
                    'internetMessageId': '<plain@outlook.com>',
                    'conversationId': 'CONV-PLAIN',
                }
            else:
                resp.json.return_value = {}
            return resp

        def _fake_patch(url, headers=None, json=None, timeout=None, **kw):
            calls['urls'].append(url)
            calls['patched'] = json
            resp = MagicMock()
            resp.raise_for_status.return_value = None
            resp.json.return_value = {
                'id': 'REPLY_DRAFT',
                'internetMessageId': '<threaded@outlook.com>',
                'conversationId': 'CONV-9',
            }
            return resp

        Client = type(self.env['microsoft.graph.client'])
        return calls, (
            patch.object(Client, 'get_valid_token', autospec=True,
                         return_value='fake-bearer-token'),
            patch(GRAPH_POST, side_effect=_fake_post),
            patch(GRAPH_PATCH, side_effect=_fake_patch),
        )

    def _send(self, reply_context, create_reply_fails=False):
        calls, patchers = self._mock_graph(create_reply_fails=create_reply_fails)
        mail = self._outgoing_mail()
        with patchers[0], patchers[1], patchers[2]:
            result = self.mailbox._get_client().send_message(
                mail, self.mailbox, self.account, reply_context=reply_context)
        return result, calls

    def test_reply_goes_through_create_reply_and_a_patch(self):
        result, calls = self._send({
            'in_reply_to': '<parent@example.com>',
            'references': ['<parent@example.com>'],
            'thread_id': 'CONV-9',
            'provider_message_id': 'GRAPH-MSG-9',
        })

        self.assertTrue(result['success'])
        self.assertTrue(any('/messages/GRAPH-MSG-9/createReply' in u for u in calls['urls']),
                        f"createReply was not called: {calls['urls']}")
        # The quoted stub Graph prefills must be replaced by what Odoo is
        # actually sending, or the recipient gets an empty reply.
        self.assertEqual(calls['patched']['subject'], 'Re: Order 12')
        self.assertIn('Reply body', calls['patched']['body']['content'])
        self.assertIsNone(calls['plain_draft'],
                          "a plain draft must not be created as well")
        # The ids returned come from the threaded draft, not the plain one.
        self.assertEqual(result['message_id'], '<threaded@outlook.com>')
        self.assertEqual(result['thread_id'], 'CONV-9')

    def test_a_stale_parent_falls_back_to_a_plain_draft(self):
        """Graph message ids die when a user moves or deletes the mail.

        The reply then loses its threading, which is a real cost — but it is a
        smaller one than the mail not going out at all.
        """
        result, calls = self._send({
            'in_reply_to': '<parent@example.com>', 'references': [],
            'thread_id': 'CONV-9', 'provider_message_id': 'GONE',
        }, create_reply_fails=True)

        self.assertTrue(result['success'], "send must survive a stale parent")
        self.assertIsNotNone(calls['plain_draft'])
        self.assertEqual(result['message_id'], '<plain@outlook.com>')

    def test_no_parent_means_the_plain_draft_flow(self):
        result, calls = self._send({
            'in_reply_to': None, 'references': [],
            'thread_id': None, 'provider_message_id': None,
        })

        self.assertTrue(result['success'])
        self.assertIsNotNone(calls['plain_draft'])
        self.assertFalse(any('/createReply' in u for u in calls['urls']))

    def test_the_draft_names_the_database_that_sent_it(self):
        """Graph takes the loop guard as `internetMessageHeaders`; the marker
        rides in the same list, so both senders stamp the same thing."""
        _result, calls = self._send({
            'in_reply_to': None, 'references': [],
            'thread_id': None, 'provider_message_id': None,
        })

        headers = {
            h['name']: h['value']
            for h in calls['plain_draft']['internetMessageHeaders']
        }
        self.assertEqual(headers.get('X-Odoo-Db'), odoo_db_marker(self.env))
        self.assertEqual(headers.get('X-Odoo-Model'), 'res.partner')
        # A record-bound reply is the full house: model, record, mail,
        # message, database. That is Graph's ceiling, so the count is pinned
        # here rather than discovered as a refused send.
        self.assertEqual(len(calls['plain_draft']['internetMessageHeaders']),
                         GRAPH_MAX_CUSTOM_HEADERS)


@tagged('pan_mail_pro', 'post_install', '-at_install')
class TestOneContextPerMessage(MailProTestCase):
    """A post to three followers is three sends and one reply context.

    `_one_send_per_recipient` makes a `mail.mail` per follower, and each
    used to build its own context -- the same walk up the parent chain, one
    search per ancestor -- and index the same message again. One batch now
    shares the context per message and mailbox and writes the shared index
    rows once; the rows that differ per copy (the provider's own Message-ID
    for each send) are still one per copy, because each recipient's reply
    names the one they got.
    """

    def setUp(self):
        super().setUp()
        self.second_partner = self.env['res.partner'].create({
            'name': 'Second Customer', 'email': 'second@elsewhere.example',
        })
        self.third_partner = self.env['res.partner'].create({
            'name': 'Third Customer', 'email': 'third@elsewhere.example',
        })
        self.record = self.env['res.partner'].create({
            'name': 'The Record', 'email': 'record@elsewhere.example',
        })
        # What the followers are answering: a mail that came in on the record.
        self.parent = self.record.with_context(
            mail_create_nosubscribe=True, mail_notrack=True,
        ).message_post(
            body='<p>question</p>', subject='Order 12', message_type='email',
            subtype_xmlid='mail.mt_comment', message_id='<parent@example.com>',
        )
        self.sends = []

    def _fake_send(self):
        """One provider Message-ID per send, one thread per record (as a
        reply's copies share the parent's conversation), and the reply
        context each send was handed."""
        sends = self.sends

        def send_message(client_self, mail_record, mailbox, account, reply_context=None):
            sends.append({'mail': mail_record, 'reply_context': reply_context})
            return {'success': True, 'message_id': f'<sent{len(sends)}@outlook.test>',
                    'thread_id': f'CONV-{mail_record.res_id}'}
        return patch.object(
            type(self.env['microsoft.graph.client']), 'send_message', send_message)

    def _post_to(self, partners, **vals):
        base = {
            'subject': 'Re: Order 12',
            'body_html': '<p>Answer</p>',
            'author_id': self.salesperson.partner_id.id,
            'x_send_from_mailbox_id': self.shared_mailbox.id,
            'model': 'res.partner',
            'res_id': self.record.id,
            # The follower mails Odoo creates for a chatter post.
            'is_notification': True,
            'recipient_ids': [(6, 0, partners.ids)],
        }
        base.update(vals)
        return self.env['mail.mail'].create(base)

    def test_three_followers_share_one_context_and_one_index(self):
        Mail = type(self.env['mail.mail'])
        Link = type(self.env['pan.mail.thread.link'])
        mail = self._post_to(self.external_partner | self.second_partner | self.third_partner)
        message = mail.mail_message_id

        with self._fake_send(), \
             patch.object(Mail, '_build_reply_context', autospec=True,
                          side_effect=Mail._build_reply_context) as build, \
             patch.object(Link, 'record_all', autospec=True,
                          side_effect=Link.record_all) as record_all:
            self.assertIsNone(send_and_capture(mail))

        self.assertEqual(len(self.sends), 3)
        self.assertEqual(build.call_count, 1)
        self.assertEqual(record_all.call_count, 1)

        # The three copies left as answers to the same mail.
        contexts = [send['reply_context'] for send in self.sends]
        for context in contexts:
            self.assertEqual(context['in_reply_to'], '<parent@example.com>')
            self.assertEqual(context['references'], ['<parent@example.com>'])
        self.assertEqual(
            {send['mail'].recipient_ids.email for send in self.sends},
            {'customer@example.com', 'second@elsewhere.example', 'third@elsewhere.example'})

        # What is indexed: the message's own Message-ID once, the provider's
        # three (one per send, each a reply may come back under), and the
        # conversation's two thread keys once each.
        refs = self.env['pan.mail.message.ref'].sudo().search(
            [('mail_message_id', '=', message.id)])
        self.assertEqual(
            sorted(refs.filtered(lambda r: r.source == 'odoo').mapped('message_id')),
            [message.message_id])
        self.assertEqual(
            sorted(refs.filtered(lambda r: r.source == 'provider').mapped('message_id')),
            ['<sent1@outlook.test>', '<sent2@outlook.test>', '<sent3@outlook.test>'])
        links = self.env['pan.mail.thread.link'].sudo().search([
            ('mailbox_id', '=', self.shared_mailbox.id),
            ('model', '=', 'res.partner'), ('res_id', '=', self.record.id)])
        self.assertEqual(
            {(link.thread_id, link.key_type) for link in links},
            {(f'CONV-{self.record.id}', 'provider'), ('<parent@example.com>', 'rfc')})

    def test_a_mixed_batch_keeps_one_context_per_message(self):
        """Two messages in one batch are two contexts and two sets of links:
        the memo is per message, not per batch."""
        Mail = type(self.env['mail.mail'])
        other = self.env['res.partner'].create({
            'name': 'Other Record', 'email': 'other-record@elsewhere.example',
        })
        first = self._post_to(self.external_partner | self.second_partner)
        second = self._post_to(self.third_partner, res_id=other.id)

        with self._fake_send(), \
             patch.object(Mail, '_build_reply_context', autospec=True,
                          side_effect=Mail._build_reply_context) as build:
            self.assertIsNone(send_and_capture(first | second))

        self.assertEqual(len(self.sends), 3)
        self.assertEqual(build.call_count, 2)
        by_record = {send['mail'].res_id: send['reply_context'] for send in self.sends}
        self.assertEqual(by_record[self.record.id]['in_reply_to'], '<parent@example.com>')
        self.assertIsNone(by_record[other.id]['in_reply_to'])
        links = self.env['pan.mail.thread.link'].sudo().search(
            [('mailbox_id', '=', self.shared_mailbox.id)])
        self.assertEqual(
            {(link.model, link.res_id) for link in links},
            {('res.partner', self.record.id), ('res.partner', other.id)})
