# -*- coding: utf-8 -*-
"""
Incoming Mail Processor.

Fetches emails from a mailbox via its provider client and routes them to the
correct partner, using message_new()/message_post() for proper threading.

Provider-neutral: everything here reads the normalized message shape documented
in `mail_provider_client.py`. No Graph, Gmail or other wire-specific key should
ever appear below this line.
"""
import logging
from typing import NamedTuple
from markupsafe import Markup

from odoo import models, api, fields, _
from odoo.exceptions import UserError

from .mail_provider_client import FOLDER_INBOX, FOLDER_SENT
from .neutralization import database_is_neutralized
from .pan_mail_mailbox import SYNCING_MODES

_logger = logging.getLogger(__name__)

# Every post the sync makes carries this context, and `pan_mail_imported` is
# the whole of the boundary in ARCHITECTURE.md §9.10: it means "this post is an
# import", which no field on the message does. `x_mailbox_id` was the obvious
# candidate and is wrong — `mail.mail._record_sent()` stamps that same field on
# mail Odoo itself sent, so keying the boundary on it would conflate the two
# directions of one mailbox and eventually silence something a person wrote.
#
# The follower flags ride along because they are the same sentence: an import
# notifies nobody and subscribes nobody. Neither the colleague who sent the mail
# nor the customer who received it asked to follow anything in Odoo.
#
# Three flags because Odoo splits the question three ways, and only the middle
# one is the flag people reach for:
#
# - `mail_post_autofollow_author_skip` keeps the *author* out. This is the one
#   that matters here and the one that is easy to miss: Odoo subscribes the
#   author of a post by default, on the reasoning that an author should see the
#   answers. A sync has no author who wants answers, only a colleague whose
#   sent mail we copied.
# - `mail_create_nosubscribe` is about record *creation*, not about posting. It
#   belongs on the `message_new()` path and does nothing for a `message_post()`.
# - `mail_post_autofollow` keeps the recipients out. False is already Odoo's
#   default; it is stated so the intent survives a default changing.
class Skip(NamedTuple):
    """Why a message may not enter Odoo.

    `record` is the part worth having in the type: whether a refusal leaves a
    row in `pan.mail.item` is a property of the gate that refused, declared
    next to it, rather than a pattern to reconstruct from every call site. It
    used to be three ad-hoc `_record_skip()` calls among seven bare returns.

    `quiet` drops the refusal to DEBUG. Only the duplicate gate sets it: an
    overlapping fetch window is normal and deliberate on IMAP, so a refusal
    there is the system working, and at INFO it would drown the log.
    """
    reason: str
    detail: str = ''
    record: bool = False
    quiet: bool = False


IMPORT_CTX = {
    'pan_mail_imported': True,
    'mail_post_autofollow_author_skip': True,
    'mail_create_nosubscribe': True,
    'mail_post_autofollow': False,
}



class PanMailFetcher(models.AbstractModel):
    """The incoming flow: email in a mailbox folder becomes chatter on a record.

    Fetches through the mailbox's provider client, filters, asks the matcher
    where each mail belongs, and posts it there with message_post() or
    message_new() so threading and partner matching are Odoo's own.
    """
    _name = 'pan.mail.fetcher'
    _description = 'Incoming Mail Fetcher'

    @api.model
    def _cron_fetch_incoming_mail(self):
        """
        Cron method to fetch emails from all enabled mailboxes.
        Called by ir.cron every 1 minute.
        """
        # Neutralization deactivates every cron, so this normally does not run
        # in staging at all. It still gets here by hand, from "Sync Now" on a
        # mailbox -- and a sync is not read-only: it marks mail read and posts
        # into chatter, which fires notifications back out.
        if database_is_neutralized(self.env):
            _logger.info('[Incoming Mail] Database is neutralized - skipping sync')
            return
        # Deliberately not filtered on an owner: whether a mailbox needs one is
        # the provider's business. A Gmail or IMAP shared mailbox is its own
        # account with nobody behind it, and requiring an owner here silently
        # skipped exactly those mailboxes. What matters is usable credentials,
        # which is what the mailbox asks its client.
        mailboxes = self.env['pan.mail.mailbox'].search([
            ('sync_mode', 'in', SYNCING_MODES),
            ('state', 'in', ['active', 'draft']),  # Also try draft to auto-activate
        ]).filtered(lambda m: m._has_working_credentials())

        # Setup is not a warning, it is a phase: nothing is carried until all
        # three steps are answered, and emptying the internal domain list
        # later puts the module straight back into it. Recorded on the mailboxes
        # rather than only logged -- a sync that stopped has to be visible
        # where somebody looks, which is the mailbox, not the server log.
        setup = self.env['pan.mail.setup']
        if not setup.is_ready():
            reason = setup.not_ready_error()
            _logger.info('[Incoming Mail] %s', reason)
            mailboxes.write({'state': 'error', 'error_message': reason})
            return

        _logger.info(f"[Incoming Mail] Starting sync for {len(mailboxes)} mailbox(es)")

        for mailbox in mailboxes:
            try:
                with self.env.cr.savepoint():
                    self._process_mailbox(mailbox)
                    # Mark as active if successful
                    if mailbox.state != 'active':
                        mailbox.write({'state': 'active', 'error_message': False})
            except Exception as e:
                # Savepoint rolled back: the cursor is usable again, so the
                # error write below won't hit "current transaction is aborted".
                _logger.exception(f"[Incoming Mail] Error processing mailbox {mailbox.email}")
                mailbox.write({
                    'state': 'error',
                    'error_message': str(e),
                })

        _logger.info("[Incoming Mail] Sync completed")

    def _process_mailbox(self, mailbox):
        """
        Fetch and process messages for a single mailbox.

        Args:
            mailbox: pan.mail.mailbox record

        Raises:
            UserError: when internal domains are not configured. Deliberately
                loud: the alternative is fetching every internal email into
                Odoo, and a mailbox stuck in `error` with a readable message is
                far cheaper than a silent data leak. The cron catches this and
                writes it onto the mailbox.
        """
        _logger.info(f"[Incoming Mail] Processing mailbox: {mailbox.email}")

        gate = self.env['pan.mail.domain'].configuration_error()
        if gate:
            raise UserError(gate)

        # First sync: if sync_start_date is set, use it for historical sync
        # Otherwise just test connection and start from now
        if not mailbox.last_sync_date:
            if mailbox.sync_start_date:
                # Historical sync: start from configured date
                _logger.info(f"[Incoming Mail] First sync for {mailbox.email}, starting from {mailbox.sync_start_date}")
                mailbox.write({'last_sync_date': mailbox.sync_start_date})
                # Continue to fetch messages below
            else:
                # No start date: just test connection and start from now
                _logger.info(f"[Incoming Mail] First sync for {mailbox.email}, testing connection...")
                client = mailbox._get_client()
                client.fetch_messages(
                    account=client.resolve_receiving_account(mailbox),
                    mailbox=mailbox,
                    folder=FOLDER_INBOX,
                    limit=1,  # Just test, don't fetch all
                )
                _logger.info(f"[Incoming Mail] Connection test passed for {mailbox.email}, setting sync date to now")
                mailbox.write({'last_sync_date': fields.Datetime.now()})
                return  # Skip this run, start fetching from next cron run

        # Both folders, always: syncing a mailbox means both sides of its
        # correspondence. Two booleans used to say so and were computed from the
        # sync mode, so they could never disagree with it.
        processed_count = 0
        folder_cursors = []
        for folder in (FOLDER_INBOX, FOLDER_SENT):
            count, latest_dt = self._fetch_folder(mailbox, folder)
            processed_count += count
            if latest_dt:
                folder_cursors.append(latest_dt)

        # Advance sync cursor incrementally:
        # Use min of folder progress (safe: won't skip messages in slower folder)
        # If no messages found, advance to now() (fully caught up)
        if folder_cursors:
            mailbox.write({'last_sync_date': min(folder_cursors)})
        else:
            mailbox.write({'last_sync_date': fields.Datetime.now()})

        _logger.info(f"[Incoming Mail] Processed {processed_count} message(s) from {mailbox.email}")

    def _fetch_folder(self, mailbox, folder):
        """
        Fetch messages from a specific folder.

        Messages are sorted ascending (oldest first) so we process
        incrementally. The cursor advances to the last fetched message's
        date, ensuring no messages are skipped across runs.

        Args:
            mailbox: mailbox record
            folder: FOLDER_INBOX or FOLDER_SENT

        Returns:
            tuple: (processed_count, latest_received_datetime or None)
        """
        # Fetch messages since last sync (sorted ascending for incremental cursor)
        client = mailbox._get_client()
        messages = client.fetch_messages(
            account=client.resolve_receiving_account(mailbox),
            mailbox=mailbox,
            folder=folder,
            since_datetime=mailbox.last_sync_date,
            limit=200,
        )

        processed = 0
        latest_datetime = None

        # Messages sorted ascending — the last item carries the latest date
        if messages:
            latest_datetime = messages[-1].get('date')

        for message in messages:
            try:
                with self.env.cr.savepoint():
                    if self._process_message(mailbox, message, folder):
                        processed += 1
            except Exception as error:
                # Without the savepoint, one DB error would leave the whole
                # transaction in `aborted` state and every later message in
                # this batch would fail with "cursor already closed".
                _logger.exception(
                    f"[Incoming Mail] Error processing message {message.get('provider_message_id')}"
                )
                # Recorded *after* the savepoint has exited and rolled back.
                # Inside it, the write would be undone by the very failure it
                # is meant to report.
                self.env['pan.mail.item']._record_skip(
                    mailbox, message, folder, 'error', detail=str(error)[:200],
                )

        return processed, latest_datetime

    # ------------------------------------------------------------------ #
    # Gates — may this message enter Odoo at all?
    # ------------------------------------------------------------------ #

    def _gate_rules(self):
        """Ordered gate method names, strongest first.

        Order is the contract: a gate may assume every gate before it passed,
        and may leave what it resolved in `ctx` for the ones after it. Add a
        gate here and nowhere else.

        The same shape as `pan.mail.matcher._match_rules()` on purpose. That
        ladder decides *where* a mail goes; this one decides *whether* it may
        come in. Two halves of one question deserve one pattern, and these
        seven decisions used to be seven bare `return False` statements strewn
        through a two-hundred-line method — which is how the internal check
        ended up guarding one folder and not the other.
        """
        return [
            '_gate_duplicate',
            '_gate_odoo_originated',
            '_gate_counterpart',
            '_gate_internal_domain',
            '_gate_blocked_contact',
            '_gate_internal_user',
            '_gate_sync_mode',
        ]

    def _refuse(self, ctx):
        """Run the ladder. Returns the `Skip` that refused, or None to proceed.

        Recording is done here rather than inside the gates, so a gate declares
        whether its refusal deserves a row in `pan.mail.item` and never has to
        know how one is written.
        """
        for name in self._gate_rules():
            skip = getattr(self, name)(ctx)
            if not skip:
                continue
            _logger.log(
                logging.DEBUG if skip.quiet else logging.INFO,
                "[Incoming Mail] Refused %s at %s: %s",
                ctx['internet_message_id'], name, skip.reason,
            )
            if skip.record:
                self.env['pan.mail.item']._record_skip(
                    ctx['mailbox'], self._full_message(ctx), ctx['folder'],
                    skip.reason, detail=skip.detail,
                    direction='outgoing' if ctx['is_outgoing'] else 'incoming',
                )
            return skip
        return None

    def _full_message(self, ctx):
        """The full message, fetched once and cached on `ctx`.

        Lazy rather than fetched up front because the first gate is the
        duplicate check, and on a mailbox the sync has already seen that is
        most of every run. Paying a provider round-trip to discover we already
        have the mail would be the most expensive way to do nothing.
        """
        if 'full_message' not in ctx:
            mailbox = ctx['mailbox']
            client = mailbox._get_client()
            account = client.resolve_receiving_account(mailbox)
            ctx['client'] = client
            ctx['account'] = account
            ctx['full_message'] = client.get_message(
                account=account,
                mailbox=mailbox,
                provider_message_id=ctx['message']['provider_message_id'],
            )
        return ctx['full_message']

    def _gate_duplicate(self, ctx):
        """Already imported. Cheapest question, so it is asked first and
        before anything reaches out to the provider."""
        if self._is_duplicate(ctx['internet_message_id']):
            return Skip(
                'duplicate', _('This message is already in Odoo.'), quiet=True,
            )
        return None

    def _gate_odoo_originated(self, ctx):
        """Mail Odoo itself sent, coming back through the mailbox it left from.

        The loop guard. Our own `X-Odoo-*` headers survive the round trip, and
        a re-import would post Odoo's own message onto the record it came from.
        """
        headers = self._full_message(ctx).get('headers', {})
        if headers.get('x-odoo-model') or headers.get('x-odoo-mail-id'):
            return Skip('odoo_originated', _('Odoo sent this message itself.'))
        return None

    def _gate_counterpart(self, ctx):
        """Collect the other party, and refuse a sent item that has none.

        Which field holds the counterpart is the whole of the direction
        question: the inbox reads the From, Sent Items reads the To. Only the
        To — CC is stored for threading and decides nothing, which is a real
        trade with a chosen direction. A customer mail addressed to a shared
        internal address with the customer in Cc is not logged, so a genuine
        customer mail goes missing; the reverse error, logging internal mail,
        is a confidentiality loss rather than a completeness one.

        A sent item can carry several recipients, so this collects all of them
        and leaves the choice between them to the gate that can make it. Every
        gate after that asks about one address, which is why it is settled here
        rather than re-derived by each of them.
        """
        full_message = self._full_message(ctx)
        if ctx['is_outgoing']:
            parties = [p for p in (full_message.get('to') or []) if p.get('email')]
            if not parties:
                return Skip('no_recipient', _('This sent message has no recipient.'))
        else:
            parties = [full_message.get('from') or {}]
        ctx['counterparts'] = parties
        self._choose_counterpart(ctx, parties[0])
        return None

    @staticmethod
    def _choose_counterpart(ctx, party):
        ctx['contact_email'] = party.get('email', '')
        ctx['contact_name'] = party.get('name', '')
        _logger.debug(
            "[Incoming Mail] Counterpart: name=%r email=%r",
            ctx['contact_name'], ctx['contact_email'],
        )

    def _gate_internal_domain(self, ctx):
        """The company's own mail, which has no business being copied to Odoo.

        Both directions, and this is the gate that used to guard only the
        inbox. The old reasoning was half right: the sender of a sent item is
        always us, so checking the *sender* there would skip everything. The
        answer to that is to check the counterpart, not to stop checking — and
        for months it was the second one. Every mail in the incident that
        produced this gate came through this gap.

        With several recipients the rule is "any external party means this is
        correspondence": the first external one becomes the counterpart and the
        mail is logged on it. Only when every recipient is ours is it internal
        traffic, and then nothing enters.

        No trace, on purpose. `pan.mail.item` is the queue of skips a person can
        reverse, and internal mail is the one refusal that must never be
        reversible — an Import button here would be a button for leaking. The
        refusal `_refuse()` logs carries the mailbox, the Message-ID, the reason
        and the time, which is what answering "why is this mail not in Odoo"
        needs and is as much as may be kept about a mail we declined to read.
        """
        if ctx['force_import']:
            return None
        mailbox = ctx['mailbox']
        for party in ctx['counterparts']:
            if not self._is_internal_domain(party.get('email', ''), mailbox):
                self._choose_counterpart(ctx, party)
                return None
        return Skip('internal_domain', _('Every party to this mail is one of ours.'))

    def _gate_blocked_contact(self, ctx):
        """A contact that objected to processing.

        Resolves the partner for the gates after it. Deliberately leaves no
        trace: a block list is an objection to processing, and a queue entry
        naming the person would be processing.
        """
        ctx['partner'] = self._find_partner(ctx['contact_email'])
        if ctx['partner'] and ctx['partner'].x_email_sync_blocked:
            return Skip('blocked_contact', _('This contact is blocked from sync.'))
        return None

    def _gate_internal_user(self, ctx):
        """A colleague, who has the mail in their own inbox already.

        Recognises only addresses with an Odoo user behind them, so a shared or
        functional address like planning@ passes as an outside correspondent.
        That gap is the other half of #37 phase 2, and the reason this reads
        `partner.user_ids` rather than asking the domain.
        """
        partner = ctx['partner']
        if partner and partner.user_ids:
            return Skip('internal_user', _('This address belongs to an Odoo user.'))
        return None

    def _gate_sync_mode(self, ctx):
        """What the mailbox was told to accept.

        The only gate whose refusals are worth recording: an unknown contact is
        a decision a person may want to reverse, and the triage queue is where
        they reverse it. The gates above refuse things nobody would want back.
        """
        mailbox = ctx['mailbox']
        if ctx['partner'] or ctx['force_import']:
            return None
        if mailbox.sync_mode == 'known_partners':
            return Skip(
                'unknown_contact',
                _('Sync mode only accepts mail from existing contacts.'),
                record=True,
            )
        if mailbox.sync_mode == 'all' and mailbox.queue_unknown_contacts:
            return Skip(
                'queued_for_review',
                _('This mailbox holds mail from unknown senders for review.'),
                record=True,
            )
        return None

    def _process_message(self, mailbox, message, folder):
        """
        Process a single message using Odoo's native routing.

        Args:
            mailbox: mailbox record
            message: normalized message dict from the provider client (preview)
            folder: FOLDER_INBOX or FOLDER_SENT

        Returns:
            bool: True if message was processed, False if skipped
        """
        internet_message_id = message.get('message_id')
        # Captured before `message` is rebound below to the posted mail.message.
        # This is the provider's own resource handle, not the RFC Message-ID.
        provider_message_id = message.get('provider_message_id')

        ctx = {
            'mailbox': mailbox,
            'folder': folder,
            'message': message,
            'internet_message_id': internet_message_id,
            'is_outgoing': folder == FOLDER_SENT,
            # An operator re-importing a held item lifts the *filters* — sync
            # mode and internal-domain exclusion. It deliberately does not lift
            # the duplicate guard, the Odoo loop guard, or the contact block
            # list: the block list is in practice an objection to processing,
            # and no button in this module should be able to override it.
            'force_import': bool(self.env.context.get('pan_mail_force_import')),
        }

        if self._refuse(ctx):
            return False

        # Sender, recipient and subject are personal data. They are logged at
        # DEBUG only, and nowhere else in this method: logs routinely leave the
        # database (hosting, aggregators) and are out of reach of an erasure
        # request. INFO identifies a message by its provider id, which is not.
        _logger.info("[Incoming Mail] Processing message %s", internet_message_id)
        _logger.debug(
            "[Incoming Mail] %s subject=%r", internet_message_id,
            message.get('subject') or '(no subject)',
        )

        full_message = self._full_message(ctx)
        client = ctx['client']
        account = ctx['account']
        contact_email = ctx['contact_email']
        contact_name = ctx['contact_name']
        is_outgoing = ctx['is_outgoing']

        # Get attachments if present
        # Note: has_attachments is false for inline-only images, so also check for cid: in body
        attachments = []
        body_may_have_inline = 'cid:' in (full_message.get('body_html') or '')
        if full_message.get('has_attachments') or body_may_have_inline:
            attachments = client.get_message_attachments(
                account=account,
                mailbox=mailbox,
                provider_message_id=message['provider_message_id'],
            )
            _logger.info(f"[Incoming Mail] Fetched {len(attachments)} attachment(s)")

        # Find or create the partner (contact) for chatter posting
        partner = None
        if contact_email:
            partner = self._find_or_create_partner(contact_email, contact_name)
            _logger.debug(f"[Incoming Mail] Partner resolved: {partner.name} (id={partner.id}, email={partner.email})")

        if not partner:
            _logger.warning(f"[Incoming Mail] Could not resolve partner for {contact_email}, skipping")
            return False

        # Where does this mail belong? The fetcher decides whether a message is
        # worth keeping; deciding where it goes is the matcher's job, and it is
        # provider-neutral — the same ladder serves Graph, Gmail and IMAP.
        match = self.env['pan.mail.matcher'].match(
            full_message,
            mailbox=mailbox,
            partner=partner,
            # In team mode a reply must land on the ticket or lead, never back
            # on the contact's own chatter.
            exclude_models=('res.partner',) if mailbox.route_to_team else (),
        )
        # Effective thread id: what the provider said, or — for providers with
        # no thread concept — the root of the References chain.
        conversation_id = match['thread_id']

        # Build email body - mark as safe HTML to preserve formatting
        body_content = full_message.get('body_html') or ''

        # Process attachments into Odoo's expected tuple format:
        # - Inline: 3-tuple so Odoo converts cid: → /web/image/
        # - Regular: 2-tuple stored as ir.attachment
        email_attachments = []
        for attachment in attachments:
            if attachment['is_inline'] and attachment['content_id']:
                email_attachments.append((
                    attachment['name'],
                    attachment['content'],
                    {'cid': attachment['content_id']},
                ))
            else:
                email_attachments.append((attachment['name'], attachment['content']))

        if full_message.get('body_is_html') and body_content:
            body_content = Markup(body_content)

        # When the mail was written, not when we happened to import it. Odoo
        # defaults `date` to now(), which collapses a historical import into a
        # single day and destroys the timeline the chatter exists to show. The
        # contract normalizes this to naive UTC for every provider, so this is
        # the same value on Graph, Gmail and IMAP.
        #
        # Falling back to now() rather than passing None: `date` reaches
        # `mail.message` through message_post's **kwargs, where an explicit None
        # writes NULL instead of letting the field default apply. A message with
        # no date at all sorts unpredictably in the chatter.
        msg_date = full_message.get('date') or fields.Datetime.now()

        # Build msg_dict in Odoo's expected format for message_new()
        email_from = f'"{contact_name}" <{contact_email}>' if contact_name else contact_email
        cc_addresses = ', '.join(
            r.get('email', '') for r in full_message.get('cc') or []
        )
        msg_dict = {
            'message_type': 'email',
            'subject': full_message.get('subject', ''),
            'from': email_from,
            'to': mailbox.email,
            'cc': cc_addresses,
            'body': body_content,
            'attachments': email_attachments,
            'message_id': internet_message_id,
            'author_id': partner.id,
            'email_from': email_from,
            # `message_new` reads this off msg_dict the way Odoo's own gateway
            # does, so a created lead or ticket is dated by the mail too.
            'date': msg_date,
        }

        # Determine correct author for sent items (mailbox owner, not the contact)
        post_author_id = partner.id
        post_email_from = email_from
        if is_outgoing:
            sender = full_message.get('from') or {}
            author_email = sender.get('email') or mailbox.email
            author_name = sender.get('name', '')
            if mailbox.mailbox_type == 'shared':
                author = self._find_or_create_partner(mailbox.email)
            else:
                author = mailbox.owner_user_id.partner_id
            post_author_id = author.id
            post_email_from = f'"{author_name}" <{author_email}>' if author_name else author_email

        try:
            if match['model']:
                # The matcher placed it. Both routing modes take this path —
                # the only difference between them is which models the matcher
                # was allowed to consider, which was decided above.
                target_record = self.env[match['model']].browse(match['res_id'])
                message = target_record.with_context(**IMPORT_CTX).message_post(
                    body=body_content,
                    subject=full_message.get('subject', ''),
                    message_type='email',
                    subtype_xmlid='mail.mt_comment',
                    author_id=post_author_id,
                    email_from=post_email_from,
                    message_id=internet_message_id,
                    parent_id=match['parent_message_id'],
                    attachments=email_attachments,
                    date=msg_date,
                )
                _logger.info(
                    f"[Incoming Mail] Threaded onto {match['model']}/{match['res_id']} "
                    f"by rule '{match['rule']}'"
                )
                outcome = 'threaded'
            elif is_outgoing and not mailbox.route_to_team:
                # Sent item we could not thread: the correspondent's chatter is
                # the only sensible home for it.
                outcome = 'sent_item'
                target_record = partner
                message = target_record.with_context(**IMPORT_CTX).message_post(
                    body=body_content,
                    subject=full_message.get('subject', ''),
                    message_type='email',
                    subtype_xmlid='mail.mt_comment',
                    author_id=post_author_id,
                    email_from=post_email_from,
                    message_id=internet_message_id,
                    attachments=email_attachments,
                    date=msg_date,
                )
                _logger.info(f"[Incoming Mail] Posted sent item to partner {partner.name}")
            else:
                # Nothing to thread onto → new record via alias, or the
                # contact's chatter when no alias is configured.
                target_record, message = self._route_email_via_alias(
                    mailbox=mailbox,
                    partner=partner,
                    msg_dict=msg_dict,
                    contact_email=contact_email,
                )
                # Landing on the sender's own chatter means no alias was
                # configured or none applied — delivered, but nobody is looking
                # there. Worth telling apart from a record we deliberately
                # created.
                outcome = 'fallback' if target_record == partner else 'created'

            self.env['pan.mail.routing.log'].log_decision(
                mailbox=mailbox,
                match=match,
                outcome=outcome,
                message=message,
                target_record=target_record,
                subject=full_message.get('subject'),
                email_from=email_from,
                internet_message_id=internet_message_id,
            )

            self._index_message(
                mailbox=mailbox,
                message=message,
                target_record=target_record,
                internet_message_id=internet_message_id,
                conversation_id=conversation_id,
                provider_message_id=provider_message_id,
            )

            # Lens fields, written here because they cannot travel any other
            # way: Odoo 19's `_raise_for_invalid_parameters` rejects field names
            # it does not know as `message_post` arguments, so passing them into
            # the post raises rather than stamping. That is fine — the matcher
            # decides *where* the mail lands and this records *how it arrived*,
            # neither of which the notification pass needs. The boundary is
            # armed by IMPORT_CTX on the post itself, which is also why it must
            # not depend on these: `mail.mail._record_sent()` writes the same
            # three fields for outgoing mail.
            if message:
                message.write({
                    'x_direction': 'outgoing' if is_outgoing else 'incoming',
                    'x_mailbox_id': mailbox.id,
                    'x_account_id': account.id,
                })

            _logger.info(f"[Incoming Mail] Successfully processed: {internet_message_id} -> {target_record._name}/{target_record.id}")
            return True

        except Exception:
            _logger.exception(f"[Incoming Mail] Failed to process message: {internet_message_id}")
            raise

    def _index_message(self, mailbox, message, target_record, internet_message_id,
                       conversation_id, provider_message_id=None):
        """Record what we just learned, so the next reply in this thread matches.

        Two writes, one per index the matching ladder reads:

        - the Message-ID under which this mail can be referenced, but only when
          it differs from what `message_post` already stored on `mail.message`.
          On import those are normally identical, so this usually writes nothing.
        - the (mailbox, thread id) → record link, which is the scoped lookup.
        """
        if not message:
            return

        if internet_message_id and message.message_id != internet_message_id:
            self.env['pan.mail.message.ref'].record(
                message, internet_message_id, source='provider')

        if conversation_id and target_record:
            self.env['pan.mail.thread.link'].record(
                mailbox=mailbox,
                thread_id=conversation_id,
                model=target_record._name,
                res_id=target_record.id,
                message=message,
                provider_message_id=provider_message_id,
            )

    def _is_duplicate(self, internet_message_id):
        """Is this Message-ID already in Odoo, imported or sent from here?

        The same lookup the matcher uses to resolve a `References` chain: the
        ref index (every id a message was ever seen under, including the one
        the provider minted on send) and Odoo's own `message_id`. That is what
        keeps a Sent Items sync from re-importing mail that left from Odoo.
        """
        if not internet_message_id:
            return False
        return bool(self.env['pan.mail.matcher']._resolve_message_id(internet_message_id))

    def _is_internal_domain(self, email, mailbox=None):
        """
        Check if email is from an internal company domain.

        The domain list lives in `pan.mail.domain`; this is only the
        call site. There is no way to switch the filter off — see
        ARCHITECTURE.md §9.12.

        Args:
            email: Email address to check
            mailbox: pan.mail.mailbox record, passed through unchanged

        Returns:
            bool: True if email should be skipped as internal
        """
        return self.env['pan.mail.domain'].should_skip(email, mailbox)

    def _find_partner(self, email):
        """
        Find existing partner by email (without creating).

        Used for sync mode filtering - only sync emails from known contacts.

        Args:
            email: Email address to search

        Returns:
            res.partner record or False if not found
        """
        if not email:
            return False

        Partner = self.env['res.partner']
        email_normalized = email.lower().strip()

        return Partner.search([
            '|',
            ('email', '=ilike', email_normalized),
            ('email_normalized', '=', email_normalized),
        ], limit=1)

    def _find_or_create_partner(self, email, name=None):
        """
        Find existing partner by email or create a new one.

        This ensures partners are created with correct name and email
        BEFORE message_process runs, which prevents Odoo from using
        the email subject as the partner name.

        Args:
            email: Email address to search/create
            name: Display name for new partner (optional)

        Returns:
            res.partner record
        """
        # First try to find existing partner
        partner = self._find_partner(email)
        if partner:
            _logger.debug(f"[Incoming Mail] Found existing partner: {partner.name} for {email}")
            return partner

        # Create new partner with correct name and email
        partner_name = name if name else email.split('@')[0]  # Use local part as fallback
        partner = self.env['res.partner'].create({
            'name': partner_name,
            'email': email,
            'is_company': False,
        })
        _logger.info("[Incoming Mail] Created new partner id=%s", partner.id)
        _logger.debug(f"[Incoming Mail] Created new partner: {partner.name} ({email})")

        return partner

    def _route_email_via_alias(self, mailbox, partner, msg_dict, contact_email):
        """
        Route incoming email using Odoo's native message_new() method.

        This leverages Odoo's built-in mail handling which:
        - Creates the record (ticket, lead, etc.)
        - Posts the initial message
        - Triggers auto-replies if configured (e.g., Helpdesk acknowledgment)
        - Does NOT send duplicate notifications to the sender

        Args:
            mailbox: pan.mail.mailbox record with routing configuration
            partner: res.partner record for the sender
            msg_dict: Parsed email dict in Odoo format
            contact_email: Sender email address

        Returns:
            tuple: (record, message) - the created record and its first message
        """
        import ast

        # Check if routing to team is enabled
        route_to_team = mailbox.route_to_team if mailbox else False
        alias = mailbox.alias_id if mailbox and route_to_team else False

        # Route to Contact: post to partner's chatter (default behavior)
        if not route_to_team or not alias or not alias.alias_model_id:
            _logger.info(f"[Incoming Mail] Routing to contact chatter for mailbox {mailbox.email}")
            message = partner.with_context(**IMPORT_CTX).message_post(
                body=msg_dict.get('body', ''),
                subject=msg_dict.get('subject', ''),
                message_type='email',
                subtype_xmlid='mail.mt_comment',
                author_id=partner.id,
                email_from=msg_dict.get('email_from'),
                message_id=msg_dict.get('message_id'),
                attachments=msg_dict.get('attachments', []),
                date=msg_dict.get('date'),
            )
            return partner, message

        model = alias.alias_model_id.model
        _logger.info(f"[Incoming Mail] Routing via alias '{alias.display_name}' -> {model}")

        # Parse alias_defaults for team_id, user_id, etc.
        custom_values = {}
        if alias.alias_defaults:
            try:
                custom_values = ast.literal_eval(alias.alias_defaults)
            except (ValueError, SyntaxError):
                pass

        # Create the record via message_new() with context flags matching
        # Odoo's standard _message_route_process() behavior:
        # - mail_create_nosubscribe: don't auto-subscribe the sender as follower
        # - mail_create_nolog: don't post a "Record created" log message
        Model = self.env[model].with_context(
            mail_create_nosubscribe=True,
            mail_create_nolog=True,
        )
        record = Model.message_new(msg_dict, custom_values=custom_values)

        # Post the email body to the chatter (message_new only creates the record).
        # IMPORT_CTX marks the post as an import, so `mail.thread._notify_thread`
        # drops the whole notification pass. See ARCHITECTURE.md §9.10.
        message = record.with_context(**IMPORT_CTX).message_post(
            body=msg_dict.get('body', ''),
            subject=msg_dict.get('subject', ''),
            message_type='email',
            subtype_xmlid='mail.mt_comment',
            author_id=msg_dict.get('author_id'),
            email_from=msg_dict.get('email_from'),
            message_id=msg_dict.get('message_id'),
            attachments=msg_dict.get('attachments', []),
            date=msg_dict.get('date'),
        )

        _logger.info("[Incoming Mail] Created %s id=%s via message_new", model, record.id)
        return record, message
