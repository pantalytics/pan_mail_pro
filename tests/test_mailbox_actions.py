# -*- coding: utf-8 -*-
"""The mailbox actions: folders, search, marking, filing and drafts.

The surface these guard is deliberately the one Squirrel (our MCP server)
exposes, so the two products cannot drift into meaning different things by the
same word. `TestActionParity` is the check that says so out loud; the rest
pins the two rules that make the actions safe to hand to an agent -- delete is
a move to Trash, and marking may not remove anybody's mail -- in each of the
three providers, because each one gets them wrong in its own way.
"""
from unittest.mock import MagicMock, patch

from odoo.exceptions import UserError
from odoo.tests import TransactionCase, tagged

from odoo.addons.pan_mail_pro.models.mail_provider_client import (
    FOLDER_ARCHIVE,
    FOLDER_INBOX,
    FOLDER_ROLES,
    FOLDER_TRASH,
    PROVIDER_CLIENTS,
    get_provider_client,
)

from .common import MailProTestCase
from .test_imap_provider import FakeImap

GRAPH_MODULE = 'odoo.addons.pan_mail_pro.models.providers.microsoft.graph_client'
GMAIL_MODULE = 'odoo.addons.pan_mail_pro.models.providers.google.gmail_client'
IMAP_MODULE = 'odoo.addons.pan_mail_pro.models.providers.imap_smtp.imap_client'

# What Squirrel calls each action, and what this contract calls it. A rename on
# either side is a breaking change to one of two shipped products, so it is
# written down where a test can fail on it rather than left to memory.
SQUIRREL_TO_CONTRACT = {
    'mail_list_folders': 'list_folders',
    'mail_search': 'search_messages',
    'mail_read': 'get_message',
    'mail_get_attachment': 'get_message_attachments',
    'mail_send': 'send_message',
    'mail_create_draft': 'save_draft',
    'mail_edit_draft': 'update_draft',
    'mail_send_draft': 'send_draft',
    'mail_move': 'move_messages',
    'mail_delete': 'delete_messages',
    'mail_flag': 'set_flagged',
    'mail_mark_read': 'set_seen',
    'mail_create_folder': 'create_folder',
    'mail_rename_folder': 'rename_folder',
    'mail_delete_folder': 'delete_folder',
}


def json_response(payload=None, status=200):
    resp = MagicMock()
    resp.status_code = status
    resp.content = b'{}'
    resp.raise_for_status.return_value = None
    resp.json.return_value = payload if payload is not None else {}
    return resp


@tagged('pan_mail_pro', 'post_install', '-at_install')
class TestActionParity(TransactionCase):
    """Every action Squirrel offers has a counterpart here, in every provider."""

    def test_every_squirrel_tool_maps_to_a_contract_method(self):
        contract = self.env['mail.provider.client']
        for tool, method in SQUIRREL_TO_CONTRACT.items():
            self.assertTrue(
                hasattr(contract, method),
                f"Squirrel's {tool} has no counterpart on the contract ({method}())",
            )

    def test_every_client_implements_every_action(self):
        """A client that inherits the contract's stub raises NotImplementedError
        at the caller instead of at import, which is the failure this catches."""
        contract = self.env['mail.provider.client']
        for code in PROVIDER_CLIENTS:
            client = get_provider_client(self.env, code)
            for tool, method in SQUIRREL_TO_CONTRACT.items():
                self.assertIsNot(
                    getattr(type(client), method, None),
                    getattr(type(contract), method),
                    f"'{code}' does not implement {method}() ({tool})",
                )

    def test_an_unknown_folder_role_is_refused_not_guessed(self):
        contract = self.env['mail.provider.client']
        for role in FOLDER_ROLES:
            contract._check_folder_role(role)
        with self.assertRaises(UserError):
            contract._check_folder_role('outbox')


@tagged('pan_mail_pro', 'post_install', '-at_install')
class TestGraphActions(MailProTestCase):
    """Microsoft 365: the folder is well-known, the message id is not stable."""

    def setUp(self):
        super().setUp()
        self.client = get_provider_client(self.env, 'outlook')
        self.mailbox = self.notification_mailbox
        self.account = self.client.resolve_receiving_account(self.mailbox)
        self.calls = []

    def _graph(self, get=None):
        """Patch every verb the actions use and record what was called."""
        def record(method):
            def call(url, headers=None, timeout=None, **kwargs):
                self.calls.append((method, url, kwargs.get('json')))
                if method == 'get' and get:
                    return json_response(get(url, kwargs.get('params') or {}))
                return json_response({'id': 'new-id'})
            return call

        return [
            patch.object(type(self.client), 'get_valid_token',
                         autospec=True, return_value='token'),
            patch(f'{GRAPH_MODULE}.requests.get', side_effect=record('get')),
            patch(f'{GRAPH_MODULE}.requests.post', side_effect=record('post')),
            patch(f'{GRAPH_MODULE}.requests.patch', side_effect=record('patch')),
            patch(f'{GRAPH_MODULE}.requests.delete', side_effect=record('delete')),
        ]

    @staticmethod
    def _stack(patches):
        import contextlib
        stack = contextlib.ExitStack()
        for item in patches:
            stack.enter_context(item)
        return stack

    def test_marking_read_patches_the_message_and_nothing_else(self):
        with self._stack(self._graph()):
            marked = self.client.set_seen(self.account, self.mailbox, ['abc'])
        self.assertEqual(marked, 1)
        self.assertEqual(
            [(method, body) for method, _url, body in self.calls],
            [('patch', {'isRead': True})],
        )

    def test_unflagging_sets_notflagged_rather_than_clearing_the_field(self):
        with self._stack(self._graph()):
            self.client.set_flagged(self.account, self.mailbox, ['abc'], flagged=False)
        self.assertEqual(self.calls[0][2], {'flag': {'flagStatus': 'notFlagged'}})

    def test_delete_moves_to_deleted_items_and_never_deletes(self):
        """Graph's DELETE on a message already in Deleted Items is permanent,
        which is the one thing this method exists not to do."""
        def get(url, params):
            if url.endswith('/mailFolders/DeletedItems'):
                return {'id': 'trash-folder'}
            return {'id': 'abc', 'parentFolderId': 'inbox-folder'}

        with self._stack(self._graph(get=get)):
            moved, trash = self.client.delete_messages(self.account, self.mailbox, ['abc'])
        self.assertEqual(trash, 'trash-folder')
        self.assertEqual(moved, ['new-id'])
        self.assertFalse([c for c in self.calls if c[0] == 'delete'])
        self.assertIn(('post', {'destinationId': 'trash-folder'}),
                      [(m, b) for m, _u, b in self.calls])

    def test_deleting_what_is_already_deleted_is_refused(self):
        def get(url, params):
            if url.endswith('/mailFolders/DeletedItems'):
                return {'id': 'trash-folder'}
            return {'id': 'abc', 'parentFolderId': 'trash-folder'}

        with self._stack(self._graph(get=get)), self.assertRaises(UserError):
            self.client.delete_messages(self.account, self.mailbox, ['abc'])

    def test_a_folder_with_mail_in_it_is_not_deleted(self):
        def get(url, params):
            if url.endswith('/mailFolders/DeletedItems'):
                return {'id': 'trash-folder'}
            return {'id': 'f1', 'displayName': 'Quotes', 'totalItemCount': 3,
                    'childFolderCount': 0}

        with self._stack(self._graph(get=get)):
            with self.assertRaises(UserError):
                self.client.delete_folder(self.account, self.mailbox, 'f1')
        self.assertFalse([c for c in self.calls if c[0] == 'delete'])

    def test_a_role_folder_cannot_be_renamed(self):
        with self._stack(self._graph()), self.assertRaises(UserError):
            self.client.rename_folder(self.account, self.mailbox, FOLDER_TRASH, 'Bin')

    def test_search_without_free_text_orders_newest_first(self):
        captured = {}

        def get(url, params):
            captured.update(params)
            return {'value': []}

        with self._stack(self._graph(get=get)):
            self.client.search_messages(self.account, self.mailbox,
                                        unread_only=True, limit=10)
        self.assertEqual(captured.get('$orderby'), 'receivedDateTime desc')
        self.assertIn('isRead eq false', captured.get('$filter', ''))
        self.assertNotIn('$search', captured)

    def test_search_with_free_text_drops_the_filter_graph_would_refuse(self):
        captured = {}

        def get(url, params):
            captured.update(params)
            return {'value': []}

        with self._stack(self._graph(get=get)):
            self.client.search_messages(self.account, self.mailbox, query='invoice',
                                        sender='a@b.test', has_attachment=True)
        self.assertIn('invoice', captured.get('$search', ''))
        self.assertIn('from:a@b.test', captured.get('$search', ''))
        self.assertNotIn('$filter', captured)

    def test_limit_is_clamped_where_it_enters(self):
        captured = {}

        def get(url, params):
            captured.update(params)
            return {'value': []}

        with self._stack(self._graph(get=get)):
            self.client.search_messages(self.account, self.mailbox, limit=100000)
        self.assertEqual(captured['$top'], 200)


@tagged('pan_mail_pro', 'post_install', '-at_install')
class TestGmailActions(MailProTestCase):
    """Gmail: folders are labels, so a move is a relabel and the id survives."""

    def setUp(self):
        super().setUp()
        self.client = get_provider_client(self.env, 'gmail')
        self.mailbox = self.env['pan.mail.mailbox'].create({
            'email': 'gmail@company.test',
            'provider': 'gmail',
        })
        self.account = self.env['pan.mail.account'].create({
            'email': 'gmail@company.test',
            'provider': 'gmail',
            'refresh_token': 'refresh',
            'access_token': 'access',
        })
        self.calls = []

    def _gmail(self, get=None):
        import contextlib

        def record(method):
            def call(*args, **kwargs):
                url = args[1] if method == 'request' else args[0]
                verb = args[0] if method == 'request' else method
                self.calls.append((verb, url, kwargs.get('json')))
                return json_response({'id': 'x'})
            return call

        def fake_get(url, headers=None, params=None, timeout=None, **kwargs):
            self.calls.append(('get', url, None))
            return json_response(get(url, params or {}) if get else {})

        stack = contextlib.ExitStack()
        stack.enter_context(patch.object(type(self.client), 'get_valid_token',
                                         autospec=True, return_value='token'))
        stack.enter_context(patch(f'{GMAIL_MODULE}.requests.get', side_effect=fake_get))
        stack.enter_context(patch(f'{GMAIL_MODULE}.requests.request',
                                  side_effect=record('request')))
        return stack

    def test_archiving_takes_the_message_out_of_the_inbox(self):
        """Gmail has no Archive label: archived is a message with none of the
        three location labels, so the role has to be named rather than mapped."""
        with self._gmail():
            ids = self.client.move_messages(self.account, self.mailbox, ['m1'],
                                            FOLDER_ARCHIVE)
        self.assertEqual(ids, ['m1'], "Gmail keeps a message's id across a move")
        body = self.calls[-1][2]
        self.assertEqual(sorted(body['removeLabelIds']), ['INBOX', 'SPAM', 'TRASH'])
        self.assertNotIn('addLabelIds', body)

    def test_moving_to_a_label_takes_the_other_locations_off(self):
        with self._gmail():
            self.client.move_messages(self.account, self.mailbox, ['m1'], 'Label_7')
        body = self.calls[-1][2]
        self.assertEqual(body['addLabelIds'], ['Label_7'])
        self.assertEqual(sorted(body['removeLabelIds']), ['INBOX', 'SPAM', 'TRASH'])

    def test_delete_uses_trash_not_gmails_permanent_delete(self):
        with self._gmail(get=lambda url, params: {'id': 'm1', 'labelIds': ['INBOX']}):
            ids, trash = self.client.delete_messages(self.account, self.mailbox, ['m1'])
        self.assertEqual((ids, trash), (['m1'], 'TRASH'))
        self.assertTrue(any(url.endswith('/trash') for _v, url, _b in self.calls))
        self.assertFalse([c for c in self.calls if c[0] == 'delete'])

    def test_deleting_what_is_already_in_trash_is_refused(self):
        with self._gmail(get=lambda url, params: {'id': 'm1', 'labelIds': ['TRASH']}):
            with self.assertRaises(UserError):
                self.client.delete_messages(self.account, self.mailbox, ['m1'])

    def test_marking_read_removes_the_unread_label(self):
        with self._gmail():
            self.client.set_seen(self.account, self.mailbox, ['m1'])
            self.client.set_seen(self.account, self.mailbox, ['m1'], seen=False)
        self.assertEqual(self.calls[0][2], {'removeLabelIds': ['UNREAD']})
        self.assertEqual(self.calls[1][2], {'addLabelIds': ['UNREAD']})

    def test_a_system_label_cannot_be_deleted(self):
        with self._gmail(), self.assertRaises(UserError):
            self.client.delete_folder(self.account, self.mailbox, FOLDER_TRASH)

    def test_archive_is_refused_where_there_is_no_label_to_name(self):
        """Answering with INBOX or with nothing would both file mail somewhere
        the caller did not ask for."""
        with self.assertRaises(UserError):
            self.client._gmail_label_id(FOLDER_ARCHIVE)


class ActionImap(FakeImap):
    """FakeImap plus the commands the actions use."""

    def __init__(self, *args, capabilities=('IMAP4REV1', 'MOVE', 'UIDPLUS'), **kwargs):
        super().__init__(*args, **kwargs)
        self.capabilities = capabilities
        self.commands = []
        self.created = []
        self.renamed = []
        self.deleted = []
        self.status_count = 0

    def uid(self, command, *args):
        self.commands.append((command, args))
        if command == 'STORE':
            return ('OK', [b'1 (UID 7 FLAGS (\\Seen))'])
        if command in ('MOVE', 'COPY'):
            return ('OK', [b'[COPYUID 99 7 12] Done'])
        if command == 'EXPUNGE':
            return ('OK', [b'Expunged'])
        if command == 'SEARCH' and self.searched is None and not self.uids:
            self.searched = args
            return ('OK', [b''])
        return super().uid(command, *args)

    def status(self, name, what):
        self.status_count += 1
        return ('OK', [b'"%s" (MESSAGES %d)' % (name.encode(), self.status_count - 1)])

    def create(self, name):
        self.created.append(name)
        return ('OK', [b'Created'])

    def subscribe(self, name):
        return ('OK', [b'Subscribed'])

    def rename(self, old, new):
        self.renamed.append((old, new))
        return ('OK', [b'Renamed'])

    def delete(self, name):
        self.deleted.append(name)
        return ('OK', [b'Deleted'])


@tagged('pan_mail_pro', 'post_install', '-at_install')
class TestImapActions(MailProTestCase):
    """IMAP: the folder name is not knowable and EXPUNGE is not scoped."""

    FOLDERS = [
        b'(\\HasNoChildren) "." "INBOX"',
        b'(\\HasNoChildren \\Sent) "." "Verzonden items"',
        b'(\\HasNoChildren \\Trash) "." "Prullenbak"',
        b'(\\HasNoChildren \\Drafts) "." "Concepten"',
        b'(\\HasNoChildren) "." "Archive"',
        b'(\\HasNoChildren) "." "Klanten"',
    ]

    def setUp(self):
        super().setUp()
        self.client = get_provider_client(self.env, 'imap')
        self.mailbox = self.env['pan.mail.mailbox'].create({
            'email': 'imap@company.test',
            'provider': 'imap',
        })
        self.account = self.env['pan.mail.account'].create({
            'email': 'imap@company.test',
            'provider': 'imap',
            'imap_host': 'imap.test',
            'smtp_host': 'smtp.test',
            'username': 'imap@company.test',
            'password': 'secret',
        })

    def _imap(self, **kwargs):
        kwargs.setdefault('folders', self.FOLDERS)
        fake = ActionImap(**kwargs)
        return fake, patch(f'{IMAP_MODULE}.imaplib.IMAP4_SSL', return_value=fake)

    def test_a_folders_role_comes_from_its_flags_not_its_name(self):
        """"Prullenbak" is the Trash and "Archive" is not the archive: the
        first says so in its SPECIAL-USE flags and the second only looks the
        part."""
        fake, patcher = self._imap()
        with patcher:
            folders = self.client.list_folders(self.account, self.mailbox)
        by_name = {f['name']: f['role'] for f in folders}
        self.assertEqual(by_name['Prullenbak'], FOLDER_TRASH)
        self.assertEqual(by_name['Verzonden items'], 'sent')
        self.assertEqual(by_name['INBOX'], FOLDER_INBOX)
        self.assertIsNone(by_name['Archive'],
                          'a folder the server did not flag has no role')

    def test_marking_read_stores_and_never_expunges(self):
        """An EXPUNGE here would permanently remove whatever another mail
        client left marked \\Deleted in the folder. Marking is meant to be the
        one mail write a person can undo."""
        fake, patcher = self._imap()
        with patcher:
            marked = self.client.set_seen(self.account, self.mailbox, ['INBOX:42:7'])
        self.assertEqual(marked, 1)
        self.assertEqual([c[0] for c in fake.commands], ['STORE'])
        self.assertEqual(fake.commands[0][1][1:], ('+FLAGS', '(\\Seen)'))
        self.assertFalse(fake.readonly, 'a STORE needs the folder open for writing')

    def test_unflagging_removes_the_marker(self):
        fake, patcher = self._imap()
        with patcher:
            self.client.set_flagged(self.account, self.mailbox, ['INBOX:42:7'],
                                    flagged=False)
        self.assertEqual(fake.commands[0][1][1:], ('-FLAGS', '(\\Flagged)'))

    def test_a_reference_from_before_a_renumber_is_skipped(self):
        fake, patcher = self._imap(uidvalidity=b'99')
        with patcher:
            marked = self.client.set_seen(self.account, self.mailbox, ['INBOX:42:7'])
        self.assertEqual(marked, 0)
        self.assertFalse(fake.commands)

    def test_delete_moves_to_the_trash_the_server_named(self):
        fake, patcher = self._imap()
        with patcher:
            moved, trash = self.client.delete_messages(
                self.account, self.mailbox, ['INBOX:42:7'])
        self.assertEqual(trash, 'Prullenbak')
        self.assertEqual(moved, ['trash:99:12'])
        self.assertIn('MOVE', [c[0] for c in fake.commands])
        self.assertNotIn('EXPUNGE', [c[0] for c in fake.commands])

    def test_deleting_out_of_the_trash_is_refused(self):
        fake, patcher = self._imap()
        with patcher, self.assertRaises(UserError):
            self.client.delete_messages(self.account, self.mailbox, ['Prullenbak:42:7'])

    def test_a_copy_is_expunged_by_uid_never_by_folder(self):
        """Without MOVE the source copy has to go, and a bare EXPUNGE would
        take another client's pending deletions with it."""
        fake, patcher = self._imap(capabilities=('IMAP4REV1', 'UIDPLUS'))
        with patcher:
            self.client.move_messages(self.account, self.mailbox, ['INBOX:42:7'],
                                      'Klanten')
        commands = [c for c in fake.commands if c[0] == 'EXPUNGE']
        self.assertEqual(len(commands), 1)
        self.assertEqual(commands[0][1], ('7',),
                         'UID EXPUNGE must name the uids it may remove')

    def test_a_server_with_neither_move_nor_uidplus_is_refused(self):
        fake, patcher = self._imap(capabilities=('IMAP4REV1',))
        with patcher, self.assertRaises(UserError):
            self.client.move_messages(self.account, self.mailbox, ['INBOX:42:7'],
                                      'Klanten')
        self.assertFalse([c for c in fake.commands if c[0] in ('MOVE', 'COPY')])

    def test_a_crafted_reference_cannot_smuggle_imap_into_a_command(self):
        fake, patcher = self._imap()
        with patcher, self.assertRaises(UserError):
            self.client.set_seen(self.account, self.mailbox,
                                 ['INBOX:42:1 UID STORE 1:* +FLAGS (\\Deleted)'])

    def test_a_child_folder_is_joined_with_the_servers_own_delimiter(self):
        """"/" on one server and "." on the next, which is why the caller
        passes a parent rather than typing a path."""
        fake, patcher = self._imap()
        with patcher:
            name, created = self.client.create_folder(
                self.account, self.mailbox, 'Offertes', parent='Klanten')
        self.assertEqual((name, created), ('Klanten.Offertes', True))
        self.assertEqual(fake.created, ['"Klanten.Offertes"'])

    def test_creating_a_folder_that_exists_is_not_a_failure(self):
        fake, patcher = self._imap()
        with patcher:
            name, created = self.client.create_folder(self.account, self.mailbox,
                                                      'Klanten')
        self.assertEqual((name, created), ('Klanten', False))
        self.assertFalse(fake.created)

    def test_a_folder_holding_mail_is_not_deleted(self):
        fake, patcher = self._imap()
        fake.status_count = 1  # the next STATUS answers "1 message"
        with patcher, self.assertRaises(UserError):
            self.client.delete_folder(self.account, self.mailbox, 'Klanten')
        self.assertFalse(fake.deleted)

    def test_the_trash_cannot_be_deleted_even_by_its_localized_name(self):
        fake, patcher = self._imap()
        with patcher, self.assertRaises(UserError):
            self.client.delete_folder(self.account, self.mailbox, 'Prullenbak')
        self.assertFalse(fake.deleted)

    def test_inbox_cannot_be_renamed(self):
        fake, patcher = self._imap()
        with patcher, self.assertRaises(UserError):
            self.client.rename_folder(self.account, self.mailbox, 'INBOX', 'Post')
        self.assertFalse(fake.renamed)

    def test_a_rename_keeps_the_folder_where_it_is(self):
        fake, patcher = self._imap()
        fake.folders = self.FOLDERS + [b'(\\HasNoChildren) "." "Klanten.Offertes"']
        with patcher:
            new_name = self.client.rename_folder(
                self.account, self.mailbox, 'Klanten.Offertes', 'Aanvragen')
        self.assertEqual(new_name, 'Klanten.Aanvragen')
