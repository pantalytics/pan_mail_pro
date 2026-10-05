# -*- coding: utf-8 -*-
"""Every public method on a model answers `call_kw` from any logged-in session.

A group on a menu is not an access rule, and a `sudo()` inside a public
`@api.model` method is a way for anybody to do what that method does. The
Inbox learnt this in 19.0.10.0.0 (`pan.mail.conversation._check_caller`); the
domain list, the Pantalytics connection and the two indexes had not. So this
file walks them as a plain user and expects a refusal, and it pins the public
surface of the models that search as sudo, so a new public method there is a
decision somebody made rather than a door somebody left open.
"""
from odoo.exceptions import AccessError
from odoo.tests import TransactionCase, new_test_user, tagged

# Public methods that may stay public: they read nothing a plain user could
# not compute themselves, or take a recordset no RPC caller can hand over.
ALLOWED_PUBLIC = {
    'pan.mail.matcher': {'describe', 'thread_keys'},
    # Counts only, over a domain the method fixes itself. `_counts(domain)`
    # took the caller's domain and searched as sudo: a count oracle over
    # every message body, one guess at a time. It is private now.
    'pan.mail.coverage': {
        'counts_since',
        # The three buttons on the coverage form; a button target has to be
        # public, and each opens an ACL-bound list the caller sees filtered.
        'action_view_unlinked', 'action_view_contact_only', 'action_view_all',
    },
    # Codes and counts, nothing that names anyone. `_record()` writes as the
    # superuser on a cursor of its own; public, it let any session fill the
    # ledger and push its own strings into the heartbeat.
    'pan.mail.error': {'codes_since', 'signature_since'},
    'pan.mail.message.ref': {'record'},
    'pan.mail.thread.link': {'record', 'record_all'},
    # The whole API the Inbox has. Every one of these starts with
    # `_check_caller()` -- directly, or through `_own_mailbox()` for the four
    # live reads -- which is what the two loops below prove. A method added
    # here is a method added to `CONVERSATION_CALLS` too, or the loops fail.
    'pan.mail.conversation': {
        'failure_remedy', 'inbox_search_view_id', 'folder_counts',
        'search_conversations', 'read_conversation', 'conversation_messages',
        'set_read', 'refresh_read_state', 'record_conversations',
        'customer_timeline', 'live_mailboxes', 'live_messages',
        'read_live_message', 'import_live_message', 'link_targets',
        'link_scope', 'new_mail_recipients',
    },
}


def public_methods(env, model_name):
    """The public, callable names a model adds over the registry's `base`."""
    # `base` is the registry's own abstract model: every method that web,
    # mail and the ORM hang on all models, and nothing of this module's.
    base = set(dir(type(env['base'])))
    Model = type(env[model_name])
    return {
        name for name in dir(Model)
        if not name.startswith('_') and callable(getattr(Model, name, None))
    } - base


@tagged('pan_mail_pro', 'post_install', '-at_install')
class TestRpcSurface(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.plain = new_test_user(cls.env, login='rpc_plain', groups='base.group_user')
        cls.manager = new_test_user(
            cls.env, login='rpc_manager',
            groups='base.group_user,pan_mail_pro.group_mail_mailbox_manager')
        cls.env['pan.mail.domain'].set_domains(['surface.test'])
        # Enough of a database for every Inbox method to be called with
        # arguments that would work: a mailbox, a contact, a lead.
        cls.mailbox = cls.env['pan.mail.mailbox'].create({
            'email': 'inbox@surface.test',
            'provider': 'imap',
            'mailbox_type': 'shared',
        })
        cls.customer = cls.env['res.partner'].create({
            'name': 'Surface Customer', 'email': 'customer@surface-customer.test',
        })
        cls.lead = cls.env['crm.lead'].create({
            'name': 'Surface lead', 'partner_id': cls.customer.id,
        })

    def _conversation_calls(self):
        """Minimal valid arguments for every public method of the Inbox's
        read layer. Keyed by name so the loops below can ask for a method
        the pin above knows and this map does not, and fail on it."""
        return {
            'failure_remedy': (),
            'inbox_search_view_id': (),
            'folder_counts': (),
            'search_conversations': (),
            'read_conversation': ('crm.lead', self.lead.id),
            'conversation_messages': ('crm.lead', self.lead.id),
            'set_read': ('crm.lead', self.lead.id),
            'refresh_read_state': (),
            'record_conversations': ('crm.lead', self.lead.id),
            'customer_timeline': (self.customer.id,),
            'live_mailboxes': (),
            'live_messages': (self.mailbox.id,),
            'read_live_message': (self.mailbox.id, 'provider-id'),
            'import_live_message': (self.mailbox.id, 'provider-id'),
            'link_targets': (),
            'link_scope': ('crm.lead',),
            'new_mail_recipients': ('crm.lead', self.lead.id),
        }

    def _every_inbox_method_refuses(self, Conversation, who):
        """Call every public method of `pan.mail.conversation` as `who` and
        expect `_check_caller` to refuse each one before it reads anything.

        The list of methods is read off the model, not written out here, so
        a method added without the guard is a method this loop reaches: it
        has to appear in `_conversation_calls()` to be callable at all, and
        it has to raise once it is.
        """
        calls = self._conversation_calls()
        names = public_methods(self.env, 'pan.mail.conversation')
        missing = names - set(calls)
        self.assertFalse(
            missing,
            f'pan.mail.conversation grew {sorted(missing)}; add the minimal '
            f'arguments to _conversation_calls() so the guard is tested')
        for name in sorted(names):
            with self.subTest(method=name, who=who):
                with self.assertRaises(AccessError):
                    getattr(Conversation, name)(*calls[name])

    def test_the_domain_list_is_a_managers_to_change(self):
        Domains = self.env['pan.mail.domain']
        with self.assertRaises(AccessError):
            Domains.with_user(self.plain).set_domains(['evil.test'])
        self.assertEqual(Domains.get_domains(), ['surface.test'])

        Domains.with_user(self.manager).set_domains(['surface.test', 'second.test'])
        self.assertEqual(sorted(Domains.get_domains()), ['second.test', 'surface.test'])

    def test_the_pantalytics_connection_is_an_administrators(self):
        License = self.env['pan.mail.license']
        with self.assertRaises(AccessError):
            License.with_user(self.plain).action_connect()
        link = License.sudo().create({'status': 'pending'})
        with self.assertRaises(AccessError):
            link.with_user(self.plain).action_check_approval()
        with self.assertRaises(AccessError):
            link.with_user(self.plain).action_disconnect()
        self.assertEqual(link.status, 'pending')

    def test_the_sudo_lookups_are_not_public(self):
        """`match`, `lookup` and `find_for_record` searched as sudo across every
        document model and answered which record a reference belongs to.
        They are private now; this is what keeps them so."""
        for model_name, allowed in ALLOWED_PUBLIC.items():
            public = public_methods(self.env, model_name)
            self.assertEqual(
                public, allowed,
                f'{model_name} grew a public method; decide whether RPC may call it '
                f'and add it to ALLOWED_PUBLIC, or make it private')

    def test_the_inbox_is_for_mailbox_managers_on_every_method(self):
        """`pan.mail.conversation` answers `call_kw` from any session, and
        the group on its menu protects nothing on its own. Every public
        method is walked as a plain internal user, `inbox_search_view_id`
        included: a view id is harmless, but the method still asks
        `_check_caller`, so it is not deliberately open and the loop treats
        it like the rest."""
        self._every_inbox_method_refuses(
            self.env['pan.mail.conversation'].with_user(self.plain), 'plain user')

    def test_the_inbox_is_closed_on_an_unconnected_instance_on_every_method(self):
        """The second half of `_check_caller`: a mailbox manager on an Odoo
        that is not linked to a Pantalytics account is refused by every
        read, not by the three `tests/test_conversation_api.py` names.
        `pan_mail_pro_real_gate` is what turns the test switch in
        `tests/connected.py` off and asks the real `sync_allowed()`."""
        self.assertFalse(
            self.env['pan.mail.license'].with_context(
                pan_mail_pro_real_gate=True).sync_allowed(),
            'the fixture is not connected, or the gate would not be tested')
        self._every_inbox_method_refuses(
            self.env['pan.mail.conversation'].with_user(self.manager).with_context(
                pan_mail_pro_real_gate=True),
            'manager on an unconnected instance')
