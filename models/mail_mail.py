# -*- coding: utf-8 -*-
import logging
from odoo import fields, models, api, _
from odoo.exceptions import AccessError, UserError

from .mail_provider_client import ERROR_NO_RECIPIENTS
from .neutralization import database_is_neutralized

_logger = logging.getLogger(__name__)

# How long an emitted References header may grow. Odoo tends to flatten
# `parent_id` onto the thread root, so real chains are two or three links; a
# longer one is trimmed to the root plus the nearest ancestors (see
# `_build_reply_context`).
REPLY_CHAIN_LIMIT = 10
# How far up the parent chain is *walked* before trimming. Only there to stop
# pathological data from looping the walk without limit; the `seen` set already
# guards against cycles.
REPLY_CHAIN_WALK_LIMIT = 100
# Marker written on internal notifications that are waiting for the
# notification mailbox to be configured. Recognisable so the setup checklist
# can count them, and so an admin reading the queue knows it is a setup gap
# rather than a delivery failure.
NOTIFICATION_PENDING_REASON = (
    'Waiting for the Notification mailbox to be configured '
    '(Settings → Mail Pro). This email will be sent automatically once it is.'
)


class RoutingError(Exception):
    """No mailbox can send this mail, and here is the sentence that says why.

    Raised by `_resolve_route`, which is the only place that decides. Routing
    used to answer this question three times — once to route, once to explain a
    failure afterwards, once more per provider — and the explanations could
    drift from what actually happened.
    """


class MailMail(models.Model):
    """Outgoing mail: route it to a mailbox and hand it to that mailbox's provider.

    One field of its own. The ids a send produces — the Message-ID that went on
    the wire and the provider's thread handle — are not stored here: the
    Message-ID goes into `pan.mail.message.ref` and the thread handle into
    `pan.mail.thread.link`, both keyed on the `mail.message` that outlives this
    row (Odoo deletes a `mail.mail` once it is sent).
    """
    _inherit = 'mail.mail'

    # The sender the author *asked for*. The lens field `mail.message.x_mailbox_id`
    # (reachable here through delegation) is the mailbox that actually carried
    # the mail, stamped only once it went out; the two differ on a mail that
    # could not be sent.
    x_send_from_mailbox_id = fields.Many2one(
        'pan.mail.mailbox',
        string='Send From',
        help='Mailbox this email is sent from. Empty means the author\'s default mailbox.',
    )

    @api.model_create_multi
    def create(self, vals_list):
        """Set mailbox from context if provided by mail.compose.message."""
        mailbox_id = self.env.context.get('send_from_mailbox_id')
        if mailbox_id:
            for vals in vals_list:
                if not vals.get('x_send_from_mailbox_id'):
                    vals['x_send_from_mailbox_id'] = mailbox_id
        for vals in vals_list:
            self._check_mailbox_permission(vals.get('x_send_from_mailbox_id'))
        return super().create(vals_list)

    def write(self, vals):
        """Guard the sender mailbox on write as well as on create."""
        if 'x_send_from_mailbox_id' in vals:
            self._check_mailbox_permission(vals['x_send_from_mailbox_id'])
        return super().write(vals)

    @api.model
    def _check_mailbox_permission(self, mailbox_id):
        """Refuse a sender mailbox the requesting user is not entitled to.

        Creation is the right place for this check: here `env.user` is still the
        real user. At send time the queue runs in cron, where `env.user` is the
        cron runner and the question can no longer be answered.

        Superuser is exempt — system mail, templates and the notification
        routing in `_resolve_route()` legitimately pick a mailbox on nobody's
        behalf.
        """
        if not mailbox_id or self.env.su:
            return
        mailbox = self.env['pan.mail.mailbox'].sudo().browse(mailbox_id)
        if not mailbox.exists() or mailbox._is_sendable_by(self.env.user):
            return
        _logger.warning(
            "[Outgoing Mail] User %s (id=%s) tried to send from mailbox %s (type=%s, owner=%s)",
            self.env.user.login, self.env.user.id, mailbox.email,
            mailbox.mailbox_type, mailbox.owner_user_id.login or '-',
        )
        raise AccessError(_(
            "You are not allowed to send email from %(mailbox)s. "
            "Personal mailboxes can only be used by their owner.",
            mailbox=mailbox.email,
        ))

    def _is_mail_pro_configured(self):
        """
        Check if Mail Pro module is minimally configured.

        Returns True if at least one active mailbox exists.
        This allows the system to work before setup is complete.
        """
        return bool(self.env['pan.mail.mailbox'].sudo().search_count([('active', '=', True)]))

    def _is_awaiting_notification_mailbox(self):
        """Is this an internal notification that Mail Pro cannot route *yet*?

        Onboarding has an unavoidable window: the first mailbox exists (so
        routing is on and SMTP is off) but notifications@ is not connected yet.
        Every user invitation and password reset lands in that window. Cancelling
        them there is the worst of the options — the admin sees nothing, and the
        mail is gone even after they finish the setup — so these are left queued
        instead and go out on the next mail-queue run.
        """
        self.ensure_one()
        if not self._is_internal_user_notification():
            return False
        try:
            self._notification_route()
        except RoutingError:
            return True
        return False

    def send(self, auto_commit=False, raise_exception=False, post_send_callback=None):
        """Route this batch through the provider APIs instead of SMTP.

        Three kinds of mail leave here by another door, and all three are
        deliberate rather than defensive:

        - Mass mailings go via SMTP, because Email Marketing has its own
          delivery infrastructure (Brevo and friends).
        - A database with no mailboxes at all has not opted in yet, so Odoo's
          own mail handling stays in charge. That is what keeps demo, QA and a
          fresh install working before anyone has been to Azure.
        - Internal notifications during the setup window are left queued. See
          `_is_awaiting_notification_mailbox`.

        Everything else is sent by a mailbox or fails saying which one it
        wanted. It is never quietly rerouted.

        **Failures are raised at the end, not in the middle.** Every mail in the
        batch is attempted and carries its own reason before anything is raised,
        so one misconfigured sender no longer stops the mails queued behind it.

        Raising at all deviates from `raise_exception`, which the interactive
        paths (chatter, composer) never set: a send that silently does nothing
        is the exact failure this module exists to prevent.

        Being told and being recorded cannot both happen in one transaction, and
        it is worth being explicit about which you get where:

        - Interactively, the raise unwinds the request and rolls the reason back
          with it. The user sees the error; the mail stays `outgoing`. The next
          mail-queue run hits the same failure and records it for good, because
          the cron passes `auto_commit` and each mail is committed as it goes.
        - In the queue, therefore, the reasons survive and the raise only ends
          up in the cron log.

        Nothing is lost either way: the mail is queued or it is marked.

        **What the raise costs, and why a mixed batch does not pay it.** That
        rollback is not selective. It also unwinds the `state='sent'` of every
        mail in the batch that already left the building — the provider has
        delivered them, but Odoo forgets it did, so the mail queue picks them up
        a minute later and the recipient gets the same email twice. Telling the
        sender is worth a lost failure_reason; it is not worth mailing a
        customer twice. So the raise happens only when there is nothing to lose:
        when the queue is driving (each mail already committed) or when nothing
        in the batch went out. A mixed batch keeps its successes, and its
        failures keep their reason on the mail — durable, because no raise
        unwinds them — under Settings → Technical → Email.
        """
        # `mailing_id` only exists when the mass_mailing module is installed.
        mass_mails = self.filtered(lambda m: hasattr(m, 'mailing_id') and m.mailing_id)
        delivered = 0
        if mass_mails:
            _logger.info(f"[Outgoing Mail] Routing {len(mass_mails)} mass mailing email(s) via standard SMTP")
            super(MailMail, mass_mails).send(
                auto_commit=auto_commit,
                raise_exception=raise_exception,
                post_send_callback=post_send_callback,
            )
            # These went out over SMTP and are just as unrecoverable as a
            # provider send, so they count towards "there is something a
            # rollback would cost" below.
            delivered += len(mass_mails.filtered(lambda m: m.state == 'sent'))

        mails = self - mass_mails
        if not mails:
            return True

        # `active_test=False` is essential: once an admin has created a mailbox,
        # even an archived one, they have opted in and mail must not slip out
        # via SMTP behind their back.
        if not self.env['pan.mail.mailbox'].sudo().with_context(
                active_test=False).search_count([]):
            _logger.info("[Outgoing Mail] No mailboxes configured — using Odoo's standard mail handling")
            return super(MailMail, mails).send(
                auto_commit=auto_commit,
                raise_exception=raise_exception,
                post_send_callback=post_send_callback,
            )

        awaiting = mails.filtered(lambda m: m._is_awaiting_notification_mailbox())
        if awaiting:
            _logger.warning(
                f"[Outgoing Mail] Holding {len(awaiting)} internal notification(s) in the "
                f"queue — no usable notification mailbox configured yet"
            )
            awaiting.write({'failure_reason': NOTIFICATION_PENDING_REASON})
            mails -= awaiting

        failures = []
        for mail in mails:
            reason = mail._send_one(raise_exception=raise_exception,
                                    post_send_callback=post_send_callback)
            if reason:
                failures.append(reason)
            elif mail.state == 'sent':
                delivered += 1
            if auto_commit:
                # The mail queue asks for this, and now it matters: the failure
                # below must not roll back the mails that already went out.
                self.env.cr.commit()

        # Telling the sender this way costs a rollback, so it is only
        # affordable while there is nothing to roll back — or when the caller
        # asked for the exception explicitly and owns that trade-off, which is
        # what `raise_exception` means in Odoo's own signature. Either way the
        # chatter already carries the failure; see `_fail`.
        if failures and (auto_commit or raise_exception or not delivered):
            raise UserError(self._batch_failure_message(failures))

        return True

    @staticmethod
    def _batch_failure_message(failures):
        """One message for the whole batch, leading with the first reason.

        Concatenating every reason produces a dialog nobody reads; naming one
        and counting the rest tells the reader what to fix first and that there
        is more behind it.
        """
        if len(failures) == 1:
            return failures[0]
        return _(
            '%(reason)s\n\n(%(others)d more email(s) could not be sent either. '
            'Each carries its own reason under Settings → Technical → Email.)',
            reason=failures[0], others=len(failures) - 1,
        )

    def _send_one(self, raise_exception=False, post_send_callback=None):
        """Send one mail. Returns the failure reason, or None when it went out.

        Never raises on a failure of its own: the reason goes onto the mail and
        back to `send()`, which decides what the batch as a whole does about it.
        """
        self.ensure_one()
        _logger.info("[Outgoing Mail] Processing email %s", self.id)
        _logger.debug(
            "[Outgoing Mail] Email %s: subject=%r to=%r", self.id, self.subject, self.email_to
        )

        try:
            mailbox, account = self._resolve_route()
            result = mailbox._get_client().send_message(
                mail_record=self,
                mailbox=mailbox,
                account=account,
                reply_context=self._build_reply_context(mailbox),
            )
        except RoutingError as e:
            return self._fail(str(e))
        except Exception as e:
            _logger.exception(f"[Outgoing Mail] Exception sending mail {self.id}")
            reason = self._fail(str(e))
            if raise_exception:
                raise
            return reason

        if result['success']:
            self._record_sent(result, mailbox, account)
            if post_send_callback:
                post_send_callback(self)
            return None

        if result.get('error_code') == ERROR_NO_RECIPIENTS:
            # Almost always an internal notification to a partner without an
            # email address (the Administrator account, typically). Standard
            # Odoo drops those silently and so must we — this is not a failure
            # anybody can act on, so it must not surface as one.
            _logger.info(f"[Outgoing Mail] Mail {self.id} has no deliverable recipient — cancelling")
            self.write({'state': 'cancel'})
            return None

        return self._fail(result.get('error') or _('Failed to send email.'))

    def _fail(self, reason):
        """Record why this mail did not go out, and hand the reason back.

        The `mail.notification` rows are marked failed too, through Odoo's own
        `_postprocess_sent_message`. That is what puts the red "message not
        sent" marker and its retry button in the chatter, and it is the reason
        `send()` can afford not to raise on a mixed batch: the author still sees
        that something failed, on the record it failed on, without a rollback
        that would resend the mails which did go out.
        """
        self.ensure_one()
        self.write({'state': 'exception', 'failure_reason': reason})
        self._postprocess_sent_message(
            success_pids=self.env['res.partner'],
            success_emails=[],
            failure_reason=reason,
            failure_type='unknown',
        )
        _logger.error(f"[Outgoing Mail] Mail {self.id} not sent: {reason}")
        return reason

    def _record_sent(self, result, mailbox, account):
        """Store the provider's ids so replies thread onto this message."""
        self.ensure_one()
        message_id = result.get('message_id')
        thread_id = result.get('thread_id')

        self.write({'state': 'sent'})

        if self.mail_message_id:
            # Lens fields. Set here rather than at create() because only a mail
            # that actually went out is outgoing communication.
            self.mail_message_id.write({
                'x_direction': 'outgoing',
                'x_mailbox_id': mailbox.id,
                'x_account_id': account.id,
            })

        self._index_sent_message(mailbox, message_id, thread_id)
        _logger.info(f"[Outgoing Mail] Mail {self.id} sent from {mailbox.email} "
                     f"(message {message_id}, thread {thread_id})")


    def _build_reply_context(self, mailbox):
        """Everything a provider needs to send this mail *inside* its thread.

        Threading is half a send problem, and it was the half we were not
        doing: outgoing mail carried no `In-Reply-To` and no `References`, so
        the recipient's client had nothing to attach the reply to and started a
        fresh conversation. Their reply then came back with a chain rooted at
        our unthreaded mail, which is why matching leaned so hard on provider
        thread ids in the first place.

        Provider-neutral by construction — it is built from Odoo data only, and
        each provider uses whichever fields it can honour:

            in_reply_to         RFC 5322 Message-ID of the direct parent
            references          the chain, root first, as References wants it
            thread_id           provider thread handle for this record
            provider_message_id parent's provider resource id, mailbox-scoped

        Microsoft cannot be handed headers at all (Graph only accepts custom
        `x-` ones), so it uses `provider_message_id` to reply *to a message*.
        Gmail and IMAP take the headers. Nobody needs all four, and a provider
        that has none of them still sends — just unthreaded, as today.
        """
        self.ensure_one()
        reply_context = {
            'in_reply_to': None,
            'references': [],
            'thread_id': None,
            'provider_message_id': None,
        }

        link = self.env['pan.mail.thread.link'].find_for_record(
            mailbox, self.model, self.res_id)
        if link:
            reply_context['thread_id'] = link.thread_id
            reply_context['provider_message_id'] = link.last_provider_message_id

        chain = []
        node = self._reply_parent_message()
        seen = set()
        while node and len(seen) < REPLY_CHAIN_WALK_LIMIT and node.id not in seen:
            seen.add(node.id)
            wire_id = self._wire_message_id(node)
            if wire_id:
                chain.append(wire_id)
            node = node.parent_id

        if chain:
            # Walked child → root; References is ordered root first.
            chain.reverse()
            if len(chain) > REPLY_CHAIN_LIMIT:
                # Trim the middle, never the root: the IMAP provider derives
                # its thread key from references[0] (`mime_utils.thread_key`),
                # so dropping the root would hand the same conversation a new
                # thread id partway through. Root plus nearest ancestors is
                # what RFC 5322 recommends for a truncated References too.
                chain = chain[:1] + chain[-(REPLY_CHAIN_LIMIT - 1):]
            reply_context['references'] = chain
            reply_context['in_reply_to'] = chain[-1]
        return reply_context

    def _reply_parent_message(self):
        """The message this mail is answering, as a mail client would see it.

        `parent_id` when Odoo set one; otherwise the most recent message on the
        same record. The fallback matters because the chatter composer often
        leaves `parent_id` empty, and "the last thing said on this record" is
        what the recipient is looking at anyway.
        """
        self.ensure_one()
        message = self.mail_message_id
        if message and message.parent_id:
            return message.parent_id
        if not self.model or not self.res_id:
            return self.env['mail.message'].browse()

        domain = [
            ('model', '=', self.model),
            ('res_id', '=', self.res_id),
            ('message_type', 'in', ['email', 'comment']),
        ]
        if message:
            domain.append(('id', '!=', message.id))
        return self.env['mail.message'].sudo().search(domain, order='id desc', limit=1)

    def _wire_message_id(self, message):
        """The Message-ID a recipient's client actually saw for `message`.

        Not always the one Odoo generated: Graph mints its own
        `internetMessageId` on send, and that is the id the recipient will put
        in `In-Reply-To`. Preferring the provider's own id keeps the chain we
        emit identical to the chain that comes back. `_index_sent_message`
        records it, so the ref index is the only place to look.
        """
        ref = self.env['pan.mail.message.ref'].sudo().search([
            ('mail_message_id', '=', message.id),
            ('source', '=', 'provider'),
        ], limit=1)
        return ref.message_id or message.message_id

    def _index_sent_message(self, mailbox, provider_message_id, provider_thread_id):
        """Make this outgoing mail findable when the recipient replies.

        The wire Message-ID is rarely the one Odoo generated. Microsoft Graph
        mints its own `internetMessageId` and offers no way to override it, so
        the address the recipient's client will put in `In-Reply-To` is not the
        one stored in `mail.message.message_id`. Both are indexed, and the
        matcher resolves a References chain against either.

        The thread link is written here too so the *first* reply already has a
        scoped (mailbox, thread) entry to match on, rather than having to wait
        until the incoming sync has seen the conversation once.
        """
        self.ensure_one()
        message = self.mail_message_id
        if not message:
            return

        Ref = self.env['pan.mail.message.ref']
        if message.message_id:
            Ref.record(message, message.message_id, source='odoo')
        if provider_message_id:
            Ref.record(message, provider_message_id, source='provider')

        if provider_thread_id and self.model and self.res_id:
            self.env['pan.mail.thread.link'].record(
                mailbox=mailbox,
                thread_id=provider_thread_id,
                model=self.model,
                res_id=self.res_id,
                message=message,
            )

    def _is_internal_user_notification(self):
        """
        Check if this mail is a notification to an internal Odoo user.

        Logic: If a mail.mail exists for a partner that is linked to a res.users,
        it means _notify_thread_by_email() was called for that user. This only
        happens when the user has notification_type='email' in their preferences.

        Users with notification_type='inbox' never get a mail.mail created for them
        (they get inbox notifications instead).

        Therefore: any mail.mail going to a user-linked partner = internal notification
        → should use notifications@ mailbox.

        Returns:
            bool: True if any recipient is an internal Odoo user
        """
        self.ensure_one()
        # Check recipient_ids - if any partner is linked to a user, it's internal
        for partner in self.recipient_ids:
            if partner.user_ids:
                _logger.info(f"[Outgoing Mail] Email {self.id} IS internal user notification to {partner.name}")
                return True
        return False

    # -------------------------------------------------------------------------
    # Routing
    #
    # One question, asked once: which mailbox sends this, and with whose
    # credentials? Every unanswerable case raises RoutingError with the sentence
    # the admin needs. Nothing falls through to a different sender — a mail
    # going out from notifications@ because your own mailbox was misconfigured
    # is worse than a mail that did not go out, because nobody finds out.
    # -------------------------------------------------------------------------

    def _resolve_route(self):
        """Return (mailbox, account) for this mail, or raise RoutingError."""
        self.ensure_one()

        # A staging copy must not mail real customers. Odoo's own neutralization
        # only reaches SMTP, which Mail Pro does not use, so the refusal lives
        # here instead. Raising rather than dropping the mail keeps the reason
        # on the record and leaves it queued, so nothing is lost if the database
        # turns out to be the real one after all.
        if database_is_neutralized(self.env):
            raise RoutingError(_(
                'This database is neutralized (a staging or test copy), so Mail '
                'Pro will not send. The email stays queued.'
            ))

        # System mail to our own users is what the notification mailbox is for.
        if self._is_internal_user_notification():
            return self._notification_route()

        author_user = self._author_user()

        # An explicit "Send From" choice in the composer outranks the author's
        # default: it is the only signal that came from a person. It also
        # survives templates whose email_from resolves author_id to the company
        # partner rather than to whoever pressed Send.
        mailbox = self.x_send_from_mailbox_id or author_user.x_default_mailbox_id

        if not mailbox:
            # Mail generated on behalf of somebody outside Odoo — an auto-reply,
            # an activity notification triggered by an incoming email — has no
            # user to send as. The notification mailbox is the answer by
            # definition here, not a fallback from a failed lookup.
            if not author_user:
                return self._notification_route()
            raise RoutingError(_(
                'User "%s" has no default mailbox. Open My Profile → Mail Pro '
                'and pick the address to send from.'
            ) % author_user.name)

        # Defence in depth behind `_check_mailbox_permission`, which already
        # refused this at create time. A row can predate that check or arrive
        # from a migration, so it is asked again here — and refused rather than
        # rerouted. Rerouting was the older behaviour, chosen because raising
        # from inside the send loop stalled every mail queued behind it; that
        # constraint is gone now that a failure is recorded per mail, so the
        # security boundary gets to be a boundary.
        if author_user and not mailbox._is_sendable_by(author_user):
            _logger.warning(
                "[Outgoing Mail] Mail %s selects mailbox %s which its author %s may not use",
                self.id, mailbox.email, author_user.login,
            )
            raise RoutingError(_(
                'Mail Pro will not send from %(mailbox)s on behalf of "%(user)s". '
                'A personal mailbox can only be used by its owner.',
                mailbox=mailbox.email, user=author_user.name,
            ))

        account = mailbox._get_client().resolve_sending_account(
            mailbox, author_user=author_user)
        if not account.connected:
            raise RoutingError(mailbox._no_credentials_error(sender=author_user))

        _logger.info(f"[Outgoing Mail] Sending from {mailbox.email} (credentials: {account.email})")
        return (mailbox, account)

    def _notification_route(self):
        """The notification mailbox and the credentials it sends with."""
        mailbox = self._notification_mailbox()
        if not mailbox:
            raise RoutingError(_(
                'No Notification mailbox configured. Go to Settings → Mail Pro '
                'and create the address system emails are sent from.'
            ))

        account = mailbox._get_client().resolve_sending_account(mailbox)
        if not account.connected:
            raise RoutingError(mailbox._no_credentials_error())
        return (mailbox, account)

    @api.model
    def _notification_mailbox(self):
        return self.env['pan.mail.mailbox'].sudo().search([
            ('mailbox_type', '=', 'notification'),
            ('active', '=', True),
        ], limit=1)

    def _author_user(self):
        """The Odoo user who wrote this mail, if there is one."""
        self.ensure_one()
        return self.author_id.user_ids[:1]
