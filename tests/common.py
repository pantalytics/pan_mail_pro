# -*- coding: utf-8 -*-
"""
Shared test fixture for pan_mail_pro tests.

Provides:
- A notification mailbox with an OAuth-connected owner
- A shared mailbox (info@) without an owner
- A personal mailbox owned by a salesperson
- A salesperson user with OAuth + a default mailbox
- An external customer partner
- A portal customer (share=True user) and their partner
- A company partner whose email matches the shared mailbox
  (mimics the production scenario where a template's email_from
   resolves author_id to the company partner instead of the user)
- A `mock_graph` context manager that patches all outbound HTTP
  calls in microsoft_graph_client and records what was sent.
"""
import base64
import contextlib
from unittest.mock import patch, MagicMock

from odoo.exceptions import UserError
from odoo.tests import TransactionCase


def send_and_capture(mails):
    """Send `mails` and hand back the UserError instead of unwinding the test.

    Deliberately not `assertRaises`: Odoo wraps that in a savepoint and rolls it
    back when the exception fires, which discards exactly the `failure_reason`
    and `state` writes a test wants to look at afterwards. A plain try/except
    opens no savepoint, so the writes survive and both halves of the contract
    can be asserted in one test.

    (Production sees the same split. Interactively the request rollback does
    discard the writes and the mail stays queued for the cron; in the queue,
    `auto_commit` has already committed them. See `mail.mail.send`.)
    """
    try:
        mails.send()
    except UserError as error:
        return error
    return None


class MailProTestCase(TransactionCase):
    """Common setUp for routing + composer tests."""

    # Context that prevents auto-subscribe / tracking emails during fixture
    # setup so we don't accidentally send mail before the test body runs.
    SILENT_CTX = {
        'mail_create_nolog': True,
        'mail_create_nosubscribe': True,
        'mail_notrack': True,
        'tracking_disable': True,
        'no_reset_password': True,
    }

    @classmethod
    def _silent(cls, model_name):
        """Get a model handle with all notification side-effects suppressed."""
        return cls.env[model_name].sudo().with_context(**cls.SILENT_CTX)

    @classmethod
    def setUpClass(cls):
        super().setUpClass()

        Mailbox = cls.env['pan.mail.mailbox']
        # Suppress auth_signup's password-reset email and tracking noise.
        User = cls.env['res.users'].with_context(
            no_reset_password=True,
            mail_create_nolog=True,
            mail_create_nosubscribe=True,
            mail_notrack=True,
            tracking_disable=True,
        )
        Partner = cls.env['res.partner']

        internal_group = cls.env.ref('base.group_user').id
        cls.notif_owner = User.create({
            'name': 'Notification Owner',
            'login': 'notif_owner@test.local',
            'email': 'notif_owner@test.local',
            'notification_type': 'email',
            'group_ids': [(6, 0, [internal_group])],
        })
        cls.salesperson = User.create({
            'name': 'Sales Person',
            'login': 'sales@test.local',
            'email': 'sales@test.local',
            'notification_type': 'email',
            'group_ids': [(6, 0, [internal_group])],
        })
        cls.other_user = User.create({
            'name': 'Other User',
            'login': 'other@test.local',
            'email': 'other@test.local',
            'notification_type': 'email',
            'group_ids': [(6, 0, [internal_group])],
        })
        cls.inbox_user = User.create({
            'name': 'Inbox Only User',
            'login': 'inbox@test.local',
            'email': 'inbox@test.local',
            'notification_type': 'inbox',
            'group_ids': [(6, 0, [internal_group])],
        })
        # A customer who can log into the portal. Has a res.users row like any
        # employee, but `share=True` — the distinction issue #39 turned on.
        cls.portal_user = User.create({
            'name': 'Portal Customer',
            'login': 'portal@example.com',
            'email': 'portal@example.com',
            'notification_type': 'email',
            'group_ids': [(6, 0, [cls.env.ref('base.group_portal').id])],
        })
        assert cls.portal_user.share, (
            "Test setup error: the portal fixture must be a share user"
        )
        cls.portal_partner = cls.portal_user.partner_id

        for user in (cls.notif_owner | cls.salesperson | cls.other_user):
            cls.connect(user)

        # Incoming sync is gated on internal domains being declared. A domain
        # nothing in this fixture uses, so the gate opens without turning any
        # fixture address internal.
        cls.env['pan.mail.domain'].set_domains(['gate-fixture.test'])

        # Mailboxes
        cls.notification_mailbox = Mailbox.create({
            'email': 'notifications@company.test',
            'mailbox_type': 'personal',
            'is_notification_mailbox': True,
            'owner_user_id': cls.notif_owner.id,
        })
        cls.shared_mailbox = Mailbox.create({
            'email': 'info@company.test',
            'mailbox_type': 'shared',
        })
        cls.personal_mailbox = Mailbox.create({
            'email': 'sales@company.test',
            'mailbox_type': 'personal',
            'owner_user_id': cls.salesperson.id,
        })

        cls.salesperson.x_default_mailbox_id = cls.shared_mailbox

        # Partners
        cls.external_partner = Partner.create({
            'name': 'External Customer',
            'email': 'customer@example.com',
        })
        # Company partner mirrors the production "Emovr B.V." case:
        # email matches the shared mailbox, so Odoo's _message_compute_author
        # may resolve to this partner (which has no res.users link).
        cls.company_partner = Partner.create({
            'name': 'Acme B.V.',
            'email': 'info@company.test',
            'is_company': True,
        })
        assert not cls.company_partner.user_ids, (
            "Test setup error: company partner must not be linked to a user"
        )

    # ------------------------------------------------------------------ #
    # Credentials
    # ------------------------------------------------------------------ #

    @classmethod
    def connect(cls, user, provider='outlook'):
        """Give `user` working credentials on `provider`.

        Written through the plain-text fields so the stored ciphertext is real
        Fernet - a hand-written encrypted value raises on the next read.
        """
        return cls.env['pan.mail.account'].sudo().create({
            'email': user.email or user.login,
            'provider': provider,
            'user_id': user.id,
            'refresh_token': 'fake-refresh',
            'access_token': 'fake-access',
        })

    @classmethod
    def disconnect(cls, user, provider='outlook'):
        """Take `user`'s credentials away, as revoking access would."""
        cls.env['pan.mail.account'].sudo().with_context(active_test=False).search([
            ('user_id', '=', user.id), ('provider', '=', provider),
        ]).write({'refresh_token_encrypted': False, 'access_token_encrypted': False})

    # ------------------------------------------------------------------ #
    # Mock helpers
    # ------------------------------------------------------------------ #

    @contextlib.contextmanager
    def mock_graph(self, draft_id='DRAFT_ID', message_id='<msg@test>',
                   conversation_id='CONV_ID'):
        """Patch every outbound Graph API call.

        Yields a `calls` dict with keys:
          - 'draft':         JSON payload sent to POST /messages
          - 'send_url':      URL hit on POST /messages/{id}/send
          - 'attach':        list of attachment payloads sent directly
          - 'upload_session':list of (name, content_type, total_bytes) tuples
          - 'upload_chunks': list of (url, byte_count) tuples for PUT chunks
          - 'token_account': last account passed to get_valid_token
        """
        calls = {
            'draft': None,
            'send_url': None,
            'attach': [],
            'upload_session': [],
            'upload_chunks': [],
            'token_account': None,
        }

        def fake_get_valid_token(client_self, account):
            calls['token_account'] = account
            return 'fake-bearer-token'

        def fake_post(url, headers=None, json=None, data=None, timeout=None, **kwargs):
            resp = MagicMock()
            resp.raise_for_status.return_value = None

            if url.endswith('/messages'):
                # Draft creation
                calls['draft'] = json
                resp.json.return_value = {
                    'id': draft_id,
                    'internetMessageId': message_id,
                    'conversationId': conversation_id,
                }
            elif url.endswith('/send'):
                calls['send_url'] = url
                resp.json.return_value = {}
            elif '/createUploadSession' in url:
                calls['upload_session'].append({
                    'url': url,
                    'payload': json,
                })
                resp.json.return_value = {
                    'uploadUrl': f'https://upload.test/session/{len(calls["upload_session"])}',
                }
            elif '/attachments' in url:
                # Direct attachment add
                calls['attach'].append(json)
                resp.json.return_value = {'id': f'att_{len(calls["attach"])}'}
            else:
                resp.json.return_value = {}
            return resp

        def fake_put(url, headers=None, data=None, timeout=None, **kwargs):
            resp = MagicMock()
            resp.raise_for_status.return_value = None
            byte_count = len(data) if data is not None else 0
            calls['upload_chunks'].append((url, byte_count))
            resp.json.return_value = {}
            return resp

        Client = type(self.env['microsoft.graph.client'])
        with patch.object(Client, 'get_valid_token', autospec=True,
                          side_effect=fake_get_valid_token), \
             patch('odoo.addons.pan_mail_pro.models.providers.microsoft.graph_client.requests.post',
                   side_effect=fake_post), \
             patch('odoo.addons.pan_mail_pro.models.providers.microsoft.graph_client.requests.put',
                   side_effect=fake_put):
            yield calls

    # ------------------------------------------------------------------ #
    # Convenience constructors
    # ------------------------------------------------------------------ #

    def make_attachment(self, name='attachment.pdf', size_bytes=1024,
                        mimetype='application/pdf'):
        """Create an ir.attachment with the requested raw size."""
        raw = b'\x00' * size_bytes
        return self.env['ir.attachment'].create({
            'name': name,
            'datas': base64.b64encode(raw),
            'mimetype': mimetype,
        })

    def get_pending_mails(self, model=None, res_id=None):
        """Fetch outbound mails created during the test, newest first."""
        domain = []
        if model:
            domain.append(('model', '=', model))
        if res_id:
            domain.append(('res_id', '=', res_id))
        return self.env['mail.mail'].search(domain, order='id desc')
