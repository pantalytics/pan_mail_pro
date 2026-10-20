# -*- coding: utf-8 -*-
"""
Incoming Mail Processor.

Fetches emails from a mailbox via its provider client and routes them to the
correct partner, using message_new()/message_post() for proper threading.

Provider-neutral: everything here reads the normalized message shape documented
in `mail_provider_client.py`. No Graph, Gmail or other wire-specific key should
ever appear below this line.
"""
from datetime import datetime
import logging
from typing import NamedTuple
from markupsafe import Markup

from odoo import models, api, fields, _
from odoo.exceptions import UserError

from .mail_provider_client import (
    DB_MARKER_HEADER, FOLDER_INBOX, FOLDER_SENT, MAX_INCOMING_ATTACHMENT_BYTES,
    ThrottledError, odoo_db_marker,
)
from .mail_message import READ_MIRROR_CTX
from .neutralization import database_is_neutralized

_logger = logging.getLogger(__name__)

# Which field on the mailbox holds each folder's cursor. One per folder, and
# the reason is issue #116: a single cursor had to be the minimum of both
# folders so the quieter one was never skipped, so a mailbox that received mail
# but sent none through that account never advanced past its last sent item and
# re-fetched months of inbox on every cron run.
FOLDER_CURSOR_FIELDS = {
    FOLDER_INBOX: 'last_sync_date',
    FOLDER_SENT: 'last_sent_sync_date',
}

# Messages read per folder per run. Ascending, so a backlog is worked oldest
# first over several runs; the cursor is what makes that terminate.
FETCH_BATCH_SIZE = 200

# The context key `_process_mailbox` carries the internal domain list under,
# read once per run, so the gate does set membership per party instead of a
# search. See `_internal_domains()`.
INTERNAL_DOMAINS_CTX = 'pan_mail_internal_domains'

# The database marker (`DB_MARKER_HEADER`, `odoo_db_marker`) is defined next
# to the header allowlist in `mail_provider_client`, which is where the
# header's name is spelled for writing and for reading alike. It is imported
# above because `_gate_odoo_originated` reads it, and the tests import it
# from here.

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

    Every refusal leaves the same trace and no more: one line in the log
    carrying the mailbox, the Message-ID, the gate and the reason. There is no
    queue a refused mail can be pulled back out of -- 19.0.7.0.0 removed it --
    so a mail a customer wants after all is re-imported by widening the
    mailbox's sync mode, not by working a backlog.

    `quiet` drops the refusal to DEBUG. Only the duplicate gate sets it: an
    overlapping fetch window is normal and deliberate on IMAP, so a refusal
    there is the system working, and at INFO it would drown the log.
    """
    reason: str
    detail: str = ''
    quiet: bool = False


IMPORT_CTX = {
    'pan_mail_imported': True,
    'mail_post_autofollow_author_skip': True,
    'mail_create_nosubscribe': True,
    'mail_post_autofollow': False,
}


def _address_list(recipients):
    """The addresses of a normalized recipient list, comma separated.

    Addresses only, no display names: this is read back as a list of people to
    reach, and a quoted name that has to survive a round trip through a char
    column is a parsing problem nobody asked for.
    """
    return ', '.join(
        r.get('email', '') for r in recipients or [] if r.get('email')
    )


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
        # Before every other gate: during setup there are no mailboxes and the
        # setup gate returns, and setup is exactly when a first heartbeat can
        # meet a bad minute. The retry must not wait for a state it unblocks.
        License = self.env['pan.mail.license']
        License._retry_if_stuck()
        # Same reason, the other direction: the setup answers ride the
        # heartbeat, and the Get started line at Pantalytics is read while
        # somebody is still answering them.
        License._report_setup_if_changed()
        # And a kind of failure not seen in the last day: during a beta that
        # is the thing to hear about today (pan.mail.error).
        License._report_errors_if_new()
        # Deliberately not filtered on an owner: whether a mailbox needs one is
        # the provider's business. A Gmail or IMAP shared mailbox is its own
        # account with nobody behind it, and requiring an owner here silently
        # skipped exactly those mailboxes. What matters is usable credentials,
        # which is what the mailbox asks its client.
        # Not filtered on the sync switches. Every mailbox that can be read is
        # read, because a reply to something Odoo sent belongs on the record it
        # continues whatever the mailbox was configured for; the gate ladder is
        # what decides how much of the rest may enter. A mailbox nobody wants
        # touched at all is archived, which is the one switch that means it.
        # `error` is in the set on purpose. It used to be the one state the
        # cron could not see, so the first failure of any kind -- a Graph 503,
        # a timeout -- took the mailbox out of the run permanently and only a
        # person pressing a button could put it back. What keeps a genuinely
        # broken mailbox from being retried every minute is the credentials
        # filter below, not the state: revoked consent and a deleted account
        # both fail `_has_working_credentials()`.
        mailboxes = self.env['pan.mail.mailbox'].search([
            ('state', 'in', ['active', 'draft', 'error']),
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

        # Not connected to Pantalytics: the same
        # shape as setup, so the stop is on the mailboxes where people look.
        if not License.sync_allowed():
            reason = License.not_allowed_error()
            _logger.info('[License] %s', reason)
            mailboxes.write({'state': 'error', 'error_message': reason})
            return

        _logger.info(f"[Incoming Mail] Starting sync for {len(mailboxes)} mailbox(es)")

        # Under the cron, what one mailbox landed is committed before the next
        # one starts. Without this the whole run was one transaction: a
        # backlog on one busy mailbox hit the cron worker's time limit, rolled
        # every mailbox's mail back, and repeated identically a minute later
        # until Odoo deactivated the cron. `_commit_progress` commits, so it is
        # only called under a cron; a person's Sync Now stays one transaction.
        # Stalest mailbox first, so a run the watchdog does cut short is not
        # the same mailboxes' turn again next minute. (Odoo's own time budget
        # per job is ten seconds and the API reports how much of it is left;
        # it re-runs the method from the top, not from where it stopped, so
        # there is nothing to gain by returning early on that number.)
        cron = self.env['ir.cron']
        in_cron = bool(self.env.context.get('ir_cron_progress_id'))
        mailboxes = mailboxes.sorted(
            key=lambda m: m.last_sync_date or datetime.min)
        if in_cron:
            cron._commit_progress(remaining=len(mailboxes))

        for index, mailbox in enumerate(mailboxes):
            # Once an hour, before the run: what is this address, and may
            # the sign-in that reads it do so. Its own savepoint, so a check
            # that fails costs the run nothing; `_verify_access` records
            # its own failures and raises none.
            if mailbox._access_check_due():
                with self.env.cr.savepoint():
                    mailbox._verify_access()
            try:
                with self.env.cr.savepoint():
                    stall = self._process_mailbox(mailbox)
                if isinstance(stall, ThrottledError):
                    # Mid-batch: what landed is kept, the cursor sits on the
                    # last message that did, and the mailbox says why it
                    # stopped there this run.
                    self._note_throttle(mailbox, stall)
                elif stall:
                    # Written outside the savepoint's success path but with no
                    # exception, so the mail that did land this run is kept and
                    # the reason it stopped there is on the mailbox. A stall is
                    # a message this mailbox cannot process, not a bad minute
                    # at the provider, so it goes straight to `error`.
                    mailbox.write({'state': 'error', 'error_message': stall})
                else:
                    mailbox._record_sync_success()
            except ThrottledError as e:
                # The listing itself was refused, before anything landed.
                self._note_throttle(mailbox, e)
            except Exception as e:
                # Savepoint rolled back: the cursor is usable again, so the
                # error write below won't hit "current transaction is aborted".
                _logger.exception(f"[Incoming Mail] Error processing mailbox {mailbox.email}")
                mailbox._record_sync_failure(str(e))
                self.env['pan.mail.error']._record(
                    'incoming.mailbox_failed', e, mailbox=mailbox)

            if in_cron:
                cron._commit_progress(processed=1, remaining=len(mailboxes) - index - 1)

        _logger.info("[Incoming Mail] Sync completed")

    def _note_throttle(self, mailbox, error):
        """The provider asked for a pause longer than a run may sleep.

        Said on the mailbox, not counted as a failure: a throttled mailbox is
        a healthy one being polite, and five polite minutes must not turn its
        badge red.
        """
        _logger.info("[Incoming Mail] Mailbox %s throttled: %s", mailbox.id, error)
        # `last_check_date` moves too: the mailbox was read, it was only asked
        # to slow down, and a run that is throttled every minute must not
        # show the "not being read" warning on top of the wait.
        mailbox.write({'error_message': str(error), 'last_check_date': fields.Datetime.now()})
        self.env['pan.mail.error']._record(
            'incoming.throttled', error, level='warning', mailbox=mailbox)

    def _process_mailbox(self, mailbox):
        """
        Fetch and process messages for a single mailbox.

        Args:
            mailbox: pan.mail.mailbox record

        Returns:
            str | ThrottledError | None: why the cursor is held, when a message
                failed to process, so the caller can put the mailbox in
                `error`; or the throttle the provider answered mid-batch, so
                the caller can write the wait without counting a failure.
                Returned rather than raised: the raise would roll back the
                mail that *did* land in this run along with it.

        Raises:
            UserError: when internal domains are not configured. Deliberately
                loud: the alternative is fetching every internal email into
                Odoo, and a mailbox stuck in `error` with a readable message is
                far cheaper than a silent data leak. The cron catches this and
                writes it onto the mailbox.
        """
        _logger.info(f"[Incoming Mail] Processing mailbox: {mailbox.email}")

        Domain = self.env['pan.mail.domain']
        gate = Domain.configuration_error()
        if gate:
            raise UserError(gate)
        # The list, read once for the run and carried on the context so the
        # internal-domain gate answers every party of every message from a
        # set rather than with a search of its own. The context rather than an
        # argument because `_fetch_folder` and `_process_message` keep their
        # signatures; `_internal_domains()` reads it back, and falls back to a
        # fresh read for a caller that did not come through here (a forced
        # import from the live mailbox calls `_process_message` directly).
        self = self.with_context(**{INTERNAL_DOMAINS_CTX: tuple(Domain.get_domains())})

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

        # One folder per direction, and each direction is its own answer. They
        # shared one switch until 19.0.7.6.0, so turning on receiving silently
        # turned on copying everything the owner wrote in Outlook as well.
        processed_count = 0
        cursors = {}
        stalls = []
        throttles = []
        # One session for the run: on IMAP that is one login and one SELECT
        # per folder for the whole mailbox, where every read used to dial in
        # on its own -- the listing, each body, each message's attachments.
        # The REST providers keep nothing and the block is a no-op for them.
        client = mailbox._get_client()
        with client.receiving_session(client.resolve_receiving_account(mailbox)):
            for folder in self._folders_to_sync(mailbox):
                count, cursor, stalled_on, throttled = self._fetch_folder(mailbox, folder)
                processed_count += count
                if stalled_on is not None:
                    stalls.append((folder, stalled_on))
                if throttled is not None:
                    throttles.append(throttled)
                # Each folder advances on its own progress only. An empty
                # folder is caught up, so it jumps to now() -- but not when it
                # stalled on its first message: that jump is exactly the skip
                # the stall prevents, and `_fetch_folder` hands back the cursor
                # it was holding instead.
                cursors[folder] = cursor or (
                    None if stalled_on is not None else fields.Datetime.now()
                )

        self._write_folder_cursors(mailbox, cursors)

        _logger.info(f"[Incoming Mail] Processed {processed_count} message(s) from {mailbox.email}")

        # A stall outranks a throttle: the stall names a message this mailbox
        # cannot process, the throttle only a wait. Both come back as a value
        # rather than a raise, for the same reason: the raise would take the
        # mail that landed this run, and the cursors just written, with it.
        if stalls:
            return self._stall_error(stalls)
        return throttles[0] if throttles else None

    @staticmethod
    def _folder_cursor(mailbox, folder):
        """Where this folder's own scan got to.

        Falls back to `last_sync_date` when the folder has no cursor of its
        own: that is a mailbox synced before per-folder cursors existed (the
        shared cursor was the minimum of both folders, so resuming from it
        re-reads at worst a little and skips nothing) or a mailbox whose first
        sync only set the shared one. Dedup on Message-ID makes the overlap
        free.
        """
        return mailbox[FOLDER_CURSOR_FIELDS[folder]] or mailbox.last_sync_date

    @staticmethod
    def _write_folder_cursors(mailbox, cursors):
        """Store each folder's progress. A `None` holds that folder where it is."""
        moved = {
            FOLDER_CURSOR_FIELDS[folder]: cursor
            for folder, cursor in cursors.items() if cursor
        }
        if moved:
            mailbox.write(moved)

    @staticmethod
    def _stall_error(stalls):
        """The message shown on a mailbox whose cursor is held by a failure.

        A stalled mailbox stops receiving anything behind the failed message,
        so it has to say so where somebody looks, which is the mailbox rather
        than the server log.
        """
        folder, message = stalls[0]
        subject = message.get('subject') or _('(no subject)')
        return _(
            'Sync stopped in %(folder)s at a message that could not be '
            'processed: "%(subject)s" (%(message_id)s). Mail behind it is not '
            'fetched until this one succeeds; the server log has the '
            'traceback. Nothing has been skipped.',
            folder=folder,
            subject=subject,
            message_id=message.get('provider_message_id') or _('unknown id'),
        )

    @staticmethod
    def _folders_to_sync(mailbox):
        """The folders this mailbox's settings ask for, in reading order.

        The inbox is always in the list. Replies arrive there and belong on the
        record they continue, so the question the mailbox's settings answer is
        not "read this folder" but "how much of what is in it may enter", which
        is the gate ladder's job. Reading is cheap and refusing is free; a
        message that no gate wants leaves nothing behind but a log line.

        The Sent folder is different, because nothing in it is ever waiting for
        Odoo: every sent item is either mail Odoo itself sent (dropped by the
        loop guard) or a copy of correspondence Odoo was never part of. That is
        opt-in.

        The list decides what is read, not what is kept: each folder carries
        its own cursor, so a folder that is not synced belongs absent here
        rather than fetched and discarded.
        """
        folders = [FOLDER_INBOX]
        if mailbox._reads_sent_folder():
            folders.append(FOLDER_SENT)
        return folders

    def _fetch_folder(self, mailbox, folder):
        """
        Fetch messages from a specific folder.

        Messages are sorted ascending (oldest first) so we process
        incrementally. The cursor advances only over messages that were
        processed, and stops at the first one that raised: stepping over it
        would lose that mail for good, and the sync dedups on Message-ID, so
        retrying it next run costs a lookup. A poison message therefore stalls
        this folder, loudly -- the caller puts the mailbox in `error` with the
        message that blocked it.

        Args:
            mailbox: mailbox record
            folder: FOLDER_INBOX or FOLDER_SENT

        Returns:
            tuple: (processed_count, cursor_datetime or None, stalled_message
                or None, throttled_error or None). The cursor is None only when
                the folder held nothing to read; a batch whose very first
                message failed, or was throttled, returns the mailbox's current
                cursor, so the caller holds instead of jumping to now(). The
                throttle is handed back rather than raised so that what landed
                before it is kept: see the loop.
        """
        # Fetch messages since last sync (sorted ascending for incremental cursor)
        client = mailbox._get_client()
        messages = client.fetch_messages(
            account=client.resolve_receiving_account(mailbox),
            mailbox=mailbox,
            folder=folder,
            since_datetime=self._folder_cursor(mailbox, folder),
            limit=FETCH_BATCH_SIZE,
        )
        if len(messages) == FETCH_BATCH_SIZE:
            # The batch is full, so there is more waiting behind it. Worth a
            # line: this is what a mailbox catching up on a backlog looks like,
            # and the next run continues from the cursor this one leaves.
            _logger.info(
                "[Incoming Mail] Full batch of %s from %s in %s; more mail is "
                "waiting and the next run continues from the cursor",
                FETCH_BATCH_SIZE, mailbox.email, folder,
            )

        processed = 0
        # Messages are sorted ascending, so the cursor may only advance over
        # messages that actually landed. It stops at the first one that raised
        # and never passes it again in this batch: a message whose date is
        # already behind the cursor is a message nobody will ever fetch again.
        # A message whose date could not be parsed must not empty the cursor
        # either -- with no cursor the caller jumps to now() and skips the
        # whole batch.
        cursor = None
        stalled_on = None
        throttled = None

        for message in messages:
            try:
                with self.env.cr.savepoint():
                    if self._process_message(mailbox, message, folder):
                        processed += 1
            except ThrottledError as error:
                # The provider asked for a pause while this message's body or
                # attachments were being fetched. That is a bad minute at the
                # provider, not a message this mailbox cannot process, so it
                # must not stall the cursor on it and put the mailbox in
                # `error`. Not raised either: a raise rolls back the mail that
                # landed earlier in this batch along with the cursor, and a
                # mailbox whose backlog meets the same wait at the same point
                # every run would never get past it. The batch stops here, the
                # cursor stays on the last message that landed, and the caller
                # writes the wait on the mailbox without counting a failure.
                throttled = error
                break
            except Exception as error:
                # Without the savepoint, one DB error would leave the whole
                # transaction in `aborted` state and every later message in
                # this batch would fail with "cursor already closed".
                _logger.exception(
                    "[Incoming Mail] Error processing message %s in %s: %s",
                    message.get('provider_message_id'), mailbox.email,
                    error,
                )
                if stalled_on is None:
                    stalled_on = message
                self.env['pan.mail.error']._record(
                    'incoming.message_failed', error, mailbox=mailbox)
                continue

            # The rest of the batch is still processed -- refusing to read the
            # mail behind a poison message helps nobody, and the processor
            # dedups on Message-ID, so re-reading them next run costs a lookup.
            if stalled_on is None and message.get('date'):
                cursor = message['date']

        if (stalled_on is not None or throttled is not None) and cursor is None:
            # The first message of the batch failed, or the provider asked for
            # a pause before any landed: hold the cursor exactly where it was
            # rather than reporting "nothing found".
            cursor = self._folder_cursor(mailbox, folder)

        return processed, cursor, stalled_on, throttled

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
        decisions used to be bare `return False` statements strewn through a
        two-hundred-line method — which is how the internal check ended up
        guarding one folder and not the other.
        """
        return [
            '_gate_odoo_originated',
            '_gate_duplicate',
            '_gate_counterpart',
            '_gate_internal_domain',
            '_gate_blocked_contact',
            '_gate_wanted',
        ]

    def _refuse(self, ctx):
        """Run the ladder. Returns the `Skip` that refused, or None to proceed.

        A refusal is logged and nothing else. The log line names the gate, so
        "why is this mail not in Odoo" is answerable without keeping a copy of
        a mail we decided not to read.
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
            return skip
        return None

    @api.model
    def _note_left_out(self, body, left_out):
        """`body` with a line naming each file too large to import.

        The file itself stays in the mailbox it arrived in; this line is how
        the reader in Odoo learns it exists.
        """
        names = ', '.join(
            '%s (%s MB)' % (a.get('name') or 'unnamed',
                            round((a.get('size') or len(a.get('content') or b'')) / 1048576, 1))
            for a in left_out)
        limit = MAX_INCOMING_ATTACHMENT_BYTES // 1048576
        note = _('Not imported, larger than %(limit)s MB: %(names)s. '
                 'Open the mail in the mailbox to get it.', limit=limit, names=names)
        return Markup('%s<p><em>%s</em></p>') % (body or '', note)

    def _full_message(self, ctx):
        """The full message, fetched once and cached on `ctx`.

        Lazy rather than fetched up front because most of every run is mail
        the sync has already seen, and the duplicate gate refuses it from the
        index alone. Paying a provider round-trip to discover we already have
        the mail would be the most expensive way to do nothing — which is why
        the gate above the duplicate check asks `_may_be_own_copy` first.
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
        """Already imported. The cheap refusal that ends most of every run.

        Second rather than first: mail Odoo sent is a duplicate *by
        construction* — the send path indexes the Message-ID the provider
        minted, and on Microsoft the draft keeps that id all the way out — so
        refusing it here first made the loop guard's re-index unreachable in
        exactly the case it was written for. The gate above pays no provider
        round-trip for the messages this one refuses; see `_may_be_own_copy`.
        """
        if self._duplicate_of(ctx):
            return Skip(
                'duplicate', _('This message is already in Odoo.'), quiet=True,
            )
        return None

    def _duplicate_of(self, ctx):
        """The `mail.message` this Message-ID already belongs to, resolved once.

        Two gates ask the same question, so it is answered once and cached on
        `ctx` rather than resolved twice through the index.
        """
        if 'duplicate_of' not in ctx:
            message_id = ctx['internet_message_id']
            ctx['duplicate_of'] = (
                self.env['pan.mail.matcher']._resolve_message_id(message_id)
                if message_id else self.env['mail.message'].browse()
            )
        return ctx['duplicate_of']

    def _may_be_own_copy(self, ctx):
        """Could this be mail Odoo sent, coming back? Asked without the provider.

        Reading the `X-Odoo-*` headers costs a `get_message`, and this gate runs
        first, so it must not pay one for every message the duplicate gate is
        about to refuse for free. Two answers are free and cover both cases
        that matter:

        - A Message-ID Odoo has never seen. `_gate_counterpart` fetches the
          full message anyway, and `_full_message` caches it, so reading the
          headers here costs nothing extra.
        - A Message-ID that resolves to a message Odoo *sent*. Only
          `mail.mail._index_sent_message` writes an `odoo`-sourced ref, so this
          is precisely the Sent Items copy the loop guard exists to read.

        Anything else is a message we imported coming round again on an
        overlapping sync window. It carries no headers of ours and there is
        nothing left to learn from it.
        """
        message = self._duplicate_of(ctx)
        if not message:
            return True
        return bool(self.env['pan.mail.message.ref'].sudo().search_count([
            ('mail_message_id', '=', message.id),
            ('source', '=', 'odoo'),
        ]))

    def _gate_odoo_originated(self, ctx):
        """Mail Odoo itself sent, coming back through the mailbox it left from.

        The loop guard. Our own `X-Odoo-*` headers survive the round trip, and
        a re-import would post Odoo's own message onto the record it came from.

        It refuses the mail, but not before reading it: this copy is the
        message *as it left*, and the send path only ever saw what the draft
        promised. See `_reindex_own_message`.

        First in the ladder, and that ordering is the whole point. Our own
        headers are a stronger signal than a Message-ID match, and the sent
        copy always matches the duplicate gate below — the send path put that
        very Message-ID in the index. Behind it, the re-index could only ever
        run when the provider changed the id between draft and send, which
        Microsoft does not do.

        "Our own" is decided by `X-Odoo-Db`, not by the headers being there:
        every Odoo running this module writes the same four, and a customer of
        ours who also runs Mail Pro writes them on the mail they send *to* us.
        That mail is ordinary incoming mail. It goes on down the ladder, its
        ids are never read, and matcher rule 1 ignores them too.
        """
        if not self._may_be_own_copy(ctx):
            return None
        headers = self._full_message(ctx).get('headers', {})
        if not (headers.get('x-odoo-model')
                or headers.get('x-odoo-mail-id')
                or headers.get('x-odoo-message-id')):
            return None
        if not self._is_own_odoo_mail(ctx, headers):
            return None
        self._reindex_own_message(ctx, headers)
        return Skip('odoo_originated', _('Odoo sent this message itself.'))

    def _is_own_odoo_mail(self, ctx, headers):
        """Did *this* database stamp the X-Odoo-* headers on this mail?

        The marker answers when it is there: equal to ours is ours, anything
        else is another Odoo's. Without one the folder answers, and only one
        way. The Sent folder holds what this account sent, so X-Odoo-* headers
        there with no marker are the copy of a mail this database sent before
        it stamped one -- the upgrade window, and the one case the re-index
        still has to see. In the inbox the same headers with no marker are
        somebody else's Odoo writing to us, or a pre-marker mail of our own
        that came back by Cc; the first is the mail this check exists to let
        in, and the second is refused one gate later, by its Message-ID.
        """
        marker = headers.get(DB_MARKER_HEADER.lower())
        if marker:
            own = odoo_db_marker(self.env)
            return bool(own) and marker == own
        return bool(ctx.get('is_outgoing'))

    def _reindex_own_message(self, ctx, headers):
        """Correct the indexes from the copy the provider actually sent.

        The send path indexes what the draft promised, and Microsoft does not
        promise much: the `conversationId` Graph reports for a draft is not
        always the one under which the reply lands in the same mailbox. A
        thread keyed on that alone goes silent on the commonest case there is,
        someone answering mail we sent — which is how a reply ends up placed by
        subject at confidence 0.50 and flagged for review.

        The copy in Sent Items carries the handles the provider really used, so
        this is the first moment they are knowable. Both indexes are refreshed
        from it: every Message-ID the mail went out under, and the record under
        each of its thread keys.

        Best effort by construction. It runs inside a gate that is about to
        refuse this mail anyway, and both `record` methods swallow their own
        failures, so the worst case is a weaker match later rather than a lost
        mail.
        """
        full_message = self._full_message(ctx)
        message, model, res_id = self._own_message(headers)
        if not message and not (model and res_id):
            return

        if message and full_message.get('message_id'):
            self.env['pan.mail.message.ref'].record(
                message, full_message['message_id'], source='provider')

        if model and res_id:
            self.env['pan.mail.thread.link'].record_all(
                mailbox=ctx['mailbox'],
                thread_ids=self.env['pan.mail.matcher'].thread_keys(full_message),
                model=model,
                res_id=res_id,
                message=message or None,
                provider_message_id=full_message.get('provider_message_id'),
            )

    def _own_message(self, headers):
        """(mail.message, model, res_id) for a copy of mail Odoo sent.

        `X-Odoo-Message-Id` names the `mail.message` directly and is the only
        one of the three that survives the mail being sent — `mail.mail` is
        deleted once it goes out. The model and record headers are the fallback
        when the message has since been deleted, because the thread link only
        needs the record.
        """
        Message = self.env['mail.message'].sudo()
        message = Message.browse()
        raw_message_id = headers.get('x-odoo-message-id')
        if raw_message_id:
            try:
                message = Message.browse(int(raw_message_id)).exists()
            except (TypeError, ValueError):
                message = Message.browse()

        model = message.model or headers.get('x-odoo-model') or False
        res_id = message.res_id or False
        if not res_id:
            try:
                res_id = int(headers.get('x-odoo-record-id') or 0) or False
            except (TypeError, ValueError):
                res_id = False
        return message, model, res_id

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

        No trace beyond the log line, on purpose. Internal mail is a refusal
        the sync must never offer to reverse: there is no queue it is held in
        and no button that re-imports it in bulk. The refusal `_refuse()` logs
        carries the mailbox, the Message-ID, the reason and the time, which is
        what answering "why is this mail not in Odoo" needs and is as much as
        may be kept about a mail we declined to read.

        One exception, and it is the only one: `force_import`. That is the
        owner of a personal mailbox pressing Add to Odoo on one mail they are
        reading in their own mailbox, live (`import_live_message`,
        ARCHITECTURE.md §1, "Reading is private, filing is public"). The sync refuses
        internal mail because nobody chose it; here somebody did, for this one
        mail, with the record it lands on in front of them, and the filing then
        runs under ordinary Odoo rules. The choice lifts this filter and the
        sync-level one, and lifts neither the duplicate guard nor the contact
        block list.

        The domain set is read once per run (`_internal_domains`) and carried
        on `ctx`, so a message with several recipients costs no search per
        party.
        """
        if ctx['force_import']:
            return None
        mailbox = ctx['mailbox']
        domains = ctx.get('internal_domains')
        if domains is None:
            domains = self._internal_domains()
        for party in ctx['counterparts']:
            if not self._is_internal_domain(party.get('email', ''), mailbox, domains):
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

    def _gate_wanted(self, ctx):
        """Does this mailbox want this email at all?

        The first clause needs no setting. A reply to something Odoo already
        has belongs on the record it continues -- a chatter thread showing the
        question and not the answer is the failure this module exists to
        prevent -- so it passes here whatever the switches say. It still had to
        get past every gate above: internal mail and a blocked contact are
        refusals no threading overrides.

        Everything else is the mailbox's `sync_level`, one rung at a time:

            replies    nothing else enters
            both       + the owner's own replies, read back from the Sent folder
            contacts   + new conversations started by existing contacts
            everyone   + new conversations from strangers, who become contacts

        Sending gets no rung of its own, because it has nothing left to widen:
        the reply clause above is the whole of what it accepts. Mail the owner
        wrote in their own client that starts something new stays out, even to
        a contact Odoo already has -- where such a mail belongs is a question
        the module cannot answer yet (the contact? a lead? an opportunity?),
        and guessing it wrong scatters chatter across records nobody asked for.
        Answering something Odoo already holds has one obvious home, so that is
        the case that syncs.
        """
        mailbox = ctx['mailbox']
        if ctx['force_import']:
            return None
        if self._is_reply_to_odoo(ctx):
            return None
        if ctx['is_outgoing']:
            return Skip(
                'not_a_reply',
                _('Sent email is only synced when it replies to a conversation '
                  'Odoo already has.'),
            )
        if not mailbox._syncs_new_conversations():
            return Skip(
                'not_a_reply',
                _('This mailbox only syncs replies to conversations Odoo already has.'),
            )
        if ctx['partner']:
            return None
        if not mailbox._syncs_strangers():
            return Skip(
                'unknown_contact',
                _('This mailbox only syncs email from existing contacts.'),
            )
        return None

    def _is_reply_to_odoo(self, ctx):
        """Does this email continue a conversation Odoo already holds?

        The References chain resolved against the ref index, which carries every
        Message-ID a message was ever seen under -- the one the provider minted
        when Odoo sent it included. So "a reply to mail we sent" and "a reply to
        mail we imported" are one lookup, and both belong on the record they
        continue.

        Deliberately not the full matcher. The matcher decides *where* a mail
        goes and is allowed to reach a weaker answer from a subject line and a
        participant list. The question here is whether the mail may enter at
        all, and only an exact chain is allowed to say yes to that.
        """
        matcher = self.env['pan.mail.matcher']
        headers = {
            k.lower(): v
            for k, v in (self._full_message(ctx).get('headers') or {}).items()
        }
        for message_id in matcher._reference_ids(headers):
            parent = matcher._resolve_message_id(message_id)
            if parent and parent.model and parent.res_id:
                return True
        return False

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
        # Same reason, and the preview already carries it: every client fills
        # `is_read` in both the list shape and the full one.
        provider_is_read = bool(message.get('is_read', True))

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
            # The company's own domains, as a set: read once per run by
            # `_process_mailbox`, or here when the call did not come through it.
            'internal_domains': self._internal_domains(),
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
            # The full message is handed back in: a provider whose full fetch
            # already carried the files reads them off it instead of fetching
            # the message a second time (see the contract).
            attachments = client.get_message_attachments(
                account=account,
                mailbox=mailbox,
                provider_message_id=message['provider_message_id'],
                full_message=full_message,
            )
            _logger.info(f"[Incoming Mail] Fetched {len(attachments)} attachment(s)")
        # Whatever the provider kept for itself stops here: the matcher, the
        # routing log and the index see the normalized message and nothing of
        # the raw payload behind it.
        full_message.pop('_source', None)

        # The partner (contact) for chatter posting. `_gate_blocked_contact`
        # already searched for it and left what it found on `ctx`, so the
        # search is not repeated here, and neither is it when the gate found
        # nobody: a stranger costs the one create, not a second search in
        # front of it. A ctx without the key (a ladder that did not run the
        # gate) still resolves the old way.
        partner = None
        if contact_email:
            partner = (ctx['partner'] if 'partner' in ctx
                       else self._find_partner(contact_email))
            if not partner:
                partner = self._create_partner(contact_email, contact_name)
            _logger.debug(f"[Incoming Mail] Partner resolved: {partner.name} (id={partner.id}, email={partner.email})")

        if not partner:
            _logger.warning(f"[Incoming Mail] Could not resolve partner for {contact_email}, skipping")
            return False

        # Where does this mail belong? The fetcher decides whether a message is
        # worth keeping; deciding where it goes is the matcher's job, and it is
        # provider-neutral — the same ladder serves Graph, Gmail and IMAP.
        match = self.env['pan.mail.matcher']._match(
            full_message,
            mailbox=mailbox,
            partner=partner,
            # In team mode a reply must land on the ticket or lead, never back
            # on the contact's own chatter.
            exclude_models=('res.partner',) if mailbox.route_to_team else (),
        )
        # Every handle this conversation carries: what the provider said, and
        # the root of the References chain. Both are indexed, because the
        # provider's own can drift between the mail we send and the reply that
        # comes back (see `pan.mail.matcher.thread_keys`).
        thread_keys = match['thread_keys']

        # Build email body - mark as safe HTML to preserve formatting
        body_content = full_message.get('body_html') or ''

        # Process attachments into Odoo's expected tuple format:
        # - Inline: 3-tuple so Odoo converts cid: → /web/image/
        # - Regular: 2-tuple stored as ir.attachment
        #
        # A file over the cap stays in the mailbox: the provider may already
        # have left it undownloaded (`content` None), and IMAP, which had to
        # read the whole message, is held to the same line here. The body
        # names what was left out, so the mail does not read as complete.
        email_attachments = []
        left_out = []
        for attachment in attachments:
            content = attachment.get('content')
            if content is None or len(content) > MAX_INCOMING_ATTACHMENT_BYTES:
                left_out.append(attachment)
                continue
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
        if left_out:
            body_content = self._note_left_out(body_content, left_out)

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
        to_addresses = _address_list(full_message.get('to'))
        cc_addresses = _address_list(full_message.get('cc'))
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
            elif is_outgoing:
                # Sent item we could not thread: the correspondent's chatter is
                # the only sensible home for it, in either routing mode. The
                # alias path below creates a lead or ticket *from* `msg_dict`,
                # whose author is the correspondent -- so a reply our user
                # wrote in their mail client became a ticket opened by the
                # customer, with the customer shown as the sender of our own
                # words. In team mode the matcher was told to keep contact
                # chatter out of its answers, which is how a sent reply to a
                # conversation held on a contact ended up here with no match.
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
                # Incoming and nothing to thread onto → new record via alias,
                # or the contact's chatter when no alias is configured. Only
                # the inbox reaches this: a record created here takes its
                # author and contact from the mail's From, which is only the
                # correspondent when the mail came in.
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
                thread_keys=thread_keys,
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
                # READ_MIRROR_CTX: the read flag below is the provider's own
                # answer, and pushing it straight back would be a write to
                # the mailbox for every mail the sync imports.
                message.with_context(**READ_MIRROR_CTX).write({
                    'x_direction': 'outgoing' if is_outgoing else 'incoming',
                    'x_mailbox_id': mailbox.id,
                    'x_account_id': account.id,
                    # Who else was on the mail. Text, not partners: see the
                    # field comment on mail.message. Written here rather than
                    # passed into message_post for the same reason as the three
                    # above -- and on every branch at once, which is what the
                    # earlier attempt through msg_dict never managed.
                    'x_email_to': to_addresses,
                    'x_email_cc': cc_addresses,
                    # The provider's own handle, and what the provider says
                    # about this mail's read state right now. Both are the
                    # mailbox's facts rather than Odoo's: the handle is how
                    # `set_seen` reaches this message later, and the read flag
                    # is why a mail you already read in Outlook does not arrive
                    # here shouting for attention. A provider that says nothing
                    # means read, which is what an imported mail looks like to
                    # everyone who was not sitting in the mailbox.
                    'x_provider_message_id': provider_message_id or False,
                    'x_is_read': bool(full_message.get(
                        'is_read', provider_is_read)),
                })

            _logger.info(f"[Incoming Mail] Successfully processed: {internet_message_id} -> {target_record._name}/{target_record.id}")
            return True

        except Exception:
            _logger.exception(f"[Incoming Mail] Failed to process message: {internet_message_id}")
            raise

    def _index_message(self, mailbox, message, target_record, internet_message_id,
                       thread_keys, provider_message_id=None):
        """Record what we just learned, so the next reply in this thread matches.

        Two writes, one per index the matching ladder reads:

        - the Message-ID under which this mail can be referenced, but only when
          it differs from what `message_post` already stored on `mail.message`.
          On import those are normally identical, so this usually writes nothing.
        - the (mailbox, thread key) → record link, one row per key, which is
          the scoped lookup.
        """
        if not message:
            return

        if internet_message_id and message.message_id != internet_message_id:
            self.env['pan.mail.message.ref'].record(
                message, internet_message_id, source='provider')

        if thread_keys and target_record:
            self.env['pan.mail.thread.link'].record_all(
                mailbox=mailbox,
                thread_ids=thread_keys,
                model=target_record._name,
                res_id=target_record.id,
                message=message,
                provider_message_id=provider_message_id,
            )

    def _internal_domains(self):
        """The company's own domains, as a set, for one run.

        `_process_mailbox` reads the list once and carries it on the context;
        this hands it back as a set. A caller that did not come through there
        (`import_live_message` calls `_process_message` directly) gets a fresh
        read, which is what every caller got before the cache. Never an
        ormcache: a list that changes on a settings page and has to be
        invalidated by hand is a list that is eventually stale, and stale here
        means internal mail entering.
        """
        cached = self.env.context.get(INTERNAL_DOMAINS_CTX)
        if cached is None:
            return frozenset(self.env['pan.mail.domain'].get_domains())
        return frozenset(cached)

    def _is_internal_domain(self, email, mailbox=None, domains=None):
        """
        Check if email is from an internal company domain.

        The domain list lives in `pan.mail.domain`; this is only the
        call site. There is no way to switch the filter off — see
        ARCHITECTURE.md §9.12.

        Args:
            email: Email address to check
            mailbox: pan.mail.mailbox record, passed through unchanged
            domains: the configured set, when the caller already read it;
                None reads the list

        Returns:
            bool: True if email should be skipped as internal
        """
        return self.env['pan.mail.domain'].should_skip(email, mailbox, domains=domains)

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
        return self._create_partner(email, name)

    def _create_partner(self, email, name=None):
        """Create the contact for an address `_find_partner` found nobody for."""
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
