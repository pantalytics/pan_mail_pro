# -*- coding: utf-8 -*-
"""Who may send from which mailbox.

A personal mailbox sends with its *owner's* delegated token. The composer's
view domain filters what the dropdown offers, but the field is writable over
RPC, so without a server-side check any internal user can send mail as a
colleague — signed by that colleague's own credentials. Microsoft does not
stop it, because it is not a SendAs.

**Where the boundary actually bites**, learned from CI rather than assumed:

- A plain internal user cannot create `mail.mail` at all in Odoo 19 — the ORM
  refuses before any of this module's code runs. Their only route to sending
  is `mail.compose.message`, so the `@api.constrains` on the composer field is
  the check that protects them. An earlier version of this file asserted an
  AccessError on `mail.mail.create()` as a normal user and passed for the
  wrong reason: Odoo's own ACL raised it, not our check.
- A privileged-but-not-superuser actor (an administrator) *can* create
  `mail.mail` directly, and for them the create/write guard is the boundary.
  Those tests use the admin user for exactly that reason.
- Mail created under `sudo()` — every notification Odoo sends — skips the
  create guard by design. The send-time check catches that case, by asking
  about the mail's *author* rather than whoever is executing.
"""
from odoo.exceptions import AccessError, ValidationError
from odoo.tests import tagged
from odoo.tools import mute_logger

from odoo.addons.pan_mail_pro.models.mail_mail import RoutingError

from .common import MailProTestCase


@tagged('pan_mail_pro', 'post_install', '-at_install')
class TestMailboxPermission(MailProTestCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        # Privileged, but not the superuser: env.su is False, so the guard
        # applies. This is the actor the create/write check exists for.
        cls.admin = cls.env.ref('base.user_admin')

    def _mail_vals(self, mailbox=None):
        vals = {
            'subject': 'Test',
            'body_html': '<p>Test</p>',
            'email_to': 'customer@example.com',
        }
        if mailbox is not None:
            vals['x_send_from_mailbox_id'] = mailbox.id
        return vals

    # -- the rule itself --------------------------------------------------- #

    def test_personal_mailbox_belongs_to_its_owner(self):
        self.assertTrue(self.personal_mailbox._is_sendable_by(self.salesperson))
        self.assertFalse(self.personal_mailbox._is_sendable_by(self.other_user))

    def test_shared_mailbox_is_shared_on_purpose(self):
        self.assertTrue(self.shared_mailbox._is_sendable_by(self.other_user))
        self.assertTrue(self.shared_mailbox._is_sendable_by(self.salesperson))

    def test_notification_mailbox_stays_usable_by_anyone(self):
        """It sends with the owner's token, but that is what it is for.

        Every internal notification leaves through this mailbox whoever wrote
        the message. Restricting it would break the module, not secure it.
        """
        self.assertTrue(self.notification_mailbox._is_sendable_by(self.other_user))

    def test_archived_mailbox_is_not_sendable(self):
        self.shared_mailbox.active = False
        self.assertFalse(self.shared_mailbox._is_sendable_by(self.salesperson))

    # -- enforcement on mail.mail (privileged actor) ----------------------- #

    def test_admin_cannot_send_from_someone_elses_personal_mailbox(self):
        with self.assertRaises(AccessError):
            self.env['mail.mail'].with_user(self.admin).create(
                self._mail_vals(self.personal_mailbox)
            )

    def test_context_key_is_guarded_too(self):
        """mail.compose.message passes the mailbox through the context."""
        with self.assertRaises(AccessError):
            self.env['mail.mail'].with_user(self.admin).with_context(
                send_from_mailbox_id=self.personal_mailbox.id
            ).create(self._mail_vals())

    def test_reassigning_on_write_is_guarded(self):
        mail = self.env['mail.mail'].with_user(self.admin).create(
            self._mail_vals(self.shared_mailbox)
        )
        with self.assertRaises(AccessError):
            mail.write({'x_send_from_mailbox_id': self.personal_mailbox.id})

    def test_shared_mailbox_still_works(self):
        mail = self.env['mail.mail'].with_user(self.admin).create(
            self._mail_vals(self.shared_mailbox)
        )
        self.assertEqual(mail.x_send_from_mailbox_id, self.shared_mailbox)

    def test_superuser_is_exempt(self):
        """System mail and templates pick a mailbox on nobody's behalf."""
        mail = self.env['mail.mail'].sudo().create(
            self._mail_vals(self.personal_mailbox)
        )
        self.assertEqual(mail.x_send_from_mailbox_id, self.personal_mailbox)

    # -- enforcement in the composer (the path a normal user has) ---------- #

    def test_composer_field_is_constrained(self):
        """The only route an ordinary internal user has, and so the one that
        matters most. The view domain suggests this rule; this enforces it."""
        composer = self.env['mail.compose.message'].with_user(self.other_user).create({
            'subject': 'Test',
            'body': '<p>Test</p>',
        })
        with self.assertRaises(ValidationError):
            composer.x_send_from_mailbox_id = self.personal_mailbox

    def test_composer_allows_the_owner(self):
        composer = self.env['mail.compose.message'].with_user(self.salesperson).create({
            'subject': 'Test',
            'body': '<p>Test</p>',
        })
        composer.x_send_from_mailbox_id = self.personal_mailbox
        self.assertEqual(composer.x_send_from_mailbox_id, self.personal_mailbox)

    # -- defence in depth at send time ------------------------------------- #

    @mute_logger('odoo.addons.pan_mail_pro.models.mail_mail')
    def test_send_refuses_when_author_may_not_use_mailbox(self):
        """Covers the sudo path, where the create guard deliberately stands
        aside. Checking the author rather than the executor is what makes this
        work for mail Odoo created on somebody's behalf.

        This used to reroute to another mailbox instead of refusing, because
        raising from inside the send loop stalled every mail queued behind it.
        A failure is recorded per mail now, so the security boundary gets to be
        a boundary: the mail fails and names the mailbox it would not use.
        """
        mail = self.env['mail.mail'].sudo().create({
            **self._mail_vals(self.personal_mailbox),
            'author_id': self.other_user.partner_id.id,
        })
        with self.assertRaises(RoutingError) as ctx:
            mail._resolve_route()
        self.assertIn(self.personal_mailbox.email, str(ctx.exception))

    # -- discovery half of the problem ------------------------------------- #

    def test_personal_mailbox_not_readable_by_others(self):
        """You cannot pick what you cannot see; the record rule closes that."""
        visible = self.env['pan.mail.mailbox'].with_user(self.other_user).search([])
        self.assertNotIn(self.personal_mailbox, visible)
        self.assertIn(self.shared_mailbox, visible)

    def test_owner_still_sees_own_personal_mailbox(self):
        visible = self.env['pan.mail.mailbox'].with_user(self.salesperson).search([])
        self.assertIn(self.personal_mailbox, visible)

    # -- what the record of a mail discloses -------------------------------- #

    def test_routing_log_is_not_readable_by_an_ordinary_user(self):
        """The row carries a subject, a sender and where the mail landed.

        None of that inherits the ACL of the document it points at, so a read
        row here hands every internal user the subject lines and
        correspondents of records they cannot open. The menu was restricted,
        which is not the same thing: a menu is not an ACL, and one search_read
        over RPC returned the table.
        """
        self.env['pan.mail.routing.log'].sudo().create({
            'mailbox_id': self.shared_mailbox.id,
            'subject': 'Redundancy consultation',
            'email_from': 'lawyer@example.com',
            'outcome': 'fallback',
        })
        Log = self.env['pan.mail.routing.log'].with_user(self.other_user)
        with self.assertRaises(AccessError):
            Log.search([])

    def test_thread_indexes_are_not_readable_by_an_ordinary_user(self):
        """Same argument, one step removed: who corresponds about what."""
        for model in ('pan.mail.thread.link', 'pan.mail.message.ref'):
            with self.subTest(model=model):
                with self.assertRaises(AccessError):
                    self.env[model].with_user(self.other_user).search([])

    def test_the_module_itself_still_reaches_those_tables(self):
        """Removing the row must not break the code that writes it."""
        log = self.env['pan.mail.routing.log'].log_decision(
            self.shared_mailbox, {'rule': 'references', 'confidence': 1.0},
            outcome='threaded', subject='Re: Quote',
        )
        self.assertTrue(log, 'log_decision goes through sudo() and must still write')

    # -- somebody else's mailbox connection --------------------------------- #

    def test_a_colleague_cannot_disconnect_your_mailbox(self):
        """It is a public method, so RPC reaches it for any browsable id.

        Wiping a colleague's tokens stops them sending until they walk through
        consent again; aimed at whoever owns notifications@ it stops every
        system mail in the database.
        """
        with self.assertRaises(AccessError):
            self.salesperson.with_user(self.other_user).action_disconnect_mailbox()

    def test_a_colleague_cannot_start_your_oauth_round(self):
        """Overwriting the nonce cancels a consent round already in progress."""
        with self.assertRaises(AccessError):
            self.salesperson.with_user(self.other_user).action_connect_mailbox()

    def test_you_can_still_disconnect_your_own(self):
        self.salesperson.with_user(self.salesperson).action_disconnect_mailbox()
        self.assertFalse(self.salesperson.x_pan_mail_connected)

    def test_disconnect_without_a_named_provider_clears_every_account(self):
        """It used to narrow on the database's setup provider, and that is
        empty until an admin picks one — so the domain became
        `provider = False`, matched nothing, wiped nothing, and still reported
        success to somebody who had just revoked access at the provider."""
        self.connect(self.salesperson, provider='gmail')
        self.salesperson.with_user(self.salesperson).action_disconnect_mailbox()
        self.assertFalse(self.salesperson.x_pan_mail_connected)
        self.assertFalse(
            self.env['pan.mail.account'].sudo().search([
                ('user_id', '=', self.salesperson.id), ('connected', '=', True),
            ]),
            'Both providers have to let go, not just the configured one')

    def test_an_administrator_may_do_it_on_a_users_behalf(self):
        """Reconnecting somebody's mailbox is a real support task."""
        self.salesperson.with_user(self.admin).action_disconnect_mailbox()
        self.assertFalse(self.salesperson.x_pan_mail_connected)

    def test_only_a_mailbox_manager_may_send_connect_invites(self):
        with self.assertRaises(AccessError):
            self.other_user.with_user(self.other_user).action_send_connect_invite()
